"""Safely upsert selected Discord command groups without replacing unrelated commands."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger("CommandRegistration")

_SIGNATURE_FIELDS = (
    "name",
    "description",
    "type",
    "options",
    "default_member_permissions",
    "dm_permission",
)


def _signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(payload.get(name) for name in _SIGNATURE_FIELDS)


async def publish_command_groups(
    bot,
    command_names: Sequence[str],
    *,
    guild=None,
) -> bool:
    """Upsert only named local commands, preserving all unrelated Discord registrations."""
    try:
        local_commands = {}
        for raw_name in command_names:
            name = str(raw_name).strip()
            if not name:
                raise ValueError("command names must be non-empty")
            command = bot.tree.get_command(name)
            if command is None:
                raise RuntimeError(f"local /{name} command group is missing")
            local_commands[name] = command

        remote = await bot.tree.fetch_commands(guild=guild)
        existing = {command.name: command for command in remote}
        updated = 0
        for name, command in local_commands.items():
            payload = command.to_dict(bot.tree)
            previous = existing.get(name)
            if previous and _signature(previous.to_dict()) == _signature(payload):
                continue
            if guild is None:
                await bot.http.upsert_global_command(bot.application_id, payload)
            else:
                await bot.http.upsert_guild_command(
                    bot.application_id,
                    int(guild.id),
                    payload,
                )
            updated += 1

        logger.info(
            "PRIME selected command groups are registered (%s updated)",
            updated,
        )
        return True
    except Exception:
        logger.exception(
            "Cannot register selected PRIME command groups; unrelated remote commands are untouched"
        )
        return False
