"""Premium Pillow cards for PRIME's original /game mini-games."""
from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from game_engine import (
    BACKGROUND, CYAN, GREEN, MUTED, PURPLE, RED, WHITE,
    _font, create_luxury_canvas, draw_arabic_text, draw_glow_panel,
    draw_status_avatar, get_circular_avatar,
)


def _text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: object,
          size: int = 26, color: str = WHITE, *, anchor: str | None = None,
          bold: bool = False) -> None:
    draw_arabic_text(draw, xy, str(value or ""), font=_font(size, bold),
                     fill=color, anchor=anchor)


def _player_name(guild: Any, user_id: int) -> str:
    member = guild.get_member(int(user_id)) if guild is not None else None
    return str(getattr(member, "display_name", f"Player {user_id}"))[:22]


async def render_classic_game(session: dict[str, Any], guild: Any = None) -> Image.Image:
    """Render a state snapshot; never mutate the durable game session."""
    key = str(session.get("game_key") or "xo")
    state = session.get("state") or {}
    players = [int(value) for value in session.get("players") or []][:2]
    accent = {"xo": CYAN, "memory": PURPLE, "word": "#ffca60",
              "dice": RED, "rps": "#ff7b72", "quiz": GREEN,
              "connect4": CYAN}.get(key, PURPLE)
    titles = {"xo": "INFINITE NEON XO", "memory": "VISUAL MEMORY ROYALE",
              "word": "WORD VAULT BREAKER", "dice": "TACTICAL DICE DUEL",
              "rps": "TACTICAL ARENA DUEL", "quiz": "PRIME KNOWLEDGE ARENA",
              "connect4": "CONNECT FOUR POWER"}
    image = create_luxury_canvas(1200, 760, titles.get(key, "PRIME ARENA"),
                                 f"SESSION #{session.get('id', '?')}  •  {session.get('status', 'active').upper()}",
                                 accent=accent)
    draw = ImageDraw.Draw(image)
    draw_glow_panel(image, (48, 150, 1152, 704), outline=accent, glow=accent)
    names = [_player_name(guild, p) for p in players]
    for i, user_id in enumerate(players):
        member = guild.get_member(user_id) if guild is not None else None
        avatar = await get_circular_avatar(member, 72) if member is not None else await get_circular_avatar(None, 72)
        draw_status_avatar(image, avatar, (82 + i * 900, 174), 72,
                           status="winner" if session.get("winner_id") == user_id else "alive")
        _text(draw, (170 + i * 900, 210), names[i], 23, WHITE, anchor="lm", bold=True)
        if key in {"dice", "memory", "word", "quiz"}:
            scores = state.get("scores") or {}
            _text(draw, (170 + i * 900, 244), f"{int(scores.get(str(user_id), 0))} PTS", 20, accent, anchor="lm")
    if key == "xo":
        board = list(state.get("board") or [None] * 9)
        pieces = state.get("pieces") or {}
        _text(draw, (600, 165), "3 PIECES MAX • ROLLING BOARD", 18, CYAN, anchor="ma")
        x0, y0, cell = 375, 250, 150
        for i in range(9):
            x, y = x0 + (i % 3) * cell, y0 + (i // 3) * cell
            draw.rounded_rectangle((x, y, x + cell - 12, y + cell - 12), 18,
                                   fill="#111625", outline=accent, width=3)
            mark = board[i]
            if mark:
                color = CYAN if mark == "X" else "#ff5b83"
                draw.ellipse((x + 25, y + 25, x + cell - 37, y + cell - 37),
                             outline=color, width=7)
                if mark == "X":
                    draw.line((x + 35, y + 35, x + cell - 47, y + cell - 47), fill=color, width=7)
                    draw.line((x + cell - 47, y + 35, x + 35, y + cell - 47), fill=color, width=7)
        for i, user_id in enumerate(players):
            mark = "X" if i == 0 else "O"
            _text(draw, (600, 725 - i * 26), f"{mark}: {names[i]} • {len(pieces.get(mark, []))}/3", 17, CYAN if mark == "X" else "#ff5b83", anchor="mm")
    elif key == "memory":
        cards = list(state.get("cards") or [])
        matched = {int(v) for v in state.get("matched") or []}
        revealed = {int(v) for v in state.get("revealed") or []}
        x0, y0, cw, ch = 360, 258, 118, 94
        for i in range(min(16, len(cards))):
            x, y = x0 + (i % 4) * 130, y0 + (i // 4) * 105
            face = i in matched or i in revealed
            color = GREEN if i in matched else accent if face else "#343c5a"
            draw.rounded_rectangle((x, y, x + cw, y + ch), 14,
                                   fill="#171c31" if face else "#202844", outline=color, width=4)
            _text(draw, (x + cw // 2, y + ch // 2), cards[i] if face else "?", 30, color, anchor="mm", bold=True)
        scores = state.get("scores") or {}
        combo = state.get("combo") or {}
        current = str(session.get("current_player_id") or (players[0] if players else ""))
        _text(draw, (600, 675), f"PAIRS {len(matched)//2}/8 • SCORE {scores.get(current, 0)} • COMBO x{min(int(combo.get(current, 0)), 3)}", 18, accent, anchor="mm")
        if state.get("trap_event"):
            _text(draw, (600, 704), "TRAP CARD • TWO CLOSED CARDS SHUFFLED", 15, RED, anchor="mm")
    elif key == "word":
        puzzles = list(state.get("puzzles") or [])
        idx = int(state.get("round_index") or 0)
        if idx < len(puzzles):
            puzzle = puzzles[idx]
            _text(draw, (600, 300), "VAULT CODE", 24, MUTED, anchor="mm")
            _text(draw, (600, 365), "   ".join(list(str(puzzle.get("scrambled", "")))), 54, "#ffca60", anchor="mm", bold=True)
            _text(draw, (600, 440), f"ROUND {idx+1}/{len(puzzles)} • SECRET VAULT ENTRY", 20, WHITE, anchor="mm")
            hint = (state.get("hints") or {}).get(str(idx))
            if hint:
                _text(draw, (600, 470), f"CLUE: FIRST LETTER  {hint}", 23, GREEN, anchor="mm", bold=True)
            for i, option in enumerate(puzzle.get("options") or []):
                _text(draw, (600, 490 + i * 42), f"{i+1}. {option}", 22, CYAN if i == int(puzzle.get("correct", -1)) else WHITE, anchor="mm")
    elif key == "dice":
        last = state.get("last_round") or {}
        rolls = last.get("rolls") or state.get("round_rolls") or {}
        hp = state.get("hp") or {}
        for i, uid in enumerate(players):
            value = max(0, min(3, int(hp.get(str(uid), 3))))
            x1, x2 = (300, 500) if i == 0 else (700, 900)
            draw.rounded_rectangle((x1, 285, x2, 310), 10, fill="#252b42", outline=accent, width=2)
            if value:
                draw.rounded_rectangle((x1 + 3, 288, x1 + 3 + int((x2-x1-6)*value/3), 307), 7, fill=GREEN if value > 1 else RED)
            _text(draw, ((x1+x2)//2, 270), f"HP {value}/3", 18, WHITE, anchor="mm")
        _text(draw, (600, 340), "VS", 60, RED, anchor="mm", bold=True)
        for i, uid in enumerate(players):
            roll = rolls.get(str(uid), "—")
            x = 390 if i == 0 else 810
            _text(draw, (x, 425), f"🎲  {roll}", 48, CYAN if i == 0 else RED, anchor="mm", bold=True)
        _text(draw, (600, 530), f"ROUND {int(state.get('round') or 1)} / 5", 25, WHITE, anchor="mm")
        _text(draw, (600, 585), "ROLL • READ • OUTPLAY", 18, MUTED, anchor="mm")
    elif key == "rps":
        choices = state.get("choices") or {}
        hp = state.get("hp") or {}
        for i, uid in enumerate(players):
            value = max(0, min(3, int(hp.get(str(uid), 3))))
            x1, x2 = (300, 500) if i == 0 else (700, 900)
            draw.rounded_rectangle((x1, 285, x2, 310), 10, fill="#252b42", outline=accent, width=2)
            if value:
                draw.rounded_rectangle((x1 + 3, 288, x1 + 3 + int((x2-x1-6)*value/3), 307), 7, fill=GREEN if value > 1 else RED)
            _text(draw, ((x1+x2)//2, 270), f"HP {value}/3", 18, WHITE, anchor="mm")
        _text(draw, (600, 340), "VS", 64, RED, anchor="mm", bold=True)
        labels = {"rock": "ROCK", "paper": "PAPER", "scissors": "SCISSORS"}
        for i, uid in enumerate(players):
            x = 390 if i == 0 else 810
            choice = labels.get(str(choices.get(str(uid), "")), "SEALED")
            _text(draw, (x, 430), choice, 32, CYAN if i == 0 else RED, anchor="mm", bold=True)
        _text(draw, (600, 540), "SEALED CHOICES • TACTICAL DUEL", 20, WHITE, anchor="mm")
    elif key == "quiz":
        questions = list(state.get("questions") or [])
        idx = int(state.get("question_index") or 0)
        if idx < len(questions):
            q = questions[idx]
            _text(draw, (600, 300), f"QUESTION {idx+1}/{len(questions)}", 24, accent, anchor="mm")
            _text(draw, (600, 355), str(q.get("question", ""))[:80], 25, WHITE, anchor="mm")
            for i, option in enumerate(q.get("options") or []):
                _text(draw, (600, 430 + i * 46), f"{i+1}. {option}", 22, WHITE, anchor="mm")
    elif key == "connect4":
        board = list(state.get("board") or [None] * 42)
        for i, mark in enumerate(board):
            x, y = 390 + (i % 7) * 60, 260 + (i // 7) * 54
            color = "#ff5b83" if mark == "R" else "#ffd166" if mark == "Y" else "#30364d"
            draw.ellipse((x, y, x + 42, y + 42), fill=color, outline=accent, width=2)
    status = str(session.get("status") or "active").upper()
    winner = session.get("winner_id")
    footer = "DRAW • REMATCH?" if status == "FINISHED" and winner is None else f"WINNER • {_player_name(guild, int(winner))}" if winner else f"TURN • {_player_name(guild, int(session.get('current_player_id') or players[0]))}" if players else status
    _text(draw, (600, 670), footer, 21, GREEN if winner else MUTED, anchor="mm", bold=True)
    return image
