"""Research compiler: turns a claim into a reproducible research spec and a tree of
questions, each pointing at the kind of record that would answer it.

Every claim automatically gets questions that look for evidence AGAINST it too —
that is what lets a finding survive a hostile reader.
"""
import hashlib
import json

from . import db
from .vault import audit

# (question template, record types where the answer would live)
TEMPLATES = {
    "ancestry": [
        ("What do baptismal, birth or parish records say about {name}'s parents?", ["parish register", "civil birth record", "notarial record"]),
        ("What did {name} say about their own origins, in their own writing?", ["letters", "memoir", "interviews"]),
        ("How did contemporaries who met {name} describe their appearance and origin?", ["letters", "newspapers", "diaries"]),
        ("What labels did official documents of the period apply to {name} or their parents?", ["census", "military roll", "manumission/slave registers", "passport/travel papers"]),
        ("Which scholars argue for this ancestry, and what primary records do they cite?", ["peer-reviewed scholarship", "biographies"]),
        ("Which scholars argue against it, and what primary records do they cite?", ["peer-reviewed scholarship", "biographies"]),
        ("Is there authenticated genetic or skeletal evidence, and who controls access to it?", ["genetic studies", "museum/archive holdings"]),
    ],
    "depiction": [
        ("What is the earliest surviving portrait of {name}, who made it, and when?", ["museum catalogues", "collection records"]),
        ("Which depictions were made during {name}'s lifetime, from life?", ["museum catalogues", "artist papers"]),
        ("How do written descriptions by people who met {name} describe them?", ["letters", "newspapers", "diaries"]),
        ("When do later depictions diverge from lifetime ones, and who commissioned them?", ["publisher archives", "commission records", "textbooks"]),
        ("Has any original been restored, repainted or retouched? What do conservation records show?", ["conservation reports", "technical imaging"]),
        ("Which depictions are used today by institutions, textbooks and media?", ["textbooks", "museum displays", "film/TV"]),
    ],
    "contribution": [
        ("What primary record establishes {name}'s authorship (patent, publication, ledger, inscription)?", ["patent office", "published works", "institutional records"]),
        ("Who else claimed priority, and what do their records show?", ["interference cases", "court records", "correspondence"]),
        ("How did contemporaries acknowledge or credit the work?", ["journals", "newspapers", "society minutes"]),
        ("Who profited from the work, and through which companies or licences?", ["corporate filings", "licence agreements", "assignments"]),
    ],
    "attribution": [
        ("Where was credit for this work given to someone else, and when did that start?", ["textbooks", "encyclopedias", "institutional histories"]),
        ("What records show the original attribution?", ["patent office", "published works", "archives"]),
        ("Was the change deliberate? Look for correspondence, editorial or policy records.", ["editorial archives", "institutional minutes"]),
        ("Is the shift explained by ordinary causes (lost records, war, language)? Look for those too.", ["archive loss records", "bibliographies"]),
    ],
    "succession": [
        ("Which entity acquired the original institution, and on what date?", ["corporate filings", "merger agreements", "state registries"]),
        ("Were the original records transferred? Who holds them now?", ["archive inventories", "corporate disclosures"]),
        ("Were assets and liabilities transferred, or only the name?", ["merger agreements", "court records"]),
        ("What evidence would establish or disprove a continuing benefit?", ["financial statements", "court records"]),
    ],
    "biographical": [
        ("What primary record establishes this?", ["archives", "civil records"]),
        ("What record would contradict it, and has it been checked?", ["archives", "civil records"]),
    ],
}

# follow-ups spawned when a finding comes in (the self-extending loop)
FOLLOW_UPS = {
    "insurance_policy": ["Who purchased the policy?", "Who was named as the insured person?",
                         "Who was the beneficiary?", "Was the policy paid, and in which ledger?",
                         "Which entity acquired the insurer, and who holds the records now?"],
    "portrait": ["Who commissioned this image?", "Was it made from life?",
                 "Has it been restored or retouched?", "Where has it been reproduced, and how was it altered?"],
    "patent": ["Was the patent assigned or sold, and to whom?", "Was there an interference case?",
               "Which products used it, and who earned the revenue?"],
    "census_entry": ["What label did the enumerator apply, and does it match other records of the same person?",
                     "Who else is in the household?", "Does the next census show the same people under different labels?"],
}


def compile_claim(claim_id: int) -> dict:
    s = db.session()
    try:
        c = s.get(db.Claim, claim_id)
        name = c.person.display_name if c.person else "the subject"
        spec = {
            "claim": c.statement, "claim_type": c.claim_type, "subject": name,
            "hypotheses": [c.statement] + list(c.alternatives or []),
            "negative_result": c.negative_result,
            "entity_matching": "name variants + birth/death year within 2y; conflicts => distinct",
            "date_normalization": "store as written + normalized year; BCE as negative",
            "manual_review": ["any status change", "any model-proposed evidence link", "any identity term"],
            "questions": [],
        }
        for tmpl, rtypes in TEMPLATES.get(c.claim_type, TEMPLATES["biographical"]):
            q = tmpl.format(name=name)
            spec["questions"].append({"q": q, "record_types": rtypes})
            if not s.query(db.Question).filter_by(claim_id=c.id, text=q).first():
                s.add(db.Question(claim_id=c.id, text=q, record_types=rtypes))
        spec_hash = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        spec["spec_hash"] = spec_hash
        audit(s, "compile", {"claim_id": c.id, "spec_hash": spec_hash})
        s.commit()
        return spec
    finally:
        s.close()


def expand_finding(question_id: int, finding_kind: str) -> list[str]:
    """Register a finding and spawn its follow-up questions as children."""
    s = db.session()
    try:
        parent = s.get(db.Question, question_id)
        made = []
        for q in FOLLOW_UPS.get(finding_kind, []):
            if not s.query(db.Question).filter_by(claim_id=parent.claim_id, text=q).first():
                s.add(db.Question(claim_id=parent.claim_id, parent_question_id=parent.id, text=q))
                made.append(q)
        audit(s, "expand", {"question_id": question_id, "kind": finding_kind, "new": len(made)})
        s.commit()
        return made
    finally:
        s.close()


def search_queries(spec: dict) -> list[str]:
    """Queries for SearXNG / archive search, including the opposing side."""
    subj = spec["subject"]
    qs = []
    for h in spec["hypotheses"]:
        qs.append(f'"{subj}" {h[:80]}')
    for q in spec["questions"]:
        for rt in q["record_types"][:2]:
            qs.append(f'"{subj}" {rt}')
    qs += [f'"{subj}" primary sources', f'"{subj}" disputed', f'"{subj}" archive collection']
    return list(dict.fromkeys(qs))
