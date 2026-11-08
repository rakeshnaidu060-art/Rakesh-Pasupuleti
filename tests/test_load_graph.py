"""Tests for scripts/load_graph.py.

The validation tests need no database. The graph tests load the Excel into the
Neo4j instance from .streamlit/secrets.toml and are skipped if it cannot be
reached. Loading is idempotent, so running them leaves the same graph behind.
"""

import copy

import pytest

from backend.graph import check_connection, close_driver, run_query
from scripts.load_graph import compare_counts, graph_counts, load, read_excel, validate

EXPECTED_ELEMENTS = {
    "Domain": 10,
    "Subdomain": 42,
    "Application": 49,
    "Capability": 134,
    "Data Object": 47,
    "Platform": 19,
    "Technology Component": 41,
}
EXPECTED_RELATIONSHIPS = {
    "Belongs To": 158,
    "Realizes": 135,
    "Associated With": 54,
    "Used In": 126,
    "Depends On": 0,
}


@pytest.fixture(scope="module")
def model():
    return read_excel()


# --- Excel reading and validation (no database) ---


def test_excel_counts(model):
    elements, relationships = model
    assert len(elements) == 342
    assert len(relationships) == 473
    for kind, count in EXPECTED_ELEMENTS.items():
        assert sum(e["type"] == kind for e in elements) == count, kind
    for kind, count in EXPECTED_RELATIONSHIPS.items():
        assert sum(r["type"] == kind for r in relationships) == count, kind


def test_excel_is_valid(model):
    assert validate(*model) == []


def test_validate_reports_unknown_endpoint(model):
    elements, relationships = copy.deepcopy(model)
    relationships[0]["to"] = "No Such Element"
    errors = validate(elements, relationships)
    assert any("'No Such Element' is not a name in Elements" in e for e in errors)


def test_validate_reports_duplicate_name(model):
    elements, relationships = copy.deepcopy(model)
    elements.append({**elements[0], "row": 999})
    errors = validate(elements, relationships)
    assert any(f"'{elements[0]['name']}' appears 2 times" in e for e in errors)


def test_validate_reports_bad_type_and_status(model):
    elements, relationships = copy.deepcopy(model)
    elements[0]["type"] = "Server"
    relationships[0]["type"] = "Calls"
    relationships[0]["status"] = "Maybe"
    errors = validate(elements, relationships)
    assert any("Type 'Server'" in e for e in errors)
    assert any("Type 'Calls'" in e for e in errors)
    assert any("Status 'Maybe'" in e for e in errors)


# --- Loading into Neo4j ---


@pytest.fixture(scope="module")
def loaded_graph(model):
    ok, message = check_connection()
    if not ok:
        pytest.skip(message)
    load(*model)
    yield model
    close_driver()


def test_graph_matches_excel(loaded_graph):
    nodes, rels = graph_counts()
    assert nodes == EXPECTED_ELEMENTS
    assert rels == EXPECTED_RELATIONSHIPS
    all_match, _ = compare_counts(*loaded_graph)
    assert all_match


def test_reload_gives_same_result(loaded_graph):
    before = graph_counts()
    load(*loaded_graph)
    assert graph_counts() == before


def test_names_are_unique_and_constrained(loaded_graph):
    rows = run_query("MATCH (n:Element) RETURN count(n) AS n, count(DISTINCT n.name) AS names")
    assert rows[0]["n"] == rows[0]["names"] == 342
    constraints = run_query(
        "SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties, type "
        "WHERE name = 'element_name_unique' RETURN labelsOrTypes, properties, type"
    )
    assert constraints == [
        {"labelsOrTypes": ["Element"], "properties": ["name"], "type": "NODE_PROPERTY_UNIQUENESS"}
    ]


def test_booking_system_realizes_create_reservation(loaded_graph):
    rows = run_query(
        "MATCH (a:Application {name: $app})-[r:REALIZES]->(c:Capability {name: $cap}) "
        "RETURN r.status AS status, r.source AS source",
        app="Booking System",
        cap="Create Reservation",
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "Documented"
    assert rows[0]["source"].startswith("P180")


def test_product_maintenance_application_belongs_to_two_domains(loaded_graph):
    # Product Management & Design is a subdomain of Product; Maintenance is a
    # domain whose mapping figure has no subdomains.
    rows = run_query(
        "MATCH (:Application {name: $app})-[:BELONGS_TO]->(p:Element) "
        "OPTIONAL MATCH (p)-[:BELONGS_TO]->(d:Domain) "
        "RETURN p.name AS parent, coalesce(d.name, p.name) AS domain",
        app="Product Maintenance Application",
    )
    assert {r["parent"] for r in rows} == {"Product Management & Design", "Maintenance"}
    assert {r["domain"] for r in rows} == {"Product", "Maintenance"}


def test_create_reservation_has_two_applications(loaded_graph):
    rows = run_query(
        "MATCH (a:Application)-[:REALIZES]->(:Capability {name: $cap}) RETURN a.name AS app",
        cap="Create Reservation",
    )
    assert {r["app"] for r in rows} == {"Booking System", "Direct Sales Platform"}


def test_relationship_keeps_status_and_notes(loaded_graph):
    rows = run_query(
        "MATCH (:Element {name: $a})-[r:BELONGS_TO]->(:Element {name: $b}) "
        "RETURN r.status AS status, r.notes AS notes",
        a="Baggage Tracking System",
        b="Tracking Platform",
    )
    assert rows[0]["status"] == "Inferred"
    assert "Fig. 22" in rows[0]["notes"]


def test_element_properties(loaded_graph):
    rows = run_query(
        "MATCH (n:Element:Domain {name: $name}) "
        "RETURN n.type AS type, n.source AS source, n.pages AS pages",
        name="Product",
    )
    assert rows == [{"type": "Domain", "source": "P180 Fig. 1, p. 8", "pages": [8]}]


def test_reload_keeps_evidence_links(loaded_graph):
    if not run_query("MATCH (v:Evidence) RETURN count(v) AS c")[0]["c"]:
        pytest.skip("No evidence loaded")
    rows = run_query(
        "MATCH (e:Element) WHERE e.pages IS NOT NULL "
        "AND NOT (e)-[:SUPPORTED_BY]->(:Evidence) RETURN count(e) AS c"
    )
    assert rows[0]["c"] == 0
