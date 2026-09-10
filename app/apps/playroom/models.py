"""Playroom tables.

The split that matters: **generic tables describe a session; per-game tables
describe a game.** Adding a game adds tables. It does not change the core, and
it does not change a route.

Three deliberate departures from the SQL in the handover document, all for the
same reason — this repository runs SQLite locally, in tests, and in the CI
container smoke test, and PostgreSQL (Neon) in production. One model has to
serve both:

1. **Arrays become JSON.** `bingo_boards.cells` and `rounds.turn_order` are
   PostgreSQL arrays in the handover. SQLite has no array type, so both are
   `JSON` (JSONB on PostgreSQL). The contents and the invariants are unchanged.
2. **Enums become text.** `room_status` and `room_phase` are text columns
   validated in Python. A native PostgreSQL enum needs its own migration to gain
   a value, and SQLite has none at all.
3. **Tables carry a `playroom_` prefix.** This is a modular monolith: `games`,
   `rooms`, `players` and especially `events` are names another product will
   want. The handover's own guidance for a new game is to prefix its tables.

What does NOT change is the constraint that makes the game safe:
`PRIMARY KEY (round_id, number)` on selections. It is how the database — not
application code — guarantees two players never take the same number.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

#: JSONB where it exists, plain JSON on SQLite. Same Python values either way.
JsonColumn = JSON().with_variant(JSONB, "postgresql")

#: SQLite only auto-increments a column declared exactly `INTEGER PRIMARY KEY`.
#: A `BIGINT PRIMARY KEY` there is an ordinary column that never fills itself
#: in, so the event log would fail on its first insert.
AutoBigInt = BigInteger().with_variant(Integer, "sqlite")

# Room lifecycle. Text rather than a native enum — see the module docstring.
ROOM_STATUS_ACTIVE = "active"
ROOM_STATUS_EXPIRED = "expired"

PHASE_LOBBY = "lobby"
PHASE_PLAYING = "playing"
PHASE_ROUND_RESULTS = "round-results"
PHASE_FINISHED = "finished"

ROUND_PLAYING = "playing"
ROUND_WON = "won"
ROUND_ABANDONED = "abandoned"
#: All 25 numbers went with nobody having claimed. See `service.py`.
ROUND_EXHAUSTED = "exhausted"


class Game(Base):
    """The catalogue. `status` is `playable` only when a game engine exists."""

    __tablename__ = "playroom_games"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # playable | building
    min_players: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_players: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Room(Base):
    """One session, identified by the six-character key the host reads out."""

    __tablename__ = "playroom_rooms"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    key: Mapped[str] = mapped_column(String(16), nullable=False)
    game_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("playroom_games.id"), nullable=False
    )
    # No foreign key to players: the host row is written in the same
    # transaction as the room, and a circular FK would need a deferred
    # constraint that SQLite does not support.
    host_player_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    phase: Mapped[str] = mapped_column(String(16), nullable=False, default=PHASE_LOBBY)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: { rounds, privacy, maxPlayers } — stored per room so a later game can
    #: vary them without reshaping the table.
    settings: Mapped[dict] = mapped_column(JsonColumn, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ROOM_STATUS_ACTIVE)
    #: Bumped on every state change. Backs `ETag`/`If-None-Match` so an idle
    #: lobby of twenty players costs almost nothing to poll.
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # A key is unique among ACTIVE rooms only. Expired rooms keep their key
        # for analytics, and the key becomes reusable. The predicate must be
        # immutable, so the sweeper flips `status` rather than the index
        # reading now().
        Index(
            "playroom_rooms_active_key",
            "key",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "playroom_rooms_expiry",
            "expires_at",
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )


class Player(Base):
    """An anonymous participant in exactly one room. There are no accounts."""

    __tablename__ = "playroom_players"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    room_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("playroom_rooms.id", ondelete="CASCADE"), nullable=False
    )
    #: Nullable so retention can drop the name without losing the row, and with
    #: it every aggregate the row feeds.
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_color: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_host: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Join order. Seeds turn order, and decides who is promoted when a host
    #: disappears.
    seat: Mapped[int] = mapped_column(Integer, nullable=False)
    #: sha256 of the player token. The token itself is never stored.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: Refreshed on every authenticated request. A host who stops being seen is
    #: replaced, so a closed tab does not strand the room.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("playroom_players_room_seat", "room_id", "seat", unique=True),
        Index("playroom_players_token", "token_hash"),
    )


# Nicknames are unique within one LIVE room only, case-insensitive. They mean
# nothing across rooms, and a player who leaves frees theirs.
#
# Declared out here rather than in `__table_args__` because the expression needs
# the mapped attribute, which does not exist until the class is built.
Index(
    "playroom_players_room_name",
    Player.room_id,
    func.lower(Player.display_name),
    unique=True,
    postgresql_where=text("left_at IS NULL"),
    sqlite_where=text("left_at IS NULL"),
)


class Round(Base):
    """One round of one game inside a room."""

    __tablename__ = "playroom_rounds"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    room_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("playroom_rooms.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("playroom_games.id"), nullable=False
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ROUND_PLAYING)
    #: Player ids in seat order, as a JSON list of strings.
    turn_order: Mapped[list] = mapped_column(JsonColumn, nullable=False, default=list)
    current_turn_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    winner_player_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    #: The winning lines, as the client's `WinningLine[]`.
    win_detail: Mapped[list | None] = mapped_column(JsonColumn, nullable=True)
    #: Per-game extras, and the round-results rows once a round ends.
    state: Mapped[dict] = mapped_column(JsonColumn, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("playroom_rounds_room_number", "room_id", "round_number", unique=True),)


class BingoBoard(Base):
    """One player's 25 cells for one round.

    Every board is stored; not every board is sent. The server has to validate
    any player's claim against their own board. Visibility is a serialisation
    concern applied on the way out of the handler — see `serializers.py`.
    """

    __tablename__ = "playroom_bingo_boards"

    round_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("playroom_rounds.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("playroom_players.id", ondelete="CASCADE"), primary_key=True
    )
    #: 25 entries, a permutation of 1..25, row-major.
    cells: Mapped[list | None] = mapped_column(JsonColumn, nullable=True)


class BingoSelection(Base):
    """One number taken, once, for the whole round.

    There is no marks table. Marking is derived: a cell is marked when its
    number appears here. One shared list means every board agrees by
    construction. Do not add per-player mark state.
    """

    __tablename__ = "playroom_bingo_selections"

    round_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("playroom_rounds.id", ondelete="CASCADE"), primary_key=True
    )
    #: The composite primary key is what makes a double-take impossible.
    number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("number BETWEEN 1 AND 25", name="playroom_bingo_selections_range"),
        Index("playroom_bingo_selections_seq", "round_id", "seq", unique=True),
    )


class Event(Base):
    """The audit log. Written in the same transaction as every state change.

    Never in a background task — an event that can be lost is not an audit log.
    It carries no name and no address, so it survives retention untouched.
    """

    __tablename__ = "playroom_events"

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    room_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    round_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    player_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    game_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonColumn, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("playroom_events_room", "room_id", "id"),
        Index("playroom_events_type_time", "type", "occurred_at"),
        Index("playroom_events_game_time", "game_id", "occurred_at"),
    )


class IdempotencyKey(Base):
    """A replayed action, and the room payload the first attempt produced.

    Network retries double-post a selection. Without this a retried "take 17"
    comes back as `number-taken` and looks like a bug to the player.
    """

    __tablename__ = "playroom_idempotency_keys"

    round_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("playroom_rounds.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    player_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    #: The unscoped room snapshot. Scoping is applied again on replay, so a
    #: replayed key can never hand one player another player's board.
    response: Mapped[dict] = mapped_column(JsonColumn, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DailyGameStats(Base):
    """The nightly rollup dashboards read. They never scan `playroom_events`."""

    __tablename__ = "playroom_daily_game_stats"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    game_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    rooms_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rounds_played: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    distinct_players: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    median_round_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
