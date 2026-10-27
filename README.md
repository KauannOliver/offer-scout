# Offer Scout

Offer Scout is a local platform for discovering and evaluating Mercado Livre affiliate offers, managing an editorial queue, and delivering approved content to Telegram. It combines a React dashboard with a Python API, background workers, and an authenticated browser session. The application itself still uses the original Money Extra and Radar do Julius names in parts of the interface and messages.

## How it works

1. A browser worker uses a manually authenticated Mercado Livre session to discover offers and coupons.
2. The application normalizes observations, retains price history, calculates an explainable score, and builds a publication queue.
3. The dashboard lets an operator inspect offers, settings, editorial decisions, and publication history.
4. Before eligible affiliate offers are sent, the application revalidates their facts and link. Groq can generate copy, with a deterministic fallback.
5. Telegram delivery defaults to a private administrative smoke mode. Public affiliate delivery requires explicit configuration and checks.

Mercado Livre is the only implemented marketplace. Availability and behavior depend on external services, account eligibility, and changes to their interfaces. Automated browsing may require manual login and challenge resolution. This project is not affiliated with Mercado Livre, Telegram, or Groq.

## Architecture

- **Backend:** Python 3.12, FastAPI, SQLAlchemy, Alembic, Celery and Playwright.
- **Frontend:** React, TypeScript, Vite and Tailwind CSS.
- **Infrastructure:** PostgreSQL, Redis, Docker Compose, and an isolated browser worker with noVNC.

See [architecture](docs/ARCHITECTURE.md) for component boundaries and state transitions.

## Requirements and local setup

Docker Desktop with Compose is the recommended environment. An affiliate account and manual browser login are needed for actual discovery. Telegram and Groq credentials are needed for their respective integrations; never commit them or the browser profile.

```powershell
Copy-Item .env.example .env
# Edit .env locally: set APP_SECRET_KEY and ADMIN_PASSWORD, and configure the integrations you need.
docker compose up -d --build
docker compose ps
```

Use a long random secret and a strong administrator password. Keep `TELEGRAM_DELIVERY_MODE=private_smoke` and both publication switches disabled while testing. The API, dashboard and noVNC ports are bound to loopback by default; the example is intended for local use, not as an internet-facing deployment.

- Dashboard: http://127.0.0.1:3080
- API documentation: http://127.0.0.1:8000/docs
- Browser login: http://127.0.0.1:7900/vnc.html?autoconnect=1&resize=scale
- Health: http://127.0.0.1:8000/health

Sign in to Mercado Livre manually in the browser. Do not enter its credentials into the application. Application data, browser profile and backups live in Docker volumes; protect and back them up appropriately. Changing the configured administrator password does not automatically update an existing user in the database.

## Testing

The isolated backend suite uses a temporary PostgreSQL database and Redis, blank external credentials, and disabled affiliate publishing. It does not use the production `.env` or browser profile.

```powershell
docker compose -f compose.test.yml run --rm tests
cd frontend
npm ci
npm run lint
npm test
npm run build
```

Browser tests are in `frontend/e2e`; they require the isolated web and API services and, for product-history scenarios, appropriate fixture data. Integration tests do not prove that external accounts, marketplace selectors or Telegram delivery currently work. External smoke scripts can contact real services or send messages; run them only deliberately with suitable credentials and destinations.

To stop isolated services after testing, run `docker compose -f compose.test.yml down` from the project root. This command targets the separate verification project.

## Repository layout

- `backend/app`: API, domain, providers, services and workers.
- `backend/alembic`: database migrations.
- `backend/tests`: unit and isolated integration tests and sanitized HTML fixtures.
- `frontend/src`: dashboard and API client.
- `compose.test.yml`: isolated test services.

## License

No license has been granted for this repository. All rights are reserved by the copyright holder.
