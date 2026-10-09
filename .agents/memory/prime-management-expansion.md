---
name: PRIME management expansion
description: Project owner's constraints for expanding PRIME Discord administration.
---

Internal PRIME AI reorganization is allowed, including moving, merging and splitting modules. Preserve all externally visible features, commands, dashboard, settings, personality and expected behavior. Never drop or recreate the database; database changes must be additive and compatible with existing records.

**Why:** The owner explicitly authorized a substantive internal refactor on 2026-10-08, on the condition that the existing user experience and data remain intact.

**How to apply:** Treat existing integration interfaces and behavioral tests as compatibility boundaries. Fix internal performance/lifecycle defects without redesigning the UI, changing prompts or introducing new product behavior.

The AI dashboard presentation may be radically redesigned; the previous UI-preservation restriction applied to the internal refactor, not to a separately requested redesign.

**Why:** The owner explicitly requested a complete creative, professional AI dashboard redesign using Magic UI on 2026-10-08.

**How to apply:** Keep changes scoped to AI/Talk surfaces and preserve their working controls, permission gates, save/conflict handling and stored data. Do not redesign unrelated dashboard destinations without a request.

The owner chose a per-server PRIME role map for Admin, Moderator, and Staff. Detect the server owner and Discord Administrator from Discord itself. An empty map preserves current behavior; a configured map only adds restrictions and never bypasses Discord permissions, server policy, or actor/target/bot role-hierarchy checks.

**Why:** The project owner explicitly required that current systems and stored data remain intact and that enhancements not break existing features.

**How to apply:** Before adding admin coverage, map the current slash-command, dashboard, AI, and persistence paths; reuse them, keep changes incremental, and verify affected existing behavior. Apply the role map as an additive shared gate, never as a source of Discord permissions.

PRIME Talk is a distinct top-level dashboard destination. Keep conversation activation, access rules, response/context/personality settings, retention, and the permissions for actions requested through Talk together there. Keep AI operations, audit history, and non-conversation controls in the AI area.

**Why:** The owner requested a dedicated Talk menu containing its editable permissions and settings.

**How to apply:** Add future Talk-specific controls to that destination without duplicating their underlying settings. Preserve server-side permission checks and mandatory safeguards for any Discord actions.

Temporary voice emergency locks must restore the channel's previous `@everyone` visibility and connect overwrites exactly when unlocked; do not infer an open state from the configured privacy mode.

**Why:** Emergency locking temporarily overlays restrictions on top of existing public, private, or manually locked room permissions. Guessing at unlock can expose a room that was restricted before the emergency.

**How to apply:** Capture the prior visibility and connect overwrite values when the emergency lock is applied, and restore those values when it is cleared. Keep ordinary lock/privacy actions unavailable until the emergency state is cleared.

Temporary voice rooms stay open when empty, including across bot restarts. Discord room controls are limited to the room owner and server administrators; either may delete using the confirmation step. Dashboard force deletion remains administrator-only.

**Why:** The owner explicitly set the empty-room lifecycle and later clarified on 2026-10-09 that server administrators may use Discord room controls, including Delete.

**How to apply:** Do not add empty-room cleanup to voice events, startup restoration, legacy adoption, or background heartbeats. Recheck owner/admin authorization for every Discord action and at delete confirmation; keep dashboard force deletion behind administrator authorization.

Temporary voice panel-layout migrations need a persisted, server-managed version separate from the button configuration.

**Why:** A button migration can be saved before the bot refreshes its existing Discord messages, so later startups cannot infer from the button list alone that panels still need updating.

**How to apply:** Bump the layout version when a rollout must refresh existing central or room panels, keep the marker out of dashboard writes, and isolate failures per message.

Temporary voice Discord panels should use the uploaded banner image above icon-only controls, without a written explanation of the buttons.

**Why:** The owner explicitly requested a graphic that explains the controls instead of button-guide text.

**How to apply:** Keep the panel title and uploaded image, omit explanatory embed descriptions and separate guide embeds, and make the image upload reliable for common mobile photos.

Temporary voice controls should prefer a coherent, usable PRIME/server custom-emoji family, with distinct Unicode fallbacks for every action.

**Why:** The owner explicitly requested a dark PRIME identity with purple, cyan, and neon accents, while ensuring premium or unavailable emojis never break controls.

**How to apply:** Resolve custom emojis from the current guild at render time, use them only when a sufficiently complete matching family is usable, avoid persisting emoji IDs, and bump the panel layout version when icons change.

PRIME backups are scoped to one selected server: include that server's persisted records and media, but exclude other servers, account-personal preferences, and shared global definitions.

**Why:** The owner requested that backup and restore cover all persisted PRIME data for the selected server without crossing server boundaries.

**How to apply:** When changing backup contents, follow guild ownership through parent records where needed; never include personal or global data in a guild snapshot.
