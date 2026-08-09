"""
Stage 2 entity-extraction regression tests. All pure/offline once
conftest.py's autouse fixture pre-fills the org_name/city caches — no DB
connection ever attempted. Values verified live against the running model
before being encoded here (see the PR/commit that introduced this file).
"""
from chat import entities


def test_tracking_id_basic():
    e = entities.extract_entities("Where is tracking number 700000000001 right now?")
    assert e.tracking_id == "700000000001"
    assert e.tracking_ids == ["700000000001"]


def test_tracking_id_glued_to_following_word():
    # Regression for AGENTIC_RAG_ARCHITECTURE.md §21 — \b never fires between
    # a digit and a following letter, so a naive word-boundary regex misses
    # this entirely.
    e = entities.extract_entities("800000000019give the summary of status")
    assert e.tracking_id == "800000000019"


def test_multiple_tracking_ids_preserve_order():
    e = entities.extract_entities("Compare 700000000001 and 100000000002")
    assert e.tracking_ids == ["700000000001", "100000000002"]


def test_no_false_positive_on_short_numbers():
    # order IDs, phone numbers etc. shouldn't be mistaken for tracking IDs —
    # the regex requires 9-15 consecutive digits.
    e = entities.extract_entities("my order ORD-2026-0000001 has 5 items, call 555-1234")
    assert e.tracking_id is None


def test_enum_prefix_word_match_customs_hold():
    # Regression for AGENTIC_RAG_ARCHITECTURE.md §13 — "customs" alone must
    # match CUSTOMS_HOLD via the leading-word prefix score, not just an exact
    # multi-word phrase match.
    e = entities.extract_entities("give me 5 shipments those are at customs")
    assert e.enum_matches.get("current_status") == "CUSTOMS_HOLD"


def test_enum_no_false_positive_lost_package_from_bare_package_word():
    # Regression for AGENTIC_RAG_ARCHITECTURE.md §13/entities.py docstring —
    # "package" is the SECOND word of "lost package", so a bare "package"
    # must NOT match LOST_PACKAGE (only a genuine leading-word match should).
    e = entities.extract_entities("pallet package shipments")
    assert e.enum_matches.get("reason_for_delay") != "LOST_PACKAGE"


def test_enum_multiword_exact_match():
    e = entities.extract_entities("Show me all pallet-sized package shipments")
    assert e.enum_matches.get("package_size") == "PALLET_SIZED"
    assert e.enum_matches.get("package_type") == "PALLET"


def test_date_extraction_embedded_in_sentence():
    # Regression for AGENTIC_RAG_ARCHITECTURE.md §12.1 — dateparser.parse()
    # requires the WHOLE string to be a date; search_dates() (substring
    # extraction) is required for a date embedded in a real question.
    e = entities.extract_entities("Which shipments are scheduled for pickup on July 17th?")
    assert e.dates and e.dates[0].startswith("2026-07-17")


def test_date_extraction_no_false_positive_on_show_me():
    # Regression for AGENTIC_RAG_ARCHITECTURE.md §12.1 — search_dates() with
    # no languages= pin matched the bare word "me" as a date in some non-
    # English locale.
    e = entities.extract_entities("Show me all our large shipments")
    assert e.dates == []


def test_org_name_fuzzy_match(_patched_org_and_city_cache):
    e = entities.extract_entities("Show me all shipments for Smith Ltd")
    assert e.org_name == "Smith Ltd"


def test_location_fuzzy_match(_patched_org_and_city_cache):
    e = entities.extract_entities("Which shipments are going to or coming from Seattle?")
    assert e.location == "Seattle"


def test_org_name_not_matched_when_absent(_patched_org_and_city_cache):
    e = entities.extract_entities("Compare 700000000001 and 100000000002")
    assert e.org_name is None
    assert e.location is None


def test_org_names_plural_detects_multiple_customers(monkeypatch):
    import time
    monkeypatch.setitem(entities._ORG_NAME_CACHE, "names", ["Acme Corp", "Globex"])
    monkeypatch.setitem(entities._ORG_NAME_CACHE, "loaded_at", time.monotonic())

    e = entities.extract_entities("compare Acme Corp and Globex")
    assert set(e.org_names) == {"Acme Corp", "Globex"}
    assert e.org_name in e.org_names  # first-match backward-compat field stays consistent


def test_org_names_single_customer_stays_singular(_patched_org_and_city_cache):
    # False-positive guard: with the FULL 7-name fixture list loaded (not
    # just the two deliberately-similar names above), a genuine single-
    # customer query must not spuriously multi-match an unrelated name via
    # fuzz.partial_ratio's substring scoring.
    e = entities.extract_entities("Show me all shipments for Smith Ltd")
    assert e.org_names == ["Smith Ltd"]
