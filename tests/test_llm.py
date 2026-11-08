"""Tests for backend/llm.py.

Most tests replace the OpenAI call with a fake, so they only need Neo4j. Tests
marked "live" call the OpenAI API and are skipped without a key.
"""

import pytest

from backend import llm
from backend.graph import check_connection, close_driver, get_secret, run_query
from backend.impact import DOCUMENTED_FACT, INFERENCE, analyze_replacement
from backend.llm import (
    DraftExplanation,
    DraftStatement,
    ParsedQuestion,
    _allowed_names,
    _llm_input,
    _model_names,
    answer_question,
    build_answer,
    check_draft,
    explain,
    fallback_answer,
    names_outside_result,
    parse_question,
    to_request,
)

live = pytest.mark.skipif(not get_secret("OPENAI_API_KEY"), reason="No OpenAI API key")


@pytest.fixture(scope="module", autouse=True)
def graph():
    ok, message = check_connection()
    if not ok:
        pytest.skip(message)
    yield
    close_driver()


@pytest.fixture(scope="module")
def booking():
    result = analyze_replacement("Booking System")
    data, index = _llm_input(result, [])
    return result, index, _allowed_names(result), _model_names()


def ids_with_label(index, label):
    return [i for i, v in index.items() if v["label"] == label]


def good_draft(index):
    """A draft that cites every finding correctly."""
    facts = ids_with_label(index, DOCUMENTED_FACT)
    inferences = ids_with_label(index, INFERENCE)
    gaps = ids_with_label(index, "Gap")
    return DraftExplanation(
        findings=[
            DraftStatement(text="Booking System realizes Create Reservation and Search Flight.", cites=facts),
            DraftStatement(text="Sales shares data with Revenue Management & Pricing.", cites=inferences),
        ],
        not_documented=[DraftStatement(text="P180 documents no dependencies.", cites=gaps)],
        recommendations=[DraftStatement(text="Consider how Search Flight will be covered.", cites=facts[:1])],
    )


# --- Name check ---


def test_names_outside_result():
    allowed = {"Booking System", "Product Maintenance Application"}
    known = {"Booking System", "Fare Engine", "Maintenance", "Product Maintenance Application"}
    assert names_outside_result("Booking System talks to Fare Engine.", allowed, known) == ["Fare Engine"]
    # A known name inside a longer allowed name is not reported on its own.
    assert names_outside_result("Product Maintenance Application is old.", allowed, known) == []
    assert names_outside_result("Plan the maintenance window.", allowed, known) == []


# --- Draft check and placement ---


def test_good_draft_passes(booking):
    result, index, allowed, known = booking
    assert check_draft(good_draft(index), index, allowed, known) == []


def test_check_rejects_problems(booking):
    result, index, allowed, known = booking
    draft = good_draft(index)
    draft.findings.append(DraftStatement(text="Booking System also feeds Fare Engine.", cites=["F99"]))
    draft.not_documented.append(DraftStatement(text="Odd.", cites=["F1"]))
    draft.findings[0].cites = draft.findings[0].cites[1:]  # leave F1 uncovered
    problems = " ".join(check_draft(draft, index, allowed, known))
    assert "unknown ids ['F99']" in problems
    assert "must cite only G ids" in problems
    assert "not covered" in problems and "F1" in problems
    assert "Fare Engine" in problems


def test_statements_placed_by_label_of_cited_findings(booking):
    result, index, allowed, known = booking
    fact, inference = ids_with_label(index, DOCUMENTED_FACT)[0], ids_with_label(index, INFERENCE)[0]
    draft = DraftExplanation(
        findings=[
            DraftStatement(text="fact only", cites=[fact]),
            DraftStatement(text="inference only", cites=[inference]),
            DraftStatement(text="mixed", cites=[fact, inference]),
        ],
        not_documented=[], recommendations=[],
    )
    answer = build_answer(draft, index, result)
    assert [s.text for s in answer.facts] == ["fact only"]
    assert [s.text for s in answer.inferences] == ["inference only", "mixed"]
    assert answer.facts[0].pages == index[fact]["pages"]


def test_fallback_answer(booking):
    result, index, allowed, known = booking
    answer = fallback_answer(result, ["test"])
    assert answer.generated_by == "fallback"
    assert answer.facts and answer.inferences and answer.recommendations and answer.not_documented
    assert all(s.pages for s in answer.facts + answer.inferences)
    text = " ".join(s.text for s in answer.facts + answer.inferences + answer.recommendations)
    assert names_outside_result(text, allowed, known) == []
    assert "Search Flight" in text


# --- Regenerate once, then fall back ---


def fake_llm(monkeypatch, drafts):
    calls = []

    def fake(model, instructions, user_input, schema):
        calls.append(user_input)
        return drafts[len(calls) - 1]

    monkeypatch.setattr(llm, "_structured", fake)
    return calls


def test_bad_then_good_draft_regenerates_once(monkeypatch, booking):
    result, index, allowed, known = booking
    bad = DraftExplanation(findings=[DraftStatement(text="Fare Engine.", cites=["F1"])], not_documented=[], recommendations=[])
    calls = fake_llm(monkeypatch, [bad, good_draft(index)])
    answer = explain(result, [])
    assert len(calls) == 2
    assert "rejected" in calls[1]
    assert answer.generated_by == "llm"


def test_two_bad_drafts_fall_back(monkeypatch, booking):
    result, index, allowed, known = booking
    bad = DraftExplanation(findings=[DraftStatement(text="Fare Engine.", cites=["F1"])], not_documented=[], recommendations=[])
    calls = fake_llm(monkeypatch, [bad, bad])
    answer = explain(result, [])
    assert len(calls) == 2
    assert answer.generated_by == "fallback"
    assert any("Fare Engine" in p for p in answer.problems)


# --- Request validation ---


def test_unsupported_question():
    request, response, _ = to_request(ParsedQuestion(operation="unsupported", application_name=None), "q")
    assert request is None and response.kind == "unsupported"


def test_no_application_named():
    request, response, _ = to_request(ParsedQuestion(operation="replace_application", application_name=None), "q")
    assert response.kind == "clarification" and response.options == []


def test_ambiguous_application_asks_instead_of_guessing():
    parsed = ParsedQuestion(operation="replace_application", application_name="booking tool")
    request, response, _ = to_request(parsed, "q")
    assert request is None
    assert response.kind == "clarification"
    assert "Booking System" in response.options


def test_unknown_application():
    parsed = ParsedQuestion(operation="replace_application", application_name="Quantum Teleporter")
    request, response, _ = to_request(parsed, "q")
    assert response.kind == "clarification" and response.options == []


def test_exact_application_gives_request():
    parsed = ParsedQuestion(operation="replace_application", application_name="booking system")
    request, response, result = to_request(parsed, "q")
    assert response is None
    assert request.application == "Booking System"
    assert result.status == "ok"


def test_full_flow_with_fake_llm(monkeypatch, booking):
    result, index, allowed, known = booking
    parsed = ParsedQuestion(operation="replace_application", application_name="Booking System")
    fake_llm(monkeypatch, [parsed, good_draft(index)])
    response = answer_question("What if we replace Booking System?")
    assert response.kind == "answer"
    assert response.answer.generated_by == "llm"
    assert response.impact.application == "Booking System"
    assert response.evidence and all("page" in e and "excerpt" in e for e in response.evidence)
    assert response.mind_map["nodes"] and response.mind_map["edges"]


# --- Live: real OpenAI calls ---


@live
def test_live_phrasings_resolve_to_same_application():
    questions = [
        "What would be affected if we replaced the Booking System?",
        "If we retire our booking system, what breaks?",
        "impact of switching off Booking System",
    ]
    applications = set()
    for q in questions:
        request, response, _ = to_request(parse_question(q), q)
        assert response is None, (q, response)
        applications.add(request.application)
    assert applications == {"Booking System"}


@live
def test_live_ambiguous_and_unsupported():
    response = answer_question("What happens if we replace the booking tool?")
    assert response.kind == "clarification" and "Booking System" in response.options
    response = answer_question("Which applications are in the Sales domain?")
    assert response.kind == "unsupported"


@live
@pytest.mark.parametrize("application", ["Booking System", "Notifying Service"])
def test_live_answer_names_only_result_elements(application):
    response = answer_question(f"What would be affected if we replaced {application}?")
    answer = response.answer
    allowed, known = _allowed_names(response.impact), _model_names()
    statements = answer.facts + answer.inferences + answer.recommendations + answer.not_documented
    assert names_outside_result(" ".join(s.text for s in statements), allowed, known) == []
    assert answer.facts and answer.recommendations and answer.not_documented
    assert all(s.pages for s in answer.facts + answer.inferences)
    uncovered = {f.subject for f in response.impact.by_kind("uncovered_capability")}
    fact_text = " ".join(s.text for s in answer.facts)
    assert all(name in fact_text for name in uncovered)
