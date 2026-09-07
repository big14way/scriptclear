"""Parallel Search fan-out: the partner integration, called at runtime for every entity.

Deterministic Python (no LLM loop): builds category-specific queries for each
extracted entity, runs them concurrently against the Parallel Search API, and
stores citation-bearing evidence in session state for the adjudicator.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from google.adk.tools.tool_context import ToolContext
from parallel import Parallel

MAX_ENTITIES = int(os.getenv("CLEARCUT_MAX_ENTITIES", "120"))
MAX_RESULTS = int(os.getenv("CLEARCUT_MAX_RESULTS", "4"))
MAX_WORKERS = int(os.getenv("CLEARCUT_MAX_WORKERS", "8"))
SEARCH_MODE = os.getenv("CLEARCUT_SEARCH_MODE", "fast")  # turbo | fast | basic | advanced
EXCERPT_CHARS = int(os.getenv("CLEARCUT_EXCERPT_CHARS", "300"))

_client: Parallel | None = None


def _get_client() -> Parallel:
    global _client
    if _client is None:
        _client = Parallel(api_key=os.environ["PARALLEL_API_KEY"])
    return _client


def _queries_for(entity: dict) -> tuple[str, list[str]]:
    """Return (objective, search_queries) tuned per clearance category."""
    t = entity["text"]
    a = entity.get("attributes") or {}
    cat = entity["category"]
    if cat == "person":
        prof, city = a.get("profession", ""), a.get("city", "")
        objective = (
            f"Determine whether a real, living person named {t} exists"
            f"{' who is a ' + prof if prof else ''}{' in ' + city if city else ''}, "
            "or whether this is a public figure."
        )
        return objective, [f"{t} {prof}".strip(), f"{t} {city}".strip(), f"{t} profile"]
    if cat == "brand":
        return (
            f"Is {t} a real registered brand, product, or trademark? Who owns it?",
            [f"{t} trademark", f"{t} brand owner", f"{t} company"],
        )
    if cat == "song_or_work":
        return (
            f"Is '{t}' a copyrighted song, book, poem, film, or artwork? Who holds the rights, "
            "or is it in the public domain?",
            [f"{t} song copyright", f"{t} rights holder publisher", f"{t} public domain"],
        )
    if cat == "business_or_location":
        city = a.get("city", "")
        return (
            f"Does a real business or place called {t} exist{' in ' + city if city else ''}?",
            [f"{t} {city}".strip(), f"{t} business", f"{t} address"],
        )
    return (
        f"Does the identifier {t} belong to a real person, business, or website?",
        [f"{t}", f"{t} owner", f"{t} lookup"],
    )


def _search_one(entity: dict) -> dict:
    objective, queries = _queries_for(entity)
    queries = [q for q in dict.fromkeys(q.strip() for q in queries) if q][:3]
    try:
        res = _get_client().search(
            objective=objective,
            search_queries=queries,
            mode=SEARCH_MODE,
            advanced_settings={"max_results": MAX_RESULTS},
        )
        evidence = [
            {
                "title": r.title or r.url,
                "url": r.url,
                "excerpt": (r.excerpts[0][:EXCERPT_CHARS] if r.excerpts else ""),
            }
            for r in res.results
        ]
        if not evidence:
            evidence = [{"title": "no_results", "url": "", "excerpt": "Parallel Search returned no results."}]
    except Exception as e:  # never let one failure kill the run
        evidence = [{"title": "search_error", "url": "", "excerpt": str(e)[:200]}]
    return {"entity_id": entity["id"], "queries": queries, "evidence": evidence}


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def loads_lenient(text: str) -> Any:
    """Parse the first complete JSON value in text, tolerating prose before it and extra data after it."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                value, _ = decoder.raw_decode(text, i)
                return value
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("no JSON value found", text, 0)


def parse_entities(raw: Any) -> list[dict]:
    """Accept a JSON string (optionally fenced), a list, or an {"entities": [...]} dict."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        raw = _FENCE.sub("", raw)
        raw = loads_lenient(raw)
    if isinstance(raw, dict):
        raw = raw.get("entities", [])
    if not isinstance(raw, list):
        return []
    out = []
    for i, e in enumerate(raw):
        if not isinstance(e, dict) or not e.get("text"):
            continue
        e.setdefault("id", f"E{i + 1}")
        e.setdefault("category", "person")
        e.setdefault("scene", "")
        e.setdefault("context", "")
        e.setdefault("attributes", {})
        out.append(e)
    return out


def research_entities(tool_context: ToolContext) -> dict:
    """Runs a Parallel web search for every extracted entity and stores cited evidence in state.

    Reads state["entities"] (JSON from the extractor), writes state["evidence"] (JSON list of
    {entity_id, queries, evidence:[{title,url,excerpt}]}). Returns a short status summary.
    """
    entities = parse_entities(tool_context.state.get("entities"))
    truncated = len(entities) > MAX_ENTITIES
    entities = entities[:MAX_ENTITIES]
    # Normalise the entity list so downstream prompts see clean JSON.
    tool_context.state["entities"] = json.dumps(entities, ensure_ascii=False)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(_search_one, entities))

    tool_context.state["evidence"] = json.dumps(results, ensure_ascii=False)
    tool_context.state["run_date"] = date.today().strftime("%B %d, %Y")
    errors = sum(1 for r in results if r["evidence"] and r["evidence"][0]["title"] == "search_error")
    return {"researched": len(results), "search_errors": errors, "truncated": truncated}
