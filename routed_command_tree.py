"""Route selected application commands into groups with rollback-safe cleanup."""

from __future__ import annotations

import discord
from discord import app_commands


# Discord limits top-level application commands. Keep the existing callbacks
# and names while grouping selected command families under stable root groups.
SLASH_COMMAND_GROUPS = {
    "ChatJailCog": {
        "chat": {
            "clear_user", "cleanup", "nuke", "lock", "unlock", "lockall",
            "unlockall", "hide", "show", "hideall", "showall", "emergency",
            "thread_lock", "thread_unlock", "clean_commands", "clean_bots",
            "delete_after", "delete_before", "block_write", "unblock_write",
            "hide_member", "show_member", "open_chat_member",
            "remove_chat_member",
        },
        "jail": {"jail", "solo_jail", "unjail"},
    },
    "AdminAdvancedCog": {
        "admin": {
            "setnick", "summon", "delwarn", "clearwarns", "give_role",
            "take_role", "strip_roles", "role_color", "dossier", "note",
            "notes", "delnote", "event_points", "reset_points", "roleall",
            "removeroleall", "massrole", "temprole", "role_icon",
            "sync_perms", "role_members", "no_role", "bot_list",
            "member_stats", "reset_nicks",
        },
    },
    "Tournaments": {
        "tournament": {"bracket", "result"},
    },
    "ToolsChannelsCog": {
        "tools": {
            "avatar", "banner", "userinfo", "serverinfo", "roleinfo", "ping",
            "serverheader", "roles", "emojis", "joinposition", "mutual",
            "whois", "channelinfo", "rolemembers", "snipe", "editsnipe",
            "firstmsg", "steal_emoji", "enlarge_emoji", "remind",
            "countdown", "color", "encode", "decode", "quote",
        },
        "channels": {
            "timestamp", "steal_sticker", "create_channel", "delete_channel",
            "rename_channel", "move_channel", "set_topic", "clone_channel",
            "create_voice", "delete_voice", "rename_voice", "move_voice",
            "mod_stats", "undo_action", "security_report",
        },
    },
}

SLASH_GROUP_DESCRIPTIONS = {
    "chat": "إدارة الشات والقنوات",
    "jail": "السجن والعزل",
    "admin": "الإدارة المتقدمة",
    "tools": "معلومات وأدوات الأعضاء",
    "channels": "إدارة القنوات والإحصائيات",
    "tournament": "إدارة جولات البطولات",
}


class RoutedCommandTree(app_commands.CommandTree):
    """Route selected cog commands into groups and support rollback removal."""

    def _ensure_group_registry(self) -> dict[str, app_commands.Group]:
        groups = getattr(self, "_slash_groups", None)
        if groups is None:
            groups = {}
            self._slash_groups = groups
        return groups

    def _remove_empty_group(
        self,
        group_name: str,
        group: app_commands.Group,
    ) -> None:
        if group.commands:
            return

        groups = self._ensure_group_registry()
        groups.pop(group_name, None)
        # A routed child is not a root command. Once the last child is removed,
        # remove its root group too so a later load can recreate it cleanly.
        super().remove_command(
            group_name,
            type=discord.AppCommandType.chat_input,
        )

    def remove_cog_grouped_commands(self, cog_name: str) -> None:
        """Purge orphaned grouped commands before retrying a failed cog load."""
        groups = self._ensure_group_registry()
        for group_name, command_names in SLASH_COMMAND_GROUPS.get(cog_name, {}).items():
            group = groups.get(group_name)
            if group is None:
                continue
            for command_name in command_names:
                group.remove_command(command_name)
            self._remove_empty_group(group_name, group)

    def add_command(
        self,
        command,
        /,
        *,
        guild=None,
        guilds=None,
        override=False,
    ) -> None:
        cog_name = getattr(getattr(self, "client", None), "_active_cog_name", None)
        cog_groups = SLASH_COMMAND_GROUPS.get(cog_name, {})
        group_name = next(
            (
                name
                for name, command_names in cog_groups.items()
                if getattr(command, "name", None) in command_names
            ),
            None,
        )
        global_registration = (
            guild is None or guild is discord.utils.MISSING
        ) and (guilds is None or guilds is discord.utils.MISSING)

        if group_name and global_registration:
            groups = self._ensure_group_registry()
            group = groups.get(group_name)
            if group is None:
                group = app_commands.Group(
                    name=group_name,
                    description=SLASH_GROUP_DESCRIPTIONS[group_name],
                )
                groups[group_name] = group
                super().add_command(group, override=override)
            group.add_command(command, override=override)
            return

        super().add_command(
            command,
            guild=guild,
            guilds=guilds,
            override=override,
        )

    def remove_command(
        self,
        command: str,
        /,
        *,
        guild=None,
        type: discord.AppCommandType = discord.AppCommandType.chat_input,
    ):
        # discord.py's normal extension rollback removes by root command name.
        # Routed cog commands are children, so first remove from the active cog's
        # known group; this is also used by unload/override cleanup.
        client = getattr(self, "client", None)
        active_cog_name = getattr(client, "_active_cog_name", None)
        if guild is None and type is discord.AppCommandType.chat_input:
            for group_name, command_names in SLASH_COMMAND_GROUPS.get(
                active_cog_name, {}
            ).items():
                if command not in command_names:
                    continue
                group = self._ensure_group_registry().get(group_name)
                if group is not None:
                    removed = group.get_command(command)
                    if removed is not None:
                        group.remove_command(command)
                        self._remove_empty_group(group_name, group)
                        return removed

        removed = super().remove_command(command, guild=guild, type=type)
        if removed is not None:
            return removed

        # Also support rollback when no active cog name is available (for
        # example, unloading an extension after startup).
        if guild is None and type is discord.AppCommandType.chat_input:
            for group_name, group in list(self._ensure_group_registry().items()):
                removed = group.get_command(command)
                if removed is not None:
                    group.remove_command(command)
                    self._remove_empty_group(group_name, group)
                    return removed
        return None
