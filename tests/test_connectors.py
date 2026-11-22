"""Connectors, loader, fetch and API — all with mocked HTTP so tests never hit the network."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from bsi import db, fetch, loader
from bsi.connectors import REGISTRY, get, run_all


def mock(routes):
    def handler(req):
        for frag, resp in routes.items():
            if frag in str(req.url):
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


def test_one_failure_never_stops_the_run_and_alternates_are_offered():
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
                               client=mock({"archive.org": pdf}))
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
