"""Load the P180 pages into Neo4j as Evidence nodes with embeddings.

Usage:
    python scripts/load_evidence.py [--pdf PATH]

Each page from printed page 1 onwards becomes one Evidence node with its
printed page number, section heading, text and an OpenAI embedding; the front
matter (cover, contents, list of figures) is skipped. A vector index is created
on the embeddings, and every element is linked with SUPPORTED_BY to the pages
its Source cites. Safe to rerun: pages whose text has not changed are not
embedded again.
"""

import argparse
import hashlib
import sys
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.evidence import (  # noqa: E402
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    LINK_EVIDENCE_QUERY,
    VECTOR_INDEX,
    embed,
)
from backend.graph import close_driver, run_query  # noqa: E402

DEFAULT_PDF = ROOT / "data" / "P180.pdf"

# The Excel cites printed page numbers, which are 10 lower than the PDF page
# number: printed page 59 is PDF page 69. PDF pages 1-10 are front matter.
PAGE_OFFSET = 10

# Running headers repeated on every page; they carry no evidence.
RUNNING_HEADERS = {
    "The Open Group Commercial Aviation Reference Architecture",
    "The Open Group Preliminary Standard (2018)",
}

EMBED_BATCH_SIZE = 50


def _clean_page_text(raw: str, printed: int) -> str:
    lines = [line.strip() for line in raw.splitlines()]
    lines = [line for line in lines if line and line not in RUNNING_HEADERS]
    # The printed page number sits in the header, among the first lines.
    for i, line in enumerate(lines[:3]):
        if line == str(printed):
            del lines[i]
            break
    return "\n".join(lines)


def _headings_by_page(doc) -> dict[int, list[tuple[str, str]]]:
    """Map each PDF page number to the (title, heading path) of sections starting on it."""
    headings: dict[int, list[tuple[str, str]]] = {}
    stack: list[str] = []
    for level, title, pdf_page in doc.get_toc():
        title = " ".join(title.split())
        stack = stack[: level - 1] + [title]
        headings.setdefault(pdf_page, []).append((title, " > ".join(stack)))
    return headings


def _flat(text: str) -> str:
    return " ".join(text.split())


def read_pages(path: Path = DEFAULT_PDF) -> list[dict]:
    """Return one dict per page from printed page 1: page, pdf_page, section, text, text_hash.

    The section is the heading in force at the top of the page: a section that
    starts lower down the page only applies from the next page on.
    """
    with fitz.open(path) as doc:
        headings = _headings_by_page(doc)
        pages = []
        current = ""
        for index in range(doc.page_count):
            pdf_page = index + 1
            printed = pdf_page - PAGE_OFFSET
            text = _clean_page_text(doc[index].get_text(), printed)
            starting = headings.get(pdf_page, [])
            section = current
            if starting and _flat(text).startswith(starting[0][0]):
                section = starting[0][1]
            if starting:
                current = starting[-1][1]
            if pdf_page <= PAGE_OFFSET:
                continue
            text_hash = hashlib.sha256(f"{EMBEDDING_MODEL}\n{text}".encode()).hexdigest()
            pages.append(
                {
                    "page": printed,
                    "pdf_page": pdf_page,
                    "section": section or current,
                    "text": text,
                    "text_hash": text_hash,
                }
            )
    return pages


def create_schema() -> None:
    run_query(
        "CREATE CONSTRAINT evidence_page_unique IF NOT EXISTS "
        "FOR (v:Evidence) REQUIRE v.page IS UNIQUE"
    )
    run_query(
        f"CREATE VECTOR INDEX {VECTOR_INDEX} IF NOT EXISTS "
        "FOR (v:Evidence) ON v.embedding "
        "OPTIONS {indexConfig: {`vector.dimensions`: $dimensions, "
        "`vector.similarity_function`: 'cosine'}}",
        dimensions=EMBEDDING_DIMENSIONS,
    )


def store_pages(pages: list[dict]) -> tuple[int, int]:
    """Write pages and embeddings, skipping unchanged ones. Returns (embedded, skipped)."""
    stored = {
        row["page"]: row["text_hash"]
        for row in run_query(
            "MATCH (v:Evidence) WHERE v.embedding IS NOT NULL "
            "RETURN v.page AS page, v.text_hash AS text_hash"
        )
    }
    changed = [p for p in pages if stored.get(p["page"]) != p["text_hash"]]

    for start in range(0, len(changed), EMBED_BATCH_SIZE):
        batch = changed[start : start + EMBED_BATCH_SIZE]
        vectors = embed([p["text"] or p["section"] for p in batch])
        run_query(
            """
            UNWIND $rows AS row
            MERGE (v:Evidence {page: row.page})
            SET v.pdf_page = row.pdf_page, v.section = row.section,
                v.text = row.text, v.text_hash = row.text_hash
            WITH v, row
            CALL db.create.setNodeVectorProperty(v, 'embedding', row.embedding)
            """,
            rows=[{**p, "embedding": vector} for p, vector in zip(batch, vectors)],
        )

    # Drop pages that no longer exist in the PDF.
    run_query(
        "MATCH (v:Evidence) WHERE NOT v.page IN $pages DETACH DELETE v",
        pages=[p["page"] for p in pages],
    )
    return len(changed), len(pages) - len(changed)


def report() -> tuple[bool, str]:
    """Check the evidence layer. Returns (all_ok, report_text)."""
    [counts] = run_query(
        """
        CALL () { MATCH (v:Evidence) RETURN count(v) AS pages,
                  count(v.embedding) AS embedded }
        CALL () { MATCH (:Element)-[s:SUPPORTED_BY]->(:Evidence) RETURN count(s) AS links }
        CALL () { MATCH (:Element)-[r]->(:Element) WHERE r.pages IS NOT NULL
                  RETURN count(r) AS rels_with_pages }
        RETURN pages, embedded, links, rels_with_pages
        """
    )
    unlinked = [
        row["name"]
        for row in run_query(
            "MATCH (e:Element) WHERE e.source IS NOT NULL "
            "AND NOT (e)-[:SUPPORTED_BY]->(:Evidence) RETURN e.name AS name ORDER BY name"
        )
    ]
    missing_pages = [
        row["page"]
        for row in run_query(
            "MATCH (e:Element) WHERE e.pages IS NOT NULL UNWIND e.pages AS page "
            "WITH DISTINCT page WHERE NOT EXISTS { MATCH (:Evidence {page: page}) } "
            "RETURN page ORDER BY page"
        )
    ]
    lines = [
        f"Evidence pages:                    {counts['pages']} ({counts['embedded']} with embeddings)",
        f"SUPPORTED_BY links:                {counts['links']}",
        f"Relationships with page numbers:   {counts['rels_with_pages']}",
        f"Elements with a Source but no link: {len(unlinked)}",
    ]
    lines += [f"  - {name}" for name in unlinked]
    if missing_pages:
        lines.append(f"Cited pages missing from the PDF:  {missing_pages}")
    all_ok = not unlinked and not missing_pages and counts["pages"] == counts["embedded"]
    return all_ok, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Load P180 pages and embeddings into Neo4j.")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="Path to P180.pdf.")
    args = parser.parse_args()

    pages = read_pages(args.pdf)
    print(f"Read {len(pages)} pages (printed pages {pages[0]['page']}-{pages[-1]['page']}).")
    try:
        create_schema()
        embedded, skipped = store_pages(pages)
        print(f"Embedded {embedded} page(s), skipped {skipped} unchanged.")
        run_query(LINK_EVIDENCE_QUERY)
        run_query("CALL db.awaitIndex($index, 300)", index=VECTOR_INDEX)
        all_ok, text = report()
    finally:
        close_driver()

    print(text)
    if not all_ok:
        print("Evidence layer is incomplete. Run scripts/load_graph.py first if no elements are linked.")
        return 1
    print("Evidence layer is complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
