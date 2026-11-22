"""Great Minds registry: people, claims, evidence links."""
import json
from pathlib import Path

from . import db
from .vault import audit

SEED = Path(__file__).parent / "seed" / "research_targets.json"


def add_person(name, *, birth_year=None, death_year=None, fields=None, aliases=None, notes=None):
    s = db.session()
    try:
        p = db.Person(display_name=name, birth_year=birth_year, death_year=death_year,
                      fields=fields or [], notes=notes)
        p.names = [db.PersonName(name=a) for a in (aliases or [])]
        s.add(p)
        s.flush()
        audit(s, "person.add", {"person_id": p.id, "name": name})
        s.commit()
        return p
    finally:
        s.close()


def add_claim(person_id, claim_type, statement, *, alternatives=None, negative_result=None):
    if claim_type not in db.CLAIM_TYPES:
        raise ValueError(f"claim_type must be one of {sorted(db.CLAIM_TYPES)}")
    s = db.session()
    try:
        c = db.Claim(person_id=person_id, claim_type=claim_type, statement=statement,
                     alternatives=alternatives or [], negative_result=negative_result)
        s.add(c)
        s.flush()
        audit(s, "claim.add", {"claim_id": c.id, "person_id": person_id, "type": claim_type})
        s.commit()
        return c
    finally:
        s.close()


def link_evidence(claim_id, source_id, stance, *, page_no=None, quote=None,
                  depiction_descriptor=None, added_by="human"):
    if stance not in {"supports", "contradicts", "context"}:
        raise ValueError("stance must be supports | contradicts | context")
    s = db.session()
    try:
        if quote:
            pages = s.query(db.Page).filter_by(source_id=source_id)
            if page_no:
                pages = pages.filter_by(page_no=page_no)
            hit = next((p for p in pages if quote in (p.text or "")), None)
            if not hit:
                raise ValueError("quote not found in that source" + (" on that page" if page_no else "")
                                 + " — link refused")
            page_no = page_no or hit.page_no
        ev = db.EvidenceLink(claim_id=claim_id, source_id=source_id, stance=stance,
                             page_no=page_no, quote=quote,
                             depiction_descriptor=depiction_descriptor, added_by=added_by)
        s.add(ev)
        claim = s.get(db.Claim, claim_id)
        claim.needs_review = True
        s.flush()
        audit(s, "evidence.link", {"claim_id": claim_id, "source_id": source_id,
                                   "stance": stance, "page": page_no, "by": added_by})
        s.commit()
        return ev
    finally:
        s.close()


def review(claim_id, reviewer, note):
    s = db.session()
    try:
        c = s.get(db.Claim, claim_id)
        c.needs_review = False
        c.reviewer_note = f"{reviewer}: {note}"
        audit(s, "claim.review", {"claim_id": claim_id, "reviewer": reviewer, "note": note})
        s.commit()
    finally:
        s.close()


def load_seed(path=SEED) -> int:
    data = json.loads(Path(path).read_text())
    s = db.session()
    existing = {p.display_name: (p.id, {c.statement for c in p.claims}) for p in s.query(db.Person)}
    s.close()
    n = 0
    for t in data["targets"]:
        if t["name"] in existing:          # resume: add only the claims that are missing
            pid, have = existing[t["name"]]
        else:
            pid, have = add_person(t["name"], birth_year=t.get("birth_year"),
                                   death_year=t.get("death_year"), fields=t.get("fields")).id, set()
            n += 1
        for c in t.get("claims", []):
            if c["statement"] not in have:
                add_claim(pid, c["type"], c["statement"], alternatives=c.get("alternatives"),
                          negative_result=c.get("negative_result"))
    return n
