"""
dataframe.py
------------
Handles:
  * Loading uploaded CSV/Excel files into pandas DataFrames
  * Profiling a dataset (schema, types, stats, natural-language summary)
  * Safely executing LLM-generated Pandas code against the dataframe in an
    isolated subprocess with a resource/time limit and a restricted builtins
    allow-list.

Each analysis "session" (one uploaded file) lives in an in-memory SESSIONS
dict keyed by a session_id (uuid4). This is fine for a single-instance MVP;
swap for Redis/DB-backed storage before scaling to multiple workers.
"""

from __future__ import annotations

import io
import json
import multiprocessing as mp
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# In-memory session store
# ---------------------------------------------------------------------------

@dataclass
class Session:
    id: str
    filename: str
    df: pd.DataFrame
    profile: dict
    history: list = field(default_factory=list)  # list of {question, code, insight}


SESSIONS: dict[str, Session] = {}

MAX_ROWS = 500_000
MAX_FILE_MB = 50


class DatasetError(Exception):
    pass


def load_file(filename: str, content: bytes) -> pd.DataFrame:
    """Parse an uploaded CSV/XLSX file into a DataFrame."""
    if len(content) > MAX_FILE_MB * 1024 * 1024:
        raise DatasetError(f"File exceeds the {MAX_FILE_MB}MB limit.")

    lower = filename.lower()
    try:
        if lower.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif lower.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise DatasetError("Unsupported file type. Please upload .csv, .xlsx, or .xls.")
    except DatasetError:
        raise
    except Exception as e:
        raise DatasetError(f"Could not parse file: {e}") from e

    if df.empty:
        raise DatasetError("The uploaded file has no rows.")
    if len(df) > MAX_ROWS:
        raise DatasetError(f"Dataset has {len(df)} rows; the current limit is {MAX_ROWS}.")

    # Normalize column names lightly (strip whitespace) without renaming semantics
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _infer_semantic_type(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    nunique = series.nunique(dropna=True)
    if nunique <= max(20, int(len(series) * 0.05)):
        return "categorical"
    return "text"


def profile_dataset(df: pd.DataFrame) -> dict:
    """Compute schema, stats, and a natural-language summary of the dataset."""
    columns = []
    for col in df.columns:
        s = df[col]
        sem_type = _infer_semantic_type(s)
        col_info = {
            "name": col,
            "dtype": str(s.dtype),
            "semantic_type": sem_type,
            "null_count": int(s.isna().sum()),
            "null_pct": round(float(s.isna().mean()) * 100, 1),
            "unique_count": int(s.nunique(dropna=True)),
        }
        if sem_type == "numeric":
            desc = s.describe()
            col_info.update({
                "min": _safe_float(desc.get("min")),
                "max": _safe_float(desc.get("max")),
                "mean": _safe_float(desc.get("mean")),
                "median": _safe_float(s.median()),
            })
        elif sem_type in ("categorical", "text"):
            top = s.value_counts(dropna=True).head(3)
            col_info["top_values"] = [{"value": str(k), "count": int(v)} for k, v in top.items()]
        columns.append(col_info)

    quality_flags = []
    for c in columns:
        if c["null_pct"] > 20:
            quality_flags.append(f"'{c['name']}' has {c['null_pct']}% missing values.")
    dup_count = int(df.duplicated().sum())
    if dup_count > 0:
        quality_flags.append(f"{dup_count} duplicate rows detected.")

    summary_text = _build_summary_text(df, columns)

    return {
        "n_rows": int(len(df)),
        "n_columns": int(len(df.columns)),
        "columns": columns,
        "quality_flags": quality_flags,
        "summary_text": summary_text,
        "sample_rows": json.loads(df.head(5).to_json(orient="records", date_format="iso")),
    }


def _safe_float(v):
    try:
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return None
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _build_summary_text(df: pd.DataFrame, columns: list[dict]) -> str:
    n_rows, n_cols = len(df), len(df.columns)
    numeric = [c["name"] for c in columns if c["semantic_type"] == "numeric"]
    dates = [c["name"] for c in columns if c["semantic_type"] == "datetime"]
    cats = [c["name"] for c in columns if c["semantic_type"] == "categorical"]

    parts = [f"This dataset has {n_rows:,} rows and {n_cols} columns."]
    if dates:
        parts.append(f"It includes a date/time column ({', '.join(dates[:2])}), suggesting time-series analysis is possible.")
    if numeric:
        parts.append(f"Numeric columns include {', '.join(numeric[:4])}{'…' if len(numeric) > 4 else ''}.")
    if cats:
        parts.append(f"Categorical columns include {', '.join(cats[:4])}{'…' if len(cats) > 4 else ''}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Sandboxed execution
# ---------------------------------------------------------------------------

SAFE_BUILTINS = {
    "len", "range", "min", "max", "sum", "sorted", "list", "dict", "set", "tuple",
    "abs", "round", "enumerate", "zip", "map", "filter", "str", "int", "float",
    "bool", "print", "True", "False", "None",
}

FORBIDDEN_TOKENS = (
    "import os", "import sys", "import subprocess", "import socket", "__import__",
    "open(", "eval(", "exec(", "compile(", "input(", "os.", "sys.", "subprocess.",
    "socket.", "shutil.", "pathlib.", "__builtins__", "globals(", "locals(",
)


def static_check(code: str) -> None:
    lowered = code.replace(" ", "")
    for token in FORBIDDEN_TOKENS:
        if token.replace(" ", "") in lowered:
            raise DatasetError(f"Generated code contains a disallowed operation: '{token.strip()}'.")


def _sandbox_worker(code: str, df_json: str, queue: mp.Queue):
    """Runs in a separate process with a restricted namespace."""
    try:
        import pandas as pd  # noqa: F401 (re-imported inside subprocess)
        import numpy as np  # noqa: F401
        import plotly.express as px  # noqa: F401

        df = pd.read_json(io.StringIO(df_json), orient="split")

        safe_builtins = {k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
                          for k in SAFE_BUILTINS if (k in __builtins__ if isinstance(__builtins__, dict) else hasattr(__builtins__, k))}

        local_ns: dict[str, Any] = {"df": df, "pd": pd, "np": np, "px": px}
        global_ns = {"__builtins__": safe_builtins}

        exec(code, global_ns, local_ns)  # noqa: S102 - sandboxed subprocess, restricted builtins

        result = local_ns.get("result")
        if result is None:
            queue.put({"ok": False, "error": "Code did not assign a `result` variable."})
            return

        if isinstance(result, pd.DataFrame):
            payload = {"kind": "dataframe", "data": json.loads(result.head(200).to_json(orient="split", date_format="iso"))}
        elif isinstance(result, pd.Series):
            payload = {"kind": "series", "data": json.loads(result.head(200).to_json(orient="split", date_format="iso"))}
        else:
            payload = {"kind": "scalar", "data": str(result)}

        queue.put({"ok": True, "result": payload})
    except Exception:
        queue.put({"ok": False, "error": traceback.format_exc(limit=3)})


def run_code_safely(code: str, df: pd.DataFrame, timeout: int = 15) -> dict:
    """Execute generated code in a sandboxed subprocess with a timeout."""
    static_check(code)

    df_json = df.to_json(orient="split", date_format="iso")
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_sandbox_worker, args=(code, df_json, queue))
    proc.start()
    proc.join(timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join()
        return {"ok": False, "error": f"Execution timed out after {timeout}s."}

    if queue.empty():
        return {"ok": False, "error": "Execution failed with no output (process crashed)."}

    return queue.get()


def new_session(filename: str, df: pd.DataFrame) -> Session:
    session_id = str(uuid.uuid4())
    profile = profile_dataset(df)
    session = Session(id=session_id, filename=filename, df=df, profile=profile)
    SESSIONS[session_id] = session
    return session


def get_session(session_id: str) -> Session:
    session = SESSIONS.get(session_id)
    if session is None:
        raise DatasetError("Session not found. Please upload the file again.")
    return session
