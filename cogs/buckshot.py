"""Buckshot Roulette: a tactical, visual multiplayer survival game for PRIME."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw

from game_engine import (
    CYAN, GREEN, MUTED, PURPLE, RED, WHITE, _font, draw_arabic_text,
    create_luxury_canvas, draw_glow_panel, draw_radial_glow, image_to_file,
    UniversalLobbyView,
)

logger = logging.getLogger("PrimeBuckshot")
MIN_PLAYERS = 2
MAX_PLAYERS = 6
TURN_SECONDS = 20
SHELL_MIN = 2
SHELL_MAX = 6
MAX_HP = 3
TACTICAL_ITEMS = ("magnifier", "saw", "handcuffs", "cigarettes")


def deal_round_items(players: list["BuckshotPlayer"], rng: random.Random | None = None) -> dict[int, str]:
    """Give each living player one random tactical item at the start of a round."""
    rng = rng or random
    return {p.user_id: rng.choice(TACTICAL_ITEMS) for p in players if p.alive}


@dataclass
class BuckshotPlayer:
    member: discord.Member
    hp: int = 3
    alive: bool = True
    handcuffed: bool = False
    used_items: set[str] | None = None
    inventory: set[str] | None = None

    def __post_init__(self) -> None:
        if self.used_items is None:
            self.used_items = set()
        if self.inventory is None:
            self.inventory = set()

    @property
    def user_id(self) -> int:
        return int(self.member.id)

    @property
    def name(self) -> str:
        return str(getattr(self.member, "display_name", self.member.name))


def load_shells(rng: random.Random | None = None) -> list[bool]:
    """Create a shuffled magazine; True is live, False is blank."""
    rng = rng or random
    total = rng.randint(SHELL_MIN, SHELL_MAX)
    live_count = rng.randint(1, total - 1)
    shells = [True] * live_count + [False] * (total - live_count)
    rng.shuffle(shells)
    return shells


def resolve_shot(shell_live: bool, *, target_self: bool, shooter_hp: int,
                 target_hp: int, damage: int = 1) -> tuple[int, int, bool]:
    """Resolve a shell and return (shooter HP, target HP, keep_turn)."""
    if not shell_live:
        return shooter_hp, target_hp, target_self
    damage = max(1, min(int(damage), 2))
    if target_self:
        shooter_hp = max(0, shooter_hp - damage)
    else:
        target_hp = max(0, target_hp - damage)
    return shooter_hp, target_hp, False


def apply_item(player: "BuckshotPlayer", item: str) -> bool:
    """Consume a single-use item. False means unavailable or ineffective."""
    inventory = player.inventory or set()
    if item not in inventory:
        return False
    if item == "cigarettes":
        if player.hp >= MAX_HP:
            return False
        player.hp = min(MAX_HP, player.hp + 1)
    inventory.remove(item)
    player.inventory = inventory
    return True


def render_buckshot_board(players: list[BuckshotPlayer], *, shells_left: int,
                          live_left: int, round_number: int, current_id: int | None,
                          last_event: str = "اختر قرارك التكتيكي") -> Image.Image:
    width, height = 1200, 760
    image = create_luxury_canvas(width, height, "الروليت الروسي التكتيكي | BUCKSHOT",
                                 f"ROUND {round_number} • لا تثق بالحظ وحده", accent="#ef4444").convert("RGBA")
    draw_radial_glow(image, (width // 2, 330), 280, color="#dc2626", max_alpha=60, blur=22)
    draw = ImageDraw.Draw(image)
    draw_glow_panel(image, (55, 165, 1145, 390), fill=(12, 16, 29, 230),
                    outline="#7f1d1d", radius=26, glow="#ef4444", width=2)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((430, 205, 770, 350), radius=24, fill="#171d2d", outline="#ef4444", width=3)
    draw_arabic_text(draw, (600, 238), "MAGAZINE", font=_font(22, True), fill=RED, anchor="mm")
    draw_arabic_text(draw, (600, 282), f"{shells_left} طلقات متبقية", font=_font(19, True), fill=WHITE, anchor="mm")
    draw_arabic_text(draw, (600, 317), f"تقدير الطلقات الحية: {live_left}", font=_font(15), fill="#fca5a5", anchor="mm")
    draw_arabic_text(draw, (600, 365), last_event[:68], font=_font(16, True), fill=CYAN, anchor="mm")
    alive = [p for p in players if p.alive]
    for index, player in enumerate(players):
        x = 55 + (index % 3) * 370
        y = 430 + (index // 3) * 145
        active = player.user_id == current_id and player.alive
        border = "#f87171" if active else (GREEN if player.alive else "#555b68")
        fill = (25, 28, 43, 245) if player.alive else (35, 37, 45, 230)
        draw.rounded_rectangle((x, y, x+345, y+125), radius=18, fill=fill, outline=border, width=3 if active else 2)
        name_color = WHITE if player.alive else "#9ca3af"
        draw_arabic_text(draw, (x+22, y+27), player.name[:24], font=_font(19, True), fill=name_color)
        hp_text = "♥ " * player.hp + "♡ " * (3-player.hp)
        draw_arabic_text(draw, (x+22, y+62), hp_text.strip(), font=_font(19, True), fill=RED if player.hp <= 1 else "#fb7185")
        state = "دورك الآن" if active else ("على قيد الحياة" if player.alive else "مقصى")
        if player.handcuffed and player.alive:
            state += " • مقيّد"
        if player.inventory and player.alive:
            state += " • أدوات: " + ",".join(player.inventory)
        draw_arabic_text(draw, (x+22, y+96), state, font=_font(14), fill=CYAN if active else MUTED)
    draw_arabic_text(draw, (600, 730), "PRIME • TACTICAL SURVIVAL", font=_font(13, True), fill=MUTED, anchor="mm")
    return image


class BuckshotTurnView(discord.ui.View):
    def __init__(self, game: "BuckshotGame", current_id: int) -> None:
        super().__init__(timeout=TURN_SECONDS)
        self.game = game
        self.current_id = current_id
        self.choice: tuple[str, int | None] | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.current_id:
            await interaction.response.send_message("هذا ليس دورك.", ephemeral=True)
            return False
        return True

    async def _choose(self, interaction: discord.Interaction, action: str,
                      target_id: int | None = None) -> None:
        if self.choice is not None:
            await interaction.response.send_message("تم تسجيل قرارك بالفعل.", ephemeral=True)
            return
        self.choice = (action, target_id)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="أطلق على نفسك", style=discord.ButtonStyle.danger, emoji="🎯", row=0)
    async def shoot_self(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._choose(interaction, "self")

    @discord.ui.button(label="أطلق على خصم", style=discord.ButtonStyle.primary, emoji="🔫", row=0)
    async def shoot_opponent(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        targets = [p for p in self.game.players if p.alive and p.user_id != self.current_id]
        if not targets:
            await self._choose(interaction, "self")
            return
        await self._choose(interaction, "opponent", random.choice(targets).user_id)

    @discord.ui.button(label="🔍 المنظار", style=discord.ButtonStyle.secondary, row=1)
    async def inspect_shell(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self.game.by_id[self.current_id]
        if "magnifier" not in (player.inventory or set()):
            await interaction.response.send_message("ليس لديك منظار في هذه الجولة.", ephemeral=True)
            return
        if not self.game.shells:
            await interaction.response.send_message("المخزن فارغ حاليًا.", ephemeral=True)
            return
        apply_item(player, "magnifier")
        await interaction.response.send_message(
            "الطلقة الحالية حية 🔴" if self.game.shells[0] else "الطلقة الحالية فارغة ⚪",
            ephemeral=True,
        )

    @discord.ui.button(label="🪚 تفعيل المنشار", style=discord.ButtonStyle.secondary, row=1)
    async def use_saw(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self.game.by_id[self.current_id]
        if not apply_item(player, "saw"):
            await interaction.response.send_message("لا تملك منشارًا متاحًا.", ephemeral=True)
            return
        self.game.saw_armed.add(self.current_id)
        await interaction.response.send_message("تم تفعيل المنشار؛ الطلقة الحية التالية تسبب 2 HP.", ephemeral=True)

    @discord.ui.button(label="⛓️ أصفاد الخصم", style=discord.ButtonStyle.secondary, row=1)
    async def use_handcuffs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self.game.by_id[self.current_id]
        if "handcuffs" not in (player.inventory or set()):
            await interaction.response.send_message("لا تملك أصفادًا متاحة.", ephemeral=True)
            return
        targets = [p for p in self.game.players if p.alive and p.user_id != self.current_id]
        if not targets:
            await interaction.response.send_message("لا يوجد خصم حي لاستهدافه.", ephemeral=True)
            return
        target = random.choice(targets)
        apply_item(player, "handcuffs")
        target.handcuffed = True
        await interaction.response.send_message(
            f"قُيّد الخصم {target.name} وسيتخطى دوره القادم.", ephemeral=True
        )

    @discord.ui.button(label="🚬 استعادة الصحة", style=discord.ButtonStyle.secondary, row=1)
    async def use_cigarettes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self.game.by_id[self.current_id]
        if player.hp >= MAX_HP:
            await interaction.response.send_message("صحتك كاملة بالفعل.", ephemeral=True)
            return
        if not apply_item(player, "cigarettes"):
            await interaction.response.send_message("لا تملك علاجًا متاحًا.", ephemeral=True)
            return
        await interaction.response.send_message(f"استعدت نقطة صحة: {player.hp}/{MAX_HP} ❤️", ephemeral=True)

    async def on_timeout(self) -> None:
        if self.choice is None:
            self.choice = ("opponent", None)
            self.stop()


class BuckshotGame:
    def __init__(self, cog: "Buckshot", members: list[discord.Member]) -> None:
        self.cog = cog
        self.players = [BuckshotPlayer(member=m) for m in members]
        self.by_id = {p.user_id: p for p in self.players}
        self.shells: list[bool] = []
        self.round_number = 1
        self.guild_id: int | None = None
        self.finished = False
        self.message: discord.Message | None = None
        self.last_event = "بدأت المواجهة. اختر قرارك بحذر."
        self.saw_armed: set[int] = set()

    def _render(self, current_id: int | None = None):
        if not self.shells:
            self.shells = load_shells()
        image = render_buckshot_board(self.players, shells_left=len(self.shells),
                                      live_left=sum(self.shells), round_number=self.round_number,
                                      current_id=current_id, last_event=self.last_event)
        return image_to_file(image, filename="buckshot.png")

    def _embed(self, text: str) -> discord.Embed:
        alive = sum(p.alive for p in self.players)
        return discord.Embed(title="🔴 Buckshot Roulette", description=f"{text}\nاللاعبون الباقون: **{alive}**",
                             color=discord.Color.dark_red())

    async def _update(self, text: str, current_id: int | None = None, view: discord.ui.View | None = None) -> None:
        self.last_event = text
        if self.message is None:
            return
        file = self._render(current_id)
        try:
            await self.message.edit(embed=self._embed(text), attachments=[], file=file, view=view)
        finally:
            file.close()

    async def run(self) -> None:
        try:
            current_index = 0
            # Distribute a random item to every player for the opening round.
            for user_id, item in deal_round_items(self.players).items():
                self.by_id[user_id].inventory.add(item)
            while sum(p.alive for p in self.players) > 1:
                if not self.shells:
                    self.shells = load_shells()
                alive_players = [p for p in self.players if p.alive]
                if len(alive_players) <= 1:
                    break
                current_index %= len(self.players)
                player = self.players[current_index]
                if not player.alive:
                    current_index += 1
                    continue
                if player.handcuffed:
                    player.handcuffed = False
                    self.last_event = f"{player.name} تخطّى دوره بسبب الأصفاد."
                    await self._update(self.last_event)
                    current_index += 1
                    continue

                view = BuckshotTurnView(self, player.user_id)
                await self._update(f"دور {player.name}: أمامك {TURN_SECONDS} ثانية.", player.user_id, view)
                await view.wait()
                action, target_id = view.choice or ("opponent", None)
                if action == "self":
                    target = player
                else:
                    target = self.by_id.get(target_id) if target_id else None
                    if target is None or not target.alive or target.user_id == player.user_id:
                        candidates = [p for p in self.players if p.alive and p.user_id != player.user_id]
                        target = random.choice(candidates) if candidates else player

                shell = self.shells.pop(0)
                damage = 2 if player.user_id in self.saw_armed else 1
                self.saw_armed.discard(player.user_id)
                shooter_hp, target_hp, keep_turn = resolve_shot(
                    shell, target_self=(target.user_id == player.user_id),
                    shooter_hp=player.hp, target_hp=target.hp, damage=damage,
                )
                if target.user_id == player.user_id:
                    player.hp = shooter_hp
                else:
                    target.hp = target_hp
                if shell and target.hp <= 0:
                    target.alive = False
                self.last_event = (
                    f"{player.name}: {'طلقة حية 🔴' if shell else 'طلقة فارغة ⚪'}"
                    f"{' — ضرر ' + str(damage) if shell else ''}."
                )
                await self._update(self.last_event)
                await asyncio.sleep(1.0)
                if sum(p.alive for p in self.players) <= 1:
                    break
                # Blank self-shot retains the turn; a live shot passes it.
                if not keep_turn:
                    current_index = (self.players.index(player) + 1) % len(self.players)
                else:
                    current_index = self.players.index(player)
                if not self.shells:
                    self.shells = load_shells()
                    self.round_number += 1
                    self.saw_armed.clear()
                    for p in self.players:
                        p.inventory = set()
                        p.handcuffed = False
                    for user_id, item in deal_round_items(self.players).items():
                        self.by_id[user_id].inventory.add(item)
                    await self._update(f"بدأت الجولة {self.round_number}: وُزّعت أدوات عشوائية.")

            winner = next((p for p in self.players if p.alive), None)
            self.finished = True
            await self._update(f"🏆 الفائز: {winner.name}" if winner else "انتهت اللعبة دون فائز.")
            if self.message:
                await self.message.edit(view=None)
        except asyncio.CancelledError:
            self.finished = True
            raise
        except Exception:
            logger.exception("Buckshot game failed")
            self.finished = True
            if self.message:
                await self.message.edit(view=None)
        finally:
            await self.cog._release_game(self)



class Buckshot(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_games: dict[int, BuckshotGame] = {}
        self._game_lock = asyncio.Lock()

    async def _release_game(self, game: BuckshotGame) -> None:
        if game.guild_id is not None:
            async with self._game_lock:
                if self.active_games.get(game.guild_id) is game:
                    self.active_games.pop(game.guild_id, None)

    async def cog_unload(self) -> None:
        games = list(self.active_games.values())
        for game in games:
            game.finished = True
        async with self._game_lock:
            self.active_games.clear()

    @app_commands.command(name="buckshot", description="ابدأ الروليت الروسي التكتيكي")
    @app_commands.guild_only()
    async def buckshot(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("هذه اللعبة متاحة داخل السيرفر فقط.", ephemeral=True)
            return

        async def on_start(start_interaction: discord.Interaction, members: list[discord.Member]) -> None:
            guild_id = int(start_interaction.guild_id or 0)
            async with self._game_lock:
                existing = self.active_games.get(guild_id)
                if existing and not existing.finished:
                    await start_interaction.followup.send("هناك مباراة Buckshot نشطة في هذا السيرفر.", ephemeral=True)
                    return
                game = BuckshotGame(self, members)
                game.guild_id = guild_id
                self.active_games[guild_id] = game
            file = game._render()
            try:
                game.message = await start_interaction.followup.send(
                    "🔴 بدأت المواجهة التكتيكية!", embed=game._embed("استعدوا."),
                    file=file, wait=True,
                )
            finally:
                file.close()
            await game.run()

        lobby = UniversalLobbyView(
            interaction.user, game_name="Buckshot Roulette",
            rules=(
                "1) من 2 إلى 6 لاعبين، ولكل لاعب 3 نقاط صحة.\n"
                "2) الطلقات الحية تسبب ضررًا واحدًا؛ الفارغة لا تسبب ضررًا.\n"
                "3) اختر إطلاق النار على نفسك أو على خصم عشوائي.\n"
                "4) المنظار يكشف الطلقة التالية مرة واحدة لكل لاعب.\n"
                "5) آخر لاعب يبقى حيًا يفوز."
            ),
            on_start=on_start, min_players=MIN_PLAYERS, max_players=MAX_PLAYERS,
            timeout=300, avatar_session=getattr(self.bot, "session", None),
        )
        await interaction.response.send_message(embed=lobby.lobby_embed(),
                                                file=await lobby.render_lobby_file(), view=lobby)
        await lobby.bind_message(await interaction.original_response())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Buckshot(bot))
