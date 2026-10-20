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
    # Infinite Neon XO: retain only each player's three newest pieces.
    pieces = {
        "X": list(state.get("pieces", {}).get("X", [])),
        "O": list(state.get("pieces", {}).get("O", [])),
    }
    # Migrate active games created before the rolling-piece rules existed.
    if not state.get("pieces"):
        for cell_index, cell_mark in enumerate(board):
            if cell_mark in pieces:
                pieces[cell_mark].append(cell_index)
        pieces = {key: value[-3:] for key, value in pieces.items()}
    if len(pieces[mark]) >= 3:
        oldest = pieces[mark].pop(0)
        board[oldest] = None
    board[index] = mark
    pieces[mark].append(index)
    next_state = {**state, "board": board, "pieces": pieces}
    winning_lines = (
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    )
    if any(all(board[cell] == mark for cell in line) for line in winning_lines):
        return GameTransition(next_state, None, winner_id=actor_id)
    # The board is rolling, so a full-board draw is impossible by design.
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
    return {
        "choices": {}, "round": 1, "hp": {str(player): 3 for player in players},
        "abilities_used": {str(player): [] for player in players},
        "active_abilities": {}, "scores": {str(player): 0 for player in players},
    }


def _reduce_rps_action(
    state: dict[str, Any], players: list[int], actor_id: int, action: dict[str, Any],
) -> GameTransition:
    if actor_id not in players:
        raise ValueError("not_a_player")
    key = str(actor_id)
    used = {str(k): list(v) for k, v in (state.get("abilities_used") or {}).items()}
    active = dict(state.get("active_abilities") or {})
    choices = dict(state.get("choices") or {})
    hp = {str(k): int(v) for k, v in (state.get("hp") or {str(p): 3 for p in players}).items()}
    ability = str(action.get("ability") or "")
    if ability in {"double", "shield"}:
        if key in choices:
            raise ValueError("ability_after_choice")
        if ability in used.setdefault(key, []):
            raise ValueError("ability_used")
        used[key].append(ability)
        active[key] = list(active.get(key, [])) + [ability]
        return GameTransition({**state, "abilities_used": used, "active_abilities": active, "hp": hp, "choices": choices}, actor_id)
    choice = str(action.get("choice", "")).strip().lower()
    if choice not in _RPS_CHOICES:
        raise ValueError("invalid_choice")
    if key in choices:
        raise ValueError("already_chose")
    choices[key] = choice
    if len(choices) < 2:
        other = next(player for player in players if str(player) not in choices)
        return GameTransition({**state, "choices": choices, "abilities_used": used, "active_abilities": active, "hp": hp}, other)
    first_choice, second_choice = (choices[str(player)] for player in players)
    scores = {str(k): int(v) for k, v in (state.get("scores") or {}).items()}
    round_winner = None
    if first_choice != second_choice:
        round_winner = players[0] if _RPS_WINS[first_choice] == second_choice else players[1]
        loser = players[1] if round_winner == players[0] else players[0]
        damage = 2 if "double" in (active.get(str(round_winner)) or []) else 1
        if "shield" in (active.get(str(loser)) or []):
            damage = 0
        hp[str(loser)] = max(0, hp.get(str(loser), 3) - damage)
        scores[str(round_winner)] = scores.get(str(round_winner), 0) + 1
    next_state = {**state, "choices": {}, "hp": hp, "scores": scores,
                  "abilities_used": used, "active_abilities": {},
                  "last_round": {"choices": choices, "winner_id": round_winner, "damage": damage if round_winner else 0},
                  "round": int(state.get("round", 1)) + 1}
    defeated = [int(player) for player in players if hp[str(player)] <= 0]
    if defeated:
        winner = next(player for player in players if player not in defeated)
        return GameTransition(next_state, None, winner_id=winner)
    if round_winner is None:
        return GameTransition(next_state, players[0])
    return GameTransition(next_state, players[0])


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




# ── Memory Matrix: find all matching pairs ───────────────────────────────────
_MEMORY_SYMBOLS = ("🍎", "⭐", "🔷", "🌙", "🔥", "🎈", "🍀", "💎", "🎯", "🦋")


def _initial_memory_state(players: list[int]) -> dict[str, Any]:
    symbols = random.sample(list(_MEMORY_SYMBOLS), 8)
    cards = [symbol for symbol in symbols for _ in range(2)]
    random.shuffle(cards)
    return {
        "cards": cards, "matched": [], "revealed": [], "pending_index": None,
        "scores": {str(player): 0 for player in players},
        "pairs": {str(player): 0 for player in players},
        "combo": {str(player): 0 for player in players},
        "last_match": None, "trap_used": {str(player): False for player in players},
        "hint_used": {},
    }


def _reduce_memory_action(state: dict[str, Any], players: list[int], actor_id: int, action: dict[str, Any]) -> GameTransition:
    if actor_id not in players:
        raise ValueError("not_a_player")
    cards = list(state.get("cards") or [])
    if len(cards) != 16:
        raise ValueError("invalid_game_state")
    matched = {int(value) for value in state.get("matched") or []}
    pending = state.get("pending_index")
    scores = {str(k): int(v) for k, v in (state.get("scores") or {}).items()}
    pairs = {str(k): int(v) for k, v in (state.get("pairs") or {}).items()}
    combo = {str(k): int(v) for k, v in (state.get("combo") or {}).items()}
    trap_used = {str(k): bool(v) for k, v in (state.get("trap_used") or {}).items()}
    key = str(actor_id)
    if action.get("trap"):
        if trap_used.get(key, False):
            raise ValueError("trap_used")
        if pending is not None:
            raise ValueError("trap_pending_pair")
        eligible = [i for i in range(len(cards)) if i not in matched and i not in set(state.get("revealed") or [])]
        if len(eligible) < 2:
            raise ValueError("no_trap_targets")
        first, second = random.sample(eligible, 2)
        cards[first], cards[second] = cards[second], cards[first]
        trap_used[key] = True
        next_player = players[1] if actor_id == players[0] else players[0]
        return GameTransition({**state, "cards": cards, "trap_used": trap_used, "trap_event": {"player_id": actor_id, "indices": [first, second]}, "combo": {**combo, key: 0}}, next_player)
    try:
        index = int(action.get("index"))
    except (TypeError, ValueError):
        raise ValueError("invalid_card") from None
    if not 0 <= index < len(cards):
        raise ValueError("invalid_card")
    if index in matched:
        raise ValueError("card_matched")
    if pending is None:
        return GameTransition({**state, "revealed": [index], "pending_index": index, "last_match": None, "trap_event": None}, actor_id)
    pending = int(pending)
    if index == pending:
        raise ValueError("same_card")
    pair = [pending, index]
    if cards[pending] == cards[index]:
        matched.update(pair)
        combo[key] = combo.get(key, 0) + 1
        multiplier = min(combo[key], 3)
        scores[key] = scores.get(key, 0) + multiplier
        pairs[key] = pairs.get(key, 0) + 1
        next_state = {**state, "matched": sorted(matched), "revealed": [], "pending_index": None,
                      "scores": scores, "pairs": pairs, "combo": combo, "last_match": True,
                      "last_pair": pair, "combo_multiplier": multiplier, "trap_event": None}
        if len(matched) == len(cards):
            first_score, second_score = scores.get(str(players[0]), 0), scores.get(str(players[1]), 0)
            if first_score == second_score:
                return GameTransition(next_state, None, is_draw=True)
            return GameTransition(next_state, None, winner_id=players[0] if first_score > second_score else players[1])
        return GameTransition(next_state, actor_id)
    combo[key] = 0
    next_state = {**state, "revealed": pair, "pending_index": None, "last_match": False,
                  "last_pair": pair, "combo": combo, "combo_multiplier": 0, "trap_event": None}
    return GameTransition(next_state, players[1] if actor_id == players[0] else players[0])


# ── Dice Duel: five rounds of luck ───────────────────────────────────────────
def _initial_dice_state(players: list[int]) -> dict[str, Any]:
    return {
        "round": 1, "round_rolls": {}, "committed": [],
        "scores": {str(player): 0 for player in players},
        "hp": {str(player): 3 for player in players},
        "abilities_used": {str(player): [] for player in players},
        "active_abilities": {}, "last_round": None,
    }


def _reduce_dice_action(state: dict[str, Any], players: list[int], actor_id: int, action: dict[str, Any]) -> GameTransition:
    if actor_id not in players:
        raise ValueError("not_a_player")
    key = str(actor_id)
    rolls = dict(state.get("round_rolls") or {})
    committed = set(str(v) for v in state.get("committed") or [])
    used = {str(k): list(v) for k, v in (state.get("abilities_used") or {}).items()}
    active = dict(state.get("active_abilities") or {})
    hp = {str(k): int(v) for k, v in (state.get("hp") or {str(p): 3 for p in players}).items()}
    ability = str(action.get("ability") or "")
    if ability in {"double", "shield"}:
        if key in committed:
            raise ValueError("ability_after_lock")
        if ability in used.setdefault(key, []):
            raise ValueError("ability_used")
        used[key].append(ability)
        active[key] = list(active.get(key, [])) + [ability]
        return GameTransition({**state, "round_rolls": rolls, "committed": sorted(committed), "abilities_used": used, "active_abilities": active, "hp": hp}, actor_id)
    if action.get("roll") in (True, "roll"):
        if key in rolls:
            raise ValueError("already_rolled")
        rolls[key] = random.randint(1, 6)
        return GameTransition({**state, "round_rolls": rolls, "committed": sorted(committed), "abilities_used": used, "active_abilities": active, "hp": hp}, actor_id)
    if action.get("reroll"):
        if key not in rolls:
            raise ValueError("roll_first")
        if "reroll" in used.setdefault(key, []):
            raise ValueError("ability_used")
        used[key].append("reroll")
        rolls[key] = random.randint(1, 6)
        return GameTransition({**state, "round_rolls": rolls, "committed": sorted(committed), "abilities_used": used, "active_abilities": active, "hp": hp}, actor_id)
    if action.get("lock"):
        if key not in rolls:
            raise ValueError("roll_first")
        committed.add(key)
        if len(committed) < 2:
            other = next(player for player in players if str(player) not in committed)
            return GameTransition({**state, "round_rolls": rolls, "committed": sorted(committed), "abilities_used": used, "active_abilities": active, "hp": hp}, other)
        first_roll, second_roll = int(rolls[str(players[0])]), int(rolls[str(players[1])])
        winner = players[0] if first_roll > second_roll else players[1] if second_roll > first_roll else None
        scores = {str(k): int(v) for k, v in (state.get("scores") or {}).items()}
        damage = 0
        if winner is not None:
            loser = players[1] if winner == players[0] else players[0]
            damage = 2 if "double" in (active.get(str(winner)) or []) else 1
            if "shield" in (active.get(str(loser)) or []):
                damage = 0
            hp[str(loser)] = max(0, hp.get(str(loser), 3) - damage)
            scores[str(winner)] = scores.get(str(winner), 0) + 1
        next_state = {**state, "round": int(state.get("round", 1)) + 1, "round_rolls": {}, "committed": [],
                      "scores": scores, "hp": hp, "abilities_used": used, "active_abilities": {},
                      "last_round": {"number": int(state.get("round", 1)), "rolls": rolls, "winner_id": winner, "draw": winner is None, "damage": damage}}
        defeated = [int(player) for player in players if hp[str(player)] <= 0]
        if defeated:
            return GameTransition(next_state, None, winner_id=next(player for player in players if player not in defeated))
        return GameTransition(next_state, players[0])
    raise ValueError("invalid_roll")


# ── Word Scramble: decode the hidden word ────────────────────────────────────
_WORD_BANK: tuple[tuple[str, tuple[str, str, str]], ...] = (
    ("قمر", ("نجم", "شمس", "بحر")),
    ("مدرسة", ("جامعة", "مكتبة", "حديقة")),
    ("حاسوب", ("هاتف", "لوحة", "شاشة")),
    ("مطر", ("ريح", "ثلج", "غيم")),
    ("طائرة", ("سيارة", "سفينة", "قطار")),
    ("مفتاح", ("نافذة", "باب", "قفل")),
    ("نجمة", ("كوكب", "مجرة", "سحابة")),
    ("مكتبة", ("مدرسة", "مطبخ", "ملعب")),
    ("صحراء", ("غابة", "جزيرة", "مدينة")),
    ("بحر", ("نهر", "بحيرة", "جبل")),
    ("تفاحة", ("موزة", "برتقالة", "عنب")),
    ("هاتف", ("حاسوب", "كاميرا", "ساعة")),
)


def _scramble_word(word: str) -> str:
    letters = list(word)
    scrambled = letters[:]
    for _ in range(8):
        random.shuffle(scrambled)
        if scrambled != letters:
            break
    if scrambled == letters and len(scrambled) > 1:
        scrambled = scrambled[1:] + scrambled[:1]
    return "".join(scrambled)


def _initial_word_state(players: list[int]) -> dict[str, Any]:
    puzzles = []
    for word, distractors in random.sample(list(_WORD_BANK), 6):
        options = [word, *distractors]
        random.shuffle(options)
        puzzles.append({
            "scrambled": _scramble_word(word),
            "options": options,
            "correct": options.index(word),
        })
    return {
        "round_index": 0,
        "puzzles": puzzles,
        "scores": {str(player): 0 for player in players},
        "last_answer": None,
    }


def _reduce_word_action(state: dict[str, Any], players: list[int], actor_id: int, action: dict[str, Any]) -> GameTransition:
    if actor_id not in players:
        raise ValueError("not_a_player")
    puzzles = list(state.get("puzzles") or [])
    round_index = int(state.get("round_index") or 0)
    if not 0 <= round_index < len(puzzles):
        raise ValueError("invalid_game_state")
    puzzle = puzzles[round_index]
    scores = {str(k): int(v) for k, v in (state.get("scores") or {}).items()}
    if action.get("hint"):
        hints = dict(state.get("hints") or {})
        hint_key = str(round_index)
        if hint_key in hints:
            raise ValueError("hint_used")
        word = str(puzzle.get("options", [""])[int(puzzle.get("correct", 0))])
        hints[hint_key] = word[0] if word else ""
        hint_costs = dict(state.get("hint_costs") or {})
        had_point = scores.get(str(actor_id), 0) > 0
        if had_point:
            scores[str(actor_id)] -= 1
        hint_costs[hint_key] = had_point
        next_state = {**state, "hints": hints, "hint_costs": hint_costs, "scores": scores, "hint_used_by": actor_id}
        return GameTransition(next_state, actor_id)
    if "answer_text" in action:
        submitted = "".join(str(action.get("answer_text") or "").split()).casefold()
        options = puzzle.get("options") or []
        correct_text = str(options[int(puzzle.get("correct", -1))]) if options else ""
        is_correct = bool(submitted) and submitted == "".join(correct_text.split()).casefold()
    else:
        try:
            answer = int(action.get("answer"))
        except (TypeError, ValueError):
            raise ValueError("invalid_answer") from None
        options = puzzle.get("options") or []
        if not 0 <= answer < len(options):
            raise ValueError("invalid_answer")
        is_correct = answer == int(puzzle.get("correct", -1))
    if is_correct:
        hinted = str(round_index) in (state.get("hints") or {})
        # A hint costs one point, but a correct answer still earns the round point.
        # If the player had no points to spend, the hint forfeits this round's point.
        round_award = 0 if hinted and not (state.get("hint_costs") or {}).get(str(round_index), False) else 1
        scores[str(actor_id)] = scores.get(str(actor_id), 0) + round_award
    next_index = round_index + 1
    next_state = {**state, "round_index": next_index, "scores": scores,
                  "last_answer": {"player_id": actor_id, "round": round_index + 1, "correct": is_correct}}
    if next_index >= len(puzzles):
        first_score, second_score = scores.get(str(players[0]), 0), scores.get(str(players[1]), 0)
        if first_score == second_score:
            return GameTransition(next_state, None, is_draw=True)
        return GameTransition(next_state, None, winner_id=players[0] if first_score > second_score else players[1])
    return GameTransition(next_state, players[1] if actor_id == players[0] else players[0])






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
registry.register(GameDefinition(
    key="memory", name="تحدّي الذاكرة", min_players=2, max_players=2,
    reward_coins=20, initial_state=_initial_memory_state, reduce_action=_reduce_memory_action,
))
registry.register(GameDefinition(
    key="dice", name="مبارزة النرد", min_players=2, max_players=2,
    reward_coins=15, initial_state=_initial_dice_state, reduce_action=_reduce_dice_action,
))
registry.register(GameDefinition(
    key="word", name="فكّ الكلمة", min_players=2, max_players=2,
    reward_coins=15, initial_state=_initial_word_state, reduce_action=_reduce_word_action,
))
