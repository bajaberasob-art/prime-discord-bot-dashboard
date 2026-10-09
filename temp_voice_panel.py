"""Persistent Discord controls. Every submission is checked against live ownership."""
import logging
import math
import re

import discord

from interaction_runtime import mark_modal_callback
import temp_voice_store as store

log = logging.getLogger("TempVoicePanel")
_EMOJI_PREFIXES = {
    "prime", "voice", "tv", "neon", "cyan", "purple", "violet", "blue",
    "ui", "icon", "emoji", "btn", "button",
}
_EMOJI_STYLE_SUFFIXES = {
    "neon", "cyan", "purple", "violet", "blue", "outline", "solid", "line",
    "filled", "icon", "emoji", "btn", "button",
}


def _emoji_name_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


_EMOJI_ALIAS_TO_ACTION = {
    _emoji_name_key(alias): action
    for action, aliases in store.BUTTON_EMOJI_ALIASES.items()
    for alias in aliases
}


def _custom_emoji_action(emoji):
    tokens = re.findall(r"[a-z0-9]+", str(getattr(emoji, "name", "")).casefold())
    family = []
    while tokens and tokens[0] in _EMOJI_PREFIXES:
        family.append(tokens.pop(0))
    while tokens and tokens[-1] in _EMOJI_STYLE_SUFFIXES:
        tokens.pop()
    name = _emoji_name_key("_".join(tokens))
    action = _EMOJI_ALIAS_TO_ACTION.get(name)
    return (tuple(family), action) if action else (None, None)


def _usable_custom_emoji(emoji):
    checker = getattr(emoji, "is_usable", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return getattr(emoji, "available", True) is not False


def _emoji_family_rank(family):
    priorities = {
        "prime": 0, "neon": 1, "voice": 2, "tv": 3, "cyan": 4,
        "purple": 4, "violet": 4, "blue": 4, "ui": 5, "icon": 6,
        "emoji": 7, "button": 8, "btn": 8,
    }
    return (min((priorities.get(part, 9) for part in family), default=9), -len(family), family)


def resolve_button_emojis(guild, actions=None):
    """Use one sufficiently complete, usable server emoji family; otherwise use Unicode."""
    resolved = dict(store.BUTTON_EMOJIS)
    requested = list(dict.fromkeys(store.BUTTONS if actions is None else actions))
    if not guild or not requested:
        return resolved

    families = {}
    for emoji in getattr(guild, "emojis", ()):
        if not _usable_custom_emoji(emoji):
            continue
        family, action = _custom_emoji_action(emoji)
        if action is None:
            continue
        candidate = families.setdefault(family, {}).get(action)
        preference = (
            bool(getattr(emoji, "animated", False)),
            str(getattr(emoji, "name", "")).casefold(),
            int(getattr(emoji, "id", 0) or 0),
        )
        if candidate is None or preference < candidate[0]:
            families[family][action] = (preference, emoji)

    branded = []
    generic = []
    for family, mapping in families.items():
        coverage = sum(action in mapping for action in requested)
        if not coverage:
            continue
        if family and coverage >= max(1, math.ceil(len(requested) * 0.6)):
            branded.append((family, mapping, coverage))
        elif not family and coverage >= max(1, math.ceil(len(requested) * 0.85)):
            generic.append((family, mapping, coverage))
    candidates = branded or generic
    if not candidates:
        return resolved
    family, mapping, _ = min(
        candidates,
        key=lambda candidate: (-candidate[2], _emoji_family_rank(candidate[0])),
    )
    for action, (_, emoji) in mapping.items():
        resolved[action] = emoji
    return resolved


class ValueModal(discord.ui.Modal):
    def __init__(self, cog, channel_id, action, label, default=""):
        super().__init__(title=label, timeout=180)
        self.cog, self.channel_id, self.action = cog, channel_id, action
        self.value = discord.ui.TextInput(label=label, default=str(default),
                                          max_length=100, required=True)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        await self.cog.dispatch(interaction, self.action, self.channel_id, str(self.value))


class RoomNameModal(discord.ui.Modal):
    def __init__(self, cog, channel_id, default=""):
        super().__init__(title="تغيير اسم الروم", timeout=180)
        self.cog, self.channel_id = cog, channel_id
        self.name = discord.ui.TextInput(
            label="اسم الروم", default=str(default), max_length=100, required=True,
            placeholder="اكتب اسمًا من 1 إلى 100 حرف",
        )
        self.add_item(self.name)

    async def on_submit(self, interaction):
        await self.cog.dispatch(interaction, "rename", self.channel_id, str(self.name).strip())


class MemberLimitModal(discord.ui.Modal):
    def __init__(self, cog, channel_id, default=0):
        super().__init__(title="حد الأعضاء", timeout=180)
        self.cog, self.channel_id = cog, channel_id
        self.limit = discord.ui.TextInput(
            label="الحد (0 إلى 99، والصفر بلا حد)", default=str(default),
            min_length=1, max_length=2, required=True,
            placeholder="0–99",
        )
        self.add_item(self.limit)

    async def on_submit(self, interaction):
        value = str(self.limit).strip()
        if not value.isdecimal() or not 0 <= int(value) <= 99:
            await interaction.response.send_message("حد الأعضاء يجب أن يكون رقمًا من 0 إلى 99.", ephemeral=True)
            return
        await self.cog.dispatch(interaction, "limit", self.channel_id, value)


class MemberPicker(discord.ui.View):
    def __init__(self, cog, channel_id, action, actor_id):
        super().__init__(timeout=120)
        self.actor_id = actor_id
        selector = discord.ui.UserSelect(placeholder="اختر العضو", min_values=1, max_values=1)

        async def callback(itx):
            if itx.user.id != actor_id:
                await itx.response.send_message("هذه القائمة ليست لك.", ephemeral=True)
                return
            await cog.dispatch(itx, action, channel_id, str(selector.values[0].id))
        selector.callback = callback
        self.add_item(selector)


class ChoicePicker(discord.ui.View):
    def __init__(self, cog, channel_id, action, options, actor_id):
        super().__init__(timeout=120)
        selector = discord.ui.Select(placeholder="اختر الإعداد", options=options[:25])

        async def callback(itx):
            if itx.user.id != actor_id:
                await itx.response.send_message("هذه القائمة ليست لك.", ephemeral=True)
                return
            await cog.dispatch(itx, action, channel_id, selector.values[0])
        selector.callback = callback
        self.add_item(selector)


class DeleteConfirmation(discord.ui.View):
    def __init__(self, cog, channel_id, actor_id, guild=None):
        super().__init__(timeout=60)
        button = discord.ui.Button(
            label=None, emoji=resolve_button_emojis(guild, ["delete"])["delete"],
            style=discord.ButtonStyle.danger,
        )

        async def confirm(itx):
            if itx.user.id != actor_id:
                await itx.response.send_message("هذا التأكيد ليس لك.", ephemeral=True)
                return
            await cog.dispatch(itx, "delete", channel_id, "confirmed")
        button.callback = confirm
        self.add_item(button)


class RoomPanel(discord.ui.View):
    def __init__(self, cog, config, guild=None):
        super().__init__(timeout=None)
        emojis = resolve_button_emojis(guild, config["buttons"])
        for index, action in enumerate(config["buttons"]):
            button = discord.ui.Button(
                label=None, emoji=emojis[action],
                custom_id="prime:tv:" + action,
                row=index // 4,
                style=discord.ButtonStyle.danger if action == "delete"
                else discord.ButtonStyle.primary if action in {"claim", "invite"}
                else discord.ButtonStyle.secondary,
            )

            async def callback(itx, key=action):
                await cog.dispatch(itx, key)
            if action in {"rename", "limit", "color"}:
                mark_modal_callback(callback)
            button.callback = callback
            self.add_item(button)

    async def on_error(self, interaction, error, item):
        log.exception("Temporary room control failed", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("تعذر تنفيذ الإجراء. راجع صلاحيات البوت.", ephemeral=True)
        else:
            await interaction.response.send_message("تعذر تنفيذ الإجراء. راجع صلاحيات البوت.", ephemeral=True)


def render_template(template, member, count):
    return (template.replace("{OWNER_NAME}", member.display_name)
            .replace("{OWNER_MENTION}", member.mention).replace("{COUNT}", str(count)))
