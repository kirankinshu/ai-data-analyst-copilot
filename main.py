"""
main.py
-------
FastAPI entrypoint for the AI Data Analyst Copilot backend.

Endpoints:
  POST /api/upload            -> upload a CSV/Excel file, returns session_id + profile
  POST /api/chat               -> ask a question about the dataset
  GET  /api/session/{id}       -> fetch session profile + history
  GET  /api/report/{id}        -> download an Excel report of the session
  GET  /api/health             -> liveness check
"""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

import charts
import llm
import report
from dataframe import DatasetError, get_session, load_file, new_session, run_code_safely

app = FastAPI(title="AI Data Analyst Copilot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str
    question: str


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    try:
        df = load_file(file.filename, content)
        session = new_session(file.filename, df)
    except DatasetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {
        "session_id": session.id,
        "filename": session.filename,
        "profile": session.profile,
    }


@app.get("/api/session/{session_id}")
def get_session_info(session_id: str):
    try:
        session = get_session(session_id)
    except DatasetError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {
        "session_id": session.id,
        "filename": session.filename,
        "profile": session.profile,
        "history": [
            {"question": t["question"], "insight": t.get("insight"), "code": t.get("code")}
            for t in session.history
        ],
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        session = get_session(req.session_id)
    except DatasetError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    def run_fn(code: str):
        try:
            return run_code_safely(code, session.df)
        except DatasetError as e:
            return {"ok": False, "error": str(e)}

    history_for_prompt = [{"question": t["question"], "code": t["code"]} for t in session.history]
    try:
        code, exec_result = llm.generate_code_with_retry(
            session.profile, question, history_for_prompt, run_fn
        )
    except llm.LLMError as e:
        # Do not let an upstream Gemini failure turn into an unhandled 500.
        # The frontend can display this structured response to the user.
        raise HTTPException(
            status_code=503,
            detail=(
                "The AI service is temporarily unavailable. Check GEMINI_MODEL, "
                "GEMINI_API_KEY, and the backend logs, then try again. "
                f"Provider error: {e}"
            ),
        ) from e

    if not exec_result.get("ok"):
        turn = {"question": question, "code": code, "error": exec_result.get("error")}
        session.history.append(turn)
        raise HTTPException(
            status_code=422,
            detail={"message": "Could not answer that question.", "error": exec_result.get("error"), "code": code},
        )

    result_payload = exec_result["result"]
    result_preview = _preview_result(result_payload)
    insight = llm.generate_insight(question, code, result_preview)
    chart_fig = charts.build_chart(result_payload, question)

    turn = {
        "question": question,
        "code": code,
        "result": result_payload,
        "insight": insight,
        "chart": chart_fig,
    }
    session.history.append(turn)

    return {
        "question": question,
        "code": code,
        "result": result_payload,
        "insight": insight,
        "chart": chart_fig,
    }


@app.get("/api/report/{session_id}")
def download_report(session_id: str):
    try:
        session = get_session(session_id)
    except DatasetError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    xlsx_bytes = report.build_excel_report(session)
    filename = f"report_{session.filename.rsplit('.', 1)[0]}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _preview_result(payload: dict) -> str:
    kind = payload.get("kind")
    if kind == "scalar":
        return str(payload["data"])
    if kind in ("dataframe", "series"):
        d = payload["data"]
        cols = d.get("columns") or [d.get("name", "value")]
        rows = d["data"][:10]
        lines = [", ".join(map(str, cols))]
        for r in rows:
            lines.append(", ".join(map(str, r if isinstance(r, list) else [r])))
        return "\n".join(lines)
    return ""
