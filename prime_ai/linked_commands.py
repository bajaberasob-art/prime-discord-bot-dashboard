"""Bridge AI tools to the existing Slash-command implementations and policies."""

from typing import Any

import discord

from .errors import AccessDenied

_LINKED_COMMANDS = {
    "timeout_member": ("timeout", "Moderation"),
    "kick_member": ("kick", "SanctionsVoiceCog"),
    "ban_member": ("ban", "SanctionsVoiceCog"),
    "unban_member": ("unban", "SanctionsVoiceCog"),
}

# These actions still use PRIME's validated native executor, but have a matching
# Slash command whose per-guild enabled/role/channel policy must also be honored.
# Keep this bridge explicit: a similar-looking command is not automatically safe
# to treat as the same operation.
_COMMAND_POLICY_BINDINGS = {
    "create_channel": "create_channel",
    "rename_channel": "rename_channel",
    "delete_channel": "delete_channel",
    "set_member_nickname": "setnick",
    "assign_role": "give_role",
    "remove_role": "take_role",
}

# Read-only AI routes share the saved policy of the equivalent Slash command,
# but keep their own bounded, read-only data implementation.
_READ_SKILL_POLICY_BINDINGS = {
    "fetch_channel": "channelinfo",
}


async def ensure_command_policy_binding(
    guild: Any, actor: Any, channel: Any, command_name: str,
) -> None:
    """Apply the saved per-guild policy for one explicitly reviewed command."""
    from command_policy_service import CommandPolicyDenied, ensure_command_policy

    try:
        await ensure_command_policy(
            guild.id, command_name, actor, getattr(channel, "id", None),
        )
    except CommandPolicyDenied as error:
        raise AccessDenied(str(error)) from error


async def ensure_read_skill_command_policy(
    guild: Any, actor: Any, channel: Any, tool: str | None,
) -> None:
    """Apply a Slash policy only to read skills with an explicit reviewed link."""
    command_name = _READ_SKILL_POLICY_BINDINGS.get(str(tool or ""))
    if command_name is None:
        return
    await ensure_command_policy_binding(guild, actor, channel, command_name)


async def execute_linked_command(
    bot: Any, guild: Any, actor: Any, channel: Any, step: dict, checked: dict,
) -> str | None:
    """Reuse linked handlers where available and preserve matching Slash policies."""
    tool = step["tool"]
    args = step["arguments"]
    linked = _LINKED_COMMANDS.get(tool)
    if tool == "set_channel_mode":
        linked = (
            "lock" if args.get("mode") == "read_only" else "unlock", "ChatJailCog",
        )
    policy_command = (
        linked[0] if linked is not None
        else _COMMAND_POLICY_BINDINGS.get(tool)
    )
    if linked is None and policy_command is None:
        return None

    await ensure_command_policy_binding(guild, actor, channel, policy_command)

    # Some PRIME AI tools have no equivalent Slash handler. For those with a
    # matching command above, return to the validated native executor only after
    # the corresponding per-guild Slash command policy has allowed the request.
    if linked is None:
        return None

    command_name, cog_name = linked
    get_cog = getattr(bot, "get_cog", None)
    cog = get_cog(cog_name) if callable(get_cog) else None
    if cog is None:
        raise AccessDenied("linked_command_handler_unavailable")
    reason = str(args.get("reason") or "PRIME AI action requested")[:512]
    if tool == "timeout_member":
        target = checked["targets"]["member"]
        expires_at = await cog.execute_timeout_command(
            guild, target, int(args["minutes"]), reason,
        )
        return f"timeout_user={target.id}_until={expires_at.isoformat()}"
    if tool == "kick_member":
        target = checked["targets"]["member"]
        await cog.execute_kick_command(guild, actor, target, reason)
        return f"kicked_user={target.id}"
    if tool == "ban_member":
        target = checked["targets"]["member"]
        await cog.execute_ban_command(
            guild, actor, target, int(args.get("delete_days", 0)), reason,
        )
        return f"banned_user={target.id}"
    if tool == "unban_member":
        target = str(int(args["user_id"]))
        user = await cog._unban_target(guild, target, reason)
        if user is None:
            raise ValueError("ban_not_found")
        try:
            await guild.fetch_ban(discord.Object(id=int(args["user_id"])))
        except discord.NotFound:
            return f"unbanned_user={args['user_id']}"
        raise RuntimeError("discord_unban_not_confirmed")
    if tool == "set_channel_mode":
        target = checked["targets"]["channel"]
        await cog.execute_channel_mode_command(
            target, actor, open_channel=args["mode"] == "open",
        )
        return f"set_channel_mode={args['mode']}_channel_id={target.id}"
    return None
