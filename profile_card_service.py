"""Shared profile-card snapshot construction for Discord delivery and storefront preview."""
from __future__ import annotations

from typing import Any

import database
import profile_store_db
from level_progression import text_progress


async def build_profile_card_data(guild: Any, member: Any) -> tuple[dict, dict]:
    """Build the exact same live profile values and selected cosmetics for every render path."""
    guild_id, user_id = int(guild.id), int(member.id)
    humans = [
        int(user.id) for user in getattr(guild, "members", [])
        if not getattr(user, "bot", False)
    ]
    if user_id not in humans:
        humans.append(user_id)

    snapshot = await database.get_command_rank_snapshot(
        guild_id, user_id, humans, mode="text",
    )
    progress = text_progress(int(snapshot.get("text_xp") or 0))
    wallet = await profile_store_db.get_profile_wallet(guild_id, user_id)
    configuration = await profile_store_db.get_profile_card_configuration(guild_id, user_id)
    async with database.connect() as db:
        async with db.execute(
            "SELECT COALESCE(current_streak, 0) FROM user_levels WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cursor:
            streak_row = await cursor.fetchone()
    streak_days = max(0, int(streak_row[0] or 0)) if streak_row else 0
    profile = {
        "display_name": str(getattr(member, "display_name", None) or member.name),
        "username": str(getattr(member, "name", "member")),
        "level": int(snapshot.get("text_level") or progress["level"]),
        "xp": int(progress["progress_xp"]),
        "required_xp": max(1, int(progress["xp_required"])),
        "balance": int(wallet.get("balance") or 0),
        "messages": int(snapshot.get("total_messages") or 0),
        "rank": snapshot.get("rank"),
        "voice_seconds": int(snapshot.get("total_voice_seconds") or 0),
        "streak_days": streak_days,
    }
    return profile, configuration
