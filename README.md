# serpentor
This is a find all the snakes in the den

## BAUBSA Sovereign Intelligence (BSI) — v0.2

An evidence-first research system for recovering the people history left out,
mis-credited, or redrawn. It runs on your own machine, on local models, and keeps
the original documents and a tamper-evident record of every step.

**The permanent rule:** no institution gets automatic credibility, no alternative
account gets automatic credibility, and no AI gets the final word. A claim's status
is computed from the original evidence linked to it, and anyone can re-run the
research and get the same result.

---

## Open it

```bash
pip install -r requirements.txt
cp .env.example .env            # add free archive keys when you have them
uvicorn bsi.api:app --port 8080
```
Then open **http://localhost:8080** — the command center:

| Page | What you do there |
|---|---|
| **Great Minds** | The claim board. Every person, every claim, its evidence status. Load the starter list or add people. |
| **Person** | Identity in their own terms · depiction timeline (lifetime vs. later portrayals) · claims with evidence for/against, gaps, research plan, sign-off |
| **Archives** | One query → every connected archive at once. Import any result straight into the vault. |
| **Evidence Vault** | Upload documents with tier/date/holder · exact-passage search · injection flags |
| **Discover** | Bulk-load thousands of research targets from Wikidata by group and occupation |
| **Audit** | Hash-chain integrity and saved research manifests |

## Archive connectors (v0.2)

| Connector | Holds | Key |
|---|---|---|
| `loc` | Library of Congress — manuscripts, photos, maps, papers | none |
| `chronicling_america` | US newspapers 1756–1963 (via loc.gov API since 2025) | none |
| `internet_archive` | Digitized books, pamphlets, almanacs — full text | none |
| `wikidata` | People, dates, identifiers (discovery only) | none |
| `commons` | Portraits, engravings, photos with metadata (for depiction timelines) | none |
| `gallica` | Bibliothèque nationale de France — French & colonial records | none |
| `openalex` | Peer-reviewed scholarship (the secondary tier) | optional |
| `nara` | US National Archives — military, Freedmen's Bureau, census, courts, land | `NARA_API_KEY` |
| `patents` | USPTO PatentSearch — inventors, assignees (1976+) | `PATENTSVIEW_API_KEY` |
| `smithsonian` | Smithsonian incl. NMAAHC collections | `DATA_GOV_API_KEY` |
| `europeana` | European museums & archives | `EUROPEANA_API_KEY` |
| `dpla` | US state archives, HBCU & university collections | `DPLA_API_KEY` |

When a source can't be reached, the run keeps going and lists **alternate routes** where the
same material is commonly also held. Some archives (LoC, Gallica) refuse cloud servers but
answer normally from a home connection — run BSI on your own machine.

Pre-1976 patents (Latimer, Woods, McCoy, Morgan): use the patent number at
`patents.google.com/patent/US<number>` and import the PDF into the vault.

## Modules

| Module | What it does |
|---|---|
| `vault.py` | Copies every source into a write-once vault named by SHA-256, extracts text **per page**, and writes a hash-chained audit log. Editing any past event breaks `verify`. |
| `registry.py` | The Great Minds registry: people, name variants, claims, evidence links. An evidence quote is refused unless it actually appears on the cited page. |
| `claims.py` | Computes each claim's status from evidence (see ladder below). Builds a **depiction timeline**: every portrayal of a person, oldest first, marked "made in lifetime" or "N years after death". Matches people deterministically and refuses to merge similar names whose dates conflict. |
| `db.IdentityTerm` | Keeps four kinds of identity apart and never converts one into another: **self-identified**, **community term**, **historical label** (verbatim, must cite its document), **government category** (must cite its document). |
| `compiler.py` | The research compiler: turns a claim into a hashed research spec with competing hypotheses, a stated negative result, and a question tree pointing at the records that would answer each question. Findings (a portrait, a patent, an insurance policy, a census entry) spawn follow-up questions automatically. |
| `search.py` | Private metasearch through your own SearXNG. Every run is saved as a manifest with a results hash. Queries always include the opposing side ("disputed"). |
| `llm.py` | Local synthesis through Ollama. Documents are fenced as data; any sentence without a valid `[P#]` page citation is stripped and reported. |
| `quarantine.py` | Flags text inside documents that tries to give an AI instructions. Flagged sources are kept — the attempt can be evidence. |
| `egress.py` | Material marked sensitive cannot be sent anywhere except local hosts. |

### Claim status ladder

| Status | Meaning |
|---|---|
| `unexamined` | No evidence linked yet |
| `documented` | A primary (contemporary) record supports it, nothing credible against it, and a human linked it |
| `supported_secondary` | Scholarship supports it; the primary record isn't in the vault yet — or only a model has linked it |
| `contested` | Credible evidence on both sides |
| `unsupported` | Only weak support, or nothing for it |
| `contradicted` | A primary record goes against it, nothing credible for it |

Every change sets `needs_review` until a person signs off.

---

## Run it

**Quick (no Docker, SQLite):**
```bash
pip install -r requirements.txt
python -m bsi.cli seed          # loads 15 research targets, all 'unexamined'
python -m bsi.cli board         # the claim board
python -m bsi.cli ingest scan.pdf --tier primary --title "Baptism register 1762" --year 1762
python -m bsi.cli find "exact phrase"     # → source, page, paragraph
python -m bsi.cli compile 1     # research spec + questions for claim 1
python -m bsi.cli verify        # audit chain check
uvicorn bsi.api:app --port 8080 # API + docs at http://localhost:8080/docs
pytest -q                       # 24 tests
```

**Full stack (Docker):**
```bash
export BSI_DB_PASSWORD=$(openssl rand -hex 16)
sed -i "s/REPLACE-WITH-RANDOM-64-CHARS/$(openssl rand -hex 32)/" searxng/settings.yml
docker compose up -d searxng postgres
# pull the model once while Ollama can reach the internet, then it runs offline:
docker compose --profile setup run --rm ollama-pull
docker compose up -d
curl -X POST localhost:8080/seed
```
Every port is bound to `127.0.0.1`. The API and Ollama sit on an internal-only
network; only SearXNG has an outbound route.

**Scanned documents:** `ocrmypdf --output-type pdfa scan.pdf scan.ocr.pdf`, then ingest
both — the scan as the original, the OCR copy as a derived source.

**Postgres backup:** `docker compose exec postgres pg_dump -U bsi bsi > backup.sql`
plus a copy of the `vault` volume. (SQLite: `vault.backup()`.)

### Adding Aleph
Aleph (OCCRP) is the next layer — cross-referencing, network graphs, FollowTheMoney
entities. It's a large stack, so run it separately from its own repository's
compose file after the core is stable, and sync people/companies using `Person.ftm_id`.
Check its resource footprint on your machine first.

---

## Workflow for one person

1. `POST /persons` (or seed) → `POST /claims` with the claim, its alternatives, and what would disprove it.
2. `POST /claims/{id}/compile` → the question tree and record types to hunt.
3. `POST /claims/{id}/search` → saved, re-runnable search manifest.
4. Get the records (archives, museums, patent office, parish registers, census) → `POST /sources` with the right **tier**.
5. `POST /claims/{id}/evidence` with page, exact quote, stance — **including evidence against**.
6. For depiction claims, give each portrayal a `depiction_descriptor` → `GET /persons/{id}/depictions`.
7. `POST /ask` for a cited local-model summary; `POST /claims/{id}/review` to sign off.

## API endpoints
`/sources` · `/sources/{id}/verify` · `/passages?q=` · `/audit/verify` · `/persons` ·
`/persons/match` · `/persons/{id}/depictions` · `/persons/{id}/identity` · `/claims` ·
`/claims/{id}` · `/claims/{id}/evidence` · `/claims/{id}/review` · `/claims/{id}/compile` ·
`/claims/{id}/search` · `/claims/{id}/questions` · `/questions/{id}/expand` · `/ask` · `/seed`

## Next builds
1. Bulk target loader (Wikidata + national biography indexes) to grow the queue into the thousands
2. Archive connectors: Library of Congress, Chronicling America, National Archives catalog, Gallica, Europeana, USPTO full-text, FamilySearch (with account)
3. Image evidence: store portraits with conservation/provenance metadata, side-by-side depiction viewer
4. Aleph/FollowTheMoney sync for corporate and institutional succession cases
5. Command-center UI (React) over this API
