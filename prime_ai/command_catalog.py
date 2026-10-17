"""Runtime catalog for explicitly reviewed command-to-AI adapter contracts.

This module inventories only exact Slash commands that have a reviewed bridge.
It never invokes callbacks or makes arbitrary loaded commands available to PRIME AI.
"""

from __future__ import annotations

from typing import Any


_REVIEWED_COMMAND_ADAPTERS: dict[str, dict[str, str]] = {
    "channelinfo": {
        "tool": "fetch_channel", "execution_path": "read_skill",
        "operation": "read", "risk": "LOW",
        "required_permission": "view_channel", "policy_command": "channelinfo",
    },
    "create_channel": {
        "tool": "create_channel", "execution_path": "native_action",
        "operation": "write", "risk": "MEDIUM",
        "required_permission": "manage_channels", "policy_command": "create_channel",
    },
    "rename_channel": {
        "tool": "rename_channel", "execution_path": "native_action",
        "operation": "write", "risk": "MEDIUM",
        "required_permission": "manage_channels", "policy_command": "rename_channel",
    },
    "delete_channel": {
        "tool": "delete_channel", "execution_path": "native_action",
        "operation": "write", "risk": "CRITICAL",
        "required_permission": "manage_channels", "policy_command": "delete_channel",
    },
    "setnick": {
        "tool": "set_member_nickname", "execution_path": "native_action",
        "operation": "write", "risk": "MEDIUM",
        "required_permission": "manage_nicknames", "policy_command": "setnick",
    },
    "give_role": {
        "tool": "assign_role", "execution_path": "native_action",
        "operation": "write", "risk": "HIGH",
        "required_permission": "manage_roles", "policy_command": "give_role",
    },
    "take_role": {
        "tool": "remove_role", "execution_path": "native_action",
        "operation": "write", "risk": "HIGH",
        "required_permission": "manage_roles", "policy_command": "take_role",
    },
    "timeout": {
        "tool": "timeout_member", "execution_path": "linked_handler",
        "operation": "write", "risk": "HIGH",
        "required_permission": "moderate_members", "policy_command": "timeout",
    },
    "kick": {
        "tool": "kick_member", "execution_path": "linked_handler",
        "operation": "write", "risk": "CRITICAL",
        "required_permission": "kick_members", "policy_command": "kick",
    },
    "ban": {
        "tool": "ban_member", "execution_path": "linked_handler",
        "operation": "write", "risk": "CRITICAL",
        "required_permission": "ban_members", "policy_command": "ban",
    },
    "unban": {
        "tool": "unban_member", "execution_path": "linked_handler",
        "operation": "write", "risk": "HIGH",
        "required_permission": "ban_members", "policy_command": "unban",
    },
    "lock": {
        "tool": "set_channel_mode", "execution_path": "linked_handler",
        "operation": "write", "risk": "HIGH",
        "required_permission": "manage_channels", "policy_command": "lock",
    },
    "unlock": {
        "tool": "set_channel_mode", "execution_path": "linked_handler",
        "operation": "write", "risk": "HIGH",
        "required_permission": "manage_channels", "policy_command": "unlock",
    },
}


def _qualified_name(command: Any) -> str:
    value = getattr(command, "qualified_name", None) or getattr(command, "name", "")
    return str(value or "").strip().lower()


def _parameter_metadata(command: Any) -> list[dict[str, Any]]:
    result = []
    for parameter in list(getattr(command, "parameters", ()) or ())[:20]:
        name = str(getattr(parameter, "name", "") or "").strip()
        if not name:
            continue
        option_type = getattr(parameter, "type", None)
        result.append({
            "name": name[:80],
            "required": bool(getattr(parameter, "required", False)),
            "type": str(getattr(option_type, "name", None) or option_type or "")[:40],
        })
    return result


def build_reviewed_command_catalog(bot: Any) -> list[dict[str, Any]]:
    """Return live metadata for reviewed bridges found in the loaded Slash tree.

    The registry is an allowlist: similarly named or dangerous commands are
    omitted unless a deliberate adapter is added and covered by tests.
    """
    tree = getattr(bot, "tree", None)
    walk_commands = getattr(tree, "walk_commands", None)
    if not callable(walk_commands):
        return []

    try:
        loaded = list(walk_commands())
    except Exception:
        return []

    by_name = {}
    for command in loaded:
        name = _qualified_name(command)
        if name in _REVIEWED_COMMAND_ADAPTERS:
            by_name[name] = command

    catalog = []
    for name, adapter in _REVIEWED_COMMAND_ADAPTERS.items():
        command = by_name.get(name)
        if command is None:
            continue
        catalog.append({
            "command_name": name,
            **adapter,
            "description": str(getattr(command, "description", "") or "")[:160],
            "parameters": _parameter_metadata(command),
        })
    return catalog
