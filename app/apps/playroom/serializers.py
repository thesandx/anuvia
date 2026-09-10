"""Building the `Room` payload, scoped to one caller.

Board visibility is enforced here and nowhere else. A player sees their own
board only; the winner's board becomes visible to the room when the round ends,
and not before. Hiding the other grids in the UI would be theatre — the payload
is one dev-tools tab away — so the narrowing happens on the way out of every
handler, including the read.

This is the Python counterpart of `scopeRoomForPlayer` in the client's
`lib/room-engine.ts`.
"""

from datetime import UTC, datetime
from uuid import UUID

from app.apps.playroom import models
from app.apps.playroom.keys import initial_of

#: Shown when retention has dropped a nickname but the row survives.
ANONYMOUS_NAME = "player"


def iso_z(value: datetime) -> str:
    """ISO-8601 with a `Z` suffix, which is what the client parses.

    SQLite hands back naive datetimes and PostgreSQL hands back aware ones. A
    naive value is treated as UTC, because every write goes through
    `datetime.now(UTC)` or `func.now()`.
    """
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def player_payload(player: models.Player, host_id: UUID | None) -> dict:
    """One entry in `players`. Carries no token and no address."""
    name = player.display_name or ANONYMOUS_NAME
    return {
        "id": str(player.id),
        "name": name,
        "initial": initial_of(name),
        "color": player.avatar_color,
        "score": player.score,
        "isHost": player.id == host_id,
        # Every player in a live room is ready. The field exists because the
        # client's Player type has it, and a future game may use it.
        "isReady": True,
    }


def bingo_payload(
    game_round: models.Round,
    selections: list[int],
    boards: dict[UUID, list[int]],
    viewer_id: UUID | None,
) -> dict:
    """The `bingo` block, with `cards` narrowed to what the caller may see.

    `boards` is every board in the round. What comes back is at most two of
    them: the caller's own, and the winner's once there is one. A caller with no
    token is a spectator and gets none.
    """
    visible: dict[str, list[int]] = {}

    if viewer_id is not None:
        own = boards.get(viewer_id)
        if own is not None:
            visible[str(viewer_id)] = list(own)

    # The reveal: once somebody has won, their board is public so the room can
    # see the lines that took the round.
    winner_id = game_round.winner_player_id
    if winner_id is not None:
        winning = boards.get(winner_id)
        if winning is not None:
            visible[str(winner_id)] = list(winning)

    return {
        "selected": selections,
        "cards": visible,
        "turnOrder": list(game_round.turn_order or []),
        "currentTurnIndex": game_round.current_turn_index,
        "winnerId": str(winner_id) if winner_id is not None else None,
        "winningLines": list(game_round.win_detail or []),
    }


def room_payload(
    room: models.Room,
    players: list[models.Player],
    game_round: models.Round | None,
    selections: list[int],
    boards: dict[UUID, list[int]],
    viewer_id: UUID | None,
) -> dict:
    """The whole `Room` object, exactly as the client's type declares it.

    `bingo` is null outside a live or just-finished round. `lastRound` is
    populated only at `round-results`, which is the one screen that reads it.
    """
    show_bingo = game_round is not None and room.phase in (
        models.PHASE_PLAYING,
        models.PHASE_ROUND_RESULTS,
    )

    last_round = None
    if room.phase == models.PHASE_ROUND_RESULTS and game_round is not None:
        rows = list(game_round.state.get("results", [])) if game_round.state else []
        last_round = sorted(rows, key=lambda row: row.get("gain", 0), reverse=True)

    return {
        "key": room.key,
        "gameId": room.game_id,
        "hostId": str(room.host_player_id) if room.host_player_id else "",
        "phase": room.phase,
        "round": room.round_number,
        "players": [player_payload(player, room.host_player_id) for player in players],
        "settings": room.settings,
        "bingo": (bingo_payload(game_round, selections, boards, viewer_id) if show_bingo else None),
        "lastRound": last_round,
        "createdAt": iso_z(room.created_at),
        "expiresAt": iso_z(room.expires_at),
    }


def rescope(payload: dict, boards: dict[UUID, list[int]], viewer_id: UUID | None) -> dict:
    """Re-narrows `bingo.cards` on a payload that was built for someone else.

    Used when replaying an idempotent action: the stored snapshot holds every
    board, and the replay must not hand this caller another player's grid.
    """
    bingo = payload.get("bingo")
    if not bingo:
        return payload

    visible: dict[str, list[int]] = {}
    if viewer_id is not None and viewer_id in boards:
        visible[str(viewer_id)] = list(boards[viewer_id])

    winner_id = bingo.get("winnerId")
    if winner_id:
        try:
            winner_uuid = UUID(winner_id)
        except ValueError:  # pragma: no cover - ids are written by this module
            winner_uuid = None
        if winner_uuid is not None and winner_uuid in boards:
            visible[winner_id] = list(boards[winner_uuid])

    return {**payload, "bingo": {**bingo, "cards": visible}}
