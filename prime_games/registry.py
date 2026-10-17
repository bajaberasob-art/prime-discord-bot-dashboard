"""Plugin registry for PRIME's reusable Discord mini-game engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class GameTransition:
    state: dict[str, Any]
    next_player_id: int | None
    winner_id: int | None = None
    is_draw: bool = False


Reducer = Callable[
    [dict[str, Any], list[int], int, dict[str, Any]],
    GameTransition,
]
StateFactory = Callable[[list[int]], dict[str, Any]]


@dataclass(frozen=True)
class GameDefinition:
    key: str
    name: str
    min_players: int
    max_players: int
    reward_coins: int
    initial_state: StateFactory
    reduce_action: Reducer


class GameRegistry:
    """Small explicit registry; games share session/storage/reward semantics."""

    def __init__(self) -> None:
        self._games: dict[str, GameDefinition] = {}

    def register(self, definition: GameDefinition) -> None:
        key = definition.key.strip().lower()
        if not key or key in self._games:
            raise ValueError(f"duplicate or empty game key: {key!r}")
        if definition.min_players < 1 or definition.max_players < definition.min_players:
            raise ValueError("invalid player bounds")
        if definition.reward_coins < 0:
            raise ValueError("game rewards cannot be negative")
        self._games[key] = definition

    def get(self, key: str) -> GameDefinition:
        try:
            return self._games[str(key).strip().lower()]
        except KeyError as error:
            raise ValueError(f"unknown game key: {key}") from error

    def all(self) -> tuple[GameDefinition, ...]:
        return tuple(self._games[key] for key in sorted(self._games))


def _initial_xo_state(players: list[int]) -> dict[str, Any]:
    return {"board": [None] * 9}


def _reduce_xo_action(
    state: dict[str, Any],
    players: list[int],
    actor_id: int,
    action: dict[str, Any],
) -> GameTransition:
    board = list(state.get("board") or [])
    if len(board) != 9:
        raise ValueError("invalid_game_state")
    try:
        index = int(action.get("index"))
    except (TypeError, ValueError):
        raise ValueError("invalid_square") from None
    if not 0 <= index < 9:
        raise ValueError("invalid_square")
    if board[index] is not None:
        raise ValueError("square_taken")
    if actor_id not in players:
        raise ValueError("not_a_player")

    mark = "X" if actor_id == players[0] else "O"
    board[index] = mark
    next_state = {**state, "board": board}
    winning_lines = (
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    )
    if any(all(board[cell] == mark for cell in line) for line in winning_lines):
        return GameTransition(next_state, None, winner_id=actor_id)
    if all(cell is not None for cell in board):
        return GameTransition(next_state, None, is_draw=True)
    next_player = players[1] if actor_id == players[0] else players[0]
    return GameTransition(next_state, next_player)


registry = GameRegistry()
registry.register(
    GameDefinition(
        key="xo",
        name="XO · Tic-Tac-Toe",
        min_players=2,
        max_players=2,
        reward_coins=25,
        initial_state=_initial_xo_state,
        reduce_action=_reduce_xo_action,
    )
)
