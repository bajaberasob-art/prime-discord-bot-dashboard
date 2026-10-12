# PRIME AI dashboard

## Production surface

The signed-in dashboard's AI/Talk surfaces are rendered by
`dashboard/ai-control.js` and styled by `dashboard/ai-control.css`.
`dashboard/app.js` supplies the authenticated request adapter and guild identity.
The React artifact's home page is not the signed-in AI administration interface.

Presentation is organized into operation, conversation, capabilities, safety,
engine and activity destinations. Standalone Talk retains its own top-level
destination. Existing settings, form field paths, delegated actions, revision
conflicts, memory scopes and safe sandbox behavior remain the backend contracts.

## Magic UI enhancement

The small React island lives in
`artifacts/prime-dashboard/src/ai-island/`. It uses registry Magic UI components
for numbers, entrance transitions and a restrained border effect.

- It is built to `dashboard/ai-magic-island.js`, a self-contained production IIFE.
- AI lazily loads it using the same base URL and cache version as its own script.
- It receives actual loaded counters, not fabricated usage figures.
- Static status tiles remain usable if the optional bundle does not load.
- Roots and the island's injected style are disposed on rerender/navigation.
- Reduced motion disables the moving border and animated counters.
- AI styles use the existing PRIME theme variables; do not apply the island's
  Tailwind reset or theme globally to unrelated dashboard pages.

## Build

From the workspace root:

```sh
PORT=8099 BASE_PATH=/ pnpm --filter @workspace/prime-dashboard run build
pnpm --filter @workspace/prime-dashboard run typecheck
node --check dashboard/ai-control.js
```

The regular artifact build regenerates the island before building the artifact.
The standalone `build:ai-island` command also regenerates just the optional
dashboard enhancement.

The Python server intentionally uses a static-file allowlist. Register any new
dashboard bundle there; do not replace it with unrestricted filesystem serving.
Keep HTML asset versions, the lazy-load URL and service-worker precache URLs in
sync when changing assets.

## Verification without live data changes

`tests.test_prime_ai_dashboard_assets` covers bundle shipping, service-worker
URL consistency, cleanup contracts and the static allowlist. It is included in
the isolated `tests/run_prime_ai.py` runner.

For interactive checks, the existing `tests/dashboard_harness.py` can serve the
real dashboard/backend routes with a fake Discord guild and a temporary SQLite
database. Always disable durable storage and explicitly use a temporary database
when running that harness. Its `/__test_login` route is strictly for that isolated
test server; never add it to the production app or leave the test server public.


## Consumption and reliability center

The Analytics destination now includes guild-scoped calendar-month request and
token totals, optional monthly request/token/USD limits, recent provider failure
reasons, failed action explanations, and deduplicated alerts for request spikes,
repeated failures, and 80%/100% budget thresholds. Settings are stored in the
existing SQLite database and are isolated by guild.

The USD estimate is deliberately labeled as an estimate: administrators must
configure a blended USD-per-million-token rate for their provider/model mix.
A zero rate means "not configured", not free usage. It is not a provider invoice
and does not include separate input/output rates unless the operator's blended
rate accounts for them. Alerts are evaluated as request events are recorded and
are visible in the authenticated dashboard; no live-host deployment or external
Discord notification delivery is implied by the repository change.
