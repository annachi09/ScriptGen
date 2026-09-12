"""
AI-assisted SQL suggestions/optimization via Google's Gemini API
(free tier - see README for how to get a key from Google AI Studio).

Talks to the REST endpoint directly with `requests` rather than pulling
in the full google-generativeai SDK, to keep the dependency list (and
therefore the built .exe) small.

This module never executes anything against the database - it only
calls out to Gemini and returns text for the user to review.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import requests

_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

_SYSTEM_PREAMBLE = (
    "You are a T-SQL (Microsoft SQL Server) assistant embedded in a desktop "
    "database tool called ScriptGen. The user is connected with a READ-ONLY "
    "account, so never suggest DML/DDL as something to run automatically - "
    "only as text for the user to review. Be concise. When you suggest or "
    "rewrite a query, put the final SQL in a fenced ```sql code block so the "
    "app can extract it, and keep any explanation brief and above the code block."
)


@dataclass
class AIResponse:
    success: bool
    text: str
    extracted_sql: str = ""


def _extract_sql_block(text: str) -> str:
    marker = "```sql"
    start = text.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = text.find("```", start)
    if end == -1:
        return ""
    return text[start:end].strip()


def _call_gemini(api_key: str, model: str, prompt: str, timeout: int = 30) -> AIResponse:
    if not api_key:
        return AIResponse(success=False, text="No Gemini API key configured. Add one in Config > AI.")

    url = _ENDPOINT_TEMPLATE.format(model=model)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": _SYSTEM_PREAMBLE}]},
        # 1024 was the original cap; raised after the Single NISS "Analyze
        # with AI" context got noticeably richer (account/offered-service/
        # contract-status + a per-reading detail list, not just counts -
        # see date_anomaly_explain_ai's case_context) and responses started
        # getting cut off mid-sentence. The truncation was mistaken for a
        # UI/layout bug (".ai-output has no max-height/overflow set that
        # would clip it - checked") but it's actually Gemini's own output
        # hitting this hard token ceiling, same failure for every AI Assist
        # call this module makes (Suggest/Optimize/Explain/Review too, not
        # just Date Anomaly), so the fix is here, not in CSS.
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048},
    }

    try:
        resp = requests.post(
            url,
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return AIResponse(success=False, text=f"Network error calling Gemini: {exc}")

    if resp.status_code == 429:
        return AIResponse(success=False, text="Gemini free-tier rate limit hit. Wait a bit and try again.")
    if resp.status_code == 400 and "API key not valid" in resp.text:
        return AIResponse(success=False, text="Gemini API key looks invalid. Check Config > AI.")
    if resp.status_code != 200:
        return AIResponse(success=False, text=f"Gemini API error ({resp.status_code}): {resp.text[:300]}")

    try:
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            block_reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
            return AIResponse(success=False, text=f"Gemini returned no answer (reason: {block_reason}).")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
    except (ValueError, KeyError, IndexError) as exc:
        return AIResponse(success=False, text=f"Couldn't parse Gemini response: {exc}")

    return AIResponse(success=True, text=text, extracted_sql=_extract_sql_block(text))


def suggest_snippet(api_key: str, model: str, current_sql: str, intent: str, schema_context: str = "") -> AIResponse:
    """
    intent: free-text description of what the user wants next, e.g.
            "add a filter for orders in the last 30 days".
    """
    prompt_parts = [
        "The user is editing this T-SQL query in ScriptGen:",
        "```sql",
        current_sql or "-- (empty query)",
        "```",
    ]
    if schema_context.strip():
        prompt_parts += ["Known schema context:", schema_context.strip()]
    prompt_parts += [
        f"Request: {intent}",
        "Suggest the SQL snippet or full query that accomplishes this. "
        "Return the complete updated query in a ```sql block.",
    ]
    return _call_gemini(api_key, model, "\n\n".join(prompt_parts))


def explain_query(api_key: str, model: str, current_sql: str, schema_context: str = "") -> AIResponse:
    """Plain-English explanation of what a query does - no SQL rewrite expected,
    so callers shouldn't rely on extracted_sql being set for this one."""
    prompt_parts = [
        "Explain in plain English what this T-SQL query does, for someone who "
        "may not read SQL fluently. Cover: which table(s) it reads, what it "
        "filters/joins on, what it returns, and anything that looks risky or "
        "unusual (e.g. no WHERE clause, an unbounded result, an ambiguous join). "
        "Keep it to a short paragraph plus a few bullet points - no fenced code "
        "block is needed since nothing here should be run as-is.",
        "```sql",
        current_sql or "-- (empty query)",
        "```",
    ]
    if schema_context.strip():
        prompt_parts += ["Known schema context:", schema_context.strip()]
    return _call_gemini(api_key, model, "\n\n".join(prompt_parts))


def suggest_where_clause(api_key: str, model: str, columns: list[str], request: str, table_context: str = "") -> AIResponse:
    """
    Natural-language -> a WHERE clause only (not a full query), for
    dropping into a query you're already writing. Asks for JUST the
    clause body (no leading "WHERE", no trailing semicolon) so it can be
    pasted straight after a WHERE keyword; still wrapped in a ```sql
    fence so _extract_sql_block can pull it out the same way as the
    other AI actions.
    """
    prompt_parts = [
        "Write a T-SQL WHERE clause (Microsoft SQL Server syntax) for the request "
        "below. Return ONLY the clause body - no leading 'WHERE' keyword, no "
        "trailing semicolon, no full SELECT/UPDATE statement - in a ```sql code "
        "block, since the caller will paste it directly after their own WHERE.",
        f"Available columns: {', '.join(columns) if columns else '(unknown - ask the user to run a query first)'}",
    ]
    if table_context.strip():
        prompt_parts.append(f"Table: {table_context.strip()}")
    prompt_parts.append(f"Request: {request}")
    return _call_gemini(api_key, model, "\n\n".join(prompt_parts))


def review_script(api_key: str, model: str, script_sql: str) -> AIResponse:
    """
    A second, independent pass over an ALREADY-GENERATED update/rollback
    script (not the source query) specifically looking for risk signals
    a human reviewer might skim past: a WHERE clause on a nullable or
    non-unique-looking column, a suspiciously broad match, a type-looking
    mismatch between the SET value and the column name, etc. This is a
    sanity check, not a substitute for the human review this whole app is
    built around - the prompt says so explicitly so the model doesn't
    imply otherwise.
    """
    prompt_parts = [
        "You are doing a pre-flight risk review of a SQL Server UPDATE script "
        "that a human is about to review and run manually (this tool never runs "
        "it automatically). Look specifically for: WHERE clauses that might not "
        "uniquely identify a row, any 'no primary key' warning already in the "
        "script, SET values that look like the wrong type for the column name, "
        "and anything else that looks like it could update more rows than "
        "intended. Be concise - a short verdict line (e.g. 'Looks safe' / "
        "'Review before running' / 'High risk') followed by a few bullet points "
        "at most. This is a second opinion, not a substitute for the human's "
        "own review.",
        "```sql",
        script_sql or "-- (empty script)",
        "```",
    ]
    return _call_gemini(api_key, model, "\n\n".join(prompt_parts))


def explain_anomaly_finding(api_key: str, model: str, row_context: str) -> AIResponse:
    """
    Explains a single row from the Date Anomaly page's "Detect All Pending
    Anomalies" scan (web/server.py's /api/date-anomaly/detect-all/explain
    route) - deliberately single-row, not a summary across a selection,
    since the point is to help the analyst sanity-check ONE specific
    finding before including it in a bulk cleanup script. row_context is
    a plain-text block the caller builds from the row's own fields
    (account, supply/NISS, offered service, contract status, anomaly
    type/status descriptions, item status) - this function only shapes
    the prompt around it, it doesn't know the app's data model.
    """
    prompt_parts = [
        "You are helping a billing/anomaly analyst understand ONE flagged row from a "
        "bulk 'Diff Date System' anomaly cleanup tool (SQL Server, read-only connection - "
        "nothing here executes automatically). The tool's own bulk-cleanup action, if the "
        "analyst runs it for this row, only does two things: advances "
        "GCCOM_ITEMS_TO_BILL.STATUS from STTOBILL00 to STTOBILL01, and cancels the open "
        "GCCOM_ANOMALOUS record. It does NOT touch READING_PREV_DATE, INI_DATE, or "
        "XML_TO_BILL - those need the separate Detect/Resolve/Generate flow first if they "
        "still need fixing.",
        "Explain in plain English what this specific finding likely means given its "
        "account/supply/service/status context, whether the bulk-cleanup action described "
        "above looks appropriate for it, and flag anything about this particular row that "
        "looks unusual or worth double-checking before including it in a bulk run (e.g. an "
        "unexpected contract status, a missing account/supply/service value, or a status "
        "combination that doesn't fit the normal pattern). This is a second opinion for the "
        "analyst's own review, not an instruction to act - keep it to a short paragraph plus "
        "a few bullet points at most.",
        "Row data:",
        row_context.strip() or "(no data provided)",
    ]
    return _call_gemini(api_key, model, "\n\n".join(prompt_parts))


def explain_single_niss_case(api_key: str, model: str, case_context: str) -> AIResponse:
    """
    AI second opinion for the Date Anomaly page's "1-3. Single NISS" flow -
    distinct from date_anomaly.build_case_explanation, which is a
    deterministic, non-AI plain-language summary generated automatically
    from the detected rows (no API key needed, always available). This
    function is the analyst-triggered "Analyze with AI" button: same idea
    as explain_anomaly_finding but shaped around one NISS's full
    Detect+Resolve context (billing period floor, anomalous readings, the
    resolved item-to-bill/XML links) rather than one Detect-All row.
    """
    prompt_parts = [
        "You are helping a billing/anomaly analyst review ONE NISS (supply point) "
        "investigated through a 'Diff Date System' anomaly tool (SQL Server, read-only "
        "connection - nothing here executes automatically). The tool's correction script, "
        "if the analyst generates and runs it: fixes READING_PREV_DATE on the anomalous "
        "reading(s); downstream advances GCCOM_ITEMS_TO_BILL.STATUS and cancels the open "
        "GCCOM_ANOMALOUS record for the linked item(s)/XML rows; and, separately, for any "
        "anomalous reading whose reading type is the 'non-cycle' TIPTL00011 type, deletes "
        "its GCCOM_READINGS_ITEMSTOBILL link and resets its READ_STATUS back to 1000STSRED "
        "(with IND_USAGE_TO_CAL cleared) so the normal billing cycle can reprocess it.",
        "Explain in plain English what this NISS's findings likely mean - use the account, "
        "offered service, and per-reading billing period/date detail below to ground the "
        "explanation in this specific case rather than speaking only in counts. Say whether "
        "generating the correction script looks appropriate given the data below, and flag "
        "anything worth double-checking first (e.g. an unusually high anomaly count, a "
        "reading that didn't resolve to any item-to-bill row, missing audit columns, a "
        "contract status that looks inactive/unexpected, or a mix of reading types/dates "
        "that doesn't fit a normal pattern). This is a second opinion for the analyst's own "
        "review, not an instruction to act - keep it to a short paragraph plus a few bullet "
        "points at most.",
        "Case data:",
        case_context.strip() or "(no data provided)",
    ]
    return _call_gemini(api_key, model, "\n\n".join(prompt_parts))


def explain_batch_run(api_key: str, model: str, batch_context: str) -> AIResponse:
    """
    AI second opinion for the Date Anomaly page's "Batch" flow, once a
    multi-NISS run finishes - summarizes the run as a whole (how many
    NISS succeeded/failed and why, any pattern across the failures)
    rather than any single NISS, since batch has no per-row selection the
    way Detect All does. batch_context is a plain-text block the caller
    builds from the job's per-NISS results (see web/server.py's batch
    routes / web/batch_jobs.py) - this function only shapes the prompt.
    """
    prompt_parts = [
        "You are helping a billing/anomaly analyst review the results of a BATCH run "
        "across multiple NISS (supply points) through a 'Diff Date System' anomaly tool "
        "(SQL Server, read-only connection - nothing here executes automatically). Each "
        "NISS in the batch was independently run through Detect -> Resolve -> Generate; "
        "one NISS failing (no anomalies found, no correctly-billed reading, connection "
        "error) does not stop the rest.",
        "Summarize this batch run in plain English: how many NISS succeeded vs. had no "
        "action needed vs. failed/warned, whether there's a pattern worth the analyst's "
        "attention across the failures or warnings (e.g. the same missing audit column "
        "repeating, a cluster of connection errors), and anything that looks worth "
        "double-checking before running the combined script. This is a second opinion for "
        "the analyst's own review, not an instruction to act - keep it to a short paragraph "
        "plus a few bullet points at most.",
        "Batch results:",
        batch_context.strip() or "(no data provided)",
    ]
    return _call_gemini(api_key, model, "\n\n".join(prompt_parts))


def test_api_key(api_key: str, model: str, timeout: int = 15) -> AIResponse:
    """Cheapest possible round-trip to confirm a key/model actually works,
    used by the 'Test Key' button so a typo is caught before it wastes a
    real Suggest/Optimize/Explain call."""
    if not api_key:
        return AIResponse(success=False, text="No API key entered.")
    return _call_gemini(api_key, model, "Reply with exactly one word: OK", timeout=timeout)


def optimize_query(api_key: str, model: str, current_sql: str, schema_context: str = "") -> AIResponse:
    prompt_parts = [
        "Review and optimize this T-SQL query for Microsoft SQL Server "
        "(readability and performance - e.g. sargable predicates, avoiding "
        "SELECT *, appropriate indexing hints as comments only, unnecessary "
        "subqueries/cursors). Keep the query's original result semantics.",
        "```sql",
        current_sql or "-- (empty query)",
        "```",
    ]
    if schema_context.strip():
        prompt_parts += ["Known schema context:", schema_context.strip()]
    prompt_parts.append("Return the optimized query in a ```sql block, with a short bullet list of what changed.")
    return _call_gemini(api_key, model, "\n\n".join(prompt_parts))
