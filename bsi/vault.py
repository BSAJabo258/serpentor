"""Evidence vault: write-once originals + hash-chained audit trail."""
import hashlib
import json
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

from . import config, db
from .extract import extract_pages
from .quarantine import scan

GENESIS = "0" * 64


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


# ---------------------------------------------------------------- audit chain
def audit(s, action: str, payload: dict) -> db.AuditEvent:
    db.serialize_writes(s)          # before the head read, held until this transaction ends
    last = s.query(db.AuditEvent).order_by(db.AuditEvent.id.desc()).first()
    prev = last.hash if last else GENESIS
    at = datetime.now(timezone.utc).isoformat()
    h = hashlib.sha256((prev + at + action + _canon(payload)).encode()).hexdigest()
    ev = db.AuditEvent(at=at, action=action, payload=payload, prev_hash=prev, hash=h)
    s.add(ev)
    s.flush()
    return ev


def verify_audit(s) -> dict:
    prev = GENESIS
    n = 0
    for ev in s.query(db.AuditEvent).order_by(db.AuditEvent.id):
        expect = hashlib.sha256((prev + ev.at + ev.action + _canon(ev.payload)).encode()).hexdigest()
        if ev.prev_hash != prev or ev.hash != expect:
            return {"ok": False, "broken_at_event": ev.id, "checked": n}
        prev = ev.hash
        n += 1
    return {"ok": True, "checked": n, "head": prev}


# ---------------------------------------------------------------- ingestion
def vault_path(sha: str, suffix: str) -> Path:
    return config.VAULT_DIR / sha[:2] / f"{sha}{suffix}"


def register(path: str | Path, *, title=None, author=None, created_date=None,
             created_year=None, origin_url=None, holder=None, tier="unverified",
             sensitive=False, rights=None) -> db.Source:
    """Copy a file into the vault (read-only), extract text per page, scan for
    embedded instructions, and record everything in the audit chain.
    The file the user handed in is only ever read, never modified."""
    path = Path(path)
    sha = sha256_file(path)
    s = db.session()
    try:
        existing = s.query(db.Source).filter_by(sha256=sha).first()
        if existing:
            if sensitive and not existing.sensitive:      # upgrade only, never downgrade
                existing.sensitive = True
                audit(s, "source.sensitivity_upgrade", {"source_id": existing.id, "sha256": sha})
                s.commit()
            return existing
        dest = vault_path(sha, path.suffix.lower())
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(path, dest)
            dest.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        if sha256_file(dest) != sha:
            raise IOError("vault copy hash mismatch")

        pages, mime = extract_pages(dest)
        flags = []
        for i, t in enumerate(pages, 1):
            for f in scan(t):
                flags.append({"page": i, **f})

        src = db.Source(sha256=sha, original_name=path.name, mime=mime,
                        size_bytes=dest.stat().st_size, title=title or path.stem,
                        author=author, created_date=created_date, created_year=created_year,
                        origin_url=origin_url, holder=holder, tier=tier,
                        sensitive=sensitive, rights=rights, injection_flags=flags)
        src.pages = [db.Page(page_no=i, text=t) for i, t in enumerate(pages, 1)]
        s.add(src)
        s.flush()
        audit(s, "source.register", {"source_id": src.id, "sha256": sha, "name": path.name,
                                     "pages": len(pages), "tier": tier,
                                     "injection_flags": len(flags)})
        s.commit()
        return src
    finally:
        s.close()


def verify_source(source_id: int) -> dict:
    s = db.session()
    try:
        src = s.get(db.Source, source_id)
        if src is None:
            return {"source_id": source_id, "ok": False, "error": "no such source"}
        p = next(config.VAULT_DIR.glob(f"{src.sha256[:2]}/{src.sha256}*"), None)
        if p is None:
            return {"source_id": source_id, "ok": False, "error": "original missing from vault"}
        actual = sha256_file(p)
        return {"source_id": source_id, "ok": actual == src.sha256, "sha256": actual}
    finally:
        s.close()


def find_passage(text: str, limit: int = 20) -> list[dict]:
    """Exact-phrase lookup returning source, page number and the paragraph."""
    s = db.session()
    try:
        out = []
        q = s.query(db.Page).filter(db.Page.text.contains(text, autoescape=True)).order_by(db.Page.id)
        for pg in q.yield_per(200):
            if len(out) >= limit:
                break
            if text not in (pg.text or ""):      # case-sensitive literal match (SQLite LIKE ignores case)
                continue
            para = next((p.strip() for p in pg.text.split("\n\n") if text in p), pg.text)
            out.append({"source_id": pg.source_id, "sha256": pg.source.sha256,
                        "title": pg.source.title, "page": pg.page_no, "paragraph": para})
        return out
    finally:
        s.close()


def backup(dest: str | Path) -> Path:
    """Consistent SQLite backup (Postgres: use pg_dump; see README)."""
    import sqlite3
    dest = Path(dest)
    src_path = config.DATABASE_URL.replace("sqlite:///", "")
    with sqlite3.connect(src_path) as a, sqlite3.connect(dest) as b:
        a.backup(b)
    return dest
