"""
prompts.py
----------
Builds the prompts sent to Gemini for:
  1. Translating a natural-language question into Pandas code
  2. Writing a plain-English insight from a query result
"""

import json


CODE_SYSTEM_INSTRUCTIONS = """You are a senior data analyst that writes short, correct Pandas code.

Rules:
- You are given a pandas DataFrame already loaded as `df`.
- Available names: df, pd (pandas), np (numpy), px (plotly.express). Nothing else is importable.
- Write the MINIMUM code needed to answer the question.
- Always assign your final answer to a variable named `result`.
  `result` must be a pandas DataFrame, a pandas Series, or a simple scalar (number/string).
- Do NOT read/write files, access the network, or use eval/exec/import/os/sys/subprocess.
- Do NOT redefine `df`'s original data destructively; derive new variables instead.
- Return ONLY a fenced Python code block. No prose, no explanation outside the block.
"""


def build_code_prompt(schema_summary: str, question: str, history: list[dict]) -> str:
    history_block = ""
    if history:
        recent = history[-3:]
        lines = []
        for turn in recent:
            lines.append(f"- Q: {turn['question']}\n  Code used: {turn['code'].strip()[:300]}")
        history_block = "Recent conversation context:\n" + "\n".join(lines) + "\n\n"

    return f"""{CODE_SYSTEM_INSTRUCTIONS}

Dataset schema:
{schema_summary}

{history_block}User question: "{question}"

Write the Pandas code now.
"""


def build_insight_prompt(question: str, code: str, result_preview: str) -> str:
    return f"""You are a data analyst summarizing a query result for a non-technical business user.

User question: "{question}"

Code that was run:
{code}

Result (preview):
{result_preview}

Write a concise, plain-English insight (2-4 sentences) that directly answers the user's question
using the numbers in the result. Be specific with figures. Do not mention pandas, code, or dataframes.
Do not repeat the raw table back verbatim; interpret it.
"""


def schema_to_text(profile: dict) -> str:
    lines = [f"Rows: {profile['n_rows']}, Columns: {profile['n_columns']}"]
    for c in profile["columns"]:
        bits = [f"{c['name']} ({c['semantic_type']}, dtype={c['dtype']}, nulls={c['null_pct']}%)"]
        if c["semantic_type"] == "numeric" and c.get("mean") is not None:
            bits.append(f"range=[{c.get('min')}, {c.get('max')}], mean={c.get('mean')}")
        if c.get("top_values"):
            tv = ", ".join(f"{t['value']}({t['count']})" for t in c["top_values"])
            bits.append(f"top values: {tv}")
        lines.append(" - " + " | ".join(bits))
    return "\n".join(lines)
