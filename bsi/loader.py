"""Bulk loader: grows the research queue from thousands to tens of thousands of people.

Wikidata is a DISCOVERY source, not an authority. What it says about a person's
ethnicity is an editor's label, so it is stored only as a provenance tag on the
target — never as an IdentityTerm, and never as a claim's evidence.
"""
import re
import threading

from . import claims, db
from .connectors import get
from .vault import audit

# Occupations (Wikidata QIDs). P106/P279* includes sub-occupations.
OCCUPATIONS = {
    "inventor": "Q205375", "scientist": "Q901", "mathematician": "Q170790",
    "engineer": "Q81096", "physician": "Q39631", "astronomer": "Q11063",
    "writer": "Q36180", "composer": "Q36834", "architect": "Q42973",
    "philosopher": "Q4964182", "historian": "Q201788", "military_officer": "Q189290",
    "chemist": "Q593644", "explorer": "Q11900058", "educator": "Q1231865",
    "artist": "Q483501", "politician": "Q82955", "religious_leader": "Q15995642",
}

# Discovery groupings. Each is a Wikidata pattern (an editor-assigned property),
# used only to find people to research.
GROUPS = {
    "african_american": "?p wdt:P172 wd:Q49085 .",                                # ethnic group
    "african_diaspora_europe": "?p wdt:P172 ?eg . VALUES ?eg { wd:Q2914431 wd:Q1062349 wd:Q2915145 } .",
    "born_in_africa": "?p wdt:P19/wdt:P17 ?c . ?c wdt:P30 wd:Q15 .",               # birthplace in Africa
    "native_american": "?p wdt:P172 ?eg . ?eg wdt:P31/wdt:P279* wd:Q1336006 .",   # Indigenous peoples of the Americas
    "moorish_andalusia": "?p wdt:P27 wd:Q194318 .",                                # citizenship: Al-Andalus
}


def build_query(group: str, occupation: str | None, limit: int, offset: int) -> str:
    occ = f"?p wdt:P106/wdt:P279* wd:{OCCUPATIONS[occupation]} ." if occupation else "?p wdt:P106 ?anyocc ."
    return f"""
SELECT DISTINCT ?p ?pLabel ?pDescription ?b ?d ?occLabel WHERE {{
  ?p wdt:P31 wd:Q5 .
  {GROUPS[group]}
  {occ}
  OPTIONAL {{ ?p wdt:P569 ?b }}
  OPTIONAL {{ ?p wdt:P570 ?d }}
  OPTIONAL {{ ?p wdt:P106 ?occ }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} ORDER BY ?p LIMIT {limit} OFFSET {offset}"""


def _year(v):
    if not v:
        return None
    m = re.match(r"(-?)(\d{1,4})-", v.lstrip("+"))
    return int(m.group(1) + m.group(2)) if m else None


_LOAD_LOCK = threading.Lock()   # QID check and insert happen as one step per process


def load_wikidata(group: str, occupation: str | None = None, *, limit: int = 500,
                  offset: int = 0, client=None) -> dict:
    if group not in GROUPS:
        raise ValueError(f"group must be one of {sorted(GROUPS)}")
    if occupation and occupation not in OCCUPATIONS:
        raise ValueError(f"occupation must be one of {sorted(OCCUPATIONS)}")
    wd = get("wikidata", client=client)
    data = wd.sparql(build_query(group, occupation, limit, offset))
    people = {}
    for b in data["results"]["bindings"]:
        qid = b["p"]["value"].rsplit("/", 1)[-1]
        p = people.setdefault(qid, {"qid": qid, "name": b.get("pLabel", {}).get("value", qid),
                                    "desc": b.get("pDescription", {}).get("value"),
                                    "born": _year(b.get("b", {}).get("value")),
                                    "died": _year(b.get("d", {}).get("value")), "fields": set()})
        if b.get("occLabel"):
            p["fields"].add(b["occLabel"]["value"])

    with _LOAD_LOCK:
        return _store(people, group, occupation, offset, limit, data)


def _store(people, group, occupation, offset, limit, data):
    s = db.session()
    added = skipped = flagged = 0
    try:
        db.serialize_writes(s)          # QID check + insert as one serialized write, across processes
        known = {}
        for pr in s.query(db.Person):
            q = (pr.external_ids or {}).get("wikidata")
            if q:
                known[q] = pr
        cands = claims.load_candidates(s)       # one load per batch, not per person
        tag = f"wikidata:{group}" + (f":{occupation}" if occupation else "")
        for p in people.values():
            if p["name"] == p["qid"]:          # no English label — skip, can't research by name yet
                skipped += 1
                continue
            if p["qid"] in known:
                pr = known[p["qid"]]
                pr.tags = sorted(set(pr.tags or []) | {tag})
                pr.fields = sorted(set(pr.fields or []) | p["fields"])[:12]   # occupations from later pages
                skipped += 1
                continue
            # same name already present without a QID? keep both, flag for human merge decision
            dup = [m for m in claims.match_against(cands, p["name"], p["born"], p["died"])
                   if m["verdict"] in ("same", "possible")]
            pr = db.Person(display_name=p["name"], birth_year=p["born"], death_year=p["died"],
                           fields=sorted(p["fields"])[:8], external_ids={"wikidata": p["qid"]},
                           tags=[tag] + (["possible_duplicate"] if dup else []),
                           notes=p["desc"])
            s.add(pr)
            known[p["qid"]] = pr                # same QID later in this batch is a duplicate
            cands.append({"id": None, "name": p["name"], "born": p["born"], "died": p["died"],
                          "norms": [claims._norm(p["name"])]})   # same-name people within this batch
            added += 1
            flagged += bool(dup)
        audit(s, "loader.wikidata", {"group": group, "occupation": occupation, "offset": offset,
                                     "returned": len(people), "added": added,
                                     "skipped": skipped, "possible_duplicates": flagged})
        s.commit()
    finally:
        s.close()
    return {"group": group, "occupation": occupation, "returned": len(people), "added": added,
            "skipped": skipped, "possible_duplicates": flagged,
            "next_offset": offset + limit if len(data["results"]["bindings"]) >= limit else None}
