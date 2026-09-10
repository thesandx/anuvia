"""Bingo rules, pure functions, no I/O, no randomness the caller cannot control.

This is a direct port of `lib/bingo.ts` in the Playroom frontend. That file is
the reference implementation and it has tests behind it, so this module keeps
its names, its cell ordering and its line indices. Do not re-derive the rules
from prose.

The board is a 5x5 grid holding the numbers 1 to 25, each exactly once,
shuffled independently for every player. There is no free square.

A player wins by completing FIVE lines, any mix of rows, columns and
diagonals, out of the twelve that exist. One letter of B-I-N-G-O per completed
line. Lines share cells, so a single number can complete two at once: count the
lines, never assume one per pick.

Cells are stored row-major, so index `i` sits at row `i // 5` and column
`i % 5`, the same order the client's 5-column CSS grid renders them in.
"""

import secrets
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

GRID_SIZE = 5
#: Lines needed to win, one per letter of B-I-N-G-O.
LINES_TO_WIN = 5
CARD_SIZE = GRID_SIZE * GRID_SIZE
#: Numbers run 1..25, and every one of them appears on every board.
LOWEST_NUMBER = 1
HIGHEST_NUMBER = CARD_SIZE

LineKind = Literal["row", "column", "diagonal"]

#: Injectable source of randomness so tests can be deterministic.
RandomInt = Callable[[int], int]


@dataclass(frozen=True, slots=True)
class WinningLine:
    """One completed line on a board."""

    kind: LineKind
    #: Row/column number (1-based), or 1 and 2 for the two diagonals.
    index: int
    #: The five cell indices that make up the line.
    cells: tuple[int, ...]

    def as_payload(self) -> dict:
        """The wire shape the client's `WinningLine` type expects."""
        return {"kind": self.kind, "index": self.index, "cells": list(self.cells)}


def _default_random(max_exclusive: int) -> int:
    """A random integer in [0, max_exclusive)."""
    return secrets.randbelow(max_exclusive)


def create_card(random: RandomInt = _default_random) -> list[int]:
    """Deal one board: the numbers 1..25 in a random order.

    Fisher-Yates, so every arrangement is equally likely and, because the pool
    is a permutation of 1..25, every number appears exactly once by
    construction rather than by a uniqueness check afterwards.
    """
    cells = list(range(LOWEST_NUMBER, LOWEST_NUMBER + CARD_SIZE))
    for index in range(len(cells) - 1, 0, -1):
        swap = random(index + 1)
        cells[index], cells[swap] = cells[swap], cells[index]
    return cells


def _build_lines() -> tuple[WinningLine, ...]:
    """Builds the twelve winning lines once, at import."""
    lines: list[WinningLine] = []

    for row in range(GRID_SIZE):
        lines.append(
            WinningLine(
                kind="row",
                index=row + 1,
                cells=tuple(row * GRID_SIZE + column for column in range(GRID_SIZE)),
            )
        )

    for column in range(GRID_SIZE):
        lines.append(
            WinningLine(
                kind="column",
                index=column + 1,
                cells=tuple(row * GRID_SIZE + column for row in range(GRID_SIZE)),
            )
        )

    lines.append(
        WinningLine(
            kind="diagonal",
            index=1,
            cells=tuple(step * GRID_SIZE + step for step in range(GRID_SIZE)),
        )
    )
    lines.append(
        WinningLine(
            kind="diagonal",
            index=2,
            cells=tuple(step * GRID_SIZE + (GRID_SIZE - 1 - step) for step in range(GRID_SIZE)),
        )
    )

    return tuple(lines)


WINNING_LINES = _build_lines()
#: The twelve lines that exist on a 5x5 board.
TOTAL_LINES = len(WINNING_LINES)


def is_playable_number(value: object) -> bool:
    """True when a value is an integer within the playable range.

    `bool` is excluded deliberately: `True` is an `int` in Python, and a JSON
    `true` must not pass as the number 1.
    """
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and (LOWEST_NUMBER <= value <= HIGHEST_NUMBER)
    )


def find_winning_lines(card: Sequence[int], selected: Iterable[int]) -> list[WinningLine]:
    """Every complete line on this board, given what has been selected.

    Reads only the board and the globally selected numbers, so a client cannot
    manufacture a win by sending its own idea of which cells are marked.
    """
    taken = set(selected)
    return [
        line
        for line in WINNING_LINES
        if all(cell < len(card) and card[cell] in taken for cell in line.cells)
    ]


def has_bingo(card: Sequence[int], selected: Iterable[int]) -> bool:
    """True when this board has the five completed lines a win needs."""
    return len(find_winning_lines(card, selected)) >= LINES_TO_WIN
