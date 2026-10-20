# ERM peer benchmarking

Marsh × NTU hackathon project (Yash, Dhruv, Harsha).

An AI-assisted workflow that profiles a client from live public sources, proposes listed peers and benchmarks them:

- **Product A (phase 1):** risk profile benchmarking. Which risks each company discloses, where the client has gaps, and how each company structures its risk categories.
- **Product B (phase 2):** ERM framework benchmarking. Evidence-based 0–4 scores across 12 ERM dimensions.

Every finding links to a verbatim quote and page, checked against the PDF text by code. Consultants confirm peers, sources and findings.

**Status:** Product A prototype with a web app (React frontend, FastAPI backend) and an assistant chat over the fixed steps. Live runs need OpenRouter credits; the browser and Python tests run offline with a fake model.

- [docs/flow/user-flow-v2.md](docs/flow/user-flow-v2.md): the user flow this code implements (v2.1)
- [docs/flow/erm-benchmark-flow-v1.2.drawio](docs/flow/erm-benchmark-flow-v1.2.drawio): the workflow diagram shown to Marsh on 15 Oct
- [PLAN.md](PLAN.md): architecture and build plan (written before the 15 Oct call; sections 3, 6, 9 and 10 predate it)

Confidential Marsh material (the vision document, meeting notes, sample reports) goes in `private/`, which is git-ignored.

## Running it

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/) and an [OpenRouter](https://openrouter.ai/keys) API key. The default model is NVIDIA Nemotron 3.5 Super VL, set in `setup/v1/method.yaml`; web search runs through OpenRouter's server-side search tool. Claude is also supported: set `ERM_LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` in `.env`.

```bash
uv sync
cp .env.example .env          # then put your key in .env
(cd frontend && npm ci && npm run build)
uv run erm-bench serve        # http://127.0.0.1:8000
```

The API serves the built frontend. For frontend development, run `uv run erm-bench serve` and
`cd frontend && npm run dev` (http://localhost:5173, which proxies `/api` to port 8000).

Without the web app, for example over the SGX test set (accepts the AI's recommended peers):

```bash
uv run erm-bench run "City Developments" --year 2025 --until export
uv run erm-bench show <run id>
```

Tests, all offline with a fake model and generated PDFs:

```bash
uv run pytest                              # pipeline, verifier, API
cd frontend && npx playwright test         # browser tests (needs Google Chrome)
```

## Spending limits

Every run has a hard cap, set under `spending` in `setup/v1/method.yaml`: **$3.00 and 30 web
searches per run** while we test. A run that reaches either stops with a message before its next
model call; raise the limit and resume to continue. Research steps also have small search and
fetch budgets, and fetched pages are cut to 5,000 tokens: research only finds sources, and our
code reads the PDFs. Each run shows its estimated spend in the app.

## How it's organised

```
setup/v1/        standard setup used on every run: method settings, market-cap buckets,
                 taxonomy, filing-section guide, sector guides, one prompt per AI stage
src/erm_bench/
  pipeline.py    the fixed stages and checkpoints of the user flow
  chat.py        chat over those stages (turns messages into stage actions)
  llm.py         LLM interface and the Claude backend: web research with API citations, structured outputs
  llm_openrouter.py  OpenRouter backend (Nemotron by default): same two kinds of call
  documents.py   download, per-page text, company/year checks, risk-section locator
  verify.py      quote-on-page verifier
  product_a.py   coverage matrix, gaps, category structure comparison (plain code)
  export.py      Excel workbook
  spend.py       spending caps and cost estimates per run
  api.py         HTTP API for the frontend; long stages run as background jobs
frontend/        React + Vite web app (Marsh branding), Playwright tests
runs/<run id>/   one folder per run: stage outputs, downloaded PDFs, review and LLM logs
```

## Known gaps

- Market-cap bucket ranges in `setup/v1/method.yaml` are provisional until Bertrand confirms them.
- No OCR: pages without a text layer are flagged, not read.
- Some company websites block automated downloads. The disclosure scan prefers SGX links, and the consultant can upload a PDF in the sidebar.
