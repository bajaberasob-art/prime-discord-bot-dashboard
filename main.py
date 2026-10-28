import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
from collections import deque

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import (
    checkpoint_wal,
    init_db,
    record_analytics_events,
    record_analytics_voice_session,
    wal_checkpoint_loop,
)
from profile_store_db import ensure_profile_store_schema
from cogs.utilities import dynamic_prefix
import prime_ai_intelligence
import prime_ai_persistence
import runtime_diagnostics
from interaction_runtime import (
    install_ui_guards,
    send_interaction_message,
    wrap_application_command,
)
from routed_command_tree import RoutedCommandTree, SLASH_COMMAND_GROUPS
from web_server import start_web_server


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("CoreRunner")

TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip() or (
    os.getenv("DISCORD_BOT_TOKEN") or ""
).strip()
if not TOKEN:
    logger.critical(
        "⚠️ مفتاح DISCORD_TOKEN أو DISCORD_BOT_TOKEN مفقود "
        "تماماً داخل Replit Secrets!"
    )
    sys.exit(1)


def configured_port_text() -> str:
    """Return a whitespace-free port value for logs and presence text."""
    return (
        (os.getenv("PORT") or "").strip()
        or (os.getenv("DASHBOARD_PORT") or "").strip()
        or "8080"
    )


intents = discord.Intents.all()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.emojis_and_stickers = True


def configured_sync_guild() -> discord.Object | None:
    """Return the optional guild used for fast slash-command synchronization."""
    guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not guild_id:
        return None

    try:
        return discord.Object(id=int(guild_id))
    except ValueError:
        logger.warning(
            "DISCORD_GUILD_ID is not numeric; falling back to global command sync."
        )
        return None


class EnterpriseBot(commands.Bot):
    def __init__(self):
        install_ui_guards()
        super().__init__(
            command_prefix=dynamic_prefix,
            intents=intents,
            help_command=None,
            max_messages=1000,
            chunk_guilds_at_startup=True,
        )
        self.tree.__class__ = RoutedCommandTree
        self.tree._slash_groups = {}
        self.session: aiohttp.ClientSession | None = None
        self.dashboard_runner = None
        self.presence_step = 0
        self.sync_guild = configured_sync_guild()
        self.metrics: deque[dict[str, int | float | str | None]] = deque(maxlen=600)
        self._analytics_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10000)
        self._analytics_writer_task: asyncio.Task | None = None
        self._voice_sessions: dict[tuple[int, int], tuple[int, float]] = {}
        self._gateway_watchdog_task: asyncio.Task | None = None
        self._diagnostics_task: asyncio.Task | None = None
        self._wal_checkpoint_task: asyncio.Task | None = None
        self._is_initialized = False
        self._retry_delay = 5
        self.started_at = time.monotonic()
        self._active_cog_name: str | None = None

    async def add_cog(self, cog, /, *, override=False, guild=None, guilds=None):
        options = {"override": override}
        if guild is not None:
            options["guild"] = guild
        if guilds is not None:
            options["guilds"] = guilds

        cog_name = cog.__class__.__name__
        # A failed Cog injection can leave routed child commands behind because
        # they are stored inside a Group, not at the tree root. Purge stale
        # children before retrying a Cog that is not currently registered.
        if cog_name not in self.cogs:
            self.tree.remove_cog_grouped_commands(cog_name)

        self._active_cog_name = cog_name
        try:
            result = await super().add_cog(cog, **options)
        finally:
            self._active_cog_name = None
        self.install_interaction_guards()
        return result

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        """Keep listener exceptions from disappearing inside discord.py."""
        logger.error(
            "[EVENT] listener failed: %s args=%s",
            event_method,
            len(args),
            exc_info=True,
        )

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Log received application commands separately from gateway readiness."""
        if interaction.type is not discord.InteractionType.application_command:
            return
        command = getattr(interaction, "command", None)
        data = getattr(interaction, "data", None) or {}
        command_name = getattr(command, "qualified_name", None) or str(data.get("name") or "unknown")
        logger.info(
            "[INTERACTION] gateway_received command=%s guild=%s",
            command_name,
            getattr(interaction, "guild_id", None),
        )

    def install_interaction_guards(self) -> int:
        wrapped = 0
        for command in self.tree.walk_commands():
            if isinstance(command, app_commands.Command):
                wrapped += int(wrap_application_command(command))
        if wrapped:
            logger.info("🛡️ تم تأمين %d أمر Slash بطبقة ACK والأخطاء الموحدة.", wrapped)
        return wrapped

    async def _cleanup_failed_setup(self, loaded_modules: list[str] | None = None) -> None:
        """Release resources when one-time startup fails partway through."""
        for module in reversed(loaded_modules or []):
            with contextlib.suppress(Exception):
                await self.unload_extension(module)
        for attribute in (
            "_gateway_watchdog_task",
            "_diagnostics_task",
            "_wal_checkpoint_task",
            "_analytics_writer_task",
        ):
            task = getattr(self, attribute, None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                setattr(self, attribute, None)
        if self.dashboard_runner:
            with contextlib.suppress(Exception):
                await self.dashboard_runner.cleanup()
            self.dashboard_runner = None
        if self.session and not self.session.closed:
            with contextlib.suppress(Exception):
                await self.session.close()
        self.session = None
        with contextlib.suppress(Exception):
            await prime_ai_persistence.close_durable_store()

    async def _reset_http_session_for_retry(self) -> None:
        """Release both HTTP clients before retrying without re-running setup."""
        old_session = self.session
        self.session = None
        if old_session and not old_session.closed:
            with contextlib.suppress(Exception):
                await old_session.close()

        # discord.py creates its own HTTP session during static_login(), before
        # setup_hook() loads extensions. If setup_hook fails, retrying without
        # closing this separate session leaks one ClientSession per attempt.
        http_client = getattr(self, "http", None)
        if http_client is not None:
            with contextlib.suppress(Exception):
                await http_client.close()
            with contextlib.suppress(Exception):
                http_client.clear()
            # The old session owns the connector. Force discord.py to create a
            # fresh connector alongside the new session on the next login.
            with contextlib.suppress(Exception):
                http_client.connector = discord.utils.MISSING

        if self._is_initialized and not self.is_closed():
            self.session = aiohttp.ClientSession()

    async def setup_hook(self):
        if self._is_initialized:
            return

        self.session = aiohttp.ClientSession()
        self._diagnostics_task = asyncio.create_task(
            runtime_diagnostics.monitor_loop(), name="prime-runtime-diagnostics"
        )
        self._gateway_watchdog_task = asyncio.create_task(
            self._gateway_startup_watchdog()
        )

        try:
            await init_db()
            await ensure_profile_store_schema()
            from prime_games.engine import ensure_game_schema
            await ensure_game_schema()
            await prime_ai_intelligence.ensure_schema()
            durable_enabled = await prime_ai_persistence.start_durable_store()
            if durable_enabled:
                restored = await prime_ai_persistence.restore_from_durable_store()
                logger.info(
                    "♻️ تمت استعادة ذاكرة PRIME AI الدائمة: %s سجل ذاكرة و%s ملف مستخدم.",
                    restored["memories"],
                    restored["profiles"],
                )
            logger.info("📦 تم التحقق من سلامة قاعدة البيانات بنجاح.")
            self._wal_checkpoint_task = asyncio.create_task(wal_checkpoint_loop())
            self._analytics_writer_task = asyncio.create_task(
                self._analytics_writer()
            )
        except Exception as error:
            await self._cleanup_failed_setup()
            raise RuntimeError("قاعدة البيانات غير جاهزة؛ أوقف الإقلاع لحماية البيانات.") from error

        try:
            self.dashboard_runner = await start_web_server(self)
            logger.info(
                "🌐 لوحة التحكم (Web Dashboard) نشطة على المنفذ %s.",
                configured_port_text(),
            )
        except Exception as error:
            await self._cleanup_failed_setup()
            raise RuntimeError("تعذر تشغيل لوحة التحكم؛ أوقف الإقلاع بدلاً من تشغيل نسخة ناقصة.") from error

        modules = [
            # Load protection before the remaining feature cogs so the security
            # listeners are registered as soon as the bot connects.
            "cogs.security",
            "cogs.analytics",
            "cogs.moderation",
            "cogs.sanctions_voice",
            "cogs.chat_jail",
            "cogs.admin_advanced",
            "cogs.tools_channels",
            "cogs.engagement",
            "cogs.economy",
            "cogs.levels",
            "cogs.rank_commands",
            "cogs.profile",
            "cogs.subscription_commands",
            "cogs.utilities",
            "cogs.temp_voice",
            "cogs.tournaments",
            "cogs.gaming",
            "cogs.games",
            "cogs.buckshot",
            "cogs.bank_heist",
            "cogs.blind_vaults",
            "cogs.connect4_power",
            "cogs.mafia",
            "cogs.musical_chairs",
            "cogs.uno_visual",
            "cogs.roulette_royale",
            "cogs.community",
            "cogs.announcement_reactions",
            "cogs.interactive_info_panel",
            "cogs.ai_tools",
        ]

        loaded_modules: list[str] = []
        for module in modules:
            try:
                await self.load_extension(module)
                loaded_modules.append(module)
                logger.info(f"✅ تم تحميل الوحدة بنجاح: {module}")
            except commands.ExtensionAlreadyLoaded:
                logger.debug("الوحدة محملة مسبقاً: %s", module)
            except Exception as error:
                await self._cleanup_failed_setup(loaded_modules)
                raise RuntimeError(f"فشل تحميل الوحدة {module}; أوقف الإقلاع.") from error

        try:
            # Register restart-safe controls only after their owning extensions loaded.
            from cogs.community import PersistentDMRatingView

            self.add_view(PersistentDMRatingView())
            self.install_interaction_guards()
            if os.getenv("SYNC_COMMANDS", "false").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }:
                try:
                    if self.sync_guild is not None:
                        self.tree.copy_global_to(guild=self.sync_guild)
                        synced = await self.tree.sync(guild=self.sync_guild)
                        logger.info(
                            "✨ تمت مزامنة %d أمر Slash مع سيرفر التطوير %s.",
                            len(synced),
                            self.sync_guild.id,
                        )
                    else:
                        synced = await self.tree.sync()
                        logger.info("✨ تمت مزامنة %d أمر Slash عالمياً بنجاح.", len(synced))
                except discord.HTTPException as error:
                    # A transient Discord sync outage should not prevent the bot
                    # from serving already-published commands. Log it clearly;
                    # errors in the explicit upsert path below still roll back.
                    logger.error("⚠️ فشل مزامنة أوامر Slash: %s", error)
            else:
                logger.info("⏭️ تم تخطي مزامنة أوامر Slash؛ فعّل SYNC_COMMANDS=true عند الحاجة.")
                from cogs.rank_commands import publish_rank_commands
                from cogs.profile import publish_profile_command
                from cogs.subscription_commands import publish_subscription_commands
                from command_registration import publish_command_groups

                # Each publisher returns False after logging an API or schema
                # error. Do not silently mark startup complete when any command
                # family was only partially registered: reconnect retries the
                # whole idempotent upsert sequence after cleanup.
                publication_results = {
                    "rank": await publish_rank_commands(self, guild=self.sync_guild),
                    "profile": await publish_profile_command(self, guild=self.sync_guild),
                    "subscription": await publish_subscription_commands(self, guild=self.sync_guild),
                }
                # Broad sync is intentionally disabled by default; explicitly upsert
                # the new game/tournament groups and standalone visual game command
                # without deleting unrelated commands.
                publication_results["game/tournament"] = await publish_command_groups(
                    self,
                    (
                        "game", "tournament", "mafia", "musical_chairs",
                        "roulette_royale", "uno_visual", "buckshot", "bank_heist",
                        "blind_vaults", "connect4_power",
                    ),
                    guild=self.sync_guild,
                )
                failed_publications = [
                    name for name, success in publication_results.items() if not success
                ]
                if failed_publications:
                    raise RuntimeError(
                        "Slash command publication incomplete: "
                        + ", ".join(failed_publications)
                    )
            self.rotate_status.start()
            self._is_initialized = True
        except Exception:
            # Command publication is part of startup, not a best-effort side
            # operation. Roll back cogs, views' owning extensions and resources
            # before the outer reconnect loop retries setup_hook().
            await self._cleanup_failed_setup(loaded_modules)
            raise

    async def close(self):
        """Shut down each owned resource even if an earlier cleanup step fails."""
        logger.info("🛑 جاري إنهاء الجلسات وإيقاف البوت بأمان...")
        self.rotate_status.cancel()

        # Cancel and await owned long-running tasks so shutdown does not leave
        # orphaned tasks or unobserved exceptions behind.
        for attribute in (
            "_gateway_watchdog_task",
            "_diagnostics_task",
            "_wal_checkpoint_task",
        ):
            task = getattr(self, attribute, None)
            setattr(self, attribute, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        for guild_id, user_id in list(self._voice_sessions):
            try:
                await self._close_voice_session(guild_id, user_id, time.time())
            except Exception:
                logger.exception(
                    "[SHUTDOWN] Failed to flush voice session guild=%s user=%s.",
                    guild_id, user_id,
                )

        writer = self._analytics_writer_task
        self._analytics_writer_task = None
        if writer is not None:
            try:
                await asyncio.wait_for(self._analytics_queue.join(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning(
                    "[ANALYTICS] writer drain timed out; closing with queued events remaining."
                )
            except Exception:
                logger.exception("[ANALYTICS] failed while draining the queue.")
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)

        try:
            await checkpoint_wal()
        except Exception:
            logger.exception("[SHUTDOWN] Final database checkpoint failed.")

        if self.dashboard_runner:
            runner, self.dashboard_runner = self.dashboard_runner, None
            try:
                await runner.cleanup()
            except Exception:
                logger.exception("[SHUTDOWN] Dashboard cleanup failed.")

        session, self.session = self.session, None
        if session is not None and not session.closed:
            try:
                await session.close()
            except Exception:
                logger.exception("[SHUTDOWN] Shared HTTP session cleanup failed.")

        try:
            await prime_ai_persistence.close_durable_store()
        except Exception:
            logger.exception("[SHUTDOWN] Durable AI storage cleanup failed.")
        finally:
            # Always close discord.py's own HTTP client even when application
            # resource cleanup above encounters an error.
            await super().close()

    async def _gateway_startup_watchdog(self):
        await asyncio.sleep(10)
        if self.is_ready():
            return
        logger.warning(
            "[GATEWAY] Ready is still pending after 10s: guilds=%d, "
            "cached_members=%d, chunked=%d",
            len(self.guilds),
            sum(len(guild.members) for guild in self.guilds),
            sum(bool(getattr(guild, "chunked", False)) for guild in self.guilds),
        )

    async def on_connect(self):
        self._retry_delay = 5
        logger.info(
            "[GATEWAY] Session connected: guilds=%d, cached_members=%d",
            len(self.guilds),
            sum(len(guild.members) for guild in self.guilds),
        )

    async def on_guild_available(self, guild: discord.Guild):
        logger.info(
            "[GATEWAY] Guild available: %s (%s), members=%d, chunked=%s",
            guild.name,
            guild.id,
            len(guild.members),
            bool(getattr(guild, "chunked", False)),
        )

    async def on_ready(self):
        self.record_metrics()
        logger.info("=" * 45)
        logger.info(
            f"🚀 المحرك المركزي جاهز للخدمة: "
            f"{self.user.name} (ID: {self.user.id})"
        )
        logger.info(
            f"📡 السيرفرات النشطة: {len(self.guilds)} | الأعضاء: "
            f"{sum(guild.member_count for guild in self.guilds if guild.member_count)}"
        )
        logger.info(
            f"⚡ زمن الاستجابة الشبكي (Ping): "
            f"{round(self.latency * 1000)}ms"
        )
        logger.info("[GATEWAY] Bot Tag: %s | Latency: %sms", self.user, round(self.latency * 1000))
        logger.info("[GATEWAY] Connected Guilds: %d", len(self.guilds))
        logger.info("[GATEWAY] Cached members by guild:")
        for guild in self.guilds:
            logger.info(
                "  - %s (%s): %d cached members | chunked=%s | emojis=%d | roles=%d",
                guild.name,
                guild.id,
                len(guild.members),
                bool(getattr(guild, "chunked", False)),
                len(guild.emojis),
                len(guild.roles),
            )
        logger.info("=" * 45)

    @runtime_diagnostics.timed_async("discord.process_commands")
    async def on_message(self, message: discord.Message):
        """Keep prefix-command dispatch active alongside cog listeners."""
        guild = getattr(message, "guild", None)
        author = getattr(message, "author", None)
        if guild is not None and author is not None and not getattr(author, "bot", False):
            self._queue_analytics_event({
                "guild_id": guild.id,
                "channel_id": getattr(getattr(message, "channel", None), "id", None),
                "user_id": author.id,
                "timestamp": time.time(),
            })
        await self.process_commands(message)

    def _queue_analytics_event(self, event: dict) -> bool:
        """Queue telemetry without making a Discord gateway callback wait on SQLite."""
        try:
            self._analytics_queue.put_nowait(event)
            runtime_diagnostics.set_gauge("analytics.queue_depth", self._analytics_queue.qsize())
            return True
        except asyncio.QueueFull:
            logger.warning("[ANALYTICS] queue full; dropping one telemetry event.")
            return False

    async def _analytics_writer(self):
        """Batch analytics writes so the gateway listener never waits on SQLite."""
        while True:
            batch = []
            try:
                batch.append(await self._analytics_queue.get())
                try:
                    while len(batch) < 100:
                        batch.append(await asyncio.wait_for(
                            self._analytics_queue.get(),
                            timeout=0.6,
                        ))
                except asyncio.TimeoutError:
                    pass

                message_events = [
                    event for event in batch
                    if event.get("kind") != "voice_session"
                ]
                if message_events:
                    try:
                        await record_analytics_events(message_events)
                    except Exception:
                        logger.exception("[ANALYTICS] failed to persist message batch.")

                for event in batch:
                    if event.get("kind") != "voice_session":
                        continue
                    try:
                        await record_analytics_voice_session(
                            event["guild_id"],
                            event.get("channel_id"),
                            event.get("user_id"),
                            event["started_at"],
                            event["ended_at"],
                            event["duration_seconds"],
                        )
                    except Exception:
                        logger.exception("[ANALYTICS] failed to persist voice session.")
            finally:
                for _ in batch:
                    self._analytics_queue.task_done()
                runtime_diagnostics.set_gauge("analytics.queue_depth", self._analytics_queue.qsize())

    async def _close_voice_session(self, guild_id: int, user_id: int, ended_at: float):
        session = self._voice_sessions.pop((int(guild_id), int(user_id)), None)
        if not session:
            return
        channel_id, started_at = session
        self._queue_analytics_event({
            "kind": "voice_session",
            "guild_id": int(guild_id),
            "channel_id": int(channel_id) if channel_id else None,
            "user_id": int(user_id),
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": max(0, round(ended_at - started_at)),
        })

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Add voice telemetry while preserving every cog listener."""
        if getattr(member, "bot", False) or member.guild is None:
            return
        before_id = getattr(getattr(before, "channel", None), "id", None)
        after_id = getattr(getattr(after, "channel", None), "id", None)
        if before_id == after_id:
            return
        now = time.time()
        if before_id is not None:
            await self._close_voice_session(member.guild.id, member.id, now)
        if after_id is not None:
            self._voice_sessions[(member.guild.id, member.id)] = (after_id, now)
            self._queue_analytics_event({
                "kind": "voice_presence",
                "guild_id": member.guild.id,
                "channel_id": after_id,
                "user_id": member.id,
                "is_voice": True,
                "timestamp": now,
            })

    def record_metrics(self) -> None:
        """Capture live gateway and guild values for the dashboard charts."""
        latency = self.latency
        latency_ms = (
            round(latency * 1000)
            if latency == latency and latency != float("inf")
            else None
        )
        timestamp = time.time()
        for guild in self.guilds:
            self.metrics.append(
                {
                    "guild_id": str(guild.id),
                    "ts": timestamp,
                    "latency_ms": latency_ms,
                    "members": guild.member_count,
                }
            )

    def metrics_for_guild(self, guild_id: int) -> list[dict]:
        """Return a bounded, JSON-ready metric history for one guild."""
        self.record_metrics()
        return [
            dict(item)
            for item in self.metrics
            if str(item.get("guild_id")) == str(guild_id)
        ][-120:]

    @tasks.loop(seconds=30)
    async def rotate_status(self):
        if not self.is_ready():
            return

        total_members = sum(
            guild.member_count
            for guild in self.guilds
            if guild.member_count
        )
        statuses = [
            (
                discord.ActivityType.watching,
                f"{total_members:,} عضو | /help",
            ),
            (
                discord.ActivityType.competing,
                f"{len(self.guilds)} سيرفر | Shield Active 🛡️",
            ),
            (
                discord.ActivityType.listening,
                f"لوحة التحكم | Port {configured_port_text()} ⚡",
            ),
        ]

        activity_type, activity_name = statuses[
            self.presence_step % len(statuses)
        ]
        await self.change_presence(
            activity=discord.Activity(
                type=activity_type,
                name=activity_name,
            ),
            status=discord.Status.online,
        )
        self.presence_step += 1

    @rotate_status.before_loop
    async def before_rotate(self):
        await self.wait_until_ready()


bot = EnterpriseBot()


@bot.tree.error
async def on_app_command_error(
    itx: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.CommandOnCooldown):
        message = (
            f"⏳ يرجى الانتظار `{error.retry_after:.1f}` ثانية "
            "قبل إعادة استخدام هذا الأمر."
        )
    elif isinstance(error, app_commands.MissingPermissions):
        missing = ", ".join(
            f"`{permission}`"
            for permission in error.missing_permissions
        )
        message = (
            "⛔ لا تمتلك الصلاحيات الكافية لتنفيذ هذا الإجراء: "
            f"{missing}"
        )
    elif isinstance(error, app_commands.BotMissingPermissions):
        missing = ", ".join(
            f"`{permission}`"
            for permission in error.missing_permissions
        )
        message = (
            "❌ يفتقر البوت إلى الصلاحيات المطلوبة في هذه القناة: "
            f"{missing}"
        )
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = (
            "🔒 هذا الأمر متاح للاستخدام داخل السيرفرات فقط "
            "وليس في الرسائل الخاصة."
        )
    else:
        command_name = itx.command.name if itx.command else "مجهول"
        logger.error(
            f"خطأ غير معالج في الأمر [{command_name}]: {error}"
        )
        message = (
            "⚠️ حدث خطأ تقني غير متوقع أثناء معالجة الطلب، "
            "تم تدوين الخطأ لمراجعته."
        )

    await send_interaction_message(itx, message, ephemeral=True)


async def main():
    loop = asyncio.get_running_loop()
    logger.info(
        "🚀 PRIME deployment marker | python=%s | discord.py=%s | port=%s",
        sys.version.split()[0],
        discord.__version__,
        configured_port_text(),
    )

    def request_shutdown(received_signal):
        logger.info("🛑 Received %s; beginning graceful shutdown.", received_signal.name)
        if not bot.is_closed():
            asyncio.create_task(bot.close())

    for received_signal in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(
                received_signal,
                request_shutdown,
                received_signal,
            )
    async with bot:
        while not bot.is_closed():
            try:
                await bot.start(TOKEN)
            except discord.LoginFailure:
                logger.critical(
                    "🚨 رمز DISCORD_TOKEN غير صالح أو تم تغييره، "
                    "تم إيقاف المحرك فوراً."
                )
                break
            except discord.PrivilegedIntentsRequired:
                logger.critical(
                    "يلزم تفعيل Server Members Intent وMessage Content Intent "
                    "في Discord Developer Portal "
                    "→ Bot → Privileged Gateway Intents. تم إيقاف المحرك."
                )
                break
            except (
                discord.ConnectionClosed,
                aiohttp.ClientConnectorError,
            ) as error:
                await bot._reset_http_session_for_retry()
                retry_delay = bot._retry_delay
                logger.warning(
                    "⚠️ فقدان مؤقت للاتصال بخوادم ديسكورد "
                    f"({error}). إعادة المحاولة خلال "
                    f"{retry_delay} ثوانٍ..."
                )
                await asyncio.sleep(retry_delay)
                bot._retry_delay = min(retry_delay * 2, 60)
            except Exception as error:
                # Preserve the original startup exception: setup_hook wraps
                # extension-load failures in RuntimeError, so exc_info is needed
                # to reveal the underlying import/registration error.
                try:
                    await bot._reset_http_session_for_retry()
                except Exception:
                    logger.exception("تعذر تنظيف موارد HTTP بعد فشل التشغيل.")

                retry_delay = bot._retry_delay
                logger.critical(
                    "❌ انقطاع غير معالج في الحلقة التشغيلية: %s. "
                    "إعادة المحاولة خلال %s ثوانٍ.",
                    error,
                    retry_delay,
                    exc_info=(type(error), error, error.__traceback__),
                )
                await asyncio.sleep(retry_delay)
                bot._retry_delay = min(retry_delay * 2, 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🚪 تم إيقاف تشغيل الخادم يدوياً بواسطة المطور.")