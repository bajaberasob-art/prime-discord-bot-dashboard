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




# ── Memory Matrix: find all matching pairs ───────────────────────────────────
_MEMORY_SYMBOLS = ("🍎", "⭐", "🔷", "🌙", "🔥", "🎈", "🍀", "💎", "🎯", "🦋")


def _initial_memory_state(players: list[int]) -> dict[str, Any]:
    symbols = random.sample(list(_MEMORY_SYMBOLS), 8)
    cards = [symbol for symbol in symbols for _ in range(2)]
    random.shuffle(cards)
    return {
        "cards": cards,
        "matched": [],
        "revealed": [],
        "pending_index": None,
        "scores": {str(player): 0 for player in players},
        "last_match": None,
    }


def _reduce_memory_action(
    state: dict[str, Any],
    players: list[int],
    actor_id: int,
    action: dict[str, Any],
) -> GameTransition:
    if actor_id not in players:
        raise ValueError("not_a_player")
    cards = list(state.get("cards") or [])
    if len(cards) != 16:
        raise ValueError("invalid_game_state")
    try:
        index = int(action.get("index"))
    except (TypeError, ValueError):
        raise ValueError("invalid_card") from None
    if not 0 <= index < len(cards):
        raise ValueError("invalid_card")

    matched = {int(value) for value in state.get("matched") or []}
    if index in matched:
        raise ValueError("card_matched")
    pending = state.get("pending_index")
    if pending is None:
        next_state = {
            **state,
            "revealed": [index],
            "pending_index": index,
            "last_match": None,
        }
        # The same player must choose the second card in their pair attempt.
        return GameTransition(next_state, actor_id)
    pending = int(pending)
    if index == pending:
        raise ValueError("same_card")

    pair = [pending, index]
    scores = {str(key): int(value) for key, value in (state.get("scores") or {}).items()}
    if cards[pending] == cards[index]:
        matched.update(pair)
        scores[str(actor_id)] = scores.get(str(actor_id), 0) + 1
        next_state = {
            **state,
            "matched": sorted(matched),
            "revealed": [],
            "pending_index": None,
            "scores": scores,
            "last_match": True,
            "last_pair": pair,
        }
        if len(matched) == len(cards):
            first_score = scores.get(str(players[0]), 0)
            second_score = scores.get(str(players[1]), 0)
            if first_score == second_score:
                return GameTransition(next_state, None, is_draw=True)
            winner_id = players[0] if first_score > second_score else players[1]
            return GameTransition(next_state, None, winner_id=winner_id)
        # A successful pair earns another turn, matching standard memory-game rules.
        return GameTransition(next_state, actor_id)
    else:
        # Keep both cards visible until the next player makes their first pick.
        next_state = {
            **state,
            "revealed": pair,
            "pending_index": None,
            "last_match": False,
            "last_pair": pair,
        }
    next_player = players[1] if actor_id == players[0] else players[0]
    return GameTransition(next_state, next_player)


# ── Dice Duel: five rounds of luck ───────────────────────────────────────────
def _initial_dice_state(players: list[int]) -> dict[str, Any]:
    return {
        "round": 1,
        "round_rolls": {},
        "scores": {str(player): 0 for player in players},
        "last_round": None,
    }


def _reduce_dice_action(
    state: dict[str, Any],
    players: list[int],
    actor_id: int,
    action: dict[str, Any],
) -> GameTransition:
    if actor_id not in players:
        raise ValueError("not_a_player")
    if action.get("roll") not in (True, "roll"):
        raise ValueError("invalid_roll")
    round_number = int(state.get("round") or 1)
    if not 1 <= round_number <= 5:
        raise ValueError("invalid_game_state")
    rolls = dict(state.get("round_rolls") or {})
    actor_key = str(actor_id)
    if actor_key in rolls:
        raise ValueError("already_rolled")
    rolls[actor_key] = random.randint(1, 6)
    if len(rolls) < 2:
        next_state = {**state, "round_rolls": rolls}
        next_player = players[1] if actor_id == players[0] else players[0]
        return GameTransition(next_state, next_player)
    if len(rolls) != 2:
        raise ValueError("invalid_game_state")

    first_roll = int(rolls[str(players[0])])
    second_roll = int(rolls[str(players[1])])
    scores = {str(key): int(value) for key, value in (state.get("scores") or {}).items()}
    round_winner = None
    if first_roll > second_roll:
        scores[str(players[0])] = scores.get(str(players[0]), 0) + 1
        round_winner = players[0]
    elif second_roll > first_roll:
        scores[str(players[1])] = scores.get(str(players[1]), 0) + 1
        round_winner = players[1]
    next_state = {
        **state,
        "round": round_number,
        "round_rolls": {},
        "scores": scores,
        "last_round": {
            "number": round_number,
            "rolls": rolls,
            "winner_id": round_winner,
            "draw": round_winner is None,
        },
    }
    if round_number == 5:
        first_score = scores.get(str(players[0]), 0)
        second_score = scores.get(str(players[1]), 0)
        if first_score == second_score:
            return GameTransition(next_state, None, is_draw=True)
        winner_id = players[0] if first_score > second_score else players[1]
        return GameTransition(next_state, None, winner_id=winner_id)
    next_state["round"] = round_number + 1
    return GameTransition(next_state, players[0])


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


def _reduce_word_action(
    state: dict[str, Any],
    players: list[int],
    actor_id: int,
    action: dict[str, Any],
) -> GameTransition:
    if actor_id not in players:
        raise ValueError("not_a_player")
    puzzles = list(state.get("puzzles") or [])
    round_index = int(state.get("round_index") or 0)
    if not 0 <= round_index < len(puzzles):
        raise ValueError("invalid_game_state")
    try:
        answer = int(action.get("answer"))
    except (TypeError, ValueError):
        raise ValueError("invalid_answer") from None
    puzzle = puzzles[round_index]
    options = puzzle.get("options") or []
    if not 0 <= answer < len(options):
        raise ValueError("invalid_answer")

    scores = {str(key): int(value) for key, value in (state.get("scores") or {}).items()}
    is_correct = answer == int(puzzle.get("correct", -1))
    if is_correct:
        scores[str(actor_id)] = scores.get(str(actor_id), 0) + 1
    next_index = round_index + 1
    next_state = {
        **state,
        "round_index": next_index,
        "scores": scores,
        "last_answer": {
            "player_id": actor_id,
            "round": round_index + 1,
            "correct": is_correct,
        },
    }
    if next_index >= len(puzzles):
        first_score = scores.get(str(players[0]), 0)
        second_score = scores.get(str(players[1]), 0)
        if first_score == second_score:
            return GameTransition(next_state, None, is_draw=True)
        winner_id = players[0] if first_score > second_score else players[1]
        return GameTransition(next_state, None, winner_id=winner_id)
    next_player = players[1] if actor_id == players[0] else players[0]
    return GameTransition(next_state, next_player)




# ── Emoji Decode: guess the hidden character or title ────────────────────────
_EMOJI_BANK: tuple[dict[str, Any], ...] = (
    {"clue": "⚽ 🇦🇷 🐐", "hint": "لاعب كرة قدم أسطوري", "options": ["ليونيل ميسي", "كريستيانو رونالدو", "كيليان مبابي", "نيمار"], "correct": 0},
    {"clue": "🧽 🍍 🌊", "hint": "شخصية كرتونية تعيش تحت البحر", "options": ["توم وجيري", "سبونج بوب", "شريك", "البحث عن نيمو"], "correct": 1},
    {"clue": "🕷️ 🕸️ 🦸", "hint": "بطل خارق يتسلق الجدران", "options": ["باتمان", "سبايدرمان", "سوبرمان", "آيرون مان"], "correct": 1},
    {"clue": "🧊 🚢 💔", "hint": "فيلم شهير عن سفينة", "options": ["تايتانيك", "فروزن", "قراصنة الكاريبي", "جومانجي"], "correct": 0},
    {"clue": "🧙 ⚡ 🦉", "hint": "ساحر مشهور ونظارته دائرية", "options": ["سيد الخواتم", "هاري بوتر", "علاء الدين", "بينوكيو"], "correct": 1},
    {"clue": "🦁 👑 🌅", "hint": "فيلم رسوم متحركة عن أمير الغابة", "options": ["مدغشقر", "الأسد الملك", "زوتوبيا", "كتاب الأدغال"], "correct": 1},
    {"clue": "🟢 👹 🫏", "hint": "غول أخضر وصديقه الحمار", "options": ["شريك", "هالك", "الوحش", "مستر بين"], "correct": 0},
    {"clue": "🦇 🌃 🦸", "hint": "حامي مدينة غوثام", "options": ["فلاش", "باتمان", "ثور", "سبايدرمان"], "correct": 1},
    {"clue": "🦖 🌴 🚙", "hint": "فيلم مغامرة في حديقة ديناصورات", "options": ["جوراسيك بارك", "أفاتار", "كينغ كونغ", "مدغشقر"], "correct": 0},
    {"clue": "🍫 🏭 🎩", "hint": "مصنع شوكولاتة وصاحبه يرتدي قبعة", "options": ["راتاتوي", "ويلي ونكا", "قصة لعبة", "علاء الدين"], "correct": 1},
)


def _initial_emoji_state(players: list[int]) -> dict[str, Any]:
    puzzles = []
    for item in random.sample(list(_EMOJI_BANK), 5):
        options = list(item["options"])
        answer = options[int(item["correct"])]
        random.shuffle(options)
        puzzles.append({
            "clue": item["clue"],
            "hint": item["hint"],
            "options": options,
            "correct": options.index(answer),
        })
    return {
        "round_index": 0,
        "puzzles": puzzles,
        "scores": {str(player): 0 for player in players},
        "last_answer": None,
    }


def _reduce_emoji_action(
    state: dict[str, Any], players: list[int], actor_id: int, action: dict[str, Any]
) -> GameTransition:
    # Both games share the same tested turn, score, and finish semantics.
    return _reduce_word_action(state, players, actor_id, action)


# ── Treasure Hunt: hidden crates with risks and rewards ───────────────────────
_TREASURE_POOL: tuple[dict[str, Any], ...] = (
    {"symbol": "👑", "points": 8},
    {"symbol": "💎", "points": 5},
    {"symbol": "💎", "points": 5},
    {"symbol": "💎", "points": 5},
    {"symbol": "🪙", "points": 3},
    {"symbol": "🪙", "points": 3},
    {"symbol": "🪙", "points": 3},
    {"symbol": "🪙", "points": 3},
    {"symbol": "🧭", "points": 2},
    {"symbol": "🧭", "points": 2},
    {"symbol": "🧭", "points": 2},
    {"symbol": "🧭", "points": 2},
    {"symbol": "🕳️", "points": -2},
    {"symbol": "🕳️", "points": -2},
    {"symbol": "🕳️", "points": -2},
    {"symbol": "🕳️", "points": -2},
)


def _initial_treasure_state(players: list[int]) -> dict[str, Any]:
    return {
        "crates": random.sample(list(_TREASURE_POOL), k=len(_TREASURE_POOL)),
        "revealed": {},
        "scores": {str(player): 0 for player in players},
        "turn_count": 0,
        "last_pick": None,
    }


def _reduce_treasure_action(
    state: dict[str, Any], players: list[int], actor_id: int, action: dict[str, Any]
) -> GameTransition:
    if actor_id not in players:
        raise ValueError("not_a_player")
    crates = list(state.get("crates") or [])
    if len(crates) != 16:
        raise ValueError("invalid_treasure_state")
    try:
        index = int(action.get("index"))
    except (TypeError, ValueError):
        raise ValueError("invalid_treasure_crate") from None
    if not 0 <= index < len(crates):
        raise ValueError("invalid_treasure_crate")
    revealed = {str(key): value for key, value in (state.get("revealed") or {}).items()}
    if str(index) in revealed:
        raise ValueError("treasure_crate_open")
    turn_count = int(state.get("turn_count") or 0)
    if not 0 <= turn_count < 8:
        raise ValueError("invalid_treasure_state")

    loot = crates[index]
    points = int(loot.get("points", 0))
    scores = {str(key): int(value) for key, value in (state.get("scores") or {}).items()}
    scores[str(actor_id)] = scores.get(str(actor_id), 0) + points
    revealed[str(index)] = {"symbol": str(loot.get("symbol", "❔")), "points": points}
    next_turn_count = turn_count + 1
    next_state = {
        **state,
        "revealed": revealed,
        "scores": scores,
        "turn_count": next_turn_count,
        "last_pick": {
            "player_id": actor_id,
            "index": index,
            "symbol": str(loot.get("symbol", "❔")),
            "points": points,
        },
    }
    if next_turn_count >= 8:
        first_score = scores.get(str(players[0]), 0)
        second_score = scores.get(str(players[1]), 0)
        if first_score == second_score:
            return GameTransition(next_state, None, is_draw=True)
        return GameTransition(
            next_state,
            None,
            winner_id=players[0] if first_score > second_score else players[1],
        )
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

registry.register(GameDefinition(
    key="emoji", name="فك شفرة الإيموجي", min_players=2, max_players=2,
    reward_coins=15, initial_state=_initial_emoji_state, reduce_action=_reduce_emoji_action,
))
registry.register(GameDefinition(
    key="treasure", name="صائد الكنز", min_players=2, max_players=2,
    reward_coins=20, initial_state=_initial_treasure_state, reduce_action=_reduce_treasure_action,
))
