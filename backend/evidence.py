"""Evidence retrieval from the P180 pages stored in Neo4j.

Each P180 page is an Evidence node with its printed page number, section
heading, text and an OpenAI embedding. Elements link to the pages their Source
cites with SUPPORTED_BY. Text shown to users is always a short excerpt with a
page reference, never a whole page.
"""

import re

from openai import OpenAI

from backend.graph import get_secret, run_query

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
VECTOR_INDEX = "evidence_embedding"
EXCERPT_LENGTH = 300

# Links every element to the Evidence nodes for the pages in its `pages`
# property (parsed from its Source by scripts/load_graph.py). Safe to rerun.
LINK_EVIDENCE_QUERY = """
MATCH (e:Element) WHERE e.pages IS NOT NULL
UNWIND e.pages AS page
MATCH (v:Evidence {page: page})
MERGE (e)-[:SUPPORTED_BY]->(v)
"""

_PAGE_PATTERN = re.compile(r"\bp\.\s*(\d+)")


def page_numbers(source: str | None) -> list[int]:
    """Return the printed P180 page numbers cited in a Source value, in order.

    "P180 Fig. 12, p. 22; P180 Fig. 27, p. 43" gives [22, 43].
    """
    if not source:
        return []
    return sorted({int(p) for p in _PAGE_PATTERN.findall(source)})


def embed(texts: list[str]) -> list[list[float]]:
    """Return one embedding per text, in order."""
    client = OpenAI(api_key=get_secret("OPENAI_API_KEY"))
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]


def excerpt(text: str, terms: list[str] | None = None, length: int = EXCERPT_LENGTH) -> str:
    """Return a short excerpt of ``text``, centred on the first term found in it."""
    flat = " ".join(text.split())
    if len(flat) <= length:
        return flat
    start = 0
    lowered = flat.lower()
    for term in terms or []:
        position = lowered.find(term.lower())
        if position >= 0:
            start = max(0, position - length // 3)
            break
    end = min(len(flat), start + length)
    start = max(0, end - length)
    # Snap to word boundaries.
    if start > 0:
        start = flat.find(" ", start) + 1
    if end < len(flat):
        end = flat.rfind(" ", start, end)
    snippet = flat[start:end]
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(flat) else "")


def evidence_for_elements(names: list[str]) -> list[dict]:
    """Return the P180 pages linked to the given elements, ordered by page.

    Each item has page, section, the linked element names and a short excerpt
    centred on the first of those names that appears on the page.
    """
    rows = run_query(
        """
        MATCH (e:Element)-[:SUPPORTED_BY]->(v:Evidence)
        WHERE e.name IN $names
        RETURN v.page AS page, v.section AS section, v.text AS text,
               collect(DISTINCT e.name) AS elements
        ORDER BY page
        """,
        names=list(names),
    )
    return [
        {
            "page": row["page"],
            "section": row["section"],
            "elements": sorted(row["elements"]),
            "excerpt": excerpt(row["text"], sorted(row["elements"], key=len, reverse=True)),
        }
        for row in rows
    ]


def search_evidence(question: str, k: int = 5) -> list[dict]:
    """Return the k P180 pages most similar in meaning to the question.

    Each item has page, section, score and a short excerpt.
    """
    [vector] = embed([question])
    rows = run_query(
        f"""
        MATCH (node:Evidence)
          SEARCH node IN (VECTOR INDEX {VECTOR_INDEX} FOR $vector LIMIT $k) SCORE AS score
        RETURN node.page AS page, node.section AS section, node.text AS text, score
        ORDER BY score DESC
        """,
        k=k,
        vector=vector,
    )
    terms = [question] + sorted((w for w in re.findall(r"\w+", question) if len(w) > 3), key=len, reverse=True)
    return [
        {
            "page": row["page"],
            "section": row["section"],
            "score": round(row["score"], 4),
            "excerpt": excerpt(row["text"], terms),
        }
        for row in rows
    ]
