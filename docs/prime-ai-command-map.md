# PRIME AI command and action map — Phase 1

Audit baseline: main at 8efa9e26eca41954bc69e54561e01fd184c86b60 (2026-10-17).
Scope: source-level inventory only. This report does not claim to have queried a live Discord guild's synchronized command tree or its per-guild settings.

## Executive finding

PRIME has three different command paths. They overlap in places but are not interchangeable:

1. **Regular Discord commands** are declared by the Cogs and executed through Discord.py's command tree or prefix-command system. Utilities can enumerate loaded commands through bot.commands and bot.tree.walk_commands().
2. **Explicit command/shortcut routing** is owned by cogs/utilities.py. Utilities.dispatch_assistant_prompt and the shortcut dispatcher can route an explicitly named loaded command or a configured shortcut into the existing callback path, with Utilities' interception and command-policy checks. This is not the same as giving the language model a complete, argument-aware tool for every command.
3. **Natural-language AI actions** are limited to the action IDs in prime_ai_control.ACTION_REGISTRY. prime_ai_runtime.plan_action only offers these registered tools, validates their schemas and target IDs, and prime_ai_runtime.execute_tool performs the real Discord operation after fresh policy and permission checks.

The current source contains 19 registered write/action tools and 11 read-intent families. A source scan of the listed Cogs found 179 application-command callback definitions (including the subscription subcommands) plus 6 prefix-command callback definitions. Some prefix commands intentionally duplicate the same feature as a slash command. Those source counts do not guarantee every command is currently synchronized to every Discord server.

## Request entry points and flow

| Entry point | Purpose | How it is routed |
|---|---|---|
| /ask_ai | Direct AI question or request; modes CHAT, ASSISTANT and ACTION; optional image/document/audio attachment | cogs/ai_tools.py → answer_ai and shared runtime |
| Mention, reply, or wake word in a server channel | Conversational AI and natural-language actions | AITools.on_message; respects AI enabled/mode, activation, talk-channel, access and rate-limit settings |
| Exact command name / configured shortcut in a message | Run an existing command through a guarded local route | Utilities.dispatch_assistant_prompt / Utilities._dispatch_command_shortcut; prefers local routing before calling a model |
| Slash or prefix command itself | Normal command behavior | Discord.py command callback and its own checks, plus Utilities' global interceptors and saved command policy |
| Dashboard AI controls | Enable/disable AI tools and configure their per-server policy | dashboard/ai-control.js and web_server.py; these edit the registered AI action/skill policy, not every COG's behavior automatically |

Natural action routing uses local intent patterns and optionally prime_ai_intelligence.infer_natural_action for colloquial phrasing. The model is asked to select from registered actions only; it cannot invent an arbitrary COG command. Action schemas require concrete valid arguments and target IDs resolved from the request/current context. Ambiguous targets should cause a clarification instead of an execution.

## Registered natural-language write/action tools

The names below are the actual keys in prime_ai_control.ACTION_REGISTRY. The arguments shown are the validated tool schema from prime_ai_runtime.TOOL_SCHEMAS. Discord permissions are checked at execution time for both the requester and PRIME, together with role hierarchy, access policies, target existence and action enablement.

| Tool | Inputs | Discord permission | Risk | Confirmation in current runtime | Existing command relationship |
|---|---|---|---|---|---|
| send_message | channel_id, content | send_messages | LOW | No | Native AI executor |
| reply_message | channel_id, message_id, content | send_messages | LOW | No | Native AI executor |
| add_reaction | channel_id, message_id, emoji | add_reactions | LOW | No | Native AI executor |
| edit_message | channel_id, message_id, content | manage_messages | MEDIUM | No | Native AI executor; only PRIME-authored messages may be edited |
| delete_message | channel_id, message_id | manage_messages | HIGH | Yes | Native AI executor; only PRIME-authored messages may be deleted |
| create_channel | name, optional category_id, optional topic | manage_channels | MEDIUM | No | Checks the per-guild policy for create_channel, then uses the validated native executor |
| rename_channel | channel_id, name | manage_channels | MEDIUM | No | Checks the per-guild policy for rename_channel, then uses the validated native executor |
| set_member_nickname | user_id, nickname | manage_nicknames | MEDIUM | No | Checks the per-guild policy for setnick, then uses the validated native executor |
| delete_channel | channel_id, optional reason | manage_channels | CRITICAL | Yes | Checks the per-guild policy for delete_channel, then uses the validated native executor |
| set_channel_mode | channel_id, mode (open or read_only) | manage_channels and manage_roles | HIGH | Yes | Uses ChatJailCog's lock/unlock execution methods and checks the matching policy |
| create_role | name, optional color | manage_roles | MEDIUM | No | Native AI executor |
| edit_role | role_id, optional name, optional color | manage_roles | HIGH | Yes | Native AI executor |
| delete_role | role_id | manage_roles | CRITICAL | Yes | Native AI executor |
| assign_role | user_id, role_id | manage_roles | HIGH | Yes | Checks the per-guild policy for give_role, then uses the validated native executor |
| remove_role | user_id, role_id | manage_roles | HIGH | Yes | Checks the per-guild policy for take_role, then uses the validated native executor |
| timeout_member | user_id, minutes, optional reason | moderate_members | HIGH | Yes | Reuses Moderation.execute_timeout_command after policy checks |
| kick_member | user_id, optional reason | kick_members | CRITICAL | Yes | Reuses SanctionsVoiceCog.execute_kick_command after policy checks |
| ban_member | user_id, optional reason | ban_members | CRITICAL | Yes | Reuses SanctionsVoiceCog.execute_ban_command after policy checks |
| unban_member | user_id, optional reason | ban_members | HIGH | Yes | Reuses SanctionsVoiceCog's ban lookup/unban handler after policy checks |

### Read-only intent and skill families

These are routed separately from the 19 write-action tools. They are not extra entries in ACTION_REGISTRY.

| Read tool | Typical supported requests | Policy/limits to remember |
|---|---|---|
| fetch_server_data | Server name, member count and summary information | Returns names/counts and visible channel information, not hidden channel contents |
| fetch_channels | List server channels | Filters to channels the requester can view |
| fetch_channel | Details of one channel | Target must resolve and be visible to requester |
| fetch_roles | List roles | Bounded list of roles |
| fetch_role | Details of one role | Role must resolve in the current server |
| fetch_members | Bounded member list | Uses gateway member cache; capped at 25 and may be incomplete |
| fetch_member | Member information | Resolves the member in the current guild; the members skill has a management-tier gate in its catalog |
| query_leveling | Level, XP, rank and leaderboard | Self queries and leaderboard queries have separate argument paths; leaderboard is capped at 20 |
| query_streak | Current/best streak or leaderboard | Leaderboard is capped at 20 |
| query_subscription | Requester's subscription or an authorized target's | Viewing another member's subscription requires manage_guild |
| query_analytics | Message, leveling, streak or subscription analytics | Analytics skill requires manage_guild |

Other AI capabilities such as help, conversation summary and attachment analysis use their existing dedicated paths rather than being arbitrary Discord-command invocations.

## Explicit linked-command and policy bridge

prime_ai/linked_commands.py deliberately links only selected actions to existing command behavior/policy:

- Reused handlers: timeout_member → timeout / Moderation; kick_member → kick / SanctionsVoiceCog; ban_member → ban / SanctionsVoiceCog; unban_member → unban / SanctionsVoiceCog.
- Channel mode: set_channel_mode uses ChatJailCog's lock method for read_only and unlock method for open.
- Policy-only links, followed by PRIME's validated native executor: create_channel → create_channel; rename_channel → rename_channel; delete_channel → delete_channel; set_member_nickname → setnick; assign_role → give_role; remove_role → take_role.

A visually similar command is not automatically equivalent to an AI action. For example, role_color is not silently treated as edit_role, and a bulk command such as massban is not silently exposed as ban_member. Read-only policy bridges: fetch_channel → channelinfo, fetch_server_data → serverinfo, fetch_roles → roles, fetch_role → roleinfo, and fetch_member → userinfo. These retain PRIME's bounded read-skill executor while honoring each matching saved Slash command policy. New links must explicitly define argument conversion, exact target semantics, permission requirements, command-policy behavior, action logging, confirmation and verification of the outcome.

## Command inventory by COG

This is the source-level list of command names found in the COG decorators. The current runtime command tree and a real guild's sync state still need to be checked before claiming an item is visible in Discord. Commands not represented in the 19 action tools do not become natural-language actions merely because they appear below.

| COG / module | Commands found |
|---|---|
| cogs.admin_advanced.py | setnick, summon, delwarn, clearwarns, give_role, take_role, strip_roles, role_color, dossier, note, notes, delnote, event_points, reset_points, roleall, removeroleall, massrole, temprole, role_icon, sync_perms, role_members, no_role, bot_list, member_stats, reset_nicks, announce |
| cogs.ai_tools.py | ask_ai, imagine, summarize, transcript, backup_structure |
| cogs.chat_jail.py | clear_user, cleanup, nuke, lock, unlock, lockall, unlockall, hide, show, hideall, showall, emergency, thread_lock, thread_unlock, clean_commands, clean_bots, delete_after, delete_before, block_write, unblock_write, hide_member, show_member, open_chat_member, remove_chat_member, jail, solo_jail, unjail |
| cogs.community.py | ticket_reply, streakreminders (prefix), suggest, poll, reminders, reminder_cancel, setup_counters |
| cogs.economy.py | profile, daily, راتب (prefix; alias يومي), set_leaderboard_channel, give_points, take_points, اعطاء_نقاط (prefix; alias give-points), سحب_نقاط (prefix; alias take-points), work, pay, leaderboard, deposit, withdraw, rob, giveaway |
| cogs.engagement.py | setup_tickets, setup_roles, setup_rules |
| cogs.gaming.py | scrim_open |
| cogs.moderation.py | timeout, untimeout, warn, warnings, unwarn, slowmode, clear, lockdown |
| cogs.rank_commands.py | rank (slash and prefix; prefix aliases are defined by RANK_ALIASES), give_level, take_level, top (slash and prefix; prefix aliases توب and متصدرين) |
| cogs.sanctions_voice.py | kick, ban, unban, tempban, softban, massban, masskick, massmute, unbanall, clearbans, mute, unmute, mutedlist, vmute, vunmute, vkick, deafen, undeafen, vban, vunban, vmove, moveall, disconnectall, vlock, vunlock |
| cogs.security.py | setup_captcha |
| cogs.subscription_commands.py | /subscription grant, /subscription renew, /subscription cancel, /subscription list, /subscription history, /subscription stats, /subscription settings, /subscription template |
| cogs.tools_channels.py | avatar, banner, userinfo, serverinfo, roleinfo, ping, serverheader, roles, emojis, joinposition, mutual, whois, channelinfo, rolemembers, snipe, editsnipe, firstmsg, steal_emoji, enlarge_emoji, remind, countdown, color, encode, decode, quote, timestamp, steal_sticker, create_channel, delete_channel, rename_channel, move_channel, set_topic, clone_channel, create_voice, delete_voice, rename_voice, move_voice, mod_stats, undo_action, security_report |
| cogs.tournaments.py | scrim_split, scrim_teams, map_randomizer, tournament_open, match_record, standings |
| cogs.utilities.py | setup_voice, help, status, radio, stop_radio, ask, say |

Modules such as analytics, levels, announcement reactions, temporary voice, card generation and card image handling also contain listeners, views, workers or supporting functionality; a module's inclusion in the bot does not imply a directly invokable AI tool.

## Confirmed safety and mapping behavior

- AI action steps are schema-validated; unexpected fields, malformed IDs, invalid enums and invalid lengths are rejected.
- Targets are resolved in the current guild. The planner is instructed to use exact IDs from locally built candidates and ask for clarification when the target or meaning is ambiguous.
- Execution re-loads current AI settings, checks access/roles/channels and Discord permissions for both the actor and bot, validates role/member hierarchy, and verifies several write outcomes against Discord state.
- All registered HIGH/CRITICAL AI actions require requester-only confirmation before execution: delete_message, delete_channel, set_channel_mode, edit_role, delete_role, assign_role, remove_role, timeout_member, kick_member, ban_member and unban_member. The dashboard cannot disable this invariant.
- Utilities has a separate saved per-guild command policy for enablement, allowed roles and channels. The shared command-policy bridge applies it only to explicitly mapped action/skill routes above, not to every similarly named command.

## Findings to carry into Phase 2

1. **There is no generic command-to-tool adapter.** The AI action planner sees the 19 ACTION_REGISTRY tools, not the full dynamically loaded command list. Natural-language requests for other commands need intentional, reviewed adapters or an explicit command/shortcut route.
2. **Command-name catalog and AI tool schemas are separate sources of truth.** cogs/command_meta.py contains dashboard/help metadata for a curated subset, Utilities enumerates live loaded commands, while prime_ai_control.ACTION_REGISTRY governs AI writes. They should not be conflated into one registry without accounting for legacy aliases, nested slash groups, different argument types and security policies.
3. **Risk and confirmation deserve a focused review.** set_channel_mode, edit_role, assign_role, remove_role and unban_member are rated HIGH in ACTION_REGISTRY but are outside the fixed confirmation set above. That may be a deliberate product choice, but it must be an explicit decision before broadening AI access.
4. **Some existing high-impact commands are not registered as AI tools.** Examples include massban, masskick, massmute, unbanall, clearbans, roleall, removeroleall, temprole, emergency, nuke, subscription grant/renew/cancel and tournament_open. Do not expose bulk or irreversible commands by merely matching their names: each requires explicit schemas, target resolution, bounded scope, confirmations, audit and outcome tests.
5. **Current source review is not a live integration test.** It does not establish which commands are synchronized in a given guild, whether the live bot has all required intents/permissions, or whether the Discloud deployment is running this exact commit.

## Recommended next step

Build a tested adapter contract for one low-risk read operation and one narrowly scoped write operation first. Generate candidate command metadata from the actual loaded Discord command tree, but keep execution opt-in through explicit reviewed schemas. Add per-action tests for argument conversion, command enabled/role/channel policies, actor/bot permissions, ambiguous targets, mandatory confirmation, idempotency and post-execution verification before adding additional COG commands.


## Phase 2 implementation — reviewed command adapter contract

The reviewed command catalog in `prime_ai/command_catalog.py` reads metadata from the **currently loaded Slash command tree** and emits only 12 explicitly reviewed write-side bridges represented in `prime_ai/linked_commands.py`: `create_channel`, `rename_channel`, `delete_channel`, `setnick`, `give_role`, `take_role`, `timeout`, `kick`, `ban`, `unban`, `lock`, and `unlock`. It never returns callbacks and never makes arbitrary COG commands executable. Risk and the required Discord permission are derived from `ACTION_REGISTRY`, avoiding a second source of truth.

For `lock` and `unlock`, the catalog carries a reviewed fixed-argument hint mapping to `set_channel_mode` (`mode=read_only` and `mode=open` respectively). The planner permits only those exact hint values and continues to use the registered action schema as the only argument contract. Adapter descriptions are contextual metadata, not authorization. Every real action still requires fresh server-side policy, permission, target, hierarchy and confirmation checks as applicable.

Read-only links such as `channelinfo` → `fetch_channel`, `serverinfo` → `fetch_server_data`, `roles` → `fetch_roles`, `roleinfo` → `fetch_role`, and `userinfo` → `fetch_member` remain separately managed by `linked_commands.py` and `ensure_read_skill_command_policy`; they are intentionally not write-action planner entries. Unit tests cover exact allowlist discovery, omitted arbitrary/bulk commands, metadata derivation, fixed mode mapping validation, missing command trees and policy-denial short-circuiting.

The generic adapter gap remains intentional: commands such as `massban`, `nuke`, `emergency`, or arbitrary economy/subscription commands are not exposed just because they exist in Discord.



## Phase 3 implementation — high-impact confirmation invariant

The fixed confirmation registry now includes every action whose registry risk is HIGH or CRITICAL. This closes the previous mismatch where set_channel_mode, edit_role, assign_role, remove_role and unban_member were labeled HIGH but could skip the requester's confirmation. A unit-test invariant compares the full HIGH/CRITICAL set against the confirmation registry so newly added high-impact tools cannot be exposed without an explicit confirmation decision.


## Phase 4 implementation — read-skill command-policy parity

The reviewed read-only policy bridge now also covers the direct equivalents `serverinfo`, `roles`, `roleinfo`, and `userinfo`. PRIME continues to use its bounded read-only skill handlers rather than directly invoking command callbacks, but a saved disabled/role/channel policy on the matching Slash command blocks the corresponding AI read too. Listing all server members and listing all channels remain unlinked because there is no exact one-to-one command equivalent in the reviewed set. Tests verify each mapping and confirm the read does not occur when the matching command is disabled.
