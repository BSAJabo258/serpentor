import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import httpx

from .. import db, egress
from ..vault import audit

USER_AGENT = os.environ.get("BSI_USER_AGENT",
                            "BAUBSA-Sovereign-Intelligence/0.2 (independent historical research)")


@dataclass
class Record:
    connector: str
    title: str
    url: str | None = None
    identifier: str | None = None
    date: str | None = None
    snippet: str | None = None
    media_type: str | None = None
    holder: str | None = None          # the institution holding the original
    rights: str | None = None
    download_url: str | None = None    # direct link to text/PDF/image when known
    extra: dict = field(default_factory=dict)


class Connector:
    name = "base"
    label = "Base"
    holder = None
    key_env: str | None = None         # env var holding the API key, if one is needed
    kind = "archive"                    # archive | newspapers | scholarship | patents | museum | knowledge-graph | images
    docs = None

    def __init__(self, client: httpx.Client | None = None):
        self.client = client

    @property
    def key(self):
        return os.environ.get(self.key_env) if self.key_env else None

    def ready(self) -> bool:
        return not self.key_env or bool(self.key)

    def http(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(timeout=30, follow_redirects=True,
                                       headers={"User-Agent": USER_AGENT})
        return self.client

    def get(self, url, **kw):
        import time
        egress.check(url, sensitive=False)
        for attempt in range(3):
            try:
                r = self.http().get(url, timeout=kw.pop("timeout", 45), **kw)
            except httpx.TimeoutException:
                if attempt == 2:
                    raise
                continue
            if r.status_code in (429, 503) and attempt < 2:
                time.sleep(min(float(r.headers.get("Retry-After", 2 * (attempt + 1))), 10))
                continue
            r.raise_for_status()
            return r

    def post(self, url, **kw):
        egress.check(url, sensitive=False)
        r = self.http().post(url, **kw)
        r.raise_for_status()
        return r

    def search(self, query: str, limit: int = 10) -> list[Record]:
        raise NotImplementedError

    def info(self):
        return {"name": self.name, "label": self.label, "kind": self.kind, "holder": self.holder,
                "needs_key": bool(self.key_env), "key_env": self.key_env,
                "ready": self.ready(), "docs": self.docs}


def safe_error(e: Exception, c: "Connector | None" = None) -> str:
    """Error text without URLs or keys — request URLs can carry API keys."""
    if isinstance(e, httpx.HTTPStatusError):
        msg = f"HTTP {e.response.status_code}"
    elif isinstance(e, httpx.TimeoutException):
        msg = "timed out"
    elif isinstance(e, httpx.TransportError):
        msg = "could not connect"
    else:
        msg = type(e).__name__
    if c is not None and c.key:
        msg = msg.replace(c.key, "[redacted]")
    return msg


REGISTRY: dict[str, type[Connector]] = {}

# Where the same material is often also held, when a source can't be reached.
ALTERNATES = {
    "loc": ["internet_archive", "dpla", "commons"],
    "chronicling_america": ["internet_archive", "dpla"],
    "gallica": ["europeana", "internet_archive"],
    "nara": ["dpla", "internet_archive", "loc"],
    "patents": ["internet_archive", "loc"],
    "smithsonian": ["commons", "dpla"],
    "europeana": ["gallica", "commons", "internet_archive"],
    "openalex": ["internet_archive", "loc"],
    "dpla": ["loc", "internet_archive"],
}


def register(cls):
    REGISTRY[cls.name] = cls
    return cls


def get(name, client=None) -> Connector:
    return REGISTRY[name](client=client)


def run_all(query: str, *, names: list[str] | None = None, limit: int = 10,
            client: httpx.Client | None = None, save: bool = True) -> dict:
    names = names or list(REGISTRY)
    runs = []
    for n in names:
        c = get(n, client=client)
        if not c.ready():
            runs.append({"connector": n, "status": "needs_key", "key_env": c.key_env, "records": []})
            continue
        try:
            recs = [asdict(r) for r in c.search(query, limit=limit)]
            runs.append({"connector": n, "status": "ok", "count": len(recs), "records": recs})
        except Exception as e:   # one archive failing never stops the others
            runs.append({"connector": n, "status": "error", "error": safe_error(e, c), "records": []})
    failed = [r["connector"] for r in runs if r["status"] != "ok"]
    fallbacks = sorted({a for f in failed for a in ALTERNATES.get(f, [])
                        if a not in failed and a in REGISTRY})
    out = {"query": query, "retrieved_at": datetime.now(timezone.utc).isoformat(), "runs": runs,
           "unreached": failed, "alternate_routes": fallbacks}
    out["results_hash"] = hashlib.sha256(
        json.dumps([r["records"] for r in runs], sort_keys=True).encode()).hexdigest()
    if save:
        s = db.session()
        try:
            spec = {"kind": "archive_search", "query": query, "connectors": names, "limit": limit}
            spec_hash = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
            m = db.ResearchManifest(spec_hash=spec_hash, spec=spec, results=out)
            s.add(m)
            s.flush()
            audit(s, "archives.search", {"manifest_id": m.id, "query": query,
                                         "results_hash": out["results_hash"],
                                         "status": {r["connector"]: r["status"] for r in runs}})
            s.commit()
            out["manifest_id"] = m.id
        finally:
            s.close()
    return out
