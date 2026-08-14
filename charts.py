"""
charts.py
---------
Turns a query result (DataFrame / Series / scalar) into a Plotly figure
(returned as JSON so the React frontend can render it with react-plotly.js
or Plotly.js directly), auto-selecting a sensible chart type.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def build_chart(payload: dict, question: str = "") -> dict | None:
    """payload is the sandbox result: {'kind': 'dataframe'|'series'|'scalar', 'data': ...}"""
    kind = payload.get("kind")

    if kind == "scalar":
        return None  # no chart for a single number; shown as a stat instead

    if kind == "series":
        s = pd.Series(payload["data"]["data"], index=payload["data"]["index"], name=payload["data"].get("name") or "value")
        df = s.reset_index()
        df.columns = ["category", "value"]
        return _chart_from_two_cols(df, "category", "value", question)

    if kind == "dataframe":
        d = payload["data"]
        df = pd.DataFrame(d["data"], columns=d["columns"])
        return _chart_from_dataframe(df, question)

    return None


def _chart_from_dataframe(df: pd.DataFrame, question: str) -> dict | None:
    if df.empty or len(df.columns) < 1:
        return None

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    non_numeric_cols = [c for c in df.columns if c not in numeric_cols]
    datetime_like = [c for c in df.columns if _looks_like_date(df[c])]

    # Time series: a date-like column + a numeric column -> line chart
    if datetime_like and numeric_cols:
        x, y = datetime_like[0], numeric_cols[0]
        fig = px.line(df.sort_values(x), x=x, y=y, markers=True)
        return _finalize(fig)

    # One categorical + one numeric -> bar chart (or pie if small cardinality & "share"/"percentage" asked)
    if non_numeric_cols and numeric_cols:
        x, y = non_numeric_cols[0], numeric_cols[0]
        subset = df[[x, y]].head(25)
        if len(subset) <= 6 and any(w in question.lower() for w in ["share", "percentage", "proportion", "%", "breakdown"]):
            fig = px.pie(subset, names=x, values=y)
        else:
            fig = px.bar(subset, x=x, y=y)
        return _finalize(fig)

    # Two numeric columns -> scatter
    if len(numeric_cols) >= 2:
        fig = px.scatter(df.head(500), x=numeric_cols[0], y=numeric_cols[1])
        return _finalize(fig)

    # Single numeric column -> histogram
    if len(numeric_cols) == 1:
        fig = px.histogram(df.head(1000), x=numeric_cols[0])
        return _finalize(fig)

    return None


def _chart_from_two_cols(df: pd.DataFrame, x: str, y: str, question: str) -> dict | None:
    if df.empty:
        return None
    subset = df.head(25)
    if len(subset) <= 6 and any(w in question.lower() for w in ["share", "percentage", "proportion", "%"]):
        fig = px.pie(subset, names=x, values=y)
    else:
        fig = px.bar(subset, x=x, y=y)
    return _finalize(fig)


def _looks_like_date(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if series.dtype == object:
        try:
            pd.to_datetime(series.dropna().head(5))
            return True
        except Exception:
            return False
    return False


def _finalize(fig: go.Figure) -> dict:
    import json as _json

    fig.update_layout(
        template="plotly_white",
        margin=dict(l=30, r=20, t=30, b=30),
        font=dict(family="Inter, Calibri, sans-serif", size=13),
        colorway=["#0B1F3A", "#14B8A6", "#FF6B57", "#16335C", "#0E9488"],
    )
    # Use Plotly's own JSON encoder (handles numpy/pandas types), then load back
    # into a plain dict so FastAPI's jsonable_encoder can serialize it safely.
    return _json.loads(fig.to_json())
