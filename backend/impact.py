"""Impact analysis: what is affected if an application is replaced.

A fixed set of predefined graph queries turns an application name into one
structured result. No AI is involved, and the same name always gives the same
result. Every finding carries a label decided here from the Status of the links
it rests on, the graph path that produced it and the P180 pages it cites.
Missing data is reported as a gap, never as "no impact".

User text is only ever passed to Neo4j as a query parameter.
"""

import difflib
import re
from typing import Literal

from pydantic import BaseModel, Field

from backend.graph import run_query

DOCUMENTED_FACT = "Documented fact"
INFERENCE = "Inference"
Label = Literal["Documented fact", "Inference"]

# Words that carry no meaning when matching a name the user typed.
_STOPWORDS = {"the", "a", "an", "our", "tool", "system", "application", "app", "service", "platform"}
_LEADING_DETERMINER = re.compile(r"^(the|our|a|an|my|your|their)\s+", re.IGNORECASE)


# --- Result model ---


class Finding(BaseModel):
    kind: Literal[
        "realizes",
        "uncovered_capability",
        "still_covered_capability",
        "subdomain",
        "domain",
        "platform",
        "component",
        "platform_peer",
        "connected_domain",
        "dependency",
    ]
    label: Label
    subject: str = Field(description="The element the finding is about.")
    statement: str
    related: list[str] = Field(default_factory=list, description="Other elements involved.")
    paths: list[str] = Field(description="Graph paths that produced the finding.")
    pages: list[int] = Field(description="Printed P180 pages cited by the links on the paths.")
    sources: list[str] = Field(default_factory=list)


class Gap(BaseModel):
    kind: Literal["no_dependencies", "no_platform", "no_capabilities", "no_domain", "no_connected_domains"]
    statement: str


class MapNode(BaseModel):
    id: str
    type: str
    role: Literal[
        "application",
        "capability",
        "uncovered_capability",
        "covering_application",
        "subdomain",
        "domain",
        "platform",
        "connected_domain",
    ]


class MapEdge(BaseModel):
    source: str
    target: str
    type: str
    label: Label
    title: str = ""


class ImpactResult(BaseModel):
    status: Literal["ok", "ambiguous", "not_found"]
    query: str = Field(description="The application name as given.")
    application: str | None = None
    message: str = ""
    candidates: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    nodes: list[MapNode] = Field(default_factory=list)
    edges: list[MapEdge] = Field(default_factory=list)

    def by_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]


# --- Helpers ---


def label_for(*statuses: str | None) -> Label:
    """A finding is a documented fact only if every link it rests on is Documented."""
    return DOCUMENTED_FACT if statuses and all(s == "Documented" for s in statuses) else INFERENCE


def _pages(*links: dict | None) -> list[int]:
    return sorted({p for link in links if link for p in (link.get("pages") or [])})


def _sources(*links: dict | None) -> list[str]:
    seen: list[str] = []
    for link in links:
        if link and link.get("source") and link["source"] not in seen:
            seen.append(link["source"])
    return seen


def _status_note(*links: dict | None) -> str:
    """Explain a link that is not Documented, using its Notes from the Excel."""
    notes = [
        f"{link['status']}: {link['notes']}" if link.get("notes") else link["status"]
        for link in links
        if link and link.get("status") != "Documented"
    ]
    return f" ({'; '.join(notes)})" if notes else ""


# --- Name resolution ---


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS}


def resolve_application(name: str) -> tuple[str | None, list[str], str]:
    """Resolve a name to exactly one application, or return candidates.

    Returns (application, candidates, message). An exact match (ignoring case
    and spacing) is used directly; anything else returns the closest names for
    the user to choose from. It never guesses, even with one candidate.
    """
    typed = " ".join(name.split())
    # "the Booking System" or "our booking system" name the application
    # exactly; a leading article or possessive is not part of the name.
    typed = _LEADING_DETERMINER.sub("", typed) or typed
    applications = [row["name"] for row in run_query(
        "MATCH (a:Application) RETURN a.name AS name ORDER BY name"
    )]
    for application in applications:
        if application.lower() == typed.lower():
            return application, [], ""

    other = run_query(
        "MATCH (e:Element) WHERE toLower(e.name) = toLower($name) RETURN e.name AS name, e.type AS type",
        name=typed,
    )
    if other:
        return None, [], f"'{other[0]['name']}' is a {other[0]['type']} in the model, not an application."

    typed_words = _words(typed)
    scored = []
    for application in applications:
        ratio = difflib.SequenceMatcher(None, typed.lower(), application.lower()).ratio()
        overlap = len(typed_words & _words(application))
        if overlap or ratio >= 0.6:
            scored.append((-overlap, -ratio, application))
    candidates = [application for _, _, application in sorted(scored)[:5]]
    if candidates:
        return None, candidates, f"'{typed}' does not exactly match an application. Did you mean one of these?"
    return None, [], f"No application named '{typed}' was found in the model."


# --- Predefined queries ---

_CAPABILITIES = """
MATCH (a:Application {name: $name})-[r:REALIZES]->(c:Capability)
OPTIONAL MATCH (other:Application)-[r2:REALIZES]->(c) WHERE other <> a
WITH c, r, other, r2 ORDER BY other.name
RETURN c.name AS capability, properties(r) AS link,
       [x IN collect({name: other.name, link: properties(r2)}) WHERE x.name IS NOT NULL] AS others
ORDER BY capability
"""

_MEMBERSHIP = """
MATCH (a:Application {name: $name})-[r:BELONGS_TO]->(p)
WHERE p:Subdomain OR p:Domain
OPTIONAL MATCH (p:Subdomain)-[r2:BELONGS_TO]->(d:Domain)
RETURN p.name AS parent, p.type AS parent_type, properties(r) AS link,
       d.name AS domain, properties(r2) AS domain_link
ORDER BY parent
"""

_PLATFORMS = """
MATCH (a:Application {name: $name})-[r:BELONGS_TO]->(pl:Platform)
OPTIONAL MATCH (peer:Element)-[r2:BELONGS_TO]->(pl) WHERE peer <> a
WITH pl, r, peer, r2 ORDER BY peer.name
RETURN pl.name AS platform, properties(r) AS link,
       [x IN collect({name: peer.name, type: peer.type, link: properties(r2)}) WHERE x.name IS NOT NULL] AS peers
ORDER BY platform
"""

_COMPONENTS = """
MATCH (t:TechnologyComponent)-[r:BELONGS_TO]->(:Application {name: $name})
RETURN t.name AS component, properties(r) AS link
ORDER BY component
"""

_CONNECTED_DOMAINS = """
MATCH (d:Domain)<-[r1:USED_IN]-(x:Element)-[r2:USED_IN]->(other:Domain)
WHERE d.name IN $domains AND NOT other.name IN $domains AND (x:DataObject OR x:Platform)
RETURN other.name AS domain, d.name AS own_domain, x.name AS shared, x.type AS shared_type,
       properties(r1) AS link1, properties(r2) AS link2
ORDER BY domain, shared, own_domain
"""

_DEPENDENCIES = """
MATCH (a:Application {name: $name})-[r:DEPENDS_ON]-(x:Element)
RETURN x.name AS other, x.type AS other_type, startNode(r) = a AS outgoing, properties(r) AS link
ORDER BY other
"""

_ANY_DEPENDENCIES = "MATCH ()-[r:DEPENDS_ON]->() RETURN count(r) AS n"


# --- Analysis ---


def analyze_replacement(name: str) -> ImpactResult:
    """Return the impact of replacing the named application.

    If the name does not resolve to exactly one application, the result has
    status "ambiguous" (with candidates to choose from) or "not_found", and no
    findings.
    """
    application, candidates, message = resolve_application(name)
    if application is None:
        status = "ambiguous" if candidates else "not_found"
        return ImpactResult(status=status, query=name, message=message, candidates=candidates)

    result = ImpactResult(status="ok", query=name, application=application)
    nodes: dict[str, MapNode] = {application: MapNode(id=application, type="Application", role="application")}
    edges: list[MapEdge] = []
    app = application

    # Capabilities, and whether another application still covers each one.
    capabilities = run_query(_CAPABILITIES, name=app)
    for row in capabilities:
        cap, link = row["capability"], row["link"]
        path = f"{app} -[REALIZES]-> {cap}"
        result.findings.append(Finding(
            kind="realizes", label=label_for(link["status"]), subject=cap,
            statement=f"{app} realizes the capability {cap}.{_status_note(link)}",
            paths=[path], pages=_pages(link), sources=_sources(link),
        ))
        edges.append(MapEdge(source=app, target=cap, type="REALIZES", label=label_for(link["status"])))

        if not row["others"]:
            result.findings.append(Finding(
                kind="uncovered_capability", label=label_for(link["status"]), subject=cap,
                statement=f"{cap} would be left uncovered: no other application in P180 realizes it.",
                paths=[path], pages=_pages(link), sources=_sources(link),
            ))
            nodes[cap] = MapNode(id=cap, type="Capability", role="uncovered_capability")
        else:
            others = row["others"]
            other_links = [o["link"] for o in others]
            result.findings.append(Finding(
                kind="still_covered_capability",
                label=label_for(link["status"], *(l["status"] for l in other_links)),
                subject=cap, related=[o["name"] for o in others],
                statement=f"{cap} stays covered by {', '.join(o['name'] for o in others)}.",
                paths=[f"{path} <-[REALIZES]- {o['name']}" for o in others],
                pages=_pages(link, *other_links), sources=_sources(link, *other_links),
            ))
            nodes[cap] = MapNode(id=cap, type="Capability", role="capability")
            for o in others:
                nodes.setdefault(o["name"], MapNode(id=o["name"], type="Application", role="covering_application"))
                edges.append(MapEdge(source=o["name"], target=cap, type="REALIZES", label=label_for(o["link"]["status"])))

    # Subdomains and domains.
    domains: list[str] = []
    for row in run_query(_MEMBERSHIP, name=app):
        parent, link = row["parent"], row["link"]
        if row["parent_type"] == "Domain":
            # The mapping figure for this domain has no subdomains.
            domains.append(parent)
            result.findings.append(Finding(
                kind="domain", label=label_for(link["status"]), subject=parent,
                statement=f"{app} belongs directly to the {parent} domain.{_status_note(link)}",
                paths=[f"{app} -[BELONGS_TO]-> {parent}"], pages=_pages(link), sources=_sources(link),
            ))
            nodes[parent] = MapNode(id=parent, type="Domain", role="domain")
            edges.append(MapEdge(source=app, target=parent, type="BELONGS_TO", label=label_for(link["status"])))
            continue

        result.findings.append(Finding(
            kind="subdomain", label=label_for(link["status"]), subject=parent,
            statement=f"{app} belongs to the subdomain {parent}.{_status_note(link)}",
            paths=[f"{app} -[BELONGS_TO]-> {parent}"], pages=_pages(link), sources=_sources(link),
        ))
        nodes[parent] = MapNode(id=parent, type="Subdomain", role="subdomain")
        edges.append(MapEdge(source=app, target=parent, type="BELONGS_TO", label=label_for(link["status"])))
        if row["domain"]:
            domain, domain_link = row["domain"], row["domain_link"]
            domains.append(domain)
            result.findings.append(Finding(
                kind="domain", label=label_for(link["status"], domain_link["status"]), subject=domain,
                related=[parent],
                statement=f"{app} belongs to the {domain} domain through the subdomain {parent}.",
                paths=[f"{app} -[BELONGS_TO]-> {parent} -[BELONGS_TO]-> {domain}"],
                pages=_pages(link, domain_link), sources=_sources(link, domain_link),
            ))
            nodes[domain] = MapNode(id=domain, type="Domain", role="domain")
            edges.append(MapEdge(source=parent, target=domain, type="BELONGS_TO", label=label_for(domain_link["status"])))
    domains = sorted(set(domains))

    # Platform, and what else runs on it.
    platforms = run_query(_PLATFORMS, name=app)
    for row in platforms:
        platform, link = row["platform"], row["link"]
        result.findings.append(Finding(
            kind="platform", label=label_for(link["status"]), subject=platform,
            statement=f"{app} runs on {platform}.{_status_note(link)}",
            paths=[f"{app} -[BELONGS_TO]-> {platform}"], pages=_pages(link), sources=_sources(link),
        ))
        nodes[platform] = MapNode(id=platform, type="Platform", role="platform")
        edges.append(MapEdge(source=app, target=platform, type="BELONGS_TO", label=label_for(link["status"])))
        for peer in row["peers"]:
            kind = "application" if peer["type"] == "Application" else "technology component"
            result.findings.append(Finding(
                kind="platform_peer", label=INFERENCE, subject=peer["name"], related=[platform],
                statement=(
                    f"The {kind} {peer['name']} also runs on {platform} and may be affected "
                    f"if the platform changes with {app}."
                ),
                paths=[f"{app} -[BELONGS_TO]-> {platform} <-[BELONGS_TO]- {peer['name']}"],
                pages=_pages(link, peer["link"]), sources=_sources(link, peer["link"]),
            ))

    # Technology components that are part of the application itself.
    for row in run_query(_COMPONENTS, name=app):
        component, link = row["component"], row["link"]
        result.findings.append(Finding(
            kind="component", label=label_for(link["status"]), subject=component,
            statement=f"The technology component {component} is part of {app}.{_status_note(link)}",
            paths=[f"{component} -[BELONGS_TO]-> {app}"], pages=_pages(link), sources=_sources(link),
        ))

    # Other domains that share data objects or platforms with the application's domains.
    connected: dict[str, list[dict]] = {}
    if domains:
        for row in run_query(_CONNECTED_DOMAINS, domains=domains):
            connected.setdefault(row["domain"], []).append(row)
    for domain, rows in connected.items():
        shared = sorted({r["shared"] for r in rows})
        links = [l for r in rows for l in (r["link1"], r["link2"])]
        result.findings.append(Finding(
            kind="connected_domain", label=INFERENCE, subject=domain, related=shared,
            statement=(
                f"The {domain} domain shares {len(shared)} data object(s) or platform(s) with "
                f"{' and '.join(domains)}: {', '.join(shared)}. Changes to how {app} handles this "
                "data may reach it."
            ),
            paths=[f"{r['own_domain']} <-[USED_IN]- {r['shared']} -[USED_IN]-> {domain}" for r in rows],
            pages=_pages(*links), sources=_sources(*links),
        ))
        nodes[domain] = MapNode(id=domain, type="Domain", role="connected_domain")
        for own in sorted({r["own_domain"] for r in rows}):
            edges.append(MapEdge(
                source=own, target=domain, type="SHARES_DATA", label=INFERENCE,
                title=", ".join(sorted({r["shared"] for r in rows if r["own_domain"] == own})),
            ))

    # Dependencies. P180 documents none; any present were added by the team.
    dependencies = run_query(_DEPENDENCIES, name=app)
    for row in dependencies:
        link, other = row["link"], row["other"]
        if row["outgoing"]:
            statement, path = f"{app} depends on {other}.", f"{app} -[DEPENDS_ON]-> {other}"
        else:
            statement, path = f"{other} depends on {app}.", f"{other} -[DEPENDS_ON]-> {app}"
        result.findings.append(Finding(
            kind="dependency", label=label_for(link["status"]), subject=other,
            statement=statement + _status_note(link),
            paths=[path], pages=_pages(link), sources=_sources(link),
        ))

    # Gaps: missing data is reported, never presented as "no impact".
    if not dependencies:
        if run_query(_ANY_DEPENDENCIES)[0]["n"] == 0:
            reason = "P180 documents no dependencies between applications"
        else:
            reason = f"The model records no dependencies for {app}"
        result.gaps.append(Gap(
            kind="no_dependencies",
            statement=(
                f"{reason}, so effects on other systems through interfaces or data flows are "
                "unknown, not absent. They should be checked with the system owners."
            ),
        ))
    if not platforms:
        result.gaps.append(Gap(
            kind="no_platform",
            statement=f"P180 assigns {app} to no platform, so its technology impact is unknown.",
        ))
    if not capabilities:
        result.gaps.append(Gap(
            kind="no_capabilities",
            statement=f"P180 maps no capabilities to {app}, so its business impact is unknown.",
        ))
    if not domains:
        result.gaps.append(Gap(
            kind="no_domain",
            statement=f"P180 places {app} in no domain, so connected domains cannot be determined.",
        ))
    elif not connected:
        result.gaps.append(Gap(
            kind="no_connected_domains",
            statement=(
                f"P180 shows no data objects or platforms that {' and '.join(domains)} share with "
                "other domains, so links to other domains are unknown."
            ),
        ))

    result.nodes = sorted(nodes.values(), key=lambda n: (n.role, n.id))
    result.edges = sorted(edges, key=lambda e: (e.type, e.source, e.target))
    return result
