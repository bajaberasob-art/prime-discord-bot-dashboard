"""Roulette Royale: a visual, turn-based survival game for PRIME."""
from __future__ import annotations

import asyncio
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw

from game_engine import (
    CYAN, GREEN, MUTED, PANEL_EDGE, PURPLE, RED, WHITE,
    _font, draw_arabic_text, get_circular_avatar, create_base_canvas, image_to_file,
    UniversalLobbyView,
)

logger = logging.getLogger("PrimeRouletteRoyale")

MIN_PLAYERS = 3
MAX_PLAYERS = 10
TURN_SECONDS = 15
RESULT_PAUSE_SECONDS = 2.2
SPIN_FRAMES = 7
SPIN_FRAME_DELAY = 0.24
WHEEL_COLORS = ("#8b5cf6", "#0f766e", "#be123c", "#1d4ed8", "#a16207",
                "#6d28d9", "#047857", "#9f1239", "#0369a1", "#7e22ce")


@dataclass
class RoyalePlayer:
    """Per-game state for a participant."""
    member: discord.Member
    alive: bool = True
    shield: bool = False
    used_skills: set[str] = field(default_factory=set)

    @property
    def user_id(self) -> int:
        return int(self.member.id)

    @property
    def name(self) -> str:
        return str(getattr(self.member, "display_name", self.member.name))


def _unique(values: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(int(v) for v in values))


def choose_target(alive_ids: Iterable[int], excluded_id: int | None = None) -> int | None:
    """Pick a random living target, excluding one ID when possible."""
    candidates = [uid for uid in _unique(alive_ids) if uid != excluded_id]
    return random.choice(candidates) if candidates else None


def apply_eliminations(
    players: dict[int, RoyalePlayer],
    requested_ids: Iterable[int],
    *,
    protected_ids: set[int] | None = None,
) -> tuple[list[int], list[int]]:
    """Eliminate living players, consuming a shield instead of eliminating its owner.

    Returns (eliminated_ids, shield_broken_ids). A shield only protects from one
    elimination attempt and is consumed when its owner is targeted.
    """
    protected_ids = protected_ids or set()
    eliminated: list[int] = []
    broken: list[int] = []
    for user_id in _unique(requested_ids):
        player = players.get(user_id)
        if player is None or not player.alive:
            continue
        if user_id in protected_ids or player.shield:
            player.shield = False
            broken.append(user_id)
            continue
        player.alive = False
        eliminated.append(user_id)
    return eliminated, broken


def render_roulette_board(
    players: dict[int, RoyalePlayer],
    *,
    alive_ids: Iterable[int],
    round_number: int,
    target_id: int | None = None,
    spin_angle: float = 0.0,
    event_text: str | None = None,
    avatars: dict[int, Image.Image] | None = None,
    obscure_wheel: bool = False,
    winner_id: int | None = None,
) -> Image.Image:
    """Draw the wheel and a readable alive/eliminated status panel with Pillow."""
    alive = set(int(v) for v in alive_ids)
    all_players = list(players.values())
    living = [p for p in all_players if p.user_id in alive]
    width, height = 1240, 760
    image = create_base_canvas(width, height, "روليت الملوك • ROULETTE ROYALE",
                               f"الجولة {round_number} • آخر لاعب يبقى هو الملك").convert("RGBA")
    draw = ImageDraw.Draw(image)
    cx, cy, radius = 385, 430, 260
    draw.ellipse((cx-radius-10, cy-radius-10, cx+radius+10, cy+radius+10),
                 fill="#080a12", outline=CYAN, width=4)
    if living:
        step = 360.0 / len(living)
        # Draw slices using a stable start angle; the needle's angle determines the stop.
        for index, player in enumerate(living):
            start = -90 + spin_angle + index * step
            end = start + step
            color = WHEEL_COLORS[index % len(WHEEL_COLORS)]
            draw.pieslice((cx-radius, cy-radius, cx+radius, cy+radius),
                          start=start, end=end, fill=color, outline="#f4f6ff", width=2)
            mid = math.radians((start + end) / 2)
            tx = cx + math.cos(mid) * radius * 0.67
            ty = cy + math.sin(mid) * radius * 0.67
            avatar = (avatars or {}).get(player.user_id)
            if avatar and not obscure_wheel:
                size = 48
                image.alpha_composite(avatar.resize((size, size)), (int(tx-size/2), int(ty-size/2)))
            if not obscure_wheel:
                label = player.name[:12]
                draw_arabic_text(draw, (int(cx + math.cos(mid) * radius * 0.43),
                                        int(cy + math.sin(mid) * radius * 0.43)),
                                 label, font=_font(13, True), fill=WHITE, anchor="mm")
        draw.ellipse((cx-58, cy-58, cx+58, cy+58), fill="#0b1020", outline=WHITE, width=3)
        draw_arabic_text(draw, (cx, cy-5), "ROYALE", font=_font(18, True), fill=CYAN, anchor="mm")
        draw.polygon([(cx, cy-radius-28), (cx-18, cy-radius+8), (cx+18, cy-radius+8)],
                     fill="#fbbf24", outline=WHITE)
    else:
        draw_arabic_text(draw, (cx, cy), "لا يوجد لاعبون", font=_font(22, True), fill=WHITE, anchor="mm")

    # Side panel lists every participant, with dead players greyed out.
    panel_x, panel_y, panel_w = 720, 150, 460
    draw.rounded_rectangle((panel_x, panel_y, panel_x+panel_w, 685), radius=20,
                           fill="#0b1020", outline=PANEL_EDGE, width=2)
    draw_arabic_text(draw, (panel_x+panel_w-24, panel_y+25),
                     f"الأحياء: {len(living)} / {len(all_players)}", font=_font(22, True),
                     fill=GREEN, anchor="ra")
    for index, player in enumerate(all_players):
        y = panel_y + 72 + index * 43
        is_alive = player.alive
        is_target = player.user_id == target_id
        bg = "#2b1b35" if is_target else ("#132a25" if is_alive else "#252936")
        fg = "#a1a1aa" if not is_alive else (RED if is_target else WHITE)
        draw.rounded_rectangle((panel_x+16, y-12, panel_x+panel_w-16, y+27),
                               radius=9, fill=bg, outline=RED if is_target else PANEL_EDGE, width=2 if is_target else 1)
        avatar = (avatars or {}).get(player.user_id)
        if avatar:
            image.alpha_composite(avatar.resize((30, 30)), (panel_x+24, y-8))
        status = "مطـرود" if not is_alive else ("🛡 درع" if player.shield else "حي")
        if player.user_id == winner_id:
            status = "👑 الملك"
        draw_arabic_text(draw, (panel_x+panel_w-28, y+6), status, font=_font(13, True),
                         fill=fg, anchor="rm")
        name_limit = 23
        draw_arabic_text(draw, (panel_x+68, y+6), player.name[:name_limit],
                         font=_font(15, True), fill=fg, anchor="lm")
    if event_text:
        draw.rounded_rectangle((52, 680, 680, 720), radius=12, fill="#321a39", outline=PURPLE, width=2)
        draw_arabic_text(draw, (366, 700), event_text[:75], font=_font(15, True),
                         fill="#f5d0fe", anchor="mm")
    if obscure_wheel:
        draw.rounded_rectangle((cx-radius+35, cy-radius+105, cx+radius-35, cy+radius-105),
                               radius=20, fill="#111827", outline=PURPLE, width=4)
        draw_arabic_text(draw, (cx, cy), "🌪️ العاصفة العمياء\nتفاصيل العجلة مخفية",
                         font=_font(21, True), fill=WHITE, anchor="mm")
    if target_id is not None:
        target = players.get(target_id)
        if target:
            draw.rounded_rectangle((55, 155, 680, 205), radius=12, fill="#20152d", outline=PURPLE, width=2)
            draw_arabic_text(draw, (367, 180), f"🎯 المستهدف: {target.name[:28]}",
                             font=_font(19, True), fill=WHITE, anchor="mm")
    return image


class RoyaleTurnView(discord.ui.View):
    """The active target's private action controls for the current 15-second turn."""

    def __init__(self, game: "RouletteRoyaleGame", target_id: int) -> None:
        super().__init__(timeout=TURN_SECONDS)
        self.game = game
        self.target_id = int(target_id)
        self.action = "eliminate"
        self.choice_id: int | None = None
        self.resolved = False
        self._build_target_select()
        self._build_skill_buttons()

    def _alive_targets(self) -> list[discord.SelectOption]:
        options = []
        for p in self.game.players.values():
            if p.alive and p.user_id != self.target_id:
                options.append(discord.SelectOption(label=p.name[:100], value=str(p.user_id),
                                                    description="هدف للطرد"[:100]))
        return options[:25]

    def _build_target_select(self) -> None:
        options = self._alive_targets()
        if not options:
            options = [discord.SelectOption(label="لا توجد أهداف متاحة", value="none")]
        select = discord.ui.Select(
            placeholder="اختر اللاعب الذي تريد طرده",
            min_values=1, max_values=1, options=options, row=0,
            custom_id=f"rr:target:{id(self):x}",
        )
        async def callback(interaction: discord.Interaction) -> None:
            if not await self._authorize(interaction):
                return
            value = select.values[0]
            if value == "none":
                await interaction.response.send_message("لا يوجد هدف متاح.", ephemeral=True)
                return
            # Keep the skill selected by the player; the same menu serves
            # elimination, revival, and fate-link targeting.
            if self.action == "heal":
                if self.game.players[int(value)].alive:
                    await interaction.response.send_message("اختر لاعبًا مطرودًا لإنعاشه.", ephemeral=True)
                    return
            elif self.action in {"eliminate", "chain"}:
                if not self.game.players[int(value)].alive or int(value) == self.target_id:
                    await interaction.response.send_message("هذا الهدف غير متاح لهذا القرار.", ephemeral=True)
                    return
            else:
                await interaction.response.send_message("اختر مهارة أولًا.", ephemeral=True)
                return
            self.choice_id = int(value)
            labels = {"eliminate": "للطرد", "heal": "للإنعاش", "chain": "لربط المصير"}
            await interaction.response.send_message(
                f"تم تحديد <@{self.choice_id}> {labels.get(self.action, 'كهدف')}. "
                "اضغط تأكيد القرار خلال الوقت المتبقي.",
                ephemeral=True,
            )
        select.callback = callback
        self.add_item(select)

    def _build_skill_buttons(self) -> None:
        specs = [
            ("shield", "🛡️ الدرع", discord.ButtonStyle.success),
            ("heal", "❤️ إنعاش", discord.ButtonStyle.primary),
            ("chain", "🔗 ربط المصير", discord.ButtonStyle.secondary),
            ("reverse", "🔄 مقامرة عكسية", discord.ButtonStyle.danger),
            ("inspect", "🔍 فحص الأسرار", discord.ButtonStyle.secondary),
        ]
        for index, (skill, label, style) in enumerate(specs):
            button = discord.ui.Button(label=label, style=style, row=1 + index // 3)
            button.disabled = skill in self.game.players[self.target_id].used_skills
            button.callback = self._make_skill_callback(skill)
            self.add_item(button)
        confirm = discord.ui.Button(label="تأكيد القرار", emoji="⚔️", style=discord.ButtonStyle.danger, row=3)
        confirm.callback = self._confirm
        self.add_item(confirm)

    async def _authorize(self, interaction: discord.Interaction) -> bool:
        if self.resolved or self.game.finished:
            await interaction.response.send_message("انتهى هذا الدور.", ephemeral=True)
            return False
        if int(interaction.user.id) != self.target_id:
            await interaction.response.send_message("هذه الخيارات مخصصة للاعب المستهدف فقط.", ephemeral=True)
            return False
        if self.game.current_target != self.target_id:
            await interaction.response.send_message("انتهى دورك بالفعل.", ephemeral=True)
            return False
        return True

    def _make_skill_callback(self, skill: str):
        async def callback(interaction: discord.Interaction) -> None:
            if not await self._authorize(interaction):
                return
            player = self.game.players[self.target_id]
            if skill in player.used_skills:
                await interaction.response.send_message("استخدمت هذه المهارة مسبقًا في هذه اللعبة.", ephemeral=True)
                return
            if skill == "shield":
                player.used_skills.add(skill)
                player.shield = True
                self.resolved = True
                await interaction.response.send_message("🛡️ فُعّل الدرع. سيحميك من محاولة طرد واحدة في الجولة القادمة.", ephemeral=True)
                await self.game.resolve_turn(self.target_id, action="shield")
                self.stop()
                return
            if skill == "inspect":
                player.used_skills.add(skill)
                self.resolved = True
                next_target = choose_target(self.game.alive_ids, excluded_id=self.target_id)
                shielded = [f"<@{p.user_id}>" for p in self.game.players.values()
                            if p.alive and p.shield and p.user_id != self.target_id]
                info = f"المستهدف المحتمل التالي: <@{next_target}>" if next_target else "لا يوجد مستهدف آخر."
                info += "\nأصحاب الدروع: " + (", ".join(shielded) if shielded else "لا أحد")
                await interaction.response.send_message("🔍 **نتيجة الفحص**\n" + info, ephemeral=True)
                await self.game.resolve_turn(self.target_id, action="inspect")
                self.stop()
                return
            if skill == "reverse":
                player.used_skills.add(skill)
                self.resolved = True
                await interaction.response.send_message("🔄 بدأت المقامرة العكسية؛ سيتم إعلان النتيجة في لوحة اللعبة.", ephemeral=True)
                await self.game.resolve_turn(self.target_id, action="reverse")
                self.stop()
                return
            self.action = skill
            self.choice_id = None
            # Reconfigure the existing select to match the selected skill.
            select = next((item for item in self.children if isinstance(item, discord.ui.Select)), None)
            if select is not None:
                if skill == "heal":
                    candidates = [p for p in self.game.players.values() if not p.alive]
                    placeholder = "اختر لاعبًا مطرودًا لإنعاشه"
                else:
                    candidates = [p for p in self.game.players.values() if p.alive and p.user_id != self.target_id]
                    placeholder = "اختر خصمًا لربط المصير به"
                select.options = [discord.SelectOption(label=p.name[:100], value=str(p.user_id))
                                  for p in candidates[:25]] or [discord.SelectOption(label="لا توجد خيارات", value="none")]
                select.placeholder = placeholder
            await interaction.response.send_message(
                "اختر الهدف من القائمة ثم اضغط **تأكيد القرار** قبل انتهاء الوقت.", ephemeral=True
            )
        return callback

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if not await self._authorize(interaction):
            return
        if self.action in {"heal", "chain"} and self.choice_id is None:
            await interaction.response.send_message("اختر هدفًا من القائمة أولًا.", ephemeral=True)
            return
        if self.action == "eliminate" and self.choice_id is None:
            await interaction.response.send_message("اختر لاعبًا للطرد أولًا.", ephemeral=True)
            return
        if self.action == "heal" and self.choice_id is not None and self.game.players[self.choice_id].alive:
            await interaction.response.send_message("هذا اللاعب ليس مطرودًا؛ حدّث اختيارك.", ephemeral=True)
            return
        self.resolved = True
        await interaction.response.defer(ephemeral=True)
        await self.game.resolve_turn(self.target_id, action=self.action, choice_id=self.choice_id)
        self.stop()

    async def on_timeout(self) -> None:
        if self.resolved or self.game.finished:
            return
        self.resolved = True
        await self.game.resolve_turn(self.target_id, action="timeout")

    async def on_error(self, interaction: discord.Interaction, error: Exception,
                       item: discord.ui.Item[object]) -> None:
        logger.exception("Roulette Royale turn interaction failed", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("تعذر تنفيذ الحركة. حاول مجددًا.", ephemeral=True)


class RouletteRoyaleGame:
    """Owns the game state and serializes all turn resolution."""

    def __init__(self, cog: "RouletteRoyale", players: list[discord.Member]) -> None:
        if not MIN_PLAYERS <= len(players) <= MAX_PLAYERS:
            raise ValueError(f"player_count_must_be_{MIN_PLAYERS}_to_{MAX_PLAYERS}")
        self.cog = cog
        self.players: dict[int, RoyalePlayer] = {int(p.id): RoyalePlayer(p) for p in players}
        if len(self.players) != len(players):
            raise ValueError("duplicate_player_ids")
        self.alive_ids = list(self.players)
        self.round_number = 0
        self.current_target: int | None = None
        self.finished = False
        self.event_text: str | None = None
        self.message: discord.Message | None = None
        self.guild_id: int | None = None
        self.avatars: dict[int, Image.Image] = {}
        self._lock = asyncio.Lock()
        self._runner: asyncio.Task | None = None
        self._turn_view: RoyaleTurnView | None = None
        self._last_eliminated: list[int] = []
        self._last_message_text: str | None = None

    async def start(self, message: discord.Message) -> None:
        self.message = message
        await self._load_avatars()
        await self._update_board("👑 بدأت **روليت الملوك**! استعدوا...")
        self._runner = asyncio.create_task(self.run(), name=f"prime-roulette-royale-{self.guild_id}")

    async def _load_avatars(self) -> None:
        session = getattr(self.cog.bot, "session", None)
        if session is not None and getattr(session, "closed", False):
            session = None
        async def load(p: RoyalePlayer):
            avatar = await get_circular_avatar(p.member, 54, session=session, timeout=3.5)
            return p.user_id, avatar
        results = await asyncio.gather(*(load(p) for p in self.players.values()), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Could not load Roulette Royale avatar", exc_info=result)
            else:
                self.avatars[result[0]] = result[1]

    def _render(self, target_id: int | None = None, angle: float = 0, obscure: bool = False) -> discord.File:
        image = render_roulette_board(
            self.players, alive_ids=self.alive_ids, round_number=max(1, self.round_number),
            target_id=target_id, spin_angle=angle, event_text=self.event_text,
            avatars=self.avatars, obscure_wheel=obscure,
            winner_id=self.alive_ids[0] if self.finished and len(self.alive_ids) == 1 else None,
        )
        return image_to_file(image, "prime-roulette-royale.png")

    def _embed(self, text: str) -> discord.Embed:
        alive = [f"<@{uid}>" for uid in self.alive_ids]
        embed = discord.Embed(title="👑 روليت الملوك | Roulette Royale",
                              description=text[:4000],
                              color=0x35E68B if self.finished else 0xA855F7)
        if alive:
            embed.add_field(name=f"الأحياء ({len(alive)})", value=", ".join(alive)[:1024], inline=False)
        embed.add_field(name="الجولة", value=str(max(1, self.round_number)), inline=True)
        if self.event_text:
            embed.add_field(name="حدث العالم", value=self.event_text[:1024], inline=True)
        embed.set_footer(text="PRIME • Roulette Royale • القرار خلال 15 ثانية")
        return embed

    async def _update_board(self, text: str, *, target_id: int | None = None,
                            angle: float = 0, obscure: bool = False,
                            view: discord.ui.View | None = None) -> None:
        if self.message is None:
            return
        file = self._render(target_id, angle, obscure)
        try:
            await self.message.edit(content=None, embed=self._embed(text),
                                    attachments=[file], view=view)
        except discord.HTTPException:
            logger.exception("Could not update Roulette Royale board")
            try:
                await self.message.edit(embed=self._embed(text), view=view)
            except discord.HTTPException:
                logger.error("Fallback board update also failed", exc_info=True)
        finally:
            file.close()

    async def _spin(self, target_id: int) -> None:
        # Animate toward the exact slice containing the selected target. The
        # pointer is fixed at the top, so the final angle must align that slice.
        living = [p.user_id for p in self.players.values() if p.alive]
        if target_id not in living:
            raise ValueError("roulette_target_is_not_alive")
        step = 360.0 / len(living)
        target_index = living.index(target_id)
        final_angle = (-(target_index + 0.5) * step) % 360.0
        start_angle = final_angle + 360.0 * 4 + random.uniform(0, step)
        for frame in range(1, SPIN_FRAMES + 1):
            progress = frame / SPIN_FRAMES
            # Ease-out curve: fast at first, slowing toward the chosen slice.
            eased = 1.0 - (1.0 - progress) ** 2
            angle = start_angle * (1.0 - eased) + final_angle * eased
            await self._update_board("🎡 تدور العجلة... لحظات ويُكشف المستهدف!", angle=angle)
            await asyncio.sleep(SPIN_FRAME_DELAY)
        await self._update_board(f"🎯 توقفت العجلة على <@{target_id}>!", target_id=target_id)

    async def run(self) -> None:
        try:
            while not self.finished and len(self.alive_ids) > 1:
                self.round_number += 1
                self._last_eliminated = []
                self.event_text = None
                if self.round_number % 3 == 0:
                    # Blind Storm is a visual-only event; it never changes game fairness.
                    self.event_text = random.choice([
                        "🌪️ العاصفة العمياء: تفاصيل العجلة مخفية!",
                        "⚡ اضطراب الطاقة: الجولة مشحونة!",
                        "🌑 كسوف الملوك: لا تثق بالحظ!",
                    ])
                    if "العاصفة العمياء" in self.event_text:
                        await self._update_board("🌪️ العاصفة العمياء! العجلة مخفية مؤقتًا.", obscure=True)
                        await asyncio.sleep(1.8)
                target_id = random.choice(self.alive_ids)
                await self._spin(target_id)
                self.current_target = target_id
                view = RoyaleTurnView(self, target_id)
                self._turn_view = view
                await self._update_board(
                    f"🎯 دور <@{target_id}>! أمامك **{TURN_SECONDS} ثانية** لاختيار قرارك. "
                    "عدم اتخاذ قرار يعني الطرد تلقائيًا.",
                    target_id=target_id, view=view,
                )
                # Wait for the view's timeout or for a callback to resolve the turn.
                try:
                    await asyncio.wait_for(self._wait_until_turn_resolved(view), timeout=TURN_SECONDS + 1)
                except asyncio.TimeoutError:
                    if not view.resolved:
                        view.resolved = True
                        await self.resolve_turn(target_id, action="timeout")
                view.stop()
                self.current_target = None
                if self.finished:
                    break
                await asyncio.sleep(RESULT_PAUSE_SECONDS)
            await self._finish_if_needed()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Roulette Royale game loop crashed")
            await self._update_board("⚠️ حدث خطأ داخلي وتم إيقاف اللعبة بأمان.")
        finally:
            if self.finished:
                await self.cog._release_game(self)

    async def _wait_until_turn_resolved(self, view: RoyaleTurnView) -> None:
        while not view.resolved and not self.finished:
            await asyncio.sleep(0.1)

    async def resolve_turn(self, target_id: int, *, action: str, choice_id: int | None = None) -> None:
        async with self._lock:
            if self.finished or self.current_target != int(target_id):
                return
            actor = self.players.get(int(target_id))
            if actor is None or not actor.alive:
                return
            eliminated: list[int] = []
            broken: list[int] = []
            message = ""
            if action == "timeout":
                eliminated, broken = apply_eliminations(self.players, [target_id])
                message = f"⏰ انتهى الوقت! {'انكسر الدرع وأنقذ' if broken else 'تم طرد'} <@{target_id}>."
            elif action == "shield":
                message = f"🛡️ <@{target_id}> فعّل الدرع، وسيصد محاولة طرد واحدة في الجولة القادمة."
            elif action == "inspect":
                message = f"🔍 <@{target_id}> استخدم فحص الأسرار."
            elif action == "eliminate":
                if choice_id is None or choice_id not in self.players or not self.players[choice_id].alive or choice_id == target_id:
                    # Invalid or stale selection defaults to self-elimination, preventing a free turn.
                    eliminated, broken = apply_eliminations(self.players, [target_id])
                    message = f"⚠️ الهدف لم يعد متاحًا؛ {'انكسر درعك' if broken else 'تم طردك'}."
                else:
                    eliminated, broken = apply_eliminations(self.players, [choice_id])
                    message = f"⚔️ <@{target_id}> اختار طرد <@{choice_id}>."
            elif action == "heal":
                if choice_id is None or choice_id not in self.players or self.players[choice_id].alive:
                    message = "❤️ لم يعد هدف الإنعاش متاحًا؛ لم تُستهلك المهارة."
                else:
                    self.players[choice_id].alive = True
                    self.players[choice_id].shield = False
                    self.alive_ids.append(choice_id)
                    actor.used_skills.add("heal")
                    message = f"❤️ <@{target_id}> أعاد <@{choice_id}> إلى المنافسة!"
            elif action == "chain":
                if choice_id is None or choice_id not in self.players or not self.players[choice_id].alive or choice_id == target_id:
                    message = "🔗 لم يعد هدف الربط متاحًا؛ لم تُستهلك المهارة."
                else:
                    actor.used_skills.add("chain")
                    self.players[choice_id].used_skills.add("_fate_link")
                    actor.used_skills.add("_fate_link")
                    self._fate_pair = (target_id, choice_id)
                    message = f"🔗 ارتبط مصير <@{target_id}> و<@{choice_id}> في الجولة القادمة."
            elif action == "reverse":
                actor.used_skills.add("reverse")
                if random.random() < 0.5:
                    candidates = [uid for uid in self.alive_ids if uid != target_id]
                    victims = random.sample(candidates, min(2, len(candidates)))
                    eliminated, broken = apply_eliminations(self.players, victims)
                    message = "🔄 نجحت المقامرة! تم استهداف خصمين عشوائيين: " + (
                        ", ".join(f"<@{uid}>" for uid in eliminated + broken) or "لم يتأثر أحد بسبب الدروع."
                    )
                else:
                    eliminated, broken = apply_eliminations(self.players, [target_id])
                    message = f"💥 فشلت المقامرة! {'انكسر درعك' if broken else f'تم طرد <@{target_id}>'}."
            else:
                logger.warning("Unknown Roulette Royale action: %s", action)
                eliminated, broken = apply_eliminations(self.players, [target_id])
                message = "⚠️ تعذر فهم القرار؛ تم تطبيق الطرد التلقائي."
            self._last_eliminated.extend(eliminated)
            self.alive_ids = [uid for uid in self.alive_ids if self.players[uid].alive]
            if hasattr(self, "_fate_pair"):
                first, second = self._fate_pair
                if action != "chain" and self.round_number >= getattr(self, "_fate_round", self.round_number):
                    # Fate pair is checked after a normal resolution below.
                    pass
            self._apply_fate_link(message_parts=None)
            self._turn_view = None
            self._last_message_text = message
        await self._update_board(message, target_id=target_id)
        await self._finish_if_needed()

    def _apply_fate_link(self, message_parts: list[str] | None = None) -> None:
        pair = getattr(self, "_fate_pair", None)
        if not pair:
            return
        # A link lasts for the next resolution after it is created.
        created_round = getattr(self, "_fate_round", None)
        if created_round is None:
            self._fate_round = self.round_number
            return
        if self.round_number <= created_round:
            return
        first, second = pair
        first_alive = self.players[first].alive
        second_alive = self.players[second].alive
        if first_alive != second_alive:
            victim = second if first_alive else first
            self.players[victim].alive = False
            if victim in self.alive_ids:
                self.alive_ids.remove(victim)
            self._last_eliminated.append(victim)
            if message_parts is not None:
                message_parts.append(f"🔗 انكسر رباط المصير وأُخرج <@{victim}>.")
        # Fate-link is explicitly for the next round only, whether or not it
        # triggered, so it cannot unexpectedly eliminate someone later.
        self._fate_pair = None
        self._fate_round = None

    async def _finish_if_needed(self) -> None:
        if self.finished:
            return
        self.alive_ids = [uid for uid in self.alive_ids if self.players[uid].alive]
        if len(self.alive_ids) > 1:
            return
        self.finished = True
        if self._turn_view:
            self._turn_view.stop()
        if self.alive_ids:
            winner_id = self.alive_ids[0]
            text = f"🏆👑 **<@{winner_id}> هو ملك روليت الملوك!**\nمبروك الفوز بعد {self.round_number} جولة."
        else:
            # Defensive recovery: avoid a no-winner state if all targets were protected/removed.
            living = [p.user_id for p in self.players.values() if p.alive]
            if living:
                self.alive_ids = [random.choice(living)]
                text = f"🏆👑 **<@{self.alive_ids[0]}> هو ملك روليت الملوك!**"
            else:
                # A simultaneous chain elimination can theoretically remove everyone.
                # Recover deterministically to the required single-winner end state.
                winner_id = random.choice(list(self.players))
                self.players[winner_id].alive = True
                self.alive_ids = [winner_id]
                text = f"🏆👑 **<@{winner_id}> هو ملك روليت الملوك!**\\nحُسمت الجولة بقرار تحكيمي بعد تعادل الإقصاء."
        await self._update_board(text, target_id=self.alive_ids[0] if self.alive_ids else None)

    async def cancel(self) -> None:
        self.finished = True
        if self._turn_view:
            self._turn_view.stop()
        if self._runner and self._runner is not asyncio.current_task() and not self._runner.done():
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)


class RouletteRoyale(commands.Cog):
    """Slash-command entry point and per-guild game registry."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_games: dict[int, RouletteRoyaleGame] = {}
        self._game_lock = asyncio.Lock()

    async def _release_game(self, game: RouletteRoyaleGame) -> None:
        if game.guild_id is None:
            return
        async with self._game_lock:
            if self.active_games.get(game.guild_id) is game:
                self.active_games.pop(game.guild_id, None)

    async def cog_unload(self) -> None:
        games = list(self.active_games.values())
        await asyncio.gather(*(game.cancel() for game in games), return_exceptions=True)
        async with self._game_lock:
            self.active_games.clear()

    @app_commands.command(name="roulette_royale",
                          description="ابدأ لعبة روليت الملوك البصرية متعددة اللاعبين")
    @app_commands.guild_only()
    async def roulette_royale(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("هذه اللعبة متاحة داخل السيرفر فقط.", ephemeral=True)
            return

        async def on_start(start_interaction: discord.Interaction, members: list[discord.Member]) -> None:
            guild_id = int(start_interaction.guild_id or interaction.guild_id or 0)
            if not guild_id:
                await start_interaction.followup.send("تعذر تحديد السيرفر.", ephemeral=True)
                return
            async with self._game_lock:
                existing = self.active_games.get(guild_id)
                if existing is not None and not existing.finished:
                    await start_interaction.followup.send(
                        "هناك مباراة روليت ملوك نشطة في هذا السيرفر بالفعل.", ephemeral=True
                    )
                    return
                game = RouletteRoyaleGame(self, members)
                game.guild_id = guild_id
                self.active_games[guild_id] = game
            game_message = None
            try:
                initial = game._render()
                try:
                    game_message = await start_interaction.followup.send(
                        "👑 استعدوا لروليت الملوك!", embed=game._embed("بدأت اللعبة!"),
                        file=initial, view=None, wait=True,
                    )
                finally:
                    initial.close()
                await game.start(game_message)
            except Exception:
                logger.exception("Failed to start Roulette Royale")
                if game_message is not None:
                    try:
                        await game_message.edit(view=None)
                    except discord.HTTPException:
                        logger.debug("Could not disable failed game message", exc_info=True)
                await self._release_game(game)
                raise

        lobby = UniversalLobbyView(
            interaction.user,
            game_name="روليت الملوك",
            rules=(
                "1) اللعبة من 3 إلى 10 لاعبين.\n"
                "2) كل جولة تستهدف العجلة لاعبًا واحدًا، ويملك 15 ثانية لاتخاذ قرار.\n"
                "3) عدم اتخاذ قرار يؤدي إلى محاولة طرد اللاعب تلقائيًا.\n"
                "4) كل مهارة استراتيجية تستخدم مرة واحدة لكل لاعب.\n"
                "5) آخر لاعب حي يفوز باللقب."
            ),
            on_start=on_start, min_players=MIN_PLAYERS, max_players=MAX_PLAYERS,
            timeout=300, avatar_session=getattr(self.bot, "session", None),
        )
        await interaction.response.send_message(embed=lobby.lobby_embed(),
                                                file=await lobby.render_lobby_file(), view=lobby)
        lobby_message = await interaction.original_response()
        await lobby.bind_message(lobby_message)


async def setup(bot: commands.Bot) -> None:
    """Load the cog through discord.py's extension mechanism."""
    await bot.add_cog(RouletteRoyale(bot))
