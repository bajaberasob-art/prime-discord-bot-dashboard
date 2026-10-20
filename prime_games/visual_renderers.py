"""Pillow renderers for PRIME's legacy mini-games upgraded to visual boards."""
from __future__ import annotations

from PIL import Image, ImageDraw

from game_engine import (
    BACKGROUND, PANEL, PANEL_EDGE, WHITE, MUTED, PURPLE, CYAN, GREEN, RED,
    _font, draw_arabic_text, draw_progress_bar,
)

WIDTH, HEIGHT = 1120, 720


def _canvas(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 24, WIDTH - 24, HEIGHT - 24), radius=28,
                           fill=PANEL, outline=PANEL_EDGE, width=2)
    draw_arabic_text(draw, (WIDTH // 2, 48), title, font=_font(38, True),
                     fill=WHITE, anchor="mt")
    draw_arabic_text(draw, (WIDTH // 2, 100), subtitle, font=_font(20),
                     fill=MUTED, anchor="mt")
    return image, draw


def render_xo_board(
    state: dict,
    players: list[int],
    names: list[str],
    avatars: list[Image.Image],
    current_player_id: int | None,
    winner_id: int | None,
    status: str,
) -> Image.Image:
    """Render Infinite Neon XO, including each player's circular avatar."""
    board = list(state.get("board") or [None] * 9)
    image, draw = _canvas("INFINITE NEON XO", "3 قطع لكل لاعب • القطعة الأقدم تختفي عند القطعة الرابعة")
    colors = (CYAN, "#ff5577")
    for i in range(2):
        x1 = 72 + i * 540
        draw.rounded_rectangle((x1, 145, x1 + 470, 222), radius=18,
                               fill="#171e31", outline=colors[i], width=2)
        if i < len(avatars):
            image.paste(avatars[i].resize((58, 58)), (x1 + 16, 154), avatars[i].resize((58, 58)))
        draw_arabic_text(draw, (x1 + 92, 160), names[i][:22], font=_font(23, True),
                         fill=WHITE)
        mark = "X" if i == 0 else "O"
        draw_arabic_text(draw, (x1 + 92, 190), f"{mark} • {sum(1 for v in board if v == mark)}/3 قطع",
                         font=_font(17), fill=colors[i])
        if status == "active" and current_player_id == players[i]:
            draw.ellipse((x1 + 430, 168, x1 + 450, 188), fill=GREEN)
    size, gap = 142, 14
    start_x, start_y = (WIDTH - (3 * size + 2 * gap)) // 2, 260
    winning_lines = ((0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6))
    winning = set()
    for line in winning_lines:
        if board[line[0]] and board[line[0]] == board[line[1]] == board[line[2]]:
            winning = set(line)
            break
    history = list(state.get("move_history") or [])
    latest = history[-1].get("index") if history else None
    for index, value in enumerate(board):
        row, col = divmod(index, 3)
        x1, y1 = start_x + col * (size + gap), start_y + row * (size + gap)
        edge = GREEN if index in winning else (CYAN if value == "X" else "#ff5577" if value == "O" else PANEL_EDGE)
        draw.rounded_rectangle((x1, y1, x1 + size, y1 + size), radius=20,
                               fill="#0b1020", outline=edge, width=4 if index in winning or index == latest else 2)
        if value:
            draw_arabic_text(draw, (x1 + size // 2, y1 + size // 2), value,
                             font=_font(74, True), fill=CYAN if value == "X" else "#ff5577", anchor="mm")
        else:
            draw_arabic_text(draw, (x1 + size // 2, y1 + size // 2), str(index + 1),
                             font=_font(28, True), fill="#46516b", anchor="mm")
    if status == "finished" and winner_id:
        message = f"الفائز: {names[players.index(winner_id)] if winner_id in players else 'Winner'}"
        color = GREEN
    elif status == "active":
        message = f"الدور الحالي: {names[players.index(current_player_id)]}" if current_player_id in players else "المباراة مستمرة"
        color = CYAN
    else:
        message, color = "انتهت المباراة", MUTED
    draw_arabic_text(draw, (WIDTH // 2, 690), message, font=_font(24, True), fill=color, anchor="mm")
    return image


def render_memory_board(
    state: dict,
    players: list[int],
    names: list[str],
    current_player_id: int | None,
    status: str,
) -> Image.Image:
    """Render Visual Memory Royale with combo multipliers and shuffled trap cards."""
    cards = list(state.get("cards") or [])
    matched = {int(v) for v in state.get("matched") or []}
    revealed = {int(v) for v in state.get("revealed") or []}
    scores = state.get("scores") or {}
    combo = state.get("combo") or {}
    image, draw = _canvas("VISUAL MEMORY ROYALE", "اكشف الأزواج • حافظ على الـ Combo • احذر بطاقة الفخ 🃏")
    for i, player in enumerate(players[:2]):
        x = 70 + i * 540
        draw.rounded_rectangle((x, 140, x + 470, 202), radius=16,
                               fill="#171e31", outline=CYAN if i == 0 else "#ff5577", width=2)
        draw_arabic_text(draw, (x + 18, 151), names[i][:22], font=_font(20, True), fill=WHITE)
        draw_arabic_text(draw, (x + 18, 178),
                         f"النقاط: {int(scores.get(str(player), 0))}  •  Combo x{max(1, int(combo.get(str(player), 0)))}",
                         font=_font(17), fill=CYAN if i == 0 else "#ff5577")
        if status == "active" and current_player_id == player:
            draw.ellipse((x + 430, 160, x + 448, 178), fill=GREEN)
    card_w, card_h, gap = 112, 94, 12
    total_w = 4 * card_w + 3 * gap
    start_x, start_y = (WIDTH - total_w) // 2, 225
    for index, symbol in enumerate(cards):
        row, col = divmod(index, 4)
        x1, y1 = start_x + col * (card_w + gap), start_y + row * (card_h + gap)
        visible = index in matched or index in revealed
        trap = visible and symbol == "🃏"
        fill = "#142b32" if index in matched else "#392044" if trap else "#19233a" if visible else "#10182a"
        edge = GREEN if index in matched else "#ffb84d" if trap else CYAN if visible else "#394764"
        draw.rounded_rectangle((x1, y1, x1 + card_w, y1 + card_h), radius=15,
                               fill=fill, outline=edge, width=3 if visible else 2)
        text = symbol if visible else "?"
        draw_arabic_text(draw, (x1 + card_w // 2, y1 + card_h // 2), text,
                         font=_font(34 if visible else 38, True), fill=WHITE if visible else "#71809f", anchor="mm")
    pairs = len([i for i in matched if i < len(cards)]) // 2
    draw_arabic_text(draw, (WIDTH // 2, 670),
                     f"الأزواج: {pairs} • بطاقة الفخ تعيد خلط بطاقتين غير مكتملتين",
                     font=_font(20, True), fill="#ffb84d", anchor="mm")
    return image
