# Faculty Recruitment Screening System

A locally hosted pipeline and web portal that screens faculty-applicant CVs.
It turns a PDF or DOCX CV into a structured, evidence-backed profile, checks
publications against the open OpenAlex index and scores the candidate against
a fixed 100 mark rubric. A reviewer can see where every number came from and
override any score with a written reason.

---

## How it works

1. **Text extraction** – PyMuPDF / python-docx turn the CV into plain text.
   Scanned (image-only) files are refused rather than scored as empty.
2. **Field extraction** – a local language model (`qwen2.5:7b` via Ollama)
   reads the text into a fixed JSON shape. Every field carries the CV text it
   was read from.
3. **Validation** – Python checks every extracted value against the CV and
   removes anything it cannot trace. A rule based parser reads the degree lines
   directly to fill and correct what the model missed.
4. **Publication verification** – titles are matched against OpenAlex; an
   ORCID iD in the CV is resolved and name checked.
5. **Scoring** – deterministic Python scores eight criteria and evaluates the
   eligibility gate separately.
6. **Storage and review** – the full audit record is saved to disk and SQLite,
   and shown in the portal, where scores can be overridden.

The language model **never scores, ranks or decides eligibility**. It is used
only to read text into JSON and, optionally, to write explanatory prose about
numbers Python has already computed.

```
Browser (React, :5173) ──/api──▶ FastAPI (:8000) ──▶ run.py pipeline
                                                      ├─▶ Ollama (:11434, local)
                                                      ├─▶ OpenAlex API (internet)
                                                      └─▶ output/ (audit JSON + SQLite)
```

---

## Scoring rubric (`demo-rubric-v2`)

| Category (marks) | Criterion | Weight | Full marks at |
|---|---|---:|---|
| Research (40) | Publications | 35 | 20 publication-equivalents |
| | Research visibility | 5 | 100 citations or h-index 10 |
| Experience (25) | Teaching experience | 20 | 5 effective years |
| | Industry experience | 5 | 3 years |
| Education (35) | Doctorate | 15 | Awarded doctorate |
| | Postdoctoral experience | 5 | 2 years |
| | Master's degree | 8 | Equivalence × institution tier × subject fit |
| | Bachelor's degree | 7 | Equivalence × institution tier × subject fit |
| **Total** | | **100** | |

**Eligibility** is a gate, not a score: a doctorate awarded, *or* a UGC/CSIR
NET, SET or SLET qualification. It returns `met`, `not_met` or
`indeterminate`; an indeterminate result is routed to a reviewer, never
auto-rejected.

**Institution tiers** are configured in `config/institutions.json`
(multipliers 1.0 / 0.85 / 0.7). A name not in the list is scored at the base
rate and flagged rather than guessed.

---

## Requirements

| Component | Version tested |
|---|---|
| Python | 3.10.11 |
| Node.js / npm | 24.11.0 / 11.7.0 |
| Ollama | with model `qwen2.5:7b` (≈4.7 GB) |
| Internet | for OpenAlex lookups (optional; see below) |

Python packages (there is no pinned `requirements.txt` yet):

```bash
pip install fastapi uvicorn pydantic requests pymupdf python-docx python-multipart
pip install pytest httpx          # for the test suite
```

Tested with FastAPI 0.104.1, uvicorn 0.24.0, Pydantic 2.12.3,
PyMuPDF 1.28.2, python-docx 1.2.0 and requests 2.32.5.

---

## Setup

```bash
# 1. Pull the model (one time)
ollama pull qwen2.5:7b

# 2. Install frontend dependencies (one time)
cd frontend
npm install
```

---

## Running

Start three processes, each in its own terminal:

```bash
# 1. Language model server  (port 11434)
ollama serve

# 2. Backend API  (port 8000) — run from recruitment-mini/src
cd src
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000

# 3. Frontend  (port 5173) — run from recruitment-mini/frontend
cd frontend
npm run dev
```

Open **http://localhost:5173** (use `localhost`, not `127.0.0.1`). Vite
proxies `/api/*` to the backend, so the browser only talks to port 5173.

Processing one CV takes **several minutes**: almost all of it is the local
model generating tokens. You can keep using the portal while a CV runs.

### Command-line runner

The pipeline also runs without the portal, from `recruitment-mini/`:

```bash
python run.py                                   # every CV in data/cvs/
python run.py data/cvs/Candidate_02.pdf         # one CV
python run.py --as-of=2026-11-14                # pin "Present" for reproducibility
python run.py --reuse-extraction --no-llm       # re-score from stored extraction
```

| Flag | Effect |
|---|---|
| `--as-of=YYYY-MM-DD` | Date used to resolve "Present" and degree completion |
| `--reuse-extraction` | Replay the stored model output instead of calling Ollama (refuses if the CV file has changed) |
| `--no-llm` | Template explanations only (no model-written prose) |
| `--no-cache` | Ignore the OpenAlex response cache |

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/candidates` | Upload a CV and run the pipeline (`use_llm`, `reuse_extraction`, `as_of`, `department`) |
| GET | `/candidates` | Ranked list: eligible, needs review, not eligible |
| GET | `/candidates/{id}` | Full evidence for one candidate |
| GET | `/candidates/{id}/audit` | Complete stored audit record |
| POST | `/candidates/{id}/explain` | Generate model-written explanations (numbers are checked) |
| PATCH | `/candidates/{id}/score` | Record an override (`field`, `new_value`, `reason` — reason is mandatory) |
| GET | `/candidates/{id}/overrides` | Append-only override history |
| GET | `/rubric` | The full rubric and its approval status |

Interactive docs: **http://127.0.0.1:8000/docs** while the backend is running.

Overrides never rewrite the computed score. They are appended to a separate
table, and the portal shows both the *computed* and the *effective* value.

---

## Project structure

```
recruitment-mini/
├── run.py                  # End-to-end runner (CLI and API entry point)
├── config/
│   └── institutions.json   # Institution tiers (configurable)
├── src/
│   ├── extract_text.py     # Stage 1  CV file -> text
│   ├── schema.py           # Stage 2  field definitions and policy tables
│   ├── extract_fields.py   # Stage 3  local LLM extraction (Ollama)
│   ├── validate.py         # Stage 4  anti-hallucination checks
│   ├── education_parse.py  # Stage 4  rule-based degree reading
│   ├── roles.py            #          position categories, rank, postdoc
│   ├── verify_pubs.py      # Stage 5  OpenAlex verification
│   ├── research_profiles.py#          ORCID / Scholar / Scopus links
│   ├── scoring.py          # Stage 6  rubric and eligibility gate
│   ├── degrees.py          #          degree equivalence, subject relevance
│   ├── institutions.py     #          institution tier classification
│   ├── explain.py          # Stage 7a explanations (template / checked LLM)
│   ├── store.py            # Stage 7b SQLite persistence and overrides
│   ├── summary.py          #          deterministic profile summary
│   └── api/main.py         # Stage 8  FastAPI endpoints
├── frontend/               # Stage 9  React + Vite reviewer portal
├── tests/                  # pytest suite
├── data/                   # CVs and ground truth   (git-ignored)
└── output/                 # audit files and SQLite (git-ignored)
```

---
