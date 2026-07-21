#!/usr/bin/env python3
"""
llm_augment_shipments.py — LLM-driven synthetic data augmentation.

Runs as its own one-shot service (llm_augmenter in docker-compose.yml) on
EVERY `docker compose up` — unlike 03_generate_phase1_data.py (which is
idempotent and only ever runs once, against an empty table), this script
always adds SYNTHETIC_SHIPMENTS_COUNT (default 250) more shipments on top
of whatever already exists. Repeated runs accumulate: the dataset grows by
this amount every time the stack comes up.

How it splits the work between the LLM and plain code:
  1. Samples a handful of the most-recently-created real shipments as
     few-shot "inspiration" context.
  2. Asks Gemini, in batches, to invent new shipment scenarios — a package
     description, which known city pair, which status/delay reason —
     constrained to the exact enum values and city/hub names the rest of
     the dataset uses. This matters beyond data hygiene: the frontend's
     JourneyMap component only has coordinates for this specific city/hub
     list (see frontend/src/geo.js), so a row referencing an unlisted city
     would silently fail to plot — the LLM is never free to invent geography.
  3. Fills in everything else programmatically (IDs, timestamps, hub
     selection, the tracking_events journey) using the same logic as the
     main generator, and bulk-inserts additively — this script never
     truncates anything.

Runs best-effort: an LLM/API failure (missing key, rate limit, malformed
response) degrades to "insert whatever was already validated," never a
crash that would look like a real seeding failure to the rest of the stack.
"""
import json
import math
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values, Json

DSN = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/shipdb_phase1")
TARGET_COUNT = int(os.environ.get("SYNTHETIC_SHIPMENTS_COUNT", "250"))
LLM_BATCH_SIZE = int(os.environ.get("SYNTHETIC_LLM_BATCH_SIZE", "10"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("AGENT_GEMINI_MODEL", "gemini-3.1-flash-lite")

# =============================================================================
# REFERENCE DATA — mirrors 03_generate_phase1_data.py's pools exactly, so
# every synthetic row stays inside the same city/hub/enum universe the rest
# of the app (dashboard filters, JourneyMap coordinates) already understands.
# =============================================================================

DOMESTIC_CITIES = [
    ("123 Market St", "San Francisco", "CA", "94105", "US"),
    ("500 5th Ave", "New York", "NY", "10110", "US"),
    ("221 W 6th St", "Austin", "TX", "78701", "US"),
    ("100 N Michigan Ave", "Chicago", "IL", "60601", "US"),
    ("1 Peachtree St", "Atlanta", "GA", "30303", "US"),
    ("400 Broad St", "Seattle", "WA", "98109", "US"),
    ("55 W Colorado Blvd", "Pasadena", "CA", "91105", "US"),
    ("2 15th St NW", "Washington", "DC", "20024", "US"),
    ("101 Ocean Dr", "Miami", "FL", "33139", "US"),
    ("909 Walnut St", "Kansas City", "MO", "64106", "US"),
]

INTERNATIONAL_CITIES = [
    ("45 Berliner Str", "Berlin", None, "10115", "DE"),
    ("10 Downing Cross", "London", None, "SW1A 2AA", "GB"),
    ("22 Rue de Rivoli", "Paris", None, "75004", "FR"),
    ("3-1 Marunouchi", "Tokyo", None, "100-0005", "JP"),
    ("88 Queensway", "Hong Kong", None, "999077", "HK"),
    ("200 Bay St", "Toronto", "ON", "M5J 2J1", "CA"),
    ("15 Collins St", "Melbourne", "VIC", "3000", "AU"),
    ("9 Raffles Pl", "Singapore", None, "048619", "SG"),
    ("Av. Paulista 1000", "Sao Paulo", None, "01310-100", "BR"),
    ("1 Connaught Rd", "Mumbai", None, "400001", "IN"),
]

DOMESTIC_BY_NAME = {c[1]: c for c in DOMESTIC_CITIES}
INTERNATIONAL_BY_NAME = {c[1]: c for c in INTERNATIONAL_CITIES}
ALL_CITIES_BY_NAME = {**DOMESTIC_BY_NAME, **INTERNATIONAL_BY_NAME}

CITY_REGION = {
    "San Francisco": "WEST", "Seattle": "WEST", "Pasadena": "WEST",
    "Austin": "CENTRAL", "Chicago": "CENTRAL", "Kansas City": "CENTRAL",
    "New York": "EAST", "Atlanta": "EAST", "Washington": "EAST", "Miami": "EAST",
}
CENTRAL_HUBS = ["Memphis SuperHub, TN, US", "Indianapolis Hub, IN, US", "Louisville Hub, KY, US"]
WEST_HUB = "Oakland Sort Facility, CA, US"

CONNECTING_HUB_BY_COUNTRY = {
    "DE": "Frankfurt Hub, DE", "GB": "Frankfurt Hub, DE", "FR": "Frankfurt Hub, DE",
    "JP": "Hong Kong Gateway Hub, HK", "HK": "Hong Kong Gateway Hub, HK",
    "SG": "Hong Kong Gateway Hub, HK", "AU": "Hong Kong Gateway Hub, HK",
    "IN": "Dubai Gateway Hub, AE",
    "CA": "Memphis Gateway Hub, US", "BR": "Memphis Gateway Hub, US",
}

PACKAGE_TYPES = ["BOX", "ENVELOPE", "TUBE", "CRATE", "PALLET", "CUSTOM"]
PACKAGE_SIZES_BY_TYPE = {
    "ENVELOPE": ["SMALL", "MEDIUM"],
    "TUBE": ["SMALL", "MEDIUM", "LARGE"],
    "BOX": ["SMALL", "MEDIUM", "LARGE", "EXTRA_LARGE"],
    "CRATE": ["LARGE", "EXTRA_LARGE", "PALLET_SIZED"],
    "PALLET": ["EXTRA_LARGE", "PALLET_SIZED"],
    "CUSTOM": ["SMALL", "MEDIUM", "LARGE", "EXTRA_LARGE", "PALLET_SIZED"],
}
PACKAGE_WEIGHT_RANGE_KG = {
    "SMALL": (0.1, 3.0),
    "MEDIUM": (1.0, 10.0),
    "LARGE": (5.0, 25.0),
    "EXTRA_LARGE": (15.0, 45.0),
    "PALLET_SIZED": (50.0, 500.0),
}
DELIVERY_TYPES = ["STANDARD", "EXPRESS", "OVERNIGHT", "ECONOMY", "INTERNATIONAL_PRIORITY"]
TRANSIT_DAYS = {
    "OVERNIGHT": (1, 1),
    "EXPRESS": (2, 3),
    "STANDARD": (4, 6),
    "ECONOMY": (6, 9),
    "INTERNATIONAL_PRIORITY": (5, 10),
}
DELAY_REASONS = [
    "NONE", "CUSTOMS", "WEATHER", "CIVIL_UNREST", "LOST_PACKAGE",
    "MECHANICAL_ISSUE", "ADDRESS_ISSUE", "OTHER",
]

JOURNEY_STAGES = [
    "LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
    "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT", "AT_CONNECTING_HUB",
    "CUSTOMS_HOLD", "CUSTOMS_CLEARED",
    "IN_TRANSIT_TO_DESTINATION_HUB", "OUT_FOR_DELIVERY", "DELIVERED",
]
TERMINAL_FAILURE_STATUSES = ["DELIVERY_FAILED", "RETURNED_TO_SENDER", "LOST", "CANCELLED"]
CURRENT_STATUSES = JOURNEY_STAGES + TERMINAL_FAILURE_STATUSES


def pick_domestic_hub(city_tuple):
    region = CITY_REGION.get(city_tuple[1], "CENTRAL")
    if region == "WEST":
        return WEST_HUB
    return random.choice(CENTRAL_HUBS)


def pick_connecting_hub(dest_tuple):
    return CONNECTING_HUB_BY_COUNTRY.get(dest_tuple[4], "Frankfurt Hub, DE")


def loc_json(city_tuple):
    address, city, state, postal, country = city_tuple
    d = {"address": address, "city": city, "postal_code": postal, "country_code": country}
    if state:
        d["state"] = state
    return d


def build_journey(scenario, is_international):
    if scenario == "CANCELLED":
        return ["LABEL_CREATED", "SHIPMENT_CREATED", "CANCELLED"]
    if scenario == "LOST":
        return ["LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
                "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT", "LOST"]
    if scenario == "RETURNED_TO_SENDER":
        base = ["LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
                "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT"]
        if is_international:
            base += ["AT_CONNECTING_HUB", "CUSTOMS_HOLD", "CUSTOMS_CLEARED"]
        base += ["IN_TRANSIT_TO_DESTINATION_HUB", "OUT_FOR_DELIVERY", "DELIVERY_FAILED", "RETURNED_TO_SENDER"]
        return base
    if scenario == "DELIVERY_FAILED":
        base = ["LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
                "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT"]
        if is_international:
            base += ["AT_CONNECTING_HUB", "CUSTOMS_HOLD", "CUSTOMS_CLEARED"]
        base += ["IN_TRANSIT_TO_DESTINATION_HUB", "OUT_FOR_DELIVERY", "DELIVERY_FAILED"]
        return base
    if scenario == "CUSTOMS_HOLD":
        return ["LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
                "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT",
                "AT_CONNECTING_HUB", "CUSTOMS_HOLD"]
    if scenario == "CUSTOMS_CLEARED":
        return ["LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
                "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT",
                "AT_CONNECTING_HUB", "CUSTOMS_HOLD", "CUSTOMS_CLEARED"]
    if scenario in JOURNEY_STAGES:
        idx = JOURNEY_STAGES.index(scenario)
        stages = JOURNEY_STAGES[:idx + 1]
        return [s for s in stages if s not in ("CUSTOMS_HOLD", "CUSTOMS_CLEARED") or is_international]
    raise ValueError(f"Unhandled scenario {scenario}")


def location_for_stage(stage, origin, dest, is_international, origin_hub, dest_hub, connecting_hub):
    if stage in ("LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED"):
        return f"{origin[1]}, {origin[4]}"
    if stage == "IN_TRANSIT_TO_ORIGIN_HUB":
        return origin_hub
    if stage == "AT_DISTRIBUTION_HUB":
        return dest_hub
    if stage in ("AT_CONNECTING_HUB", "CUSTOMS_HOLD", "CUSTOMS_CLEARED"):
        return connecting_hub if is_international else dest_hub
    if stage in ("IN_TRANSIT", "IN_TRANSIT_TO_DESTINATION_HUB"):
        return "In transit"
    if stage in ("OUT_FOR_DELIVERY", "DELIVERED", "DELIVERY_FAILED", "RETURNED_TO_SENDER"):
        return f"{dest[1]}, {dest[4]}"
    return "Unknown"


def note_for_stage(stage, reason_for_delay, delay_comments):
    if stage in ("CUSTOMS_HOLD", "CUSTOMS_CLEARED"):
        if reason_for_delay == "CUSTOMS" and delay_comments:
            return delay_comments
        return "Routine customs clearance checkpoint — no issues reported."
    return "LLM-augmented seed event"


# =============================================================================
# GEMINI CALL — scenario generation only (never IDs, timestamps, or geometry)
# =============================================================================

def call_gemini_batch(n, inspiration_rows):
    if not GEMINI_API_KEY:
        return None

    from google import genai
    from google.genai import types

    system_prompt = (
        "You invent realistic (entirely fictional) shipment scenarios for a logistics "
        "demo dataset, in the same style as the existing examples provided. You MUST "
        "only use the exact city names, and enum values listed as allowed — never invent "
        "a new city, hub, or status value not in those lists."
    )
    user_message = json.dumps({
        "instruction": f"Generate exactly {n} new, varied shipment scenarios.",
        "allowed_domestic_cities": list(DOMESTIC_BY_NAME.keys()),
        "allowed_international_cities": list(INTERNATIONAL_BY_NAME.keys()),
        "allowed_package_types": PACKAGE_TYPES,
        "allowed_delivery_types": DELIVERY_TYPES,
        "allowed_current_statuses": CURRENT_STATUSES,
        "allowed_delay_reasons": DELAY_REASONS,
        "inspiration_examples_from_real_data": inspiration_rows,
    })

    parameters = {
        "type": "object",
        "properties": {
            "scenarios": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "src_city": {"type": "string", "enum": list(ALL_CITIES_BY_NAME.keys())},
                        "dest_city": {"type": "string", "enum": list(ALL_CITIES_BY_NAME.keys())},
                        "package_type": {"type": "string", "enum": PACKAGE_TYPES},
                        "package_desc": {"type": "string"},
                        "delivery_type": {"type": "string", "enum": DELIVERY_TYPES},
                        "current_status": {"type": "string", "enum": CURRENT_STATUSES},
                        "reason_for_delay": {"type": "string", "enum": DELAY_REASONS},
                        "delay_comments": {"type": "string"},
                        "comments": {"type": "string"},
                    },
                    "required": [
                        "src_city", "dest_city", "package_type", "package_desc",
                        "delivery_type", "current_status", "reason_for_delay",
                    ],
                },
            },
        },
        "required": ["scenarios"],
    }

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=8192,
                tools=[types.Tool(function_declarations=[types.FunctionDeclaration(
                    name="generate_shipment_scenarios",
                    description="Return an array of new shipment scenarios.",
                    parameters=parameters,
                )])],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY", allowed_function_names=["generate_shipment_scenarios"],
                    )
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — network/rate-limit/SDK failure degrades gracefully
        print(f"[llm_augment] Gemini call failed: {exc}", file=sys.stderr)
        return None

    for candidate in response.candidates or []:
        for part in candidate.content.parts or []:
            fc = getattr(part, "function_call", None)
            if fc and fc.name == "generate_shipment_scenarios":
                return list(dict(fc.args).get("scenarios") or [])

    print("[llm_augment] Gemini response had no scenarios function call", file=sys.stderr)
    return None


# =============================================================================
# ROW ASSEMBLY — the LLM only chose enum values + copy; everything
# structural (IDs, timestamps, hub routing, journey timeline) is computed
# here so a bad/creative LLM output can never produce an invalid row.
# =============================================================================

def validate_and_build_row(sc, customer_id, seq, run_salt):
    src_name = sc.get("src_city")
    dest_name = sc.get("dest_city")
    package_type = sc.get("package_type")
    delivery_type = sc.get("delivery_type")
    current_status = sc.get("current_status")
    reason_for_delay = sc.get("reason_for_delay") or "NONE"

    if src_name not in ALL_CITIES_BY_NAME or dest_name not in ALL_CITIES_BY_NAME:
        return None
    if src_name == dest_name:
        return None
    if package_type not in PACKAGE_TYPES or delivery_type not in DELIVERY_TYPES:
        return None
    if current_status not in CURRENT_STATUSES or reason_for_delay not in DELAY_REASONS:
        return None

    origin = ALL_CITIES_BY_NAME[src_name]
    dest = ALL_CITIES_BY_NAME[dest_name]
    is_international = src_name in INTERNATIONAL_BY_NAME or dest_name in INTERNATIONAL_BY_NAME

    package_size = random.choice(PACKAGE_SIZES_BY_TYPE[package_type])
    package_weight = round(random.uniform(*PACKAGE_WEIGHT_RANGE_KG[package_size]), 3)
    package_desc = (sc.get("package_desc") or "Package").strip()[:250] or "Package"

    now = datetime.now(timezone.utc)
    created_at = now - timedelta(days=random.uniform(0, 30))
    transit_lo, transit_hi = TRANSIT_DAYS[delivery_type]
    estimated_delivery = created_at + timedelta(days=random.randint(transit_lo, transit_hi))

    delivery_date = None
    if current_status == "DELIVERED":
        delivery_date = estimated_delivery + timedelta(hours=random.uniform(-12, 12))
        if delivery_date < created_at:
            delivery_date = created_at + timedelta(hours=random.randint(4, 12))

    origin_hub = pick_domestic_hub(origin)
    dest_hub = pick_domestic_hub(dest)
    connecting_hub = pick_connecting_hub(dest) if is_international else None

    stages = build_journey(current_status, is_international)

    span_end = delivery_date or now
    if span_end <= created_at:
        span_end = created_at + timedelta(hours=len(stages) * 4)
    step = (span_end - created_at) / max(len(stages) - 1, 1)

    delay_comments = None
    if reason_for_delay != "NONE":
        fallback = f"Delay due to {reason_for_delay.replace('_', ' ').lower()}."
        delay_comments = (sc.get("delay_comments") or fallback).strip()[:500] or fallback

    customs_status = "NOT_REQUIRED"
    if is_international:
        if current_status == "CUSTOMS_HOLD":
            customs_status = "HELD"
        elif current_status in ("CUSTOMS_CLEARED", "IN_TRANSIT_TO_DESTINATION_HUB",
                                 "OUT_FOR_DELIVERY", "DELIVERED", "DELIVERY_FAILED", "RETURNED_TO_SENDER"):
            customs_status = "CLEARED"
        else:
            customs_status = "PENDING"

    comments = (sc.get("comments") or "").strip()
    comments = f"{comments} (LLM-generated synthetic record)".strip()

    tracking_id = f"9{run_salt}{seq:04d}"
    order_id = f"ORD-SYN-{run_salt}-{seq:04d}"
    failed_attempts = random.randint(1, 3) if current_status == "DELIVERY_FAILED" else 0

    shipment_row = (
        tracking_id, order_id, customer_id, package_type, package_desc, package_size,
        package_weight, delivery_type, is_international, Json(loc_json(origin)), Json(loc_json(dest)),
        customs_status, created_at.date(),
        (created_at + timedelta(hours=1)).time(), (created_at + timedelta(hours=6)).time(),
        None, None,
        current_status, estimated_delivery, delivery_date, reason_for_delay, delay_comments,
        failed_attempts, None, comments, created_at,
    )

    event_rows = []
    for i, stage in enumerate(stages):
        ts = created_at + step * i
        loc = location_for_stage(stage, origin, dest, is_international, origin_hub, dest_hub, connecting_hub)
        note = note_for_stage(stage, reason_for_delay, delay_comments)
        event_rows.append((str(uuid.uuid4()), tracking_id, stage, loc, ts, note))

    return shipment_row, event_rows


SHIPMENT_COLS = [
    "tracking_id", "order_id", "customer_id", "package_type", "package_desc", "package_size",
    "package_weight_kg", "delivery_type", "is_international", "src_loc", "dest_loc",
    "customs_status", "pickup_date", "pickup_window_start", "pickup_window_end",
    "delivery_window_start", "delivery_window_end", "current_status", "estimated_delivery",
    "delivery_date", "reason_for_delay", "delay_comments", "failed_delivery_attempts",
    "last_delivery_attempt_at", "comments", "created_at",
]
EVENT_COLS = ["event_id", "tracking_id", "stage", "location", "event_timestamp", "notes"]


def fetch_customer_ids(cur, limit=300):
    cur.execute("SELECT customer_id FROM customers ORDER BY random() LIMIT %s;", (limit,))
    return [row[0] for row in cur.fetchall()]


def fetch_inspiration_rows(cur, limit=8):
    cur.execute("""
        SELECT s.package_type, s.package_desc, s.delivery_type, s.current_status,
               s.reason_for_delay, s.comments
        FROM shipments s
        ORDER BY s.created_at DESC
        LIMIT %s;
    """, (limit,))
    cols = ["package_type", "package_desc", "delivery_type", "current_status", "reason_for_delay", "comments"]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main():
    if not GEMINI_API_KEY:
        print("[llm_augment] GEMINI_API_KEY not set — skipping synthetic augmentation.", flush=True)
        return

    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            customer_ids = fetch_customer_ids(cur)
            if not customer_ids:
                print("[llm_augment] no customers found — run the base seeder first. Skipping.", flush=True)
                return
            inspiration = fetch_inspiration_rows(cur)
    finally:
        conn.close()

    print(f"[llm_augment] generating {TARGET_COUNT} synthetic shipments via {GEMINI_MODEL} ...", flush=True)

    run_salt = int(time.time())
    shipment_rows = []
    event_rows = []
    generated = 0
    batch_num = 0
    max_batches = math.ceil(TARGET_COUNT / LLM_BATCH_SIZE) * 3  # headroom for skipped/invalid scenarios

    while generated < TARGET_COUNT and batch_num < max_batches:
        batch_num += 1
        n = min(LLM_BATCH_SIZE, TARGET_COUNT - generated)
        scenarios = call_gemini_batch(n, inspiration)
        if not scenarios:
            print(f"[llm_augment] batch {batch_num}: no scenarios returned, retrying shortly...", flush=True)
            time.sleep(5)
            continue

        for sc in scenarios:
            if generated >= TARGET_COUNT:
                break
            built = validate_and_build_row(sc, random.choice(customer_ids), generated, run_salt)
            if built is None:
                continue
            row, events = built
            shipment_rows.append(row)
            event_rows.extend(events)
            generated += 1

        print(f"[llm_augment] progress: {generated}/{TARGET_COUNT}", flush=True)

    if not shipment_rows:
        print("[llm_augment] no valid synthetic rows were generated — nothing inserted.", flush=True)
        return

    conn = psycopg2.connect(DSN)
    try:
        with conn, conn.cursor() as cur:
            # Bulk-load our own authoritative journey timestamps instead of
            # relying on the auto-log trigger's now()-only stamps — same
            # reason the main generator disables it during its bulk load.
            cur.execute("ALTER TABLE shipments DISABLE TRIGGER trg_shipments_stage_history;")
            try:
                execute_values(
                    cur, f"INSERT INTO shipments ({', '.join(SHIPMENT_COLS)}) VALUES %s",
                    shipment_rows, page_size=500,
                )
                execute_values(
                    cur, f"INSERT INTO tracking_events ({', '.join(EVENT_COLS)}) VALUES %s",
                    event_rows, page_size=2000,
                )
            finally:
                cur.execute("ALTER TABLE shipments ENABLE TRIGGER trg_shipments_stage_history;")
    finally:
        conn.close()

    print(
        f"[llm_augment] inserted {len(shipment_rows)} synthetic shipments "
        f"({len(event_rows)} tracking events).", flush=True,
    )


if __name__ == "__main__":
    main()
