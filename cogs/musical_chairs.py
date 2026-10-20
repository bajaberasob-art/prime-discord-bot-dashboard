"""Visual multiplayer Musical Chairs game for PRIME Discord servers."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Sequence

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw

from game_engine import (
    CYAN,
    GREEN,
    MUTED,
    PANEL_EDGE,
    PURPLE,
    RED,
    WHITE,
    _font,
    draw_arabic_text,
    get_circular_avatar,
    UniversalLobbyView,
    create_base_canvas,
    image_to_file,
)

logger = logging.getLogger("PrimeMusicalChairs")

MIN_PLAYERS = 3
MAX_PLAYERS = 10
MUSIC_MIN_SECONDS = 4.0
MUSIC_MAX_SECONDS = 8.0
SEATING_SECONDS = 9.0
RESULT_PAUSE_SECONDS = 2.5


def resolve_round(
    alive_ids: Sequence[int],
    seated_ids: Sequence[int],
) -> tuple[list[int], list[int]]:
    """Resolve one round; the first N-1 distinct living players to sit survive.

    The order of seated_ids is significant because only len(alive_ids) - 1
    chairs exist. Unknown IDs, eliminated players, and repeated clicks are ignored.
    """
    alive = list(dict.fromkeys(int(user_id) for user_id in alive_ids))
    if len(alive) <= 1:
        return alive, []

    alive_set = set(alive)
    seated: list[int] = []
    seen: set[int] = set()
    for raw_id in seated_ids:
        user_id = int(raw_id)
        if user_id in alive_set and user_id not in seen:
            seated.append(user_id)
            seen.add(user_id)
        if len(seated) >= len(alive) - 1:
            break

    survivors = seated
    eliminated = [user_id for user_id in alive if user_id not in set(survivors)]
    return survivors, eliminated


def render_musical_chairs_image(
    players: Sequence[object],
    *,
    alive_ids: Sequence[int],
    seated_ids: Sequence[int] = (),
    eliminated_ids: Sequence[int] = (),
    round_number: int = 1,
    phase: str = "music",
    chair_count: int = 0,
    winner_id: int | None = None,
    avatars: dict[int, Image.Image] | None = None,
    chair_assignments: dict[int, int] | None = None,
) -> Image.Image:
    """Render the game board, green chair grid, player states and winner."""
    player_list = list(players)
    alive = {int(value) for value in alive_ids}
    seated = {int(value) for value in seated_ids}
    eliminated = {int(value) for value in eliminated_ids}
    assignments = chair_assignments or {}
    columns = 4
    rows = max(1, (len(player_list) + columns - 1) // columns)
    width = 1200
    height = max(560, 385 + rows * 126)
    if phase == "music":
        subtitle = f"ROUND {round_number} • الموسيقى تعمل 🎵"
    elif phase == "seating":
        subtitle = f"ROUND {round_number} • توقفت الموسيقى! اجلس بسرعة"
    elif phase == "result":
        subtitle = f"ROUND {round_number} • نتيجة الجولة"
    else:
        subtitle = "🏆 انتهت اللعبة"
    image = create_base_canvas(width, height, "الكراسي الموسيقية", subtitle).convert("RGBA")
    draw = ImageDraw.Draw(image)
    draw_arabic_text(
        draw,
        (width - 54, 143),
        f"اللاعبون الباقون: {len(alive)}   •   الكراسي: {chair_count}",
        font=_font(20, True),
        fill=CYAN,
        anchor="ra",
    )

    # A visible chair grid complements the individual Discord chair buttons.
    names = {
        int(getattr(player, "id", 0)): str(
            getattr(player, "display_name", getattr(player, "name", f"Player {getattr(player, 'id', 0)}"))
        )
        for player in player_list
    }
    for index in range(max(0, chair_count)):
        column, row = index % 5, index // 5
        x, y = 92 + column * 210, 184 + row * 78
        occupant = assignments.get(index)
        occupied = occupant is not None
        fill = "#164e35" if not occupied else "#173b50"
        outline = CYAN if occupied else GREEN
        draw.rounded_rectangle((x, y, x + 174, y + 61), radius=12, fill=fill, outline=outline, width=3)
        draw.rounded_rectangle((x + 12, y + 10, x + 162, y + 34), radius=7, fill="#14532d" if not occupied else "#155e75")
        draw.line((x + 22, y + 40, x + 22, y + 53), fill=outline, width=4)
        draw.line((x + 152, y + 40, x + 152, y + 53), fill=outline, width=4)
        label = f"كرسي {index + 1}"
        if occupied:
            owner = names.get(int(occupant), str(occupant))[:15]
            label = f"{label} • {owner}"
        draw_arabic_text(draw, (x + 87, y + 46), label, font=_font(12, True), fill=WHITE, anchor="mm")

    avatars = avatars or {}
    for index, player in enumerate(player_list):
        user_id = int(getattr(player, "id", 0))
        name = str(getattr(player, "display_name", getattr(player, "name", f"Player {user_id}")))
        column, row = index % columns, index // columns
        x, y = 54 + column * 278, 350 + row * 126
        is_alive = user_id in alive
        if user_id == winner_id:
            outline, status, status_color = GREEN, "الفائز 🏆", GREEN
        elif user_id in eliminated:
            outline, status, status_color = RED, "خرج من اللعبة", RED
        elif user_id in seated and phase in {"seating", "result"}:
            outline, status, status_color = CYAN, "حجز كرسيًا 🪑", CYAN
        elif not is_alive:
            outline, status, status_color = RED, "خرج من اللعبة", RED
        else:
            outline = PURPLE if is_alive else PANEL_EDGE
            status = "جاهز للجلوس" if phase == "seating" else "في المنافسة"
            status_color = MUTED
        draw.rounded_rectangle(
            (x, y, x + 260, y + 106),
            radius=16,
            fill="#171d2d",
            outline=outline,
            width=3 if user_id == winner_id else 2,
        )
        avatar = avatars.get(user_id)
        if avatar is not None:
            image.alpha_composite(avatar.convert("RGBA"), (x + 14, y + 20))
        else:
            draw.ellipse((x + 14, y + 20, x + 76, y + 82), fill="#29233f", outline=outline, width=2)
            initial = (name.strip()[:1] or "?").upper()
            draw_arabic_text(draw, (x + 45, y + 51), initial, font=_font(24, True), fill=WHITE, anchor="mm")
        draw_arabic_text(draw, (x + 88, y + 27), name[:20], font=_font(17, True), fill=WHITE)
        draw_arabic_text(draw, (x + 88, y + 61), status, font=_font(14), fill=status_color)
    draw_arabic_text(draw, (width // 2, height - 25), "PRIME • VISUAL MULTIPLAYER", font=_font(13, True), fill=MUTED, anchor="mm")
    return image


def eliminate_early_press(alive_ids: Sequence[int], user_id: int) -> tuple[list[int], bool]:
    """Return the living players after a premature click and whether it eliminated them."""
    alive = list(dict.fromkeys(int(value) for value in alive_ids))
    actor = int(user_id)
    if actor not in alive:
        return alive, False
    return [value for value in alive if value != actor], True


class MusicalChairsGameView(discord.ui.View):
    """Timed, round-based game whose board and image are edited in place."""

    def __init__(
        self,
        cog: "MusicalChairs",
        players: Sequence[discord.Member],
        *,
        music_min_seconds: float = MUSIC_MIN_SECONDS,
        music_max_seconds: float = MUSIC_MAX_SECONDS,
        seating_seconds: float = SEATING_SECONDS,
        result_pause_seconds: float = RESULT_PAUSE_SECONDS,
    ) -> None:
        super().__init__(timeout=None)
        if len(players) < MIN_PLAYERS or len(players) > MAX_PLAYERS:
            raise ValueError("invalid_player_count")
        ids = [int(player.id) for player in players]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_players")
        self.cog = cog
        self.players: dict[int, discord.Member] = {int(player.id): player for player in players}
        self.alive_ids = list(ids)
        self.eliminated_ids: list[int] = []
        self.seated_ids: list[int] = []
        self.round_number = 1
        self.chair_count = len(ids) - 1
        self.chair_assignments: dict[int, int] = {}
        self.chair_buttons: dict[int, discord.ui.Button] = {}
        self.phase = "music"
        self.closed = False
        self.winner_id: int | None = None
        self.message: discord.Message | None = None
        self.music_min_seconds = max(0.0, float(music_min_seconds))
        self.music_max_seconds = max(self.music_min_seconds, float(music_max_seconds))
        self.seating_seconds = max(0.1, float(seating_seconds))
        self.result_pause_seconds = max(0.0, float(result_pause_seconds))
        self._lock = asyncio.Lock()
        self._seat_event = asyncio.Event()
        self._runner: asyncio.Task | None = None
        self._avatar_cache: dict[int, Image.Image] = {}
        self._last_round_eliminated: list[int] = []
        self._rebuild_chair_buttons()
        self._update_controls()

    def _rebuild_chair_buttons(self) -> None:
        for button in self.chair_buttons.values():
            self.remove_item(button)
        self.chair_buttons.clear()
        order = list(range(max(0, self.chair_count)))
        random.shuffle(order)
        for display_index, chair_index in enumerate(order):
            button = discord.ui.Button(
                label=f"🪑 كرسي {chair_index + 1}",
                style=discord.ButtonStyle.success,
                row=display_index // 5,
                custom_id=f"prime:mc:{id(self):x}:{self.round_number}:{chair_index}",
            )
            button.callback = self._make_chair_callback(chair_index)
            self.chair_buttons[chair_index] = button
            self.add_item(button)

    def _make_chair_callback(self, chair_index: int):
        async def callback(interaction: discord.Interaction) -> None:
            user_id = int(interaction.user.id)
            early_elimination = False
            async with self._lock:
                if self.closed or self.phase in {"resolving", "result", "finished"}:
                    return await interaction.response.send_message("انتهت هذه الجولة؛ انتظر الجولة التالية.", ephemeral=True)
                if user_id not in self.alive_ids:
                    return await interaction.response.send_message("لم تعد ضمن اللاعبين المتنافسين.", ephemeral=True)
                if self.phase == "music":
                    self.alive_ids, early_elimination = eliminate_early_press(self.alive_ids, user_id)
                    if early_elimination:
                        self.eliminated_ids.append(user_id)
                        self._last_round_eliminated.append(user_id)
                        self.chair_count = max(0, len(self.alive_ids) - 1)
                        self._rebuild_chair_buttons()
                        self._update_controls()
                elif self.phase == "seating":
                    if user_id in self.seated_ids:
                        return await interaction.response.send_message("🪑 حجزت كرسيًا بالفعل؛ لا يمكنك حجز كرسي ثانٍ.", ephemeral=True)
                    if chair_index in self.chair_assignments:
                        return await interaction.response.send_message("هذا الكرسي حُجز للتو؛ اختر كرسيًا أخضر آخر.", ephemeral=True)
                    self.chair_assignments[chair_index] = user_id
                    self.seated_ids.append(user_id)
                    if len(self.seated_ids) >= self.chair_count:
                        self.phase = "resolving"
                        self._seat_event.set()
                    self._update_controls()
                else:
                    return await interaction.response.send_message("انتظر إشارة الجولة التالية.", ephemeral=True)

            if early_elimination:
                await interaction.response.send_message(
                    "🚫 ضغطت أثناء الموسيقى! خرجت من هذه اللعبة.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message("🪑 تم حجز الكرسي لك!", ephemeral=True)
            await self._edit_board()

        return callback

    def _update_controls(self) -> None:
        for chair_index, button in self.chair_buttons.items():
            occupant = self.chair_assignments.get(chair_index)
            button.disabled = (
                self.closed
                or self.phase not in {"music", "seating"}
                or (self.phase == "seating" and occupant is not None)
            )
            if occupant is not None:
                player = self.players.get(int(occupant))
                button.label = f"🔒 {str(getattr(player, 'display_name', 'محجوز'))[:55]}"
                button.style = discord.ButtonStyle.secondary
            else:
                button.label = f"🪑 كرسي {chair_index + 1}"
                button.style = discord.ButtonStyle.success

    async def start(self, message: discord.Message) -> None:
        self.message = message
        await self._load_avatars()
        await self._edit_board()
        self._runner = asyncio.create_task(
            self._run_game(),
            name=f"prime-musical-chairs-{getattr(message, 'id', 'pending')}",
        )

    async def _load_avatars(self) -> None:
        session = getattr(self.cog.bot, "session", None)
        if session is not None and getattr(session, "closed", False):
            session = None

        async def load(player: discord.Member) -> tuple[int, Image.Image]:
            avatar = await get_circular_avatar(
                player,
                62,
                session=session,
                timeout=3.5,
                border_color=CYAN if int(player.id) == int(self.players[next(iter(self.players))].id) else PURPLE,
            )
            return int(player.id), avatar

        results = await asyncio.gather(
            *(load(player) for player in self.players.values()),
            return_exceptions=True,
        )
        for player, result in zip(self.players.values(), results):
            if isinstance(result, Exception):
                logger.debug("Avatar unavailable for musical chairs player %s", player.id, exc_info=result)
                continue
            self._avatar_cache[result[0]] = result[1]

    def _render_file(self) -> discord.File:
        image = render_musical_chairs_image(
            list(self.players.values()),
            alive_ids=self.alive_ids,
            seated_ids=self.seated_ids,
            eliminated_ids=self._last_round_eliminated,
            round_number=self.round_number,
            phase=self.phase,
            chair_count=self.chair_count,
            winner_id=self.winner_id,
            avatars=self._avatar_cache,
            chair_assignments=self.chair_assignments,
        )
        return image_to_file(image, "prime-musical-chairs.png")

    def _embed(self) -> discord.Embed:
        if self.phase == "music":
            title = f"🎵 الجولة {self.round_number}: الموسيقى تعمل"
            description = "انتظر توقف الموسيقى. لا يمكن الجلوس قبل ظهور إشارة التوقف."
            color = 0xA855F7
        elif self.phase == "seating":
            title = f"🪑 الجولة {self.round_number}: الكراسي متاحة!"
            description = (
                f"اضغط زر الجلوس بسرعة. يوجد **{self.chair_count}** كرسيًا و"
                f"**{len(self.alive_ids)}** لاعبًا. أول من يجلس يحتفظ بمكانه."
            )
            color = 0x36E0D0
        elif self.phase in {"resolving", "result"}:
            title = f"📣 نتيجة الجولة {self.round_number}"
            out_mentions = [f"<@{user_id}>" for user_id in self._last_round_eliminated]
            description = (
                "خرج من المنافسة: " + ", ".join(out_mentions)
                if out_mentions else "لم يخرج أي لاعب في هذه الجولة."
            )
            color = 0xFF5577 if out_mentions else 0x35E68B
        elif self.phase == "finished":
            title = "🏆 انتهت الكراسي الموسيقية!"
            description = f"الفائز هو <@{self.winner_id}>! مبروك الفوز."
            color = 0x35E68B
        else:
            title = "🎮 الكراسي الموسيقية"
            description = "جاري تجهيز الجولة."
            color = 0x64748B

        embed = discord.Embed(title=title, description=description, color=color)
        alive_mentions = ", ".join(f"<@{user_id}>" for user_id in self.alive_ids)
        if alive_mentions and self.phase != "finished":
            embed.add_field(name="المتنافسون", value=alive_mentions[:1024], inline=False)
        embed.set_footer(text=f"PRIME • الجولة {self.round_number} • Visual Musical Chairs")
        return embed

    async def _edit_board(self) -> None:
        if self.message is None:
            return
        self._update_controls()
        file = self._render_file()
        try:
            await self.message.edit(
                content=None,
                embed=self._embed(),
                attachments=[file],
                view=self,
            )
        except discord.HTTPException:
            logger.warning("Could not update musical chairs board", exc_info=True)
            try:
                await self.message.edit(embed=self._embed(), view=self)
            except discord.HTTPException:
                logger.error("Could not update musical chairs embed fallback", exc_info=True)
        finally:
            file.close()

    async def _run_game(self) -> None:
        try:
            while len(self.alive_ids) > 1:
                async with self._lock:
                    self.phase = "music"
                    self.seated_ids = []
                    self.chair_assignments = {}
                    self.chair_count = len(self.alive_ids) - 1
                    self._seat_event = asyncio.Event()
                    self._last_round_eliminated = []
                    self._rebuild_chair_buttons()
                    self._update_controls()
                await self._edit_board()
                await asyncio.sleep(random.uniform(self.music_min_seconds, self.music_max_seconds))

                async with self._lock:
                    if self.closed:
                        return
                    if len(self.alive_ids) <= 1:
                        break
                    self.phase = "seating"
                    self._update_controls()
                await self._edit_board()

                try:
                    await asyncio.wait_for(self._seat_event.wait(), timeout=self.seating_seconds)
                except asyncio.TimeoutError:
                    pass

                async with self._lock:
                    if self.closed:
                        return
                    # If nobody reacts, simulate a fair scramble: fill all but one chair.
                    if not self.seated_ids and self.alive_ids:
                        self.seated_ids = random.sample(self.alive_ids, min(self.chair_count, len(self.alive_ids)))
                        self.chair_assignments = {index: user_id for index, user_id in enumerate(self.seated_ids)}
                    survivors, eliminated = resolve_round(self.alive_ids, self.seated_ids)
                    if not survivors and self.alive_ids:
                        survivors = random.sample(self.alive_ids, min(self.chair_count, len(self.alive_ids))) or [random.choice(self.alive_ids)]
                        eliminated = [user_id for user_id in self.alive_ids if user_id not in survivors]
                    self.alive_ids = survivors
                    self.eliminated_ids.extend(user_id for user_id in eliminated if user_id not in self.eliminated_ids)
                    self._last_round_eliminated.extend(user_id for user_id in eliminated if user_id not in self._last_round_eliminated)
                    self.phase = "result"
                    # Keep the current round number and chair count for the result
                    # board. Advance only after showing this round's outcome.
                    self._update_controls()
                await self._edit_board()
                if len(self.alive_ids) > 1 and self.result_pause_seconds:
                    await asyncio.sleep(self.result_pause_seconds)
                if len(self.alive_ids) > 1:
                    self.round_number += 1

            async with self._lock:
                self.winner_id = self.alive_ids[0] if self.alive_ids else None
                self.phase = "finished"
                self.closed = True
                self._update_controls()
            await self._edit_board()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Musical chairs game crashed")
            async with self._lock:
                self.closed = True
                self.phase = "finished"
                self._update_controls()
            await self._edit_board()
        finally:
            self.stop()
            release = getattr(self.cog, "_release_game", None)
            if release is not None:
                await release(self)

    async def on_timeout(self) -> None:
        async with self._lock:
            self.closed = True
            self.phase = "finished"
            self._update_controls()
        if self._runner and not self._runner.done():
            self._runner.cancel()
        await self._edit_board()
        release = getattr(self.cog, "_release_game", None)
        if release is not None and (self._runner is None or self._runner.done()):
            await release(self)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[object],
    ) -> None:
        logger.exception("Musical chairs interaction failed", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "تعذر تنفيذ الحركة. حاول مرة أخرى.", ephemeral=True
            )


class MusicalChairs(commands.Cog):
    """Slash-command entry point for the visual multiplayer game."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_games: dict[int, MusicalChairsGameView] = {}
        self._game_lock = asyncio.Lock()

    async def _release_game(self, view: MusicalChairsGameView) -> None:
        guild_id = getattr(getattr(view.message, "guild", None), "id", None)
        if guild_id is None:
            guild_id = getattr(view, "guild_id", None)
        if guild_id is None:
            return
        async with self._game_lock:
            if self.active_games.get(int(guild_id)) is view:
                self.active_games.pop(int(guild_id), None)

    async def cog_unload(self) -> None:
        views = list(self.active_games.values())
        for view in views:
            view.closed = True
            view.stop()
            if view._runner and not view._runner.done():
                view._runner.cancel()
        tasks = [view._runner for view in views if view._runner is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._game_lock:
            self.active_games.clear()

    @app_commands.command(
        name="musical_chairs",
        description="ابدأ لعبة الكراسي الموسيقية البصرية متعددة اللاعبين",
    )
    @app_commands.guild_only()
    async def musical_chairs(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message(
                "هذه اللعبة متاحة داخل السيرفر فقط.", ephemeral=True
            )
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "تعذر تحديد حسابك داخل السيرفر.", ephemeral=True
            )

        async def on_start(
            start_interaction: discord.Interaction,
            players: list[discord.Member],
        ) -> None:
            guild_id = int(start_interaction.guild_id or interaction.guild.id)
            async with self._game_lock:
                existing = self.active_games.get(guild_id)
                if existing is not None and not existing.closed:
                    await start_interaction.followup.send(
                        "هناك مباراة كراسي موسيقية نشطة في هذا السيرفر. انتظر انتهائها قبل بدء أخرى.",
                        ephemeral=True,
                    )
                    return
                view = MusicalChairsGameView(self, players)
                view.guild_id = guild_id
                self.active_games[guild_id] = view
            game_message = None
            try:
                game_file = view._render_file()
                try:
                    game_message = await start_interaction.followup.send(
                        "🎵 بدأت الكراسي الموسيقية! راقب اللوحة وانتظر توقف الموسيقى.",
                        embed=view._embed(),
                        file=game_file,
                        view=view,
                        wait=True,
                    )
                finally:
                    game_file.close()
                await view.start(game_message)
            except Exception:
                if game_message is not None:
                    try:
                        await game_message.edit(view=None)
                    except (discord.Forbidden, discord.HTTPException):
                        logger.warning("Could not disable incomplete musical chairs board", exc_info=True)
                await self._release_game(view)
                raise

        lobby = UniversalLobbyView(
            interaction.user,
            game_name="الكراسي الموسيقية",
            rules=(
                "1) تحتاج اللعبة إلى 3–10 لاعبين.\n"
                "2) الضغط على أي كرسي أثناء الموسيقى يخرجك من اللعبة.\n"
                "3) عند توقف الموسيقى، احجز كرسيًا أخضر بسرعة لتبقى في المنافسة.\n"
                "4) يوجد كرسي أقل من عدد المتنافسين في كل جولة.\n"
                "5) آخر لاعب يبقى هو الفائز."
            ),
            on_start=on_start,
            min_players=MIN_PLAYERS,
            max_players=MAX_PLAYERS,
            timeout=300,
            avatar_session=getattr(self.bot, "session", None),
        )
        await interaction.response.send_message(
            embed=lobby.lobby_embed(),
            file=await lobby.render_lobby_file(),
            view=lobby,
        )
        lobby_message = await interaction.original_response()
        await lobby.bind_message(lobby_message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicalChairs(bot))
