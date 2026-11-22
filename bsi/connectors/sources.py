"""Connector implementations. Endpoints verified against official docs, Nov 2026."""
import re
import xml.etree.ElementTree as ET

from .base import Connector, Record, register


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


# ---------------------------------------------------------------- Library of Congress
@register
class LibraryOfCongress(Connector):
    name, label, kind = "loc", "Library of Congress", "archive"
    holder = "Library of Congress"
    docs = "https://www.loc.gov/apis/json-and-yaml/"
    base = "https://www.loc.gov/search/"

    def params(self, query, limit):
        return {"q": query, "fo": "json", "c": limit, "at": "results,pagination"}

    def search(self, query, limit=10):
        d = self.get(self.base, params=self.params(query, limit)).json()
        out = []
        for r in d.get("results", []):
            out.append(Record(connector=self.name, title=r.get("title") or "(untitled)",
                              url=r.get("url") or r.get("id"), identifier=r.get("id"),
                              date=r.get("date"), snippet=_first(r.get("description")),
                              media_type=_first(r.get("original_format")), holder=self.holder,
                              extra={"partof": r.get("partof"), "image_url": _first(r.get("image_url"))}))
        return out


@register
class ChroniclingAmerica(LibraryOfCongress):
    """Historic US newspapers 1756-1963 — now served only through the loc.gov API."""
    name, label, kind = "chronicling_america", "Chronicling America (newspapers)", "newspapers"
    base = "https://www.loc.gov/collections/chronicling-america/"
    docs = "https://libraryofcongress.github.io/data-exploration/loc.gov%20JSON%20API/Chronicling_America/README.html"

    def params(self, query, limit):
        return {"q": query, "fo": "json", "c": limit, "dl": "page", "at": "results,pagination"}


# ---------------------------------------------------------------- Internet Archive
@register
class InternetArchive(Connector):
    name, label, kind = "internet_archive", "Internet Archive", "archive"
    holder = "Internet Archive"
    docs = "https://archive.org/advancedsearch.php"

    def search(self, query, limit=10):
        d = self.get("https://archive.org/advancedsearch.php", params={
            "q": query, "fl[]": ["identifier", "title", "date", "mediatype", "creator", "description"],
            "rows": limit, "output": "json"}).json()
        out = []
        for doc in d.get("response", {}).get("docs", []):
            ident = doc["identifier"]
            text = f"https://archive.org/download/{ident}/{ident}_djvu.txt" if doc.get("mediatype") == "texts" else None
            out.append(Record(connector=self.name, title=_first(doc.get("title")) or ident,
                              url=f"https://archive.org/details/{ident}", identifier=ident,
                              date=doc.get("date"), snippet=(_first(doc.get("description")) or "")[:400],
                              media_type=doc.get("mediatype"), holder=self.holder, download_url=text,
                              extra={"creator": doc.get("creator")}))
        return out


# ---------------------------------------------------------------- Wikidata
@register
class Wikidata(Connector):
    name, label, kind = "wikidata", "Wikidata", "knowledge-graph"
    holder = "Wikimedia Foundation"
    docs = "https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service"
    endpoint = "https://query.wikidata.org/sparql"

    def sparql(self, q):
        return self.get(self.endpoint, params={"query": q, "format": "json"},
                        headers={"Accept": "application/sparql-results+json"}).json()

    def search(self, query, limit=10):
        d = self.get("https://www.wikidata.org/w/api.php", params={
            "action": "wbsearchentities", "search": query, "language": "en",
            "limit": limit, "format": "json"}).json()
        return [Record(connector=self.name, title=e.get("label") or e["id"], identifier=e["id"],
                       url=f"https://www.wikidata.org/wiki/{e['id']}", snippet=e.get("description"),
                       holder=self.holder) for e in d.get("search", [])]


# ---------------------------------------------------------------- Wikimedia Commons (depictions)
@register
class WikimediaCommons(Connector):
    """Portraits, engravings, photographs — with upload metadata for depiction timelines."""
    name, label, kind = "commons", "Wikimedia Commons (images)", "images"
    holder = "Wikimedia Commons (originals held by the institutions credited per file)"
    docs = "https://commons.wikimedia.org/w/api.php"

    def search(self, query, limit=10):
        d = self.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "generator": "search", "gsrsearch": query, "gsrnamespace": 6,
            "gsrlimit": limit, "prop": "imageinfo", "iiprop": "url|extmetadata", "format": "json"}).json()
        out = []
        for p in (d.get("query", {}).get("pages", {}) or {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            md = ii.get("extmetadata", {})
            val = lambda k: re.sub(r"<[^>]+>", "", (md.get(k) or {}).get("value", "") or "")[:300] or None  # noqa: E731
            out.append(Record(connector=self.name, title=p.get("title"), url=ii.get("descriptionurl"),
                              identifier=p.get("title"), date=val("DateTimeOriginal"),
                              snippet=val("ImageDescription"), media_type="image",
                              holder=val("Credit") or self.holder, rights=val("LicenseShortName"),
                              download_url=ii.get("url"), extra={"artist": val("Artist")}))
        return out


# ---------------------------------------------------------------- Gallica (BnF)
@register
class Gallica(Connector):
    """French national library — key for Dumas, Saint-Georges, colonial records."""
    name, label, kind = "gallica", "Gallica (Bibliothèque nationale de France)", "archive"
    holder = "Bibliothèque nationale de France"
    docs = "https://api.bnf.fr/fr/api-gallica-de-recherche"
    NS = {"srw": "http://www.loc.gov/zing/srw/", "dc": "http://purl.org/dc/elements/1.1/"}

    def search(self, query, limit=10):
        q = f'gallica all "{query}"'
        r = self.get("https://gallica.bnf.fr/SRU", params={
            "version": "1.2", "operation": "searchRetrieve", "query": q, "maximumRecords": limit})
        root = ET.fromstring(r.content)
        out = []
        for rec in root.iter("{http://www.loc.gov/zing/srw/}record"):
            g = lambda t: [e.text for e in rec.iter(f"{{http://purl.org/dc/elements/1.1/}}{t}") if e.text]  # noqa: E731
            ids = g("identifier")
            url = next((i for i in ids if "gallica.bnf.fr/ark:" in i), _first(ids))
            out.append(Record(connector=self.name, title=_first(g("title")) or "(sans titre)",
                              url=url, identifier=url, date=_first(g("date")),
                              snippet=_first(g("description")), media_type=_first(g("type")),
                              holder=self.holder, rights=_first(g("rights")),
                              extra={"creator": g("creator")}))
        return out


# ---------------------------------------------------------------- OpenAlex (scholarship)
@register
class OpenAlex(Connector):
    """Peer-reviewed scholarship — the 'secondary' tier, for and against."""
    name, label, kind = "openalex", "OpenAlex (scholarship)", "scholarship"
    holder = "OpenAlex"
    docs = "https://docs.openalex.org/"

    def search(self, query, limit=10):
        import os
        params = {"search": query, "per-page": limit}
        if os.environ.get("OPENALEX_API_KEY"):
            params["api_key"] = os.environ["OPENALEX_API_KEY"]
        d = self.get("https://api.openalex.org/works", params=params).json()
        out = []
        for w in d.get("results", []):
            oa = w.get("open_access") or {}
            out.append(Record(connector=self.name, title=w.get("display_name") or "(untitled)",
                              url=w.get("doi") or w.get("id"), identifier=w.get("id"),
                              date=str(w.get("publication_year") or ""), media_type=w.get("type"),
                              holder=((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                              download_url=oa.get("oa_url"),
                              extra={"cited_by": w.get("cited_by_count"),
                                     "authors": [a["author"]["display_name"] for a in w.get("authorships", [])][:6]}))
        return out


# ---------------------------------------------------------------- keyed sources
@register
class NationalArchives(Connector):
    """US National Archives catalog — military, Freedmen's Bureau, census, court, land records."""
    name, label, kind = "nara", "US National Archives Catalog", "archive"
    holder = "US National Archives and Records Administration"
    key_env = "NARA_API_KEY"     # free: email Catalog_API@nara.gov
    docs = "https://www.archives.gov/research/catalog/help/api"

    def search(self, query, limit=10):
        d = self.get("https://catalog.archives.gov/api/v2/records/search",
                     params={"q": query, "limit": limit},
                     headers={"x-api-key": self.key, "Content-Type": "application/json"}).json()
        hits = d.get("body", {}).get("hits", {}).get("hits", [])
        out = []
        for h in hits:
            rec = (h.get("_source") or {}).get("record", {})
            naid = rec.get("naId") or h.get("_id")
            out.append(Record(connector=self.name, title=rec.get("title") or "(untitled)",
                              url=f"https://catalog.archives.gov/id/{naid}", identifier=str(naid),
                              date=(rec.get("productionDates") or [{}])[0].get("logicalDate")
                              if rec.get("productionDates") else None,
                              snippet=(rec.get("scopeAndContentNote") or "")[:400],
                              media_type=_first(rec.get("generalRecordsTypes")), holder=self.holder,
                              extra={"level": rec.get("levelOfDescription")}))
        return out


@register
class PatentSearch(Connector):
    """USPTO PatentSearch (PatentsView). Inventor priority and assignment — who got paid."""
    name, label, kind = "patents", "USPTO PatentSearch", "patents"
    holder = "United States Patent and Trademark Office"
    key_env = "PATENTSVIEW_API_KEY"
    docs = "https://search.patentsview.org/docs/docs/Search%20API/SearchAPIReference"

    def search(self, query, limit=10):
        parts = query.split()
        q = ({"_and": [{"inventors.inventor_name_first": parts[0]},
                       {"inventors.inventor_name_last": parts[-1]}]}
             if len(parts) >= 2 and not query.startswith("text:") else
             {"_text_any": {"patent_title": query.removeprefix("text:")}})
        d = self.post("https://search.patentsview.org/api/v1/patent/",
                      headers={"X-Api-Key": self.key},
                      json={"q": q, "f": ["patent_id", "patent_title", "patent_date", "inventors",
                                          "assignees"], "o": {"size": limit}}).json()
        out = []
        for p in d.get("patents", []):
            pid = p.get("patent_id")
            out.append(Record(connector=self.name, title=p.get("patent_title") or pid,
                              url=f"https://patents.google.com/patent/US{pid}", identifier=pid,
                              date=p.get("patent_date"), media_type="patent", holder=self.holder,
                              extra={"inventors": p.get("inventors"), "assignees": p.get("assignees")}))
        return out
    # Note: PatentSearch coverage starts 1976. Pre-1976 patents (Latimer, Woods, McCoy,
    # Morgan): use Google Patents (patents.google.com/patent/US<number>) and USPTO full-text
    # images, found via the internet_archive / loc connectors or the patent numbers.


@register
class Smithsonian(Connector):
    name, label, kind = "smithsonian", "Smithsonian Open Access", "museum"
    holder = "Smithsonian Institution (incl. NMAAHC)"
    key_env = "DATA_GOV_API_KEY"   # free at api.data.gov/signup
    docs = "https://edan.si.edu/openaccess/apidocs/"

    def search(self, query, limit=10):
        d = self.get("https://api.si.edu/openaccess/api/v1.0/search",
                     params={"q": query, "rows": limit, "api_key": self.key}).json()
        out = []
        for r in d.get("response", {}).get("rows", []):
            c = r.get("content", {})
            dnr = c.get("descriptiveNonRepeating", {})
            media = (dnr.get("online_media") or {}).get("media") or [{}]
            out.append(Record(connector=self.name, title=r.get("title") or "(untitled)",
                              url=dnr.get("record_link"), identifier=r.get("id"),
                              date=_first((c.get("indexedStructured") or {}).get("date")),
                              media_type=r.get("type"), holder=dnr.get("data_source") or self.holder,
                              download_url=media[0].get("content"), rights=media[0].get("usage", {}).get("access")
                              if isinstance(media[0].get("usage"), dict) else None))
        return out


@register
class Europeana(Connector):
    name, label, kind = "europeana", "Europeana", "museum"
    holder = "Europeana (aggregating European museums & archives)"
    key_env = "EUROPEANA_API_KEY"
    docs = "https://pro.europeana.eu/page/search"

    def search(self, query, limit=10):
        d = self.get("https://api.europeana.eu/record/v2/search.json",
                     params={"query": query, "rows": limit}, headers={"X-Api-Key": self.key}).json()
        return [Record(connector=self.name, title=_first(i.get("title")) or "(untitled)",
                       url=i.get("guid"), identifier=i.get("id"), date=_first(i.get("year")),
                       snippet=_first(i.get("dcDescription")), media_type=i.get("type"),
                       holder=_first(i.get("dataProvider")), rights=_first(i.get("rights")),
                       download_url=_first(i.get("edmIsShownBy")))
                for i in d.get("items", [])]


@register
class DPLA(Connector):
    """Digital Public Library of America — aggregates US state archives, HBCU and university collections."""
    name, label, kind = "dpla", "Digital Public Library of America", "archive"
    holder = "DPLA (aggregating US libraries & archives)"
    key_env = "DPLA_API_KEY"
    docs = "https://pro.dp.la/developers/api-codex"

    def search(self, query, limit=10):
        d = self.get("https://api.dp.la/v2/items",
                     params={"q": query, "page_size": limit, "api_key": self.key}).json()
        out = []
        for doc in d.get("docs", []):
            sr = doc.get("sourceResource", {})
            out.append(Record(connector=self.name, title=_first(sr.get("title")) or "(untitled)",
                              url=doc.get("isShownAt"), identifier=doc.get("id"),
                              date=_first([x.get("displayDate") for x in (sr.get("date") or [])
                                           if isinstance(x, dict)]),
                              snippet=_first(sr.get("description")), media_type=_first(sr.get("type")),
                              holder=(doc.get("dataProvider") or {}).get("name")
                              if isinstance(doc.get("dataProvider"), dict) else doc.get("dataProvider"),
                              rights=_first(doc.get("rights"))))
        return out
