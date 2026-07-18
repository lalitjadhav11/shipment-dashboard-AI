# Shipment Dashboard AI

A shipment tracking dashboard, backed by a customer + shipment data model, that
will be enabled with an AI agent (**Shipment Journey Summary Chat**) for
natural-language search over large shipment datasets.

Full design details live in
[`Phase1_Shipment_Customer_Design_Document.docx`](Phase1_Shipment_Customer_Design_Document.docx).

## Phase 1 scope

Phase 1 deliberately limits the data model to two primary entities —
**customers** and **shipments** — plus three minimal support tables needed to
power the AI chat feature later:

| Table               | Purpose                                                              |
|----------------------|-----------------------------------------------------------------------|
| `customers`          | Organization/account holder                                          |
| `shipments`          | Tracking-ID-centric shipment record (package, customs, pickup, delivery folded in) |
| `tracking_events`    | Append-only journey log, auto-logged on every `current_status` change |
| `shipment_issues`    | One row per delay/failed-delivery/RCA incident                       |
| `shipment_chat_log`  | Audit log of every AI chat interaction (question, context, answer, confidence) |

A shipment moves through a 13-stage happy path (plus 4 terminal-failure
states) from `LABEL_CREATED` to `DELIVERED`, with `CUSTOMS_HOLD` /
`CUSTOMS_CLEARED` as international-only sub-stages. See the design document
for the full stage reference, the AI chat's grounded-context design
(`v_shipment_journey_summary`), and the issue/delay taxonomy.

### Implementation status

| Piece                                                             | Status |
|---------------------------------------------------------------------|--------|
| 5-table schema, enums, triggers, `v_shipment_journey_summary` view   | ✅ Done (`db/init/01_phase1_schema.sql`) |
| 10 dashboard summary views (headline KPIs, breakdowns, trends)       | ✅ Done (`db/init/02_dashboard_summary_views.sql`) |
| Scenario-based data generator (25k shipments / 800 customers)        | ✅ Done (`03_generate_phase1_data.py`, run by the `seeder` service) |
| Backend API (FastAPI) — hello world, health check, KPI reads         | ✅ Done, boilerplate (`backend/main.py`) |
| Frontend (React) — hello world screen, pings backend                 | ✅ Done, boilerplate (`frontend/src`) |
| **AI Shipment Journey Summary chat** (the feature the schema supports) | ⏳ Not yet implemented — `shipment_chat_log` and `v_shipment_journey_summary` are in place to support it |
| Real dashboard UI (charts/tables over the 10 summary views)          | ⏳ Not yet implemented |

## Architecture

Three-tier stack, fully containerized:

| Tier      | Tech             | Container           | Port (host) |
|-----------|------------------|----------------------|-------------|
| Frontend  | React (Vite)     | `shipment_frontend`  | `3000`      |
| Backend   | Python (FastAPI) | `shipment_backend`   | `8000`      |
| Database  | PostgreSQL 16    | `shipment_db`        | `5432`      |
| Seeder    | Python (one-shot)| `shipment_seeder`    | —           |

The database schema is created **automatically** on first boot (Postgres
native `docker-entrypoint-initdb.d` init scripts), and the dataset is loaded
by a one-shot, idempotent **seeder**.

## Quick start (Docker)

Only Docker Desktop (or Docker Engine + Compose v2) is required — no Python,
Node, or Postgres installed locally.

```bash
docker compose up --build
```

Then open:

- **Frontend (Hello World):** http://localhost:3000
- **Backend API root:** http://localhost:8000
- **API docs (Swagger):** http://localhost:8000/docs
- **Headline KPIs:** http://localhost:8000/api/summary

The first boot takes a few minutes because the seeder generates and loads the
full **25,000-shipment** dataset. Watch progress with:

```bash
docker compose logs -f seeder
```

Other useful commands:

```bash
# Run in the background
docker compose up --build -d

# Stop (keep the data volume)
docker compose down

# Stop and wipe the database (forces a fresh schema + reseed next time)
docker compose down -v

# Faster first boot for local dev — smaller dataset
SEED_SHIPMENTS=2000 SEED_CUSTOMERS=200 docker compose up --build

# Rebuild a single service after changing its code
docker compose up --build backend

# Tail logs for a specific service
docker compose logs -f backend
```

See [`DOCKER_README.md`](DOCKER_README.md) for the full breakdown of how the
database init/seed flow works, configuration options, and how to export the
stack to another machine.

## Project layout

```
.
├── docker-compose.yml              # orchestrates db, seeder, backend, frontend
├── .env.example                    # optional config overrides
├── Phase1_Shipment_Customer_Design_Document.docx   # requirement / design doc
├── sample_realtime_summary_report.json
├── db/init/
│   ├── 01_phase1_schema.sql        # DDL: enums, 5 tables, triggers, journey view
│   └── 02_dashboard_summary_views.sql   # 10 dashboard summary views
├── seeder/
│   ├── Dockerfile
│   ├── seed.py                     # waits for DB, idempotency guard, runs generator
│   └── 03_generate_phase1_data.py  # scenario-based data generator
├── backend/
│   ├── Dockerfile
│   └── main.py                     # FastAPI: /, /health, /api/summary, ...
└── frontend/
    ├── Dockerfile                  # multi-stage: Vite build -> nginx
    ├── nginx.conf                  # serves SPA, proxies /api -> backend
    └── src/                        # React Hello World screen
```

## Backend endpoints

| Method | Path                    | Description                              |
|--------|-------------------------|-------------------------------------------|
| GET    | `/`                     | Hello World                               |
| GET    | `/api/hello`            | Hello World (via frontend `/api` proxy)   |
| GET    | `/health`               | Liveness + DB connectivity                |
| GET    | `/api/summary`          | `v_dashboard_headline` — 10 KPIs          |
| GET    | `/api/status-breakdown` | `v_status_breakdown`                      |

## Configuration

All settings have defaults; override by copying `.env.example` to `.env`:

| Variable            | Default         | Purpose                               |
|----------------------|-----------------|----------------------------------------|
| `POSTGRES_USER`     | `postgres`      | DB user                                |
| `POSTGRES_PASSWORD` | `postgres`      | DB password                            |
| `POSTGRES_DB`       | `shipdb_phase1` | DB name                                |
| `SEED_SHIPMENTS`    | `25000`         | Rows to generate (lower = faster dev)  |
| `SEED_CUSTOMERS`    | `800`           | Customer count                         |
| `SEED_RANDOM_SEED`  | `42`            | Deterministic generation seed          |
