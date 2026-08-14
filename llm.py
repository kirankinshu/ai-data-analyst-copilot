"""
llm.py
------
Thin wrapper around Gemini 3.5 Flash for:
  * Natural language -> Pandas code generation (with self-correction retry)
  * Narrative insight generation

If GEMINI_API_KEY is not set, falls back to a small rule-based MOCK so the
rest of the app (upload -> profile -> chat -> chart -> report) can still be
exercised end-to-end without an API key during local development.
"""

from __future__ import annotations

import os
import re
import time

from dotenv import load_dotenv

from prompts import build_code_prompt, build_insight_prompt, schema_to_text

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# Keep this configurable for deployments. Flash-Lite is a stable, low-latency
# model well suited to the application's short Pandas code-generation prompts.
# The former gemini-2.5-flash endpoint is unavailable to new Gemini API users.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
# Fall back to the more capable Flash model if the primary endpoint is briefly
# overloaded. Set this to an empty value to disable failover.
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash").strip()

_client = None
if GEMINI_API_KEY:
    from google import genai

    _client = genai.Client(api_key=GEMINI_API_KEY)


class LLMError(Exception):
    pass


def _extract_code(text: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    code = match.group(1) if match else text
    return code.strip()


def _call_gemini(prompt: str) -> str:
    if _client is None:
        return _mock_response(prompt)

    # The SDK retries requests internally, but Gemini can still return a short
    # capacity spike. Retry temporary errors, then fail over to a separate
    # supported model instead of making the whole chat unavailable.
    last_error = None
    models = dict.fromkeys(model for model in (GEMINI_MODEL, GEMINI_FALLBACK_MODEL) if model)
    for model in models:
        for attempt in range(3):
            try:
                response = _client.models.generate_content(model=model, contents=prompt)
                return response.text
            except Exception as e:  # noqa: BLE001
                last_error = e
                message = str(e)
                is_temporary = "503" in message or "UNAVAILABLE" in message or "429" in message
                if not is_temporary or attempt == 2:
                    break
                time.sleep(2**attempt)

    raise LLMError(f"Gemini API call failed: {last_error}") from last_error


def generate_code(profile: dict, question: str, history: list[dict]) -> str:
    schema_text = schema_to_text(profile)
    prompt = build_code_prompt(schema_text, question, history)
    raw = _call_gemini(prompt)
    return _extract_code(raw)


def generate_code_with_retry(profile: dict, question: str, history: list[dict], run_fn, max_attempts: int = 2):
    """Generate code, run it, and if it errors, feed the error back to the LLM once more."""
    last_error = None
    code = generate_code(profile, question, history)

    for attempt in range(max_attempts):
        exec_result = run_fn(code)
        if exec_result.get("ok"):
            return code, exec_result
        last_error = exec_result.get("error", "Unknown error")
        if attempt < max_attempts - 1:
            retry_prompt = (
                f"The previous code raised an error:\n{last_error}\n\n"
                f"Previous code:\n```python\n{code}\n```\n\n"
                "Fix the code and return only the corrected fenced Python code block. "
                f"Remember the rules: {schema_to_text(profile)[:0]}"
                "Use only df, pd, np, px; assign the final answer to `result`."
            )
            raw = _call_gemini(retry_prompt)
            code = _extract_code(raw)

    return code, {"ok": False, "error": last_error}


def generate_insight(question: str, code: str, result_preview: str) -> str:
    prompt = build_insight_prompt(question, code, result_preview)
    try:
        return _call_gemini(prompt).strip()
    except LLMError:
        return "Insight generation is unavailable right now, but the table above reflects your query result."


# ---------------------------------------------------------------------------
# Mock mode (no API key) — very small heuristic so the app is runnable offline
# ---------------------------------------------------------------------------

def _mock_response(prompt: str) -> str:
    """Extremely simple heuristic fallback used only when GEMINI_API_KEY is unset."""
    if "User question:" in prompt:
        q = prompt.split("User question:")[-1].lower()
        if "insight" in prompt.lower() and "Result (preview)" in prompt:
            return "Mock insight: set GEMINI_API_KEY to enable real narrative insights from Gemini."
        if "average" in q or "mean" in q:
            return "```python\nresult = df.select_dtypes('number').mean()\n```"
        if "count" in q or "how many" in q:
            return "```python\nresult = len(df)\n```"
        if "top" in q or "highest" in q or "most" in q:
            num_cols = "df.select_dtypes('number').columns"
            return f"```python\ncol = {num_cols}[0]\nresult = df.sort_values(col, ascending=False).head(10)\n```"
        return "```python\nresult = df.describe(include='all').fillna('')\n```"
    return "```python\nresult = df.head(10)\n```"
