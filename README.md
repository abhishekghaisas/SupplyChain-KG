# Supply Chain Knowledge Graph

A **neuro-symbolic AI system** for supply chain intelligence. Combines a Neo4j property graph (symbolic reasoning) with Anthropic's Claude (neural processing) to extract entities from supplier catalogs, analyse supply risk, model disruption scenarios, and answer natural language questions about your supply chain.

---

## What it does

| Feature | Description |
|---|---|
| **Entity extraction** | Paste a supplier catalog or BOM — Claude extracts parts, suppliers, and supply relationships into the graph |
| **BOM management** | Create, version, diff, and approve Bills of Materials with a full state machine workflow |
| **Supply risk analysis** | Every BOM component is assessed for supplier coverage, single-source risk, and substitute availability |
| **Disruption modelling** | Model the impact of a supplier or part becoming unavailable across all released BOMs |
| **Substitute suggestion** | Claude compares part specifications and infers compatible substitutes with per-spec reasoning |
| **Similarity search** | Semantic search over parts, suppliers, and BOMs — no exact keywords required |
| **Ask the graph** | Natural language queries translated to Cypher and answered in plain English with cited row references |
| **AI reviews** | Grounded pre-approval BOM reviews, supplier qualification memos, and disruption narratives |

All AI features are **grounded** — Claude receives live graph data in the prompt and is explicitly instructed not to use training knowledge for supply chain facts.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    React Frontend                    │
│         (Vite · Tailwind · 7 pages)                 │
└───────────────────┬─────────────────────────────────┘
                    │ HTTP (OAuth2 Bearer)
┌───────────────────▼─────────────────────────────────┐
│                  FastAPI (2 workers)                 │
│  Auth · Parts · Suppliers · BOMs · Disruption        │
│  Extraction · Search · Query · Reasoning             │
└──────┬────────────┬──────────────┬───────────────────┘
       │            │              │
┌──────▼───┐  ┌─────▼─────┐  ┌───▼──────────────────┐
│  Neo4j   │  │   Redis   │  │  Anthropic Claude     │
│  5.14    │  │  7.2      │  │  Haiku 4.5            │
│  Graph   │  │  4 DBs    │  │  + Prompt caching     │
└──────────┘  └───────────┘  └──────────────────────┘
```

**Redis databases:**
- `db:0` — rate limiting
- `db:1` — refresh token store
- `db:2` — API response cache
- `db:3` — vector embeddings (all-MiniLM-L6-v2)

---

## Quick start

### Prerequisites

- Docker Desktop
- An [Anthropic API key](https://console.anthropic.com)
- Node.js 18+ (for the frontend)

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/SupplyChain-KG.git
cd SupplyChain-KG

cp .env.example .env
```

Edit `.env` and fill in:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Generate with: openssl rand -hex 32
JWT_SECRET_KEY=your-secret-here

# OAuth2 client credentials
OAUTH2_CLIENT_ID=supply-chain-api

# Generate hash: python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt(rounds=12)).decode())"
# Then escape every $ as $$ in .env
OAUTH2_CLIENT_SECRET_HASH=$$2b$$12$$...
```

### 2. Start the backend

```bash
docker compose up -d
```

This starts Neo4j, Redis, and the API. First run takes 2–3 minutes (downloads the sentence-transformers model).

```bash
# Check everything is healthy
docker compose ps
curl http://localhost:8000/health
```

### 3. Get an access token

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -d 'grant_type=client_credentials&client_id=supply-chain-api&client_secret=yourpassword' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) and sign in with your client ID and secret.

---

## Loading data

The system starts empty. Load data via the **Extract** page in the frontend, or by calling the extraction API directly:

```bash
# Extract from a text document
curl -s -X POST http://localhost:8000/extraction/extract \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "SUPPLIER CATALOG — Acme Motors\nPart No: AM-400\nName: Servo Motor 400W\n...",
    "document_type": "catalog",
    "persist": true
  }'
```

After loading data, rebuild the search index:

```bash
curl -s -X POST http://localhost:8000/search/reindex \
  -H "Authorization: Bearer $TOKEN"
```

---

## Running tests

```bash
# Unit tests (no Neo4j required)
pytest tests/ -m "not db" --strict-markers

# All tests (requires running Neo4j)
pytest tests/
```

The CI workflow runs unit tests on every push to `main` and `develop` across Python 3.11 and 3.12.

---

## Project structure

```
SupplyChain-KG/
├── src/
│   ├── api/
│   │   ├── auth.py              # OAuth2 + JWT + refresh tokens
│   │   ├── cache.py             # Redis response cache middleware
│   │   ├── limiter.py           # Rate limiting (slowapi + Redis)
│   │   ├── token_store.py       # Refresh token store (Redis db:1)
│   │   └── routers/
│   │       ├── parts.py         # Parts CRUD + substitute suggestion
│   │       ├── suppliers.py     # Suppliers CRUD + AI qualification
│   │       ├── boms.py          # BOMs + versioning + approval workflow
│   │       ├── disruption.py    # Disruption analysis
│   │       ├── extraction.py    # Claude entity extraction
│   │       ├── search.py        # Semantic similarity search
│   │       └── query.py         # Natural language graph queries
│   ├── ai/
│   │   ├── grounded.py          # RAG foundation + NL query engine
│   │   └── substitute_suggester.py  # AI substitute inference
│   ├── bom/
│   │   ├── versioning.py        # Clone, diff, lineage
│   │   ├── approval_workflow.py # State machine + rules gate
│   │   └── disruption.py       # Disruption analysis engine
│   ├── graph/
│   │   └── neo4j_client.py      # Neo4j client (MERGE-safe)
│   ├── ingestion/
│   │   └── entity_extractor.py  # Claude extraction + normalisation
│   ├── reasoning/
│   │   ├── rules_engine.py      # Symbolic rules engine
│   │   ├── supply_chain_rules.py
│   │   └── provenance.py        # Decision audit trail
│   └── search/
│       ├── embedder.py          # sentence-transformers wrapper
│       └── vector_store.py      # Redis vector store + cosine search
├── frontend/
│   └── src/
│       ├── api/client.js        # Typed API client + auto token refresh
│       ├── components/          # Layout, SearchBar, AIReview, SubstituteSuggestions
│       └── pages/               # Parts, Suppliers, BOMs, Disruption, Reasoning, Extract, Query
├── tests/
│   ├── test_auth.py
│   ├── test_rate_limiting.py
│   ├── test_token_refresh.py
│   ├── test_bom_versioning.py
│   ├── test_approval_workflow.py
│   ├── test_disruption.py
│   └── integration/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .github/workflows/ci.yml
```

---

## Key API endpoints

### Auth
```
POST /auth/token    — get access + refresh token pair
POST /auth/refresh  — rotate refresh token
POST /auth/revoke   — logout (invalidate refresh token)
```

### Core
```
GET  /parts                          — list parts (filter: category, criticality, id_prefix)
POST /parts/{id}/suggest-substitutes — AI substitute candidates with spec reasoning
GET  /suppliers                      — list suppliers
POST /suppliers/{id}/ai-qualify      — AI qualification memo
GET  /boms                           — list BOMs
POST /boms                           — create BOM with inline components
POST /boms/{id}/clone                — clone to new version
GET  /boms/{id}/diff/{other_id}      — diff two versions
POST /boms/{id}/ai-review            — pre-approval AI review
POST /boms/{id}/transition           — advance status (DRAFT→REVIEW→RELEASED)
```

### Intelligence
```
GET  /disruption/supplier/{id}  — supplier disruption analysis
GET  /disruption/part/{id}      — part disruption analysis
POST /disruption/ai-narrate     — plain-English executive summary
GET  /search?q=servo+motor      — semantic similarity search
POST /query                     — natural language graph query
```

Full API docs available at [http://localhost:8000/docs](http://localhost:8000/docs) when running.

---

## AI & cost management

**Prompt caching** — the 4,353-token system preamble is cached by Anthropic for 5 minutes. Measured savings: **83% on repeated calls** within a session (`cache_tokens_read: 4,474` vs `cache_tokens_written: 4,474`).

**Redis API cache** — GET endpoints are cached in Redis (TTL 60–600s by endpoint group). Cache hit rate is visible on `GET /health`.

**Rate limiting** — extraction endpoint is limited to 20 requests/hour to control Claude API costs. All other endpoints limited to 120 requests/minute.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✓ | Anthropic API key |
| `JWT_SECRET_KEY` | ✓ | HS256 signing secret (`openssl rand -hex 32`) |
| `OAUTH2_CLIENT_ID` | ✓ | OAuth2 client identifier |
| `OAUTH2_CLIENT_SECRET_HASH` | ✓ | bcrypt hash of client secret (escape `$` as `$$`) |
| `NEO4J_PASSWORD` | — | Default: `supplychainkg` |
| `CLAUDE_MODEL` | — | Default: `claude-haiku-4-5-20251001` |
| `JWT_EXPIRE_MINUTES` | — | Default: `60` |
| `REFRESH_EXPIRE_DAYS` | — | Default: `7` |

---

## Troubleshooting

**`ImportError: cannot import name 'verify_api_key'`**
```bash
sed -i '' 's/verify_api_key/verify_token/g' src/api/routers/*.py
docker compose build --no-cache api && docker compose up -d api
```

**Parts/suppliers not showing after data load**
```bash
# Clear the API response cache
docker exec supply-chain-redis redis-cli -n 2 FLUSHDB
```

**Search returning no results**
```bash
# Rebuild vector embeddings
curl -X POST http://localhost:8000/search/reindex -H "Authorization: Bearer $TOKEN"
```

**Prompt caching not activating (Haiku 4.5)**
The preamble must be ≥ 4,096 tokens. Current preamble is 4,353 tokens. If you see `cache_tokens_written: 0`, check that `src/ai/grounded.py` contains the full preamble including the domain knowledge sections.

---

## License

Private — Internal Use Only
