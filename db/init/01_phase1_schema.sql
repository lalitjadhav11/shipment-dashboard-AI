-- =============================================================================
-- PHASE 1 — SHIPMENT + CUSTOMER DATA MODEL (DDL)
-- Target: PostgreSQL 15+
-- Reconstructed from Phase1_Shipment_Customer_Design_Document.docx and the
-- column contracts in 03_generate_phase1_data.py.
--
-- Five tables:
--   customers, shipments (primary entities)
--   tracking_events, shipment_issues, shipment_chat_log (AI-chat support)
--
-- tracking_id (VARCHAR, not UUID) is the shipment primary key — it is the
-- stable, externally-visible identifier the chat agent and customers key off.
-- =============================================================================

-- gen_random_uuid() is built into PostgreSQL 13+ (pgcrypto no longer required),
-- but we create the extension defensively in case of an older base image.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- ENUM TYPES
-- -----------------------------------------------------------------------------
CREATE TYPE package_type_enum AS ENUM (
  'BOX', 'ENVELOPE', 'TUBE', 'CRATE', 'PALLET', 'CUSTOM'
);

CREATE TYPE package_size_enum AS ENUM (
  'SMALL', 'MEDIUM', 'LARGE', 'EXTRA_LARGE', 'PALLET_SIZED'
);

CREATE TYPE delivery_type_enum AS ENUM (
  'STANDARD', 'EXPRESS', 'OVERNIGHT', 'ECONOMY', 'INTERNATIONAL_PRIORITY'
);

CREATE TYPE customs_status_enum AS ENUM (
  'NOT_REQUIRED', 'PENDING', 'HELD', 'CLEARED', 'REJECTED'
);

-- 13 happy-path stages + 4 terminal-failure states. Shared by
-- shipments.current_status and tracking_events.stage.
CREATE TYPE shipment_status_enum AS ENUM (
  'LABEL_CREATED',
  'SHIPMENT_CREATED',
  'PACKAGE_RECEIVED',
  'TRACKING_ID_ISSUED',
  'IN_TRANSIT_TO_ORIGIN_HUB',
  'AT_DISTRIBUTION_HUB',
  'IN_TRANSIT',
  'AT_CONNECTING_HUB',
  'CUSTOMS_HOLD',
  'CUSTOMS_CLEARED',
  'IN_TRANSIT_TO_DESTINATION_HUB',
  'OUT_FOR_DELIVERY',
  'DELIVERED',
  'DELIVERY_FAILED',
  'RETURNED_TO_SENDER',
  'LOST',
  'CANCELLED'
);

CREATE TYPE reason_for_delay_enum AS ENUM (
  'NONE', 'CUSTOMS', 'WEATHER', 'CIVIL_UNREST', 'LOST_PACKAGE',
  'MECHANICAL_ISSUE', 'ADDRESS_ISSUE', 'OTHER'
);

CREATE TYPE issue_type_enum AS ENUM (
  'CUSTOMS_HOLD', 'WEATHER_DELAY', 'CIVIL_UNREST', 'LOST_PACKAGE',
  'FAILED_DELIVERY_ATTEMPT', 'ADDRESS_ISSUE', 'OTHER'
);

CREATE TYPE issue_status_enum AS ENUM (
  'OPEN', 'INVESTIGATING', 'RESOLVED', 'CLOSED'
);

-- -----------------------------------------------------------------------------
-- 1. customers — the organization / account holder
-- -----------------------------------------------------------------------------
CREATE TABLE customers (
  customer_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fedex_account_id  VARCHAR(30)  NOT NULL UNIQUE,
  org_name          VARCHAR(200) NOT NULL,
  customer_profile  JSONB        NOT NULL DEFAULT '{}'::jsonb,
  is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
  created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 2. shipments — tracking-ID-centric core entity
--    Package, customs, pickup and delivery-appointment detail folded in.
-- -----------------------------------------------------------------------------
CREATE TABLE shipments (
  tracking_id              VARCHAR(40) PRIMARY KEY,
  order_id                 VARCHAR(50),
  customer_id              UUID NOT NULL REFERENCES customers(customer_id),

  -- package (folded in)
  package_type             package_type_enum NOT NULL,
  package_desc             VARCHAR(255),
  package_size             package_size_enum NOT NULL,
  package_weight_kg        NUMERIC(10,3),

  delivery_type            delivery_type_enum NOT NULL,
  is_international          BOOLEAN NOT NULL DEFAULT FALSE,

  -- locations
  src_loc                  JSONB NOT NULL,
  dest_loc                 JSONB NOT NULL,

  -- customs (folded in)
  customs_status           customs_status_enum NOT NULL DEFAULT 'NOT_REQUIRED',

  -- pickup scheduling (folded in)
  pickup_date              DATE,
  pickup_window_start      TIME,
  pickup_window_end        TIME,

  -- delivery appointment (folded in)
  delivery_window_start    TIMESTAMPTZ,
  delivery_window_end      TIMESTAMPTZ,

  -- journey + outcome
  current_status           shipment_status_enum NOT NULL DEFAULT 'LABEL_CREATED',
  estimated_delivery       TIMESTAMPTZ,
  delivery_date            TIMESTAMPTZ,

  -- delay / exception detail
  reason_for_delay         reason_for_delay_enum NOT NULL DEFAULT 'NONE',
  delay_comments           TEXT,
  failed_delivery_attempts SMALLINT NOT NULL DEFAULT 0,
  last_delivery_attempt_at TIMESTAMPTZ,

  comments                 TEXT,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 3. tracking_events — append-only journey log (one row per stage transition)
-- -----------------------------------------------------------------------------
CREATE TABLE tracking_events (
  event_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tracking_id      VARCHAR(40) NOT NULL REFERENCES shipments(tracking_id) ON DELETE CASCADE,
  stage            shipment_status_enum NOT NULL,
  location         VARCHAR(200),
  event_timestamp  TIMESTAMPTZ NOT NULL DEFAULT now(),
  notes            TEXT
);

-- -----------------------------------------------------------------------------
-- 4. shipment_issues — one row per delay / failed-delivery / RCA incident
-- -----------------------------------------------------------------------------
CREATE TABLE shipment_issues (
  issue_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tracking_id   VARCHAR(40) NOT NULL REFERENCES shipments(tracking_id) ON DELETE CASCADE,
  issue_type    issue_type_enum NOT NULL,
  description   TEXT,
  status        issue_status_enum NOT NULL DEFAULT 'OPEN',
  reported_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at   TIMESTAMPTZ
);

-- -----------------------------------------------------------------------------
-- 5. shipment_chat_log — audit log of every AI chat interaction
-- -----------------------------------------------------------------------------
CREATE TABLE shipment_chat_log (
  chat_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tracking_id       VARCHAR(40) REFERENCES shipments(tracking_id) ON DELETE SET NULL,
  customer_id       UUID REFERENCES customers(customer_id) ON DELETE SET NULL,
  user_query        TEXT NOT NULL,
  ai_response       TEXT NOT NULL,
  context_snapshot  JSONB,
  confidence_score  NUMERIC(5,4),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- INDEXES — back the dashboard summary views and common lookups
-- -----------------------------------------------------------------------------
CREATE INDEX idx_shipments_customer_id       ON shipments (customer_id);
CREATE INDEX idx_shipments_current_status    ON shipments (current_status);
CREATE INDEX idx_shipments_reason_for_delay  ON shipments (reason_for_delay);
CREATE INDEX idx_shipments_created_at        ON shipments (created_at);
CREATE INDEX idx_shipments_delivery_date     ON shipments (delivery_date);
CREATE INDEX idx_shipments_estimated_deliv   ON shipments (estimated_delivery);
CREATE INDEX idx_shipments_is_international   ON shipments (is_international);
CREATE INDEX idx_shipments_customs_status    ON shipments (customs_status);
CREATE INDEX idx_shipments_delivery_type     ON shipments (delivery_type);

CREATE INDEX idx_tracking_events_tracking_id ON tracking_events (tracking_id);
CREATE INDEX idx_tracking_events_timestamp   ON tracking_events (event_timestamp);

CREATE INDEX idx_shipment_issues_tracking_id ON shipment_issues (tracking_id);
CREATE INDEX idx_shipment_issues_type_status ON shipment_issues (issue_type, status);

CREATE INDEX idx_chat_log_tracking_id        ON shipment_chat_log (tracking_id);
CREATE INDEX idx_chat_log_confidence         ON shipment_chat_log (confidence_score);

-- -----------------------------------------------------------------------------
-- TRIGGER: keep updated_at fresh on customers + shipments
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_customers_updated_at
  BEFORE UPDATE ON customers
  FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_shipments_updated_at
  BEFORE UPDATE ON shipments
  FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- -----------------------------------------------------------------------------
-- TRIGGER: auto journey-stage logging
-- Appends a tracking_events row whenever shipments.current_status changes
-- (and on insert). The bulk seeder disables this trigger by name
-- (trg_shipments_stage_history) so it can supply its own authoritative,
-- back-dated journey rows instead of now()-only stamps.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_log_stage_change() RETURNS trigger AS $$
BEGIN
  IF (TG_OP = 'INSERT') OR (NEW.current_status IS DISTINCT FROM OLD.current_status) THEN
    INSERT INTO tracking_events (tracking_id, stage, location, event_timestamp, notes)
    VALUES (NEW.tracking_id, NEW.current_status, NULL, now(), 'Auto-logged on status change');
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_shipments_stage_history
  AFTER INSERT OR UPDATE OF current_status ON shipments
  FOR EACH ROW EXECUTE FUNCTION fn_log_stage_change();

-- -----------------------------------------------------------------------------
-- VIEW: v_shipment_journey_summary
-- Single grounded-context query for the AI Shipment Journey Summary chat.
-- Returns status, customs, ETA, delay reason, open-issue count and the full
-- ordered journey timeline as one JSON payload per tracking_id.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_shipment_journey_summary AS
SELECT
  s.tracking_id,
  s.order_id,
  s.customer_id,
  c.org_name,
  s.current_status,
  s.is_international,
  s.customs_status,
  s.delivery_type,
  s.estimated_delivery,
  s.delivery_date,
  s.reason_for_delay,
  s.delay_comments,
  s.src_loc,
  s.dest_loc,
  (SELECT count(*) FROM shipment_issues i
     WHERE i.tracking_id = s.tracking_id
       AND i.status IN ('OPEN', 'INVESTIGATING')) AS open_issue_count,
  (SELECT coalesce(
            jsonb_agg(jsonb_build_object(
              'stage', te.stage,
              'location', te.location,
              'event_timestamp', te.event_timestamp,
              'notes', te.notes
            ) ORDER BY te.event_timestamp),
            '[]'::jsonb)
     FROM tracking_events te
     WHERE te.tracking_id = s.tracking_id) AS journey_timeline
FROM shipments s
JOIN customers c ON c.customer_id = s.customer_id;

COMMENT ON VIEW v_shipment_journey_summary IS
  'Single grounded-context payload for the Phase 1 AI Shipment Journey Summary chat.';
