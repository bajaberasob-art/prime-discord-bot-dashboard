# Python Discord Bot

A starter Discord bot built with Python and `discord.py`. It currently includes:

- `/ping` for gateway latency
- `/hello` for a user greeting
- `/serverinfo` for basic server details
- Moderation commands: `/timeout`, `/untimeout`, `/warn`, `/warnings`, `/clear`, and
  `/lockdown`
- Automatic invite detection, mass-mention protection, duplicate-message detection, and
  timed spam protection
- CAPTCHA verification, account-age gating, scam-link blocking, and anti-nuke audit-log
  monitors
- Slash-command syncing for either one development server or all servers
- Structured logs and clear startup errors

## Run it

The bot reads its token from the `DISCORD_BOT_TOKEN` Replit Secret.

```bash
python main.py
```

The configured project workflow runs `python main.py` automatically.

## Dashboard access

The Discord bot serves its dashboard on port `8099`, and the API service exposes
it through the public project domain at `/api/dashboard/`.

Open:

```text
https://<REPLIT_DEV_DOMAIN>/api/dashboard/
```

The root URL redirects there automatically. Select **تسجيل الدخول عبر ديسكورد**
and sign in with an account that owns the server or has the Administrator
permission.

For Discord OAuth login, add this exact callback URL under **OAuth2 → Redirects**:

```text
https://<REPLIT_DEV_DOMAIN>/api/dashboard/api/auth/callback
```

The dashboard uses `CLIENT_ID`, `CLIENT_SECRET`, and `REDIRECT_URI` for OAuth
login. `CLIENT_SECRET` must be stored as a Replit Secret; never put it in the
bot invite URL or source code.

## Discord setup

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Open **Bot**, create the bot user, and copy its raw token into the `DISCORD_BOT_TOKEN` Replit Secret. Do not use the Application ID, public key, client secret, or an OAuth URL.
3. In **Bot → Privileged Gateway Intents**, enable **Server Members Intent** and
   **Message Content Intent**, then save the change. The security cog uses the members
   intent for account-age gating and ban/join events, and the message-content intent for
   scam-link blocking. Presence Intent is not required.
4. Open **OAuth2 → URL Generator**.
5. Select the `bot` and `applications.commands` scopes.
6. Select the permissions the bot needs, including **View Channel**, **Send Messages**,
   **Embed Links**, **Add Reactions**, **Read Message History**, **Manage Messages**,
   and **Manage Channels**. The community commands use Embed Links for suggestions and
   polls, Add Reactions for suggestion voting, and Manage Channels for live counters.
   Add these security permissions where the corresponding feature is enabled:

   | Permission | Used by |
   | --- | --- |
   | View Audit Log | Anti-nuke channel, role, and mass-ban monitors |
   | Moderate Members | Scam-link timeout |
   | Manage Roles | CAPTCHA role assignment and anti-nuke quarantine |
   | Kick Members | Account-age gate for accounts younger than three days |
   | Mention Everyone | Emergency anti-nuke alert in the system channel |

   Do not grant these permissions to unrelated bots or roles. The bot must also be
   above the verified CAPTCHA role and any roles it may quarantine.
7. Open the generated URL and add the bot to your server.

Existing installations must grant **Embed Links** and **Add Reactions** to the bot role
in the channels where community commands are used, or reauthorize the bot with an
updated OAuth URL containing those permissions.

For fast slash-command updates during development, add a non-secret `DISCORD_GUILD_ID`
environment variable containing the server ID. Without it, commands sync globally, which
can take longer to appear.

After the bot connects and commands finish syncing, an administrator can run
`/setup_captcha` and choose the role that verified members should receive. The CAPTCHA
button remains active while the bot is running and each server's button is bound to its
own selected role.

## Extend the bot

Add additional slash commands next to the starter commands in the loaded cogs. Keep
privileged intents disabled unless a feature explicitly needs them, and request only the
permissions required by that feature. The security and moderation cogs are loaded from
`main.py` before slash commands are synced.

## PRIME Leveling

The leveling system is managed from the authenticated dashboard at `/api/dashboard/`. PRIME separates lifetime leveling from scheduled Daily/Weekly/Monthly TOP periods.

Manual commands include `/rank`, `/lvl`, `لفل`, and `rank`; `/rank` defaults to a single PRIME PNG card. `/top` and `توب` show the lifetime server leaderboard with Text/Voice modes. Scheduled Daily/Weekly/Monthly TOP use only XP earned inside their configured period and do not reset lifetime XP or levels.

Dashboard notification settings are stored in PRIME controls and are applied through the leveling settings API. Legacy leveling fields remain as compatibility storage for existing installations.

Runtime SQLite files are ignored by Git via `*.db`, `*.sqlite`, and `*.sqlite3`. Existing deployments that intentionally use a tracked legacy database should migrate that data to their persistent `DB_PATH` before removing the legacy file from the repository; do not delete a live production database blindly.


## Discloud deployment verification

After a change is merged into `main`, verify the deployed revision in the Discloud app details before testing dashboard features. The latest GitHub commit and the latest deployed commit are separate states; a successful rebuild or restart alone does not prove that the newest revision was deployed. If the displayed revision is stale, refresh the GitHub repository list and inspect the deployment event/log before retrying the build.


## Profile customization store administration

The profile-store API is served by the existing authenticated dashboard backend. Catalog writes require the dashboard's CSRF/session checks, the live admin management tier for the selected guild, and bot-owner authorization. The application owner is detected from discord.py when available; otherwise configure one of these comma-separated environment variables with Discord **user IDs**: `PROFILE_STORE_ADMIN_IDS`, `BOT_OWNER_IDS`, or `BOT_OWNER_ID`.

Uploaded catalog images are validated server-side and accept PNG, JPEG/JPG, WebP, and bounded animated GIFs. GIF uploads preserve animation with a maximum of 60 frames, 32 million aggregate frame pixels, and a 5MB sanitized output limit; APNG and animated WebP remain rejected. Static files are re-encoded to strip source metadata and stored outside the public dashboard directory beside the configured database volume, under `profile_store_assets/catalog/`. Set `PROFILE_STORE_ASSET_DIR` to an alternate private persistent directory if needed; paths inside `dashboard/` are rejected. The profile-card output remains a static PNG and uses the first GIF frame.

The custom-upload toggle and price are global store settings, but the member-facing custom-upload workflow is not yet wired into the public storefront routes. The shop does have a separate `user_profile_gem_wallets` table and transactional gem debiting in the existing purchase service; do not treat the custom-upload toggle as proof that the member upload purchase flow is implemented.

## PRIME AI: strict zero-cost mode

PRIME AI is configured for a **free-only policy**. The request path must not call paid Gemini, xAI/Grok API, Mistral paid endpoints, or any other paid model. A free tier is quota-limited and can change; PRIME must stop when no verified free route is available rather than silently switch to a paid model.

### Text chat

- Set `OPENROUTER_API_KEY` in Discloud environment variables to enable the text provider.
- By default, PRIME uses `openrouter/free`, which routes to eligible free models.
- Optional `OPENROUTER_FREE_MODEL` overrides are accepted only when the value is exactly `openrouter/free` or ends with `:free`. Paid model IDs are rejected.
- `PRIME_AI_ALLOW_OPENROUTER_FALLBACK` is no longer required. With the key present, free-only text routing is used for CHAT, ASSISTANT, and ACTION planning; action execution still passes server-side permissions, policy validation, and required confirmation.
- If OpenRouter has no eligible free model, quota is exhausted, or the key is missing, PRIME returns an unavailable message. It must not try Gemini or a paid fallback.
- OpenRouter's free plan has published request limits (currently listed as 50 requests/day); limits and available free models can change. Do not add credits or enable paid models if the policy is zero spend.

### Images

Remote image generation is **disabled** in strict zero-cost mode because the currently wired Gemini and Pollinations endpoints cannot be guaranteed to remain zero-cost for every account and quota state. The command returns an explanatory message without calling either provider. Do not enable `PRIME_AI_ALLOW_PAID_IMAGE_GENERATION` or rely on `POLLINATIONS_API_KEY` to override this policy.

### Provider keys and privacy

Keep all provider keys in Discloud environment variables. Never paste them into source code or Discord. Requests sent to OpenRouter are processed by a third party; do not send confidential content unless permitted by your server policy. No free tier is unlimited or guaranteed to be continuously available.

### Validation status

These changes have not been tested yet, as requested. Run the full tests and provider-call audit only after implementation is complete. A repository change is not proof of a successful deployment; verify the deployed revision separately before claiming it is live.
