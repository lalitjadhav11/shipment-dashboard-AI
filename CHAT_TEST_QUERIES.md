# Chat Test Query Guide

Every distinct question the AI Shipment Journey Summary chat (`POST /api/chat`) can currently
answer — one section per template/routing path (28 v0 templates + the Stage 4b/7v1 analytical
path), each with real, in-database values so every example is directly testable and expected to
return actual data, not a "not found." Cross-referenced against `backend/chat/sql_templates.py`,
`02_phase1_agentic_schema.json`'s `query_patterns`, and the live-verified fixes in
`AGENTIC_RAG_ARCHITECTURE.md` (§9-§16.2).

Sample values below were pulled live from the seeded dataset and will drift on reseed
(`docker compose down -v` / a different `SEED_RANDOM_SEED`) — re-run the lookup queries in each
section's "how these values were found" note if they stop matching.

## How to test

```bash
# Plain answer only (customer-facing default):
curl -s -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Where is tracking number 700000000001 right now?"}'

# Full "thinking" trace (intent, entities, scoped schema, generated SQL, validation, execution):
curl -s -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" -H "X-User-Role: OPS" \
  -d '{"query": "..."}'
```

Or through the frontend at `http://localhost:3000` — type into the top search bar.

---

## 1. Single-shipment lookups (tracking_id required)

All 9 templates below need a real tracking_id. Values already confirmed to return data:

| tracking_id | Scenario |
|---|---|
| `700000000001` | Domestic, `IN_TRANSIT`, no issues |
| `100000000002` | `DELIVERED` |
| `400000000067` | International, `customs_status = HELD` |
| `800000000010` | Delayed — `reason_for_delay = OTHER` |
| `900000000005` | Delayed (`CIVIL_UNREST`) **and** has an open issue |
| `400000000019` | 3 failed delivery attempts |
| `100000000255` | `current_status = CUSTOMS_HOLD` |

### `where_is_my_package`
*Current status, ETA, and journey timeline for one shipment.*
- "Where is tracking number 700000000001 right now?"
- "700000000001" *(bare tracking_id — see §5 for why this works)*
- "Wheres 100000000002"

### `why_is_it_late`
*Delay reason, comments, open-issue count, ETA for one shipment.*
- "Why is my shipment 800000000010 delayed?"
- "What is the reason my shipment 900000000005 is delayed?" *(the word "reason" doesn't trigger the causal gate here — this template already explains causation, §15)*

### `shipment_customer_lookup`
*Which customer owns this shipment — org name, account ID, contact.*
- "Who is the customer for tracking number 700000000001?"
- "What customer does 400000000067 belong to?"

### `shipment_package_details`
*Package type/size/weight/description, service level, order ID.*
- "What kind of package is tracking number 700000000001, and how much does it weigh?"
- "What delivery service is tracking number 700000000001 using?"

### `shipment_route`
*Origin/destination, domestic vs. international.*
- "Where is my shipment 700000000001 coming from and where is it going?"
- "Is 400000000067 a domestic or international shipment?"

### `shipment_schedule`
*Pickup window, delivery window, ETA.*
- "When will my package 700000000001 be picked up, and what's the delivery window?"

### `shipment_delivery_attempts`
*Failed delivery attempt count and last attempt time.*
- "Has tracking number 400000000019 had any failed delivery attempts?"

### `customs_status`
*Domestic (N/A) or international customs clearance state.*
- "Is 400000000067 held in customs?"
- "Is tracking number 700000000001 held in customs right now?" *(domestic → "no customs processing applies")*

### `open_issues_for_shipment`
*Open/investigating issues for one shipment.*
- "Are there any open issues on 900000000005?"

---

## 2. Fleet-wide dashboards (no parameters — same answer regardless of phrasing)

### `dashboard_headline`
- "Give me the dashboard headline numbers"
- "What's our overall shipment summary?"

### `status_breakdown`
- "Give me a breakdown of shipment statuses"
- "How many shipments are in each status?"

### `ontime_performance`
- "How is our on-time delivery performance?"
- "What's our on-time percentage?"

### `delay_reason_breakdown`
- "What are the top reasons shipments are delayed?"
- "Break down delays by reason"

### `domestic_vs_international_split`
- "How many domestic versus international shipments do we have?"

### `daily_volume_trend`
- "Show me shipment volume trend over the last two weeks"
- "Give me a daily volume trend"

### `service_level_mix`
- "What's our service level mix across all shipments?"

### `chat_activity_summary`
- "How much chat activity have we had, and what's the average confidence?"

### `ops_daily_briefing`
- "Give me today's critical shipment issues"

### `top_customers_by_volume`
- "Show me top customers by volume"
- "Who are our top shippers?"

---

## 3. Reverse lookups (filter the fleet by an attribute)

### `shipments_by_customer` / `shipments_by_customer_delayed`
*Real customer names confirmed in the data:*
- "Show me all shipments for Smith Ltd" *(24 delayed shipments on record)*
- "Which of Walker PLC's shipments are currently delayed?"
- "Show me all shipments for Brown and Sons"

### `shipments_by_status`
*Any of the 17 `current_status` values (see table in §1) work — CUSTOMS_HOLD is the
best-tested one:*
- "Give me 5 shipments that currently have status customs hold"
- "give me 5 shipments those are at customs" *(typo-tolerant — §13)*
- "Show me all delivery failed shipments"

### `shipments_by_package_type`
*Values: `BOX, ENVELOPE, TUBE, CRATE, PALLET, CUSTOM`*
- "Show me all pallet shipments"

### `shipments_by_delivery_type`
*Values: `STANDARD, EXPRESS, OVERNIGHT, ECONOMY, INTERNATIONAL_PRIORITY`*
- "Show me all express delivery shipments" *(confirmed real matches, e.g. `200000000004`)*

### `failed_delivery_shipments`
- "Which shipments have had failed delivery attempts?"

### `shipments_by_location`
*Real cities in the data: Atlanta, Washington, Austin, Chicago, New York, Kansas City, Miami,
Seattle (matches either origin or destination).*
- "Which shipments are going to or coming from Seattle?"

### `shipments_by_package_size`
*Values: `SMALL, MEDIUM, LARGE, EXTRA_LARGE, PALLET_SIZED`*
- "Show me all our large and extra-large shipments"

### `shipments_by_pickup_date`
*Confirmed dates with real volume: July 13-17, 2026 (700-850 shipments each).*
- "Which shipments are scheduled for pickup on July 17th?"

---

## 4. Routing edge cases (worth testing on their own)

- **Multiple tracking IDs in one query** — declines rather than silently answering about only
  the first: "Compare 700000000001 and 100000000002" → asks which one you meant.
- **Bare tracking ID** (≤4 words after stripping it) — defaults to `where_is_my_package`:
  "700000000001"
- **Specific question + tracking ID, NOT minimal** — skips the blind default, tries Stage 4b
  instead of confidently answering the wrong thing: "What was the previous stage of
  100000000002?" (§11)
- **Fleet-wide-sounding phrasing + a tracking ID present** — overridden to the shipment-scoped
  lookup rather than answering an irrelevant fleet report (§9): "top customers 700000000001"

---

## 5. Causal / analytical questions (Stage 4b + Stage 7 v1 — real LLM calls, not free)

These have no dedicated template (asking "why"/"what's causing X" is structurally different
from a lookup or count — §15) and route to the LLM, grounded in `shipment_issues.description`'s
real recorded text, not generic textbook knowledge (§15.1). Confirmed working phrasings from
this session's testing, spanning every recognized synonym family (§16-§16.2: why, reason(s),
cause/causes/caused/**causing**, block/blocker/blocking, bottleneck, obstacle,
obstruct/obstructing, impede/impediment/impeding, hinder/hindering, prevent/preventing):

- "Why are so many orders held at customs?" *(958 CUSTOMS_HOLD issues — real causes: missing HS
  codes, rejected declarations needing resubmission)*
- "What are the major blockers for international packages?"
- "What is causing failed deliveries?" *(400 incidents — real cause: recipient not available /
  no safe location to leave package)*
- "Why do we have so many failed delivery attempts?"
- "What is the root cause of our open issues?"
- "Why are so many shipments returned to sender?"

These are genuinely slower (a real LLM round-trip per query, not the instant v0 path) and, with
`AGENT_LLM_PROVIDER=anthropic`, cost real API usage — the `.env` provider switch documented in
`README.md`'s Configuration table controls which of `anthropic`/`ollama` handles them.

---

## 6. Known non-goals (correctly declined, not bugs)

- **Numeric range/threshold questions** — "shipments weighing more than 20kg," "delayed by more
  than 3 days." No template supports inequality filters (all use exact equality); these fall to
  Stage 4b, which may or may not draft a usable query depending on provider/model.
- **Cross-entity comparisons** — "compare Acme Corp and Globex" — no template compares two named
  values at once; correctly falls to Stage 4b.
- **Anything not about shipments/customers/issues** — declines with the clarifying-question
  answer rather than hallucinating.
