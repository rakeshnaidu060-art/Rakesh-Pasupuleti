"""Neo4j connection and shared helpers.

Settings are read from Streamlit secrets (.streamlit/secrets.toml locally, the
app settings on Streamlit Community Cloud). Environment variables with the same
names are used as a fallback, so scripts and tests can run outside Streamlit.
"""

import os
from functools import lru_cache

import certifi
from neo4j import Driver, GraphDatabase, TrustCustomCAs


def get_secret(key: str, default: str | None = None) -> str | None:
    """Return a setting from Streamlit secrets, falling back to the environment."""
    try:
        import streamlit as st

        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        # No secrets file, or Streamlit not available: use the environment.
        pass
    return os.environ.get(key, default)


def _require(key: str) -> str:
    value = get_secret(key)
    if not value:
        raise RuntimeError(
            f"Missing setting {key}. Add it to .streamlit/secrets.toml "
            "(see secrets.toml.example)."
        )
    return value


def get_database() -> str:
    return get_secret("NEO4J_DATABASE") or "neo4j"


@lru_cache(maxsize=1)
def get_driver() -> Driver:
    """Return one shared Neo4j driver for the whole process."""
    uri = _require("NEO4J_URI")
    auth = (_require("NEO4J_USERNAME"), _require("NEO4J_PASSWORD"))
    scheme, sep, rest = uri.partition("://")
    if scheme.endswith("+s"):
        # Verify the server certificate against certifi's CA bundle rather than
        # the OS store: Windows may lack the root that AuraDB's certificate
        # chains to (SSL.com) until it is fetched on demand, which fails here.
        return GraphDatabase.driver(
            scheme[:-2] + sep + rest,
            auth=auth,
            encrypted=True,
            trusted_certificates=TrustCustomCAs(certifi.where()),
        )
    return GraphDatabase.driver(uri, auth=auth)


def run_query(query: str, **params) -> list[dict]:
    """Run a read or write query with parameters and return the rows as dicts.

    User text must always be passed in ``params``, never formatted into ``query``.
    """
    records, _, _ = get_driver().execute_query(
        query, parameters_=params, database_=get_database()
    )
    return [record.data() for record in records]


def check_connection() -> tuple[bool, str]:
    """Check that Neo4j is reachable. Returns (ok, message)."""
    try:
        get_driver().verify_connectivity()
        count = run_query("MATCH (n) RETURN count(n) AS nodes")[0]["nodes"]
        return True, f"Connected to Neo4j ({count} nodes in the database)."
    except Exception as exc:
        return False, (
            f"Could not reach Neo4j: {exc}. If you use AuraDB Free, the instance "
            "may be paused; resume it in the Aura console."
        )


def close_driver() -> None:
    if get_driver.cache_info().currsize:
        get_driver().close()
        get_driver.cache_clear()
