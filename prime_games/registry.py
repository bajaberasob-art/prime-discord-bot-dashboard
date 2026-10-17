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


def _initial_connect_four_state(players: list[int]) -> dict[str, Any]:
    return {"board": [None] * 42}


def _reduce_connect_four_action(
    state: dict[str, Any],
    players: list[int],
    actor_id: int,
    action: dict[str, Any],
) -> GameTransition:
    """Drop one checker into a 7-column, 6-row board."""
    board = list(state.get("board") or [])
    if len(board) != 42:
        raise ValueError("invalid_game_state")
    try:
        column = int(action.get("column"))
    except (TypeError, ValueError):
        raise ValueError("invalid_column") from None
    if not 0 <= column < 7:
        raise ValueError("invalid_column")
    if actor_id not in players:
        raise ValueError("not_a_player")

    landing_index = None
    for row in range(5, -1, -1):
        candidate = row * 7 + column
        if board[candidate] is None:
            landing_index = candidate
            break
    if landing_index is None:
        raise ValueError("column_full")

    mark = "X" if actor_id == players[0] else "O"
    board[landing_index] = mark
    next_state = {**state, "board": board}

    def count_direction(row_delta: int, column_delta: int) -> int:
        count = 0
        row = landing_index // 7 + row_delta
        col = landing_index % 7 + column_delta
        while 0 <= row < 6 and 0 <= col < 7 and board[row * 7 + col] == mark:
            count += 1
            row += row_delta
            col += column_delta
        return count

    row_delta = landing_index // 7
    col_delta = landing_index % 7
    for row_step, col_step in ((0, 1), (1, 0), (1, 1), (1, -1)):
        streak = 1 + count_direction(row_step, col_step) + count_direction(-row_step, -col_step)
        if streak >= 4:
            return GameTransition(next_state, None, winner_id=actor_id)

    if all(cell is not None for cell in board):
        return GameTransition(next_state, None, is_draw=True)
    next_player = players[1] if actor_id == players[0] else players[0]
    return GameTransition(next_state, next_player)


def _initial_rps_state(players: list[int]) -> dict[str, Any]:
    return {"choices": {}}


def _reduce_rps_action(
    state: dict[str, Any],
    players: list[int],
    actor_id: int,
    action: dict[str, Any],
) -> GameTransition:
    """Collect each player's choice without revealing it until both have chosen."""
    choice = str(action.get("choice") or "").strip().lower()
    if choice not in {"rock", "paper", "scissors"}:
        raise ValueError("invalid_choice")

    choices = dict(state.get("choices") or {})
    actor_key = str(int(actor_id))
    if actor_key in choices:
        raise ValueError("already_chose")
    if actor_id not in players:
        raise ValueError("not_a_player")

    choices[actor_key] = choice
    next_state = {**state, "choices": choices}
    if len(choices) < 2:
        next_player = players[1] if actor_id == players[0] else players[0]
        return GameTransition(next_state, next_player)

    first_choice = choices.get(str(players[0]))
    second_choice = choices.get(str(players[1]))
    if first_choice not in {"rock", "paper", "scissors"} or second_choice not in {
        "rock", "paper", "scissors"
    }:
        raise ValueError("invalid_game_state")
    if first_choice == second_choice:
        return GameTransition(next_state, None, is_draw=True)

    defeats = {
        "rock": "scissors",
        "paper": "rock",
        "scissors": "paper",
    }
    winner_id = players[0] if defeats[first_choice] == second_choice else players[1]
    return GameTransition(next_state, None, winner_id=winner_id)


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
registry.register(
    GameDefinition(
        key="connect4",
        name="Connect Four · أربعة على التوالي",
        min_players=2,
        max_players=2,
        reward_coins=25,
        initial_state=_initial_connect_four_state,
        reduce_action=_reduce_connect_four_action,
    )
)

registry.register(
    GameDefinition(
        key="rps",
        name="Rock Paper Scissors · حجر ورقة مقص",
        min_players=2,
        max_players=2,
        reward_coins=25,
        initial_state=_initial_rps_state,
        reduce_action=_reduce_rps_action,
    )
)
