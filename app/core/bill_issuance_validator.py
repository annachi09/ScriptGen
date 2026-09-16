"""
Bill Issuance Validator: finds accounts where a "Rate" bill (ID_OFFERED_
SERVICE 176) still being put to collection is blocking the account's next
regular Electricity/Water bill from being issued.

Background (RJ, 2026-09-15, own words + own SQL). RJ's own starting query:

    select distinct pf.id_payment_form, pf.reference, nt.update_date
    from gccom_notice_tmp nt
    join gccom_bill b on b.id_bill = nt.id_bill
    join gccom_payment_form pf on pf.id_payment_form = b.id_payment_form
    where nt.cod_status = '5000NOTEMP'
      and b.billing_status = 'ESTFAC0012'
    order by nt.update_date;

- every payment form (account) with a pending notice (GCCOM_NOTICE_TMP.
COD_STATUS = 5000NOTEMP) for a bill still "in process of being put to
collection" (GCCOM_BILL.BILLING_STATUS = ESTFAC0012, confirmed live via
`SELECT * FROM GCCOM_BILL_STATUS`: "En proceso de puesta al cobro").

RJ's own further instructions, verbatim: "base on this, I wan to get all
records where it has only 1 recored for the billing period and the
id_offered_service is 176, and using the same id_payment_form, check the
table gccom_bill with id_offered_service = 1 or 19 and the billing_period
is 1 more than our bill with 176 id_offered service, next the status of
the bill on the + 1 id_billing_period should be ESTFAC0015" - i.e., among
those accounts:

  1. Narrow to the ones where that account's ID_BILLING_PERIOD has EXACTLY
     ONE GCCOM_BILL row, and that lone row's ID_OFFERED_SERVICE is 176.
     176 = "Rate" (confirmed live via GCCOM_COMPANY_OFFERED_SERVICE).
  2. For the SAME ID_PAYMENT_FORM, look up GCCOM_BILL for the NEXT billing
     period (ID_BILLING_PERIOD + 1) where ID_OFFERED_SERVICE IN (1, 19) -
     1 = Electricity, 19 = Water (also confirmed live, same lookup table).
  3. Keep only the ones where that next-period bill's BILLING_STATUS is
     ESTFAC0015 - confirmed live via GCCOM_BILL_STATUS: "En espera de
     otros servicios" (waiting for other services). This status is the
     whole point of the tool: it's the system's own literal "I'm stuck
     waiting on another service's bill" flag, and the pattern this query
     surfaces is exactly that dependency - the account's Rate (176) bill
     hasn't finished being issued yet, so the very next period's regular
     Electricity/Water bill can't move past ESTFAC0015.

RJ, 2026-09-15, follow-up: "i dont want duplicates, if you find ele, stop
otherwise if it is not found check water" - an account whose next period
has BOTH Electricity and Water stuck at ESTFAC0015 was showing up as TWO
rows; RJ wants ONE row per account, preferring Electricity (1) when it's
itself stuck, falling back to Water (19) only when Electricity ISN'T one
of the stuck rows. Note this is "found stuck", not "found any bill" - an
Electricity bill that exists but already moved past ESTFAC0015 simply
never enters the ESTFAC0015-filtered set to begin with, so it doesn't
block falling through to Water; only an ELECTRICITY ROW THAT IS ITSELF
STUCK takes priority over one for Water. Implemented as a ROW_NUMBER()
PARTITION BY account/period, ORDER BY Electricity-first, keep RN = 1 -
see the `matched` CTE below.

Confirmed live against the tunnel DB (2026-09-15): of ~3800 payment forms
matching RJ's own starting query, ~880 have exactly one bill in their
period and it's a Rate (176) bill, and (after the Electricity-first
dedupe above) ~593 rows - one per distinct account, no duplicates -
match the full chain (576 Electricity-preferred, 17 fell back to Water
because Electricity wasn't itself stuck). Exact counts drift slightly
run to run since this is live production data, not a fixture - the
SHAPE (one row per account, Electricity-first) is what's guaranteed, not
an exact count.

Deliberately pure logic, same convention as every other app/core module:
this only builds SELECT query text; it never executes anything itself.
The caller (web/server.py) runs it via app.db.mssql.run_query.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Iterable

from .script_generator import DEFAULT_AUDIT_PROGRAM, DEFAULT_AUDIT_USER
from .sql_format import format_sql_literal, quote_ident

# --- Schema / table names -------------------------------------------------
# NOTICE_TABLE lives in OUC_ADMIN; BILL/PAYMENT_FORM/OFFERED_SERVICE/
# BILL_STATUS live in OUC_COMMON_ADMIN / OUC_ADMIN as confirmed live via
# INFORMATION_SCHEMA.COLUMNS and the earlier GCCOM_BILL_STATUS query -
# same split this app already has elsewhere (e.g. date_anomaly.py's
# READING_SCHEMA vs. ADMIN_SCHEMA).
NOTICE_SCHEMA = "OUC_ADMIN"
NOTICE_TABLE = "GCCOM_NOTICE_TMP"

# Same schema/table date_anomaly.py's own ADMIN_SCHEMA/ITEMS_TO_BILL_TABLE
# use - confirmed live (INFORMATION_SCHEMA.COLUMNS) it has its own
# ID_BILL/ID_BILLING_PERIOD/UPDATE_DATE/UPDATE_PROGRAM/UPDATE_USER columns,
# same shape as GCCOM_BILL's own audit columns - used by Case 2's own
# "Generate Update Script" (see build_terminated_period_fix_script).
ITEMS_TO_BILL_SCHEMA = "OUC_ADMIN"
ITEMS_TO_BILL_TABLE = "GCCOM_ITEMS_TO_BILL"

BILL_SCHEMA = "OUC_COMMON_ADMIN"
BILL_TABLE = "GCCOM_BILL"
PAYMENT_FORM_TABLE = "GCCOM_PAYMENT_FORM"
OFFERED_SERVICE_TABLE = "GCCOM_COMPANY_OFFERED_SERVICE"

BILL_STATUS_SCHEMA = "OUC_ADMIN"
BILL_STATUS_TABLE = "GCCOM_BILL_STATUS"
BILL_STATUS_LOOKUP_KEY_COLUMN = "COD_DEVELOP"
BILL_STATUS_LOOKUP_DESC_COLUMN = "NAME_TYPE"
OFFERED_SERVICE_LOOKUP_DESC_COLUMN = "NAME_TYPE"

# --- Fixed business constants (RJ's own codes, confirmed live) -----------
NOTICE_STATUS_PENDING = "5000NOTEMP"
# ESTFAC0012 "En proceso de puesta al cobro" (in process of being issued).
BILL_STATUS_ISSUING = "ESTFAC0012"
# ESTFAC0015 "En espera de otros servicios" (waiting for other services) -
# the literal "stuck on a dependency" status this tool surfaces.
BILL_STATUS_WAITING_OTHER_SERVICES = "ESTFAC0015"

# 176 = "Rate" (GCCOM_COMPANY_OFFERED_SERVICE.NAME_TYPE, confirmed live).
OFFERED_SERVICE_RATE = 176
# 1 = Electricity, 19 = Water - the two "regular" services RJ named.
OFFERED_SERVICE_ELECTRICITY = 1
OFFERED_SERVICE_WATER = 19
NEXT_PERIOD_OFFERED_SERVICES = (OFFERED_SERVICE_ELECTRICITY, OFFERED_SERVICE_WATER)

BILL_ISSUANCE_DEFAULT_LIMIT = 2000

# RJ, 2026-09-14, own words: "enhancing Case 1, I found cases that the
# next water or ele bills can be up to 11 billing period ahead of the
# rate bills, and they are valid. So can you include them if the next
# bill of water and rate is up to 11 billing period ahead". The original
# query only ever checked PERIOD_RATE + 1 (the immediate next period) -
# live-confirmed this round that's now finding ZERO accounts against the
# real current data, while widening to PERIOD_RATE+1..PERIOD_RATE+11
# finds 74, spread across 2 (47 accounts), 3 (12), 4 (9), and a long tail
# out to 10 periods ahead. In other words the tool had gone essentially
# blind - real operations can leave Water/Electricity lagging the Rate
# bill by several months, not just one, and every one of those is a
# genuine ESTFAC0015 "waiting for other services" bill, the exact same
# defining status the whole tool is keyed off - widening the period
# range doesn't relax what counts as "stuck", it just stops assuming the
# lag is always exactly 1 period.
NEXT_PERIOD_MAX_AHEAD_DEFAULT = 11

# --- Case 2: terminated-account billing-period mismatch ------------------
# RJ, 2026-09-15, own words, first cut of the business rule: accounts
# where EVERY GCCOM_CONTRACTED_SERVICE row is Terminated, but the
# account's bills disagree on ID_BILLING_PERIOD. RJ's own 342702 example:
# 3 such bills, 2 at period 236 (Water, Sanitary) and 1 at period 237
# (Electricity) - the 4th (Rate) bill turned out to already exist, just
# already ESTFAC0005 "Puesta al cobro" (issued), at period 236.
#
# RJ, 2026-09-16, REDESIGN (verbatim): "mostly this will be the final
# bill for each service, so we need to be sure that all service has
# bills in invoicing status, having the same billing date as the
# termination date, or the bill is in status invoiced but also have the
# same termination date as the billing date of gccom_bill. I need a
# filter for this cases where the bill is already invoiced and having
# same termiantion date." Plus two follow-up AskUserQuestion answers:
# termination date = GCCOM_CONTRACTED_SERVICE.END_DATE (confirmed live -
# matches real bills' BILLING_DATE exactly for a real terminated
# service, unlike DROP_DATE which trails END_DATE by ~1 day and never
# matches a bill), and target period = MAX(ID_BILLING_PERIOD) among the
# account's own matched final bills (same as the original rule, just
# now computed from the termination-date-matched set instead of the
# NOTICE_TMP/ESTFAC0012 "pending" set). Third answer, RJ's own words:
# "the idea is i only want to see the accounts, maybe its a drill down
# to show the bills... the filter is for me to know the cases where it
# is complete only that the other service bills are already invoiced" -
# i.e. group by account (one row per account, drill down to services),
# with a filter for "Complete" (every service's final bill already
# invoiced and aligned) vs. "Needs action".
#
# So per terminated service: find its OWN final bill - the one row in
# GCCOM_BILL whose BILLING_DATE equals that service's END_DATE - and
# require it be either ESTFAC0012 (invoicing) or ESTFAC0005 (invoiced).
# No matching bill at all is itself a NEEDS_UPDATE case (nothing to
# align, but worth flagging - see build_terminated_period_mismatch_query
# docstring for the exact NEEDS_UPDATE rule). Target period is the MAX
# ID_BILLING_PERIOD among an account's matched final bills; a service
# not already at that period needs its ID_BILLING_PERIOD moved there.
#
# RJ, 2026-09-17 (later same day), verbatim: "we are only checking where
# the accounts exists in gccom_notic_tmp where it is in status pending
# validation". Confirmed live via OUC_ADMIN.GCCOM_NOTICE_TMP_STATUS:
# COD_DEVELOP 5000NOTEMP = NAME_TYPE "For validation" - the exact same
# "pending" notice status Case 1's build_stuck_bills_query already keys
# off (NOTICE_STATUS_PENDING). GCCOM_NOTICE_TMP has its own ID_PAYMENT_
# FORM column (confirmed live via INFORMATION_SCHEMA.COLUMNS) and a
# supporting index, IDX_GCCOM_NOTICE_TMP_02 on (ID_PAYMENT_FORM, COD_
# STATUS) (confirmed live via sys.indexes).
#
# RJ, same day, real-data correction: account 342... no, account with
# REFERENCE 1076362704 (ID_PAYMENT_FORM 3134) was showing up even though,
# RJ's own words, "there is no bill in notice tmp with status pending
# valudation and bill is in status invoicing". Investigated live: this
# account DOES have a GCCOM_NOTICE_TMP row with COD_STATUS = 5000NOTEMP -
# but that notice's own linked bill (nt.ID_BILL -> GCCOM_BILL.ID_BILL,
# 1072467931) is BILLING_STATUS = ESTFAC0007 ("Anulada" / voided), not
# ESTFAC0012 ("En proceso de puesta al cobro" / invoicing). A pending-
# validation notice pointing at a bill that's since been voided isn't a
# live "someone needs to review this" signal - the same combined pattern
# Case 1's own build_stuck_bills_query already requires (notice pending
# AND that notice's bill = ESTFAC0012), just not carried over here in
# the first pass. Fixed by joining notice_tmp to GCCOM_BILL on ID_BILL
# and requiring the bill still be BILL_STATUS_ISSUING (ESTFAC0012), not
# just checking the notice status in isolation. GCCOM_NOTICE_TMP has a
# supporting index on ID_BILL too (IDX_GCCOM_NOTICE_TMP_03, confirmed
# live via sys.indexes). Confirmed live (60-day window): narrows further
# from 1,335 accounts (notice-status-only) to 1,064 (notice + bill still
# invoicing) - and account 3134's own match count against the tightened
# EXISTS is 0, confirming the fix. This is the real scoping rule RJ
# wants, not a cosmetic filter: most terminated accounts have nothing
# genuinely pending review at all, and the account-grouped table's whole
# job is to show only the ones an analyst actually needs to look at.
#
# RJ, 2026-09-13, real-data correction #2: account REFERENCE 1059650711
# (ID_PAYMENT_FORM 49734), own words: "check account 1059650711, it has
# all the service bill on the termination date, by the way we need cycle
# bills TFGEN0001 something in bill_type of gccom_bill, the only issue is
# that it is in a different billing period and its already issued, so
# this account should be marked ok, then tagged as complete with other
# bill invoiced in another billing period." Investigated live: two real
# issues. (1) This account's Rate/176 service has END_DATE = 2026-08-30
# 19:45:30 - a non-midnight timestamp - while GCCOM_BILL.BILLING_DATE is
# always midnight, so the old exact `b.BILLING_DATE = ts.END_DATE` join
# could never match its real final bill at all (confirmed this isn't a
# one-off: ~59% of Rate/176 terminations in a 60-day sample carry a
# non-midnight END_DATE). Fixed by comparing DATE only (a >= / < DATEADD
# range on the un-wrapped BILLING_DATE column, not a CAST() on it, so an
# index on BILLING_DATE stays usable - same N'...'-literal-style sargability
# lesson as the earlier performance fix above). (2) Once date-matched, that
# service's real final bill (period 236) turned out to already be
# BILL_STATUS_INVOICED (ESTFAC0005) while the account's other 3 services
# landed at period 237 (still BILL_STATUS_ISSUING/ESTFAC0012) - a genuine
# period disagreement, but RJ's own call is that an ALREADY-invoiced bill
# doesn't need to be flagged over it: the bill's gone out, "correcting"
# its period now would be pointless (and the generated UPDATE script could
# never safely target it anyway). NEEDS_UPDATE now only fires on a period
# mismatch when the bill is still ESTFAC0012 (not yet finalized); an
# already-ESTFAC0005 bill in a different period is accepted as-is. Also
# added BILL_TYPE = BILL_TYPE_CYCLE (TFGEN00001, "Contracted Service Bill"
# per OUC_ADMIN.GCCOM_GENERABLE_BILL_TYPE, confirmed live) to the final-
# bill match per RJ's explicit ask, so a one-off charge bill (deposit,
# reconnection fee, etc. - the ~13 other TFGEN1xxxx types) sharing the
# termination date can't be mistaken for the real final bill - a defensive
# correctness fix, not something real data has shown causing a wrong
# match yet (every final bill inspected so far was already TFGEN00001).
#
# GCCOM_CONTRACTED_SERVICE lives in OUC_COMMON_ADMIN (confirmed live via
# INFORMATION_SCHEMA.COLUMNS) - same schema as BILL_SCHEMA above.
CONTRACTED_SERVICE_TABLE = "GCCOM_CONTRACTED_SERVICE"
CONTRACTED_SERVICE_PK_COLUMN = "ID_CONTRACTED_SERVICE"
CONTRACTED_SERVICE_STATUS_COLUMN = "STATUS"
CONTRACTED_SERVICE_END_DATE_COLUMN = "END_DATE"
# ESTSC00004 "Baja" (Terminated) - confirmed live via GCCOM_CONTRACT_SERV_
# STATUS and cross-checked against a real terminated account's services.
TERMINATED_SERVICE_STATUS = "ESTSC00004"
# ESTFAC0005 "Puesta al cobro" (issued/invoiced) - confirmed live via
# GCCOM_BILL_STATUS, same lookup table BILL_STATUS_ISSUING (ESTFAC0012)
# already uses above. A terminated service's final bill counts as
# "already handled" only once it reaches this status AT the termination
# date - see CONTRACTED_SERVICE_END_DATE_COLUMN comment above.
BILL_STATUS_INVOICED = "ESTFAC0005"
FINAL_BILL_STATUSES = (BILL_STATUS_ISSUING, BILL_STATUS_INVOICED)

# TFGEN00001 "Contracted Service Bill" (GCCOM_BILL.BILL_TYPE, confirmed
# live via OUC_ADMIN.GCCOM_GENERABLE_BILL_TYPE) - the regular/cycle bill
# type tied to a contracted service, as opposed to the ~13 other TFGEN1xxxx
# one-off charge types (deposits, reconnection fees, misc charges, etc.)
# that share GCCOM_BILL. RJ, 2026-09-13, own words: "we need cycle bills
# TFGEN0001 something in bill_type of gccom_bill" - requiring this on the
# final-bill match keeps a one-off charge bill that happens to land on a
# service's termination date from being mistaken for its real final bill.
BILL_TYPE_CYCLE = "TFGEN00001"

# How far back to look for terminated services by default. RJ's business
# process only ever reviews recent terminations, and this also keeps the
# query fast: GCCOM_CONTRACTED_SERVICE has ~4.7M rows total, ~3M of them
# already Terminated (confirmed live 2026-09-17), so scanning "every
# terminated service ever" is both irrelevant to the analyst and needless
# extra work once scoped by a real date. 60 days keeps the default result
# set to a size an analyst can actually review in one page (a 6-month
# window returned ~94K service rows / 27K accounts live - correct, but
# far more than anyone would page through) while staying well inside
# TERMINATED_PERIOD_DEFAULT_LIMIT's row cap so a normal scan isn't
# truncated mid-account. RJ can widen it via the UI/API `days_back` param
# when they need to look further back.
TERMINATED_PERIOD_LOOKBACK_DAYS_DEFAULT = 60

# GCCOM_BILL's own audit columns (confirmed live via INFORMATION_SCHEMA.
# COLUMNS: CREATE_DATE, UPDATE_DATE, UPDATE_USER, UPDATE_PROGRAM,
# OPTIMIST_LOCK) - uppercase here (unlike date_anomaly.py's lowercase
# AUDIT_*_COL) to match this module's own column-naming convention
# (ID_BILLING_PERIOD, BILLING_STATUS, etc. are all uppercase throughout).
BILL_AUDIT_PROGRAM_COL = "UPDATE_PROGRAM"
BILL_AUDIT_DATE_COL = "UPDATE_DATE"
BILL_AUDIT_USER_COL = "UPDATE_USER"

TERMINATED_PERIOD_DEFAULT_LIMIT = 8000

# --- Case 3: All Contract Status - Bills Complete -------------------------
# RJ, 2026-09-14, own words + own verbatim SQL: "for the third case of bill
# issuance validator we will call it 'All Contract Status - Bills Complete'
# ... here is the query to detect them, you can always optimize, provided
# that it will give us the same result". RJ's own starting query (one
# hardcoded period, 10000000237):
#
#   SELECT pf.reference, cs.ID_PAYMENT_FORM, cs.CONTRACTED_SERVICES,
#          ISNULL(b.BILLS, 0) AS BILLS,
#          cs.CONTRACTED_SERVICES - ISNULL(b.BILLS, 0) AS MISSING_BILLS,
#          b.id_billing_period,
#          case when exists(select 1 from gccom_contracted_service r
#                 where r.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
#                   and r.STATUS in ('ESTSC00002','ESTSC00007','ESTSC00003'))
#               then 'YES' else 'NO' end as WITH_ACTIVE_CONTRACT
#   FROM (SELECT ID_PAYMENT_FORM, COUNT(*) AS CONTRACTED_SERVICES
#         FROM GCCOM_CONTRACTED_SERVICE
#         WHERE STATUS in ('ESTSC00002','ESTSC00007','ESTSC00003','ESTSC00004')
#         GROUP BY ID_PAYMENT_FORM) cs
#   JOIN (SELECT ID_PAYMENT_FORM, COUNT(*) AS BILLS, b.ID_BILLING_PERIOD
#         FROM OUC_COMMON_ADMIN.GCCOM_BILL B
#         WHERE B.ID_BILLING_PERIOD = 10000000237
#           and B.BILLING_STATUS = 'ESTFAC0012' and b.BILL_TYPE = 'TFGEN00001'
#         GROUP BY B.ID_PAYMENT_FORM, b.ID_BILLING_PERIOD) b
#     ON cs.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
#   JOIN gccom_payment_form pf on pf.id_Payment_form = cs.ID_PAYMENT_FORM
#   WHERE cs.CONTRACTED_SERVICES = ISNULL(b.BILLS, 0)
#     and cs.ID_PAYMENT_FORM in (
#       select distinct pf1.id_payment_form from gccom_payment_form pf1
#       join gccom_bill b on b.id_payment_form = pf1.id_payment_form
#       join gccom_notice_tmp tmp on tmp.id_bill = b.id_bill
#       where b.billing_status = 'ESTFAC0012' and tmp.cod_status = '5000NOTEMP'
#         and tmp.update_date < getdate() - 0)
#
# What this finds: per account, the count of ALL its contracted services
# across every non-deleted status (Vigente/Active, Suspendido/Suspended,
# "Baja pendiente de facturar"/Terminating, Baja/Terminated - confirmed
# live via GCCOM_CONTRACT_SERV_STATUS) versus the count of its cycle bills
# (BILL_TYPE = TFGEN00001, same constant Case 2 uses) still invoicing
# (BILLING_STATUS = ESTFAC0012) for ONE billing period - when the two
# counts are EXACTLY EQUAL, every one of that account's services already
# has its bill issuing for that period; nothing's missing, hence "Bills
# Complete". Scoped (via the ID_PAYMENT_FORM IN subquery) to only accounts
# that also have a pending notice on an issuing bill - same NOTICE_TMP/
# ESTFAC0012 "something's actually pending review" gate Case 1 and Case 2
# both already use (`getdate() - 0` is a literal no-op, same as
# `< GETDATE()`, kept here only because that's what the WHERE needs to be
# true rather than any real "as-of" semantics).
#
# RJ, same message, verbatim: "repeat this for all the billing period of
# 2026 from gccom_billing_period, it is very important that you use the
# billing period 1 by 1 to obtain the correct result." Confirmed live via
# GCCOM_BILLING_PERIOD: 2026 is exactly 12 periods, IDs 10000000229-
# 10000000240, one per calendar month, PERIOD_ORDER 1-12, no gaps/overlap.
# Rather than hardcode those 12 IDs (or literally loop/UNION the query 12
# times), build_bills_complete_query widens the single WHERE B.ID_BILLING_
# PERIOD = 10000000237 filter to B.ID_BILLING_PERIOD IN (<every 2026
# period, from a live subquery against GCCOM_BILLING_PERIOD itself - not a
# hardcoded list>), keeping the bill subquery's own GROUP BY ID_PAYMENT_
# FORM, ID_BILLING_PERIOD unchanged. This is mathematically identical to
# running the original query once per period and UNIONing the results:
# GROUP BY already partitions bill counts strictly by (account, period)
# with no cross-period contamination, and GCCOM_CONTRACTED_SERVICE (the
# `cs` side) carries no period column at all, so widening the `b` side's
# period filter can only ever add more (account, period) combinations to
# check, never change what "equal" means for any one of them. Verified
# live (2026-09-14): a literal translation of RJ's own query shape
# (WHERE ... IN (2026 periods), same GROUP BY) and this module's optimized
# CTE rewrite (below) return byte-for-byte identical row sets against the
# real tunnel DB.
#
# Optimization (RJ's own explicit permission: "you can always optimize,
# provided that it will give us the same result"): RJ's own `cs` subquery
# aggregates GCCOM_CONTRACTED_SERVICE with NO account scoping first -
# confirmed live, that's 4,620,339 rows (most of the table) before any
# join narrows it down. Since the final result can only ever include
# accounts that already passed the pending-notice gate AND have a
# matching period bill (confirmed live: 2,960 accounts, in a table with
# millions of contracted-service rows), this module computes the
# pending-notice account set and the per-period bill counts FIRST, then
# aggregates GCCOM_CONTRACTED_SERVICE only for that already-narrow account
# list - same "filter before you aggregate a huge table" lesson as Case
# 2's own account_status CTE. IX_GCCOM_CONTRACTED_SERVICE_STATUS
# (ID_PAYMENT_FORM, STATUS) and IX_GCCOM_BILL_BILL_TYPE_BILLING_STATUS /
# IX_GCCOM_BILL_ID_PAYMENT_FORM_BILL_TYPE (both confirmed live via
# sys.indexes) make both narrowed lookups index seeks instead of scans.
CONTRACT_STATUS_ACTIVE = "ESTSC00002"       # Vigente
CONTRACT_STATUS_SUSPENDED = "ESTSC00007"    # Suspendido
CONTRACT_STATUS_TERMINATING = "ESTSC00003"  # Baja pendiente de facturar
# CONTRACT_STATUS_TERMINATED reuses TERMINATED_SERVICE_STATUS (ESTSC00004,
# "Baja") already defined above for Case 2 - same code, same lookup table.
ALL_CONTRACT_STATUSES = (
    CONTRACT_STATUS_ACTIVE, CONTRACT_STATUS_SUSPENDED,
    CONTRACT_STATUS_TERMINATING, TERMINATED_SERVICE_STATUS,
)
# WITH_ACTIVE_CONTRACT's own status set - RJ's CASE expression, everything
# in ALL_CONTRACT_STATUSES except Terminated.
ACTIVE_CONTRACT_STATUSES = (CONTRACT_STATUS_ACTIVE, CONTRACT_STATUS_SUSPENDED, CONTRACT_STATUS_TERMINATING)

BILLING_PERIOD_TABLE = "GCCOM_BILLING_PERIOD"  # OUC_COMMON_ADMIN, confirmed live
BILLING_PERIOD_ID_COLUMN = "ID_BILLING_PERIOD"
BILLING_PERIOD_NAME_COLUMN = "PERIOD_NAME"
BILLING_PERIOD_YEAR_COLUMN = "INITIAL_DATE"  # YEAR(INITIAL_DATE) - confirmed live, 12 rows/year, no dedicated YEAR column

ALL_CONTRACT_STATUS_DEFAULT_YEAR = 2026
ALL_CONTRACT_STATUS_DEFAULT_LIMIT = 5000

# --- Case 4: Unclassified (pending validation, not caught by any other
# Case) -----------------------------------------------------------------
# RJ, 2026-09-14 (same day as the Case 2 filter-split and Case 1
# widening rounds), own words: "create a 4th case, 'Unclassified' those
# that are pending validation in notice TMP, and not in case 1, case 2,
# case 3, and any other case that we will add in the future. Make it
# look like case 2, where there is a drill down on the bills and just
# showing the accounts on the row and option to copy and export to
# excel."
#
# Design decision: rather than re-encode Case 1/2/3's own business rules
# a second time here as exclusion subqueries (which would silently drift
# the moment any of those cases' own logic changes - exactly the "not in
# case 1, case 2, case 3, and any other case we will add in the future"
# correctness requirement RJ asked for), this module only builds the
# general "pending validation" universe and its per-account bill detail.
# The actual "not in Case 1/2/3" computation happens in web/server.py's
# case4/detect route: it calls build_stuck_bills_query, build_terminated_
# period_mismatch_query, and build_bills_complete_query directly
# (unlimited, i.e. limit=None) to get each Case's own real account-ID
# set, then excludes any account already in one of those sets from what
# build_unclassified_query returns - so Case 4 can never disagree with
# what Case 1/2/3 actually flag, and a future Case 5 only needs its own
# account set added to that same exclusion list, no change here.
#
# RJ, 2026-09-14 (later same day, asked directly): "was this new rule
# considered in case for unclassified?" - referring to Case 1's own New
# Contract Match addition (build_new_contract_match_query, above). It
# was NOT - that query was added to the Case 1 tab as a same-tab addition
# (not a new numbered Case), and web/server.py's case4/detect route was
# never updated to also exclude its account set, so every account it
# matched (656 live at the time this was caught) was silently still
# showing up as "Unclassified" even though Case 1 now explains it. Fixed
# in web/server.py by adding build_new_contract_match_query as a fifth
# exclusion source alongside Case 1/2/3 - this is exactly the "any other
# case that we will add in the future" scenario this design comment
# already anticipated, it just didn't automatically cover a same-tab,
# non-numbered addition. Takeaway for any future Case 1-tab addition:
# it still needs its own account set added to web/server.py's case4/
# detect exclusion list by hand, same as a brand-new numbered Case would.
#
# Live-confirmed (2026-09-14) account-set sizes going into this design:
# 2,940 total pending-validation accounts; 74 in Case 1 (unlimited);
# 645 in Case 2 (unlimited/all-time, days_back=None); 2 in Case 3
# (year=2026). Each of the three unlimited queries ran in ~1-1.6s live.
UNCLASSIFIED_DEFAULT_LIMIT = 8000


def build_unclassified_query(limit: int | None = UNCLASSIFIED_DEFAULT_LIMIT) -> str:
    """
    Case 4's detection query - see the module-level Case 4 comment block
    above for RJ's own request and the "call the other Cases' own query
    builders, don't re-derive their rules" design this implements.

    `pending_accounts` - the exact same "something's pending review" gate
    Case 1's own `candidate` CTE, Case 2's own terminated_services EXISTS,
    and Case 3's own `pending_accounts` CTE all already use: an account
    with a GCCOM_NOTICE_TMP row (COD_STATUS = 5000NOTEMP, "For
    validation") whose own linked bill is still BILLING_STATUS = ESTFAC0012
    ("En proceso de puesta al cobro"). Live-confirmed (2026-09-14): 2,940
    accounts.

    Final SELECT - one row per (account, bill) for every one of that
    account's CURRENTLY-ISSUING bills (BILLING_STATUS = ESTFAC0012),
    regardless of BILL_TYPE or ID_OFFERED_SERVICE, so the frontend's
    drill-down shows the account's full "what's actually pending right
    now" picture rather than guessing which single bill matters - same
    "show everything, let the analyst read it" stance Case 2's own
    per-service drill-down already takes. LEFT JOINs for human-readable
    offered-service/billing-status descriptions, same defensive pattern
    every other lookup JOIN in this app uses.

    `limit` behaves the same as every other Case's own `limit` - a
    TOP (N) cap on the per-bill ROW count, pass None to disable. Note
    this caps rows BEFORE the caller's Case 1/2/3 exclusion and before
    accounts are grouped, so a small limit could under-represent the true
    Unclassified account count; the route calls this unlimited (None) for
    the same reason it calls the other three Cases' own builders
    unlimited - see the module comment above.
    """
    bill_tbl = _qualified(BILL_SCHEMA, BILL_TABLE)
    pf_tbl = _qualified(BILL_SCHEMA, PAYMENT_FORM_TABLE)
    notice_tbl = _qualified(NOTICE_SCHEMA, NOTICE_TABLE)
    offered_service_tbl = _qualified(BILL_SCHEMA, OFFERED_SERVICE_TABLE)
    bill_status_tbl = _qualified(BILL_STATUS_SCHEMA, BILL_STATUS_TABLE)
    top_clause = f"TOP ({int(limit)}) " if limit else ""
    return (
        f"WITH pending_accounts AS (\n"
        f"  SELECT DISTINCT pf1.ID_PAYMENT_FORM\n"
        f"  FROM {pf_tbl} pf1\n"
        f"  JOIN {bill_tbl} b ON b.ID_PAYMENT_FORM = pf1.ID_PAYMENT_FORM\n"
        f"  JOIN {notice_tbl} tmp ON tmp.ID_BILL = b.ID_BILL\n"
        f"  WHERE b.BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"    AND tmp.COD_STATUS = {format_sql_literal(NOTICE_STATUS_PENDING)}\n"
        f")\n"
        f"SELECT {top_clause}pf.REFERENCE, pa.ID_PAYMENT_FORM,\n"
        f"  b.ID_BILL, b.ID_BILLING_PERIOD, b.BILL_TYPE,\n"
        f"  b.ID_OFFERED_SERVICE, os.{OFFERED_SERVICE_LOOKUP_DESC_COLUMN} AS OFFERED_SERVICE_DESC,\n"
        f"  b.BILLING_STATUS, bs.{BILL_STATUS_LOOKUP_DESC_COLUMN} AS BILLING_STATUS_DESC,\n"
        f"  b.BILLING_DATE\n"
        f"FROM pending_accounts pa\n"
        f"JOIN {bill_tbl} b ON b.ID_PAYMENT_FORM = pa.ID_PAYMENT_FORM\n"
        f"  AND b.BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"JOIN {pf_tbl} pf ON pf.ID_PAYMENT_FORM = pa.ID_PAYMENT_FORM\n"
        f"LEFT JOIN {offered_service_tbl} os ON os.ID_OFFERED_SERVICE = b.ID_OFFERED_SERVICE\n"
        f"LEFT JOIN {bill_status_tbl} bs ON bs.{BILL_STATUS_LOOKUP_KEY_COLUMN} = b.BILLING_STATUS\n"
        f"ORDER BY pf.REFERENCE, b.ID_BILLING_PERIOD;"
    )


def _qualified(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}" if schema else quote_ident(table)


def build_stuck_bills_query(
    limit: int | None = BILL_ISSUANCE_DEFAULT_LIMIT,
    *,
    max_periods_ahead: int = NEXT_PERIOD_MAX_AHEAD_DEFAULT,
) -> str:
    """
    The full chain from the module docstring, as one query:

    `period_counts` - one row per (ID_PAYMENT_FORM, ID_BILLING_PERIOD)
    with how many GCCOM_BILL rows share it, so `candidate` can require
    exactly 1. RJ, 2026-09-14 (investigating why account REFERENCE
    1100068871 wasn't detected): "we only check TFGEN0001 and in status
    INVOICING for case 1" - this used to count EVERY bill in the period
    regardless of type/status, so a one-off charge bill (a deposit, fee,
    etc. - BILL_TYPE other than TFGEN00001) or an already-invoiced/voided
    old cycle bill sharing the Rate bill's period would wrongly push the
    count to 2+ and disqualify a genuine match. Investigated live:
    account 1100068871's Rate/176 bill (period 236, ID_BILL 1073563109)
    shares that period with a Deposito bill (BILL_TYPE TFGEN10006, ID_BILL
    1073296305, already ESTFAC0005/invoiced) - unrelated to the cycle-
    billing dependency this tool is actually about, but it was counted
    anyway. `period_counts` now only counts bills that are BOTH
    BILL_TYPE_CYCLE (TFGEN00001) AND BILL_STATUS_ISSUING (ESTFAC0012) -
    same distinction Case 2/3 already learned to make (see BILL_TYPE_CYCLE's
    own comment) for the same reason, just never carried back into Case 1
    when it was built earlier. Live-confirmed before this fix: 1,714
    candidate accounts (pending notice + issuing Rate/176 bill) were
    disqualified purely by a coexisting non-cycle bill sharing their Rate
    bill's period (counting by BILL_TYPE alone, before adding the STATUS
    condition too); scoping period_counts to cycle+still-invoicing bills
    only raised Case 1's own end-to-end unlimited match count from 74 to
    1,272 in that same live check. 1100068871 itself is confirmed matched
    under this fix (its real next-period Electricity bill, ID_BILL
    1074731158 at period 237, genuinely is ESTFAC0015) - live-confirmed
    end-to-end through the real /api/bill-issuance/detect route (not just
    raw SQL) after this exact fix (BILL_TYPE + STATUS both scoped) shipped
    and the server restarted: Case 1's match count went from 74 to 1,359
    (well under BILL_ISSUANCE_DEFAULT_LIMIT/2000, so not truncated), and
    1100068871 is in that result with exactly the expected fields.

    `candidate` - RJ's own starting query (notice pending + bill issuing),
    narrowed to accounts whose Rate (176) bill is the ONLY cycle bill
    still invoicing in its billing period.

    `matched` - joins GCCOM_BILL again for the SAME account's next billing
    period(s), restricted to Electricity/Water (1, 19), keeping only rows
    still BILLING_STATUS = ESTFAC0015. RJ, 2026-09-14: "the next water or
    ele bills can be up to 11 billing period ahead of the rate bills, and
    they are valid" - this used to require exactly PERIOD_RATE + 1; now
    it's a range, PERIOD_RATE + 1 .. PERIOD_RATE + max_periods_ahead (see
    NEXT_PERIOD_MAX_AHEAD_DEFAULT's own comment for the live numbers that
    justified this - the exact-next-period version currently finds ZERO
    accounts against real data, the widened one finds 74). PERIODS_AHEAD
    (NEXT period minus PERIOD_RATE) is carried through to the final
    SELECT so the UI can show and filter on it. A ROW_NUMBER()
    PARTITIONed by (ID_PAYMENT_FORM, PERIOD_RATE), ORDERed by PERIODS_
    AHEAD ascending (closest period first) then Electricity (1) before
    Water (19) as a tiebreak for same-period ties, keeping only RN = 1 in
    the final SELECT, is the "no duplicates - Electricity first, Water
    only if Electricity isn't stuck" rule RJ asked for (2026-09-15),
    generalized to the wider range: an account with more than one
    matching period now surfaces its CLOSEST one, not an arbitrary one.

    Final SELECT - LEFT JOINs to GCCOM_COMPANY_OFFERED_SERVICE and
    GCCOM_BILL_STATUS purely for human-readable descriptions (service
    name, status name) - LEFT so a row still surfaces even if a
    description happens to be missing, same defensive pattern every other
    lookup JOIN in this app uses.

    `limit` adds a `TOP (N)` cap (no NISS/id scoping here, same reasoning
    as build_detect_all_anomalies_query's own `limit` - this is a system-
    wide scan). Pass None to disable the cap entirely. `max_periods_ahead`
    defaults to RJ's own confirmed number (11) but is left as a real
    parameter so a future round (or the UI) can widen/narrow it without a
    code change - see NEXT_PERIOD_MAX_AHEAD_DEFAULT.
    """
    notice_tbl = _qualified(NOTICE_SCHEMA, NOTICE_TABLE)
    bill_tbl = _qualified(BILL_SCHEMA, BILL_TABLE)
    pf_tbl = _qualified(BILL_SCHEMA, PAYMENT_FORM_TABLE)
    offered_service_tbl = _qualified(BILL_SCHEMA, OFFERED_SERVICE_TABLE)
    bill_status_tbl = _qualified(BILL_STATUS_SCHEMA, BILL_STATUS_TABLE)
    next_services_list = ", ".join(format_sql_literal(s) for s in NEXT_PERIOD_OFFERED_SERVICES)
    top_clause = f"TOP ({int(limit)}) " if limit else ""
    return (
        f"WITH period_counts AS (\n"
        f"  SELECT ID_PAYMENT_FORM, ID_BILLING_PERIOD, COUNT(*) AS BILL_COUNT\n"
        f"  FROM {bill_tbl}\n"
        f"  WHERE BILL_TYPE = {format_sql_literal(BILL_TYPE_CYCLE)}\n"
        f"    AND BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"  GROUP BY ID_PAYMENT_FORM, ID_BILLING_PERIOD\n"
        f"),\n"
        f"candidate AS (\n"
        f"  SELECT DISTINCT pf.ID_PAYMENT_FORM, pf.REFERENCE, nt.UPDATE_DATE AS NOTICE_UPDATE_DATE,\n"
        f"    b.ID_BILL AS ID_BILL_RATE, b.ID_BILLING_PERIOD AS PERIOD_RATE\n"
        f"  FROM {notice_tbl} nt\n"
        f"  JOIN {bill_tbl} b ON b.ID_BILL = nt.ID_BILL\n"
        f"  JOIN {pf_tbl} pf ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM\n"
        f"  JOIN period_counts pc ON pc.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM\n"
        f"    AND pc.ID_BILLING_PERIOD = b.ID_BILLING_PERIOD\n"
        f"  WHERE nt.COD_STATUS = {format_sql_literal(NOTICE_STATUS_PENDING)}\n"
        f"    AND b.BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"    AND b.ID_OFFERED_SERVICE = {format_sql_literal(OFFERED_SERVICE_RATE)}\n"
        f"    AND pc.BILL_COUNT = 1\n"
        f"),\n"
        f"matched AS (\n"
        f"  SELECT c.ID_PAYMENT_FORM, c.REFERENCE, c.NOTICE_UPDATE_DATE,\n"
        f"    c.ID_BILL_RATE, c.PERIOD_RATE,\n"
        f"    nb.ID_BILL AS ID_BILL_NEXT, nb.ID_OFFERED_SERVICE AS OFFERED_SERVICE_NEXT,\n"
        f"    nb.ID_BILLING_PERIOD AS PERIOD_NEXT, nb.BILLING_STATUS AS STATUS_NEXT,\n"
        f"    nb.ID_BILLING_PERIOD - c.PERIOD_RATE AS PERIODS_AHEAD,\n"
        f"    ROW_NUMBER() OVER (\n"
        f"      PARTITION BY c.ID_PAYMENT_FORM, c.PERIOD_RATE\n"
        f"      ORDER BY nb.ID_BILLING_PERIOD ASC,\n"
        f"        CASE WHEN nb.ID_OFFERED_SERVICE = {format_sql_literal(OFFERED_SERVICE_ELECTRICITY)} THEN 0 ELSE 1 END\n"
        f"    ) AS RN\n"
        f"  FROM candidate c\n"
        f"  JOIN {bill_tbl} nb ON nb.ID_PAYMENT_FORM = c.ID_PAYMENT_FORM\n"
        f"    AND nb.ID_BILLING_PERIOD BETWEEN c.PERIOD_RATE + 1 AND c.PERIOD_RATE + {int(max_periods_ahead)}\n"
        f"    AND nb.ID_OFFERED_SERVICE IN ({next_services_list})\n"
        f"  WHERE nb.BILLING_STATUS = {format_sql_literal(BILL_STATUS_WAITING_OTHER_SERVICES)}\n"
        f")\n"
        f"SELECT {top_clause}m.ID_PAYMENT_FORM, m.REFERENCE, m.NOTICE_UPDATE_DATE,\n"
        f"  m.ID_BILL_RATE, m.PERIOD_RATE,\n"
        f"  m.ID_BILL_NEXT, m.OFFERED_SERVICE_NEXT,\n"
        f"  os.{OFFERED_SERVICE_LOOKUP_DESC_COLUMN} AS OFFERED_SERVICE_NEXT_DESC,\n"
        f"  m.PERIOD_NEXT, m.STATUS_NEXT, m.PERIODS_AHEAD,\n"
        f"  bs.{BILL_STATUS_LOOKUP_DESC_COLUMN} AS STATUS_NEXT_DESC\n"
        f"FROM matched m\n"
        f"LEFT JOIN {offered_service_tbl} os ON os.ID_OFFERED_SERVICE = m.OFFERED_SERVICE_NEXT\n"
        f"LEFT JOIN {bill_status_tbl} bs ON bs.{BILL_STATUS_LOOKUP_KEY_COLUMN} = m.STATUS_NEXT\n"
        f"WHERE m.RN = 1\n"
        f"ORDER BY m.NOTICE_UPDATE_DATE;"
    )


# --- Case 1: Generate Release Script ---------------------------------------
# RJ, 2026-09-14, own words + own exact SQL template: "for case 1, create
# script for all detected, 'Generate Release script' this is the script:
#
#   update gccom_notice_tmp set cod_status = '1000NOTEMP',
#     update_date = getdate(), update_user = 'RMA',
#     update_program = 'VALIDATION RELEASE_TERMINATED'
#   where id_notice_tmp in (
#     select nt.id_notice_tmp from gccom_notice_tmp nt
#     join gccom_bill b on b.id_bill = nt.id_bill
#     where nt.cod_status = '5000NOTEMP' and b.billing_status = 'ESTFAC0012'
#       and b.id_bill in ( <all the rate bills detected here> )
#   ) and cod_status = '5000NOTEMP';"
#
# Releases the pending-validation notice (GCCOM_NOTICE_TMP.COD_STATUS
# 5000NOTEMP "For validation" -> 1000NOTEMP "Pendiente de generar factura",
# both confirmed live via OUC_ADMIN.GCCOM_NOTICE_TMP_STATUS) for every
# Rate/176 bill Case 1 currently flags as the blocking bill - i.e. every
# ID_BILL_RATE build_stuck_bills_query returns. The caller (web/server.py)
# re-runs build_stuck_bills_query fresh right before Generate (same
# "re-verify at generate time" pattern every other Generate route in this
# app already uses) and passes every ID_BILL_RATE from that fresh result
# in as bill_ids.
NOTICE_STATUS_RELEASE = "1000NOTEMP"  # "Pendiente de generar factura"

# RJ's own literal audit values for this specific release action - unlike
# every other build_*_script function's DEFAULT_AUDIT_PROGRAM ("JIRAXXXX",
# a per-run Jira-ticket placeholder the analyst is expected to replace),
# RJ gave these as a fixed template for this one action, not a placeholder.
# Kept as real, overridable parameters anyway (same signature shape as
# every other build_*_script here), just defaulted to RJ's exact literals
# so the out-of-the-box script matches his template byte-for-byte.
RELEASE_SCRIPT_DEFAULT_PROGRAM = "VALIDATION RELEASE_TERMINATED"
RELEASE_SCRIPT_DEFAULT_USER = "RMA"


@dataclass
class ReleaseNoticeScript:
    """
    Result of build_release_notice_script - a single UPDATE statement that
    releases every selected bill's blocking notice at once (RJ's own
    template is ONE statement with an IN(...) list, not one statement per
    bill - unlike build_terminated_period_fix_script/build_cleanup_script's
    one-statement-per-id shape elsewhere in this app).
    """
    sql_text: str
    bill_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


def build_release_notice_script(
    bill_ids: Iterable[Any],
    *,
    program: str = RELEASE_SCRIPT_DEFAULT_PROGRAM,
    user: str = RELEASE_SCRIPT_DEFAULT_USER,
    clean: bool = False,
) -> ReleaseNoticeScript:
    """
    Builds Case 1's "Generate Release Script" button output - see the
    module-level comment block just above for RJ's own verbatim template
    and the exact business meaning of what this releases.

    bill_ids is deliberately the caller's OWN bill-id list (every currently
    detected ID_BILL_RATE, or a caller-narrowed subset of it), not something
    this function re-derives - so a future "release only the accounts I
    selected" UI option is a drop-in, not a redesign. Duplicates and None
    entries are dropped (order-preserving) before building the IN(...)
    list; an empty/all-None bill_ids produces a warning and a "nothing to
    release" placeholder script instead of a syntactically invalid empty
    IN() clause.

    Both WHERE COD_STATUS = '5000NOTEMP' guards from RJ's own template are
    kept verbatim - the OUTER one (after the closing paren) is what makes
    re-running this script against a notice that's already been released a
    safe no-op (same "only touch if still in the state we expect" guard
    convention as every other correction script in this app - build_
    terminated_period_fix_script's defensive WHERE, build_cleanup_script's
    Part A/B, build_correction_script's Part 3/5); the INNER one is what
    RJ's own subquery already needed to correctly join notice-to-bill in
    the first place (same NOTICE_STATUS_PENDING + BILL_STATUS_ISSUING gate
    build_stuck_bills_query's own `candidate` CTE uses).

    This only ever touches GCCOM_NOTICE_TMP - it does not change GCCOM_BILL
    itself (BILLING_STATUS, BILLING_DATE, etc. are untouched), matching
    RJ's own template exactly.
    """
    seen_ids: list[Any] = []
    seen_set: set[Any] = set()
    for bill_id in bill_ids:
        if bill_id is None or bill_id in seen_set:
            continue
        seen_set.add(bill_id)
        seen_ids.append(bill_id)

    notice_tbl = _qualified(NOTICE_SCHEMA, NOTICE_TABLE)
    bill_tbl = _qualified(BILL_SCHEMA, BILL_TABLE)
    warnings: list[str] = []

    header = [
        "-- Bill Issuance Validator: Case 1 RELEASE script - generated by ScriptGen",
        f"-- Generated (UTC): {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"-- Rate bills selected: {len(seen_ids)}",
        f"-- Program: {program}",
        f"-- User: {user}",
        "-- Releases the pending-validation notice (GCCOM_NOTICE_TMP.COD_STATUS",
        "-- 5000NOTEMP 'For validation' -> 1000NOTEMP 'Pendiente de generar",
        "-- factura') for each selected Rate bill's own blocking notice.",
        "-- Does NOT touch GCCOM_BILL itself - only GCCOM_NOTICE_TMP.",
    ]

    if not seen_ids:
        warnings.append("No Rate bills were selected - nothing to release.")
        header.append(f"-- WARNING: {warnings[0]}")
        header.append("-- Statements: 0")
        header.append("")
        sql_text = "\n".join(header) + "-- Nothing to release.\n"
        if clean:
            sql_text = _strip_sql_comments(sql_text)
        return ReleaseNoticeScript(sql_text=sql_text, bill_count=0, warnings=warnings)

    id_list = ", ".join(format_sql_literal(i) for i in seen_ids)
    set_clause = (
        f"{quote_ident('COD_STATUS')} = {format_sql_literal(NOTICE_STATUS_RELEASE)},\n"
        f"    {quote_ident('UPDATE_DATE')} = GETDATE(),\n"
        f"    {quote_ident('UPDATE_USER')} = {format_sql_literal(user)},\n"
        f"    {quote_ident('UPDATE_PROGRAM')} = {format_sql_literal(program)}"
    )
    stmt = (
        f"UPDATE {notice_tbl}\n"
        f"SET {set_clause}\n"
        f"WHERE ID_NOTICE_TMP IN (\n"
        f"  SELECT nt.ID_NOTICE_TMP\n"
        f"  FROM {notice_tbl} nt\n"
        f"  JOIN {bill_tbl} b ON b.ID_BILL = nt.ID_BILL\n"
        f"  WHERE nt.COD_STATUS = {format_sql_literal(NOTICE_STATUS_PENDING)}\n"
        f"    AND b.BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"    AND b.ID_BILL IN ({id_list})\n"
        f")\n"
        f"AND COD_STATUS = {format_sql_literal(NOTICE_STATUS_PENDING)};"
    )

    header.append("-- Statements: 1")
    header.append("")
    sql_text = "\n".join(header) + stmt + "\n\n-- Review the statement above before running it.\n"
    if clean:
        sql_text = _strip_sql_comments(sql_text)
    return ReleaseNoticeScript(sql_text=sql_text, bill_count=len(seen_ids), warnings=warnings)


# --- Case 1: New Contract Match (RJ, 2026-09-14, later same day) ---------
# RJ, verbatim (own SQL): "incorporate in case 1, the existing case 1 is ok,
# now i only want to add the case that its is only rate which is in
# pending validation notice_tmp and invoicing gccom_bill, the contract
# start (from_date) of gccom_contracted service is same as last_billing_
# date of gccom_bill, see this example query but i did not check that it
# is the only bill in pending validation":
#
#   select pf.reference, b.id_bill, b.billing_status, t.COD_STATUS,
#     b.LAST_BILLING_DATE, b.billing_date, cs.FROM_DATE, cs.STATUS, b.bill_type
#   from OUC_COMMON_ADMIN.gccom_bill b
#     join OUC_ADMIN.GCCOM_NOTICE_TMP t on t.id_bill = b.id_bill
#     join GCCOM_CONTRACTED_SERVICE cs on cs.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE
#     join gccom_payment_form pf on pf.id_payment_form = b.ID_PAYMENT_FORM
#   where b.BILLING_STATUS = 'ESTFAC0012'
#     and t.COD_STATUS = '5000NOTEMP'
#     and b.LAST_BILLING_DATE = cs.FROM_DATE
#     and bill_type = 'TFGEN00001'
#     and cs.status = 'ESTSC00002'
#     and cs.ID_OFFERED_SERVICE = 176;
#
# A DIFFERENT relationship from Case 1's own "Stuck Bills" pattern above (no
# next-period Electricity/Water lookup at all) - this one instead catches a
# Rate/176 bill still invoicing, still pending validation, whose contract's
# own start date (GCCOM_CONTRACTED_SERVICE.FROM_DATE - the contract is still
# Active/ESTSC00002) lands exactly on that bill's LAST_BILLING_DATE. RJ kept
# it "Case 1" rather than a new numbered Case since it shares Case 1's same
# gate (Rate/176, pending validation, invoicing) and lives in the same UI
# tab as a second section, not a whole new business rule area like Case
# 2/3/4. Reuses this module's existing NOTICE_STATUS_PENDING, BILL_STATUS_
# ISSUING, BILL_TYPE_CYCLE, OFFERED_SERVICE_RATE, and CONTRACT_STATUS_ACTIVE
# constants - same codes, same meanings, already confirmed live earlier in
# this module for exactly these purposes.
#
# RJ's own explicit caveat: "i did not check that it is the only bill in
# pending validation... add a filter for this case[s]" - i.e. his example
# query can multi-match an account that has MORE than one bill currently
# pending validation (a second, unrelated pending bill on the same account
# would make this same Rate bill show up as a false "clean single match").
# Added a `pending_counts` CTE - COUNT(*) of GCCOM_NOTICE_TMP rows still
# COD_STATUS = 5000NOTEMP for that account (via the linked bill's own
# ID_PAYMENT_FORM) - and require PENDING_COUNT = 1, i.e. this Rate bill's
# own pending notice is the ONLY one on the account right now. This is
# deliberately a different uniqueness scope than Case 1's own `period_
# counts` (which counts bills sharing one billing period) - RJ's own words
# here are "the only bill in pending validation", not "the only bill in the
# period", so the count is over every pending notice on the account,
# regardless of billing period.
#
# Live-verified after restart (2026-09-14): 656 matches, well under the
# 2,000 cap. Also spot-checked the uniqueness fix's real impact - RJ's own
# raw query (no pending_counts filter) returns 706, confirming the fix
# correctly excludes 50 accounts that had more than one bill pending
# validation.
#
# RJ, 2026-09-14 (later same day), real-data correction: "check 1102978994,
# it is wrong, i explicitly told you that it should only be rate bill for
# that specific billing period." Investigated live: account 1102978994's
# billing period 10000000236 has THREE bills, not one - a Water (19) cycle
# bill still invoicing (ESTFAC0012), the Rate (176) cycle bill still
# invoicing (ESTFAC0012, the one this query matched), and a Deposito
# (TFGEN10006) one-off bill already invoiced (ESTFAC0005). RJ's own
# original words ("its is only rate which is in pending validation") meant
# the Rate bill must be the ONLY bill in its own billing period - the exact
# same "only bill in the period" concept Case 1's own Stuck Bills query
# already enforces via its own `period_counts` CTE - but this query never
# had that check at all, so an account like 1102978994 with a second
# cycle+invoicing bill (Water) sharing the same period as its Rate bill
# wrongly matched. Fixed by adding a `period_counts` CTE identical in
# shape to Stuck Bills' own (COUNT of BILL_TYPE=cycle AND BILLING_STATUS=
# issuing bills per ID_PAYMENT_FORM+ID_BILLING_PERIOD) and requiring
# BILL_COUNT = 1 for the matched bill's own period - 1102978994 is
# confirmed excluded under this fix (period_counts = 2 for period
# 10000000236: the Water bill and the Rate bill both count, the Deposito
# bill doesn't since it's neither cycle type nor still invoicing).
#
# RJ, 2026-09-15, after the table-merge round surfaced account 1102980263
# appearing as BOTH a Stuck Bill row AND a New Contract Match row: "for
# contract match, there should no bill on the succeeding cycles which is
# in waiting for other services as it is already covered by stuck bill."
# Investigated live: 1102980263's Rate bill (1072985046, period 236) is
# genuinely both - it's the account's only pending-validation Rate bill
# (matches New Contract Match) AND it's blocking that same account's
# Electricity bill (1075198521, period 237, ESTFAC0015) one period later
# (matches Stuck Bill independently). Not a bug in either query - the two
# patterns are just modeling the same underlying notice from two angles -
# but RJ wants New Contract Match to defer to Stuck Bill whenever a later
# bill is already ESTFAC0015, rather than flagging the same root cause
# twice. Added a `NOT EXISTS` check: no bill on the account with a LATER
# ID_BILLING_PERIOD than the matched Rate bill's own period is currently
# BILLING_STATUS = ESTFAC0015 (BILL_STATUS_WAITING_OTHER_SERVICES).
# Deliberately broader than an exact mirror of Stuck Bill's own scan (which
# only checks OFFERED_SERVICE IN (Electricity, Water) within the next 11
# periods) - RJ's own words state the general principle ("no bill... which
# is in waiting for other services"), not a scoped one, so ANY ESTFAC0015
# bill on the account in a later period disqualifies the match, regardless
# of which service or how far ahead. Live-confirmed: 12 of 674 candidate
# matches are excluded by this filter, including both of Case 1's own
# current Stuck Bill accounts (1102980263, 1102980503) - exactly the
# overlap RJ flagged.
NEW_CONTRACT_MATCH_DEFAULT_LIMIT = 2000


def build_new_contract_match_query(limit: int | None = NEW_CONTRACT_MATCH_DEFAULT_LIMIT) -> str:
    """
    Case 1's "New Contract Match" - see the module-level comment block just
    above (--- Case 1: New Contract Match ---) for RJ's own verbatim SQL,
    the business meaning, and the uniqueness-filter fix he explicitly asked
    for ("i did not check that it is the only bill in pending validation").

    `pending_counts` - one row per ID_PAYMENT_FORM with how many GCCOM_
    NOTICE_TMP rows (joined to their own bill, same as this module's other
    Case 1 query) are still COD_STATUS = 5000NOTEMP for that account, so
    the final SELECT can require exactly 1 - the account's only currently
    pending-validation notice is this same Rate bill's own.

    `period_counts` - same shape as build_stuck_bills_query's own CTE of
    the same name: one row per (ID_PAYMENT_FORM, ID_BILLING_PERIOD) with
    how many bills are BOTH BILL_TYPE_CYCLE AND BILL_STATUS_ISSUING for
    that account/period, so the final SELECT can require exactly 1 - the
    Rate bill is the ONLY cycle bill still invoicing in its own billing
    period. RJ's own words: "it should only be rate bill for that specific
    billing period" (see the module-level comment above for the real
    1102978994 case this fixed - a second cycle+invoicing bill, Water,
    sharing the Rate bill's period, wrongly went unnoticed before this).

    `matched` - RJ's own starting query (Rate/176 bill still invoicing,
    still pending validation, contract still Active, contract's own start
    date equal to the bill's LAST_BILLING_DATE, cycle bill type), joined to
    both `pending_counts` (PENDING_COUNT = 1) and `period_counts`
    (BILL_COUNT = 1), plus a `NOT EXISTS` check that no bill on the same
    account in a LATER billing period is currently BILLING_STATUS =
    ESTFAC0015 (BILL_STATUS_WAITING_OTHER_SERVICES) - RJ, 2026-09-15:
    "there should no bill on the succeeding cycles which is in waiting for
    other services as it is already covered by stuck bill." Without this,
    the same Rate bill can legitimately satisfy both this query AND Case
    1's own Stuck Bills query at once (see the module-level comment above
    for the 1102980263 case this fixed) - this defers to Stuck Bill
    whenever that overlap exists, rather than flagging the same root cause
    twice.

    Final SELECT - LEFT JOINs to GCCOM_BILL_STATUS purely for a human-
    readable BILLING_STATUS description, same defensive-LEFT-JOIN pattern
    every other lookup join in this module uses.

    `limit` adds a `TOP (N)` cap, same convention as every other query
    builder in this module - pass None to disable it.
    """
    notice_tbl = _qualified(NOTICE_SCHEMA, NOTICE_TABLE)
    bill_tbl = _qualified(BILL_SCHEMA, BILL_TABLE)
    pf_tbl = _qualified(BILL_SCHEMA, PAYMENT_FORM_TABLE)
    contracted_service_tbl = _qualified(BILL_SCHEMA, CONTRACTED_SERVICE_TABLE)
    bill_status_tbl = _qualified(BILL_STATUS_SCHEMA, BILL_STATUS_TABLE)
    top_clause = f"TOP ({int(limit)}) " if limit else ""
    return (
        f"WITH pending_counts AS (\n"
        f"  SELECT b2.ID_PAYMENT_FORM, COUNT(*) AS PENDING_COUNT\n"
        f"  FROM {notice_tbl} t2\n"
        f"  JOIN {bill_tbl} b2 ON b2.ID_BILL = t2.ID_BILL\n"
        f"  WHERE t2.COD_STATUS = {format_sql_literal(NOTICE_STATUS_PENDING)}\n"
        f"  GROUP BY b2.ID_PAYMENT_FORM\n"
        f"),\n"
        f"period_counts AS (\n"
        f"  SELECT ID_PAYMENT_FORM, ID_BILLING_PERIOD, COUNT(*) AS BILL_COUNT\n"
        f"  FROM {bill_tbl}\n"
        f"  WHERE BILL_TYPE = {format_sql_literal(BILL_TYPE_CYCLE)}\n"
        f"    AND BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"  GROUP BY ID_PAYMENT_FORM, ID_BILLING_PERIOD\n"
        f"),\n"
        f"matched AS (\n"
        f"  SELECT pf.ID_PAYMENT_FORM, pf.REFERENCE, t.UPDATE_DATE AS NOTICE_UPDATE_DATE,\n"
        f"    b.ID_BILL, b.BILLING_STATUS, b.LAST_BILLING_DATE, b.BILLING_DATE,\n"
        f"    b.ID_BILLING_PERIOD, cs.ID_CONTRACTED_SERVICE, cs.FROM_DATE AS CONTRACT_FROM_DATE,\n"
        f"    cs.STATUS AS CONTRACT_STATUS\n"
        f"  FROM {notice_tbl} t\n"
        f"  JOIN {bill_tbl} b ON b.ID_BILL = t.ID_BILL\n"
        f"  JOIN {contracted_service_tbl} cs ON cs.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE\n"
        f"  JOIN {pf_tbl} pf ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM\n"
        f"  JOIN pending_counts pc ON pc.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM\n"
        f"  JOIN period_counts prc ON prc.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM\n"
        f"    AND prc.ID_BILLING_PERIOD = b.ID_BILLING_PERIOD\n"
        f"  WHERE b.BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"    AND t.COD_STATUS = {format_sql_literal(NOTICE_STATUS_PENDING)}\n"
        f"    AND b.LAST_BILLING_DATE = cs.FROM_DATE\n"
        f"    AND b.BILL_TYPE = {format_sql_literal(BILL_TYPE_CYCLE)}\n"
        f"    AND cs.STATUS = {format_sql_literal(CONTRACT_STATUS_ACTIVE)}\n"
        f"    AND cs.ID_OFFERED_SERVICE = {format_sql_literal(OFFERED_SERVICE_RATE)}\n"
        f"    AND pc.PENDING_COUNT = 1\n"
        f"    AND prc.BILL_COUNT = 1\n"
        f"    AND NOT EXISTS (\n"
        f"      SELECT 1 FROM {bill_tbl} b3\n"
        f"      WHERE b3.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM\n"
        f"        AND b3.ID_BILLING_PERIOD > b.ID_BILLING_PERIOD\n"
        f"        AND b3.BILLING_STATUS = {format_sql_literal(BILL_STATUS_WAITING_OTHER_SERVICES)}\n"
        f"    )\n"
        f")\n"
        f"SELECT {top_clause}m.ID_PAYMENT_FORM, m.REFERENCE, m.NOTICE_UPDATE_DATE,\n"
        f"  m.ID_BILL, m.BILLING_STATUS,\n"
        f"  bs.{BILL_STATUS_LOOKUP_DESC_COLUMN} AS BILLING_STATUS_DESC,\n"
        f"  m.LAST_BILLING_DATE, m.BILLING_DATE, m.ID_BILLING_PERIOD,\n"
        f"  m.ID_CONTRACTED_SERVICE, m.CONTRACT_FROM_DATE, m.CONTRACT_STATUS\n"
        f"FROM matched m\n"
        f"LEFT JOIN {bill_status_tbl} bs ON bs.{BILL_STATUS_LOOKUP_KEY_COLUMN} = m.BILLING_STATUS\n"
        f"ORDER BY m.NOTICE_UPDATE_DATE;"
    )


def build_terminated_period_mismatch_query(
    limit: int | None = TERMINATED_PERIOD_DEFAULT_LIMIT,
    *,
    days_back: int | None = TERMINATED_PERIOD_LOOKBACK_DAYS_DEFAULT,
) -> str:
    """
    Case 2's detection query (2026-09-17 redesign) - one row per
    (account, terminated service), for the caller (web/server.py) to
    group into one row per account. See the module-level Case 2 comment
    block above for the full business-rule narrative, RJ's own 342702
    example, and the three AskUserQuestion answers this shape implements.

    `terminated_services` - every GCCOM_CONTRACTED_SERVICE row that's
    Terminated (STATUS = ESTSC00004), optionally scoped to the last
    `days_back` days by END_DATE (see TERMINATED_PERIOD_LOOKBACK_DAYS_
    DEFAULT's comment for why this matters: ~3M of GCCOM_CONTRACTED_
    SERVICE's ~4.7M rows are already Terminated, so an unscoped scan is
    both irrelevant to the analyst and the single biggest cost in this
    query - confirmed live, COUNT(*) with no date filter: 2,961,122
    rows/1.4s; with a 6-month filter: 95,256 rows/1.0s). Pass
    days_back=None to disable the window entirely (every Terminated
    service, any age). Also requires an EXISTS match against GCCOM_
    NOTICE_TMP for the SAME account with COD_STATUS = 5000NOTEMP ("For
    validation") AND whose own linked bill (nt.ID_BILL) is still
    BILL_STATUS_ISSUING (ESTFAC0012) - RJ's 2026-09-17 scoping rule,
    tightened same day after a real false-positive (a stale notice
    pointing at a voided bill). See the module-level Case 2 comment
    block for the live-confirmed counts and the indexes this EXISTS
    leans on.

    `matched` - LEFT JOINs each terminated service to its OWN final bill:
    the GCCOM_BILL row for that SAME contracted service (joined on
    ID_CONTRACTED_SERVICE - the service's own row PK, not ID_PAYMENT_
    FORM + ID_OFFERED_SERVICE; see CONTRACTED_SERVICE_PK_COLUMN's use
    below and the module Case 2 comment) whose BILLING_DATE falls on that
    service's own END_DATE (the termination date - compared by DATE only,
    a real bug fix from the original exact-datetime equality: END_DATE
    can carry a non-midnight time-of-day component - confirmed live,
    ~59% of Rate/176 terminations in a 60-day sample - while BILLING_DATE
    is always midnight, so the old `b.BILLING_DATE = ts.END_DATE` could
    never match those services' real final bill at all) and whose status
    is invoicing or already invoiced (FINAL_BILL_STATUSES) and whose
    BILL_TYPE is the regular/cycle type (BILL_TYPE_CYCLE - RJ, 2026-09-13:
    "we need cycle bills TFGEN0001 something in bill_type of gccom_bill",
    keeping a one-off charge bill like a reconnection fee or deposit that
    happens to land on the termination date from being mistaken for the
    real final bill). ROW_NUMBER() PARTITIONed by ID_CONTRACTED_SERVICE
    picks one bill if more than one happens to match (defensive - real
    data hasn't shown this, but the join has no uniqueness guarantee to
    lean on). LEFT (not INNER) so a service with NO matching final bill
    still surfaces as a row with ID_BILL NULL - itself a NEEDS_UPDATE case
    (see `flagged` below).

    `with_target` - keeps RN = 1 and adds TARGET_PERIOD: MAX(ID_BILLING_
    PERIOD) OVER (PARTITION BY ID_PAYMENT_FORM) among the account's own
    matched final bills - RJ's own confirmed rule (AskUserQuestion #2).

    `flagged` - NEEDS_UPDATE = 1 when a service has no matching final
    bill at all (ID_BILL IS NULL) OR its final bill's ID_BILLING_PERIOD
    doesn't match TARGET_PERIOD AND that bill is still BILL_STATUS_ISSUING
    (ESTFAC0012, "En proceso de puesta al cobro" - still correctable,
    hasn't gone out yet). A final bill that's already BILL_STATUS_INVOICED
    (ESTFAC0005, "Puesta al cobro") does NOT count as needing an update
    even if its period disagrees with TARGET_PERIOD - RJ, 2026-09-13, real
    account example (REFERENCE 1059650711, Rate/176 service): "the only
    issue is that it is in a different billing period and its already
    issued, so this account should be marked ok". Live-confirmed: that
    service's own final bill (period 236) was already ESTFAC0005 while
    the account's other 3 services landed at period 237 (still ESTFAC0012)
    - an already-issued bill has gone out to the customer and can't be
    usefully "corrected" after the fact, so a period mismatch there isn't
    a live problem the way an uncorrected still-in-process bill is. This
    also matters for build_terminated_period_fix_script: NEEDS_UPDATE
    drives exactly which (ID_BILL, TARGET_PERIOD) pairs get UPDATEd, so
    this change doubles as a safety fix - the generated script could never
    have correctly targeted an already-invoiced bill anyway (the customer
    already has it), and before this fix an account like 1059650711 would
    have been flagged and its already-invoiced Rate bill would have been
    the one offered up for "correction". NEEDS_UPDATE = 0 means that
    service's final bill is either already exactly where it should be, or
    already invoiced and therefore accepted as-is. ACCOUNT_HAS_ISSUE is
    the same flag re-aggregated with MAX(...) OVER (PARTITION BY
    ID_PAYMENT_FORM) - 1 if ANY of the account's services needs action,
    0 only when every service's final bill already agrees or is already
    invoiced (RJ's own "Complete" case, AskUserQuestion #3) - the caller
    uses this to group rows into an account-level Complete/Needs-action
    filter without a second query.

    `account_status` - per account (scoped to just the accounts already
    in `terminated_services`, not a full-table scan), whether it has any
    NON-Terminated service left. Only accounts with ACTIVE_COUNT = 0
    (every service Terminated) are Case 2's business - a terminated
    service whose account also has a live service is a different
    situation entirely. Grouped/joined once (not a correlated EXISTS per
    row) - the module Case 2 comment covers why a correlated EXISTS
    against a big table, re-evaluated per outer row, was a real
    performance trap earlier in this same investigation.

    Final SELECT - joins GCCOM_PAYMENT_FORM for REFERENCE. `limit`
    behaves the same as build_stuck_bills_query's own - a `TOP (N)` cap
    (applied to the per-service row count, not per-account), pass None
    to disable.

    Live-confirmed performance (2026-09-17, 6-month window): full query
    (COUNT + account/issue rollup) ran in ~8s over 94,218 terminated-
    service rows / 27,166 distinct accounts, 16,138 of them flagged
    ACCOUNT_HAS_ISSUE = 1 - a huge improvement over every earlier shape
    tried (OR EXISTS, UNION, even this same JOIN shape with an N'...'
    literal) which all hit the app's 120s query timeout. The fix wasn't
    the join shape - see app/core/sql_format.py's 2026-09-17 comment:
    format_sql_literal used to emit N'...' (nvarchar) literals, and
    BILLING_STATUS/STATUS are plain varchar columns, so every one of
    those WHERE/JOIN comparisons was silently defeating its supporting
    index via an implicit CONVERT().

    With the 2026-09-17 (later same day) pending-validation EXISTS added
    on top - tightened same day to also require the notice's own linked
    bill still be invoicing, after a real false-positive - the same
    60-day window narrows further: 9,593 terminated accounts down to
    1,064 with a genuinely pending notice. See the module-level Case 2
    comment block above for the full rationale.

    2026-09-13: with the DATE-only final-bill match, BILL_TYPE_CYCLE
    filter, and already-invoiced-is-OK NEEDS_UPDATE rule (see the module
    Case 2 comment block's REFERENCE 1059650711 entry), the SAME 60-day
    window's total account count is unchanged (963 - the notice-pending
    scoping is untouched) but the Complete/Needs-action split flips
    dramatically: 946 needing action / 17 Complete (before this fix) to
    344 needing action / 619 Complete (after). The exact-datetime bug
    alone (see `matched`'s own comment above) was silently mis-flagging
    the large majority of these accounts - not because their billing was
    actually broken, but because the query itself couldn't see their real
    final bill.
    """
    bill_tbl = _qualified(BILL_SCHEMA, BILL_TABLE)
    pf_tbl = _qualified(BILL_SCHEMA, PAYMENT_FORM_TABLE)
    contracted_service_tbl = _qualified(BILL_SCHEMA, CONTRACTED_SERVICE_TABLE)
    notice_tbl = _qualified(NOTICE_SCHEMA, NOTICE_TABLE)
    top_clause = f"TOP ({int(limit)}) " if limit else ""
    final_statuses_list = ", ".join(format_sql_literal(s) for s in FINAL_BILL_STATUSES)
    lookback_clause = (
        f"\n    AND cs.{CONTRACTED_SERVICE_END_DATE_COLUMN} >= DATEADD(DAY, -{int(days_back)}, CAST(GETDATE() AS DATE))"
        if days_back
        else ""
    )
    return (
        f"WITH terminated_services AS (\n"
        f"  SELECT cs.{CONTRACTED_SERVICE_PK_COLUMN}, cs.ID_PAYMENT_FORM, cs.ID_OFFERED_SERVICE,\n"
        f"    cs.{CONTRACTED_SERVICE_END_DATE_COLUMN} AS END_DATE\n"
        f"  FROM {contracted_service_tbl} cs\n"
        f"  WHERE cs.{CONTRACTED_SERVICE_STATUS_COLUMN} = {format_sql_literal(TERMINATED_SERVICE_STATUS)}"
        f"{lookback_clause}\n"
        f"    AND EXISTS (\n"
        f"      SELECT 1 FROM {notice_tbl} nt\n"
        f"      JOIN {bill_tbl} nb ON nb.ID_BILL = nt.ID_BILL\n"
        f"      WHERE nt.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM\n"
        f"        AND nt.COD_STATUS = {format_sql_literal(NOTICE_STATUS_PENDING)}\n"
        f"        AND nb.BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"    )\n"
        f"),\n"
        f"matched AS (\n"
        f"  SELECT ts.{CONTRACTED_SERVICE_PK_COLUMN}, ts.ID_PAYMENT_FORM, ts.ID_OFFERED_SERVICE, ts.END_DATE,\n"
        f"    b.ID_BILL, b.ID_BILLING_PERIOD, b.BILLING_STATUS,\n"
        f"    ROW_NUMBER() OVER (PARTITION BY ts.{CONTRACTED_SERVICE_PK_COLUMN} ORDER BY b.ID_BILL DESC) AS RN\n"
        f"  FROM terminated_services ts\n"
        f"  LEFT JOIN {bill_tbl} b\n"
        f"    ON b.{CONTRACTED_SERVICE_PK_COLUMN} = ts.{CONTRACTED_SERVICE_PK_COLUMN}\n"
        f"    AND b.BILLING_DATE >= CAST(ts.END_DATE AS DATE)\n"
        f"    AND b.BILLING_DATE < DATEADD(DAY, 1, CAST(ts.END_DATE AS DATE))\n"
        f"    AND b.BILLING_STATUS IN ({final_statuses_list})\n"
        f"    AND b.BILL_TYPE = {format_sql_literal(BILL_TYPE_CYCLE)}\n"
        f"),\n"
        f"with_target AS (\n"
        f"  SELECT *, MAX(ID_BILLING_PERIOD) OVER (PARTITION BY ID_PAYMENT_FORM) AS TARGET_PERIOD\n"
        f"  FROM matched\n"
        f"  WHERE RN = 1\n"
        f"),\n"
        f"flagged AS (\n"
        f"  SELECT *,\n"
        f"    CASE\n"
        f"      WHEN ID_BILL IS NULL THEN 1\n"
        f"      WHEN ID_BILLING_PERIOD <> TARGET_PERIOD AND BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)} THEN 1\n"
        f"      ELSE 0\n"
        f"    END AS NEEDS_UPDATE,\n"
        f"    MAX(CASE\n"
        f"      WHEN ID_BILL IS NULL THEN 1\n"
        f"      WHEN ID_BILLING_PERIOD <> TARGET_PERIOD AND BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)} THEN 1\n"
        f"      ELSE 0\n"
        f"    END) OVER (PARTITION BY ID_PAYMENT_FORM) AS ACCOUNT_HAS_ISSUE\n"
        f"  FROM with_target\n"
        f"),\n"
        f"account_status AS (\n"
        f"  SELECT cs.ID_PAYMENT_FORM,\n"
        f"    SUM(CASE WHEN cs.{CONTRACTED_SERVICE_STATUS_COLUMN} <> {format_sql_literal(TERMINATED_SERVICE_STATUS)} THEN 1 ELSE 0 END) AS ACTIVE_COUNT\n"
        f"  FROM {contracted_service_tbl} cs\n"
        f"  WHERE cs.ID_PAYMENT_FORM IN (SELECT DISTINCT ID_PAYMENT_FORM FROM terminated_services)\n"
        f"  GROUP BY cs.ID_PAYMENT_FORM\n"
        f")\n"
        f"SELECT {top_clause}f.ID_PAYMENT_FORM, pf.REFERENCE, f.ID_OFFERED_SERVICE,\n"
        f"  f.END_DATE, f.ID_BILL, f.ID_BILLING_PERIOD, f.BILLING_STATUS,\n"
        f"  f.TARGET_PERIOD, f.NEEDS_UPDATE, f.ACCOUNT_HAS_ISSUE\n"
        f"FROM flagged f\n"
        f"JOIN account_status acct ON acct.ID_PAYMENT_FORM = f.ID_PAYMENT_FORM AND acct.ACTIVE_COUNT = 0\n"
        f"JOIN {pf_tbl} pf ON pf.ID_PAYMENT_FORM = f.ID_PAYMENT_FORM\n"
        f"ORDER BY f.ID_PAYMENT_FORM, f.ID_OFFERED_SERVICE;"
    )


def build_bills_complete_query(
    *,
    year: int | None = ALL_CONTRACT_STATUS_DEFAULT_YEAR,
    billing_period_ids: Iterable[Any] | None = None,
    with_active_contract: bool | None = None,
    limit: int | None = ALL_CONTRACT_STATUS_DEFAULT_LIMIT,
) -> str:
    """
    Case 3's detection query - see the module-level Case 3 comment block
    above (CONTRACT_STATUS_ACTIVE etc.) for RJ's own verbatim starting SQL,
    the per-period-correctness reasoning, and the optimization rationale.

    One row per (account, billing period) where that account's total
    contracted-service count (every non-deleted status - ALL_CONTRACT_
    STATUSES) exactly equals its count of still-invoicing cycle bills
    (BILL_TYPE_CYCLE, BILL_STATUS_ISSUING) for that ONE period - i.e.
    nothing's missing for that account in that period, "Bills Complete".

    `pending_accounts` - same pending-notice-on-an-issuing-bill gate Case 1
    and Case 2 both use (GCCOM_NOTICE_TMP.COD_STATUS = 5000NOTEMP on a bill
    still ESTFAC0012) - RJ's own scoping rule from the verbatim query's
    outer `cs.ID_PAYMENT_FORM IN (...)` clause.

    `period_bills` - one row per (account, period) with how many cycle
    bills are still invoicing for that period, scoped to `year` (or an
    explicit `billing_period_ids` override - see below) AND to
    pending_accounts up front, so this never aggregates more than the
    handful of periods/accounts that can possibly matter.

    `contract_counts` - total ALL_CONTRACT_STATUSES service count per
    account, scoped to only the accounts that already survived
    `period_bills` (the optimization: RJ's own `cs` subquery aggregated
    the whole ~4.6M-row table first; this computes the same numbers but
    only for the already-narrow account list - see module comment).

    Final SELECT keeps only rows where CONTRACTED_SERVICES = BILLS exactly
    (RJ's own equality, MISSING_BILLS is always 0 in the output - matches
    his own query, which never surfaces a nonzero MISSING_BILLS either
    since that's exactly what its WHERE filters down to) and computes
    WITH_ACTIVE_CONTRACT via the same correlated EXISTS RJ's own query
    uses (cheap here - only runs once per already-tiny result row).

    `year` sources the period list from GCCOM_BILLING_PERIOD itself
    (`WHERE YEAR(INITIAL_DATE) = year`) rather than a hardcoded ID list,
    so this keeps working correctly in 2027 and beyond. Pass
    `billing_period_ids` instead (e.g. from the frontend's own period
    filter) to scope to specific periods explicitly; if both are given,
    billing_period_ids wins. Passing neither (year=None, billing_period_
    ids=None) is rejected by the caller - an unscoped IN (SELECT * FROM
    GCCOM_BILLING_PERIOD) would defeat the whole point of RJ's "1 by 1,
    to obtain the correct result" instruction by silently including every
    period ever created, not just the ones asked for.

    `with_active_contract`: None = no filter (both YES/NO rows), True/False
    filters to just that WITH_ACTIVE_CONTRACT value - pushed into the SQL
    (not left to the caller to filter in Python) so `limit` still caps the
    right row set.

    `limit` behaves the same as every other Case's own `limit` - a
    `TOP (N)` cap, pass None to disable.
    """
    if not billing_period_ids and year is None:
        raise ValueError("build_bills_complete_query needs either year or billing_period_ids.")

    bill_tbl = _qualified(BILL_SCHEMA, BILL_TABLE)
    pf_tbl = _qualified(BILL_SCHEMA, PAYMENT_FORM_TABLE)
    contracted_service_tbl = _qualified(BILL_SCHEMA, CONTRACTED_SERVICE_TABLE)
    notice_tbl = _qualified(NOTICE_SCHEMA, NOTICE_TABLE)
    billing_period_tbl = _qualified(BILL_SCHEMA, BILLING_PERIOD_TABLE)

    all_statuses_list = ", ".join(format_sql_literal(s) for s in ALL_CONTRACT_STATUSES)
    active_statuses_list = ", ".join(format_sql_literal(s) for s in ACTIVE_CONTRACT_STATUSES)
    top_clause = f"TOP ({int(limit)}) " if limit else ""

    if billing_period_ids:
        ids_list = ", ".join(format_sql_literal(p) for p in billing_period_ids)
        period_scope_sql = f"({ids_list})"
    else:
        period_scope_sql = (
            f"(SELECT {quote_ident(BILLING_PERIOD_ID_COLUMN)} FROM {billing_period_tbl} "
            f"WHERE YEAR({quote_ident(BILLING_PERIOD_YEAR_COLUMN)}) = {int(year)})"
        )

    active_filter_clause = ""
    if with_active_contract is True:
        active_filter_clause = "\n  AND f.WITH_ACTIVE_CONTRACT = 'YES'"
    elif with_active_contract is False:
        active_filter_clause = "\n  AND f.WITH_ACTIVE_CONTRACT = 'NO'"

    return (
        f"WITH pending_accounts AS (\n"
        f"  SELECT DISTINCT pf1.ID_PAYMENT_FORM\n"
        f"  FROM {pf_tbl} pf1\n"
        f"  JOIN {bill_tbl} b ON b.ID_PAYMENT_FORM = pf1.ID_PAYMENT_FORM\n"
        f"  JOIN {notice_tbl} tmp ON tmp.ID_BILL = b.ID_BILL\n"
        f"  WHERE b.BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"    AND tmp.COD_STATUS = {format_sql_literal(NOTICE_STATUS_PENDING)}\n"
        f"    AND tmp.UPDATE_DATE < GETDATE()\n"
        f"),\n"
        f"period_bills AS (\n"
        f"  SELECT B.ID_PAYMENT_FORM, B.ID_BILLING_PERIOD, COUNT(*) AS BILLS\n"
        f"  FROM {bill_tbl} B\n"
        f"  WHERE B.BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"    AND B.BILL_TYPE = {format_sql_literal(BILL_TYPE_CYCLE)}\n"
        f"    AND B.ID_BILLING_PERIOD IN {period_scope_sql}\n"
        f"    AND B.ID_PAYMENT_FORM IN (SELECT ID_PAYMENT_FORM FROM pending_accounts)\n"
        f"  GROUP BY B.ID_PAYMENT_FORM, B.ID_BILLING_PERIOD\n"
        f"),\n"
        f"contract_counts AS (\n"
        f"  SELECT ID_PAYMENT_FORM, COUNT(*) AS CONTRACTED_SERVICES\n"
        f"  FROM {contracted_service_tbl}\n"
        f"  WHERE STATUS IN ({all_statuses_list})\n"
        f"    AND ID_PAYMENT_FORM IN (SELECT DISTINCT ID_PAYMENT_FORM FROM period_bills)\n"
        f"  GROUP BY ID_PAYMENT_FORM\n"
        f"),\n"
        f"flagged AS (\n"
        f"  SELECT pf.REFERENCE, cc.ID_PAYMENT_FORM, cc.CONTRACTED_SERVICES,\n"
        f"    pb.BILLS, cc.CONTRACTED_SERVICES - pb.BILLS AS MISSING_BILLS,\n"
        f"    pb.ID_BILLING_PERIOD, bp.{quote_ident(BILLING_PERIOD_NAME_COLUMN)} AS BILLING_PERIOD_NAME,\n"
        f"    CASE WHEN EXISTS (\n"
        f"      SELECT 1 FROM {contracted_service_tbl} r\n"
        f"      WHERE r.ID_PAYMENT_FORM = cc.ID_PAYMENT_FORM\n"
        f"        AND r.STATUS IN ({active_statuses_list})\n"
        f"    ) THEN 'YES' ELSE 'NO' END AS WITH_ACTIVE_CONTRACT\n"
        f"  FROM period_bills pb\n"
        f"  JOIN contract_counts cc ON cc.ID_PAYMENT_FORM = pb.ID_PAYMENT_FORM\n"
        f"  JOIN {pf_tbl} pf ON pf.ID_PAYMENT_FORM = cc.ID_PAYMENT_FORM\n"
        f"  LEFT JOIN {billing_period_tbl} bp ON bp.ID_BILLING_PERIOD = pb.ID_BILLING_PERIOD\n"
        f"  WHERE cc.CONTRACTED_SERVICES = pb.BILLS\n"
        f")\n"
        f"SELECT {top_clause}f.REFERENCE, f.ID_PAYMENT_FORM, f.CONTRACTED_SERVICES,\n"
        f"  f.BILLS, f.MISSING_BILLS, f.ID_BILLING_PERIOD, f.BILLING_PERIOD_NAME,\n"
        f"  f.WITH_ACTIVE_CONTRACT\n"
        f"FROM flagged f\n"
        f"WHERE 1 = 1{active_filter_clause}\n"
        f"ORDER BY f.ID_BILLING_PERIOD, f.REFERENCE;"
    )


def _bill_audit_set_fragment(program: str, user: str) -> str:
    return (
        f",\n    {quote_ident(BILL_AUDIT_PROGRAM_COL)} = {format_sql_literal(program)}"
        f",\n    {quote_ident(BILL_AUDIT_DATE_COL)} = GETDATE()"
        f",\n    {quote_ident(BILL_AUDIT_USER_COL)} = {format_sql_literal(user)}"
    )


def _strip_sql_comments(sql_text: str) -> str:
    """
    Local copy of date_anomaly.py's own _strip_sql_comments - same
    line-based "drop any line that's purely a SQL comment, then collapse
    repeated blank lines" behavior, kept as its own copy here (rather
    than importing date_anomaly's private helper) so this module stays
    self-contained, same as every other app/core module in this app.
    """
    kept = [line for line in sql_text.split("\n") if not line.strip().startswith("--")]
    collapsed: list[str] = []
    prev_blank = False
    for line in kept:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank
    return "\n".join(collapsed).strip("\n") + "\n"


@dataclass
class TerminatedPeriodFixScript:
    """
    Result of build_terminated_period_fix_script - TWO UPDATE statements
    per (id_bill, target_period) pair the caller passes in (GCCOM_BILL and
    GCCOM_ITEMS_TO_BILL, RJ 2026-09-15: "we need to also update GCCOM_
    ITEMS_TO_BILL... similar to how we are doing now gccom_bill"), each
    moving that bill's ID_BILLING_PERIOD to the account's target (latest)
    period.

    `update_count` is the number of BILLS affected (what the UI shows,
    e.g. "N bill(s)") - `statement_count` is the actual number of raw SQL
    UPDATE statements in the script (2x update_count, since each bill now
    gets one statement per table) - kept as two separate fields rather
    than update_count doubling, so the frontend's own "N bill(s)" wording
    doesn't silently change meaning.
    """
    sql_text: str
    update_count: int = 0
    statement_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


def build_terminated_period_fix_script(
    updates: Iterable[tuple[Any, Any]],
    *,
    program: str = DEFAULT_AUDIT_PROGRAM,
    user: str = DEFAULT_AUDIT_USER,
    clean: bool = False,
) -> TerminatedPeriodFixScript:
    """
    Builds the "generate update script" button's output for Case 2 - RJ's
    own words: "we need to find this cases, and an update on the bill
    with different id_billing_period... will be created, the new
    billing_period will be the latest id_billing_period... so you will
    add a button form me to generate the update script".

    `updates` is an iterable of (id_bill, target_period) pairs - the
    caller (web/server.py) gets these by re-running build_terminated_
    period_mismatch_query right before Generate (same "re-verify at
    generate time" pattern as build_cleanup_script/build_correction_
    script elsewhere in this app, since the detect list may be stale by
    the time the analyst clicks Generate) and keeping only the rows
    where NEEDS_UPDATE = 1, passing (ID_BILL, TARGET_PERIOD) for each.

    A row with NEEDS_UPDATE = 1 but ID_BILL NULL (the 2026-09-17 redesign
    - a terminated service with no bill at all matching its own END_DATE)
    has nothing to UPDATE - there's no bill row to move. This function
    skips those pairs (id_bill is None) rather than emitting a broken
    `WHERE ID_BILL = NULL` statement (always false, silently a no-op, but
    still wrong to generate), and records one warning per skip so the
    analyst sees it needs manual investigation instead of it just
    vanishing.

    Each bill gets TWO statements - GCCOM_BILL and GCCOM_ITEMS_TO_BILL -
    RJ, 2026-09-15: "we need to also update GCCOM_ITEMS_TO_BILL in the
    script generated... similar to how we are doing now gccom_bill." Both
    set ID_BILLING_PERIOD to the same target period and carry the same
    audit columns and the same defensive WHERE ID_BILLING_PERIOD <> target
    guard (in addition to WHERE ID_BILL = id) - so re-running the script
    against a bill that's already been corrected some other way (or
    already ran once) is a safe no-op, same "only touch if still in the
    state we expect" guard convention as every other correction script
    in this app (build_correction_script's Part 3, build_cleanup_script's
    Part A, etc).
    """
    bill_tbl = _qualified(BILL_SCHEMA, BILL_TABLE)
    items_to_bill_tbl = _qualified(ITEMS_TO_BILL_SCHEMA, ITEMS_TO_BILL_TABLE)
    warnings: list[str] = []
    pairs = list(updates)

    stmts: list[str] = []
    skipped = 0
    bills_updated = 0
    for id_bill, target_period in pairs:
        if id_bill is None:
            skipped += 1
            continue
        bills_updated += 1
        set_clause = f"{quote_ident('ID_BILLING_PERIOD')} = {format_sql_literal(target_period)}"
        set_clause += _bill_audit_set_fragment(program, user)
        where_clause = (
            f"WHERE {quote_ident('ID_BILL')} = {format_sql_literal(id_bill)}\n"
            f"  AND {quote_ident('ID_BILLING_PERIOD')} <> {format_sql_literal(target_period)};"
        )
        stmts.append(
            f"-- Align ID_BILLING_PERIOD for bill {id_bill} -> {target_period} (GCCOM_BILL)\n"
            f"UPDATE {bill_tbl}\n"
            f"SET {set_clause}\n"
            f"{where_clause}"
        )
        stmts.append(
            f"-- Align ID_BILLING_PERIOD for bill {id_bill} -> {target_period} (GCCOM_ITEMS_TO_BILL)\n"
            f"UPDATE {items_to_bill_tbl}\n"
            f"SET {set_clause}\n"
            f"{where_clause}"
        )
    if skipped:
        warnings.append(
            f"{skipped} service(s) with no bill matching their own termination "
            "date were skipped - nothing to UPDATE. Investigate manually."
        )

    header = [
        "-- Bill Issuance Validator: Case 2 (terminated account, billing-period",
        "-- mismatch) UPDATE script - generated by ScriptGen",
        f"-- Generated (UTC): {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"-- Bills selected: {len(pairs)}",
        f"-- Program/Jira: {program}",
        "-- WARNING: every service on the affected account(s) is Terminated",
        "-- (GCCOM_CONTRACTED_SERVICE.STATUS = ESTSC00004) - this script only",
        "-- realigns GCCOM_BILL.ID_BILLING_PERIOD and GCCOM_ITEMS_TO_BILL.",
        "-- ID_BILLING_PERIOD so the account's remaining pending/issuing bills",
        "-- share one billing period and can complete issuance together. It",
        "-- does not change BILLING_STATUS, BILLING_DATE, or anything else.",
        f"-- Statements: {len(stmts)}",
    ]
    for w in warnings:
        header.append(f"-- WARNING: {w}")
    header.append("")

    body = "\n\n".join(stmts) if stmts else "-- Nothing to update."

    footer = [
        "",
        "-- Review the statements above before running them.",
    ]

    sql_text = "\n".join(header) + body + "\n".join(footer)
    if clean:
        sql_text = _strip_sql_comments(sql_text)
    return TerminatedPeriodFixScript(
        sql_text=sql_text,
        update_count=bills_updated,
        statement_count=len(stmts),
        warnings=warnings,
    )
