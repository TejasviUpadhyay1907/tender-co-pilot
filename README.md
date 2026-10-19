# Tender Co-Pilot — Real System

An AI multi-agent co-pilot for Indian MSME bidders: it watches public tender
portals, reads bid documents with RAG, checks eligibility line-by-line against
a company profile, and tracks deadlines. Built from the research in
`../research_notes/indian-tender-system-ai-agent-research-20261019-0956/`.

> **Prototype honesty:** this repo ships with a **snapshot of 10 real tenders
> captured 19-Oct-2026** (`seed/seed_tenders.json`). The CPPP fetcher pulls
> live public listings, but portal HTML changes and bot protections mean live
> ingestion is best-effort — the app **never invents tender data** and fails
> loudly instead.

## Quickstart (Docker)

```bash
cp .env.example .env        # fill in values (LLM key optional — see below)
docker compose up --build
# open http://localhost:8000
```

The backend serves the web UI at `/` and the REST API at `/api`.

## Run locally (no Docker)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

On first start the app creates `data/tendercopilot.db` (SQLite) and imports
the seed tenders. Set `DISABLE_SCHEDULER=1` to skip background jobs.

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/tendercopilot.db` | Use `postgresql+psycopg2://…` for Postgres (see compose comments) |
| `LLM_API_KEY` | empty | **Optional.** Without it the agents use deterministic rule/regex fallbacks — the whole pipeline still runs. |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `LLM_MODEL` | `gpt-4o-mini` | Used for extraction/drafting only |
| `CHROMA_DIR` | `./data/chroma` | Vector store; falls back to in-memory if chromadb missing |
| `EMBEDDINGS_MODEL` | `all-MiniLM-L6-v2` | Falls back to TF-IDF if sentence-transformers missing |
| `INGEST_INTERVAL_HOURS` | `6` | Scheduled portal re-scan |
| `SCAN_KEYWORDS` | `works,supply,services` | Keywords for scheduled scans |
| `SEED_PATH` | `./seed/seed_tenders.json` | Imported once when the DB is empty |

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  CPPP / GeM │────▶│  Ingestion layer │────▶│   SQLite (tenders│
│  public     │     │  cppp.py: polite │     │   profiles, runs,│
│  listings   │     │  fetcher, robots │     │   verdicts,      │
└─────────────┘     │  check, retries, │     │   deadlines)     │
                    │  PDF download    │     └────────┬─────────┘
┌─────────────┐     └──────────────────┘              │
│ Tender PDFs │────▶┌──────────────────┐              ▼
│ (upload or  │     │ Document pipeline│     ┌──────────────────┐
│  auto-fetch)│     │ pypdf extract →  │────▶│  RAG core        │
└─────────────┘     │ section-aware    │     │  ChromaDB +      │
                    │ chunking (every  │     │  embeddings;     │
                    │ chunk: section+  │     │  citation-       │
                    │ page)            │     │  grounded        │
                    └──────────────────┘     │  extraction      │
                                             └────────┬─────────┘
                                                      ▼
                    ┌──────────────────────────────────────────────┐
                    │ Orchestrated agents (deterministic first,    │
                    │ LLM only for language understanding)         │
                    │ Scanner → Normaliser → Matcher → DocIntel →  │
                    │ EligibilityChecker → DeadlineTracker (+BidPack)│
                    └──────────────────────────────────────────────┘
                                                      ▼
                    ┌──────────────────────────────────────────────┐
                    │ Rules engine (pure functions, no LLM):       │
                    │ EMD calc + GFR-170 exemptions, 3% perf.      │
                    │ security, Make-in-India classes, MSE L1+15%, │
                    │ two-bid flow, deadline urgency, GO/CAUTION/  │
                    │ NO-GO aggregation                            │
                    └──────────────────────────────────────────────┘
```

**LLM usage policy:** deterministic rules wherever possible (money/date parsing,
threshold comparisons, scoring). The LLM is used only for extraction phrasing
and drafting, always constrained to retrieved text, with every fact carrying a
section/page citation. **No API key → full deterministic fallback.**

## The 6 agents

| Agent | Module | What it does |
|---|---|---|
| Scanner | `agents/scanner.py` | Fetches public listings per keyword; strict `looks_like_tender` gate rejects nav junk; dedupes |
| Normaliser | `agents/normaliser.py` | Fills missing category/EMD-type; never overwrites known values |
| Matcher | `agents/matcher.py` | Deterministic 0–100 score: sector match, turnover vs estimate, EMD affordability |
| DocIntel | `agents/docintel.py` | PDF → chunks → vector store → cited brief (dates, EMD, criteria, scope, evaluation) |
| EligibilityChecker | `agents/eligibility.py` | Criteria × company vault → PASS/FAIL/UNKNOWN per line + overall verdict |
| DeadlineTracker | `agents/deadline.py` | Deadline rows, urgency, corrigendum diffing |

## API (base `/api`)

- `GET/POST /profiles`, `GET/PUT/DELETE /profiles/{id}`
- `GET /tenders?profile_id=&verdict=&category=` (match scores + verdicts when profile given)
- `GET /tenders/{id}?profile_id=` → `{tender, brief, verdicts, deadlines, bidpack}`
- `POST /tenders/seed`, `DELETE /tenders/{id}`
- `POST /runs {profile_id, tender_id|null}`, `GET /runs`, `GET /runs/{id}`
- `GET /deadlines?days=30`, `GET/PUT /bidpack/{tender_id}`
- `POST /ingest/run {keyword, max_results}`, `GET /ingest/status`, `GET /health`

## Seed data

`seed/seed_tenders.json` — 10 real tenders captured 19-Oct-2026: 4 verified
(NHIDCL NH-110 maintenance ₹17.64cr; MoRTH NH-17 Kalimpong ₹26.67cr; MoRTH
Dhadhar bridge ₹19.84cr; MoRTH NH-52 DPR ₹7.30cr) + 6 CPPP tenders with full
tender IDs (NIT Karnataka, IIT Tirupati printers incl. full eligibility
criteria, IISc, Dept of Posts, DRDO, ASI Red Fort). Fields the portal didn't
show are `null` — never guessed. Each card is stamped with its capture date.

## LLM cost notes

- The pipeline runs **fully without an API key** (rule/regex/TF-IDF fallbacks).
- With a key, typical cost per tender deep-dive is a few paise: ~11 small
  extraction prompts + optional drafting. At `gpt-4o-mini`-class pricing,
  1,000 tender briefs ≈ ₹150–400.
- Embeddings are local (sentence-transformers) — no per-call cost. The model
  downloads once (~90MB) to `HF_HOME`.
- Scheduled ingestion makes **no** LLM calls (Scanner/Normaliser/Matcher are
  deterministic).

## Legal & safety notes — read before operating

1. **Portal ToS / robots:** the fetcher checks `robots.txt`, identifies itself,
   sleeps 2s between requests, retries twice, and times out. Review CPPP/GeM
   terms with counsel before production crawling; prefer official APIs where
   offered. Aggregators crawl at scale, but that is not legal advice.
2. **NO bid-submission automation, ever.** The app is read-only intelligence:
   discovery, reading, checking, tracking. Actual bid submission stays
   human-with-DSC. There is no code path that submits a bid.
3. **Snapshot vs live:** seed data is a dated snapshot for demo/evaluation.
   Always verify dates, EMD and criteria on the live portal before bidding —
   every tender card shows its source and capture date.
4. **Verdicts are advisory.** GO/CAUTION/NO-GO summarise extracted criteria
   against the profile you entered; the tender document is authoritative.
   Unknowns are marked UNKNOWN, never guessed.
5. **Secrets:** only via environment (`.env`, never committed). `.env.example`
   ships with empty placeholders.

## Tests

```bash
cd backend && python -m pytest tests/ -q
```

34 tests: rules engine (EMD matrix incl. GFR-170 exemptions, MSE L1+15%
boundaries, MII classes, eligibility comparators, deadline urgency, verdict
aggregation), section-aware chunking, citation grounding (quotes must be
verbatim substrings), and the scanner's junk-rejection gate.

## Project structure

```
tender-copilot/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, lifespan, static SPA mount
│   │   ├── config.py          # env-driven settings
│   │   ├── db.py              # SQLAlchemy models
│   │   ├── api/               # profiles, tenders, runs, deadlines, bidpack, ingest
│   │   ├── ingestion/cppp.py  # defensive CPPP fetcher + seed import
│   │   ├── pipeline/          # PDF extraction, section-aware chunking
│   │   ├── rag/               # embeddings (ST→TF-IDF fallback), ChromaDB store, cited extraction
│   │   ├── agents/            # 6 agents + LLM client + orchestrator
│   │   ├── rules/engine.py    # pure-function rules engine
│   │   ├── scheduler.py       # APScheduler: ingest + corrigendum jobs
│   │   └── static/            # SPA (index.html, app.js, styles.css)
│   ├── tests/                 # pytest suite
│   ├── requirements.txt
│   └── Dockerfile
├── seed/seed_tenders.json     # 10 real tenders, captured 19-Oct-2026
├── data/                      # SQLite DB, ChromaDB, HF cache (created at runtime)
├── docker-compose.yml
├── .env.example
└── README.md
```
