-- =============================================================================
-- PHASE 1 — REAL-TIME DASHBOARD SUMMARY VIEWS
-- Target: PostgreSQL 15+  (builds on 01_phase1_schema.sql)
-- These views back the "real-time summary report" — they are cheap enough to
-- query on every dashboard page load (all aggregate over indexed columns) and
-- are also used by 03_generate_summary_report.py to render a point-in-time
-- console/JSON snapshot after data loads.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Overall status breakdown — counts + share of total by current_status
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_status_breakdown AS
SELECT
  current_status,
  count(*) AS shipment_count,
  round(100.0 * count(*) / NULLIF((SELECT count(*) FROM shipments), 0), 2) AS pct_of_total
FROM shipments
GROUP BY current_status
ORDER BY shipment_count DESC;

-- -----------------------------------------------------------------------------
-- 2. On-time performance — only meaningful for shipments that have a verdict
--    (delivered, or terminal failure states count as "not on time")
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ontime_performance AS
SELECT
  count(*) FILTER (WHERE current_status = 'DELIVERED') AS delivered_count,
  count(*) FILTER (WHERE current_status = 'DELIVERED' AND delivery_date <= estimated_delivery) AS delivered_on_time,
  count(*) FILTER (WHERE current_status = 'DELIVERED' AND delivery_date > estimated_delivery) AS delivered_late,
  round(
    100.0 * count(*) FILTER (WHERE current_status = 'DELIVERED' AND delivery_date <= estimated_delivery)
    / NULLIF(count(*) FILTER (WHERE current_status = 'DELIVERED'), 0), 2
  ) AS on_time_pct,
  round(
    avg(EXTRACT(EPOCH FROM (delivery_date - estimated_delivery)) / 3600.0)
      FILTER (WHERE current_status = 'DELIVERED' AND delivery_date > estimated_delivery), 1
  ) AS avg_delay_hours_when_late,
  count(*) FILTER (WHERE current_status NOT IN ('DELIVERED') AND estimated_delivery < now()
                     AND current_status NOT IN ('CANCELLED','RETURNED_TO_SENDER','LOST')) AS in_transit_overdue
FROM shipments;

-- -----------------------------------------------------------------------------
-- 3. Delay reason breakdown (active + historical)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_delay_reason_breakdown AS
SELECT
  reason_for_delay,
  count(*) AS shipment_count,
  round(100.0 * count(*) / NULLIF((SELECT count(*) FROM shipments WHERE reason_for_delay <> 'NONE'), 0), 2) AS pct_of_delayed
FROM shipments
WHERE reason_for_delay <> 'NONE'
GROUP BY reason_for_delay
ORDER BY shipment_count DESC;

-- -----------------------------------------------------------------------------
-- 4. Open issues needing attention — the dashboard "critical areas" widget
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_open_issues_summary AS
SELECT
  issue_type,
  status,
  count(*) AS issue_count,
  round(avg(EXTRACT(EPOCH FROM (COALESCE(resolved_at, now()) - reported_at)) / 3600.0), 1) AS avg_age_or_resolution_hours
FROM shipment_issues
GROUP BY issue_type, status
ORDER BY (status = 'OPEN') DESC, issue_count DESC;

-- -----------------------------------------------------------------------------
-- 5. Domestic vs international split + customs snapshot
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_domestic_vs_international AS
SELECT
  CASE WHEN is_international THEN 'INTERNATIONAL' ELSE 'DOMESTIC' END AS shipment_scope,
  count(*) AS shipment_count,
  count(*) FILTER (WHERE customs_status = 'HELD') AS customs_held,
  count(*) FILTER (WHERE customs_status = 'PENDING') AS customs_pending,
  count(*) FILTER (WHERE customs_status = 'CLEARED') AS customs_cleared
FROM shipments
GROUP BY shipment_scope;

-- -----------------------------------------------------------------------------
-- 6. Daily volume trend — created vs delivered, last/next 60 days
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_daily_volume_trend AS
SELECT
  d.day,
  count(DISTINCT s1.tracking_id) AS shipments_created,
  count(DISTINCT s2.tracking_id) AS shipments_delivered
FROM generate_series(
       (SELECT min(created_at)::date FROM shipments),
       (SELECT max(created_at)::date FROM shipments),
       interval '1 day'
     ) AS d(day)
LEFT JOIN shipments s1 ON s1.created_at::date = d.day
LEFT JOIN shipments s2 ON s2.delivery_date::date = d.day
GROUP BY d.day
ORDER BY d.day;

-- -----------------------------------------------------------------------------
-- 7. Delivery-type / service-level mix
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_service_level_mix AS
SELECT
  delivery_type,
  count(*) AS shipment_count,
  round(100.0 * count(*) FILTER (WHERE current_status='DELIVERED' AND delivery_date <= estimated_delivery)
        / NULLIF(count(*) FILTER (WHERE current_status='DELIVERED'), 0), 2) AS on_time_pct
FROM shipments
GROUP BY delivery_type
ORDER BY shipment_count DESC;

-- -----------------------------------------------------------------------------
-- 8. Top customers by shipment volume (with their own on-time rate)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_top_customers AS
SELECT
  c.org_name,
  c.fedex_account_id,
  count(s.tracking_id) AS shipment_count,
  count(*) FILTER (WHERE s.current_status = 'DELIVERED') AS delivered_count,
  round(100.0 * count(*) FILTER (WHERE s.current_status = 'DELIVERED' AND s.delivery_date <= s.estimated_delivery)
        / NULLIF(count(*) FILTER (WHERE s.current_status = 'DELIVERED'), 0), 2) AS on_time_pct
FROM customers c
JOIN shipments s ON s.customer_id = c.customer_id
GROUP BY c.org_name, c.fedex_account_id
ORDER BY shipment_count DESC
LIMIT 25;

-- -----------------------------------------------------------------------------
-- 9. AI chat feature usage snapshot
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_chat_activity_summary AS
SELECT
  count(*) AS total_chat_interactions,
  count(DISTINCT tracking_id) AS shipments_with_chat,
  round(avg(confidence_score), 4) AS avg_confidence,
  count(*) FILTER (WHERE confidence_score < 0.75) AS low_confidence_needing_review
FROM shipment_chat_log;

-- -----------------------------------------------------------------------------
-- 10. One-shot "everything" snapshot — single round trip for a dashboard header
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_dashboard_headline AS
SELECT
  (SELECT count(*) FROM shipments) AS total_shipments,
  (SELECT count(*) FROM customers) AS total_customers,
  (SELECT delivered_count FROM v_ontime_performance) AS delivered_count,
  (SELECT on_time_pct FROM v_ontime_performance) AS on_time_pct,
  (SELECT in_transit_overdue FROM v_ontime_performance) AS in_transit_overdue,
  (SELECT count(*) FROM shipment_issues WHERE status IN ('OPEN','INVESTIGATING')) AS open_issues,
  (SELECT count(*) FROM shipments WHERE is_international) AS international_shipments,
  (SELECT count(*) FROM shipments WHERE current_status = 'CUSTOMS_HOLD') AS customs_held_now,
  (SELECT count(*) FROM shipments WHERE current_status = 'LOST') AS lost_count,
  (SELECT count(*) FROM shipments WHERE current_status = 'RETURNED_TO_SENDER') AS returned_count,
  (SELECT count(*) FROM shipments WHERE current_status = 'CANCELLED') AS cancelled_count,
  now() AS generated_at;

COMMENT ON VIEW v_dashboard_headline IS 'Single-row headline snapshot for the top of the real-time dashboard — one query, ten KPIs.';
