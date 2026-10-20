"""Runtime catalog for explicitly reviewed PRIME AI write-action adapters.

Only exact, loaded Slash commands with a reviewed bridge are included. This is
metadata for planning; it never invokes callbacks or grants permission.
"""

from __future__ import annotations

from typing import Any

import prime_ai_control as control


# Keep this allowlist aligned with prime_ai/linked_commands.py. A bridge is
# explicit: command similarity alone never makes a command AI-callable.
_REVIEWED_COMMAND_ADAPTERS: dict[str, dict[str, Any]] = {
    "create_channel": {
        "tool": "create_channel",
        "execution_path": "native_action",
        "operation": "write",
        "policy_command": "create_channel",
    },
    "rename_channel": {
        "tool": "rename_channel",
        "execution_path": "native_action",
        "operation": "write",
        "policy_command": "rename_channel",
    },
    "delete_channel": {
        "tool": "delete_channel",
        "execution_path": "native_action",
        "operation": "write",
        "policy_command": "delete_channel",
    },
    "setnick": {
        "tool": "set_member_nickname",
        "execution_path": "native_action",
        "operation": "write",
        "policy_command": "setnick",
    },
    "give_role": {
        "tool": "assign_role",
        "execution_path": "native_action",
        "operation": "write",
        "policy_command": "give_role",
    },
    "take_role": {
        "tool": "remove_role",
        "execution_path": "native_action",
        "operation": "write",
        "policy_command": "take_role",
    },
    "timeout": {
        "tool": "timeout_member",
        "execution_path": "linked_handler",
        "operation": "write",
        "policy_command": "timeout",
    },
    "kick": {
        "tool": "kick_member",
        "execution_path": "linked_handler",
        "operation": "write",
        "policy_command": "kick",
    },
    "ban": {
        "tool": "ban_member",
        "execution_path": "linked_handler",
        "operation": "write",
        "policy_command": "ban",
    },
    "unban": {
        "tool": "unban_member",
        "execution_path": "linked_handler",
        "operation": "write",
        "policy_command": "unban",
    },
    "lock": {
        "tool": "set_channel_mode",
        "execution_path": "linked_handler",
        "operation": "write",
        "policy_command": "lock",
        "argument_hints": {"mode": "read_only"},
    },
    "unlock": {
        "tool": "set_channel_mode",
        "execution_path": "linked_handler",
        "operation": "write",
        "policy_command": "unlock",
        "argument_hints": {"mode": "open"},
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
    """Return dynamic metadata for the explicitly reviewed, currently loaded commands.

    Only the catalog's exact names are eligible. Tool risk and required Discord
    permission are derived from ACTION_REGISTRY, not duplicated in this file.
    Read-only skill policies are handled by linked_commands.py separately.
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
        tool = str(adapter["tool"])
        metadata = control.ACTION_REGISTRY.get(tool)
        if metadata is None:
            continue
        entry = {
            "command_name": name,
            "tool": tool,
            "execution_path": str(adapter["execution_path"]),
            "operation": str(adapter["operation"]),
            "policy_command": str(adapter["policy_command"]),
            "risk": str(metadata["risk"]),
            "required_permission": str(metadata["discord_permission"]),
            "description": str(getattr(command, "description", "") or "")[:160],
            "parameters": _parameter_metadata(command),
        }
        argument_hints = adapter.get("argument_hints")
        if isinstance(argument_hints, dict):
            entry["argument_hints"] = dict(argument_hints)
        catalog.append(entry)
    return catalog
