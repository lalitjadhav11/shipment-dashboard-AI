#!/usr/bin/env python3
"""
seed.py — one-shot idempotent DB seeder entrypoint.

Runs inside the `seeder` container after Postgres is healthy. It:
  1. Waits for the database to accept connections.
  2. Skips seeding if the shipments table is already populated (idempotent —
     safe to re-run `docker compose up` without re-truncating a full dataset).
  3. Otherwise invokes 03_generate_phase1_data.py, which generates the full
     scenario-based dataset and bulk-loads it.

The schema + dashboard views themselves are created by Postgres automatically
from /docker-entrypoint-initdb.d on first boot (see docker-compose.yml), so this
container only owns DATA, not DDL.
"""
import os
import sys
import time

import psycopg2

DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@db:5432/shipdb_phase1",
)
SHIPMENTS = os.environ.get("SEED_SHIPMENTS", "25000")
CUSTOMERS = os.environ.get("SEED_CUSTOMERS", "800")
SEED = os.environ.get("SEED_RANDOM_SEED", "42")


def wait_for_db(dsn: str, timeout: int = 120) -> None:
    print(f"[seeder] waiting for database at {dsn.split('@')[-1]} ...", flush=True)
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            conn = psycopg2.connect(dsn)
            conn.close()
            print("[seeder] database is up.", flush=True)
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
    raise SystemExit(f"[seeder] database not reachable within {timeout}s: {last_err}")


def already_seeded(dsn: str) -> bool:
    try:
        conn = psycopg2.connect(dsn)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM shipments;")
            (count,) = cur.fetchone()
        conn.close()
        return count > 0
    except Exception as e:  # noqa: BLE001
        # Table may not exist yet if init scripts are still running; treat as not seeded.
        print(f"[seeder] could not read shipments count ({e}); will attempt seed.", flush=True)
        return False


def main() -> None:
    wait_for_db(DSN)

    if already_seeded(DSN):
        print("[seeder] shipments table already populated — skipping seed.", flush=True)
        return

    print(f"[seeder] seeding {SHIPMENTS} shipments / {CUSTOMERS} customers ...", flush=True)
    import subprocess

    cmd = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "03_generate_phase1_data.py"),
        "--dsn", DSN,
        "--shipments", SHIPMENTS,
        "--customers", CUSTOMERS,
        "--seed", SEED,
    ]
    rc = subprocess.call(cmd)
    if rc != 0:
        raise SystemExit(f"[seeder] data generator failed with exit code {rc}")
    print("[seeder] seeding complete.", flush=True)


if __name__ == "__main__":
    main()
