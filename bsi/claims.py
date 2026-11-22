"""Claim engine: the evidence decides, not the institution and not the model.

Status ladder (computed, never typed in):
  unexamined           no evidence linked yet
  documented           primary evidence supports it, no primary evidence against
  supported_secondary  only scholarship supports it; primary record not yet found
  contested            credible evidence on both sides
  unsupported          only weak support (tertiary/unverified), or only evidence against
  contradicted         primary evidence against it, none for it
Every status carries `needs_review` until a human signs off, and model-added
links never count toward `documented` on their own.
"""
import re
from difflib import SequenceMatcher

from . import db
from .vault import audit

TIER_WEIGHT = {"primary": 3, "secondary": 2, "tertiary": 1, "unverified": 0}


def compute_status(claim: db.Claim) -> dict:
    sup = {t: 0 for t in TIER_WEIGHT}
    con = {t: 0 for t in TIER_WEIGHT}
    model_only = True
    for ev in claim.evidence:
        tier = ev.source.tier or "unverified"
        if ev.added_by == "human":
            model_only = False
        if ev.stance == "supports":
            sup[tier] += 1
        elif ev.stance == "contradicts":
            con[tier] += 1

    strong_sup = sup["primary"] + sup["secondary"]
    strong_con = con["primary"] + con["secondary"]

    if not claim.evidence:
        status = "unexamined"
    elif sup["primary"] and not con["primary"] and not con["secondary"]:
        status = "documented"
    elif strong_sup and strong_con:
        status = "contested"
    elif con["primary"] and not strong_sup:
        status = "contradicted"
    elif sup["secondary"] and not strong_con:
        status = "supported_secondary"
    else:
        status = "unsupported"

    if status == "documented" and model_only:
        status = "supported_secondary"   # a model's reading alone can't establish a record

    return {"claim_id": claim.id, "status": status, "supports": sup, "contradicts": con,
            "needs_review": claim.needs_review,
            "gaps": _gaps(claim, sup, con)}


def _gaps(claim, sup, con) -> list[str]:
    g = []
    if not sup["primary"]:
        g.append("No primary (contemporary) record linked in support.")
    if not (con["primary"] + con["secondary"] + con["tertiary"]):
        g.append("No contrary evidence examined yet — search for it before concluding.")
    if claim.claim_type == "depiction" and len([e for e in claim.evidence if e.depiction_descriptor]) < 2:
        g.append("Depiction claims need at least two dated portrayals to show change over time.")
    return g


def depiction_timeline(person_id: int) -> list[dict]:
    """Every portrayal of a person, oldest first, with how each source portrays them
    and how far after the person's lifetime it was made. This is how 'they changed
    how he looked' becomes something you can show, or rule out."""
    s = db.session()
    try:
        p = s.get(db.Person, person_id)
        rows = []
        for c in p.claims:
            for ev in c.evidence:
                if not ev.depiction_descriptor:
                    continue
                yr = ev.source.created_year
                rows.append({
                    "year": yr, "created_date": ev.source.created_date,
                    "years_after_death": (yr - p.death_year) if (yr and p.death_year) else None,
                    "made_in_lifetime": bool(yr and p.birth_year and p.death_year and p.birth_year <= yr <= p.death_year),
                    "descriptor": ev.depiction_descriptor, "source": ev.source.title,
                    "tier": ev.source.tier, "holder": ev.source.holder,
                    "page": ev.page_no, "quote": ev.quote,
                })
        return sorted(rows, key=lambda r: (r["year"] is None, r["year"] or 0))
    finally:
        s.close()


# ---------------------------------------------------------------- identity terms
def add_identity_term(person_id: int, term: str, kind: str, *, source_id=None,
                      page_no=None, period=None):
    if kind not in db.IDENTITY_KINDS:
        raise ValueError(f"kind must be one of {sorted(db.IDENTITY_KINDS)}")
    if kind in {"historical_label", "government_category"} and not source_id:
        raise ValueError("historical labels and government categories must cite the document they come from")
    s = db.session()
    try:
        t = db.IdentityTerm(person_id=person_id, term=term, kind=kind,
                            source_id=source_id, page_no=page_no, period=period)
        s.add(t)
        s.flush()
        audit(s, "identity.add", {"person_id": person_id, "term": term, "kind": kind, "source_id": source_id})
        s.commit()
        return t
    finally:
        s.close()


# ---------------------------------------------------------------- entity resolution
def _norm(name: str) -> str:
    return re.sub(r"[^a-z ]", "", name.lower()).strip()


def match_person(name: str, birth_year: int | None = None, death_year: int | None = None,
                 threshold: float = 0.88) -> list[dict]:
    """Deterministic candidate matching. Similar names with conflicting dates are
    reported as DISTINCT, never merged automatically."""
    s = db.session()
    try:
        out = []
        for p in s.query(db.Person):
            names = [p.display_name] + [n.name for n in p.names]
            score = max(SequenceMatcher(None, _norm(name), _norm(n)).ratio() for n in names)
            if score < 0.6:
                continue
            conflict = []
            if birth_year and p.birth_year and abs(birth_year - p.birth_year) > 2:
                conflict.append(f"birth {birth_year} vs {p.birth_year}")
            if death_year and p.death_year and abs(death_year - p.death_year) > 2:
                conflict.append(f"death {death_year} vs {p.death_year}")
            verdict = ("distinct" if conflict else
                       "same" if score >= threshold and (birth_year or death_year) else
                       "possible" if score >= threshold else "unlikely")
            out.append({"person_id": p.id, "name": p.display_name, "score": round(score, 3),
                        "verdict": verdict, "conflicts": conflict})
        return sorted(out, key=lambda r: -r["score"])
    finally:
        s.close()
