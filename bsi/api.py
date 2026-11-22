"""HTTP API.  Run:  uvicorn bsi.api:app --host 0.0.0.0 --port 8080"""
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import claims, compiler, connectors, db, egress, fetch, llm, loader, registry, search, vault

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title="BAUBSA Sovereign Intelligence", version="0.2.0", lifespan=lifespan)

# Write safety lives in the database, not in the HTTP layer: every audit append takes a
# transaction-scoped lock before reading the chain head (db.serialize_writes), so slow
# requests (model calls, large downloads) never block other writes.
STATIC = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC / "index.html")


# ------------------------------------------------------------ evidence
@app.post("/sources")
def upload_source(file: UploadFile = File(...), title: str = Form(None),
                        author: str = Form(None), created_date: str = Form(None),
                        created_year: int = Form(None), holder: str = Form(None),
                        origin_url: str = Form(None), tier: str = Form("unverified"),
                        sensitive: bool = Form(False), rights: str = Form(None)):
    if tier not in claims.TIER_WEIGHT:
        raise HTTPException(400, f"tier must be one of {list(claims.TIER_WEIGHT)}")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / Path(file.filename).name
        with open(p, "wb") as f:
            shutil.copyfileobj(file.file, f)
        src = vault.register(p, title=title, author=author, created_date=created_date,
                             created_year=created_year, holder=holder, origin_url=origin_url,
                             tier=tier, sensitive=sensitive, rights=rights)
    return {"source_id": src.id, "sha256": src.sha256, "injection_flags": src.injection_flags}


@app.get("/sources/{sid}/verify")
def verify_source(sid: int):
    return vault.verify_source(sid)


@app.get("/passages")
def passages(q: str):
    return vault.find_passage(q)


@app.get("/audit/verify")
def audit_verify():
    s = db.session()
    try:
        return vault.verify_audit(s)
    finally:
        s.close()


# ------------------------------------------------------------ registry
class PersonIn(BaseModel):
    name: str
    birth_year: int | None = None
    death_year: int | None = None
    fields: list[str] = []
    aliases: list[str] = []


@app.post("/persons")
def create_person(p: PersonIn):
    return {"person_id": registry.add_person(p.name, birth_year=p.birth_year, death_year=p.death_year,
                                             fields=p.fields, aliases=p.aliases).id}


@app.get("/persons")
def list_persons(field: str | None = None, q: str | None = None, has_claims: bool = False,
                 limit: int = 100, offset: int = 0):
    s = db.session()
    try:
        qry = s.query(db.Person).order_by(db.Person.display_name)
        if q:
            qry = qry.filter(db.Person.display_name.ilike(f"%{q}%"))
        if has_claims:
            qry = qry.filter(db.Person.claims.any())
        if field:   # JSON field: filter in Python, then paginate
            people = [p for p in qry if field in (p.fields or [])]
            total, page = len(people), people[offset:offset + limit]
        else:       # paginate in the database
            total, page = qry.count(), qry.offset(offset).limit(limit).all()
        out = []
        for p in page:
            out.append({"id": p.id, "name": p.display_name, "born": p.birth_year, "died": p.death_year,
                        "fields": p.fields, "tags": p.tags or [], "external_ids": p.external_ids or {},
                        "claims": [claims.compute_status(c) | {"statement": c.statement, "type": c.claim_type}
                                   for c in p.claims]})
        return {"total": total, "offset": offset, "items": out}
    finally:
        s.close()


@app.get("/persons/{pid:int}")
def person_detail(pid: int):
    s = db.session()
    try:
        p = s.get(db.Person, pid)
        if not p:
            raise HTTPException(404)
        return {"id": p.id, "name": p.display_name, "born": p.birth_year, "died": p.death_year,
                "fields": p.fields, "tags": p.tags or [], "external_ids": p.external_ids or {},
                "notes": p.notes, "aliases": [n.name for n in p.names],
                "identity_terms": [{"term": t.term, "kind": t.kind, "period": t.period,
                                    "source_id": t.source_id, "page": t.page_no} for t in p.identity_terms],
                "claims": [claim_status(c.id) | {"id": c.id, "type": c.claim_type} for c in p.claims],
                "depictions": claims.depiction_timeline(pid)}
    finally:
        s.close()


@app.get("/stats")
def stats():
    s = db.session()
    try:
        by_status = {}
        for c in s.query(db.Claim):
            st = claims.compute_status(c)["status"]
            by_status[st] = by_status.get(st, 0) + 1
        return {"persons": s.query(db.Person).count(), "claims": s.query(db.Claim).count(),
                "sources": s.query(db.Source).count(), "evidence_links": s.query(db.EvidenceLink).count(),
                "open_questions": s.query(db.Question).filter_by(status="open").count(),
                "manifests": s.query(db.ResearchManifest).count(), "claims_by_status": by_status,
                "audit": vault.verify_audit(s)}
    finally:
        s.close()


@app.get("/sources")
def list_sources(limit: int = 100):
    s = db.session()
    try:
        return [{"id": x.id, "title": x.title, "tier": x.tier, "holder": x.holder,
                 "created_date": x.created_date, "created_year": x.created_year, "pages": len(x.pages), "rights": x.rights,
                 "sha256": x.sha256, "origin_url": x.origin_url, "sensitive": x.sensitive,
                 "injection_flags": len(x.injection_flags or [])}
                for x in s.query(db.Source).order_by(db.Source.id.desc()).limit(limit)]
    finally:
        s.close()


# ------------------------------------------------------------ archives
@app.get("/connectors")
def list_connectors():
    return [connectors.get(n).info() for n in connectors.REGISTRY]


@app.get("/archives/search")
def archives_search(q: str, connectors_: str | None = Query(None, alias="connectors"), limit: int = 10):
    names = [n for n in (connectors_ or "").split(",") if n] or None
    try:
        return connectors.run_all(q, names=names, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e))


class ImportIn(BaseModel):
    url: str
    title: str | None = None
    tier: str = "unverified"
    holder: str | None = None
    created_date: str | None = None
    created_year: int | None = None
    rights: str | None = None


@app.post("/archives/import")
def archives_import(i: ImportIn):
    if i.tier not in claims.TIER_WEIGHT:
        raise HTTPException(400, "bad tier")
    try:
        src = fetch.fetch_to_vault(i.url, title=i.title, tier=i.tier, holder=i.holder,
                                   created_date=i.created_date, created_year=i.created_year, rights=i.rights)
    except egress.EgressDenied as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"could not fetch: {connectors.base.safe_error(e)}")
    return {"source_id": src.id, "sha256": src.sha256, "injection_flags": src.injection_flags}


@app.get("/loader/options")
def loader_options():
    return {"groups": sorted(loader.GROUPS), "occupations": sorted(loader.OCCUPATIONS)}


class LoadIn(BaseModel):
    group: str
    occupation: str | None = None
    limit: int = 500
    offset: int = 0


@app.post("/loader/wikidata")
def load_wd(l: LoadIn):
    try:
        return loader.load_wikidata(l.group, l.occupation, limit=min(l.limit, 5000), offset=l.offset)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/persons/match")
def match(name: str, birth_year: int | None = None, death_year: int | None = None):
    return claims.match_person(name, birth_year, death_year)


@app.get("/persons/{pid}/depictions")
def depictions(pid: int):
    return claims.depiction_timeline(pid)


class IdentityIn(BaseModel):
    term: str
    kind: str
    source_id: int | None = None
    page_no: int | None = None
    period: str | None = None


@app.post("/persons/{pid}/identity")
def identity(pid: int, t: IdentityIn):
    try:
        x = claims.add_identity_term(pid, t.term, t.kind, source_id=t.source_id,
                                     page_no=t.page_no, period=t.period)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"identity_term_id": x.id}


class ClaimIn(BaseModel):
    person_id: int
    claim_type: str
    statement: str
    alternatives: list[str] = []
    negative_result: str | None = None


@app.post("/claims")
def create_claim(c: ClaimIn):
    try:
        return {"claim_id": registry.add_claim(c.person_id, c.claim_type, c.statement,
                                               alternatives=c.alternatives,
                                               negative_result=c.negative_result).id}
    except ValueError as e:
        raise HTTPException(400, str(e))


class EvidenceIn(BaseModel):
    source_id: int
    stance: str
    page_no: int | None = None
    quote: str | None = None
    depiction_descriptor: str | None = None


@app.post("/claims/{cid}/evidence")
def add_evidence(cid: int, e: EvidenceIn):
    try:
        registry.link_evidence(cid, e.source_id, e.stance, page_no=e.page_no, quote=e.quote,
                               depiction_descriptor=e.depiction_descriptor)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return claim_status(cid)


@app.get("/claims/{cid}")
def claim_status(cid: int):
    s = db.session()
    try:
        c = s.get(db.Claim, cid)
        if not c:
            raise HTTPException(404)
        return claims.compute_status(c) | {
            "statement": c.statement, "alternatives": c.alternatives,
            "evidence": [{"source": e.source.title, "tier": e.source.tier, "stance": e.stance,
                          "page": e.page_no, "quote": e.quote, "sha256": e.source.sha256}
                         for e in c.evidence]}
    finally:
        s.close()


class ReviewIn(BaseModel):
    reviewer: str
    note: str


@app.post("/claims/{cid}/review")
def review(cid: int, r: ReviewIn):
    registry.review(cid, r.reviewer, r.note)
    return claim_status(cid)


# ------------------------------------------------------------ research
@app.post("/claims/{cid}/compile")
def compile_claim(cid: int):
    return compiler.compile_claim(cid)


@app.post("/claims/{cid}/search")
def run_search(cid: int):
    spec = compiler.compile_claim(cid)
    return search.run_manifest(spec, compiler.search_queries(spec))


@app.get("/claims/{cid}/questions")
def questions(cid: int):
    s = db.session()
    try:
        return [{"id": q.id, "text": q.text, "records": q.record_types, "status": q.status,
                 "parent": q.parent_question_id}
                for q in s.query(db.Question).filter_by(claim_id=cid)]
    finally:
        s.close()


class ExpandIn(BaseModel):
    finding_kind: str


@app.post("/questions/{qid}/expand")
def expand(qid: int, e: ExpandIn):
    return compiler.expand_finding(qid, e.finding_kind)


class AskIn(BaseModel):
    question: str
    page_ids: list[int]


@app.post("/ask")
def ask(a: AskIn):
    return llm.synthesize(a.question, a.page_ids)


@app.post("/seed")
def seed():
    return {"added": registry.load_seed()}


# ------------------------------------------------------------ broadcast
import threading  # noqa: E402
import uuid  # noqa: E402

from .broadcast import episode as bc_episode  # noqa: E402
from .broadcast import studio  # noqa: E402

_JOBS: dict[str, dict] = {}


class EpisodeIn(BaseModel):
    claim_id: int | None = None      # omit to let the planner pick today's episode
    kind: str | None = None          # case | update | open_case
    note: str | None = None          # producer's note, read in the episode
    short: bool = True


@app.get("/broadcast/plan")
def broadcast_plan():
    s = db.session()
    try:
        return bc_episode.plan_next(s) or {"status": "nothing_to_make"}
    finally:
        s.close()


@app.post("/broadcast/episodes")
def broadcast_make(e: EpisodeIn):
    """Rendering takes minutes, so it runs in the background; poll /broadcast/jobs/{id}."""
    job = uuid.uuid4().hex[:12]
    _JOBS[job] = {"state": "running"}

    def run():
        try:
            _JOBS[job] = {"state": "done", "result": studio.make_episode(e.claim_id, e.kind, captain_note=e.note,
                                                                         make_short=e.short)}
        except Exception as ex:   # surfaced to the UI, not swallowed
            _JOBS[job] = {"state": "failed", "error": f"{type(ex).__name__}: {ex}"[:300]}
    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job}


@app.get("/broadcast/jobs/{job_id}")
def broadcast_job(job_id: str):
    if job_id not in _JOBS:
        raise HTTPException(404)
    return _JOBS[job_id]


def _ep_out(ep):
    return {"id": ep.id, "claim_id": ep.claim_id, "kind": ep.kind, "title": ep.title, "state": ep.state,
            "status_at_render": ep.status_at_render, "previous_status": ep.previous_status, "voice": ep.voice,
            "approved_by": ep.approved_by, "published": ep.published or {},
            "files": sorted((ep.files or {}).keys()), "created_at": str(ep.created_at)}


@app.get("/broadcast/episodes")
def broadcast_list(limit: int = 50):
    s = db.session()
    try:
        return [_ep_out(e) for e in s.query(db.Episode).order_by(db.Episode.id.desc()).limit(limit)]
    finally:
        s.close()


@app.get("/broadcast/episodes/{ep_id:int}")
def broadcast_get(ep_id: int):
    s = db.session()
    try:
        ep = s.get(db.Episode, ep_id)
        if not ep:
            raise HTTPException(404)
        out = _ep_out(ep)
        out["script"] = ep.script
        return out
    finally:
        s.close()


@app.get("/broadcast/episodes/{ep_id:int}/files/{role}")
def broadcast_file(ep_id: int, role: str):
    s = db.session()
    try:
        ep = s.get(db.Episode, ep_id)
        path = (ep.files or {}).get(role) if ep else None   # only files the episode recorded — no path input
    finally:
        s.close()
    if not path or not Path(path).exists():
        raise HTTPException(404)
    return FileResponse(path)


class ReviewEpIn(BaseModel):
    reviewer: str
    note: str = ""


@app.post("/broadcast/episodes/{ep_id:int}/approve")
def broadcast_approve(ep_id: int, r: ReviewEpIn):
    try:
        studio.approve(ep_id, r.reviewer, r.note)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return broadcast_get(ep_id)


@app.post("/broadcast/episodes/{ep_id:int}/reject")
def broadcast_reject(ep_id: int, r: ReviewEpIn):
    try:
        studio.reject(ep_id, r.reviewer, r.note)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return broadcast_get(ep_id)


class PublishIn(BaseModel):
    platform: str = "youtube"       # youtube | package
    privacy: str = "private"
    publish_at: str | None = None   # RFC 3339, schedules the release
    short: bool = False


@app.post("/broadcast/episodes/{ep_id:int}/publish")
def broadcast_publish(ep_id: int, p: PublishIn):
    kw = {} if p.platform == "package" else {"privacy": p.privacy, "publish_at": p.publish_at, "short": p.short}
    try:
        return studio.publish(ep_id, p.platform, **kw)
    except PermissionError as ex:
        raise HTTPException(403, str(ex))
    except (RuntimeError, KeyError) as ex:
        raise HTTPException(400, str(ex))
