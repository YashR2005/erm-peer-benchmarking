# Build plan: ERM peer benchmarking (v0.1)

Planning draft, 20 Oct 2026. No code yet.

**Sources this plan is built from**

- *ERM Product Vision and Requirements* (24 Sep WIP, with Bertrand's comments BL1–BL3)
- Marsh call, 13 Oct (Zoom summary): the static knowledge-base approach was rejected
- Our flow diagram v1.2, 14 Oct: [docs/flow/erm-benchmark-flow-v1.2.drawio](docs/flow/erm-benchmark-flow-v1.2.drawio)

The diagram is the spec. This plan says how to build it in 8 weeks (two 4-week sprints).

---

## 1. What we're building

A consultant enters a client name and some context: sector, regions, reporting year, peer type and peer count. The system then:

1. Profiles the client from live public sources.
2. Proposes peers, which the consultant confirms.
3. Finds and prepares the disclosure documents for the client and each peer.
4. Runs one or both products:
   - **Product A (risk profile benchmarking).** Proposes a client risk list, extracts each company's disclosed risks and builds a risk coverage matrix with gaps.
   - **Product B (ERM framework benchmarking).** Extracts evidence across 12 ERM dimensions, scores each dimension 0–4 from rubric rules and compares the client with its peers.
5. Drafts a report in Marsh's template. A consultant reviews it, an ERM reviewer approves it, and it is exported as Word/PDF plus an Excel evidence appendix.

## 2. Design rules (what the 13 Oct call changed)

| Rule | Where it comes from |
|---|---|
| **Live search on every run. No pre-built company database.** We only store what one run downloaded (by file hash) so the run can be reproduced. | Marsh call: the KB approach was "too static" |
| **The AI proposes peers from the user's input.** The user confirms, swaps or adds. | Marsh call, vision doc FR-09/10 |
| **One versioned "standard setup" is used on every run**: rubric, sector search guides, filing-section guide, taxonomy, prompts and report template. Two consultants with the same input get comparable output. This is our version of the "skill" Akshay described. | Marsh call (standardisation), diagram step 2 |
| **Every fact and finding traces to an evidence ID**, which points to a document, page and verbatim quote. Unverified quotes never reach scoring or the report. | Vision doc §6, diagram steps 4, 8, A3, 14 |
| **Scores come from code applying rubric rules**, not from model judgement, so they are repeatable. | Diagram steps 9–10 |
| **The LLM proposes and people decide**: peers, sources, risk list, scores and client-facing wording. | Vision doc §6 |
| **No public disclosure does not mean no risk management.** A gap means "not publicly evident", and a client with no standalone disclosure reads "insufficient evidence". | Vision doc core principle, diagram A3 |

## 3. Scope for the 8 weeks

**In scope:** steps 1–18 and A1–A7 from diagram v1.2, using public data only.

**Out of scope:**

| Item | Why |
|---|---|
| Confidential client uploads | Diagram v1.2 is public-only. The vision doc MVP includes them, so we need to confirm with Marsh (§11). |
| Likelihood × impact ratings | Removed per BL2 |
| SSO, multi-tenancy, Singapore cloud deployment | Production concerns, not prototype work |
| GRC integrations | Deferred in the vision doc |
| PowerPoint export | Diagram outputs are Word/PDF plus Excel. JSON comes free from the run store. |

## 4. Architecture

The app is a pipeline of stages. Each stage reads the run state and writes one JSON artifact. The diagram's lanes map directly onto three kinds of stage:

- **System** (white): deterministic Python, such as loaders, verifiers, scoring, comparison and exports
- **AI** (amber): Claude calls that return schema-validated JSON
- **Person** (blue): a checkpoint. The run pauses in `awaiting_review` until someone acts in the UI.

```
UI (Streamlit) ── checkpoints: peers · sources · risk list · scores · draft · approval
   │
Orchestrator ── run state machine, resumable, one folder per run
   │
   ├── stages/   request · profile · peers · discover · prepare · product_a · product_b · report
   ├── llm/      Claude client: web search/fetch, structured outputs, caching, call log
   ├── docs/     download · SHA-256 · per-page text · OCR fallback · section locator
   ├── verify/   quote-on-page · entity boundary · claim checker
   ├── scoring/  rubric rules → 0–4, peer median/range/n
   └── export/   Excel · Word (Marsh template) · PDF
setup/vN/ ── versioned standard setup: rubric · sector guides · filing sections · taxonomy · prompts · template
```

**Run store.** No database for the prototype. Each run is one folder, which covers the vision doc's reproducibility requirement (§10):

```
runs/<run_id>/
  run.json          request, setup version, model IDs, prompt versions, status
  sources/          downloaded documents, named by SHA-256
  pages/            extracted text per page (what quotes are checked against)
  stages/*.json     one artifact per stage (profile, peers, evidence, scores, ...)
  reviews.jsonl     every human action: who, what, before → after, reason
  llm_calls.jsonl   model, prompt version, input hash, output, tokens, cost
  exports/
```

Re-running a stage replaces its artifact and marks the stages after it as stale. That covers the diagram's loops: re-propose peers, re-review a dimension, regenerate a section.

## 5. Tech stack (recommendation)

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Best libraries for PDFs, OCR and Office exports |
| LLM | Claude API via the `anthropic` SDK | Wrapped in a thin `llm/` interface so the provider can change (Marsh Q16, vision doc provider independence) |
| Default model | `claude-opus-5-5`, 1M context | Effort tuned per stage |
| Cheaper model | `claude-haiku-5-5` | Only for classification stages (A4 mapping, evidence-type tagging), and only after the eval shows no quality loss |
| Live discovery | Server tools `web_search_20260209` and `web_fetch_20260209` | Use `allowed_domains` once Marsh confirms which sources are allowed |
| Structured output | `output_config.format` with Pydantic schemas | Every AI stage |
| Long documents | Prompt caching | Each company's document is the cached prefix for its 12 dimension prompts (§7) |
| SEC filers | EDGAR APIs directly | Deterministic and cheaper than web search for 10-K/20-F |
| PDF text | PyMuPDF, per page | `ocrmypdf`/Tesseract fallback for scans |
| Quote checking | `rapidfuzz` | Normalised fuzzy match per page |
| Exports | `openpyxl`, `docxtpl` (Marsh .docx template), LibreOffice headless for PDF | |
| UI | Streamlit | Fastest way to build the six review screens. The pipeline core has no UI dependency, so FastAPI + React can replace it later. |
| Tooling | `uv`, `pytest`, `ruff` | |

## 6. Stage-by-stage build notes

### Shared steps 1–6b

| Step | Lane | Build | Output and checks |
|---|---|---|---|
| 1 Request | UI | Form with client, sector, regions, reporting year, peer type (direct / disclosure / both) and peer count (default 3) | `request.json` |
| 2 Load setup | code | Load `setup/vN/` | Version recorded in `run.json` |
| 3 Profile client | AI + web | Business model, markets, group/ownership, scale, listing and disclosure availability. Each fact is `{text, url, quote, retrieved_at}`. | `profile.json` |
| 4 Check profile | code | Every fact's URL was actually fetched and the quote appears in it. Flags group vs subsidiary, e.g. unlisted client with a listed parent (Lazada → Alibaba). | Boundary flag stored on the run and used by A3 and 8 |
| 5 Propose peers | AI + web | Recommended set plus alternatives, comparison table, why comparable, key limitation, linked-entity warnings, disclosure availability | `peers.json` |
| ◇ Confirm peers | UI | Accept, swap or add. On "no", the feedback goes back to step 5. | Locked peer set |
| 6 Find disclosures | AI + web, plus EDGAR | Uses the sector guide (regulators, exchanges, filing types). Covers client and peers equally. | `sources.json`: URL, type, period, entity |
| 6b Prepare | code | Download, hash, check company/year/document type, per-page text, OCR fallback, locate sections from the filing-section guide (regex first, LLM fallback) | `pages/`, `sections.json` |
| ◇ Sources complete | UI | Add a URL or a public PDF, or mark a source n/a | Locked source set |

### Product A, steps A1–A7

| Step | Lane | Build |
|---|---|---|
| A1 Propose risk list | AI | Built from profile facts and the sector's typical risks. Each risk cites a `fact_id` and gives its consequence and the segment/geography affected. Labelled `context-inferred`. |
| A2 Extract disclosed risks | AI, per company | Runs only on located risk sections (10-K 1A, 20-F 3.D, annual report risk section). Keeps the verbatim quote, document, page and the company's own wording. |
| A3 Verify | code | Same verifier as step 8. If the client has no standalone risk disclosure, the client column is "insufficient evidence" and no client gaps are reported. |
| A4 Map to taxonomy | AI (Haiku candidate) | Taxonomy comes from setup. Labels are context-inferred, client-public and peer-public. |
| A5 Review | UI | Accept, edit, remove or remap |
| A6 Output | code | Coverage matrix of risk × company. Three gap types, reported only where the client has its own disclosure. |
| A7 Export | code | Excel matrix with quotes and pages, plus a short Word summary |

### Product B, steps 7–18

| Step | Lane | Build |
|---|---|---|
| 7 Extract evidence | AI, one company × 12 dimensions | Each evidence item has a quote, document, page and evidence type (policy, governance, process, metric/KRI, assurance, outcome; see PB-04). Each dimension also gets an explicit "not publicly evident" list. |
| 8 Verify | code | Quote must appear on the cited page. Entity boundary rule, e.g. AWS customer material is not Amazon corporate ERM. Evidence state is found, none or insufficient. |
| 9 Rubric answers | AI | Each criterion is answered yes, no, partly or unclear, citing evidence IDs |
| 10 Score | code | Rules give 0–4, plus a confidence value and evidence availability |
| 11 Review | UI | Confirm, override with a reason, or reject evidence. Rejected evidence re-runs that dimension only. |
| 12 Compare | code | Client vs each peer per dimension, peer median/range/n (PB-06), strongest and weakest dimensions, comparability warnings |
| 13 Draft report | AI | Uses Marsh template sections and approved evidence only. Every sentence carries evidence IDs. Stops at findings. |
| 14 Claim checker | code | Every factual sentence has at least one approved evidence ID. Optional LLM check that the quote supports the sentence. |
| 15–16 Review / regenerate | UI / AI | Comments per section. Only the commented section is regenerated. The consultant writes the recommendations. |
| 17 Final approval | UI | ERM reviewer approves the client-facing wording |
| 18 Export | code | Branded Word/PDF plus the Excel evidence appendix |

## 7. Extracting from long documents

Annual reports run 150–400 pages, so extraction needs some care:

- **Product A** only needs the located risk-factor sections, which are small.
- **Product B** evidence is spread across the governance report, the risk management section, the audit committee report and the sustainability report. Send the located sections, or the whole report if it fits. Most fit in Opus 5.5's 1M context.
- **Cost control.** Put a company's page-tagged text in a cached prefix (`<page n="47">…</page>`), then run the 12 dimension prompts against it. The first call writes the cache. The other 11 run in parallel and read the prefix at about 10% of the normal input price.
- **Citations: spike in week 1.** Claude's native citations return verbatim `cited_text` with page numbers, but they can't be combined with structured outputs in the same call (the API returns a 400).
  - Plan of record: structured output returns `{quote, page}` and our verifier checks it. We need the verifier anyway for steps 4, 8, A3 and 14.
  - Spike: native citations on per-page content blocks, followed by a cheap structuring pass.
  - Pick whichever gives the higher verified-quote rate.

## 8. The standard setup (`setup/v1/`)

| File | Contents |
|---|---|
| `rubric.yaml` | 12 dimensions, each with criteria tied to levels 1–4, plus the scoring rule. Config-driven, so the COSO alternative can be loaded without code changes (§11). |
| `sector_guides/<sector>.yaml` | Regulators, exchanges, filing types, typical risks and search hints. Start with e-commerce/tech (Lazada) and the sectors of the other test clients. |
| `filing_sections.yaml` | 10-K Items 1A/1C/9A; 20-F Items 3.D/15/16K; SGX annual report risk management and corporate governance sections (Code of Corporate Governance Principle 9) |
| `taxonomy.yaml` | 7-category default (open question Qa4) |
| `prompts/*.md` | One versioned prompt per AI stage |
| `report_template.docx` | Marsh template (pending Q26) |

**Draft scoring rule (to confirm with Marsh).** Each level has required criteria. The score is the highest level whose required criteria, and those of every level below it, are all "yes". A "partly" answer counts toward level 1–2 criteria only.

Example, dimension 1 (risk strategy, appetite and decision integration):

| Level | Criterion |
|---|---|
| 1 | Mentions risk appetite |
| 2 | Company-specific appetite statement |
| 3 | Board-approved, with quantified limits or tolerances, cascaded to businesses |
| 4 | Performance against appetite reported, with breaches or changes disclosed |

We draft the criteria from the "not publicly evident" lists in Marsh's demo and Marsh confirms them (Q9, BL3).

## 9. Evaluation

The test plan comes from diagram note q34: Lazada plus 2–3 other clients, each run twice, with one run compared against a Marsh-reviewed answer.

| Metric | How |
|---|---|
| Quote accuracy | Verifier pass rate, plus a manual spot check of 20 quotes per run |
| Peer stability | Overlap of proposed peers between the two runs |
| Score stability | Same score, or within ±1, between the two runs per dimension |
| Agreement with Marsh | Our scores vs the Marsh-reviewed scores for one client |
| Unsupported claims | Claim-checker failures in the draft before review |
| Effort | Wall-clock time and API cost per run, from `llm_calls.jsonl` |

Ask Marsh for the reviewed answer early, ideally by week 4, so the target is fixed.

## 10. Sprint plan

**Sprint 1 (weeks 1–4): a thin end-to-end slice of Product B on Lazada, run from the CLI first**

| Week | Work |
|---|---|
| 1 | Repo, schemas (Pydantic), run store, `llm/` wrapper with call logging, `setup/v1` skeleton, draft rubric. Spikes: citations vs structured output with verifier; web search reliability for finding disclosures. |
| 2 | Steps 1–6b: profile, peers, disclosure discovery and document prep for Lazada and its peers |
| 3 | Steps 7–10: extraction, verifier, rubric answers, scoring. Measure cost per run. |
| 4 | Step 12 comparison, basic Excel export, Streamlit checkpoints for peers and scores. **Demo to Marsh; collect rubric feedback.** |

**Sprint 2 (weeks 5–8): Product A, the report and test runs**

| Week | Work |
|---|---|
| 5 | Product A, A1–A7 |
| 6 | Steps 13–16: report drafting, claim checker, Word template export, PDF |
| 7 | Remaining checkpoints and final approval (17). Run the test plan: 3–4 clients × 2 runs. |
| 8 | Fixes, eval write-up, final demo, handover README |

**Suggested ownership** (one track each; names to assign):

1. Discovery and documents: steps 3–6b, EDGAR/SGX, OCR, section locator
2. Evidence and scoring: steps 7–12, A2–A4, verifier, rubric engine
3. Report, export and UI: steps 13–18, A6–A7, Streamlit checkpoints, templates

## 11. Decisions needed from Marsh

**Blocking sprint 1**

1. **Scoring framework.** The vision doc now has two:
   - The 12-dimension, 0–4 framework (its last section, and our diagram)
   - COSO-based scoring (BL1), with BL3 suggesting a 1–5 scale per sub-principle

   Note that COSO ERM 2017 has **20** principles across 5 components, not 21 as BL3 says. Our proposal is to keep the 12 dimensions as the scored unit and map each one to its COSO components/principles in the report. The rubric engine is config-driven either way.
2. **Rubric criteria.** May we draft them from the demo's "not publicly evident" lists for Marsh to confirm? (Q9)
3. **AI provider and web access.** Which are approved, and are any sources off-limits? (Q16) Is Singapore data residency needed for inference in the prototype?
4. **Claude CoPilot transcript.** This was Akshay's action item. Has it arrived? We want to match the demo's peer reasoning.

**Needed by sprint 2**

5. Can Marsh share 1–2 past reports to use as the template? (Q26)
6. Should Product A use the Product B peers, or direct competitors only? (Qa2)
7. Taxonomy: the 7 appendix categories or the vision doc's 8? (Qa4)
8. Is Product A delivered on its own? Does an ERM reviewer approve its Word summary? (Qad)
9. Peer defaults: how many, global or region-first, and who confirms? (Q3)
10. Can we confirm confidential uploads are out of scope for the hackathon? They are in the vision doc MVP.
11. Can we confirm the risk-context matrix is removed everywhere? BL2 removed it, but MVP §11 still lists a "configurable matrix".

## 12. Risks

| Risk | Mitigation |
|---|---|
| Search returns the wrong document (wrong year, parent company, a press summary) | Checks in step 6b, the sources-complete gate, and EDGAR/SGX first where possible |
| Private clients and peers have thin disclosure (e.g. Lazada) | "Insufficient evidence" is a valid result, and peer proposals weigh disclosure availability |
| The model invents a quote | The verifier is deterministic, and nothing unverified reaches scoring or the report |
| Scores vary between runs | Score by code from criterion answers, pin the setup version, and run the run-twice test |
| Cost and latency (12 dimensions × ~4–6 companies × long documents) | Section filtering, prompt caching, parallel calls, and measuring cost in week 3 |
| Exam break between sprints | Sprint 1 ends with a working demo and written notes, so sprint 2 starts cleanly |

## 13. Planned repo layout

```
erm-peer-benchmarking/
  README.md
  PLAN.md
  docs/flow/            flow diagrams (v1.2 and later)
  setup/v1/             standard setup (rubric, sector guides, filing sections, taxonomy, prompts, template)
  src/erm_bench/        orchestrator, stages/, llm/, docs/, verify/, scoring/, export/
  app/                  Streamlit UI
  eval/                 gold answers, run comparisons, eval reports
  tests/
  runs/                 run outputs (git-ignored)
  private/              Marsh documents, meeting notes (git-ignored)
```
