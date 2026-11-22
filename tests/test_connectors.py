"""Connectors, loader, fetch and API — all with mocked HTTP so tests never hit the network."""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from bsi import db, fetch, loader
from bsi.connectors import REGISTRY, get, run_all


def mock(routes):
    def handler(req):
        for frag, resp in routes.items():
            if frag in str(req.url) or frag in req.headers.get("host", ""):
                if isinstance(resp, int):
                    return httpx.Response(resp)
                if isinstance(resp, bytes):
                    return httpx.Response(200, content=resp, headers={"content-type": "application/pdf"})
                if isinstance(resp, str):
                    return httpx.Response(200, text=resp)
                return httpx.Response(200, json=resp)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


IA = {"response": {"numFound": 1, "docs": [{"identifier": "bannekeralmanac1792", "title": "Banneker's Almanack",
                                            "date": "1792-01-01T00:00:00Z", "mediatype": "texts"}]}}
LOC = {"results": [{"title": "Lewis H. Latimer papers", "id": "https://www.loc.gov/item/x/", "url": "https://www.loc.gov/item/x/",
                    "date": "1880", "original_format": ["manuscript/mixed material"], "description": ["Papers"]}]}
GALLICA = """<?xml version="1.0"?><srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">
<srw:records><srw:record><srw:recordData><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Mes mémoires</dc:title><dc:date>1852</dc:date><dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6k1</dc:identifier>
</oai_dc:dc></srw:recordData></srw:record></srw:records></srw:searchRetrieveResponse>"""


def test_parsers():
    c = mock({"archive.org": IA, "loc.gov": LOC, "gallica": GALLICA})
    ia = get("internet_archive", client=c).search("banneker", 5)
    assert ia[0].download_url.endswith("bannekeralmanac1792_djvu.txt")
    assert get("loc", client=c).search("latimer", 5)[0].title == "Lewis H. Latimer papers"
    g = get("gallica", client=c).search("dumas", 5)[0]
    assert g.title == "Mes mémoires" and g.date == "1852" and "ark:" in g.url


def test_one_failure_never_stops_the_run_and_alternates_are_offered(monkeypatch):
    monkeypatch.delenv("NARA_API_KEY", raising=False)
    c = mock({"archive.org": IA, "loc.gov": 503, "gallica": 403})
    r = run_all("dumas", names=["loc", "gallica", "internet_archive", "nara"], client=c)
    st = {x["connector"]: x["status"] for x in r["runs"]}
    assert st == {"loc": "error", "gallica": "error", "internet_archive": "ok", "nara": "needs_key"}
    assert "internet_archive" not in r["unreached"] and "europeana" in r["alternate_routes"]
    assert r["manifest_id"]


def test_every_connector_reports_info():
    for n in REGISTRY:
        i = get(n).info()
        assert i["name"] == n and "ready" in i


WD = {"results": {"bindings": [
    {"p": {"value": "http://www.wikidata.org/entity/Q1"}, "pLabel": {"value": "Ada Example"},
     "b": {"value": "1850-01-01T00:00:00Z"}, "d": {"value": "1910-01-01T00:00:00Z"}, "occLabel": {"value": "inventor"}},
    {"p": {"value": "http://www.wikidata.org/entity/Q1"}, "pLabel": {"value": "Ada Example"},
     "occLabel": {"value": "engineer"}},
    {"p": {"value": "http://www.wikidata.org/entity/Q2"}, "pLabel": {"value": "Q2"}},
]}}


def test_loader_dedupes_and_never_writes_identity():
    c = mock({"query.wikidata.org": WD})
    r = loader.load_wikidata("african_american", "inventor", limit=10, client=c)
    assert r["added"] == 1 and r["skipped"] == 1        # Q2 has no label
    r2 = loader.load_wikidata("african_american", "inventor", limit=10, client=c)
    assert r2["added"] == 0                              # QID already known
    s = db.session()
    p = s.query(db.Person).filter_by(display_name="Ada Example").one()
    assert p.birth_year == 1850 and set(p.fields) == {"inventor", "engineer"}
    assert p.identity_terms == [] and p.external_ids["wikidata"] == "Q1"
    s.close()
    with pytest.raises(ValueError):
        loader.load_wikidata("not_a_group")


def test_bce_years():
    assert loader._year("-0069-01-01T00:00:00Z") == -69


def test_fetch_to_vault(make_pdf):
    pdf = make_pdf("dl.pdf", ["Downloaded record page one"]).read_bytes()
    src = fetch.fetch_to_vault("https://archive.org/download/x/x.pdf", tier="primary",
                               client=mock({"archive.org": pdf}), resolve=PUBLIC)
    assert src.origin_url.endswith("x.pdf") and src.tier == "primary"


def test_api_and_ui():
    from bsi.api import app
    with TestClient(app) as c:
        assert "BAUBSA Sovereign Intelligence" in c.get("/").text
        c.post("/seed")
        ps = c.get("/persons?q=Dumas").json()
        assert ps["total"] >= 1
        pid = ps["items"][0]["id"]
        d = c.get(f"/persons/{pid}").json()
        assert d["claims"] and "depictions" in d
        assert c.get("/persons/match?name=Alexandre%20Dumas").status_code == 200
        assert c.get("/stats").json()["audit"]["ok"]
        assert len(c.get("/connectors").json()) == len(REGISTRY)
        assert "groups" in c.get("/loader/options").json()


# ---------------------------------------------------------------- review fixes (PR #1)
PUBLIC = lambda host, port: [(None, None, None, "", ("93.184.216.34", port))]   # noqa: E731
PRIVATE = {"localhost": "127.0.0.1", "intranet": "10.0.0.5", "meta": "169.254.169.254"}


def resolver(host, port):
    return [(None, None, None, "", (PRIVATE.get(host, "93.184.216.34"), port))]


def test_import_refuses_local_and_private_addresses():
    from bsi import egress
    for url in ["http://localhost:8080/sources", "http://intranet/admin", "http://meta/latest",
                "file:///etc/passwd", "ftp://archive.org/x"]:
        with pytest.raises(egress.EgressDenied):
            egress.check_public(url, resolve=resolver)
    egress.check_public("https://archive.org/x.pdf", resolve=resolver)


def test_import_rechecks_every_redirect():
    from bsi import egress

    def handler(req):
        if req.headers.get("host") == "archive.org":
            return httpx.Response(302, headers={"location": "http://localhost:8080/stats"})
        return httpx.Response(200, text="internal")
    with pytest.raises(egress.EgressDenied):
        fetch.fetch_to_vault("https://archive.org/redirect", resolve=resolver,
                             client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_content_type_beats_misleading_suffix(make_pdf):
    pdf = make_pdf("ct.pdf", ["Served from a php script"]).read_bytes()
    src = fetch.fetch_to_vault("https://archive.org/download.php?id=9", resolve=PUBLIC,
                               client=mock({"archive.org": pdf}))
    s = db.session()
    assert "php script" in s.get(db.Source, src.id).pages[0].text
    s.close()


def test_errors_never_carry_api_keys(monkeypatch):
    monkeypatch.setenv("DATA_GOV_API_KEY", "SECRETKEY123")
    c = mock({"api.si.edu": 403})
    r = run_all("dumas", names=["smithsonian"], client=c)
    assert "SECRETKEY123" not in json.dumps(r)
    assert r["runs"][0]["error"] == "HTTP 403"


def test_documented_needs_human_primary_support(make_pdf):
    from bsi import claims, registry, vault
    p = registry.add_person("Review Case A")
    c = registry.add_claim(p.id, "contribution", "Built Y.")
    prim = vault.register(make_pdf("rp.pdf", ["record of Y built by A"]), tier="primary")
    tert = vault.register(make_pdf("rt.pdf", ["a later story about Y"]), tier="tertiary")
    registry.link_evidence(c.id, prim.id, "supports", added_by="model:x")
    registry.link_evidence(c.id, tert.id, "context")             # a human's unrelated link
    s = db.session()
    assert claims.compute_status(s.get(db.Claim, c.id))["status"] == "supported_secondary"
    s.close()


def test_quote_checked_even_without_page(make_pdf):
    from bsi import registry, vault
    p = registry.add_person("Review Case B")
    c = registry.add_claim(p.id, "biographical", "x")
    src = vault.register(make_pdf("rq.pdf", ["first page", "the real words"]), tier="primary")
    with pytest.raises(ValueError):
        registry.link_evidence(c.id, src.id, "supports", quote="invented words")
    ev = registry.link_evidence(c.id, src.id, "supports", quote="the real words")
    assert ev.page_no == 2                                         # page filled in from the match


def test_citation_cannot_carry_into_next_sentence():
    from bsi import llm
    v = llm.validate("He was born in 1802. [P3] He liked tea.", {"P3"})
    assert "born in 1802" in v["grounded"] and "liked tea" not in v["grounded"]
    assert v["uncited_or_invalid"] == ["He liked tea."]


def test_sensitivity_upgrade_on_duplicate(make_pdf):
    from bsi import vault
    path = make_pdf("sens.pdf", ["family record"])
    a = vault.register(path, sensitive=False)
    b = vault.register(path, sensitive=True)
    c2 = vault.register(path, sensitive=False)                    # never downgrades
    s = db.session()
    assert a.id == b.id == c2.id and s.get(db.Source, a.id).sensitive
    s.close()


def test_seed_resumes_partial_target():
    from bsi import registry
    registry.load_seed()
    s = db.session()
    d = s.query(db.Person).filter_by(display_name="Alexandre Dumas").one()
    victim = d.claims[-1]
    s.delete(victim); s.commit(); s.close()
    registry.load_seed()
    s = db.session()
    d = s.query(db.Person).filter_by(display_name="Alexandre Dumas").one()
    assert len(d.claims) == 2 and s.query(db.Person).filter_by(display_name="Alexandre Dumas").count() == 1
    s.close()


def test_audit_chain_cannot_fork():
    from sqlalchemy.exc import IntegrityError
    from bsi import vault
    s = db.session()
    vault.audit(s, "test.setup", {"for": "fork test"})
    s.commit()
    head = s.query(db.AuditEvent).order_by(db.AuditEvent.id.desc()).first()
    s.add(db.AuditEvent(at="x", action="fork", payload={}, prev_hash=head.prev_hash, hash="f" * 64))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback(); s.close()


def test_ui_never_builds_handlers_from_names():
    from pathlib import Path
    html = (Path(__file__).parents[1] / "bsi/static/index.html").read_text()
    assert "JSON.stringify(p.name)" not in html


def test_import_connects_to_the_validated_address(make_pdf):
    """DNS rebinding: a second lookup can't redirect the connection — we connect to the checked IP."""
    pdf = make_pdf("pin.pdf", ["pinned download"]).read_bytes()
    seen = {}
    answers = iter(["93.184.216.34", "127.0.0.1"])        # rebinding attempt on the 2nd lookup

    def rebinding(host, port):
        return [(None, None, None, "", (next(answers), port))]

    def handler(req):
        seen.update(host=req.url.host, header=req.headers.get("host"), sni=req.extensions.get("sni_hostname"))
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
    fetch.fetch_to_vault("https://archive.org/x.pdf", resolve=rebinding,
                         client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen == {"host": "93.184.216.34", "header": "archive.org", "sni": "archive.org"}


def test_audit_chain_serializes_across_processes(tmp_path):
    """Two separate processes appending at once must still produce one unbroken chain."""
    import subprocess
    import sys
    dbfile = tmp_path / "race.db"
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from bsi import db, vault\n"
            "db.init('sqlite:///%s')\n"
            "for i in range(25):\n"
            "    s = db.session(); vault.audit(s, 'race', {'i': i}); s.commit(); s.close()\n"
            % (str(Path(__file__).parents[1]), dbfile))
    subprocess.run([sys.executable, "-c", "from bsi import db; db.init('sqlite:///%s')" % dbfile],
                   check=True, cwd=str(Path(__file__).parents[1]))
    procs = [subprocess.Popen([sys.executable, "-c", code], cwd=str(Path(__file__).parents[1])) for _ in range(3)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    with Session(create_engine(f"sqlite:///{dbfile}")) as s:
        from bsi import vault
        res = vault.verify_audit(s)
    assert res["ok"] and res["checked"] == 75


def test_review_round_two_fixes():
    from bsi import claims, llm, vault
    # citations never jump a paragraph break
    v = llm.validate("Unsupported claim.\n\n[P1] Supported claim.", {"P1"})
    assert "Unsupported" not in v["grounded"]
    # abbreviations don't cut a sentence in half and flip its meaning
    v = llm.validate("The witness denied that Mr. Smith killed Jones [P1].", {"P1"})
    assert v["grounded"].startswith("The witness denied that Mr. Smith")
    # names keep their letters
    assert claims._norm("José Martí") == "josé martí"
    # 'same' needs an agreeing date, not merely a missing one
    registry_name = "Datelesss Person Q"
    from bsi import registry
    registry.add_person(registry_name)
    top = claims.match_person(registry_name, birth_year=1850)[0]
    assert top["verdict"] == "possible"
    # LIKE wildcards are literal in exact-phrase search
    assert all("100%" in h["paragraph"] for h in vault.find_passage("100%"))
    # unknown connectors are rejected up front
    import pytest as _pt
    with _pt.raises(ValueError):
        run_all("x", names=["nope"], save=False)
    # missing originals verify as failed, not crash
    assert vault.verify_source(999999)["ok"] is False
