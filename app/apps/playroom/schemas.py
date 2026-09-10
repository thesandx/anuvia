"""Request and response shapes.

The client is TypeScript, so every field on the wire is camelCase. That is what
`alias_generator=to_camel` does. `populate_by_name=True` lets Python construct
these models with their snake_case field names.

The bounds below are enforced twice on purpose. The client enforces them so a
player gets an instant message; the server enforces them again because the
client is not trusted.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from pydantic.alias_generators import to_camel

from app.apps.playroom.keys import normalise_name

AvatarColor = Literal["peach", "mint", "yellow", "mustard", "cream"]
RoomPrivacy = Literal["Key only", "Locked after start"]
RoomPhase = Literal["lobby", "playing", "round-results", "finished"]

#: Trimmed, 1 to 16 characters. Whitespace-only input is rejected by the
#: validator below, not by the length bound, "   " is four characters.
Nickname = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class CamelModel(BaseModel):
    """Base for everything on the wire."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class RoomSettings(CamelModel):
    #: Games played before the final scoreboard. The client always sends 1.
    rounds: int = Field(ge=1, le=20)
    privacy: RoomPrivacy
    max_players: int = Field(ge=2, le=20)


class Player(CamelModel):
    id: str
    name: str
    #: First character of `name`, uppercased.
    initial: str
    color: AvatarColor
    #: Cumulative across every round of the session.
    score: int
    is_host: bool
    is_ready: bool


class WinningLine(CamelModel):
    kind: Literal["row", "column", "diagonal"]
    #: Row/column number (1-based), or 1 and 2 for the two diagonals.
    index: int
    #: 0-based indices into the 25-cell array, row-major.
    cells: list[int]


class LastPick(CamelModel):
    """The most recent number, and who took it."""

    value: int
    player_id: str


class BingoState(CamelModel):
    #: Numbers taken so far, in the order they were chosen. The single source
    #: of truth for what is marked, on every board at once.
    selected: list[int]
    #: Boards by player id, **scoped to the caller**. During a round this holds
    #: the caller's own board and nothing else. The winner's board is added
    #: once the round ends, so the results screen can show the winning lines.
    cards: dict[str, list[int]]
    turn_order: list[str]
    current_turn_index: int
    winner_id: str | None
    #: Every line the winner held, five or more. Empty while play continues.
    winning_lines: list[WinningLine]
    #: Seconds left for the player on turn, or null when nothing is on the
    #: clock. A duration, not a deadline, so a skewed device clock cannot
    #: mis-time a twenty-second turn.
    turn_seconds_remaining: int | None = None
    #: The most recent move. Null before anybody has taken a number.
    last_pick: LastPick | None = None


class RoundResultRow(CamelModel):
    player_id: str
    name: str
    initial: str
    color: AvatarColor
    #: Human-readable achievement, e.g. `Bingo` or `Two lines`.
    note: str
    gain: int


class Room(CamelModel):
    key: str
    game_id: str
    host_id: str
    phase: RoomPhase
    #: 1-based. Equals `settings.rounds` on the final round.
    round: int
    players: list[Player]
    settings: RoomSettings
    #: Present only while a round is live or just finished.
    bingo: BingoState | None = None
    #: Populated only when `phase` is `round-results`.
    last_round: list[RoundResultRow] | None = None
    created_at: str
    expires_at: str


class JoinedRoom(CamelModel):
    """The create and join response. `playerToken` is returned exactly once."""

    room: Room
    player_id: str
    player_token: str


class CreateRoomRequest(CamelModel):
    game_id: str
    settings: RoomSettings
    host_name: Nickname
    host_color: AvatarColor

    @field_validator("host_name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validated_nickname(value)


class JoinRoomRequest(CamelModel):
    name: Nickname
    color: AvatarColor

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validated_nickname(value)


class ActionRequest(CamelModel):
    """The envelope every in-game move travels in.

    One endpoint carries every move of every game. This is the seam that lets a
    new game be added without adding a route: dispatch on `(game_id, type)`.
    """

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class GameSummary(CamelModel):
    """One entry in the public catalogue."""

    id: str
    name: str
    status: Literal["playable", "building"]
    min_players: int
    max_players: int


class SweepResult(CamelModel):
    """What the retention job did, so an operator can see it ran."""

    expired_rooms: int
    anonymised_players: int
    purged_rounds: int


def _validated_nickname(value: str) -> str:
    """Trims, normalises to NFC, and rejects blank or over-long names.

    NFC first, then measure: two visually identical nicknames can be different
    byte strings, and one of them could otherwise take the other's apparent
    name.
    """
    name = normalise_name(value)
    if not name:
        raise ValueError("Enter a nickname so the room knows who you are.")
    if len(name) > 16:
        raise ValueError("Nicknames are at most 16 characters.")
    return name
