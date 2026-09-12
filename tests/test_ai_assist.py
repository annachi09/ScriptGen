"""
Pure-logic tests for app.core.ai_assist that don't require network access -
covers the "no key configured" / "no key entered" short-circuits that
_call_gemini takes before ever making an HTTP request, plus the SQL-block
extraction helper. Anything that actually calls the Gemini API is out of
scope for an offline test suite. suggest_where_clause (AI Assist's
NL-to-WHERE builder) and review_script (the Script page's "AI Review"
button) both route through the same _call_gemini short-circuit, so their
coverage here is the same no-key path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.ai_assist import explain_query, suggest_where_clause, review_script, _extract_sql_block
# Aliased on import: pytest collects any module-level `test_*` callable in a
# test file as a test case, including ones merely imported (not defined)
# here - importing ai_assist.test_api_key under its real name made pytest
# try to run IT as a test (and fail, since it requires real arguments).
from app.core.ai_assist import test_api_key as ai_test_api_key


def test_explain_query_without_key_short_circuits():
    response = explain_query("", "gemini-3.6-flash", "SELECT * FROM Accounts")
    assert response.success is False
    assert "No Gemini API key configured" in response.text


def test_test_api_key_without_key_short_circuits():
    response = ai_test_api_key("", "gemini-3.6-flash")
    assert response.success is False
    assert response.text == "No API key entered."


def test_suggest_where_clause_without_key_short_circuits():
    response = suggest_where_clause("", "gemini-3.6-flash", ["id", "status"], "active rows only")
    assert response.success is False
    assert "No Gemini API key configured" in response.text


def test_review_script_without_key_short_circuits():
    response = review_script("", "gemini-3.6-flash", "UPDATE dbo.Accounts SET status = 1 WHERE id = 1;")
    assert response.success is False
    assert "No Gemini API key configured" in response.text


def test_extract_sql_block_found():
    text = "Here you go:\n```sql\nSELECT 1;\n```\nDone."
    assert _extract_sql_block(text) == "SELECT 1;"


def test_extract_sql_block_missing_returns_empty():
    assert _extract_sql_block("no code block here") == ""


def test_call_gemini_sends_raised_max_output_tokens(monkeypatch):
    # Regression test for the "AI message is cut off" bug: it turned out
    # to be Gemini's own maxOutputTokens cap (originally 1024), not a CSS
    # truncation issue - raised to 2048 in app.core.ai_assist._call_gemini.
    # Mocks requests.post entirely (no real network call) purely to
    # inspect the JSON payload this module builds.
    import json as _json

    import app.core.ai_assist as ai_assist

    captured = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    def fake_post(url, params, headers, data, timeout):
        captured["payload"] = _json.loads(data)
        return _FakeResponse()

    monkeypatch.setattr(ai_assist.requests, "post", fake_post)
    response = explain_query("fake-key", "gemini-3.6-flash", "SELECT * FROM Accounts")
    assert response.success is True
    assert captured["payload"]["generationConfig"]["maxOutputTokens"] == 2048


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
