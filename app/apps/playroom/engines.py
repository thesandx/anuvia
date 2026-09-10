"""Game engines: the per-game half of a round.

The core tables carry no Bingo concepts. A new game adds tables and a strategy
class here. It changes no route, because every move travels in one action
envelope and is dispatched on `(game_id, type)`.

Checklist for a new game:

1. Insert a row in `playroom_games`.
2. Add its tables, prefixed with the game id.
3. Implement `GameEngine` and register it in `ENGINES`.
4. Add its action types to `apply_action`.
5. Flip `playroom_games.status` to `playable` only when all four are done.

Keep the invariant that makes Bingo safe: **derive shared state from one
append-only table with the right unique constraint, rather than storing
per-player copies.**
"""

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.playroom import models
from app.apps.playroom.bingo import (
    CARD_SIZE,
    HIGHEST_NUMBER,
    LINES_TO_WIN,
    LOWEST_NUMBER,
    create_card,
    find_winning_lines,
    is_playable_number,
)
from app.apps.playroom.errors import RoomError

#: Points for taking the round, and for each line a non-winner completed.
WIN_POINTS = 100
LINE_POINTS = 10


class RejectedClaim(RoomError):
    """An invalid bingo claim, carrying how many lines the board really held.

    The round keeps running. The count travels with the error so the service
    can record it: a rejection at four lines is a player who misread the rule,
    and one at a single line is a player who did not know there was a rule.
    """

    def __init__(self, message: str, lines_held: int) -> None:
        super().__init__("invalid-claim", message)
        self.lines_held = lines_held


@dataclass(slots=True)
class EventSpec:
    """One row for the audit log, written in the action's own transaction."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    player_id: UUID | None = None


@dataclass(slots=True)
class ActionResult:
    """What one move did to the round."""

    events: list[EventSpec] = field(default_factory=list)
    #: True when the move ended the round. The service moves the room to
    #: `round-results` and applies the awards.
    ended: bool = False
    #: player id -> (note shown on the results screen, points gained)
    awards: dict[UUID, tuple[str, int]] = field(default_factory=dict)


class GameEngine(Protocol):
    """What a game has to provide. Everything else is the shared session."""

    game_id: str

    async def deal(
        self, db: AsyncSession, game_round: models.Round, player_ids: list[UUID]
    ) -> None:
        """Create the per-game rows for a new round."""

    async def deal_one(self, db: AsyncSession, game_round: models.Round, player_id: UUID) -> None:
        """Deal to a player who joined after the round started."""

    async def apply_action(
        self,
        db: AsyncSession,
        game_round: models.Round,
        player: models.Player,
        players: list[models.Player],
        action_type: str,
        payload: dict[str, Any],
    ) -> ActionResult:
        """Validate and apply one move. Raise `RoomError` on a rule violation."""

    async def load_view(
        self, db: AsyncSession, game_round: models.Round
    ) -> tuple[list[int], dict[UUID, list[int]]]:
        """Everything the serialiser needs, before scoping."""

    async def auto_move(
        self,
        db: AsyncSession,
        game_round: models.Round,
        player: models.Player,
        players: list[models.Player],
    ) -> ActionResult | None:
        """Play for a player whose turn ran out, or return None if it cannot."""


class BingoEngine:
    """Turn-based Bingo on a 1-25 board.

    Two rules do all the work and neither trusts the client:

    - **Marking is derived.** A cell is marked when its number is in
      `playroom_bingo_selections`. The client sends no marks and there is no
      field for it to do so.
    - **A win is counted, not asserted.** Five completed lines, computed from
      the caller's stored board and the shared selections.
    """

    game_id = "bingo"

    async def deal(
        self, db: AsyncSession, game_round: models.Round, player_ids: list[UUID]
    ) -> None:
        for player_id in player_ids:
            db.add(
                models.BingoBoard(round_id=game_round.id, player_id=player_id, cells=create_card())
            )

    async def deal_one(self, db: AsyncSession, game_round: models.Round, player_id: UUID) -> None:
        db.add(models.BingoBoard(round_id=game_round.id, player_id=player_id, cells=create_card()))

    async def load_view(
        self, db: AsyncSession, game_round: models.Round
    ) -> tuple[list[int], dict[UUID, list[int]]]:
        selections = await self._selections(db, game_round.id)
        boards = await self._boards(db, game_round.id)
        return selections, boards

    async def apply_action(
        self,
        db: AsyncSession,
        game_round: models.Round,
        player: models.Player,
        players: list[models.Player],
        action_type: str,
        payload: dict[str, Any],
    ) -> ActionResult:
        if action_type == "select_number":
            return await self._select_number(db, game_round, player, players, payload)
        if action_type == "claim_bingo":
            return await self._claim_bingo(db, game_round, player, players)
        raise RoomError("wrong-phase", "That move does not exist in this game.")

    async def auto_move(
        self,
        db: AsyncSession,
        game_round: models.Round,
        player: models.Player,
        players: list[models.Player],
    ) -> ActionResult | None:
        """Takes a number for a player who ran out of time.

        The number is drawn from the ones still free. "From their own board" and
        "still free" are the same set here, because every board holds all 25
        numbers. A player can always take any number nobody else has.

        It is a real move: the same insert, the same turn advance, the same
        end-of-round check. The only difference is who chose, and the event
        records that so a timed-out turn is never mistaken for a played one.
        """
        taken = set(await self._selections(db, game_round.id))
        free = [n for n in range(LOWEST_NUMBER, HIGHEST_NUMBER + 1) if n not in taken]
        if not free:
            return None

        result = await self._select_number(
            db, game_round, player, players, {"value": secrets.choice(free)}
        )
        for event in result.events:
            if event.type == "number_selected":
                event.payload["auto"] = True
        result.events.append(
            EventSpec(type="turn_timed_out", player_id=player.id, payload={"seat": player.seat})
        )
        return result

    # --- moves ---------------------------------------------------------------

    async def _select_number(
        self,
        db: AsyncSession,
        game_round: models.Round,
        player: models.Player,
        players: list[models.Player],
        payload: dict[str, Any],
    ) -> ActionResult:
        value = payload.get("value")
        if not is_playable_number(value):
            raise RoomError("invalid-number", f"Pick a number between 1 and {HIGHEST_NUMBER}.")

        if game_round.status != models.ROUND_PLAYING:
            raise RoomError("round-over", "This round is already over.")

        turn_order: list[str] = list(game_round.turn_order or [])
        if not turn_order:
            raise RoomError("wrong-phase", "No round is in progress.")

        on_turn = turn_order[game_round.current_turn_index % len(turn_order)]
        if on_turn != str(player.id):
            raise RoomError("not-your-turn", "It is not your turn yet.")

        selections = await self._selection_rows(db, game_round.id)
        seq = len(selections) + 1
        previous_at = selections[-1].created_at if selections else game_round.started_at

        # The unique primary key on (round_id, number) is the real guarantee
        # that two players never take the same number. A savepoint keeps the
        # violation from poisoning the surrounding transaction.
        # A Core insert, not `db.add`, and the difference matters on the losing
        # side of the race: an ORM object survives the savepoint rollback as a
        # pending row, and the next flush retries the same doomed insert.
        # A Core statement leaves nothing behind to retry.
        try:
            async with db.begin_nested():
                await db.execute(
                    insert(models.BingoSelection).values(
                        round_id=game_round.id,
                        number=value,
                        seq=seq,
                        player_id=player.id,
                    )
                )
        except IntegrityError as error:
            raise RoomError("number-taken", f"{value} has already been taken.") from error

        game_round.current_turn_index = (game_round.current_turn_index + 1) % len(turn_order)

        taken = [row.number for row in selections] + [value]
        result = ActionResult(
            events=[
                EventSpec(
                    type="number_selected",
                    player_id=player.id,
                    payload={
                        "number": value,
                        "seq": seq,
                        # Seat is what makes turn-order fairness measurable.
                        "seat": player.seat,
                        "thinkingMs": _elapsed_ms(previous_at),
                    },
                )
            ]
        )

        if len(taken) >= CARD_SIZE:
            self._end_exhausted(game_round, player, players, taken, result)

        return result

    async def _claim_bingo(
        self,
        db: AsyncSession,
        game_round: models.Round,
        player: models.Player,
        players: list[models.Player],
    ) -> ActionResult:
        if game_round.status != models.ROUND_PLAYING or game_round.winner_player_id is not None:
            raise RoomError("round-over", "Someone already called bingo on this round.")

        selections = await self._selections(db, game_round.id)
        boards = await self._boards(db, game_round.id)

        own = boards.get(player.id)
        if own is None:
            raise RoomError("not-in-room", "You have no board for this round.")

        lines = find_winning_lines(own, selections)
        if len(lines) < LINES_TO_WIN:
            short = LINES_TO_WIN - len(lines)
            # A rejected claim leaves the round running. The event separates a
            # player who misread the rule from one who did not know there was
            # one: a rejection at four lines is not the same mistake as at one.
            raise RejectedClaim(
                f"You need {LINES_TO_WIN} complete lines to call bingo. "
                f"You have {len(lines)}, so {short} to go.",
                lines_held=len(lines),
            )

        game_round.winner_player_id = player.id
        game_round.win_detail = [line.as_payload() for line in lines]
        game_round.status = models.ROUND_WON
        game_round.ended_at = datetime.now(UTC)

        awards = self._score(players, boards, selections, winner_id=player.id)

        return ActionResult(
            ended=True,
            awards=awards,
            events=[
                EventSpec(
                    type="bingo_claimed",
                    player_id=player.id,
                    payload={"lines": len(lines)},
                ),
                EventSpec(
                    type="round_won",
                    player_id=player.id,
                    payload={"lines": len(lines), "numbersTaken": len(selections)},
                ),
            ],
        )

    # --- round end -----------------------------------------------------------

    def _end_exhausted(
        self,
        game_round: models.Round,
        closer: models.Player,
        players: list[models.Player],
        taken: list[int],
        result: ActionResult,
    ) -> None:
        """Ends a round in which all 25 numbers went and nobody claimed.

        Reaching five lines takes roughly 19 numbers, and a full room of eight
        gets about three turns each, so this is the case a busy room is most
        likely to reach. Nothing forces a player to claim, and once every number
        is gone no further selection is possible. The round would otherwise sit
        there until the host ended the session.

        **Why the closer wins.** With all 25 numbers taken, every board holds
        all twelve lines, so "most lines" is a twelve-way tie by construction
        and cannot decide anything. The player who took the final number is the
        one deterministic, seat-neutral answer available at that moment, and it
        gives the results screen a winner to show.

        Line points are not awarded here. They exist to reward a near miss, and
        at exhaustion every board is complete, paying 10 a line would hand each
        non-winner 120 points against the winner's 100.
        """
        game_round.winner_player_id = closer.id
        game_round.status = models.ROUND_EXHAUSTED
        game_round.ended_at = datetime.now(UTC)

        result.ended = True
        result.awards = {
            person.id: (
                ("Closed the board", WIN_POINTS)
                if person.id == closer.id
                else ("No bingo called", 0)
            )
            for person in players
        }
        result.events.append(
            EventSpec(
                type="board_exhausted",
                player_id=closer.id,
                payload={"numbersTaken": len(taken)},
            )
        )
        result.events.append(
            EventSpec(
                type="round_won",
                player_id=closer.id,
                payload={"lines": None, "numbersTaken": len(taken), "reason": "board_exhausted"},
            )
        )

    def _score(
        self,
        players: list[models.Player],
        boards: dict[UUID, list[int]],
        selections: list[int],
        winner_id: UUID,
    ) -> dict[UUID, tuple[str, int]]:
        """The winner takes 100. Everyone else takes 10 for each line they hold."""
        awards: dict[UUID, tuple[str, int]] = {}
        for person in players:
            if person.id == winner_id:
                awards[person.id] = ("Bingo", WIN_POINTS)
                continue
            held = len(find_winning_lines(boards.get(person.id, []), selections))
            note = "No line" if held == 0 else "One line" if held == 1 else f"{held} lines"
            awards[person.id] = (note, held * LINE_POINTS)
        return awards

    # --- reads ---------------------------------------------------------------

    async def _selection_rows(
        self, db: AsyncSession, round_id: UUID
    ) -> list[models.BingoSelection]:
        rows = await db.execute(
            select(models.BingoSelection)
            .where(models.BingoSelection.round_id == round_id)
            .order_by(models.BingoSelection.seq)
        )
        return list(rows.scalars().all())

    async def _selections(self, db: AsyncSession, round_id: UUID) -> list[int]:
        rows = await db.execute(
            select(models.BingoSelection.number)
            .where(models.BingoSelection.round_id == round_id)
            .order_by(models.BingoSelection.seq)
        )
        return [int(number) for number in rows.scalars().all()]

    async def _boards(self, db: AsyncSession, round_id: UUID) -> dict[UUID, list[int]]:
        rows = await db.execute(
            select(models.BingoBoard).where(models.BingoBoard.round_id == round_id)
        )
        return {
            board.player_id: list(board.cells)
            for board in rows.scalars().all()
            if board.cells is not None
        }


def _elapsed_ms(since: datetime | None) -> int | None:
    """Milliseconds since a moment, or None when there is nothing to measure."""
    if since is None:
        return None
    start = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - start).total_seconds() * 1000))


ENGINES: dict[str, GameEngine] = {BingoEngine.game_id: BingoEngine()}


def engine_for(game_id: str) -> GameEngine:
    engine = ENGINES.get(game_id)
    if engine is None:
        raise RoomError("wrong-phase", "That game is not playable yet.")
    return engine
