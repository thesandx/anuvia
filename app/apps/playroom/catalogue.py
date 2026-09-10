"""The game catalogue, as data.

`status` is what a host can open a room for. It is `playable` only where an
engine exists in `engines.py`, so flipping a row to `playable` without writing
the engine would route players into a room that cannot be played. That is why
the flag lives beside the engine registry and not only in the database.

**These rows are seeded by migration `a1f3c7d90b21`.** The migration carries its
own literal copy on purpose: a migration is a frozen snapshot of a moment, and
importing live application code into one makes an old migration change meaning
when the code moves on. Keep the two in step by hand when a game is added, and
`test_the_catalogue_matches_the_seeded_rows` will tell you when they are not.
"""

from typing import TypedDict


class GameRow(TypedDict):
    id: str
    name: str
    status: str
    min_players: int
    max_players: int


CATALOGUE: list[GameRow] = [
    {"id": "bingo", "name": "Bingo", "status": "playable", "min_players": 2, "max_players": 20},
    {
        "id": "scribble",
        "name": "Scribble",
        "status": "building",
        "min_players": 4,
        "max_players": 12,
    },
    {"id": "ttt", "name": "Tic-tac-toe", "status": "building", "min_players": 2, "max_players": 2},
]
