"""Tests for backend/impact.py. They need Neo4j with the model loaded and are skipped otherwise.

Expected values were worked out from the Excel (Relationships sheet).
"""

import pytest

from backend.graph import check_connection, close_driver, run_query
from backend.impact import DOCUMENTED_FACT, INFERENCE, analyze_replacement, label_for


@pytest.fixture(scope="module", autouse=True)
def graph():
    ok, message = check_connection()
    if not ok:
        pytest.skip(message)
    if not run_query("MATCH (a:Application) RETURN count(a) AS c")[0]["c"]:
        pytest.skip("Model not loaded; run python scripts/load_graph.py")
    yield
    close_driver()


def subjects(result, kind):
    return {f.subject for f in result.by_kind(kind)}


def gap_kinds(result):
    return {g.kind for g in result.gaps}


# --- Labels ---


def test_label_follows_status():
    assert label_for("Documented") == DOCUMENTED_FACT
    assert label_for("Documented", "Documented") == DOCUMENTED_FACT
    assert label_for("Documented", "Inferred") == INFERENCE
    assert label_for("Illustrative") == INFERENCE


# --- Applications ---


def test_booking_system():
    r = analyze_replacement("Booking System")
    assert r.status == "ok" and r.application == "Booking System"
    assert subjects(r, "realizes") == {
        "Create Reservation", "Execute Book On Hold", "Search Basic Flight", "Search Flight",
        "Search Multistop Flight", "Search Rebook Flight", "Service List",
    }
    assert subjects(r, "uncovered_capability") == subjects(r, "realizes") - {"Create Reservation"}
    [covered] = r.by_kind("still_covered_capability")
    assert covered.subject == "Create Reservation"
    assert covered.related == ["Direct Sales Platform"]
    assert covered.label == DOCUMENTED_FACT
    assert subjects(r, "subdomain") == {"Reservation Ticketing"}
    assert subjects(r, "domain") == {"Sales"}
    assert {"no_platform", "no_dependencies"} <= gap_kinds(r)


def test_direct_sales_platform_shares_create_reservation():
    r = analyze_replacement("Direct Sales Platform")
    [covered] = r.by_kind("still_covered_capability")
    assert covered.subject == "Create Reservation"
    assert covered.related == ["Booking System"]
    assert covered.paths == ["Direct Sales Platform -[REALIZES]-> Create Reservation <-[REALIZES]- Booking System"]
    assert subjects(r, "uncovered_capability") == {"Manage Booking", "Manage Order", "Search And Offer Product"}


def test_product_maintenance_application_in_two_domains():
    r = analyze_replacement("Product Maintenance Application")
    assert subjects(r, "subdomain") == {"Product Management & Design"}
    assert subjects(r, "domain") == {"Product", "Maintenance"}
    assert len(r.by_kind("uncovered_capability")) == 7
    # Domains shared with the app's own domains are not reported as connected.
    assert not subjects(r, "connected_domain") & {"Product", "Maintenance"}


def test_notifying_service_has_no_platform():
    r = analyze_replacement("Notifying Service")
    assert r.by_kind("platform") == []
    assert r.by_kind("platform_peer") == []
    assert "no_platform" in gap_kinds(r)
    assert subjects(r, "uncovered_capability") == {"Notify Service Disruption", "Notify Service Fulfillment"}
    assert subjects(r, "domain") == {"Marketing & Customer Care"}


def test_baggage_tracking_system_inferred_platform():
    r = analyze_replacement("Baggage Tracking System")
    [platform] = r.by_kind("platform")
    assert platform.subject == "Tracking Platform"
    assert platform.label == INFERENCE  # the link's Status is Inferred
    assert "Fig. 22" in platform.statement
    assert subjects(r, "platform_peer") == {"Cargo Tracking Application"}
    assert all(f.label == INFERENCE for f in r.by_kind("platform_peer"))
    assert "no_platform" not in gap_kinds(r)


def test_accounting_components_and_direct_domain():
    r = analyze_replacement("Accounting")
    assert subjects(r, "component") == {"Manage Business Transactions", "Manage Enterprise Resources"}
    assert subjects(r, "domain") == {"Network & Fleet Planning"}
    assert r.by_kind("subdomain") == []


def test_application_without_capabilities_reports_gap():
    r = analyze_replacement("Group Sales Tool")
    assert r.by_kind("realizes") == []
    assert "no_capabilities" in gap_kinds(r)


def test_connected_domains_are_inferences_with_paths():
    r = analyze_replacement("Booking System")
    connected = r.by_kind("connected_domain")
    assert "Revenue Management & Pricing" in {f.subject for f in connected}
    for f in connected:
        assert f.label == INFERENCE
        assert all("USED_IN" in p and p.startswith("Sales <-") for p in f.paths)


# --- Name resolution ---


def test_case_insensitive_exact_match():
    assert analyze_replacement("  booking   SYSTEM ").application == "Booking System"


def test_leading_article_is_not_part_of_the_name():
    # Found by the evaluation: the parser sometimes keeps "the".
    assert analyze_replacement("the Baggage Tracking System").application == "Baggage Tracking System"
    assert analyze_replacement("our booking system").application == "Booking System"
    assert analyze_replacement("the booking tool").status == "ambiguous"


def test_ambiguous_name_offers_choices_without_guessing():
    r = analyze_replacement("the booking tool")
    assert r.status == "ambiguous"
    assert "Booking System" in r.candidates
    assert r.findings == [] and r.application is None


def test_partial_name_lists_all_matches():
    r = analyze_replacement("Crew")
    assert r.status == "ambiguous"
    assert set(r.candidates) == {"Crew Management System", "Cabin Crew Support System"}


def test_unknown_name():
    r = analyze_replacement("Quantum Teleporter")
    assert r.status == "not_found" and r.candidates == [] and r.findings == []


def test_non_application_element():
    r = analyze_replacement("Create Reservation")
    assert r.status == "not_found"
    assert "Capability" in r.message


def test_user_text_is_only_a_parameter():
    r = analyze_replacement("Booking System'}) DETACH DELETE (n) //")
    assert r.status in {"ambiguous", "not_found"}
    assert run_query("MATCH (a:Application) RETURN count(a) AS c")[0]["c"] == 49


# --- Properties across every application ---


@pytest.fixture(scope="module")
def all_results():
    names = [r["name"] for r in run_query("MATCH (a:Application) RETURN a.name AS name ORDER BY name")]
    return {name: analyze_replacement(name) for name in names}


def test_every_finding_has_label_path_and_source(all_results):
    assert len(all_results) == 49
    for name, r in all_results.items():
        assert r.status == "ok", name
        for f in r.findings:
            assert f.label in (DOCUMENTED_FACT, INFERENCE), (name, f)
            assert f.paths, (name, f)
            assert f.pages and f.sources, (name, f)


def test_missing_data_is_always_a_gap(all_results):
    for name, r in all_results.items():
        assert "no_dependencies" in gap_kinds(r), name
        if not r.by_kind("platform"):
            assert "no_platform" in gap_kinds(r), name
        if not r.by_kind("realizes"):
            assert "no_capabilities" in gap_kinds(r), name


def test_same_name_gives_same_result(all_results):
    for name in ("Booking System", "Product Maintenance Application", "Baggage Tracking System"):
        assert analyze_replacement(name).model_dump() == all_results[name].model_dump()


def test_mind_map_graph(all_results):
    r = all_results["Booking System"]
    roles = {n.id: n.role for n in r.nodes}
    assert roles["Booking System"] == "application"
    assert roles["Search Flight"] == "uncovered_capability"
    assert roles["Create Reservation"] == "capability"
    assert roles["Direct Sales Platform"] == "covering_application"
    assert roles["Reservation Ticketing"] == "subdomain"
    assert roles["Sales"] == "domain"
    assert roles["Revenue Management & Pricing"] == "connected_domain"
    ids = set(roles)
    for e in r.edges:
        assert e.source in ids and e.target in ids
        assert e.label == (INFERENCE if e.type == "SHARES_DATA" else DOCUMENTED_FACT)
