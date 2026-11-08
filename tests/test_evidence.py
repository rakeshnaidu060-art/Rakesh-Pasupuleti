"""Tests for the evidence layer (scripts/load_evidence.py and backend/evidence.py).

The parsing tests need nothing. The graph tests need Neo4j with the evidence
loaded (python scripts/load_evidence.py) and are skipped otherwise; the search
test also calls the OpenAI API.
"""

import pytest

from backend.evidence import (
    EXCERPT_LENGTH,
    LINK_EVIDENCE_QUERY,
    evidence_for_elements,
    excerpt,
    page_numbers,
    search_evidence,
)
from backend.graph import check_connection, close_driver, get_secret, run_query
from scripts.load_evidence import read_pages

GROUND_OPERATIONS_PAGES = range(28, 37)  # printed pages of section 6.4


# --- Parsing (no database) ---


def test_page_numbers_from_source():
    assert page_numbers("P180 Fig. 37, p. 59") == [59]
    assert page_numbers("P180 Fig. 12, p. 22; P180 Fig. 27, p. 43") == [22, 43]
    assert page_numbers("P180 Fig. 5, p. 15; P180 Fig. 6, p. 15") == [15]
    assert page_numbers(None) == []


def test_excerpt_is_short_and_centred_on_term():
    text = "filler " * 200 + "Baggage Tracking System appears here. " + "more " * 200
    snippet = excerpt(text, ["Baggage Tracking System"])
    assert "Baggage Tracking System" in snippet
    assert len(snippet) <= EXCERPT_LENGTH + 2  # plus the ellipses
    assert snippet.startswith("…") and snippet.endswith("…")


def test_pages_use_printed_numbers():
    pages = {p["page"]: p for p in read_pages()}
    assert min(pages) == 1 and max(pages) == 93
    # Printed page 59 is PDF page 69 and holds the Customer Communication mapping.
    assert pages[59]["pdf_page"] == 69
    assert "Notifying Service" in pages[59]["text"]
    assert "6.7 Marketing & Customer Care" in pages[59]["section"]
    # Page 36 starts with the Ground Operations technology figure, although
    # section 6.5 starts further down the page.
    assert "6.4.5 Technology Architecture" in pages[36]["section"]
    assert "The Open Group Preliminary Standard" not in pages[2]["text"]


# --- Evidence in Neo4j ---


@pytest.fixture(scope="module")
def evidence_graph():
    ok, message = check_connection()
    if not ok:
        pytest.skip(message)
    if not run_query("MATCH (v:Evidence) RETURN count(v) AS c")[0]["c"]:
        pytest.skip("No evidence loaded; run python scripts/load_evidence.py")
    run_query(LINK_EVIDENCE_QUERY)
    yield
    close_driver()


def test_every_sourced_element_is_linked(evidence_graph):
    rows = run_query(
        "MATCH (e:Element) WHERE e.source IS NOT NULL "
        "AND NOT (e)-[:SUPPORTED_BY]->(:Evidence) RETURN e.name AS name"
    )
    assert rows == []


def test_links_match_source_pages(evidence_graph):
    rows = run_query(
        "MATCH (e:Element {name: $name})-[:SUPPORTED_BY]->(v:Evidence) "
        "RETURN e.source AS source, collect(v.page) AS pages",
        name="Notifying Service",
    )
    assert sorted(rows[0]["pages"]) == page_numbers(rows[0]["source"])


def test_relationships_store_pages(evidence_graph):
    rows = run_query(
        "MATCH (:Element)-[r]->(:Element) WHERE r.pages IS NULL RETURN count(r) AS c"
    )
    assert rows[0]["c"] == 0
    rows = run_query(
        "MATCH (:Application {name: $app})-[r:REALIZES]->(:Capability {name: $cap}) "
        "RETURN r.pages AS pages, r.source AS source",
        app="Booking System",
        cap="Create Reservation",
    )
    assert rows[0]["pages"] == page_numbers(rows[0]["source"])


def test_evidence_for_elements_is_short_with_page(evidence_graph):
    results = evidence_for_elements(["Booking System", "Create Reservation"])
    assert results
    for item in results:
        assert isinstance(item["page"], int)
        assert item["section"]
        assert len(item["excerpt"]) <= EXCERPT_LENGTH + 2
    assert any("Booking System" in item["elements"] for item in results)


def test_search_baggage_tracking_finds_ground_operations(evidence_graph):
    if not get_secret("OPENAI_API_KEY"):
        pytest.skip("No OpenAI API key")
    results = search_evidence("baggage tracking", k=5)
    assert len(results) == 5
    assert results[0]["page"] in GROUND_OPERATIONS_PAGES
    assert sum(r["page"] in GROUND_OPERATIONS_PAGES for r in results) >= 2
    assert all(len(r["excerpt"]) <= EXCERPT_LENGTH + 2 for r in results)
