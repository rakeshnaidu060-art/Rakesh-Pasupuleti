"""Run every test question through the full flow and compare with the expected answers.

Usage:
    python eval/run_eval.py [--questions eval/questions.csv] [--workers 4]

Each question goes through backend.llm.answer_question exactly as in the app
(real OpenAI and Neo4j calls). Writes:
    eval/results/results.csv   one row per question
    eval/results/summary.md    the measured results, ready for the evaluation report

Expected behaviour values in questions.csv:
    answer                an analysis with findings is shown
    clarify_with_options  the user is asked to choose; expected_application must be among the choices
    clarify_unknown       the user is asked for another name; no choices, no analysis
    unsupported           the question is declined as not about replacing an application
"""

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.graph import close_driver  # noqa: E402
from backend.impact import DOCUMENTED_FACT, INFERENCE  # noqa: E402
from backend.llm import (  # noqa: E402
    ANSWER_MODEL,
    PARSE_MODEL,
    AdvisorResponse,
    _allowed_names,
    _model_names,
    answer_question,
    names_outside_result,
)

DEFAULT_QUESTIONS = ROOT / "eval" / "questions.csv"
RESULTS_DIR = ROOT / "eval" / "results"


def split_list(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def read_questions(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["expected_capabilities"] = split_list(row["expected_capabilities"])
        row["expected_uncovered"] = split_list(row["expected_uncovered"])
    return rows


def actual_behaviour(response: AdvisorResponse) -> str:
    if response.kind == "clarification":
        return "clarify_with_options" if response.options else "clarify_unknown"
    return response.kind


def ratio(hits: int, total: int) -> float | None:
    return hits / total if total else None


def evaluate(question: dict, known: set[str]) -> dict:
    """Run one question and measure the result."""
    started = time.time()
    try:
        response = answer_question(question["question"])
    except Exception as exc:  # recorded as a failed question, not a crash
        return {"id": question["id"], "question": question["question"], "error": f"{type(exc).__name__}: {exc}",
                "behaviour_ok": False, "seconds": round(time.time() - started, 1)}
    seconds = round(time.time() - started, 1)

    behaviour = actual_behaviour(response)
    row = {
        "id": question["id"],
        "question": question["question"],
        "expected_behaviour": question["expected_behaviour"],
        "actual_behaviour": behaviour,
        "behaviour_ok": behaviour == question["expected_behaviour"],
        "seconds": seconds,
        "error": "",
    }

    if behaviour == "clarify_with_options":
        row["options"] = "; ".join(response.options)
        row["behaviour_ok"] &= question["expected_application"] in response.options
    if behaviour != "answer":
        row["no_analysis_run"] = response.impact is None
        row["behaviour_ok"] &= response.impact is None
        return row

    result, answer = response.impact, response.answer
    expected_caps, expected_unc = set(question["expected_capabilities"]), set(question["expected_uncovered"])
    caps = {f.subject for f in result.by_kind("realizes")}
    unc = {f.subject for f in result.by_kind("uncovered_capability")}

    labels = {f"F{i}": f.label for i, f in enumerate(result.findings, start=1)}
    labels.update({f"G{i}": "Gap" for i in range(1, len(result.gaps) + 1)})

    def labelled(statement, section: str) -> bool:
        cited = {labels.get(c) for c in statement.cites}
        if not statement.cites or None in cited:
            return False
        if section == "facts":
            return cited == {DOCUMENTED_FACT}
        if section == "inferences":
            return INFERENCE in cited
        if section == "not_documented":
            return cited == {"Gap"}
        return True  # recommendations are labelled by their section

    sections = {"facts": answer.facts, "inferences": answer.inferences,
                "not_documented": answer.not_documented, "recommendations": answer.recommendations}
    statements = [(name, s) for name, group in sections.items() for s in group]
    # Recommendations may legitimately cite nothing; count them as labelled by section.
    labelled_count = sum(1 for name, s in statements if name == "recommendations" or labelled(s, name))
    answer_text = " ".join(s.text for _, s in statements)
    fact_text = " ".join(s.text for s in answer.facts)

    row.update({
        "application": result.application,
        "application_ok": result.application == question["expected_application"],
        "capabilities_expected": len(expected_caps),
        "capabilities_returned": len(caps),
        "capability_recall": ratio(len(caps & expected_caps), len(expected_caps)),
        "capability_precision": ratio(len(caps & expected_caps), len(caps)),
        "uncovered_recall": ratio(len(unc & expected_unc), len(expected_unc)),
        "uncovered_precision": ratio(len(unc & expected_unc), len(unc)),
        "answer_names_all_uncovered": all(name in fact_text for name in expected_unc),
        "statements": len(statements),
        "statements_labelled": labelled_count,
        "facts": len(answer.facts),
        "facts_with_pages": sum(1 for s in answer.facts if s.pages),
        "names_outside_result": "; ".join(names_outside_result(answer_text, _allowed_names(result), known)),
        "gaps": "; ".join(g.kind for g in result.gaps),
        "generated_by": answer.generated_by,
        "findings_signature": sorted((f.kind, f.subject, f.label) for f in result.findings),
    })
    if question["expected_capabilities"] == [] and not caps:
        row["capability_recall"] = row["capability_precision"] = None
    return row


def mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def count(rows, predicate) -> tuple[int, int]:
    return sum(1 for r in rows if predicate(r)), len(rows)


def summarise(questions: list[dict], rows: list[dict]) -> str:
    by_id = {q["id"]: q for q in questions}
    answers = [r for r in rows if r.get("actual_behaviour") == "answer"]
    expected_answers = [r for r in rows if by_id[r["id"]]["expected_behaviour"] == "answer"]

    def of(behaviour):
        return [r for r in rows if by_id[r["id"]]["expected_behaviour"] == behaviour]

    # Questions about the same application must give the same findings.
    groups: dict[str, list[dict]] = {}
    for r in answers:
        groups.setdefault(r["application"], []).append(r)
    repeated = {app: rs for app, rs in groups.items() if len(rs) > 1}
    consistent = sum(1 for rs in repeated.values() if all(r["findings_signature"] == rs[0]["findings_signature"] for r in rs))

    statements = sum(r["statements"] for r in answers)
    labelled = sum(r["statements_labelled"] for r in answers)
    facts = sum(r["facts"] for r in answers)
    facts_paged = sum(r["facts_with_pages"] for r in answers)

    metrics = [
        ("Questions with the expected behaviour", "%d / %d" % count(rows, lambda r: r["behaviour_ok"])),
        ("Application identified correctly", "%d / %d" % count(expected_answers, lambda r: r.get("application_ok"))),
        ("Expected capabilities found (recall)", pct(mean(r.get("capability_recall") for r in answers))),
        ("Returned capabilities that are correct (precision)", pct(mean(r.get("capability_precision") for r in answers))),
        ("Expected uncovered capabilities found (recall)", pct(mean(r.get("uncovered_recall") for r in answers))),
        ("Returned uncovered capabilities that are correct (precision)", pct(mean(r.get("uncovered_precision") for r in answers))),
        ("Answers naming every uncovered capability", "%d / %d" % count(answers, lambda r: r["answer_names_all_uncovered"])),
        ("Statements carrying a correct label", f"{labelled} / {statements} ({pct(ratio(labelled, statements))})"),
        ("Documented facts with a page reference", f"{facts_paged} / {facts} ({pct(ratio(facts_paged, facts))})"),
        ("Answers naming no element outside the analysis", "%d / %d" % count(answers, lambda r: not r["names_outside_result"])),
        ("Answers written by the LLM (not the fallback summary)", "%d / %d" % count(answers, lambda r: r["generated_by"] == "llm")),
        ("Same findings for differently phrased questions", f"{consistent} / {len(repeated)} applications"),
        ("Ambiguous or partial names: choices offered, no guess", "%d / %d" % count(of("clarify_with_options"), lambda r: r["behaviour_ok"])),
        ("Unknown names: asked again, no analysis", "%d / %d" % count(of("clarify_unknown"), lambda r: r["behaviour_ok"])),
        ("Unsupported questions declined", "%d / %d" % count(of("unsupported"), lambda r: r["behaviour_ok"])),
        ("Errors", str(sum(1 for r in rows if r.get("error")))),
        ("Response time, mean / max (seconds)",
         f"{mean(r['seconds'] for r in rows):.1f} / {max(r['seconds'] for r in rows):.1f}"),
    ]

    lines = [
        "# Evaluation results",
        "",
        f"Run on {datetime.now():%Y-%m-%d %H:%M} with {len(rows)} questions "
        f"(parsing model `{PARSE_MODEL}`, answer model `{ANSWER_MODEL}`).",
        "",
        "## Summary",
        "",
        "| Measure | Result |",
        "| --- | --- |",
        *[f"| {name} | {value} |" for name, value in metrics],
        "",
        "## Per question",
        "",
        "| Id | Question | Expected | Actual | OK | Capability recall | Uncovered recall | Labelled | Facts with pages | Time (s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        if r.get("actual_behaviour") == "answer":
            detail = (f"{pct(r['capability_recall'])} | {pct(r['uncovered_recall'])} | "
                      f"{r['statements_labelled']}/{r['statements']} | {r['facts_with_pages']}/{r['facts']}")
        else:
            detail = "– | – | – | –"
        actual = r.get("actual_behaviour") or f"error: {r['error'][:40]}"
        lines.append(
            f"| {r['id']} | {r['question']} | {by_id[r['id']]['expected_behaviour']} | {actual} | "
            f"{'yes' if r['behaviour_ok'] else '**no**'} | {detail} | {r['seconds']} |"
        )
    failures = [r for r in rows if not r["behaviour_ok"] or r.get("names_outside_result") or r.get("error")]
    if failures:
        lines += ["", "## Problems", ""]
        for r in failures:
            problem = r.get("error") or (
                f"names outside the analysis: {r['names_outside_result']}" if r.get("names_outside_result")
                else f"expected {by_id[r['id']]['expected_behaviour']}, got {r.get('actual_behaviour')}"
                + (f" (options: {r['options']})" if r.get("options") else "")
            )
            lines.append(f"- **{r['id']}**: {problem}")
    return "\n".join(lines) + "\n"


def write_results(rows: list[dict]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["id", "question", "expected_behaviour", "actual_behaviour", "behaviour_ok", "options", "application",
              "application_ok", "capabilities_expected", "capabilities_returned", "capability_recall",
              "capability_precision", "uncovered_recall", "uncovered_precision", "answer_names_all_uncovered",
              "statements", "statements_labelled", "facts", "facts_with_pages", "names_outside_result", "gaps",
              "generated_by", "no_analysis_run", "seconds", "error"]
    with open(RESULTS_DIR / "results.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: (f"{v:.2f}" if isinstance(v, float) else v) for k, v in r.items()})


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the AI Architecture Advisor.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--workers", type=int, default=4, help="Questions run in parallel.")
    args = parser.parse_args()

    questions = read_questions(args.questions)
    print(f"Running {len(questions)} questions with {args.workers} workers...")
    try:
        known = _model_names()
        with ThreadPoolExecutor(args.workers) as pool:
            rows = list(pool.map(lambda q: evaluate(q, known), questions))
    finally:
        close_driver()

    for r in rows:
        print(f"  {r['id']} {'ok ' if r['behaviour_ok'] else 'FAIL'} {r.get('actual_behaviour', 'error'):<21} {r['seconds']:>5}s  {r['question']}")
    write_results(rows)
    summary = summarise(questions, rows)
    (RESULTS_DIR / "summary.md").write_text(summary, encoding="utf-8")
    print(f"\nWrote {RESULTS_DIR / 'results.csv'} and {RESULTS_DIR / 'summary.md'}")
    return 0 if all(r["behaviour_ok"] and not r.get("error") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
