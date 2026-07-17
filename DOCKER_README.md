# Shipment Dashboard — Dockerized Stack (Phase 1)

A three-tier system, fully containerized and orchestrated with Docker Compose:

| Tier      | Tech             | Container          | Port (host) |
|-----------|------------------|--------------------|-------------|
| Frontend  | React (Vite)     | `shipment_frontend`| `3000`      |
| Backend   | Python (FastAPI) | `shipment_backend` | `8000`      |
| Database  | PostgreSQL 16    | `shipment_db`      | `5432`      |
| Seeder    | Python (one-shot)| `shipment_seeder`  | —           |

The frontend shows a **Hello World** screen and pings the backend. The backend is
a small FastAPI boilerplate wired to the DB. The database schema is created
**automatically**, and the dataset is loaded by a one-shot **seeder** using the
project's own data generator.

---

## Quick start (fresh machine)

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

The seeder exits `0` when done; `backend` and `frontend` keep running.

To stop (keeping data):

```bash
docker compose down
```

To stop **and wipe the database** (forces a fresh schema + reseed next time):

```bash
docker compose down -v
```

---

## How database creation works ("appropriate technique")

Two clean, standard mechanisms — no manual steps:

1. **Schema + views (DDL) — Postgres native init.**
   `db/init/` is mounted into the Postgres image's
   `/docker-entrypoint-initdb.d/`. On the **first** boot with an empty data
   volume, Postgres runs the files in order:
   - `01_phase1_schema.sql` — enums, 5 tables, `updated_at` + auto journey-stage
     triggers, and the `v_shipment_journey_summary` context view.
   - `02_dashboard_summary_views.sql` — the 10 dashboard summary views.

2. **Data (DML) — one-shot idempotent seeder.**
   The `seeder` service waits for the DB to be healthy, then runs
   `03_generate_phase1_data.py` (the project's own generator) to produce a
   fully self-consistent, scenario-covered dataset and bulk-load it.
   It is **idempotent**: if the `shipments` table already has rows, it skips —
   so re-running `docker compose up` never re-truncates a loaded dataset.

This separation (DDL declaratively baked into the image init, DML in a
guarded job) is what makes the stack **portable**: on any new machine, a single
`docker compose up` produces an identical, fully-populated database.

---

## Exporting / importing images to another machine

The stack builds portable images. To move it without a network:

```bash
# On the source machine
docker compose build
docker save shipment-dashboard-ai-backend shipment-dashboard-ai-frontend \
            shipment-dashboard-ai-seeder postgres:16-alpine -o shipment-stack.tar

# On the target machine
docker load -i shipment-stack.tar
docker compose up            # schema auto-creates, seeder populates
```

> Image names follow `<project-folder>-<service>`. Run `docker compose images`
> to confirm the exact names on your machine.

---

## Configuration

All settings have defaults; override by copying `.env.example` to `.env`:

| Variable            | Default        | Purpose                                  |
|---------------------|----------------|------------------------------------------|
| `POSTGRES_USER`     | `postgres`     | DB user                                  |
| `POSTGRES_PASSWORD` | `postgres`     | DB password                              |
| `POSTGRES_DB`       | `shipdb_phase1`| DB name                                  |
| `SEED_SHIPMENTS`    | `25000`        | Rows to generate (lower = faster dev)    |
| `SEED_CUSTOMERS`    | `800`          | Customer count                           |
| `SEED_RANDOM_SEED`  | `42`           | Deterministic generation seed            |

Faster first boot for development:

```bash
SEED_SHIPMENTS=2000 SEED_CUSTOMERS=200 docker compose up --build
```

---

## Project layout

```
.
├── docker-compose.yml              # orchestrates db, seeder, backend, frontend
├── .env.example                    # optional config overrides
├── db/init/
│   ├── 01_phase1_schema.sql        # DDL: enums, 5 tables, triggers, journey view
│   └── 02_dashboard_summary_views.sql
├── seeder/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── seed.py                     # waits for DB, idempotency guard, runs generator
│   └── 03_generate_phase1_data.py  # the project's data generator
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py                     # FastAPI: /, /health, /api/summary, ...
└── frontend/
    ├── Dockerfile                  # multi-stage: Vite build -> nginx
    ├── nginx.conf                  # serves SPA, proxies /api -> backend
    ├── package.json
    └── src/                        # React Hello World screen
```

---

## Backend endpoints

| Method | Path                    | Description                              |
|--------|-------------------------|------------------------------------------|
| GET    | `/`                     | Hello World                              |
| GET    | `/api/hello`            | Hello World (via frontend `/api` proxy)  |
| GET    | `/health`               | Liveness + DB connectivity              |
| GET    | `/api/summary`          | `v_dashboard_headline` — 10 KPIs        |
| GET    | `/api/status-breakdown` | `v_status_breakdown`                     |

---

## Note on the schema file

The design document references a companion `01_phase1_schema.sql` that was not
included in the source folder. It has been **reconstructed** in
`db/init/01_phase1_schema.sql` from the Entity Field Dictionary in
`Phase1_Shipment_Customer_Design_Document.docx` and the exact column/enum/trigger
contracts used by `03_generate_phase1_data.py`, then verified by loading the full
generator dataset and all dashboard views without error.
```
