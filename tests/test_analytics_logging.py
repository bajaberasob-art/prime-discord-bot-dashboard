import asyncio
from types import SimpleNamespace

import database
import cogs.analytics as analytics_module
from cogs.analytics import Analytics


def _make_analytics():
    cog = Analytics(SimpleNamespace())
    return cog


class _Invite:
    def __init__(self, code, uses, inviter_id=42):
        self.code = code
        self.uses = uses
        self.inviter = SimpleNamespace(
            id=inviter_id,
            name="Invite owner",
            display_name="Invite owner",
            global_name=None,
        )


class _InviteGuild:
    def __init__(self, guild_id, invites):
        self.id = guild_id
        self._invites = invites

    async def invites(self):
        return self._invites

    async def vanity_invite(self):
        return None


def test_invite_attribution_requires_one_increment_and_stable_invite_set(monkeypatch):
    async def scenario():
        async def persist(_guild_id, _snapshot):
            return None

        monkeypatch.setattr(
            analytics_module, "replace_invite_tracking_cache", persist,
        )

        cog = _make_analytics()
        guild_id = 918273
        cog._invite_cache[guild_id] = {
            "alpha": {"uses": 3, "inviter_id": 42, "inviter_name": "Invite owner", "is_vanity": False},
        }
        cog._invite_ready.add(guild_id)
        attributed = await cog._identify_invite(
            _InviteGuild(guild_id, [_Invite("alpha", 4)])
        )
        assert attributed == {
            "source": "invite",
            "code": "alpha",
            "inviter_id": 42,
            "inviter_name": "Invite owner",
            "uses": 4,
        }

        cog._invite_cache[guild_id] = {
            "alpha": {"uses": 3, "inviter_id": 42, "inviter_name": "Invite owner", "is_vanity": False},
            "beta": {"uses": 7, "inviter_id": 84, "inviter_name": "Other owner", "is_vanity": False},
        }
        ambiguous = await cog._identify_invite(
            _InviteGuild(guild_id, [_Invite("alpha", 4), _Invite("beta", 8, 84)])
        )
        assert ambiguous == {"source": "unknown"}

        cog._invite_cache[guild_id] = {
            "alpha": {"uses": 3, "inviter_id": 42, "inviter_name": "Invite owner", "is_vanity": False},
        }
        changed_set = await cog._identify_invite(
            _InviteGuild(guild_id, [_Invite("alpha", 4), _Invite("new", 0, 99)])
        )
        assert changed_set == {"source": "unknown"}

    asyncio.run(scenario())


def test_member_role_filter_does_not_leak_unselected_role_changes():
    async def scenario():
        cog = _make_analytics()
        guild = SimpleNamespace(id=4455)

        class Role:
            def __init__(self, mention):
                self.mention = mention

            def is_default(self):
                return False

        base = Role("@base")
        added = Role("@selected-add")
        removed = Role("@unselected-remove")
        before = SimpleNamespace(
            guild=guild, id=9, mention="@member", roles=[base, removed],
            nick=None, guild_avatar=None, pending=None, timed_out_until=None,
        )
        after = SimpleNamespace(
            guild=guild, id=9, mention="@member", roles=[base, added],
            nick=None, guild_avatar=None, pending=None, timed_out_until=None,
        )
        logged = []

        async def event_enabled(_guild, _category, event_type=None):
            return event_type == "member_role_add"

        async def audit(*_args, **_kwargs):
            return None

        async def log(*args, **kwargs):
            logged.append((args, kwargs))

        cog._event_enabled = event_enabled
        cog._audit = audit
        cog._log = log

        await cog.on_member_update(before, after)
        assert len(logged) == 1
        fields = logged[0][1]["fields"]
        rendered = " ".join(str(value) for field in fields for value in field)
        assert "@selected-add" in rendered
        assert "@unselected-remove" not in rendered

    asyncio.run(scenario())


def test_audit_embeds_use_member_avatars_and_clean_discord_mentions():
    avatar_url = "https://cdn.example.test/member.png"
    member = SimpleNamespace(
        id=123,
        mention="<@123>",
        display_avatar=SimpleNamespace(url=avatar_url),
    )
    channel = SimpleNamespace(id=456, mention="<#456>", name="general")
    guild = SimpleNamespace(
        id=789,
        name="PRIME Test",
        icon=None,
        get_member=lambda member_id: member if member_id == member.id else None,
        get_channel_or_thread=lambda channel_id: channel if channel_id == channel.id else None,
    )

    embed = _make_analytics()._embed(
        guild,
        "log_member",
        "تحديث عضو",
        "تفاصيل التغيير",
        fields=[
            ("👤 العضو", "<@123> (`123`)", True),
            ("المعرف", "123", True),
            ("📍 القناة", "<#456> (`#general`)", True),
        ],
    )

    assert embed.fields[0].value == "<@123>"
    assert embed.fields[1].value == "<@123>"
    assert embed.fields[2].value == "<#456>"
    assert embed.thumbnail.url == avatar_url
    assert "PRIME Test" in embed.footer.text
    assert "`123`" not in " ".join(field.value for field in embed.fields)


def test_channel_id_field_is_rendered_as_a_channel_mention():
    channel = SimpleNamespace(id=456, mention="<#456>", name="general")
    guild = SimpleNamespace(
        get_channel_or_thread=lambda channel_id: channel if channel_id == channel.id else None,
    )

    fields, thumbnail = analytics_module._format_log_fields(
        guild, "log_channel", [("المعرف", "456", True)],
    )

    assert fields == [("المعرف", "<#456>", True)]
    assert thumbnail is None


def test_logging_routes_and_category_settings_round_trip_in_fresh_database(
    tmp_path, monkeypatch,
):
    async def scenario():
        monkeypatch.setattr(database, "DB_NAME", str(tmp_path / "logs.sqlite"))
        await database.init_db()
        await database.set_logging_channels(
            918274,
            {"log_member": 12345, "log_moderation": 23456},
            {
                "log_member": {
                    "enabled": True,
                    "events": ["member_join"],
                },
                "log_sanctions": {
                    "enabled": False,
                    "events": ["ban"],
                },
            },
        )
        routes = await database.get_logging_channels(918274)
        settings = await database.get_logging_category_settings(918274)
        assert routes["log_member"] == 12345
        assert routes["log_sanctions"] == 23456
        assert routes["log_moderation"] == 23456
        assert settings["log_member"] == {
            "enabled": True,
            "events": ["member_join"],
        }
        assert settings["log_sanctions"] == {
            "enabled": False,
            "events": ["ban"],
        }

    try:
        asyncio.run(scenario())
    finally:
        database.LOG_ROUTING_CACHE.clear()
        database.LOG_CATEGORY_SETTINGS_CACHE.clear()
