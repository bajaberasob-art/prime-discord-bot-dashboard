"""Plugin registry for PRIME's reusable Discord mini-game engine."""
from __future__ import annotations

import random
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


# ── XO ──────────────────────────────────────────────────────────────────────

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


# ── Four in a row ───────────────────────────────────────────────────────────

def _initial_connect4_state(players: list[int]) -> dict[str, Any]:
    return {"board": [None] * 42, "rows": 6, "columns": 7}


def _reduce_connect4_action(
    state: dict[str, Any],
    players: list[int],
    actor_id: int,
    action: dict[str, Any],
) -> GameTransition:
    board = list(state.get("board") or [])
    if len(board) != 42 or actor_id not in players:
        raise ValueError("invalid_game_state" if actor_id in players else "not_a_player")
    try:
        column = int(action.get("column"))
    except (TypeError, ValueError):
        raise ValueError("invalid_column") from None
    if not 0 <= column < 7:
        raise ValueError("invalid_column")

    row = next((r for r in range(5, -1, -1) if board[r * 7 + column] is None), None)
    if row is None:
        raise ValueError("column_full")
    mark = "R" if actor_id == players[0] else "Y"
    board[row * 7 + column] = mark
    next_state = {**state, "board": board, "last_move": {"row": row, "column": column}}

    def has_four(delta_row: int, delta_column: int) -> bool:
        for start in range(-3, 1):
            cells = [
                (row + (start + offset) * delta_row,
                 column + (start + offset) * delta_column)
                for offset in range(4)
            ]
            if all(
                0 <= r < 6 and 0 <= c < 7 and board[r * 7 + c] == mark
                for r, c in cells
            ):
                return True
        return False

    if any(has_four(dr, dc) for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1))):
        return GameTransition(next_state, None, winner_id=actor_id)
    if all(cell is not None for cell in board):
        return GameTransition(next_state, None, is_draw=True)
    next_player = players[1] if actor_id == players[0] else players[0]
    return GameTransition(next_state, next_player)


# ── Rock, paper, scissors: sealed picks ──────────────────────────────────────

_RPS_CHOICES = {"rock", "paper", "scissors"}
_RPS_WINS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}


def _initial_rps_state(players: list[int]) -> dict[str, Any]:
    return {"choices": {}}


def _reduce_rps_action(
    state: dict[str, Any],
    players: list[int],
    actor_id: int,
    action: dict[str, Any],
) -> GameTransition:
    if actor_id not in players:
        raise ValueError("not_a_player")
    choice = str(action.get("choice", "")).strip().lower()
    if choice not in _RPS_CHOICES:
        raise ValueError("invalid_choice")
    choices = dict(state.get("choices") or {})
    actor_key = str(actor_id)
    if actor_key in choices:
        raise ValueError("already_chose")
    choices[actor_key] = choice
    next_state = {**state, "choices": choices}
    if len(choices) < 2:
        next_player = next(player for player in players if str(player) not in choices)
        return GameTransition(next_state, next_player)

    first_choice, second_choice = (choices[str(player)] for player in players)
    if first_choice == second_choice:
        next_state["round_result"] = "draw"
        return GameTransition(next_state, None, is_draw=True)
    winner_id = players[0] if _RPS_WINS[first_choice] == second_choice else players[1]
    next_state["round_result"] = "winner"
    return GameTransition(next_state, None, winner_id=winner_id)


# ── Quiz Blitz ───────────────────────────────────────────────────────────────

_QUIZ_BANK: tuple[dict[str, Any], ...] = (
    {"question": "ما عاصمة اليابان؟", "options": ["سيول", "طوكيو", "بكين", "بانكوك"], "correct": 1},
    {"question": "ما أكبر كوكب في المجموعة الشمسية؟", "options": ["الأرض", "المريخ", "المشتري", "زحل"], "correct": 2},
    {"question": "ما الكوكب المعروف بالكوكب الأحمر؟", "options": ["الزهرة", "المريخ", "عطارد", "نبتون"], "correct": 1},
    {"question": "كم دقيقة في الساعة الواحدة؟", "options": ["30", "45", "60", "90"], "correct": 2},
    {"question": "ما الصيغة الكيميائية للماء؟", "options": ["H₂O", "CO₂", "O₂", "NaCl"], "correct": 0},
    {"question": "في أي قارة تقع مصر؟", "options": ["آسيا", "أوروبا", "أمريكا الجنوبية", "أفريقيا"], "correct": 3},
    {"question": "ما أكبر محيط على الأرض؟", "options": ["الهادئ", "الأطلسي", "الهندي", "المتجمد الشمالي"], "correct": 0},
    {"question": "كم ضلعًا للشكل السداسي؟", "options": ["خمسة", "ستة", "سبعة", "ثمانية"], "correct": 1},
    {"question": "ما اللغة الرسمية في البرازيل؟", "options": ["الإسبانية", "الإنجليزية", "البرتغالية", "الفرنسية"], "correct": 2},
    {"question": "أي حيوان يُعرف بسفينة الصحراء؟", "options": ["الحصان", "الجمل", "الغزال", "اللاما"], "correct": 1},
)


def _initial_quiz_state(players: list[int]) -> dict[str, Any]:
    return {
        "question_index": 0,
        "questions": random.sample(list(_QUIZ_BANK), k=6),
        "scores": {str(player): 0 for player in players},
        "last_answer": None,
    }


def _reduce_quiz_action(
    state: dict[str, Any],
    players: list[int],
    actor_id: int,
    action: dict[str, Any],
) -> GameTransition:
    if actor_id not in players:
        raise ValueError("not_a_player")
    questions = list(state.get("questions") or [])
    question_index = int(state.get("question_index") or 0)
    if not 0 <= question_index < len(questions):
        raise ValueError("invalid_game_state")
    try:
        answer = int(action.get("answer"))
    except (TypeError, ValueError):
        raise ValueError("invalid_answer") from None
    options = questions[question_index].get("options") or []
    if not 0 <= answer < len(options):
        raise ValueError("invalid_answer")

    scores = {str(key): int(value) for key, value in (state.get("scores") or {}).items()}
    correct_answer = int(questions[question_index].get("correct", -1))
    is_correct = answer == correct_answer
    if is_correct:
        scores[str(actor_id)] = scores.get(str(actor_id), 0) + 1
    next_index = question_index + 1
    next_state = {
        **state,
        "question_index": next_index,
        "scores": scores,
        "last_answer": {
            "player_id": actor_id,
            "question_number": question_index + 1,
            "correct": is_correct,
            "correct_answer": correct_answer,
        },
    }
    if next_index >= len(questions):
        first_score = scores.get(str(players[0]), 0)
        second_score = scores.get(str(players[1]), 0)
        if first_score == second_score:
            return GameTransition(next_state, None, is_draw=True)
        winner_id = players[0] if first_score > second_score else players[1]
        return GameTransition(next_state, None, winner_id=winner_id)

    next_player = players[1] if actor_id == players[0] else players[0]
    return GameTransition(next_state, next_player)


registry = GameRegistry()
registry.register(GameDefinition(
    key="xo", name="XO · تيك تاك تو", min_players=2, max_players=2,
    reward_coins=25, initial_state=_initial_xo_state, reduce_action=_reduce_xo_action,
))
registry.register(GameDefinition(
    key="connect4", name="أربعة على التوالي", min_players=2, max_players=2,
    reward_coins=20, initial_state=_initial_connect4_state, reduce_action=_reduce_connect4_action,
))
registry.register(GameDefinition(
    key="rps", name="حجر ورقة مقص", min_players=2, max_players=2,
    reward_coins=10, initial_state=_initial_rps_state, reduce_action=_reduce_rps_action,
))
registry.register(GameDefinition(
    key="quiz", name="تحدّي المعلومات", min_players=2, max_players=2,
    reward_coins=15, initial_state=_initial_quiz_state, reduce_action=_reduce_quiz_action,
))
