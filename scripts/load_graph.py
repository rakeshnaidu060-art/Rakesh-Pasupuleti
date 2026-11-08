"""Load the architecture model from the Excel file into Neo4j.

Usage:
    python scripts/load_graph.py [--excel PATH]

The Elements sheet becomes nodes and the Relationships sheet becomes
relationships (see section 3 of the sprint plan). The Excel is validated first;
if any check fails, nothing is written. Each run replaces all Element nodes, so
running it twice gives the same graph. Evidence nodes (P180 pages) are left in
place and the new elements are linked to them again.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.evidence import LINK_EVIDENCE_QUERY, page_numbers  # noqa: E402
from backend.graph import close_driver, get_database, get_driver  # noqa: E402

DEFAULT_EXCEL = ROOT / "data" / "Lufthansa_Architecture_Data_P180_All_Domains.xlsx"

# Allowed values, matching the dropdowns and the Read Me sheet of the Excel.
# Labels and relationship types cannot be query parameters, so they are only
# ever taken from these fixed mappings, never from the file directly.
ELEMENT_LABELS = {
    "Domain": "Domain",
    "Subdomain": "Subdomain",
    "Application": "Application",
    "Capability": "Capability",
    "Data Object": "DataObject",
    "Platform": "Platform",
    "Technology Component": "TechnologyComponent",
}
RELATIONSHIP_TYPES = {
    "Belongs To": "BELONGS_TO",
    "Realizes": "REALIZES",
    "Associated With": "ASSOCIATED_WITH",
    "Used In": "USED_IN",
    "Depends On": "DEPENDS_ON",
}
STATUSES = {"Documented", "Inferred", "Illustrative"}

ELEMENT_COLUMNS = ("Name", "Type", "Source", "Notes")
RELATIONSHIP_COLUMNS = ("From", "To", "Type", "Status", "Source", "Notes")


class ValidationError(Exception):
    """The Excel failed one or more checks. ``errors`` lists every problem."""

    def __init__(self, errors: list[str]):
        super().__init__(f"{len(errors)} problem(s) found in the Excel")
        self.errors = errors


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_sheet(workbook, sheet: str, columns: tuple[str, ...]) -> list[dict]:
    if sheet not in workbook.sheetnames:
        raise ValidationError([f"Sheet '{sheet}' is missing."])
    rows = workbook[sheet].iter_rows(values_only=True)
    header = [_clean(c) for c in next(rows, ())]
    missing = [c for c in columns if c not in header]
    if missing:
        raise ValidationError([f"Sheet '{sheet}' is missing column(s): {', '.join(missing)}."])
    index = {c: header.index(c) for c in columns}
    records = []
    for row_number, row in enumerate(rows, start=2):
        if all(_clean(c) is None for c in row):
            continue
        record = {c.lower(): _clean(row[i]) if i < len(row) else None for c, i in index.items()}
        record["row"] = row_number
        records.append(record)
    return records


def read_excel(path: Path = DEFAULT_EXCEL) -> tuple[list[dict], list[dict]]:
    """Read the Elements and Relationships sheets.

    Elements have keys name, type, source, notes; relationships have keys
    from, to, type, status, source, notes. Both also carry their Excel row.
    In Neo4j, both also get ``pages``: the printed P180 pages in their Source.
    """
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        elements = _read_sheet(workbook, "Elements", ELEMENT_COLUMNS)
        relationships = _read_sheet(workbook, "Relationships", RELATIONSHIP_COLUMNS)
    finally:
        workbook.close()
    return elements, relationships


def validate(elements: list[dict], relationships: list[dict]) -> list[str]:
    """Return every problem found in the model; an empty list means it is valid."""
    errors = []

    for e in elements:
        where = f"Elements row {e['row']}"
        if not e["name"]:
            errors.append(f"{where}: Name is empty.")
        if e["type"] not in ELEMENT_LABELS:
            errors.append(f"{where} ({e['name']}): Type '{e['type']}' is not an allowed value.")

    counts = Counter(e["name"] for e in elements if e["name"])
    for name, count in counts.items():
        if count > 1:
            rows = [str(e["row"]) for e in elements if e["name"] == name]
            errors.append(f"Element name '{name}' appears {count} times (rows {', '.join(rows)}).")

    names = set(counts)
    for r in relationships:
        where = f"Relationships row {r['row']}"
        for end in ("from", "to"):
            if not r[end]:
                errors.append(f"{where}: {end.title()} is empty.")
            elif r[end] not in names:
                errors.append(f"{where}: {end.title()} '{r[end]}' is not a name in Elements.")
        if r["type"] not in RELATIONSHIP_TYPES:
            errors.append(f"{where}: Type '{r['type']}' is not an allowed value.")
        if r["status"] not in STATUSES:
            errors.append(f"{where}: Status '{r['status']}' is not an allowed value.")

    return errors


def _write_graph(tx, elements: list[dict], relationships: list[dict]) -> None:
    tx.run("MATCH (n:Element) DETACH DELETE n")

    for excel_type, label in ELEMENT_LABELS.items():
        rows = [
            {
                "name": e["name"],
                "type": e["type"],
                "source": e["source"],
                "notes": e["notes"],
                "pages": page_numbers(e["source"]) or None,
            }
            for e in elements
            if e["type"] == excel_type
        ]
        if rows:
            tx.run(
                f"UNWIND $rows AS row CREATE (n:Element:{label}) SET n = row",
                rows=rows,
            )

    for excel_type, rel_type in RELATIONSHIP_TYPES.items():
        rows = [
            {
                "from": r["from"],
                "to": r["to"],
                "props": {
                    "status": r["status"],
                    "source": r["source"],
                    "notes": r["notes"],
                    "pages": page_numbers(r["source"]) or None,
                },
            }
            for r in relationships
            if r["type"] == excel_type
        ]
        if rows:
            tx.run(
                "UNWIND $rows AS row "
                "MATCH (a:Element {name: row.from}), (b:Element {name: row.to}) "
                f"CREATE (a)-[rel:{rel_type}]->(b) SET rel = row.props",
                rows=rows,
            )

    # Relink the new elements to any P180 pages already loaded by load_evidence.py.
    tx.run(LINK_EVIDENCE_QUERY)


def load(elements: list[dict], relationships: list[dict]) -> None:
    """Replace all Element nodes in Neo4j with the given model, in one transaction."""
    driver = get_driver()
    driver.execute_query(
        "CREATE CONSTRAINT element_name_unique IF NOT EXISTS "
        "FOR (n:Element) REQUIRE n.name IS UNIQUE",
        database_=get_database(),
    )
    with driver.session(database=get_database()) as session:
        session.execute_write(_write_graph, elements, relationships)


def graph_counts() -> tuple[dict[str, int], dict[str, int]]:
    """Count Element nodes per Excel type and relationships between them per Excel type."""
    driver = get_driver()
    db = get_database()
    nodes = {}
    for excel_type, label in ELEMENT_LABELS.items():
        records, _, _ = driver.execute_query(
            f"MATCH (n:Element:{label}) RETURN count(n) AS c", database_=db
        )
        nodes[excel_type] = records[0]["c"]
    rels = {}
    for excel_type, rel_type in RELATIONSHIP_TYPES.items():
        records, _, _ = driver.execute_query(
            f"MATCH (:Element)-[r:{rel_type}]->(:Element) RETURN count(r) AS c", database_=db
        )
        rels[excel_type] = records[0]["c"]
    return nodes, rels


def compare_counts(elements: list[dict], relationships: list[dict]) -> tuple[bool, str]:
    """Compare the graph with the Excel per type. Returns (all_match, report_text)."""
    expected_nodes = Counter(e["type"] for e in elements)
    expected_rels = Counter(r["type"] for r in relationships)
    actual_nodes, actual_rels = graph_counts()

    lines = [f"{'Type':<24}{'Excel':>8}{'Neo4j':>8}"]
    all_match = True

    def section(title, expected, actual):
        nonlocal all_match
        lines.append(f"-- {title}")
        for kind in actual:
            mark = "" if expected[kind] == actual[kind] else "  MISMATCH"
            all_match &= not mark
            lines.append(f"{kind:<24}{expected[kind]:>8}{actual[kind]:>8}{mark}")
        total_expected, total_actual = sum(expected.values()), sum(actual.values())
        lines.append(f"{'Total':<24}{total_expected:>8}{total_actual:>8}")

    section("Elements", expected_nodes, actual_nodes)
    section("Relationships", expected_rels, actual_rels)
    return all_match, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Load the Excel architecture model into Neo4j.")
    parser.add_argument("--excel", type=Path, default=DEFAULT_EXCEL, help="Path to the Excel file.")
    args = parser.parse_args()

    try:
        elements, relationships = read_excel(args.excel)
        errors = validate(elements, relationships)
        if errors:
            raise ValidationError(errors)
    except ValidationError as exc:
        print(f"Excel check failed, nothing was loaded. {exc}:")
        for error in exc.errors:
            print(f"  - {error}")
        return 1

    print(f"Excel is valid: {len(elements)} elements, {len(relationships)} relationships.")
    try:
        load(elements, relationships)
        all_match, report = compare_counts(elements, relationships)
    finally:
        close_driver()

    print(report)
    if not all_match:
        print("Graph does not match the Excel.")
        return 1
    print("Graph matches the Excel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
