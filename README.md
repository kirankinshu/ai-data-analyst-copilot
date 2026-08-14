# AI Data Analyst Copilot

**Live demo:** http://ai-data-analyst-copilot.ap-south-2.elasticbeanstalk.com/

![Python](https://img.shields.io/badge/Python-3.13-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688) ![React](https://img.shields.io/badge/React-frontend-61DAFB) ![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

Upload a CSV/Excel file, ask questions about it in plain English, and get back
auto-generated charts, plain-language insights, and a downloadable Excel report —
powered by Gemini Flash + Pandas, executed in a sandboxed backend.

This implements the workflow and tech stack from the project PRD:
**Stack:** FastAPI (backend) · React + Vite (frontend) · Gemini Flash (LLM) ·
Pandas + Plotly (data & charts) · sandboxed subprocess execution (no LangChain /
vector DB needed yet — see PRD §10).

---

## Project structure
---

## 1. Backend setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env and add your key:
#   GEMINI_API_KEY=your_key_here
```

Get a Gemini API key from https://aistudio.google.com/apikey (free tier available).

Run the server:

```bash
uvicorn main:app --reload --port 8000
```

The API is now live at `http://localhost:8000` (interactive docs at `/docs`).

> **No API key yet?** The backend runs fine without `GEMINI_API_KEY` — it falls
> back to a small rule-based mock so you can test the upload → chat → chart →
> report pipeline end-to-end before wiring up a real key.

## 2. Frontend setup

```bash
cd frontend
npm install
cp .env.example .env    # VITE_API_BASE=http://localhost:8000
npm run dev
```

Open `http://localhost:5173`.

## 3. Try it

1. Upload a CSV/Excel file (a sample is at the bottom of this README).
2. Read the auto-generated dataset summary in the sidebar.
3. Ask a question, e.g. *"what's the average revenue by region?"* or
   *"show me the trend over time"*.
4. Expand **Show generated code** to see the exact Pandas that ran.
5. Click **Download Report** for an Excel workbook of the whole session.

---

## Security notes (see PRD §11 — Data Handling & Security)

- Generated code is statically checked for disallowed tokens (`import os`,
  `open(`, `eval(`, `subprocess`, etc.) before it ever runs.
- Code executes in an **isolated worker thread** with a restricted
  `__builtins__` set — no filesystem, network, or process access — and a
  wall-clock timeout (15s default).
- Only `df`, `pd`, `np`, and `px` are available inside the sandbox.
- Uploaded files and session data live in memory only (`SESSIONS` dict) and
  are cleared when the process restarts. Swap in Redis/a DB before scaling to
  multiple workers or persisting across restarts.

This is an MVP-grade sandbox suitable for a trusted single-tenant demo. For a
production deployment, harden further with a containerized sandbox (e.g. gVisor
/ Firecracker) rather than relying on restricted builtins alone.

## What's intentionally left out of v1 (see PRD §5, §14)

- Multi-file joins, live DB connections, write-back to source data
- Real user auth (`auth.py` currently issues opaque per-session tokens only)
- LangChain / a vector DB — add only if prompt orchestration or semantic
  search needs outgrow direct Gemini API calls (see PRD §10)

## Sample dataset to test with

```csv
date,region,revenue
2026-01-01,West,100
2026-02-01,East,150
2026-03-01,West,120
2026-04-01,East,160
2026-05-01,West,130
2026-06-01,East,170
2026-07-01,West,140
2026-08-01,East,180
2026-09-01,West,150
2026-10-01,East,190
2026-11-01,West,160
2026-12-01,East,200
```

## License

MIT — see [LICENSE](LICENSE).
