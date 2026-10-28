# Snapshot verification

Checks recorded in the isolated source copy on 2026-10-28. These results are not a production certification.

| Directory | Command | Result |
| --- | --- | --- |
| `frontend` | `npm ci --ignore-scripts --no-audit --no-fund` | Passed |
| `frontend` | `npm run lint` | Passed |
| `frontend` | `npm test` | Passed |
| `frontend` | `npm run build` | Passed |

The frontend lint, two frontend tests and production build passed. Backend integration tests requiring isolated PostgreSQL/Redis, browser login, marketplace discovery and Telegram delivery were not rerun.

## Publication boundaries

- Operational sources remained read-only; only isolated copies were prepared.
- No corporate service, real-record import, live notification or production database was invoked.
- No deployment command, deploy workflow or new hosting integration is part of this update. Included Vercel configurations disable Git-driven deployments.
- New source imports exclude local environments, databases, generated outputs and operational documents.
- Existing Git history is preserved. Historical public names or assets may remain in old commits; current snapshots use neutral public presentation.

## Production npm dependency audit — offer-scout/frontend

`npm audit --omit=dev --json` reported 0 critical, 2 high, 0 moderate, 0 low findings in the installed dependency tree on 2026-10-28. Counts include transitive packages and do not establish exploitability in this application.

| Package | Severity | Direct dependency |
| --- | --- | --- |
| `source-map-js` | high | No |
| `vite` | high | Yes |

These advisories remain a maintenance item. This documentation/publication task did not perform dependency upgrades or validate a production security posture.
