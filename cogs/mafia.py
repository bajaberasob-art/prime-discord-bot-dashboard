"""Mafia Royale — configurable social-deduction game for PRIME."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Sequence

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter

from game_engine import (
    CYAN, GREEN, MUTED, PURPLE, RED, WHITE, _font, draw_arabic_text,
    create_luxury_canvas, draw_glow_panel, draw_radial_glow,
    get_circular_avatar, image_to_file, UniversalLobbyView,
)

logger = logging.getLogger("PrimeMafiaRoyale")
ROLE_ORDER = (
    "godfather", "framer", "silencer", "detective", "doctor",
    "vigilante", "veteran", "jester", "lover", "citizen",
)
ROLE_LABELS = {
    "godfather": "👑 العرّاب", "framer": "🕵️ المُضلّل",
    "silencer": "🤫 المنوّم", "detective": "🔍 المحقق",
    "doctor": "💉 الطبيب", "vigilante": "🎯 القناص",
    "veteran": "💥 المحارب", "jester": "🃏 المهرج",
    "lover": "❤️ العاشق", "citizen": "👤 المواطن",
}
MAFIA_ROLES = {"godfather", "framer", "silencer"}
TOWN_ROLES = {"detective", "doctor", "vigilante", "veteran", "citizen"}
ROLE_DESCRIPTIONS = {
    "godfather": "قائد المافيا؛ يظهر بريئًا أمام المحقق.",
    "framer": "يلوث لاعبًا بريئًا ليظهر مشتبهًا به عند التحقيق.",
    "silencer": "يمنع لاعبًا من الكلام في الدردشة خلال النهار التالي.",
    "detective": "يفحص لاعبًا سرًا كل ليلة.",
    "doctor": "يحمي لاعبًا من القتل خلال الليل.",
    "vigilante": "رصاصة واحدة؛ قتل بريء يجعله يموت ندمًا.",
    "veteran": "وضع تأهب مرتان؛ يقتل من يزوره ليلًا.",
    "jester": "يفوز وحده إذا أُعدم بالتصويت النهاري.",
    "lover": "شريكان؛ موت أحدهما يؤدي إلى موت الآخر.",
    "citizen": "يستدل ويصوت مع البلدة.",
}
MIN_PLAYERS = 5
MAX_PLAYERS = 15
TRIAL_SECONDS = 20


@dataclass
class MafiaSettings:
    enabled_roles: set[str] = field(default_factory=lambda: {
        "godfather", "detective", "doctor", "vigilante", "citizen"
    })
    night_weather: bool = True
    forensic_newspaper: bool = True
    auto_mute: bool = True

    def toggle_role(self, role: str) -> None:
        if role not in ROLE_ORDER:
            raise ValueError("unknown_role")
        if role in self.enabled_roles:
            self.enabled_roles.remove(role)
        else:
            self.enabled_roles.add(role)
        self.enabled_roles.add("citizen")

    def toggle_option(self, option: str) -> None:
        if option not in {"night_weather", "forensic_newspaper", "auto_mute"}:
            raise ValueError("unknown_option")
        setattr(self, option, not getattr(self, option))

    def validate(self, player_count: int) -> None:
        if not MIN_PLAYERS <= player_count <= MAX_PLAYERS:
            raise ValueError("invalid_player_count")
        if not self.enabled_roles.intersection(MAFIA_ROLES):
            raise ValueError("enable_at_least_one_mafia_role")
        if "citizen" not in self.enabled_roles:
            raise ValueError("citizen_role_required")
        # The role pool may not exceed the number of players; citizens fill the rest.
        special = self.enabled_roles - {"citizen"}
        if len(special) > player_count:
            raise ValueError("too_many_roles")


@dataclass
class MafiaPlayer:
    member: object
    role: str = "citizen"
    alive: bool = True
    will: str = ""
    silenced: bool = False
    bullets: int = 1
    veteran_alerts: int = 2
    lover_id: int | None = None
    framed: bool = False
    visited: bool = False

    @property
    def user_id(self) -> int:
        return int(getattr(self.member, "id"))

    @property
    def name(self) -> str:
        return str(getattr(self.member, "display_name", getattr(self.member, "name", "Player")))


def distribute_roles(members: Sequence[object], settings: MafiaSettings, rng=None) -> list[MafiaPlayer]:
    """Shuffle enabled special roles without replacement; citizens fill remaining slots."""
    rng = rng or random
    settings.validate(len(members))
    roles = list(settings.enabled_roles - {"citizen"})
    # Lovers is a paired role: enabling it creates exactly two linked players.
    if "lover" in roles:
        roles.remove("lover")
        roles.extend(["lover", "lover"])
    mafia_count = sum(role in MAFIA_ROLES for role in roles)
    if mafia_count == 0:
        raise ValueError("enable_at_least_one_mafia_role")
    if len(roles) > len(members):
        raise ValueError("too_many_roles")
    roles.extend(["citizen"] * (len(members) - len(roles)))
    rng.shuffle(roles)
    players = [MafiaPlayer(member=member, role=role) for member, role in zip(members, roles)]
    lovers = [p for p in players if p.role == "lover"]
    if len(lovers) >= 2:
        lovers[0].lover_id, lovers[1].lover_id = lovers[1].user_id, lovers[0].user_id
    return players


def investigate(target: MafiaPlayer, *, framed: bool = False) -> str:
    """Return an investigation result; Godfather reads innocent unless framed."""
    if target.role == "godfather":
        return "mafia" if framed or target.framed else "innocent"
    if target.role in MAFIA_ROLES or framed or target.framed:
        return "mafia"
    return "innocent"


def resolve_night(
    players: Sequence[MafiaPlayer],
    *,
    mafia_target: int | None,
    doctor_target: int | None = None,
    detective_target: int | None = None,
    framed_target: int | None = None,
    silenced_target: int | None = None,
    veteran_targets: set[int] | None = None,
    veteran_visitors: dict[int, set[int]] | None = None,
    vigilante_target: int | None = None,
) -> dict[str, object]:
    """Resolve night actions in a deterministic order and return private/public events."""
    by_id = {p.user_id: p for p in players}
    living = {uid for uid, p in by_id.items() if p.alive}
    events: list[str] = []
    deaths: set[int] = set()
    veteran_targets = veteran_targets or set()
    veteran_visitors = veteran_visitors or {}
    for p in players:
        p.framed = False
        p.visited = False
    if framed_target in living and by_id[framed_target].role not in MAFIA_ROLES:
        by_id[framed_target].framed = True
    # A Veteran who activated alert is not killed by a visitor; each visitor is retaliated against.
    for veteran_id in veteran_targets:
        veteran = by_id.get(veteran_id)
        if veteran and veteran.alive and veteran.role == "veteran" and veteran.veteran_alerts > 0:
            visitors = veteran_visitors.get(veteran_id, set())
            if visitors:
                deaths.update(uid for uid in visitors if uid in living and uid != veteran_id)
                veteran.veteran_alerts -= 1
                events.append("veteran_alert")
    if mafia_target in living:
        by_id[mafia_target].visited = True
        if mafia_target not in veteran_targets:
            deaths.add(mafia_target)
    if vigilante_target in living:
        shooter = next((p for p in players if p.alive and p.role == "vigilante" and p.bullets > 0), None)
        if shooter:
            shooter.bullets -= 1
            deaths.add(vigilante_target)
            if by_id[vigilante_target].role not in MAFIA_ROLES and by_id[vigilante_target].role != "jester":
                deaths.add(shooter.user_id)
                events.append("vigilante_regret")
    if doctor_target in living:
        deaths.discard(doctor_target)
    # A visitor who triggers an alert is killed; the Veteran itself remains protected.
    for uid in list(deaths):
        target = by_id.get(uid)
        if target and target.lover_id in living:
            deaths.add(target.lover_id)
    for uid in deaths:
        by_id[uid].alive = False
        events.append(f"death:{uid}")
    if detective_target in living:
        detective = next((p for p in players if p.alive and p.role == "detective"), None)
        if detective:
            events.append(f"investigation:{detective.user_id}:{investigate(by_id[detective_target])}")
    if silenced_target in living and by_id[silenced_target].alive:
        by_id[silenced_target].silenced = True
        events.append(f"silenced:{silenced_target}")
    return {"deaths": sorted(deaths), "events": events, "living": sorted(uid for uid, p in by_id.items() if p.alive)}


def tally_trial(votes: dict[int, str], *, candidate_id: int, living_ids: set[int]) -> dict[str, object]:
    """Count only valid votes from living players; ties and abstentions acquit."""
    valid = {uid: vote for uid, vote in votes.items()
             if uid in living_ids and uid != candidate_id and vote in {"execute", "spare"}}
    execute = sum(vote == "execute" for vote in valid.values())
    spare = sum(vote == "spare" for vote in valid.values())
    executed = execute > spare
    return {"execute_votes": execute, "spare_votes": spare, "executed": executed,
            "winner": "jester" if executed else None}


def check_victory(players: Sequence[MafiaPlayer]) -> str | None:
    living = [p for p in players if p.alive]
    if any(p.role == "jester" and not p.alive and getattr(p, "won_as_jester", False) for p in players):
        return "jester"
    mafia = sum(p.role in MAFIA_ROLES for p in living)
    town = sum(p.role in TOWN_ROLES or p.role == "lover" for p in living)
    if mafia == 0:
        return "town"
    if mafia >= town:
        return "mafia"
    return None


def render_setup_board(settings: MafiaSettings, *, player_count: int, host_name: str = "المضيف") -> Image.Image:
    image = create_luxury_canvas(1200, 850, "MAFIA ROYALE", "لوحة تخصيص الأدوار والقواعد", accent=PURPLE).convert("RGBA")
    draw = ImageDraw.Draw(image)
    draw_glow_panel(image, (45, 145, 1155, 785), fill=(12, 16, 29, 235), outline="#49376b", glow=PURPLE, radius=26)
    draw = ImageDraw.Draw(image)
    draw_arabic_text(draw, (1100, 105), f"المضيف: {host_name} • اللاعبون: {player_count}", font=_font(18, True), fill=CYAN, anchor="ra")
    for index, role in enumerate(ROLE_ORDER):
        col, row = index % 2, index // 2
        x, y = 75 + col * 550, 170 + row * 99
        active = role in settings.enabled_roles
        color = GREEN if active else "#667085"
        draw_glow_panel(image, (x, y, x + 510, y + 78), fill=(18, 26, 42, 225), outline=color, glow=color if active else "#29324a", radius=14, width=2)
        draw = ImageDraw.Draw(image)
        draw_arabic_text(draw, (x + 490, y + 23), ROLE_LABELS[role], font=_font(18, True), fill=WHITE if active else MUTED, anchor="ra")
        draw_arabic_text(draw, (x + 490, y + 54), "مفعّل" if active else "معطّل", font=_font(13, True), fill=color, anchor="ra")
    options = [
        ("🌧️ الطقس الليلي", settings.night_weather),
        ("📰 الأدلة الجنائية", settings.forensic_newspaper),
        ("🎙️ الميوت التلقائي", settings.auto_mute),
    ]
    for index, (label, active) in enumerate(options):
        x = 85 + index * 365
        draw.rounded_rectangle((x, 700, x + 330, 750), radius=12, fill="#142536", outline=GREEN if active else "#667085", width=2)
        draw_arabic_text(draw, (x + 165, 725), f"{label}: {'ON' if active else 'OFF'}", font=_font(14, True), fill=WHITE, anchor="mm")
    return image


def render_mafia_board(players: Sequence[MafiaPlayer], *, phase: str, headline: str = "",
                       candidate_id: int | None = None, avatars: dict[int, Image.Image] | None = None) -> Image.Image:
    titles = {"night": "الليل الدامس", "morning": "جريدة جريمة الفجر", "trial": "قفص الاتهام • ميزان العدالة", "reveal": "كشف الأقنعة"}
    accent = RED if phase in {"night", "trial"} else CYAN
    image = create_luxury_canvas(1200, 800, "MAFIA ROYALE", titles.get(phase, "MAFIA ROYALE"), accent=accent).convert("RGBA")
    draw_radial_glow(image, (600, 370), 300, color=accent, max_alpha=45, blur=24)
    draw = ImageDraw.Draw(image)
    draw_glow_panel(image, (45, 155, 1155, 710), fill=(11, 15, 27, 235), outline=accent, glow=accent, radius=25)
    draw = ImageDraw.Draw(image)
    draw_arabic_text(draw, (600, 125), headline[:100], font=_font(22, True), fill=WHITE, anchor="mm")
    avatars = avatars or {}
    for index, player in enumerate(players):
        col, row = index % 4, index // 4
        x, y = 70 + col * 275, 190 + row * 145
        alive_color = GREEN if player.alive else "#6b7280"
        outline = RED if player.user_id == candidate_id else alive_color
        draw.rounded_rectangle((x, y, x + 250, y + 122), radius=15, fill="#171d2d", outline=outline, width=3)
        avatar = avatars.get(player.user_id)
        if avatar is not None:
            image.alpha_composite(avatar.convert("RGBA").resize((54, 54)), (x + 12, y + 20))
        else:
            draw.ellipse((x + 12, y + 20, x + 66, y + 74), fill="#29233f", outline=outline, width=2)
        draw = ImageDraw.Draw(image)
        draw_arabic_text(draw, (x + 78, y + 30), player.name[:17], font=_font(15, True), fill=WHITE if player.alive else MUTED)
        draw_arabic_text(draw, (x + 78, y + 65), "على قيد الحياة" if player.alive else "ميت", font=_font(13), fill=alive_color)
        if phase == "reveal":
            draw_arabic_text(draw, (x + 125, y + 100), ROLE_LABELS[player.role], font=_font(13, True), fill=PURPLE, anchor="mm")
    draw_arabic_text(draw, (600, 755), "PRIME • SOCIAL DEDUCTION", font=_font(13, True), fill=MUTED, anchor="mm")
    return image


class MafiaSetupView(discord.ui.View):
    def __init__(self, game: "MafiaGame"):
        super().__init__(timeout=300)
        self.game = game

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.game.host_id:
            await interaction.response.send_message("إعدادات المباراة متاحة للمضيف فقط.", ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        image_file = image_to_file(render_setup_board(self.game.settings, player_count=len(self.game.members), host_name=self.game.host_name), "mafia-setup.png")
        try:
            await interaction.response.edit_message(embed=self.game.setup_embed(), attachments=[image_file], view=self)
        finally:
            image_file.close()

    @discord.ui.button(label="⚙️ تخصيص الأدوار والقواعد", style=discord.ButtonStyle.primary, row=0)
    async def open_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("لوحة المضيف: فعّل أو عطّل الأدوار والخيارات أدناه.", view=MafiaRoleToggleView(self.game), ephemeral=True)

    @discord.ui.button(label="🎮 بدء المافيا الملكية", style=discord.ButtonStyle.success, row=0)
    async def start_game(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if len(self.game.members) < MIN_PLAYERS:
            await interaction.response.send_message(f"تحتاج إلى {MIN_PLAYERS} لاعبين على الأقل.", ephemeral=True)
            return
        try:
            self.game.start_roles()
        except ValueError as exc:
            await interaction.response.send_message(f"إعدادات الأدوار غير صالحة: {exc}", ephemeral=True)
            return
        self.game.started = True
        await self.game.start_night(interaction)

    @discord.ui.button(label="📋 عرض الإعدادات", style=discord.ButtonStyle.secondary, row=0)
    async def show_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._refresh(interaction)


class MafiaRoleToggleView(discord.ui.View):
    def __init__(self, game: "MafiaGame"):
        super().__init__(timeout=300)
        self.game = game
        for role in ROLE_ORDER:
            button = discord.ui.Button(label=ROLE_LABELS[role], style=discord.ButtonStyle.success if role in game.settings.enabled_roles else discord.ButtonStyle.secondary, row=0 if ROLE_ORDER.index(role) < 5 else 1)
            button.callback = self._role_callback(role)
            self.add_item(button)
        for option, label in (
            ("night_weather", "🌧️ الطقس الليلي"),
            ("forensic_newspaper", "📰 الأدلة الجنائية"),
            ("auto_mute", "🎙️ الميوت التلقائي"),
        ):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.success if getattr(game.settings, option) else discord.ButtonStyle.secondary, row=2)
            button.callback = self._option_callback(option)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.game.host_id:
            await interaction.response.send_message("هذه اللوحة للمضيف فقط.", ephemeral=True)
            return False
        return True

    def _role_callback(self, role: str):
        async def callback(interaction: discord.Interaction):
            try:
                self.game.settings.toggle_role(role)
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            # Update toggle button labels and states in place.
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    if child.label == ROLE_LABELS[role]:
                        child.style = discord.ButtonStyle.success if role in self.game.settings.enabled_roles else discord.ButtonStyle.secondary
            await interaction.response.edit_message(content=self.game.settings_summary(), view=self)
        return callback

    def _option_callback(self, option: str):
        async def callback(interaction: discord.Interaction):
            self.game.settings.toggle_option(option)
            await interaction.response.edit_message(content=self.game.settings_summary(), view=self)
        return callback

    def settings_summary(self) -> str:
        active = "، ".join(ROLE_LABELS[r] for r in ROLE_ORDER if r in self.game.settings.enabled_roles)
        inactive = "، ".join(ROLE_LABELS[r] for r in ROLE_ORDER if r not in self.game.settings.enabled_roles)
        return (f"**الأدوار المفعّلة:** {active}\n**الأدوار المعطّلة:** {inactive}\n"
                f"🌧️ الطقس: {'ON' if self.game.settings.night_weather else 'OFF'} • "
                f"📰 الأدلة: {'ON' if self.game.settings.forensic_newspaper else 'OFF'} • "
                f"🎙️ الميوت: {'ON' if self.game.settings.auto_mute else 'OFF'}")


class LastWillModal(discord.ui.Modal, title="وصية الموت • Last Will"):
    will = discord.ui.TextInput(label="وصيتك وملاحظاتك السرية", style=discord.TextStyle.paragraph, max_length=900, required=False, placeholder="اكتب ملاحظاتك لمن سيبقى بعدك…")

    def __init__(self, game: "MafiaGame", user_id: int):
        super().__init__()
        self.game = game
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        player = self.game.by_id.get(self.user_id)
        if not player or not player.alive:
            await interaction.response.send_message("لا يمكن تعديل الوصية بعد الموت.", ephemeral=True)
            return
        player.will = str(self.will.value).strip()
        await interaction.response.send_message("تم حفظ وصيتك بسرية.", ephemeral=True)


class MafiaActionView(discord.ui.View):
    def __init__(self, game: "MafiaGame", *, phase: str, candidate_id: int | None = None):
        super().__init__(timeout=TRIAL_SECONDS if phase == "trial" else 90)
        self.game = game
        self.phase = phase
        self.candidate_id = candidate_id
        self.votes: dict[int, str] = {}
        self.actions: dict[int, int] = {}
        self._event = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        player = self.game.by_id.get(int(interaction.user.id))
        if not player or not player.alive:
            await interaction.response.send_message("المشاركة متاحة للاعبين الأحياء فقط.", ephemeral=True)
            return False
        if self.phase == "trial" and player.user_id == self.candidate_id:
            await interaction.response.send_message("المتهم لا يصوت في محاكمته.", ephemeral=True)
            return False
        return True

    async def _act(self, interaction: discord.Interaction, action: str, target_id: int | None = None) -> None:
        uid = int(interaction.user.id)
        if self.phase == "trial":
            self.votes[uid] = action
            await interaction.response.send_message("تم تسجيل تصويتك سرًا.", ephemeral=True)
        else:
            self.actions[uid] = int(target_id or 0)
            await interaction.response.send_message("تم تسجيل قرارك سرًا.", ephemeral=True)
        self._event.set()

    @discord.ui.button(label="⚖️ إعدام", style=discord.ButtonStyle.danger, row=0)
    async def execute(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.phase != "trial":
            await interaction.response.send_message("هذا الزر مخصص للمحاكمة.", ephemeral=True)
            return
        await self._act(interaction, "execute")

    @discord.ui.button(label="🕊️ براءة", style=discord.ButtonStyle.success, row=0)
    async def spare(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.phase != "trial":
            await interaction.response.send_message("هذا الزر مخصص للمحاكمة.", ephemeral=True)
            return
        await self._act(interaction, "spare")

    @discord.ui.button(label="📜 وصيتي الأخيرة", style=discord.ButtonStyle.secondary, row=1)
    async def will_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(LastWillModal(self.game, int(interaction.user.id)))

    @discord.ui.button(label="🌙 اختر هدفًا", style=discord.ButtonStyle.primary, row=1)
    async def target_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.phase != "night":
            await interaction.response.send_message("اختيار الهدف متاح في الليل فقط.", ephemeral=True)
            return
        options = [discord.SelectOption(label=p.name[:100], value=str(p.user_id)) for p in self.game.players if p.alive and p.user_id != int(interaction.user.id)]
        if not options:
            await interaction.response.send_message("لا يوجد هدف متاح.", ephemeral=True)
            return
        player = self.game.by_id[int(interaction.user.id)]
        if player.role == "veteran":
            if player.veteran_alerts <= 0:
                await interaction.response.send_message("استهلكت وضعيتي التأهب.", ephemeral=True)
                return
            self.actions[player.user_id] = player.user_id
            await interaction.response.send_message("💥 تم تفعيل وضع التأهب لهذه الليلة؛ من يزور بابك سيتعرض للنيران.", ephemeral=True)
            self._event.set()
            return
        if player.role == "vigilante" and player.bullets <= 0:
            await interaction.response.send_message("استخدمت رصاصتك الوحيدة بالفعل.", ephemeral=True)
            return
        await interaction.response.send_message("اختر هدفك السري:", view=MafiaTargetView(self, int(interaction.user.id), options), ephemeral=True)


class MafiaTargetView(discord.ui.View):
    def __init__(self, action_view: MafiaActionView, user_id: int, options: list[discord.SelectOption]):
        super().__init__(timeout=60)
        self.action_view = action_view
        self.user_id = user_id
        self.add_item(MafiaTargetSelect(action_view, user_id, options))


class MafiaTargetSelect(discord.ui.Select):
    def __init__(self, action_view: MafiaActionView, user_id: int, options: list[discord.SelectOption]):
        super().__init__(placeholder="اختر اللاعب", min_values=1, max_values=1, options=options)
        self.action_view = action_view
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("هذه القائمة ليست لك.", ephemeral=True)
            return
        self.action_view.actions[self.user_id] = int(self.values[0])
        await interaction.response.send_message("تم حفظ اختيارك سريًا.", ephemeral=True)
        self.action_view._event.set()


class MafiaGame:
    def __init__(self, cog: "Mafia", host: discord.Member, members: Sequence[discord.Member], settings: MafiaSettings | None = None):
        self.cog = cog
        self.host_id = int(host.id)
        self.host_name = str(getattr(host, "display_name", host.name))
        self.members = list(members)
        self.settings = settings or MafiaSettings()
        self.players: list[MafiaPlayer] = []
        self.by_id: dict[int, MafiaPlayer] = {}
        self.avatars: dict[int, Image.Image] = {}
        self.message: discord.Message | None = None
        self.started = False
        self.finished = False
        self.day_number = 0
        self.setup_view = MafiaSetupView(self)
        self.last_newspaper = ""
        self._lock = asyncio.Lock()

    def setup_embed(self) -> discord.Embed:
        active = sum(1 for r in ROLE_ORDER if r in self.settings.enabled_roles)
        return discord.Embed(title="👑 Mafia Royale • إعداد المضيف",
                             description=f"اللاعبون: **{len(self.members)}**\nالأدوار المفعّلة: **{active}/10**\nاضغط ⚙️ لتخصيص الأدوار والقواعد.",
                             color=discord.Color.purple())

    def start_roles(self) -> None:
        self.players = distribute_roles(self.members, self.settings)
        self.by_id = {p.user_id: p for p in self.players}
        self.started = True
        # Secret role assignment is sent privately; members who block DMs can still play from the shared action UI.
        for player in self.players:
            try:
                awaitable = player.member.send(
                    f"🎭 دورك في Mafia Royale: **{ROLE_LABELS[player.role]}**\\n{ROLE_DESCRIPTIONS[player.role]}"
                )
                if hasattr(awaitable, "__await__"):
                    asyncio.create_task(awaitable)
            except (discord.HTTPException, AttributeError):
                logger.debug("Could not DM role to player %s", player.user_id, exc_info=True)

    async def start_night(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("🌑 بدأت الليلة الأولى. ستصل أزرار الأدوار السرية عبر رسائل خاصة.", ephemeral=True)
        await self.run_game()

    async def run_game(self) -> None:
        try:
            while not self.finished:
                self.day_number += 1
                living = [p for p in self.players if p.alive]
                if not living:
                    break
                if self.settings.auto_mute:
                    await self._apply_voice_mutes(night=True)
                view = MafiaActionView(self, phase="night")
                if self.message:
                    night_file = image_to_file(render_mafia_board(self.players, phase="night", headline="الليل الدامس", avatars=self.avatars), "mafia-night.png")
                    try:
                        await self.message.edit(embed=discord.Embed(title=f"🌑 الليل الدامس — ليلة {self.day_number}", description="تتحرك المافيا في الظلام…", color=discord.Color.dark_purple()), attachments=[night_file], view=view)
                    finally:
                        night_file.close()
                # Wait for all enabled living roles to submit an action, or end the night after 90 seconds.
                required = {
                    p.user_id for p in self.players if p.alive and (
                        p.role in MAFIA_ROLES or p.role in {"doctor", "detective"}
                        or (p.role == "vigilante" and p.bullets > 0)
                        or (p.role == "veteran" and p.veteran_alerts > 0)
                    )
                }
                deadline = asyncio.get_running_loop().time() + 90
                while not required.issubset(view.actions):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    view._event.clear()
                    try:
                        await asyncio.wait_for(view._event.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                actions = view.actions
                mafia_target = next((actions[p.user_id] for p in self.players if p.alive and p.role == "godfather" and p.user_id in actions), None)
                if mafia_target is None:
                    mafia_target = next((actions[p.user_id] for p in self.players if p.alive and p.role in MAFIA_ROLES and p.role not in {"framer", "silencer"} and p.user_id in actions), None)
                doctor_target = next((actions[p.user_id] for p in self.players if p.alive and p.role == "doctor" and p.user_id in actions), None)
                detective_target = next((actions[p.user_id] for p in self.players if p.alive and p.role == "detective" and p.user_id in actions), None)
                framed_target = next((actions[p.user_id] for p in self.players if p.alive and p.role == "framer" and p.user_id in actions), None)
                silenced_target = next((actions[p.user_id] for p in self.players if p.alive and p.role == "silencer" and p.user_id in actions), None)
                veteran_targets = {
                    p.user_id for p in self.players if p.alive and p.role == "veteran"
                    and actions.get(p.user_id) == p.user_id and p.veteran_alerts > 0
                }
                veteran_visitors: dict[int, set[int]] = {}
                for actor_id, target_id in actions.items():
                    if target_id in veteran_targets and actor_id != target_id:
                        veteran_visitors.setdefault(target_id, set()).add(actor_id)
                vigilante_target = next((actions[p.user_id] for p in self.players if p.alive and p.role == "vigilante" and p.bullets > 0 and p.user_id in actions), None)
                result = resolve_night(
                    self.players, mafia_target=mafia_target, doctor_target=doctor_target,
                    detective_target=detective_target, framed_target=framed_target,
                    silenced_target=silenced_target, veteran_targets=veteran_targets,
                    veteran_visitors=veteran_visitors, vigilante_target=vigilante_target,
                )
                for event in result["events"]:
                    if event.startswith("investigation:"):
                        _, detective_id, verdict = event.split(":", 2)
                        detective = self.by_id.get(int(detective_id))
                        if detective:
                            try:
                                await detective.member.send("🔍 نتيجة تحقيقك: " + ("المشتبه به يبدو من المافيا." if verdict == "mafia" else "المشتبه به يبدو بريئًا."))
                            except (discord.HTTPException, AttributeError):
                                logger.debug("Could not DM investigation result", exc_info=True)
                dead = [p for p in self.players if p.user_id in result["deaths"]]
                clue = random.choice(["شوهد شخص يهرب من قرب الساحة.", "آثار أقدام تتجه نحو المستودع.", "مصباح الشارع انطفأ أثناء الجريمة."]) if self.settings.forensic_newspaper else "لا توجد أدلة جنائية الليلة."
                wills = "\n".join(f"📜 وصية {p.name}: {p.will}" for p in dead if p.will)
                self.last_newspaper = f"📰 **جريدة الفجر — اليوم {self.day_number}**\n" + ("لم تقع جريمة الليلة." if not dead else "الضحايا: " + "، ".join(p.name for p in dead)) + f"\n🔎 {clue}" + (f"\n{wills}" if wills else "")
                if self.message:
                    f = image_to_file(render_mafia_board(self.players, phase="morning", headline=self.last_newspaper[:80], avatars=self.avatars), "mafia-morning.png")
                    await self.message.edit(content=self.last_newspaper, attachments=[f], view=MafiaActionView(self, phase="day"))
                    f.close()
                if self.settings.auto_mute:
                    await self._apply_voice_mutes(night=False)
                for player in self.players:
                    if player.alive and player.silenced:
                        try:
                            await player.member.timeout(timedelta(seconds=60), reason="Mafia Royale: Silencer role")
                        except (discord.Forbidden, discord.HTTPException, AttributeError):
                            logger.debug("Could not enforce text silence for %s", player.user_id, exc_info=True)
                        player.silenced = False
                if check_victory(self.players):
                    await self.end_game(check_victory(self.players) or "town")
                    return
                # Open a short public nomination window; the most-voted living player is tried.
                day_view = MafiaDayView(self)
                if self.message:
                    await self.message.edit(content=self.last_newspaper + "\n\nرشّحوا المشتبه به عبر أزرار التصويت.", view=day_view)
                eligible_voters = {p.user_id for p in self.players if p.alive}
                deadline = asyncio.get_running_loop().time() + 60
                while not eligible_voters.issubset(day_view.votes):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    day_view.event.clear()
                    try:
                        await asyncio.wait_for(day_view.event.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                candidate = day_view.top_candidate()
                if candidate is not None:
                    await self.run_trial(candidate)
                    if check_victory(self.players):
                        await self.end_game(check_victory(self.players) or "town")
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mafia Royale game failed")
            self.finished = True
            await self.cog.release(self)

    async def run_trial(self, candidate_id: int) -> None:
        candidate = self.by_id[candidate_id]
        view = MafiaActionView(self, phase="trial", candidate_id=candidate_id)
        if self.message:
            f = image_to_file(render_mafia_board(self.players, phase="trial", headline=f"المتهم: {candidate.name}", candidate_id=candidate_id, avatars=self.avatars), "mafia-trial.png")
            await self.message.edit(content=f"⚖️ قفص الاتهام: **{candidate.name}** — أمامكم {TRIAL_SECONDS} ثانية.", attachments=[f], view=view)
            f.close()
        eligible = {p.user_id for p in self.players if p.alive and p.user_id != candidate_id}
        deadline = asyncio.get_running_loop().time() + TRIAL_SECONDS
        while not eligible.issubset(view.votes):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            view._event.clear()
            try:
                await asyncio.wait_for(view._event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                break
        result = tally_trial(view.votes, candidate_id=candidate_id, living_ids={p.user_id for p in self.players if p.alive})
        if result["executed"]:
            candidate.alive = False
            if candidate.role == "jester":
                setattr(candidate, "won_as_jester", True)
                await self.end_game("jester")
                return
            if candidate.lover_id in self.by_id:
                self.by_id[candidate.lover_id].alive = False
        if self.message:
            await self.message.edit(content=f"نتيجة المحاكمة: إعدام {result['execute_votes']} — براءة {result['spare_votes']}.", view=None)

    async def _apply_voice_mutes(self, *, night: bool = False) -> None:
        # Voice mute is best-effort and only affects members currently connected to voice.
        for player in self.players:
            member = player.member
            voice = getattr(member, "voice", None)
            if not voice or not voice.channel:
                continue
            desired = (night and player.alive) or not player.alive
            try:
                await member.edit(mute=desired, reason="Mafia Royale: night/dead-player voice mute")
            except (discord.Forbidden, discord.HTTPException, AttributeError):
                logger.debug("Could not apply voice mute to %s", player.user_id, exc_info=True)

    async def end_game(self, winner: str) -> None:
        self.finished = True
        if self.message:
            f = image_to_file(render_mafia_board(self.players, phase="reveal", headline=f"انتصار: {winner}", avatars=self.avatars), "mafia-reveal.png")
            try:
                await self.message.edit(content=f"🎭 **كشف الأقنعة — الفائز: {winner}**", attachments=[f], view=None)
            finally:
                f.close()
        await self.cog.release(self)


class MafiaDayView(discord.ui.View):
    def __init__(self, game: MafiaGame):
        super().__init__(timeout=60)
        self.game = game
        self.votes: dict[int, int] = {}
        self.event = asyncio.Event()
        self.add_item(MafiaNominationSelect(self))

    def top_candidate(self) -> int | None:
        if not self.votes:
            return None
        counts: dict[int, int] = {}
        for target in self.votes.values():
            if target in self.game.by_id and self.game.by_id[target].alive:
                counts[target] = counts.get(target, 0) + 1
        return max(counts, key=counts.get) if counts else None


class MafiaNominationSelect(discord.ui.Select):
    def __init__(self, view: MafiaDayView):
        self.mafia_view = view
        options = [discord.SelectOption(label=p.name[:100], value=str(p.user_id)) for p in view.game.players if p.alive]
        super().__init__(placeholder="رشّح لاعبًا للمحاكمة", min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction: discord.Interaction) -> None:
        player = self.mafia_view.game.by_id.get(int(interaction.user.id))
        if not player or not player.alive:
            await interaction.response.send_message("التصويت للأحياء فقط.", ephemeral=True)
            return
        self.mafia_view.votes[player.user_id] = int(self.values[0])
        self.mafia_view.event.set()
        await interaction.response.send_message("تم تسجيل الترشيح.", ephemeral=True)



class MafiaLobbySettingsView(discord.ui.View):
    """Ephemeral, host-only role and rule toggles available before the lobby starts."""
    def __init__(self, lobby: "MafiaLobbyView"):
        super().__init__(timeout=300)
        self.lobby = lobby
        for role_index, role in enumerate(ROLE_ORDER):
            button = discord.ui.Button(
                label=ROLE_LABELS[role],
                style=discord.ButtonStyle.success if role in lobby.mafia_settings.enabled_roles else discord.ButtonStyle.secondary,
                row=role_index // 5,
            )
            button.callback = self._role_callback(role)
            self.add_item(button)
        for option, label in (
            ("night_weather", "🌧️ الطقس الليلي"),
            ("forensic_newspaper", "📰 الأدلة الجنائية"),
            ("auto_mute", "🎙️ الميوت التلقائي"),
        ):
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success if getattr(lobby.mafia_settings, option) else discord.ButtonStyle.secondary,
                row=2,
            )
            button.callback = self._option_callback(option)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != int(self.lobby.host.id):
            await interaction.response.send_message("لوحة تخصيص الأدوار للمضيف فقط.", ephemeral=True)
            return False
        return True

    def summary(self) -> str:
        settings = self.lobby.mafia_settings
        active = "، ".join(ROLE_LABELS[r] for r in ROLE_ORDER if r in settings.enabled_roles)
        inactive = "، ".join(ROLE_LABELS[r] for r in ROLE_ORDER if r not in settings.enabled_roles)
        return (f"**الأدوار المفعّلة:** {active}\n**الأدوار المعطّلة:** {inactive}\n"
                f"🌧️ الطقس: {'ON' if settings.night_weather else 'OFF'} • "
                f"📰 الأدلة: {'ON' if settings.forensic_newspaper else 'OFF'} • "
                f"🎙️ الميوت: {'ON' if settings.auto_mute else 'OFF'}")

    def _role_callback(self, role: str):
        async def callback(interaction: discord.Interaction):
            try:
                self.lobby.mafia_settings.toggle_role(role)
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            for child in self.children:
                if isinstance(child, discord.ui.Button) and child.label == ROLE_LABELS[role]:
                    child.style = discord.ButtonStyle.success if role in self.lobby.mafia_settings.enabled_roles else discord.ButtonStyle.secondary
            await interaction.response.edit_message(content=self.summary(), view=self)
        return callback

    def _option_callback(self, option: str):
        async def callback(interaction: discord.Interaction):
            self.lobby.mafia_settings.toggle_option(option)
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    if (option == "night_weather" and child.label == "🌧️ الطقس الليلي"
                        or option == "forensic_newspaper" and child.label == "📰 الأدلة الجنائية"
                        or option == "auto_mute" and child.label == "🎙️ الميوت التلقائي"):
                        child.style = discord.ButtonStyle.success if getattr(self.lobby.mafia_settings, option) else discord.ButtonStyle.secondary
            await interaction.response.edit_message(content=self.summary(), view=self)
        return callback


class MafiaLobbyView(UniversalLobbyView):
    def __init__(self, host: discord.Member, *, on_start, avatar_session=None):
        self.mafia_settings = MafiaSettings()
        super().__init__(
            host, game_name="Mafia Royale",
            rules="خصص الأدوار والميزات من زر ⚙️ المخصص للمضيف. نفّذ الأدوار سرًا ليلًا، ناقش الأدلة نهارًا، ثم صوّت للمحاكمة.",
            on_start=on_start, min_players=MIN_PLAYERS, max_players=MAX_PLAYERS,
            timeout=300, avatar_session=avatar_session,
        )

    @discord.ui.button(label="⚙️ تخصيص الأدوار والقواعد", style=discord.ButtonStyle.secondary, row=1)
    async def host_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if int(interaction.user.id) != int(self.host.id):
            await interaction.response.send_message("هذه اللوحة للمضيف فقط.", ephemeral=True)
            return
        view = MafiaLobbySettingsView(self)
        await interaction.response.send_message(
            content=view.summary(), view=view, ephemeral=True
        )


class Mafia(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: dict[int, MafiaGame] = {}
        self.lock = asyncio.Lock()

    async def release(self, game: MafiaGame) -> None:
        guild_id = getattr(getattr(game.message, "guild", None), "id", None)
        if guild_id is not None:
            async with self.lock:
                if self.games.get(int(guild_id)) is game:
                    self.games.pop(int(guild_id), None)

    @app_commands.command(name="mafia", description="ابدأ المافيا الملكية")
    @app_commands.guild_only()
    async def mafia(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("اللعبة متاحة داخل السيرفر فقط.", ephemeral=True)
            return
        guild_id = int(interaction.guild.id)
        async with self.lock:
            if guild_id in self.games:
                await interaction.response.send_message("توجد مباراة مافيا نشطة في هذا السيرفر.", ephemeral=True)
                return

        async def on_start(lobby_interaction: discord.Interaction, members: Sequence[discord.Member]) -> None:
            if not MIN_PLAYERS <= len(members) <= MAX_PLAYERS:
                await lobby_interaction.followup.send(f"المافيا تحتاج من {MIN_PLAYERS} إلى {MAX_PLAYERS} لاعبًا.", ephemeral=True)
                return
            game = MafiaGame(self, interaction.user, members, settings=lobby.mafia_settings)
            async with self.lock:
                if guild_id in self.games:
                    await lobby_interaction.followup.send("توجد مباراة نشطة بالفعل.", ephemeral=True)
                    return
                self.games[guild_id] = game
            images = await asyncio.gather(
                *(get_circular_avatar(member, 54, session=getattr(self.bot, "session", None), timeout=3) for member in members),
                return_exceptions=True,
            )
            for member, avatar in zip(members, images):
                if hasattr(avatar, "convert"):
                    game.avatars[int(member.id)] = avatar
            f = image_to_file(render_setup_board(game.settings, player_count=len(members), host_name=game.host_name), "mafia-setup.png")
            try:
                message = await lobby_interaction.followup.send(embed=game.setup_embed(), file=f, view=game.setup_view, wait=True)
            finally:
                f.close()
            game.message = message

        lobby = MafiaLobbyView(interaction.user, on_start=on_start, avatar_session=getattr(self.bot, "session", None))
        f = await lobby.render_lobby_file()
        try:
            await interaction.response.send_message(embed=lobby.lobby_embed(), file=f, view=lobby)
        finally:
            f.close()
        await lobby.bind_message(await interaction.original_response())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Mafia(bot))
