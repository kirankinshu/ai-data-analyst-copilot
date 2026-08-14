"""
report.py
---------
Assembles a session's Q&A history into a downloadable Excel report:
  - Summary sheet (dataset profile + all Q&A pairs with insights)
  - One sheet per query result table
"""

from __future__ import annotations

import io

import pandas as pd

from dataframe import Session


def build_excel_report(session: Session) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        # Summary sheet
        summary_rows = [
            {"Field": "Dataset", "Value": session.filename},
            {"Field": "Rows", "Value": session.profile["n_rows"]},
            {"Field": "Columns", "Value": session.profile["n_columns"]},
            {"Field": "Description", "Value": session.profile["summary_text"]},
        ]
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Summary", index=False)

        qa_rows = []
        for i, turn in enumerate(session.history, start=1):
            qa_rows.append({
                "#": i,
                "Question": turn["question"],
                "Insight": turn.get("insight", ""),
                "Code": turn.get("code", ""),
            })
        if qa_rows:
            pd.DataFrame(qa_rows).to_excel(writer, sheet_name="Q&A Log", index=False)

        # One sheet per result table (only for dataframe-shaped results)
        for i, turn in enumerate(session.history, start=1):
            result = turn.get("result")
            if result and result.get("kind") == "dataframe":
                d = result["data"]
                df = pd.DataFrame(d["data"], columns=d["columns"])
                sheet = f"Q{i}_Result"[:31]
                df.to_excel(writer, sheet_name=sheet, index=False)

    buffer.seek(0)
    return buffer.read()
