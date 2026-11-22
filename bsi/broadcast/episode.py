"""Episode planning and scripting.

The script is built from the case file, never from a model's memory. What the
narrator is allowed to say is set by the claim's computed status:

  documented           "The record shows ..."
  supported_secondary  "Scholars report ... the original record isn't in the case file yet."
  contested            "The evidence conflicts."
  unsupported          "The evidence we have doesn't carry this claim."
  contradicted         "The surviving record points the other way."
  unexamined           "Open case — no evidence examined yet. Here's what we're looking for."

Phrasing is drawn from pools seeded by the claim, and each episode's structure
follows its own evidence, so no two episodes are the same video with the names swapped.
"""
import random
from datetime import date

from .. import claims as claims_mod
from .. import compiler, db

STATUS_LINE = {
    "documented": ["The record shows it.", "The documents back it up.", "On this point, the record is clear."],
    "supported_secondary": ["Scholars report it, but the original record isn't in our case file yet.",
                            "Historians support it. We're still hunting the primary record."],
    "contested": ["The evidence conflicts.", "The record pulls in two directions.",
                  "Credible sources disagree."],
    "unsupported": ["The evidence we have doesn't carry this claim.",
                    "So far, the support is too thin to stand on."],
    "contradicted": ["The surviving record points the other way.",
                     "The primary documents don't support this claim."],
    "unexamined": ["This is an open case. No evidence has been examined yet.",
                   "Open case. Nothing has been weighed yet."],
}
STATUS_LABEL = {"documented": "DOCUMENTED", "supported_secondary": "SUPPORTED BY SCHOLARSHIP",
                "contested": "CONTESTED", "unsupported": "UNSUPPORTED", "contradicted": "CONTRADICTED",
                "unexamined": "OPEN CASE"}
HOOKS = {
    "ancestry": ["Who was {name}, really?", "Where did {name} come from?",
                 "What do the records say about {name}'s origins?"],
    "depiction": ["Did they change how {name} looked?", "Is the {name} you've seen the {name} who lived?",
                  "Look closely at {name}. Then look at the record."],
    "contribution": ["Who really did the work?", "Whose name belongs on this?",
                     "{name}. Remember the name. Here's why."],
    "attribution": ["Who got the credit, and who did the work?", "Where did {name}'s credit go?"],
    "succession": ["Follow the records. Follow the money.", "Where did it all go?"],
    "biographical": ["What does the record say about {name}?"],
}
STANCE_INTRO = {
    "supports": ["Evidence for:", "In support:", "This source backs it:"],
    "contradicts": ["Evidence against:", "This source cuts the other way:", "The counter-record:"],
    "context": ["For context:", "Background:"],
}
TIER_SAY = {"primary": "a primary record, made at the time", "secondary": "later scholarship",
            "tertiary": "a later retelling", "unverified": "a source of unconfirmed origin"}


def _years(p):
    def y(v):
        return None if v is None else (f"{-v} BCE" if v < 0 else str(v))
    if p.birth_year is None and p.death_year is None:
        return ""
    return f"{y(p.birth_year) or '?'}–{y(p.death_year) or ''}"


def _evidence_rows(c):
    rows = []
    for ev in c.evidence:
        s = ev.source
        rows.append({"evidence_id": ev.id, "source_id": s.id, "title": s.title, "tier": s.tier,
                     "holder": s.holder, "year": s.created_year, "date": s.created_date,
                     "stance": ev.stance, "page": ev.page_no, "quote": ev.quote,
                     "descriptor": ev.depiction_descriptor, "sha256": s.sha256,
                     "mime": s.mime, "rights": s.rights, "added_by": ev.added_by})
    tier_rank = {"primary": 0, "secondary": 1, "tertiary": 2, "unverified": 3}
    return sorted(rows, key=lambda r: (tier_rank.get(r["tier"], 9), r["stance"] != "supports"))


def last_aired_status(s, claim_id):
    ep = (s.query(db.Episode).filter(db.Episode.claim_id == claim_id,
                                     db.Episode.state.in_(["approved", "published"]))
          .order_by(db.Episode.id.desc()).first())
    return ep.status_at_render if ep else None


def plan_next(s) -> dict | None:
    """Pick today's episode. Priority:
    1. a claim whose status changed since it last aired  -> case update (the series moves forward)
    2. a claim with evidence that has never aired        -> full case episode
    3. an open case (no evidence yet)                    -> open case: what we're looking for
    """
    aired = {e.claim_id for e in s.query(db.Episode).filter(db.Episode.state.in_(["approved", "published"]))}
    drafted = {e.claim_id for e in s.query(db.Episode).filter(db.Episode.state == "draft")}
    updates, fresh, open_cases = [], [], []
    for c in s.query(db.Claim):
        if c.id in drafted:
            continue
        st = claims_mod.compute_status(c)["status"]
        prev = last_aired_status(s, c.id)
        if c.id in aired and prev and prev != st:
            updates.append((c, prev))
        elif c.id not in aired and c.evidence:
            fresh.append(c)
        elif c.id not in aired:
            open_cases.append(c)
    if updates:
        c, prev = updates[0]
        return {"claim_id": c.id, "kind": "update", "previous_status": prev}
    if fresh:
        c = max(fresh, key=lambda c: len(c.evidence))
        return {"claim_id": c.id, "kind": "case"}
    if open_cases:
        rng = random.Random(date.today().toordinal())
        return {"claim_id": rng.choice(open_cases).id, "kind": "open_case"}
    return None


def build_script(claim_id: int, kind: str = "case", previous_status: str | None = None,
                 captain_note: str | None = None) -> dict:
    s = db.session()
    try:
        c = s.get(db.Claim, claim_id)
        p = c.person
        st = claims_mod.compute_status(c)
        status = st["status"]
        rng = random.Random(f"{c.id}:{kind}:{len(c.evidence)}:{status}")
        name = p.display_name if p else "this case"
        pick = lambda pool: rng.choice(pool).format(name=name)  # noqa: E731
        ev = _evidence_rows(c)
        spec = compiler.compile_claim(c.id)
        scenes = []

        # 1. cold open
        if kind == "update":
            hook = f"Case update. {name}."
            narr = (f"Case update on {name}. When we last looked at this claim, its status was "
                    f"{STATUS_LABEL.get(previous_status, previous_status).lower()}. "
                    f"New evidence has changed that. Here's what moved.")
        elif kind == "open_case":
            hook = pick(HOOKS.get(c.claim_type, HOOKS["biographical"]))
            narr = f"{hook} {pick(STATUS_LINE['unexamined'])} Today we lay out the question, and the records that could answer it."
        else:
            hook = pick(HOOKS.get(c.claim_type, HOOKS["biographical"]))
            narr = f"{hook} Today's case file: {name}."
        scenes.append({"type": "title", "narration": narr, "hook": hook, "name": name,
                       "years": _years(p) if p else "", "claim": c.statement,
                       "status": status, "case_no": c.id})

        # 2. the claim, stated with its alternatives up front
        alts = list(c.alternatives or [])
        narr = f"The claim: {c.statement}"
        if alts:
            narr += f" The competing explanation{'s' if len(alts) > 1 else ''}: " + " Or: ".join(alts[:2])
        scenes.append({"type": "claim", "narration": narr, "claim": c.statement,
                       "alternatives": alts[:3], "claim_type": c.claim_type})

        # 3. depiction timeline, when the case is about how someone was shown
        if c.claim_type == "depiction":
            tl = claims_mod.depiction_timeline(p.id) if p else []
            if tl:
                life = [r for r in tl if r["made_in_lifetime"]]
                late = [r for r in tl if not r["made_in_lifetime"]]
                narr = (f"We lined up every portrayal of {name} in the case file by date. "
                        f"{len(life)} {'was' if len(life) == 1 else 'were'} made in their lifetime, "
                        f"{len(late)} after.")
                if life and late:
                    narr += (f" The earliest, from {life[0]['created_date'] or life[0]['year']}, shows "
                             f"{life[0]['descriptor']}. By {late[-1]['created_date'] or late[-1]['year']}, "
                             f"the portrayal reads: {late[-1]['descriptor']}.")
                scenes.append({"type": "timeline", "narration": narr, "points": tl,
                               "born": p.birth_year, "died": p.death_year, "name": name})

        # 4. evidence — every piece, for and against
        for i, e in enumerate(ev[:6]):
            intro = rng.choice(STANCE_INTRO.get(e["stance"], STANCE_INTRO["context"]))
            when = f" from {e['date'] or e['year']}" if (e["date"] or e["year"]) else ""
            where = f", held by {e['holder']}" if e["holder"] else ""
            narr = f"{intro} {e['title']}{when}{where}. It's {TIER_SAY.get(e['tier'], 'a source')}."
            if e["quote"]:
                narr += f" It reads: {e['quote']}"
            if e["added_by"] != "human":
                narr += " This link was proposed by our software and is awaiting human review."
            scenes.append({"type": "evidence", "narration": narr, "evidence": e, "index": i + 1,
                           "of": len(ev), "citation": f"S{e['source_id']}" + (f" p.{e['page']}" if e["page"] else "")})

        # 5. the scale — the living status, computed now
        line = pick(STATUS_LINE[status])
        sup = sum(st["supports"].values())
        con = sum(st["contradicts"].values())
        narr = (f"Where the case stands today. {sup} source{'s' if sup != 1 else ''} for, "
                f"{con} against. {line}")
        if status == "documented" and c.needs_review:
            narr += " A human reviewer still has to sign off."
        narr += " This status isn't an opinion. It's computed from the evidence, and it changes when the evidence does."
        scenes.append({"type": "scale", "narration": narr, "status": status, "supports": st["supports"],
                       "contradicts": st["contradicts"], "as_of": date.today().isoformat(),
                       "label": STATUS_LABEL[status]})

        # 6. what would settle it
        qs = spec["questions"][:3]
        narr = "What would settle it: " + " ".join(q["q"] for q in qs[:2])
        if c.negative_result:
            narr += f" And what would count against it: {c.negative_result}"
        scenes.append({"type": "questions", "narration": narr, "questions": qs,
                       "negative": c.negative_result})

        # 7. the producer's own words, when given — the human voice of the series
        if captain_note:
            scenes.append({"type": "note", "narration": captain_note, "text": captain_note})

        # 8. sources + call for records
        narr = ("Every source in this episode is listed with its archive and fingerprint. "
                "If you hold records that bear on this case, the file stays open.")
        scenes.append({"type": "sources", "narration": narr, "sources": ev, "case_no": c.id})

        named = name.lower() in hook.lower()
        title = {"update": f"Case Update: {name} — now {STATUS_LABEL[status].title()}",
                 "open_case": f"Open Case: {hook}" if named else f"Open Case: {name} — {hook}",
                 "case": hook if named else f"{name}: {hook}"}[kind]
        return {"claim_id": c.id, "person_id": p.id if p else None, "kind": kind, "title": title[:95],
                "status": status, "previous_status": previous_status, "scenes": scenes,
                "spec_hash": spec["spec_hash"], "as_of": date.today().isoformat()}
    finally:
        s.close()
