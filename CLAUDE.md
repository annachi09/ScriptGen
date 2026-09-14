# Working with RJ on ScriptGen

Read this before touching anything in this repo. It documents how RJ actually
works, not generic best practice — deviate from it and the round will need
redoing.

## The standing habit: review the OUC schema first

Before writing a query, a filter, or a status/type constant that touches any
`GCCOM_*`, `GCCB_*`, or `GCGT_*` table, check `docs/db_schema_reference.md`
first. It's a cached snapshot (table list, FK graph, full column dump) built
from `INFORMATION_SCHEMA`/`sys.foreign_keys` so this doesn't need re-querying
every round. If the reference doesn't have what's needed (a lookup table's
code values, an index, a row-count estimate), confirm it LIVE against the
real tunnel DB (see below) — never guess a status code or column name. This
is the single most consistent thing RJ asks for across rounds, whether or not
he says it that turn.

## Investigate live before writing code

RJ gives a business rule in his own words, often with a real account number
("check account 1059650711…", "account 342702..."). The pattern every round:

1. Pull that account's actual rows live via the app's own DB access — never
   invent what the data probably looks like.
2. Find the real mechanism behind the symptom (e.g. `END_DATE` carrying a
   non-midnight time-of-day while `BILLING_DATE` is always midnight — found
   by comparing raw rows, not by guessing).
3. Quantify how widespread it is (a % over a real sample) before deciding
   whether it's a one-off or a systemic bug worth a general fix.
4. Before committing to a fix, actively try to disprove the obvious
   alternative explanation (e.g. the "Rate lags a period" hypothesis was
   tested against 14 other accounts and rejected) rather than pattern-
   matching to an existing rule and moving on.
5. Report real before/after counts from the live data, not "should be
   fixed."

There is no local test DB and no mocking. Every `app/core/*.py` query is
validated by actually running it (or the raw SQL equivalent) against the
tunnel DB via the app's own `/api/query/run` endpoint, from a browser session
already authenticated into the running app. `mcp__workspace__bash` /
pytest are NOT used for SQL validation — the shell sandbox can't reach the
tunnel DB, only the app's own Windows process can.

## Code shape

- `app/core/*.py` modules are pure logic: they build SQL text and never
  execute anything themselves. `web/server.py` (or the desktop app) is the
  only thing that calls `app/db/mssql.py` to actually run a query.
- `run_web.py` runs uvicorn WITHOUT `reload=True`. Any Python change needs a
  manual process restart before it's live — static HTML/CSS/JS serves fresh
  every request, but `.py` changes don't take effect until restarted. The
  `/api/server-info` route (returns `{started_at, pid}`) confirms whether a
  restart actually picked up the change.
- RJ gives explicit permission to optimize a query he supplies, as long as
  the result is provably identical — always state the equivalence reasoning
  (e.g. "GROUP BY already partitions by period with no cross-period
  contamination, so widening one hardcoded period to an IN(...) list is
  the same as unioning one run per period") rather than just asserting it.
- SQL literals go through `format_sql_literal` — it must emit plain
  (non-`N'...'`) string literals for varchar columns, or comparisons
  silently lose their index via an implicit `CONVERT()`. This has bitten
  a real round before; don't reintroduce `N'...'`.
- A date-range comparison on a possibly-non-midnight datetime column should
  leave the OTHER side of the join (the always-midnight column, e.g.
  `BILLING_DATE`) un-wrapped — `CAST()` only the side that needs it, so any
  index on the untouched column stays sargable.

## Documentation convention

Every round that changes real behavior gets three write-ups, in order:
1. A dated comment block in the `app/core/*.py` module itself (RJ's own
   words quoted where he gave one), explaining the business rule and why —
   this is the primary record, since the next round's investigation starts
   by reading it.
2. A dated `## ...` section appended to `README.md`.
3. A dated round entry appended to this project's local memory file
   (`scriptgen_architecture.md`, NOT `mcp__memory` — a separate, project-
   local file-based memory system), plus an updated one-line pointer in
   `MEMORY.md`.

Skip none of the three, even for a "just a query fix" round — the module
comment in particular is what keeps the next round from re-discovering the
same bug from scratch.

## Bill Issuance Validator: Case 4 "Unclassified" must reflect every case change

RJ, 2026-09-14, after New Contract Match (a same-tab addition to Case 1) shipped without this:
"was this new rule considered in case for unclassified?" It hadn't been — a real gap that shipped
and had to be caught reactively.

Case 4's `bill_issuance_case4_detect` (in `web/server.py`) doesn't re-derive Case 1/2/3's own
business rules — it calls each case's own query builder directly (unlimited, `limit=None`) and
unions their account-ID sets into `excluded_ids`, so "Unclassified" only shows accounts none of the
other cases already explain. **This exclusion list does not update itself.** Any change to what
Case 1, 2, or 3 detect — a new sub-pattern (like New Contract Match), a tightened filter, a new
numbered Case — needs its account set manually added to (or adjusted in) `excluded_ids`, or
Unclassified silently starts showing accounts another case already covers (or stops excluding ones
it shouldn't).

**Standing rule: before finishing any round that changes Bill Issuance Validator detection logic
in any case (1, 2, 3, or a future one), explicitly check whether Case 4's exclusion list needs the
same change — and say so even if the answer is "no change needed," rather than leaving it for RJ
to ask.** This applies to every kind of change: a new detection pattern, a tightened/loosened
filter on an existing one, or a bug fix that changes which accounts a case matches.

## Communication style

RJ's messages are terse, dense, and often arrive as one run-on paragraph
covering several asks at once — read the whole thing before starting, since
the ordering of sub-asks in the message is usually the intended execution
order (e.g. "do X, then once done, do Y" really does mean sequence, not
"pick whichever"). He gives exact literal SQL when he has it — translate it
faithfully first, confirm the semantics live, and only then optimize. He
also drops real account numbers/IDs as the actual spec for a bug fix — those
are test cases to live-verify against, not illustrative examples to
paraphrase.
