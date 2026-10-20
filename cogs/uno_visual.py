"""Visual multiplayer UNO for PRIME Discord servers."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Sequence

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw

from game_engine import (
    CYAN, GREEN, MUTED, PURPLE, RED, WHITE, _font, draw_arabic_text,
    get_circular_avatar, draw_status_avatar, create_luxury_canvas,
    draw_glow_panel, draw_radial_glow, image_to_file, UniversalLobbyView,
)

logger = logging.getLogger("PrimeVisualUNO")
MIN_PLAYERS = 2
MAX_PLAYERS = 8
STARTING_HAND = 7
UNO_COLORS = ("red", "blue", "green", "yellow")
COLOR_LABELS = {"red": "أحمر", "blue": "أزرق", "green": "أخضر", "yellow": "أصفر"}
COLOR_HEX = {"red": "#f04452", "blue": "#3289ff", "green": "#2ecb78", "yellow": "#f7ce46"}
ACTION_LABELS = {"skip": "🚫 تخطي", "reverse": "🔄 عكس", "draw2": "➕2"}
WILD_LABELS = {"wild": "🌈 تغيير اللون", "wild4": "💥 +4"}
CARD_VALUES = tuple(str(i) for i in range(10)) + ("skip", "reverse", "draw2")
UNO_SECONDS = 45


@dataclass(frozen=True)
class UNOCard:
    color: str | None
    value: str

    @property
    def label(self) -> str:
        if self.value in WILD_LABELS:
            return WILD_LABELS[self.value]
        label = ACTION_LABELS.get(self.value, self.value)
        return f"{COLOR_LABELS.get(self.color, 'جوكر')} {label}"


def create_deck(rng: random.Random | None = None) -> list[UNOCard]:
    """Build a standard 108-card UNO deck."""
    rng = rng or random
    deck: list[UNOCard] = []
    for color in UNO_COLORS:
        deck.append(UNOCard(color, "0"))
        for value in CARD_VALUES[1:]:
            deck.extend((UNOCard(color, value), UNOCard(color, value)))
    deck.extend(UNOCard(None, "wild") for _ in range(4))
    deck.extend(UNOCard(None, "wild4") for _ in range(4))
    rng.shuffle(deck)
    return deck


@dataclass
class UNOPlayer:
    member: object
    hand: list[UNOCard] = field(default_factory=list)
    uno_called: bool = False

    @property
    def user_id(self) -> int:
        return int(getattr(self.member, "id"))

    @property
    def name(self) -> str:
        return str(getattr(self.member, "display_name", getattr(self.member, "name", "Player")))


def can_play(card: UNOCard, top_card: UNOCard, current_color: str) -> bool:
    """A card matches by active color, face value/action, or is a wild."""
    if card.value in {"wild", "wild4"}:
        return True
    return card.color == current_color or card.value == top_card.value


def playable_cards(hand: Sequence[UNOCard], top_card: UNOCard, current_color: str) -> list[int]:
    return [index for index, card in enumerate(hand) if can_play(card, top_card, current_color)]


@dataclass
class UNOState:
    players: list[UNOPlayer]
    draw_pile: list[UNOCard] = field(default_factory=list)
    discard_pile: list[UNOCard] = field(default_factory=list)
    current_index: int = 0
    direction: int = 1
    current_color: str = "red"
    finished: bool = False
    winner_id: int | None = None
    last_action: str = "بدأت المباراة"
    rng: random.Random = field(default_factory=random.Random)

    @classmethod
    def new_game(cls, members: Sequence[object], rng: random.Random | None = None) -> "UNOState":
        if not MIN_PLAYERS <= len(members) <= MAX_PLAYERS:
            raise ValueError("invalid_player_count")
        randomizer = rng or random.Random()
        deck = create_deck(randomizer)
        players = [UNOPlayer(member) for member in members]
        for _ in range(STARTING_HAND):
            for player in players:
                player.hand.append(deck.pop())
        # Avoid an action card as the first discard to keep the opening deterministic.
        starter_index = next((i for i, card in enumerate(deck) if card.value.isdigit()), 0)
        starter = deck.pop(starter_index)
        return cls(players=players, draw_pile=deck, discard_pile=[starter],
                   current_color=starter.color or "red", rng=randomizer)

    @property
    def current_player(self) -> UNOPlayer:
        return self.players[self.current_index]

    @property
    def top_card(self) -> UNOCard:
        return self.discard_pile[-1]

    def player(self, user_id: int) -> UNOPlayer | None:
        return next((p for p in self.players if p.user_id == int(user_id)), None)

    def playable_indices(self, user_id: int) -> list[int]:
        player = self.player(user_id)
        if not player:
            return []
        return playable_cards(player.hand, self.top_card, self.current_color)

    def _refill_draw_pile(self) -> None:
        if self.draw_pile:
            return
        if len(self.discard_pile) <= 1:
            return
        top = self.discard_pile[-1]
        self.draw_pile = self.discard_pile[:-1]
        self.discard_pile = [top]
        self.rng.shuffle(self.draw_pile)

    def draw_cards(self, user_id: int, amount: int = 1) -> list[UNOCard]:
        player = self.player(user_id)
        if self.finished or player is None or player is not self.current_player:
            raise ValueError("not_your_turn")
        if amount < 1 or amount > 20:
            raise ValueError("invalid_draw_amount")
        drawn = []
        for _ in range(amount):
            self._refill_draw_pile()
            if not self.draw_pile:
                break
            card = self.draw_pile.pop()
            player.hand.append(card)
            drawn.append(card)
        self.last_action = f"{player.name} سحب {len(drawn)} ورقة"
        return drawn

    def _advance(self, steps: int = 1) -> None:
        count = len(self.players)
        if count:
            self.current_index = (self.current_index + self.direction * steps) % count

    def play_card(self, user_id: int, card_index: int, *, chosen_color: str | None = None) -> UNOCard:
        player = self.player(user_id)
        if self.finished or player is None or player is not self.current_player:
            raise ValueError("not_your_turn")
        if not 0 <= card_index < len(player.hand):
            raise ValueError("invalid_card_index")
        card = player.hand[card_index]
        if not can_play(card, self.top_card, self.current_color):
            raise ValueError("card_not_playable")
        if card.value in {"wild", "wild4"}:
            if chosen_color not in UNO_COLORS:
                raise ValueError("choose_color")
        elif chosen_color is not None:
            raise ValueError("color_not_needed")

        player.hand.pop(card_index)
        self.discard_pile.append(card)
        if chosen_color:
            self.current_color = chosen_color
        elif card.color:
            self.current_color = card.color
        player.uno_called = False
        self.last_action = f"{player.name} لعب {card.label}"

        if not player.hand:
            self.finished = True
            self.winner_id = player.user_id
            self.last_action = f"{player.name} فاز بأونو!"
            return card

        # UNO is called explicitly; if a player reached one card, the public catch
        # button remains available until that player calls UNO or another turn starts.
        if len(player.hand) == 1:
            player.uno_called = False

        if card.value == "reverse":
            self.direction *= -1
            self._advance(2 if len(self.players) == 2 else 1)
        elif card.value == "skip":
            self._advance(2)
        elif card.value == "draw2":
            self._advance()
            victim = self.current_player
            self._draw_penalty(victim, 2)
            self._advance()
        else:
            self._advance()
        return card

    def _draw_penalty(self, player: UNOPlayer, amount: int) -> list[UNOCard]:
        result = []
        for _ in range(amount):
            self._refill_draw_pile()
            if not self.draw_pile:
                break
            card = self.draw_pile.pop()
            player.hand.append(card)
        player.uno_called = False
        return result

    def call_uno(self, user_id: int) -> bool:
        player = self.player(user_id)
        if not player or len(player.hand) != 1 or player is not self.current_player:
            # The player can call UNO immediately after their play even though turn
            # control advances; use last_action to verify they are the last actor.
            if not player or len(player.hand) != 1 or player.uno_called:
                return False
            if not self.last_action.startswith(f"{player.name} لعب"):
                return False
        player.uno_called = True
        self.last_action = f"{player.name} قال UNO!"
        return True

    def catch_uno(self, catcher_id: int, target_id: int) -> int:
        target = self.player(target_id)
        catcher = self.player(catcher_id)
        if self.finished or target is None or catcher is None or target.user_id == catcher.user_id:
            return 0
        if len(target.hand) != 1 or target.uno_called:
            return 0
        drawn = self._draw_penalty(target, 2)
        self.last_action = f"{catcher.name} صاد UNO قبل {target.name} — +2"
        return len(drawn)

    def legal_draw_required(self, user_id: int) -> bool:
        return not self.playable_indices(user_id)


def render_uno_board(state: UNOState, avatars: dict[int, Image.Image] | None = None) -> Image.Image:
    """Draw a casino table, face-up discard, turn direction, active color and player seats."""
    width, height = 1240, 820
    color = COLOR_HEX.get(state.current_color, PURPLE)
    image = create_luxury_canvas(width, height, "VISUAL MULTIPLAYER UNO",
                                 f"ROUND • {len(state.players)} PLAYERS", accent=color).convert("RGBA")
    draw_radial_glow(image, (620, 438), 310, color=color, max_alpha=54, blur=24)
    draw = ImageDraw.Draw(image)
    draw_glow_panel(image, (54, 155, 1186, 742), fill=(12, 27, 28, 245),
                    outline=color, glow=color, radius=210, width=4)
    draw = ImageDraw.Draw(image)
    draw.ellipse((135, 205, 1105, 695), fill=(10, 42, 36, 255), outline="#c3a76b", width=8)
    draw.ellipse((165, 230, 1075, 670), outline="#6b5b3b", width=3)
    # Direction ring and current color marker.
    draw.arc((360, 260, 880, 625), start=25 if state.direction == 1 else 205,
             end=310 if state.direction == 1 else 490, fill=color, width=12)
    arrow_x, arrow_y = (870, 430) if state.direction == 1 else (370, 430)
    draw.polygon([(arrow_x, arrow_y), (arrow_x - 26, arrow_y - 18),
                  (arrow_x - 20, arrow_y + 18)], fill=color)
    draw.ellipse((568, 325, 672, 429), fill="#0b111a", outline=color, width=4)
    draw.text((620, 377), "↻" if state.direction == 1 else "↺", font=_font(48, True),
              fill=color, anchor="mm")
    # Draw the top card.
    _draw_uno_card(draw, 574, 430, state.top_card, scale=1.28, active_color=state.current_color)
    draw.rounded_rectangle((470, 590, 770, 635), radius=18, fill="#101722", outline=color, width=2)
    draw_arabic_text(draw, (620, 612), f"اللون الحالي: {COLOR_LABELS[state.current_color]}",
                     font=_font(17, True), fill=color, anchor="mm")
    draw_arabic_text(draw, (620, 657), f"الكومة: {len(state.draw_pile)} ورقة",
                     font=_font(14), fill=WHITE, anchor="mm")

    count = len(state.players)
    cx, cy = width // 2, 450
    rx, ry = 460, 230
    for index, player in enumerate(state.players):
        angle = (-math.pi / 2) + (2 * math.pi * index / max(1, count))
        x = int(cx + rx * math.cos(angle))
        y = int(cy + ry * math.sin(angle))
        active = index == state.current_index and not state.finished
        card_color = COLOR_HEX[state.current_color] if active else "#bda56c"
        draw.ellipse((x - 64, y - 64, x + 64, y + 64), fill="#111827", outline=card_color, width=4)
        avatar = (avatars or {}).get(player.user_id)
        if avatar is not None:
            draw_status_avatar(image, avatar, (x - 42, y - 42), 84,
                               status="target" if active else "alive")
        else:
            draw.ellipse((x - 42, y - 42, x + 42, y + 42), fill="#26354b", outline=card_color, width=3)
            draw_arabic_text(draw, (x, y), player.name[:2], font=_font(22, True), fill=WHITE, anchor="mm")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((x - 56, y + 43, x + 56, y + 72), radius=10,
                               fill="#0b1018", outline=card_color, width=2)
        draw_arabic_text(draw, (x, y + 57), f"{len(player.hand)} ورقة", font=_font(13, True),
                         fill=WHITE, anchor="mm")
        name_y = y + (78 if y < cy else -83)
        draw_arabic_text(draw, (x, name_y), player.name[:15], font=_font(13, True),
                         fill=GREEN if active else WHITE, anchor="mm")
    draw_arabic_text(draw, (620, 780), "PRIME • PLAY SMART • SAY UNO", font=_font(14, True),
                     fill=MUTED, anchor="mm")
    return image


def _draw_uno_card(draw: ImageDraw.ImageDraw, x: int, y: int, card: UNOCard,
                   *, scale: float = 1.0, active_color: str | None = None) -> None:
    w, h = int(104 * scale), int(152 * scale)
    base = COLOR_HEX.get(card.color, "#22283c")
    draw.rounded_rectangle((x - w // 2 + 4, y - h // 2 + 6, x + w // 2 + 4, y + h // 2 + 6),
                           radius=13, fill="#05070c")
    draw.rounded_rectangle((x - w // 2, y - h // 2, x + w // 2, y + h // 2),
                           radius=13, fill=base, outline="#fff1c2", width=3)
    draw.ellipse((x - w // 3, y - h // 3, x + w // 3, y + h // 3),
                 fill="#f9fafb", outline="#101522", width=2)
    label = {"skip": "⊘", "reverse": "↻", "draw2": "+2", "wild": "W", "wild4": "+4"}.get(card.value, card.value)
    ink = "#101522" if card.value not in {"wild", "wild4"} else "#ffffff"
    draw.text((x, y), label, font=_font(max(17, int(34 * scale)), True),
              fill=ink if card.value not in {"wild", "wild4"} else base, anchor="mm")


class UNOHandView(discord.ui.View):
    """Private ephemeral hand; only playable cards are enabled."""
    def __init__(self, game: "UNOGameView", user_id: int):
        super().__init__(timeout=90)
        self.game = game
        self.user_id = int(user_id)
        self.message: discord.WebhookMessage | None = None
        player = game.state.player(self.user_id)
        if not player:
            return
        legal = set(game.state.playable_indices(self.user_id))
        for index, card in enumerate(player.hand[:25]):
            button = discord.ui.Button(label=card.label[:80], style=discord.ButtonStyle.success if index in legal else discord.ButtonStyle.secondary,
                                       disabled=index not in legal, row=index // 5)
            button.callback = self._play_callback(index)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("هذه اليد خاصة بصاحبها فقط.", ephemeral=True)
            return False
        return True

    def _play_callback(self, card_index: int):
        async def callback(interaction: discord.Interaction):
            player = self.game.state.player(self.user_id)
            if not player or self.game.state.current_player.user_id != self.user_id:
                await interaction.response.send_message("ليس دورك الآن.", ephemeral=True)
                return
            card = player.hand[card_index] if card_index < len(player.hand) else None
            if card is None or not can_play(card, self.game.state.top_card, self.game.state.current_color):
                await interaction.response.send_message("هذه الورقة لم تعد صالحة للعب.", ephemeral=True)
                return
            if card.value in {"wild", "wild4"}:
                await interaction.response.send_message("اختر اللون القادم:", view=UNOColorView(self.game, self.user_id, card_index), ephemeral=True)
                return
            try:
                self.game.state.play_card(self.user_id, card_index)
            except ValueError as exc:
                await interaction.response.send_message(f"تعذر لعب الورقة: {exc}", ephemeral=True)
                return
            await interaction.response.send_message("تم لعب الورقة.", ephemeral=True)
            await self.game.refresh_board()

        return callback


class UNOColorView(discord.ui.View):
    def __init__(self, game: "UNOGameView", user_id: int, card_index: int):
        super().__init__(timeout=45)
        self.game, self.user_id, self.card_index = game, int(user_id), int(card_index)
        for color in UNO_COLORS:
            button = discord.ui.Button(label=COLOR_LABELS[color], style=discord.ButtonStyle.primary)
            button.callback = self._callback(color)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("اختيار اللون متاح للاعب صاحب الدور فقط.", ephemeral=True)
            return False
        return True

    def _callback(self, color: str):
        async def callback(interaction: discord.Interaction):
            try:
                self.game.state.play_card(self.user_id, self.card_index, chosen_color=color)
            except ValueError as exc:
                await interaction.response.send_message(f"تعذر تغيير اللون: {exc}", ephemeral=True)
                return
            await interaction.response.edit_message(content=f"اخترت اللون {COLOR_LABELS[color]}.", view=None)
            await self.game.refresh_board()
        return callback


class UNOGameView(discord.ui.View):
    def __init__(self, cog: "VisualUNO", state: UNOState, avatars: dict[int, Image.Image]):
        super().__init__(timeout=UNO_SECONDS * 10)
        self.cog, self.state, self.avatars = cog, state, avatars
        self.message: discord.Message | None = None
        self._lock = asyncio.Lock()
        self.add_item(discord.ui.Button(label="🎴 أوراقي", style=discord.ButtonStyle.primary, row=0, custom_id="uno:hand"))
        self.children[-1].callback = self._hand_callback
        self.add_item(discord.ui.Button(label="➕ سحب ورقة", style=discord.ButtonStyle.secondary, row=0, custom_id="uno:draw"))
        self.children[-1].callback = self._draw_callback
        self.add_item(discord.ui.Button(label="🔥 صيد أونو!", style=discord.ButtonStyle.danger, row=0, custom_id="uno:catch"))
        self.children[-1].callback = self._catch_callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.state.finished:
            await interaction.response.send_message("انتهت المباراة.", ephemeral=True)
            return False
        return True

    async def _hand_callback(self, interaction: discord.Interaction):
        user_id = int(interaction.user.id)
        player = self.state.player(user_id)
        if not player:
            await interaction.response.send_message("أنت لست ضمن هذه المباراة.", ephemeral=True)
            return
        if self.state.current_player.user_id != user_id:
            await interaction.response.send_message("يمكنك عرض يدك، لكن اللعب متاح لصاحب الدور الحالي فقط.", ephemeral=True)
            # Private hand is still allowed out of turn; present it below.
        view = UNOHandView(self, user_id)
        cards = "\n".join(f"{i+1}. {'✅' if i in self.state.playable_indices(user_id) else '▫️'} {card.label}" for i, card in enumerate(player.hand))
        await interaction.response.send_message(f"**أوراقك الخاصة**\n{cards or 'لا توجد أوراق.'}\n\nالأخضر يعني ورقة قابلة للعب.", view=view, ephemeral=True)

    async def _draw_callback(self, interaction: discord.Interaction):
        user_id = int(interaction.user.id)
        if not self.state.player(user_id):
            await interaction.response.send_message("أنت لست ضمن هذه المباراة.", ephemeral=True)
            return
        if self.state.current_player.user_id != user_id:
            await interaction.response.send_message("ليس دورك الآن.", ephemeral=True)
            return
        if self.state.playable_indices(user_id):
            await interaction.response.send_message("لديك ورقة قابلة للعب؛ العبها بدل السحب.", ephemeral=True)
            return
        try:
            cards = self.state.draw_cards(user_id, 1)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.state.last_action = f"{self.state.current_player.name} سحب ورقة"
        await interaction.response.send_message(f"سحبت ورقة سرًا: {cards[0].label if cards else 'الكومة فارغة'}. افتح 🎴 أوراقي لاختيارها إذا أصبحت قابلة للعب.", ephemeral=True)
        # UNO rule: after drawing one card, if it cannot be played, turn passes;
        # if playable, player may play it, otherwise they must pass.
        if not cards or not can_play(cards[0], self.state.top_card, self.state.current_color):
            self.state._advance()
        await self.refresh_board()

    async def _catch_callback(self, interaction: discord.Interaction):
        catcher_id = int(interaction.user.id)
        if not self.state.player(catcher_id):
            await interaction.response.send_message("صيد أونو متاح للاعبي المباراة فقط.", ephemeral=True)
            return
        eligible = [p for p in self.state.players if p.user_id != catcher_id and len(p.hand) == 1 and not p.uno_called]
        if not eligible:
            await interaction.response.send_message("لا يوجد لاعب مكشوف بلا نداء UNO.", ephemeral=True)
            return
        # Catch the first eligible player; list targets explicitly to avoid accidental punishment.
        await interaction.response.send_message("اختر اللاعب الذي تريد اصطياده:", view=UNOCatchView(self, catcher_id), ephemeral=True)

    async def refresh_board(self):
        if not self.message:
            return
        file = image_to_file(render_uno_board(self.state, self.avatars), "prime-uno-board.png")
        try:
            content = None
            if self.state.finished:
                content = f"🏆 **فاز {self.state.player(self.state.winner_id).name} في Visual UNO!**"
                self.stop()
                await self.cog.release(self)
            await self.message.edit(content=content, embed=self.embed(), attachments=[file], view=None if self.state.finished else self)
        except discord.HTTPException:
            logger.exception("Could not refresh UNO board")
        finally:
            file.close()

    def embed(self) -> discord.Embed:
        state = self.state
        current = state.current_player.name if not state.finished else "انتهت المباراة"
        color = int(COLOR_HEX[state.current_color].replace("#", ""), 16)
        embed = discord.Embed(title="🎴 Visual Multiplayer UNO", description=state.last_action, color=color)
        embed.add_field(name="الدور الحالي", value=current, inline=True)
        embed.add_field(name="اللون", value=COLOR_LABELS[state.current_color], inline=True)
        embed.add_field(name="الاتجاه", value="↻ مع عقارب الساعة" if state.direction == 1 else "↺ عكس عقارب الساعة", inline=True)
        embed.add_field(name="طريقة اللعب", value="افتح أوراقي سرًا، العب الورقة الخضراء، واسحب فقط إذا لم تملك ورقة صالحة.", inline=False)
        embed.set_footer(text="PRIME • Visual Multiplayer UNO • 2–8 players")
        return embed


class UNOCatchView(discord.ui.View):
    def __init__(self, game: UNOGameView, catcher_id: int):
        super().__init__(timeout=30)
        self.game, self.catcher_id = game, int(catcher_id)
        targets = [p for p in game.state.players if p.user_id != catcher_id and len(p.hand) == 1 and not p.uno_called]
        for index, player in enumerate(targets[:25]):
            button = discord.ui.Button(label=player.name[:80], style=discord.ButtonStyle.danger)
            button.callback = self._callback(player.user_id)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.catcher_id:
            await interaction.response.send_message("هذه قائمة الصيد خاصة بك.", ephemeral=True)
            return False
        return True

    def _callback(self, target_id: int):
        async def callback(interaction: discord.Interaction):
            amount = self.game.state.catch_uno(self.catcher_id, target_id)
            if amount:
                await interaction.response.edit_message(content=f"🔥 تم اصطياد اللاعب! سحب {amount} ورقتين عقوبة.", view=None)
                await self.game.refresh_board()
            else:
                await interaction.response.edit_message(content="فاتتك الفرصة؛ لا توجد مخالفة UNO صالحة الآن.", view=None)
        return callback


class VisualUNO(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: dict[int, UNOGameView] = {}
        self.lock = asyncio.Lock()

    async def release(self, game: UNOGameView):
        guild_id = getattr(getattr(game.message, "guild", None), "id", None)
        if guild_id is not None:
            async with self.lock:
                if self.games.get(int(guild_id)) is game:
                    self.games.pop(int(guild_id), None)

    @app_commands.command(name="uno_visual", description="ابدأ لعبة أونو المرئية (2–8 لاعبين)")
    @app_commands.guild_only()
    async def uno_visual(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("اللعبة متاحة داخل السيرفر فقط.", ephemeral=True)
            return
        guild_id = int(interaction.guild.id)
        async with self.lock:
            if guild_id in self.games:
                await interaction.response.send_message("توجد مباراة UNO نشطة في هذا السيرفر.", ephemeral=True)
                return

        async def on_start(lobby_interaction: discord.Interaction, members: list[discord.Member]):
            if not MIN_PLAYERS <= len(members) <= MAX_PLAYERS:
                await lobby_interaction.followup.send("تحتاج أونو إلى 2–8 لاعبين.", ephemeral=True)
                return
            try:
                state = UNOState.new_game(members)
            except ValueError as exc:
                await lobby_interaction.followup.send(str(exc), ephemeral=True)
                return
            game = UNOGameView(self, state, {})
            async with self.lock:
                if guild_id in self.games:
                    await lobby_interaction.followup.send("توجد مباراة UNO نشطة بالفعل.", ephemeral=True)
                    return
                self.games[guild_id] = game
            avatars = await asyncio.gather(
                *(get_circular_avatar(member, 84, session=getattr(self.bot, "session", None), timeout=3.5)
                  for member in members), return_exceptions=True
            )
            for member, avatar in zip(members, avatars):
                if isinstance(avatar, Image.Image):
                    game.avatars[int(member.id)] = avatar
            image_file = image_to_file(render_uno_board(state, game.avatars), "prime-uno-board.png")
            try:
                game.message = await lobby_interaction.followup.send(embed=game.embed(), file=image_file, view=game, wait=True)
            finally:
                image_file.close()
            await lobby.stop()
        lobby = UniversalLobbyView(
            interaction.user, game_name="Visual Multiplayer UNO",
            rules="2–8 لاعبين. طابق اللون أو الرقم/الرمز، العب التخطي والعكس و+2 والجوكر، وقل UNO عند بقاء ورقة واحدة. السحب متاح عندما لا تملك ورقة قابلة للعب.",
            on_start=on_start, min_players=MIN_PLAYERS, max_players=MAX_PLAYERS,
            timeout=300, avatar_session=getattr(self.bot, "session", None),
        )
        lobby_file = await lobby.render_lobby_file()
        try:
            await interaction.response.send_message(embed=lobby.lobby_embed(), file=lobby_file, view=lobby)
        finally:
            lobby_file.close()
        await lobby.bind_message(await interaction.original_response())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VisualUNO(bot))
