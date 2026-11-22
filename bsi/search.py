"""Private metasearch via a self-hosted SearXNG, with every run saved as a manifest."""
import hashlib
import json
from datetime import datetime, timezone

import httpx

from . import config, db, egress
from .vault import audit


def searx(query: str, *, categories="general", pages=1, client=None) -> list[dict]:
    url = f"{config.SEARXNG_URL}/search"
    egress.check(url, sensitive=False)
    own = client is None
    c = client or httpx.Client(timeout=30)
    out = []
    try:
        for p in range(1, pages + 1):
            r = c.get(url, params={"q": query, "format": "json", "categories": categories, "pageno": p})
            r.raise_for_status()
            for item in r.json().get("results", []):
                out.append({"url": item.get("url"), "title": item.get("title"),
                            "snippet": item.get("content"), "engine": item.get("engine")})
    finally:
        if own:
            c.close()
    return out


def run_manifest(spec: dict, queries: list[str], *, client=None) -> dict:
    retrieved_at = datetime.now(timezone.utc).isoformat()
    results = []
    for q in queries:
        try:
            hits = searx(q, client=client)
        except Exception as e:   # record failure as a result — negative results matter
            hits = [{"error": str(e)}]
        results.append({"query": q, "hits": hits})
    body = {"spec_hash": spec["spec_hash"], "queries": queries, "retrieved_at": retrieved_at,
            "results": results}
    rh = hashlib.sha256(json.dumps(results, sort_keys=True).encode()).hexdigest()
    s = db.session()
    try:
        m = db.ResearchManifest(spec_hash=spec["spec_hash"], spec=spec, results=body)
        s.add(m)
        s.flush()
        audit(s, "search.manifest", {"manifest_id": m.id, "spec_hash": spec["spec_hash"],
                                     "queries": len(queries), "results_hash": rh})
        s.commit()
        return {"manifest_id": m.id, "results_hash": rh, **body}
    finally:
        s.close()
