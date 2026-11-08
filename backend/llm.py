"""AI layer: turn a question into an engine request, and an engine result into an explanation.

The LLM does two things only:
1. Parse a plain-language question into a validated request (operation and
   application name, copied as the user wrote it).
2. Write an explanation from the engine result and evidence passages it is given.

It never connects to Neo4j. Labels are not chosen by the LLM: every sentence it
writes must cite the ids of the findings it rests on, and the code places the
sentence under "Documented facts" or "Inferences" from those findings' labels
and attaches their page numbers. After generation, every application,
capability, domain, subdomain and platform named in the answer must appear in
the engine result; otherwise the answer is regenerated once, then replaced by a
plain structured summary built by the code.
"""

import json
import re
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from backend.evidence import evidence_for_elements
from backend.graph import get_secret, run_query
from backend.impact import DOCUMENTED_FACT, ImpactResult, analyze_replacement

PARSE_MODEL = get_secret("OPENAI_PARSE_MODEL") or "gpt-5.4-mini"
ANSWER_MODEL = get_secret("OPENAI_ANSWER_MODEL") or "gpt-5.4"

# Element types whose names the post-generation check looks for in answers.
CHECKED_TYPES = ["Application", "Capability", "Domain", "Subdomain", "Platform"]

# Findings about the application itself; their elements' P180 pages are the evidence shown.
DIRECT_KINDS = {"realizes", "subdomain", "domain", "platform", "component"}


# --- Models ---


class ParsedQuestion(BaseModel):
    """What the LLM extracts from a question."""

    operation: Literal["replace_application", "unsupported"] = Field(
        description="replace_application if the question asks what is affected by replacing, "
        "retiring, removing, switching off or migrating away from an application; otherwise unsupported."
    )
    application_name: str | None = Field(
        description="The words the user used to name the application, copied exactly, "
        "or null if no application is named."
    )


class Request(BaseModel):
    """A validated request for the engine."""

    operation: Literal["replace_application"]
    application: str


class DraftStatement(BaseModel):
    text: str
    cites: list[str] = Field(description="Ids of the findings (F…) or gaps (G…) this sentence rests on.")


class DraftExplanation(BaseModel):
    """What the LLM writes. The code decides where each statement goes."""

    findings: list[DraftStatement] = Field(description="Statements explaining the findings, each citing finding ids.")
    not_documented: list[DraftStatement] = Field(description="Plain statements of what P180 does not document, citing gap ids.")
    recommendations: list[DraftStatement] = Field(
        description="2 to 4 suggestions for an architect to validate, citing the finding or gap ids they follow from."
    )


class Statement(BaseModel):
    text: str
    pages: list[int] = Field(default_factory=list)
    cites: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    headline: str
    facts: list[Statement]
    inferences: list[Statement]
    recommendations: list[Statement]
    not_documented: list[Statement]
    generated_by: Literal["llm", "fallback"]
    problems: list[str] = Field(default_factory=list, description="Why the LLM answer was rejected, if it was.")


class AdvisorResponse(BaseModel):
    kind: Literal["answer", "clarification", "unsupported"]
    question: str
    message: str = ""
    options: list[str] = Field(default_factory=list, description="Application names to choose from.")
    request: Request | None = None
    answer: Answer | None = None
    impact: ImpactResult | None = None
    evidence: list[dict] = Field(default_factory=list)
    mind_map: dict = Field(default_factory=dict)


# --- OpenAI calls ---


def _structured(model: str, instructions: str, user_input: str, schema: type[BaseModel]) -> BaseModel:
    """Call the model with structured output and return the parsed Pydantic object."""
    client = OpenAI(api_key=get_secret("OPENAI_API_KEY"))
    options = {"reasoning": {"effort": "low"}} if model.startswith(("gpt-5", "o")) else {"temperature": 0}
    response = client.responses.parse(
        model=model, instructions=instructions, input=user_input, text_format=schema, **options
    )
    if response.output_parsed is None:
        raise RuntimeError("The model returned no structured output.")
    return response.output_parsed


# --- Step 1: parsing ---

PARSE_INSTRUCTIONS = """\
You read questions from enterprise architects about an airline's application landscape.
Extract two things:
- operation: "replace_application" if the question asks what would be affected by replacing,
  retiring, removing, decommissioning, switching off or migrating away from one application,
  or what depends on it in that sense. Anything else is "unsupported".
- application_name: the words the user used to name that application, copied exactly as written,
  without a leading article or possessive ("the", "our"). Do not correct, complete, expand or
  translate the name, and do not substitute a name you know.
  Use null if no specific application is named (for example "an application" or "our systems").
"""


def parse_question(question: str) -> ParsedQuestion:
    return _structured(PARSE_MODEL, PARSE_INSTRUCTIONS, question, ParsedQuestion)


# --- Step 2: explanation ---

EXPLAIN_INSTRUCTIONS = """\
You explain the result of an impact analysis to an enterprise architect at Lufthansa.
The analysis was run on a graph built from The Open Group Commercial Aviation Reference
Architecture (P180). You receive the findings (each with an id, a label and the P180 pages
behind it), the gaps (what P180 does not document) and short P180 evidence passages.

Rules:
- Use only the data you receive. Do not add applications, capabilities, domains, platforms,
  vendors, numbers or facts that are not in it, and do not use outside knowledge.
- Refer to elements by the exact names used in the data.
- findings: write short, clear statements that explain the findings. Group related findings
  into one statement where it reads better (for example, list all uncovered capabilities in one
  statement). Every statement must cite the ids of all findings it is based on. Keep findings
  labelled "Documented fact" and findings labelled "Inference" in separate statements. Cover
  every finding, and say which capabilities would be left uncovered, since that is the
  strongest risk.
- Phrase inferences as possibilities ("may", "could"), never as certainties.
- not_documented: state each gap plainly, citing its gap id. Never present missing data as
  "no impact".
- recommendations: 2 to 4 concrete suggestions for the architect to validate, phrased as
  suggestions ("Consider…", "Check with…", "Verify…"). Cite the finding or gap ids each one
  follows from.
- Do not write page numbers in the text; they are added from the ids you cite.
- Write plain English for a reader who knows the business but not graph databases.
"""


def _llm_input(result: ImpactResult, evidence: list[dict]) -> tuple[str, dict[str, dict]]:
    """Build the data the LLM receives, and the id → finding/gap index used to check its output."""
    index: dict[str, dict] = {}
    findings = []
    for i, f in enumerate(result.findings, start=1):
        fid = f"F{i}"
        index[fid] = {"label": f.label, "pages": f.pages}
        findings.append({"id": fid, "label": f.label, "kind": f.kind, "statement": f.statement, "pages": f.pages})
    gaps = []
    for i, g in enumerate(result.gaps, start=1):
        gid = f"G{i}"
        index[gid] = {"label": "Gap", "pages": []}
        gaps.append({"id": gid, "statement": g.statement})
    data = {
        "question": f"What would be affected if {result.application} were replaced?",
        "application": result.application,
        "findings": findings,
        "gaps": gaps,
        "evidence": [{"page": e["page"], "section": e["section"], "excerpt": e["excerpt"]} for e in evidence],
    }
    return json.dumps(data, ensure_ascii=False, indent=1), index


def _allowed_names(result: ImpactResult) -> set[str]:
    names = {result.application} if result.application else set()
    for f in result.findings:
        names.add(f.subject)
        names.update(f.related)
    names.update(n.id for n in result.nodes)
    return names


def _model_names() -> set[str]:
    rows = run_query("MATCH (e:Element) WHERE e.type IN $types RETURN e.name AS name", types=CHECKED_TYPES)
    return {row["name"] for row in rows}


def names_outside_result(text: str, allowed: set[str], known: set[str]) -> list[str]:
    """Return model element names that appear in ``text`` but not in the engine result."""
    masked = text
    # Remove allowed names first, longest first, so "Maintenance" inside
    # "Product Maintenance Application" is not counted on its own.
    for name in sorted(allowed, key=len, reverse=True):
        masked = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", " ", masked, flags=re.IGNORECASE)
    found = [
        name for name in known - allowed
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", masked)
    ]
    return sorted(found)


def check_draft(draft: DraftExplanation, index: dict[str, dict], allowed: set[str], known: set[str]) -> list[str]:
    """Return every problem with a draft; an empty list means it can be used."""
    problems = []
    groups = (("findings", draft.findings, "F"), ("not_documented", draft.not_documented, "G"),
              ("recommendations", draft.recommendations, ""))
    for section, statements, prefix in groups:
        for s in statements:
            unknown = [c for c in s.cites if c not in index]
            if unknown:
                problems.append(f"{section}: '{s.text[:60]}' cites unknown ids {unknown}.")
            if section != "recommendations" and not s.cites:
                problems.append(f"{section}: '{s.text[:60]}' cites no ids.")
            if prefix and any(not c.startswith(prefix) for c in s.cites if c in index):
                problems.append(f"{section}: '{s.text[:60]}' must cite only {prefix} ids.")
    cited = {c for s in draft.findings for c in s.cites}
    missing = [fid for fid in index if fid.startswith("F") and fid not in cited]
    if missing:
        problems.append(f"Findings not covered by any statement: {missing}.")
    if not draft.recommendations:
        problems.append("No recommendations were given.")
    text = " ".join(s.text for group in (draft.findings, draft.not_documented, draft.recommendations) for s in group)
    outside = names_outside_result(text, allowed, known)
    if outside:
        problems.append(f"Names not in the analysis result: {outside}.")
    return problems


def _pages_for(cites: list[str], index: dict[str, dict]) -> list[int]:
    return sorted({p for c in cites if c in index for p in index[c]["pages"]})


def headline(result: ImpactResult) -> str:
    capabilities = len(result.by_kind("realizes"))
    uncovered = len(result.by_kind("uncovered_capability"))
    if not capabilities:
        return f"P180 maps no capabilities to {result.application}."
    return (
        f"Replacing {result.application} affects {capabilities} capabilit{'y' if capabilities == 1 else 'ies'}; "
        f"{uncovered} would be left uncovered."
    )


def build_answer(draft: DraftExplanation, index: dict[str, dict], result: ImpactResult) -> Answer:
    """Place each drafted statement by the labels of what it cites, and attach pages."""
    facts, inferences = [], []
    for s in draft.findings:
        statement = Statement(text=s.text, cites=s.cites, pages=_pages_for(s.cites, index))
        labels = {index[c]["label"] for c in s.cites if c in index}
        # A statement is a documented fact only if everything it cites is one.
        (facts if labels == {DOCUMENTED_FACT} else inferences).append(statement)
    return Answer(
        headline=headline(result),
        facts=facts,
        inferences=inferences,
        recommendations=[Statement(text=s.text, cites=s.cites, pages=_pages_for(s.cites, index))
                         for s in draft.recommendations],
        not_documented=[Statement(text=s.text, cites=s.cites) for s in draft.not_documented],
        generated_by="llm",
    )


def fallback_answer(result: ImpactResult, problems: list[str]) -> Answer:
    """A plain structured summary built from the engine result, without the LLM."""
    facts, inferences = [], []
    for i, f in enumerate(result.findings, start=1):
        if f.kind == "realizes":
            continue  # covered by the uncovered / still covered statements below
        statement = Statement(text=f.statement, pages=f.pages, cites=[f"F{i}"])
        (facts if f.label == DOCUMENTED_FACT else inferences).append(statement)
    realized = [(i, f) for i, f in enumerate(result.findings, start=1) if f.kind == "realizes"]
    if realized:
        facts.insert(0, Statement(
            text=f"{result.application} realizes these capabilities: {', '.join(f.subject for _, f in realized)}.",
            pages=sorted({p for _, f in realized for p in f.pages}),
            cites=[f"F{i}" for i, _ in realized],
        ))
    not_documented = [Statement(text=g.statement, cites=[f"G{i}"]) for i, g in enumerate(result.gaps, start=1)]
    recommendations = []
    uncovered = [f.subject for f in result.by_kind("uncovered_capability")]
    if uncovered:
        recommendations.append(Statement(text=(
            f"Consider how the replacement for {result.application} will cover {', '.join(uncovered)}, "
            "since no other application in P180 does."
        )))
    if result.gaps:
        recommendations.append(Statement(text=(
            f"Check with the owners of {result.application} which interfaces and data flows it has, "
            "since P180 does not document them."
        )))
    return Answer(
        headline=headline(result),
        facts=facts,
        inferences=inferences,
        recommendations=recommendations,
        not_documented=not_documented,
        generated_by="fallback",
        problems=problems,
    )


def explain(result: ImpactResult, evidence: list[dict]) -> Answer:
    """Write the labelled answer; regenerate once if it fails the check, then fall back."""
    data, index = _llm_input(result, evidence)
    allowed, known = _allowed_names(result), _model_names()
    feedback = ""
    problems: list[str] = []
    for _attempt in range(2):
        try:
            draft = _structured(ANSWER_MODEL, EXPLAIN_INSTRUCTIONS, data + feedback, DraftExplanation)
        except Exception as exc:  # malformed output counts as a failed attempt
            problems = [f"Generation failed: {exc}"]
            continue
        problems = check_draft(draft, index, allowed, known)
        if not problems:
            return build_answer(draft, index, result)
        feedback = (
            "\n\nYour previous answer was rejected for these reasons. Write it again and fix them:\n- "
            + "\n- ".join(problems)
        )
    return fallback_answer(result, problems)


# --- Full flow ---


def to_request(parsed: ParsedQuestion, question: str) -> tuple[Request | None, AdvisorResponse | None, ImpactResult | None]:
    """Validate a parsed question against the model. Returns a request, or a response to show instead."""
    if parsed.operation != "replace_application":
        return None, AdvisorResponse(
            kind="unsupported", question=question,
            message=(
                "I can only answer questions about replacing an application, for example "
                "\"What would be affected if we replaced the Booking System?\""
            ),
        ), None
    if not parsed.application_name or not parsed.application_name.strip():
        return None, AdvisorResponse(
            kind="clarification", question=question,
            message="Which application would you like to replace?",
        ), None
    result = analyze_replacement(parsed.application_name)
    if result.status != "ok":
        return None, AdvisorResponse(
            kind="clarification", question=question, message=result.message, options=result.candidates,
        ), None
    return Request(operation="replace_application", application=result.application), None, result


def answer_question(question: str) -> AdvisorResponse:
    """Run the full flow from question to answer, evidence and mind map data."""
    parsed = parse_question(question)
    request, early, result = to_request(parsed, question)
    if early is not None:
        return early
    return answer_for_application(request.application, question, result)


def answer_for_application(application: str, question: str | None = None, result: ImpactResult | None = None) -> AdvisorResponse:
    """Answer for an application chosen directly, for example from a clarification."""
    result = result or analyze_replacement(application)
    if result.status != "ok":
        return AdvisorResponse(kind="clarification", question=question or application,
                               message=result.message, options=result.candidates)
    direct = [result.application] + [f.subject for f in result.findings if f.kind in DIRECT_KINDS]
    evidence = evidence_for_elements(direct)
    return AdvisorResponse(
        kind="answer",
        question=question or f"What would be affected if we replaced {result.application}?",
        request=Request(operation="replace_application", application=result.application),
        answer=explain(result, evidence),
        impact=result,
        evidence=evidence,
        mind_map={"nodes": [n.model_dump() for n in result.nodes], "edges": [e.model_dump() for e in result.edges]},
    )
