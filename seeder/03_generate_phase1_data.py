#!/usr/bin/env python3
"""
generate_phase1_data.py — Production-ready synthetic data loader for the
Phase 1 shipping schema (customers, shipments, tracking_events, shipment_issues,
shipment_chat_log).

WHAT THIS DOES
--------------
1. Generates a realistic customer base and 25,000 shipments (configurable)
   spanning a rolling window of `created_at` in the last N days (default 30)
   with resulting `estimated_delivery` / `delivery_date` values that naturally
   extend up to N days into the future — i.e. "1 month past, 1 month future".
2. Guarantees coverage of every shipment_status, delay_reason, issue_type and
   customs_status enum value at least once (quota-based scenario allocation),
   so the dataset is genuinely representative rather than randomly skewed.
3. Builds a plausible tracking_events journey per shipment (multiple ordered
   stage rows with realistic timestamps and locations) and shipment_issues /
   shipment_chat_log rows for the relevant scenarios.
4. Loads everything with bulk `execute_values` batched inserts inside a single
   transaction per phase, with the shipment auto-logging trigger temporarily
   disabled (bulk seeding supplies its own authoritative, timestamped journey
   rather than relying on the trigger's `now()`-only stamping).
5. Re-enables the trigger, runs ANALYZE, and prints/exports a real-time
   dashboard summary report (console + JSON) using the views defined in
   02_dashboard_summary_views.sql — simulating what the live dashboard would
   show immediately after this data lands.

USAGE
-----
    python3 generate_phase1_data.py \\
        --dsn "postgresql://postgres:postgres@localhost:5432/shipdb_phase1" \\
        --shipments 25000 \\
        --days-back 30 \\
        --days-forward 30 \\
        --seed 42 \\
        --report-json /mnt/user-data/outputs/realtime_summary_report.json

Environment variable DATABASE_URL is used if --dsn is not supplied.

RE-RUN BEHAVIOR (idempotent by default)
----------------------------------------
Every run TRUNCATEs the 5 Phase 1 tables (customers, shipments, tracking_events,
shipment_issues, shipment_chat_log) and loads a fresh, fully self-consistent
dataset. This is deliberate: shipment scenarios, journeys, and issues are all
generated together as one coherent snapshot, so partial/mixed data from two
different runs would not reconcile cleanly. Re-running with a different
--seed, --shipments, or date window always gives you a clean, complete reload.

If you instead want to append on top of existing data (e.g. a daily job that
adds only "today's" new shipments), pass --no-truncate together with
--id-offset set past the highest existing tracking_id/order_id counter to
avoid duplicate-key errors — see --help for details. This mode does not
regenerate history for previously-loaded shipments.
"""

import argparse
import json
import logging
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
from psycopg2.extras import execute_values, Json

try:
    from faker import Faker
except ImportError:
    Faker = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("phase1-seed")

# =============================================================================
# REFERENCE DATA
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

# Coarse US region per domestic city, used to pick a geographically sensible
# hub instead of a uniform random one (see pick_domestic_hub below). Of the 4
# domestic hubs, only Oakland is West Coast — Memphis/Indianapolis/Louisville
# are all central US, close enough together to treat as one interchangeable
# "central" pool (this mirrors real carrier super-hub placement — Memphis and
# Louisville ARE FedEx/UPS's actual central super-hub cities).
CITY_REGION = {
    "San Francisco": "WEST", "Seattle": "WEST", "Pasadena": "WEST",
    "Austin": "CENTRAL", "Chicago": "CENTRAL", "Kansas City": "CENTRAL",
    "New York": "EAST", "Atlanta": "EAST", "Washington": "EAST", "Miami": "EAST",
}
CENTRAL_HUBS = ["Memphis SuperHub, TN, US", "Indianapolis Hub, IN, US", "Louisville Hub, KY, US"]
WEST_HUB = "Oakland Sort Facility, CA, US"

# One connecting/gateway hub per DESTINATION country — picked once per
# shipment and reused for every customs-adjacent stage in that journey (see
# pick_connecting_hub). Previously each of AT_CONNECTING_HUB/CUSTOMS_HOLD/
# CUSTOMS_CLEARED called random.choice() independently, so a single shipment
# could visit Frankfurt, then Dubai, then Frankfurt again — customs happens
# AT the gateway hub a shipment is already sitting at, not by bouncing to a
# different one and back.
CONNECTING_HUB_BY_COUNTRY = {
    "DE": "Frankfurt Hub, DE", "GB": "Frankfurt Hub, DE", "FR": "Frankfurt Hub, DE",
    "JP": "Hong Kong Gateway Hub, HK", "HK": "Hong Kong Gateway Hub, HK",
    "SG": "Hong Kong Gateway Hub, HK", "AU": "Hong Kong Gateway Hub, HK",
    "IN": "Dubai Gateway Hub, AE",
    "CA": "Memphis Gateway Hub, US", "BR": "Memphis Gateway Hub, US",
}


def pick_domestic_hub(city_tuple):
    """A hub whose region matches this city — so a journey's hub stops move
    toward the shipment's actual direction of travel instead of randomly
    jumping to whichever coast happens to get rolled (the original bug: an
    Atlanta -> Seattle shipment routing through Louisville then, with equal
    probability, Oakland OR back to a central hub with no regard for which
    way the shipment was actually headed)."""
    region = CITY_REGION.get(city_tuple[1], "CENTRAL")
    if region == "WEST":
        return WEST_HUB
    return random.choice(CENTRAL_HUBS)


def pick_connecting_hub(dest_tuple):
    """The one gateway hub this international shipment clears customs
    through, chosen by destination country/region — not a fresh random pick
    per stage (see CONNECTING_HUB_BY_COUNTRY's docstring above)."""
    return CONNECTING_HUB_BY_COUNTRY.get(dest_tuple[4], "Frankfurt Hub, DE")

PACKAGE_TYPES = ["BOX", "ENVELOPE", "TUBE", "CRATE", "PALLET", "CUSTOM"]
PACKAGE_SIZES = ["SMALL", "MEDIUM", "LARGE", "EXTRA_LARGE", "PALLET_SIZED"]

# package_type, package_size, and package_weight_kg were three fully
# independent random.choice()/uniform() draws — an ENVELOPE and a PALLET
# averaged the identical ~22.5kg, and any size from SMALL to PALLET_SIZED
# could land at 45kg or 0.2kg with equal odds. Chained instead: type ->
# plausible size subset -> weight range for that size, so an ENVELOPE can
# never roll PALLET_SIZED and a PALLET_SIZED item weighs like a pallet, not
# like a phone case.
PACKAGE_SIZES_BY_TYPE = {
    "ENVELOPE": ["SMALL", "MEDIUM"],
    "TUBE": ["SMALL", "MEDIUM", "LARGE"],
    "BOX": ["SMALL", "MEDIUM", "LARGE", "EXTRA_LARGE"],
    "CRATE": ["LARGE", "EXTRA_LARGE", "PALLET_SIZED"],
    "PALLET": ["EXTRA_LARGE", "PALLET_SIZED"],
    "CUSTOM": PACKAGE_SIZES,  # catch-all — genuinely anything goes
}
PACKAGE_WEIGHT_RANGE_KG = {
    "SMALL": (0.1, 3.0),
    "MEDIUM": (1.0, 10.0),
    "LARGE": (5.0, 25.0),
    "EXTRA_LARGE": (15.0, 45.0),
    "PALLET_SIZED": (50.0, 500.0),
}
DELIVERY_TYPES_WEIGHTED = (
    ["STANDARD"] * 40 + ["EXPRESS"] * 25 + ["OVERNIGHT"] * 10 +
    ["ECONOMY"] * 15 + ["INTERNATIONAL_PRIORITY"] * 10
)
PACKAGE_DESCRIPTIONS = [
    "Wireless headphones", "Laptop computer", "Running shoes", "Office chair parts",
    "Kitchen appliance", "Books", "Clothing apparel", "Smartphone accessories",
    "Cosmetics sample kit", "Medical supplies", "Auto parts", "Toys", "Furniture hardware",
    "Camera equipment", "Sporting goods", "Home decor item", "Pet supplies", "Tools",
]

# Transit time ranges (business days) by delivery_type: (min, max)
TRANSIT_DAYS = {
    "OVERNIGHT": (1, 1),
    "EXPRESS": (2, 3),
    "STANDARD": (4, 6),
    "ECONOMY": (6, 9),
    "INTERNATIONAL_PRIORITY": (5, 10),
}

# On-time probability by delivery_type — previously every tier landed at the
# same ~75% on-time rate regardless of price/speed (scenario, which decides
# on-time vs. late, was assigned before delivery_type and never referenced
# it), which undersells the entire premise of paying for a faster tier: a
# real carrier's OVERNIGHT service is watched far more tightly than ECONOMY.
# Weighted by DELIVERY_TYPES_WEIGHTED's volume mix, this still averages to
# ~75.8% overall — STANDARD (the largest tier by volume) is anchored at
# exactly the original 75% baseline, so the fleet-wide on-time % barely
# moves; only the per-tier breakdown (v_service_level_mix / the
# service_level_mix chat template) gains real spread.
ONTIME_PROB_BY_DELIVERY_TYPE = {
    "OVERNIGHT": 0.92,
    "EXPRESS": 0.82,
    "STANDARD": 0.75,
    "ECONOMY": 0.62,
    "INTERNATIONAL_PRIORITY": 0.68,  # customs risk pulls this below EXPRESS despite the premium price
}

DELAY_REASONS_NON_CUSTOMS = ["WEATHER", "CIVIL_UNREST", "MECHANICAL_ISSUE", "ADDRESS_ISSUE", "OTHER"]

ISSUE_TYPE_BY_DELAY_REASON = {
    "CUSTOMS": "CUSTOMS_HOLD",
    "WEATHER": "WEATHER_DELAY",
    "CIVIL_UNREST": "CIVIL_UNREST",
    "LOST_PACKAGE": "LOST_PACKAGE",
    "ADDRESS_ISSUE": "ADDRESS_ISSUE",
    "MECHANICAL_ISSUE": "OTHER",
    "OTHER": "OTHER",
}

# Full happy-path journey stage order (used to build partial/complete timelines)
JOURNEY_STAGES = [
    "LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
    "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT", "AT_CONNECTING_HUB",
    "CUSTOMS_HOLD", "CUSTOMS_CLEARED",  # international-only sub-stages, spliced in conditionally
    "IN_TRANSIT_TO_DESTINATION_HUB", "OUT_FOR_DELIVERY", "DELIVERED",
]

# Scenario quota: exact allocation across 25,000 shipments guarantees coverage
# of every enum value (see docstring). Scale factor applied if --shipments != 25000.
SCENARIO_QUOTA_AT_25000 = {
    "DELIVERED_ONTIME": 10500,
    "DELIVERED_LATE": 3500,
    "IN_TRANSIT": 3250,
    "AT_DISTRIBUTION_HUB": 1000,
    "IN_TRANSIT_TO_ORIGIN_HUB": 800,
    "AT_CONNECTING_HUB": 800,
    "IN_TRANSIT_TO_DESTINATION_HUB": 1000,
    "OUT_FOR_DELIVERY": 750,
    "CUSTOMS_HOLD": 600,
    "CUSTOMS_CLEARED": 500,
    "TRACKING_ID_ISSUED": 400,
    "PACKAGE_RECEIVED": 400,
    "SHIPMENT_CREATED": 300,
    "LABEL_CREATED": 300,
    "DELIVERY_FAILED": 400,
    "RETURNED_TO_SENDER": 200,
    "LOST": 100,
    "CANCELLED": 200,
}
assert sum(SCENARIO_QUOTA_AT_25000.values()) == 25000


def scale_quota(total_shipments: int) -> dict:
    """Scale the 25,000-shipment quota to any target count, preserving proportions
    and guaranteeing at least 1 of every scenario for small counts."""
    scale = total_shipments / 25000.0
    scaled = {k: max(1, round(v * scale)) for k, v in SCENARIO_QUOTA_AT_25000.items()}
    # Reconcile rounding drift against the largest bucket so the total matches exactly.
    drift = total_shipments - sum(scaled.values())
    largest_key = max(scaled, key=scaled.get)
    scaled[largest_key] += drift
    return scaled


# =============================================================================
# HELPERS
# =============================================================================

def business_days_delta(start: datetime, n_days: int) -> datetime:
    """Add n_days worth of transit time (simple calendar-day approximation,
    intentionally not skipping weekends — Phase 1 keeps this simple)."""
    return start + timedelta(days=n_days, hours=random.randint(0, 20), minutes=random.randint(0, 59))


def pick_locations(is_international: bool):
    if is_international:
        origin = random.choice(DOMESTIC_CITIES)
        dest = random.choice(INTERNATIONAL_CITIES)
    else:
        origin, dest = random.sample(DOMESTIC_CITIES, 2)
    return origin, dest


def loc_json(city_tuple):
    address, city, state, postal, country = city_tuple
    d = {"address": address, "city": city, "postal_code": postal, "country_code": country}
    if state:
        d["state"] = state
    return d


def gen_tracking_id(rng_counter: int) -> str:
    # 12-digit numeric tracking number, FedEx-style, guaranteed unique via counter salt
    return f"{random.randint(1, 9)}{rng_counter:011d}"


def build_journey(scenario: str, is_international: bool):
    """Return the ordered list of stage names this shipment's tracking_events
    timeline should contain, based on its terminal scenario."""
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
    if scenario in ("DELIVERED_ONTIME", "DELIVERED_LATE"):
        base = ["LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
                "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT"]
        if is_international:
            base += ["AT_CONNECTING_HUB", "CUSTOMS_HOLD", "CUSTOMS_CLEARED"]
        base += ["IN_TRANSIT_TO_DESTINATION_HUB", "OUT_FOR_DELIVERY", "DELIVERED"]
        return base
    if scenario == "CUSTOMS_HOLD":
        return ["LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
                "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT",
                "AT_CONNECTING_HUB", "CUSTOMS_HOLD"]
    if scenario == "CUSTOMS_CLEARED":
        return ["LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
                "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT",
                "AT_CONNECTING_HUB", "CUSTOMS_HOLD", "CUSTOMS_CLEARED"]
    # Generic "currently mid-journey" statuses: build the prefix up to and including that stage
    if scenario in JOURNEY_STAGES:
        idx = JOURNEY_STAGES.index(scenario)
        stages = JOURNEY_STAGES[:idx + 1]
        # If mid-journey but not yet at a customs sub-stage, strip customs stages unless
        # the shipment is international and has reached AT_CONNECTING_HUB or later.
        return [s for s in stages if s not in ("CUSTOMS_HOLD", "CUSTOMS_CLEARED") or is_international]
    raise ValueError(f"Unhandled scenario {scenario}")


def location_for_stage(stage: str, origin, dest, is_international: bool,
                        origin_hub: str, dest_hub: str, connecting_hub: str) -> str:
    """origin_hub/dest_hub/connecting_hub are computed ONCE per shipment
    (pick_domestic_hub/pick_connecting_hub, called at journey-build time) and
    passed in here unchanged for every stage of that one journey — never
    re-randomized per stage. That consistency is exactly what keeps a single
    shipment's journey from revisiting unrelated hubs (Frankfurt -> Dubai ->
    Frankfurt) or detouring through a hub facing the wrong direction (Atlanta
    -> Seattle via a hub on the opposite coast from either city)."""
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
    if stage in ("LOST", "CANCELLED"):
        return "Unknown"
    return "Unknown"


def note_for_stage(stage: str, reason_for_delay: str, delay_comments: str | None) -> str:
    """CUSTOMS_HOLD/CUSTOMS_CLEARED are standard sub-stages build_journey()
    splices into EVERY international shipment's journey — the customs
    checkpoint every international package passes through, not by itself a
    sign anything went wrong. Verified live: a shipment sitting at
    CUSTOMS_CLEARED with reason_for_delay='NONE' and zero shipment_issues
    rows has an IDENTICAL-looking CUSTOMS_HOLD->CUSTOMS_CLEARED journey to a
    shipment that had a genuine, resolved customs incident — the two are
    indistinguishable from tracking_events alone; telling them apart
    required cross-referencing shipment_issues, which a plain journey view
    doesn't do. Distinguishing note text closes that gap directly in the
    journey itself: an actual incident (reason_for_delay == 'CUSTOMS') gets
    the real delay_comments text; the routine checkpoint gets an explicit
    'no issues' note instead of the generic placeholder every other stage
    still uses."""
    if stage in ("CUSTOMS_HOLD", "CUSTOMS_CLEARED"):
        if reason_for_delay == "CUSTOMS" and delay_comments:
            return delay_comments
        return "Routine customs clearance checkpoint — no issues reported."
    return "Auto-generated seed event"


# =============================================================================
# CORE GENERATION
# =============================================================================

def generate_dataset(args, fake) -> dict:
    random.seed(args.seed)
    now = datetime.now(timezone.utc)

    # ---- Customers ----
    log.info("Generating %d customers...", args.customers)
    customers = []
    tiers = ["STANDARD"] * 50 + ["SILVER"] * 25 + ["GOLD"] * 18 + ["PLATINUM"] * 7
    for i in range(args.customers):
        customer_id = str(uuid.uuid4())
        org_name = fake.company()
        profile = {
            "contact_name": fake.name(),
            "email": fake.company_email(),
            "phone": fake.phone_number(),
            "tier": random.choice(tiers),
        }
        customers.append((
            customer_id,
            f"ACCT-{100000 + i}",
            org_name,
            Json(profile),
            True,
        ))

    # ---- Scenario quota -> shuffled list of scenario labels, one per shipment ----
    quota = scale_quota(args.shipments)
    log.info("Scenario quota (target=%d): %s", args.shipments, quota)
    scenario_pool = []
    for scenario, n in quota.items():
        scenario_pool.extend([scenario] * n)
    random.shuffle(scenario_pool)
    assert len(scenario_pool) == args.shipments

    shipments = []
    tracking_events = []
    shipment_issues = []
    chat_logs = []

    window_start = now - timedelta(days=args.days_back)
    window_end = now  # shipments are *created* only up to "now"; future dates arise from transit time

    for i, scenario in enumerate(scenario_pool):
        counter = i + 1 + args.id_offset
        tracking_id = gen_tracking_id(counter)
        order_id = f"ORD-{now.year}-{2_000_000 + counter}"
        customer = random.choice(customers)
        customer_id = customer[0]

        # International forced true for customs scenarios; ~18% baseline otherwise
        if scenario in ("CUSTOMS_HOLD", "CUSTOMS_CLEARED"):
            is_international = True
        elif scenario in ("DELIVERED_ONTIME", "DELIVERED_LATE", "DELIVERY_FAILED", "RETURNED_TO_SENDER"):
            is_international = random.random() < 0.22
        else:
            is_international = random.random() < 0.15

        origin, dest = pick_locations(is_international)
        # Computed ONCE per shipment and reused for every matching stage below —
        # see location_for_stage's docstring for why that consistency matters.
        origin_hub = pick_domestic_hub(origin)
        dest_hub = pick_domestic_hub(dest)
        connecting_hub = pick_connecting_hub(dest) if is_international else None

        delivery_type = random.choice(DELIVERY_TYPES_WEIGHTED)
        if is_international and delivery_type not in ("INTERNATIONAL_PRIORITY", "STANDARD", "EXPRESS"):
            delivery_type = "INTERNATIONAL_PRIORITY"

        package_type = random.choice(PACKAGE_TYPES)
        package_size = random.choice(PACKAGE_SIZES_BY_TYPE[package_type])
        package_weight = round(random.uniform(*PACKAGE_WEIGHT_RANGE_KG[package_size]), 3)
        package_desc = random.choice(PACKAGE_DESCRIPTIONS)

        created_at = window_start + (window_end - window_start) * random.random()

        tmin, tmax = TRANSIT_DAYS[delivery_type]
        transit_days = random.randint(tmin, tmax)
        estimated_delivery = business_days_delta(created_at, transit_days)

        reason_for_delay = "NONE"
        delay_comments = None
        customs_status = "NOT_REQUIRED"
        failed_attempts = 0
        last_attempt_at = None
        delivery_date = None
        comments = None
        pickup_date = created_at.date()
        pickup_start = (created_at + timedelta(hours=1)).time()
        pickup_end = (created_at + timedelta(hours=6)).time()
        delivery_window_start = None
        delivery_window_end = None
        current_status = None

        if is_international:
            customs_status = "CLEARED"  # default; overridden per-scenario below

        if scenario in ("DELIVERED_ONTIME", "DELIVERED_LATE"):
            current_status = "DELIVERED"
            # Re-decide on-time vs. late per shipment via ONTIME_PROB_BY_DELIVERY_TYPE
            # rather than trusting the scenario pool's raw label directly — see that
            # constant's docstring for why (every tier was landing at the same ~75%).
            if random.random() < ONTIME_PROB_BY_DELIVERY_TYPE[delivery_type]:
                delivery_date = estimated_delivery - timedelta(hours=random.randint(0, 20))
                if delivery_date < created_at:
                    delivery_date = created_at + timedelta(hours=random.randint(4, 12))
            else:
                delay_hours = random.randint(6, 96)
                delivery_date = estimated_delivery + timedelta(hours=delay_hours)
                reason_for_delay = "CUSTOMS" if is_international and random.random() < 0.4 else random.choice(DELAY_REASONS_NON_CUSTOMS)
                delay_comments = f"Delivery delayed due to {reason_for_delay.lower().replace('_', ' ')}."
                if reason_for_delay == "CUSTOMS":
                    customs_status = "CLEARED"
        elif scenario == "CUSTOMS_HOLD":
            current_status = "CUSTOMS_HOLD"
            customs_status = "REJECTED" if random.random() < 0.15 else "HELD"
            reason_for_delay = "CUSTOMS"
            delay_comments = (
                "Customs declaration rejected; resubmission required with corrected documentation."
                if customs_status == "REJECTED"
                else "Missing HS code on declaration; awaiting broker resubmission."
            )
            estimated_delivery = max(estimated_delivery, now + timedelta(days=random.randint(1, 5)))
        elif scenario == "CUSTOMS_CLEARED":
            current_status = "CUSTOMS_CLEARED"
            customs_status = "CLEARED"
            estimated_delivery = max(estimated_delivery, now + timedelta(days=random.randint(1, 4)))
        elif scenario == "DELIVERY_FAILED":
            current_status = "DELIVERY_FAILED"
            failed_attempts = random.randint(1, 2)
            last_attempt_at = now - timedelta(hours=random.randint(1, 48))
            reason_for_delay = "ADDRESS_ISSUE" if random.random() < 0.6 else "OTHER"
            delay_comments = "Recipient not available; no safe location to leave package."
            delivery_window_start = last_attempt_at - timedelta(hours=4)
            delivery_window_end = last_attempt_at
            estimated_delivery = max(estimated_delivery, now + timedelta(days=random.randint(1, 3)))
        elif scenario == "RETURNED_TO_SENDER":
            current_status = "RETURNED_TO_SENDER"
            failed_attempts = 3
            last_attempt_at = now - timedelta(days=random.randint(1, 10))
            reason_for_delay = "ADDRESS_ISSUE" if random.random() < 0.5 else "OTHER"
            delay_comments = "Maximum delivery attempts exceeded; returned to sender."
            comments = "RTS after 3 failed attempts."
        elif scenario == "LOST":
            current_status = "LOST"
            reason_for_delay = "LOST_PACKAGE"
            delay_comments = "No scan activity for an extended period; investigating."
            estimated_delivery = min(estimated_delivery, now - timedelta(days=random.randint(1, 10)))
        elif scenario == "CANCELLED":
            current_status = "CANCELLED"
            reason_for_delay = "NONE"
            comments = "Cancelled by customer before pickup."
            customs_status = "NOT_REQUIRED"
        else:
            # Generic mid-journey "currently in progress" statuses
            current_status = scenario
            # ~12% of in-progress shipments are running behind schedule right now
            if random.random() < 0.12:
                reason_for_delay = "CUSTOMS" if (is_international and random.random() < 0.3) else random.choice(DELAY_REASONS_NON_CUSTOMS)
                delay_comments = f"Currently tracking behind schedule due to {reason_for_delay.lower().replace('_', ' ')}."
                estimated_delivery = max(estimated_delivery, now + timedelta(hours=random.randint(1, 72)))
            # customs_status must track the shipment's actual position relative to
            # AT_CONNECTING_HUB in JOURNEY_STAGES, not just "is it international" —
            # the previous version set every non-AT_CONNECTING_HUB international
            # stage to PENDING (including LABEL_CREATED, days before the package
            # even leaves origin) while AT_CONNECTING_HUB itself kept the default
            # "CLEARED" set above — backwards: a shipment sitting AT the gateway
            # hasn't cleared customs yet (that's CUSTOMS_CLEARED, a later stage),
            # and a shipment already OUT_FOR_DELIVERY has necessarily cleared it.
            if is_international:
                connecting_idx = JOURNEY_STAGES.index("AT_CONNECTING_HUB")
                stage_idx = JOURNEY_STAGES.index(current_status)
                if stage_idx < connecting_idx:
                    customs_status = "NOT_REQUIRED"  # not yet reached the border
                elif stage_idx == connecting_idx:
                    customs_status = "PENDING"  # at the gateway, awaiting processing
                else:
                    customs_status = "CLEARED"  # past AT_CONNECTING_HUB in the happy path
            else:
                customs_status = "NOT_REQUIRED"

        shipments.append((
            tracking_id, order_id, customer_id,
            package_type, package_desc, package_size, package_weight,
            delivery_type, is_international,
            Json(loc_json(origin)), Json(loc_json(dest)),
            customs_status,
            pickup_date, pickup_start, pickup_end,
            delivery_window_start, delivery_window_end,
            current_status, estimated_delivery, delivery_date,
            reason_for_delay, delay_comments,
            failed_attempts, last_attempt_at,
            comments,
            created_at,
        ))

        # ---- tracking_events journey ----
        journey = build_journey(scenario, is_international)
        span_end = delivery_date or last_attempt_at or min(now, estimated_delivery)
        if span_end <= created_at:
            span_end = created_at + timedelta(hours=len(journey) * 4)
        step = (span_end - created_at) / max(len(journey) - 1, 1)
        for j, stage in enumerate(journey):
            ts = created_at + step * j
            tracking_events.append((
                str(uuid.uuid4()), tracking_id, stage,
                location_for_stage(stage, origin, dest, is_international, origin_hub, dest_hub, connecting_hub),
                ts,
                note_for_stage(stage, reason_for_delay, delay_comments),
            ))

        # ---- shipment_issues ----
        if reason_for_delay != "NONE":
            if scenario in ("DELIVERY_FAILED", "RETURNED_TO_SENDER"):
                issue_type = "FAILED_DELIVERY_ATTEMPT"
            else:
                issue_type = ISSUE_TYPE_BY_DELAY_REASON.get(reason_for_delay, "OTHER")
            reported_at = created_at + timedelta(hours=random.randint(1, 24))
            if current_status == "DELIVERED":
                issue_status = "RESOLVED"
                resolved_at = delivery_date
            elif current_status in ("RETURNED_TO_SENDER",):
                issue_status = "CLOSED"
                resolved_at = created_at + timedelta(days=random.randint(3, 12))
            elif current_status in ("LOST",):
                issue_status = random.choice(["OPEN", "INVESTIGATING"])
                resolved_at = None
            elif current_status == "DELIVERY_FAILED":
                issue_status = "INVESTIGATING"
                resolved_at = None
            elif current_status != "CUSTOMS_HOLD" and random.random() < 0.35:
                # Mid-journey (not yet DELIVERED/RETURNED/LOST/FAILED) —
                # previously this branch was ALWAYS OPEN/INVESTIGATING, so
                # "the issue got fixed, but the shipment hasn't reached
                # DELIVERED yet" had NO representation in the data — every
                # RESOLVED row was tied to DELIVERED by construction (see
                # the branch above). This is exactly that missing case: a
                # genuine incident that got resolved WHILE the shipment kept
                # moving toward its next stage, not blocked anymore, just
                # not delivered yet either. ~35% of eligible mid-journey
                # delayed shipments get this treatment; the rest stay an
                # active, ongoing problem (the unchanged branch below).
                # current_status == "CUSTOMS_HOLD" excluded deliberately:
                # that scenario means the shipment IS ACTIVELY sitting at
                # the blocking stage RIGHT NOW (reason_for_delay="CUSTOMS"
                # by construction) — marking that "RESOLVED" while its
                # current_status still says "blocked" is a direct
                # contradiction, caught live in the first regenerated
                # dataset before being shipped (400000000088 had exactly
                # this).
                issue_status = "RESOLVED"
                resolved_at = min(reported_at + timedelta(hours=random.randint(6, 72)), now)
            else:
                issue_status = random.choice(["OPEN", "INVESTIGATING"])
                resolved_at = None
            shipment_issues.append((
                str(uuid.uuid4()), tracking_id, issue_type, delay_comments or "Delay reported.",
                issue_status, reported_at, resolved_at,
            ))

        # ---- shipment_chat_log (simulate ~8% of shipments generating chat activity) ----
        if random.random() < 0.08:
            n_chats = random.randint(1, 2)
            for _ in range(n_chats):
                if reason_for_delay != "NONE":
                    q = random.choice(["Why is my package delayed?", "What's going on with my shipment?", "Is this shipment stuck?"])
                    a = (f"Your package is currently in status {current_status} due to {reason_for_delay.lower().replace('_',' ')}. "
                         f"{delay_comments or ''} Updated estimated delivery is {estimated_delivery.strftime('%Y-%m-%d %H:%M UTC')}.")
                    conf = round(random.uniform(0.78, 0.97), 4)
                else:
                    q = random.choice(["Where is my package?", "When will my order arrive?", "Has it been delivered yet?"])
                    if current_status == "DELIVERED":
                        a = f"Your package was delivered on {delivery_date.strftime('%Y-%m-%d %H:%M UTC')}."
                    else:
                        a = f"Your package is currently at stage {current_status}, estimated to arrive by {estimated_delivery.strftime('%Y-%m-%d %H:%M UTC')}."
                    conf = round(random.uniform(0.85, 0.99), 4)
                context_snapshot = {
                    "tracking_id": tracking_id, "current_status": current_status,
                    "reason_for_delay": reason_for_delay, "estimated_delivery": estimated_delivery.isoformat(),
                }
                chat_logs.append((
                    str(uuid.uuid4()), tracking_id, customer_id, q, a, Json(context_snapshot), conf,
                    created_at + timedelta(hours=random.randint(1, 200)),
                ))

        if (i + 1) % 5000 == 0:
            log.info("  ...generated %d / %d shipments", i + 1, args.shipments)

    return {
        "customers": customers,
        "shipments": shipments,
        "tracking_events": tracking_events,
        "shipment_issues": shipment_issues,
        "chat_logs": chat_logs,
        "quota": quota,
    }


# =============================================================================
# DATABASE LOAD
# =============================================================================

CUSTOMER_COLS = ["customer_id", "fedex_account_id", "org_name", "customer_profile", "is_active"]
SHIPMENT_COLS = [
    "tracking_id", "order_id", "customer_id", "package_type", "package_desc", "package_size",
    "package_weight_kg", "delivery_type", "is_international", "src_loc", "dest_loc",
    "customs_status", "pickup_date", "pickup_window_start", "pickup_window_end",
    "delivery_window_start", "delivery_window_end", "current_status", "estimated_delivery",
    "delivery_date", "reason_for_delay", "delay_comments", "failed_delivery_attempts",
    "last_delivery_attempt_at", "comments", "created_at",
]
EVENT_COLS = ["event_id", "tracking_id", "stage", "location", "event_timestamp", "notes"]
ISSUE_COLS = ["issue_id", "tracking_id", "issue_type", "description", "status", "reported_at", "resolved_at"]
CHAT_COLS = ["chat_id", "tracking_id", "customer_id", "user_query", "ai_response", "context_snapshot", "confidence_score", "created_at"]


def bulk_insert(cur, table, cols, rows, page_size=2000):
    if not rows:
        return
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s"
    execute_values(cur, sql, rows, page_size=page_size)


def load_dataset(conn, data, truncate: bool):
    with conn.cursor() as cur:
        if truncate:
            log.info("Truncating existing Phase 1 tables...")
            cur.execute("TRUNCATE TABLE shipment_chat_log, shipment_issues, tracking_events, shipments, customers RESTART IDENTITY CASCADE;")

        log.info("Disabling auto-journey trigger for bulk load...")
        cur.execute("ALTER TABLE shipments DISABLE TRIGGER trg_shipments_stage_history;")

        log.info("Inserting %d customers...", len(data["customers"]))
        bulk_insert(cur, "customers", CUSTOMER_COLS, data["customers"])

        log.info("Inserting %d shipments...", len(data["shipments"]))
        bulk_insert(cur, "shipments", SHIPMENT_COLS, data["shipments"])

        log.info("Inserting %d tracking_events...", len(data["tracking_events"]))
        bulk_insert(cur, "tracking_events", EVENT_COLS, data["tracking_events"], page_size=5000)

        log.info("Inserting %d shipment_issues...", len(data["shipment_issues"]))
        bulk_insert(cur, "shipment_issues", ISSUE_COLS, data["shipment_issues"])

        log.info("Inserting %d shipment_chat_log rows...", len(data["chat_logs"]))
        bulk_insert(cur, "shipment_chat_log", CHAT_COLS, data["chat_logs"])

        log.info("Re-enabling auto-journey trigger...")
        cur.execute("ALTER TABLE shipments ENABLE TRIGGER trg_shipments_stage_history;")

    conn.commit()
    with conn.cursor() as cur:
        log.info("Running ANALYZE...")
        cur.execute("ANALYZE customers, shipments, tracking_events, shipment_issues, shipment_chat_log;")
    conn.commit()


# =============================================================================
# REAL-TIME SUMMARY REPORT
# =============================================================================

def _row_to_dict(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _jsonable(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    return obj


def generate_summary_report(conn) -> dict:
    report = {"generated_at": datetime.now(timezone.utc).isoformat()}
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM v_dashboard_headline;")
        report["headline"] = _row_to_dict(cur)[0]

        cur.execute("SELECT * FROM v_status_breakdown;")
        report["status_breakdown"] = _row_to_dict(cur)

        cur.execute("SELECT * FROM v_ontime_performance;")
        report["ontime_performance"] = _row_to_dict(cur)[0]

        cur.execute("SELECT * FROM v_delay_reason_breakdown;")
        report["delay_reason_breakdown"] = _row_to_dict(cur)

        cur.execute("SELECT * FROM v_open_issues_summary;")
        report["open_issues_summary"] = _row_to_dict(cur)

        cur.execute("SELECT * FROM v_domestic_vs_international;")
        report["domestic_vs_international"] = _row_to_dict(cur)

        cur.execute("SELECT * FROM v_service_level_mix;")
        report["service_level_mix"] = _row_to_dict(cur)

        cur.execute("SELECT * FROM v_top_customers LIMIT 10;")
        report["top_10_customers"] = _row_to_dict(cur)

        cur.execute("SELECT * FROM v_chat_activity_summary;")
        report["chat_activity"] = _row_to_dict(cur)[0]

    # normalize Decimals/datetimes for JSON serialization
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(v) for v in o]
        return _jsonable(o)

    return clean(report)


def print_report(report: dict):
    hl = report["headline"]
    print("\n" + "=" * 78)
    print(" REAL-TIME SHIPMENT DASHBOARD — SUMMARY SNAPSHOT")
    print(" generated_at:", report["generated_at"])
    print("=" * 78)
    print(f" Total shipments........... {hl['total_shipments']:>8}")
    print(f" Total customers........... {hl['total_customers']:>8}")
    print(f" Delivered................. {hl['delivered_count']:>8}   On-time %: {hl['on_time_pct']}")
    print(f" In-transit & overdue...... {hl['in_transit_overdue']:>8}")
    print(f" Open issues (attention).. {hl['open_issues']:>8}")
    print(f" International shipments... {hl['international_shipments']:>8}")
    print(f" Currently in customs hold.. {hl['customs_held_now']:>8}")
    print(f" Lost / Returned / Cancelled {hl['lost_count']:>4} / {hl['returned_count']:>4} / {hl['cancelled_count']:>4}")

    print("\n-- Status breakdown " + "-" * 57)
    for r in report["status_breakdown"]:
        print(f"   {r['current_status']:<32} {r['shipment_count']:>7}   ({r['pct_of_total']}%)")

    op = report["ontime_performance"]
    print("\n-- On-time performance " + "-" * 54)
    print(f"   Delivered on-time: {op['delivered_on_time']}   Delivered late: {op['delivered_late']}"
          f"   On-time %: {op['on_time_pct']}   Avg delay when late (hrs): {op['avg_delay_hours_when_late']}")

    print("\n-- Delay reason breakdown " + "-" * 51)
    for r in report["delay_reason_breakdown"]:
        print(f"   {r['reason_for_delay']:<18} {r['shipment_count']:>7}   ({r['pct_of_delayed']}% of delayed)")

    print("\n-- Open issues needing attention " + "-" * 44)
    for r in report["open_issues_summary"]:
        print(f"   {r['issue_type']:<26} {r['status']:<14} {r['issue_count']:>6}   avg age/res (hrs): {r['avg_age_or_resolution_hours']}")

    print("\n-- Domestic vs International " + "-" * 48)
    for r in report["domestic_vs_international"]:
        print(f"   {r['shipment_scope']:<14} {r['shipment_count']:>7}   customs held: {r['customs_held']}   pending: {r['customs_pending']}   cleared: {r['customs_cleared']}")

    print("\n-- Service level mix " + "-" * 56)
    for r in report["service_level_mix"]:
        print(f"   {r['delivery_type']:<24} {r['shipment_count']:>7}   on-time %: {r['on_time_pct']}")

    print("\n-- Top 10 customers by volume " + "-" * 47)
    for r in report["top_10_customers"]:
        print(f"   {r['org_name']:<30} {r['shipment_count']:>6}   on-time %: {r['on_time_pct']}")

    ca = report["chat_activity"]
    print("\n-- AI chat activity " + "-" * 57)
    print(f"   Total interactions: {ca['total_chat_interactions']}   Shipments with chat: {ca['shipments_with_chat']}"
          f"   Avg confidence: {ca['avg_confidence']}   Low-confidence (review): {ca['low_confidence_needing_review']}")
    print("=" * 78 + "\n")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Seed Phase 1 shipping schema with realistic scenario-based data.")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/shipdb_phase1"))
    parser.add_argument("--shipments", type=int, default=25000)
    parser.add_argument("--customers", type=int, default=800)
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--days-forward", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--no-truncate", action="store_true",
        help=(
            "Skip the default truncate-and-reload behavior and append on top of existing data instead. "
            "NOT recommended: tracking_id/order_id are generated from a counter that restarts at 1 each "
            "run, so appending without --truncate will hit duplicate-key errors unless you also pass "
            "--id-offset to shift the generated IDs past what's already in the table."
        ),
    )
    parser.add_argument(
        "--id-offset", type=int, default=0,
        help="Offset added to the tracking_id/order_id counter. Only relevant with --no-truncate.",
    )
    parser.add_argument("--report-json", default=None, help="Optional path to write the summary report as JSON.")
    parser.add_argument("--skip-report", action="store_true")
    args = parser.parse_args()

    if Faker is None:
        log.error("The 'faker' package is required. Install with: pip install faker --break-system-packages")
        sys.exit(1)

    fake = Faker()
    Faker.seed(args.seed)

    log.info("Connecting to %s", args.dsn.split("@")[-1])
    conn = psycopg2.connect(args.dsn)

    try:
        t0 = datetime.now()
        log.info("Generating dataset: %d shipments, %d customers, window -%dd/+%dd, seed=%d",
                  args.shipments, args.customers, args.days_back, args.days_forward, args.seed)
        data = generate_dataset(args, fake)
        log.info("Generation complete in %.1fs. Rows: shipments=%d events=%d issues=%d chats=%d",
                  (datetime.now() - t0).total_seconds(),
                  len(data["shipments"]), len(data["tracking_events"]),
                  len(data["shipment_issues"]), len(data["chat_logs"]))

        truncate = not args.no_truncate
        if not truncate:
            log.warning(
                "Running in --no-truncate (append) mode. This will fail with a duplicate-key "
                "error unless --id-offset is set past the highest existing counter."
            )
        t1 = datetime.now()
        load_dataset(conn, data, truncate=truncate)
        log.info("Database load complete in %.1fs.", (datetime.now() - t1).total_seconds())

        if not args.skip_report:
            report = generate_summary_report(conn)
            print_report(report)
            if args.report_json:
                with open(args.report_json, "w") as f:
                    json.dump(report, f, indent=2)
                log.info("Summary report written to %s", args.report_json)

        log.info("Done.")
    except Exception:
        conn.rollback()
        log.exception("Seeding failed; transaction rolled back.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
