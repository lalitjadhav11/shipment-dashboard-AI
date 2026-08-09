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
| **AI Shipment Journey Summary chat — v0** (deterministic, zero-LLM backbone) | ✅ Done (`backend/chat/`) — intent classification, entity extraction, schema scoping, template SQL, guardrail validation, and response formatting all run without an LLM call. **28 templates** covering every dashboard view, mix-and-match filters (by customer, status, package type, delivery type), single-shipment identity lookups (customer, package/service details, route, schedule, delivery attempts), and their reverse (shipments by location, package size, pickup date). See [`AGENTIC_RAG_ARCHITECTURE.md`](AGENTIC_RAG_ARCHITECTURE.md) §10, §12 |
| **Frontend AI chat panel** | ✅ Done — the top search bar (`TopBar.jsx`) submits to `/api/chat` via a dedicated `agentApi.js` service module (SSE-over-POST, hand-parsed since `EventSource` is GET-only) and renders the answer in an `AiInsightPanel` below the KPI row |
| **AI chat v1** (LLM SQL fallback + LLM response synthesis) | ✅ Done and **live-verified end-to-end** with a local Ollama model — a genuine `GROUP BY` question ("group shipments by package type and show how many are delayed") got a correctly-drafted query, guardrail-validated, executed, and synthesized into a natural-language answer with follow-up suggestions. Supports three interchangeable providers via `AGENT_LLM_PROVIDER` — `anthropic` (cloud, paid), `gemini` (cloud, free tier), or `ollama` (local, no API cost) — switching is one env var, no code change. See `AGENTIC_RAG_ARCHITECTURE.md` §9 |
| Real dashboard UI (charts/tables over the 10 summary views)          | ⏳ Not yet implemented |
| Backend API (FastAPI) — hello world, health check, KPI reads, paginated shipment listing | ✅ Done (`backend/main.py`) |
| Frontend (React) — reporting dashboard: KPI row + searchable/filterable/paginated shipment table | ✅ Done (`frontend/src`) |
| Shipment detail drawer — click a tracking ID for status, customs, delay info, and a Google Maps journey view (stops + flight/truck legs) | ✅ Done (`frontend/src/components/ShipmentDetailDrawer.jsx`, `JourneyMap.jsx`) |
| **AI Shipment Journey Summary chat** (the feature the schema supports) | ⏳ Not yet implemented — `shipment_chat_log` and `v_shipment_journey_summary` are in place to support it |
| **LLM data augmentation** — Gemini invents new shipment scenarios (city pair, package, status, delay reason) every `docker compose up`, appended on top of the base 25k | ✅ Done (`seeder/llm_augment_shipments.py`, run by the `llm_augmenter` service) |

## Architecture

Three-tier stack, fully containerized:

| Tier          | Tech             | Container                 | Port (host) |
|---------------|------------------|----------------------------|-------------|
| Frontend      | React (Vite)     | `shipment_frontend`        | `3000`      |
| Backend       | Python (FastAPI) | `shipment_backend`         | `8000`      |
| Database      | PostgreSQL 16    | `shipment_db`               | `5432`      |
| Seeder        | Python (one-shot)| `shipment_seeder`           | —           |
| LLM augmenter | Python (one-shot)| `shipment_llm_augmenter`    | —           |

The database schema is created **automatically** on first boot (Postgres
native `docker-entrypoint-initdb.d` init scripts), and the dataset is loaded
by a one-shot, idempotent **seeder**. On top of that, the **LLM augmenter**
runs every single `docker compose up` (it is *not* idempotent) — it asks
Gemini to invent `SYNTHETIC_SHIPMENTS_COUNT` (default 250) new shipment
scenarios in the same style as the existing data, then builds and inserts
full rows (IDs, timestamps, hub routing, journey timeline) around those
scenarios in Python. The dataset grows by that amount on every restart if
`GEMINI_API_KEY` is set; it's a no-op (skipped with a log line) if it's
blank. See `seeder/llm_augment_shipments.py` for the full design rationale.

## Quick start (Docker)

Only Docker Desktop (or Docker Engine + Compose v2) is required — no Python,
Node, or Postgres installed locally.

```bash
docker compose up --build
```

Then open:

- **Frontend (reporting dashboard):** http://localhost:3000
- **Backend API root:** http://localhost:8000
- **API docs (Swagger):** http://localhost:8000/docs
- **Headline KPIs:** http://localhost:8000/api/summary
- **Paginated shipments:** http://localhost:8000/api/shipments?page=1&page_size=50

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
│   ├── main.py                     # FastAPI: /, /health, /api/summary, ...
│   └── chat/                       # AI chat pipeline (v0 — see AGENTIC_RAG_ARCHITECTURE.md)
│       ├── router.py               # POST /api/chat (SSE), GET /api/chat/history
│       ├── schema_loader.py        # loads 02_phase1_agentic_schema.json + embedding indexes
│       ├── intent.py               # Stage 1 — intent classifier
│       ├── entities.py             # Stage 2 — entity extractor
│       ├── schema_scope.py         # Stage 3 — schema scoper
│       ├── sql_templates.py        # Stage 4a — template SQL fill
│       ├── guardrails.py           # Stage 5 — sqlglot SQL allow-list validator
│       ├── executor.py             # Stage 6 — read-only query execution
│       ├── respond_template.py     # Stage 7 v0 — template response formatter
│       ├── pipeline.py             # orchestrates Stages 1-7
│       ├── audit.py                # writes shipment_chat_log
│       └── db.py, trace.py         # read-only DB helper, SSE event formatting
└── frontend/
    ├── Dockerfile                  # multi-stage: Vite build -> nginx
    ├── nginx.conf                  # serves SPA, proxies /api -> backend
    └── src/                        # React reporting dashboard (KPI row + shipment table)
```

## Backend endpoints

| Method | Path                    | Description                              |
|--------|-------------------------|-------------------------------------------|
| GET    | `/`                     | Hello World                               |
| GET    | `/api/hello`            | Hello World (via frontend `/api` proxy)   |
| GET    | `/health`               | Liveness + DB connectivity                |
| GET    | `/api/summary`          | `v_dashboard_headline` — 10 KPIs          |
| GET    | `/api/status-breakdown` | `v_status_breakdown`                      |
| POST   | `/api/chat`              | AI Shipment Journey Summary chat — streams Server-Sent Events, one per pipeline stage, ending with `answer_ready`. Send `{"query": "..."}`. Falls back to Stage 4b (LLM) when no template matches, if `ANTHROPIC_API_KEY` is set — see status table above. |
| GET    | `/api/chat/history`      | Read `shipment_chat_log` for QA (optional `?tracking_id=`) |
| GET    | `/api/chat/llm-usage`    | `v_chat_llm_usage` — template-vs-LLM traffic split by provider, with avg latency and guardrail-rejection counts |

| GET    | `/api/shipments`        | Paginated shipment listing — `page`, `page_size` (default 50), `search`, `status`, `delivery_type`, `is_international`, `customs_status`, `sort_by`, `sort_dir` |
| GET    | `/api/shipments/{tracking_id}` | `v_shipment_journey_summary` for one shipment — status, customs, delay, open issues, full journey timeline. Backs the detail drawer + journey map. |


`/api/chat`'s intermediate "thinking" trace (intent, extracted entities, scoped
schema, generated SQL, validation, execution) is a **privilege**, gated by an
`X-User-Role` header (`SUPPORT`/`OPS`/`ADMIN`) — this header is a placeholder
until a real auth/session system exists (see `router.py`). Without it, only
the final `answer_ready` event is streamed. Try it:

```bash
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" -H "X-User-Role: OPS" \
  -d '{"query": "Why is my package <a real tracking_id> delayed?"}'
```
## Chat pipeline regression suite

`backend/tests/` is an executable version of `CHAT_TEST_QUERIES.md` — one intent-classification
assertion per template, plus the documented routing edge cases (multi-tracking-ID decline, the
bare-ID default, the fleet-intent override guard, the causal/aggregate "successfully-filled
template isn't necessarily the right one" gates) and Stage 5 guardrail checks for every template.
It runs offline — no live Postgres or LLM call — by pre-loading the embedding model once per
session and monkeypatching the org-name/city fuzzy-match caches with fixture data, so it finishes
in a few seconds and is safe to run before every commit:

```bash
docker compose up -d db backend   # only these two services are needed
docker compose exec backend pytest -v
```

Building it surfaced one real, previously-undocumented gap: comparing two named customers
("compare Acme Corp and Globex") used to silently answer about only the first — the same failure
shape the multi-tracking-ID guard already prevented, just never extended to `org_name`. Fixed by
adding `entities.py`'s `org_names` (plural — mirrors `tracking_ids`) and a matching
`multiple_org_names_detected` guard in `pipeline.py`; `test_multi_customer_comparison_declines_without_guessing`
regression-guards it.

## Chat pipeline observability

`shipment_chat_log` now records, per interaction: `source` (`template`/`llm`/`NULL` for a pure
decline), `llm_provider`/`llm_model` (only when `source='llm'`), `guardrail_status`
(`accepted`/`rejected`/`NULL`), and `latency_ms` (end-to-end pipeline wall-clock time) — turning
the audit log into an actual ops signal for the "minimize LLM load" design goal, instead of
something only answerable by grepping container logs. `GET /api/chat/llm-usage`
(`v_chat_llm_usage`) surfaces the aggregate: template-vs-LLM traffic split by provider, with avg
latency and guardrail-rejection counts. Existing rows from before this change have these columns
`NULL` — expected, not a data-quality bug.

## Chat pipeline reliability (Phase 2)

Two additions to `llm_client.py`/`sql_llm.py`/`pipeline.py`, both named as intended design in
`AGENTIC_RAG_ARCHITECTURE.md` at Stage 4b's original write-up but never actually built until now:

- **Guardrail-rejection retry.** When Stage 5 rejects Stage 4b's drafted SQL, it now gets exactly
  one retry — the rejection reason and the failed SQL are fed back into the prompt (`sql_generated`
  fires a second time with `"retry": true` if it succeeds), instead of declining outright on the
  first mistake. Optionally escalates to a different model via `AGENT_LLM_ESCALATION_MODEL`
  (unset by default — the retry then just reuses the same model with feedback). Only applies to
  `source="llm"` — a rejected *template's* SQL is a bug in hand-written code, not something a
  prompt retry can fix, so it still declines immediately.
- **Per-provider circuit breaker.** After `AGENT_LLM_CIRCUIT_THRESHOLD` (default 3) consecutive
  LLM failures — a hard error or a response with no usable tool call, the same "provider isn't
  giving usable output" signal either way — `call_tool()` fails fast for
  `AGENT_LLM_CIRCUIT_COOLDOWN_SECONDS` (default 60) instead of waiting out a real timeout on every
  subsequent request. Aimed at the documented Ollama no-forced-tool-choice reliability gap, but
  applies uniformly to all three providers, tracked independently per provider.

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
| `AGENT_DB_PASSWORD` | `agent_ro_pw`   | Password for the read-only `agent_ro` DB role the chat agent uses |
| `AGENT_LLM_PROVIDER` | `anthropic`    | v1 LLM provider — `anthropic` (cloud, paid), `gemini` (cloud, free tier available), or `ollama` (local); all three implement the same interface, see `llm_client.py` |
| `ANTHROPIC_API_KEY` | *(none)*        | Required only if `AGENT_LLM_PROVIDER=anthropic`; also needs account credit |
| `AGENT_LLM_MODEL`   | `claude-haiku-4-5-20251001` | Anthropic model, used only when that provider is active |
| `GEMINI_API_KEY`    | *(none)*        | Required only if `AGENT_LLM_PROVIDER=gemini`; get a free-tier key at https://aistudio.google.com/apikey |
| `AGENT_GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini model, used only when that provider is active — the 3.x preview line is what's actually reachable on a new project's free tier at the time this was set up; the 2.x stable models returned "not available to new users" |
| `AGENT_OLLAMA_MODEL` | `llama3.1`     | Ollama model tag — must already be pulled (`ollama pull <model>`) and support tool/function calling |
| `AGENT_OLLAMA_HOST` | `http://host.docker.internal:11434` | Where the backend container reaches Ollama running on the host |
| `AGENT_LLM_ESCALATION_MODEL` | *(none)* | Model to retry with after a Stage 5 guardrail rejection of Stage 4b's SQL; unset reuses the same model with the rejection reason fed back into the prompt instead |
| `AGENT_LLM_CIRCUIT_THRESHOLD` | `3` | Consecutive LLM failures before the circuit breaker opens and calls fail fast instead of attempting |
| `AGENT_LLM_CIRCUIT_COOLDOWN_SECONDS` | `60` | How long the circuit stays open before allowing a half-open trial call |
| `VITE_GOOGLE_MAPS_API_KEY` | *(empty)* | Google Maps JavaScript API key for the shipment detail drawer's journey map. Without it, the drawer shows a "map disabled" placeholder instead of erroring. Baked into the static frontend build at image-build time — rebuild the frontend after changing it (`docker compose up --build frontend`). |
