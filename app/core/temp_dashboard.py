"""
Temp Dashboard (RJ, 2026-10-11).

RJ: "create a new menu temp dashboard, optimize and use the query that i will
give you, I want to get all account where there is reading for them".

Source (RJ 2026-10-11: "replace table to EWA_DDI_ELIGIBILITY" - the app login
has no SELECT on support_admin.ddi_new_offering): OUC_COMMON_ADMIN.
EWA_DDI_ELIGIBILITY rows with no October bill amount. In this table that is
stored as 0, not NULL (live: 0 NULL, 56,883 zero of 166,674; account
1099311910 = 0), so the filter is ISNULL(BILL_AMOUNT_OCT, 0) = 0. Per
account, using its active contracted services (status NOT IN ESTSC00001 /
ESTSC00004 / ESTSC00005):
  WITH_ELE            - has an Electricity (1) service
  WITH_READING_ELE    - an Electricity service's sector supply has a reading in
                        the billing period (default 10000000238), READ_STATUS IN
                        (5000STSRED, 6000STSRED, 7000STSRED) - RJ 2026-10-11, was
                        <> 0500STSRED - and READING_TYPE IN (TIPTL00003, TIPTL00005)
  WITH_WATER          - has a Water (19) service
  WITH_READING_WATER  - same reading test on a Water service

Fix vs RJ's text: his WITH_READING_WATER block filtered id_offered_service = 1
(copy of the Electricity block), so it always equalled WITH_READING_ELE; here it
uses 19 (Water) - flagged to RJ.

Optimization (same answer as the 4 correlated EXISTS per row): the account
list is read once, its active Electricity/Water services are joined once, the
period's qualifying readings are read once (only for those sector supplies),
and the four YES/NO flags are MAX() over that join grouped per account - an
EXISTS over a set is exactly "MAX(flag) = 1" over the same set. Accounts are
returned once (GROUP BY), so a duplicated account row in the source table no
longer repeats.

Pure logic: builds SELECT text only.
"""
from __future__ import annotations

import re

# RJ 2026-10-11: "change it bill_amount_set is null then main focus for now is
# billing period september" - September 2026 = 10000000237 (GCCOM_BILLING_PERIOD
# "9-September 2026"), source filter on BILL_AMOUNT_SET (was _OCT / 10000000238).
# Live: BILL_AMOUNT_SET has 0 NULLs, 3,248 zeros of 166,674 rows - "no bill" is
# stored as 0, so ISNULL(..., 0) = 0 keeps "is null" and catches the zeros.
DEFAULT_BILLING_PERIOD = 10000000237
BILL_AMOUNT_COLUMN = "BILL_AMOUNT_SET"
EXCLUDED_CS_STATUSES = ("ESTSC00001", "ESTSC00004", "ESTSC00005")
READING_TYPES = ("TIPTL00003", "TIPTL00005")
# RJ 2026-10-11: "to consider with reading, the read_status should be
# '5000STSRED','7000STSRED',6000STSRED" - replaces READ_STATUS <> '0500STSRED'.
READING_STATUSES = ("5000STSRED", "6000STSRED", "7000STSRED")
BILLED_READ_STATUS = "7000STSRED"   # reading Billed (Facturada)
OFFERED_ELECTRICITY = 1
OFFERED_WATER = 19


# ---------------------------------------------------------------------------
# Tab 2 - "SANITARY not Match Water Status" (RJ 2026-10-12):
# "in ITEMS_TO_BILL it has 2 or more record with the same END DATE AND INI DATE,
# and the status of our target is not billed, and then using the same reading
# item to bill get the water item to bill, using the billing service and id
# offered service from gccom contracted service, the water should be billed
# already for that billing period and ini date and end date".
#
#   target  = Sanitary item (GCCOM_ITEMS_TO_BILL -> BILLING_SERVICE ->
#             CONTRACTED_SERVICE.ID_OFFERED_SERVICE = 190), STATUS <> STTOBILL07,
#             in the billing period, whose billing service has >= 2 items with
#             the same INI_DATE + END_DATE (GROUP_COUNT, the target included)
#   water   = item sharing a reading with the target (OUC_ADMIN.
#             GCCOM_READINGS_ITEMSTOBILL.ID_READING), offered service 19,
#             STATUS = STTOBILL07, same ID_BILLING_PERIOD, INI_DATE, END_DATE
#   SAN_BILLED_IN_GROUP = Billed (STTOBILL07) Sanitary items in the target's
#             group: 0 = the Sanitary was never billed while the Water was
#             (the real mismatch); > 0 = a twin already billed it.
# ITB status codes (live GCCOM_ITEMS_TO_BILL_STATUS): 00 Anomalous, 01 Pending,
# 04 Cancelled, 05 Modified consumption, 06 Resolved without billing,
# 07 Billed, 08 Sent to curve aggregation, 09 Hierarchy in calculation.
# ---------------------------------------------------------------------------
OFFERED_SANITARY = 190
ITB_BILLED = "STTOBILL07"
# RJ 2026-10-12 "forget about cancelled, only status 0000": the target Sanitary
# item must be Anomalous (STTOBILL00); Cancelled (STTOBILL04) items are left out
# of the same-INI/END group count too.
ITB_TARGET_STATUS = "STTOBILL00"
ITB_CANCELLED = "STTOBILL04"
ITB_STATUS_NAMES = {
    "STTOBILL00": "Anomalous", "STTOBILL01": "Pending", "STTOBILL04": "Cancelled",
    "STTOBILL05": "Modified consumption", "STTOBILL06": "Resolved without billing",
    "STTOBILL07": "Billed", "STTOBILL08": "Sent to curve aggregation",
    "STTOBILL09": "Hierarchy in calculation",
}
READINGS_ITB_TABLE = "OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL"


def build_sanitary_mismatch_query(billing_period: int = DEFAULT_BILLING_PERIOD) -> str:
    bp = int(billing_period)
    return f"""
WITH san AS (
    SELECT s.ID_ITEM_TO_BILL, s.ID_BILLING_SERVICE, s.ID_BILLING_PERIOD, s.INI_DATE, s.END_DATE, s.STATUS,
           scs.ID_SECTOR_SUPPLY, scs.ID_PAYMENT_FORM
    FROM GCCOM_ITEMS_TO_BILL s WITH (NOLOCK)
    JOIN GCCOM_BILLING_SERVICE sbs WITH (NOLOCK) ON sbs.ID_BILLING_SERVICE = s.ID_BILLING_SERVICE
    JOIN GCCOM_CONTRACTED_SERVICE scs WITH (NOLOCK) ON scs.ID_CONTRACTED_SERVICE = sbs.ID_CONTRACTED_SERVICE
     AND scs.ID_OFFERED_SERVICE = {OFFERED_SANITARY}
    -- RJ 2026-10-12: "forget about cancelled, only status 0000" -> target = Anomalous only
    WHERE s.ID_BILLING_PERIOD = {bp} AND s.STATUS = '{ITB_TARGET_STATUS}'
),
grp AS (
    SELECT sa.ID_ITEM_TO_BILL, COUNT(*) AS GROUP_COUNT,
           SUM(CASE WHEN d.STATUS = '{ITB_BILLED}' THEN 1 ELSE 0 END) AS SAN_BILLED_IN_GROUP,
           STRING_AGG(CAST(d.ID_ITEM_TO_BILL AS varchar(20)) + ':' + d.STATUS, ', ') AS GROUP_ITEMS
    FROM san sa
    JOIN GCCOM_ITEMS_TO_BILL d WITH (NOLOCK) ON d.ID_BILLING_SERVICE = sa.ID_BILLING_SERVICE
     AND d.INI_DATE = sa.INI_DATE AND d.END_DATE = sa.END_DATE
     AND d.STATUS <> '{ITB_CANCELLED}'   -- cancelled items are ignored (RJ 2026-10-12)
    GROUP BY sa.ID_ITEM_TO_BILL
    HAVING COUNT(*) >= 2
)
SELECT ss.NISS AS SAN_NISS, pf.REFERENCE AS ACCOUNT, sa.ID_BILLING_PERIOD, sa.INI_DATE, sa.END_DATE,
       sa.ID_BILLING_SERVICE AS SAN_ID_BILLING_SERVICE, sa.ID_ITEM_TO_BILL AS SAN_ID_ITEM_TO_BILL,
       sa.STATUS AS SAN_STATUS, g.GROUP_COUNT, g.SAN_BILLED_IN_GROUP, g.GROUP_ITEMS,
       wt.WATER_NISS, wt.WATER_ID_BILLING_SERVICE, wt.WATER_ID_ITEM_TO_BILL, wt.WATER_ID_BILL, wt.READINGS
FROM san sa
JOIN grp g ON g.ID_ITEM_TO_BILL = sa.ID_ITEM_TO_BILL
CROSS APPLY (
    SELECT STRING_AGG(CAST(x.ID_ITEM_TO_BILL AS varchar(20)), ',') AS WATER_ID_ITEM_TO_BILL,
           STRING_AGG(CAST(x.ID_BILLING_SERVICE AS varchar(20)), ',') AS WATER_ID_BILLING_SERVICE,
           STRING_AGG(CAST(x.ID_BILL AS varchar(20)), ',') AS WATER_ID_BILL,
           MAX(x.NISS) AS WATER_NISS,
           (SELECT STRING_AGG(CAST(r3.ID_READING AS varchar(20)), ',') FROM {READINGS_ITB_TABLE} r3
             WHERE r3.ID_ITEM_TO_BILL = sa.ID_ITEM_TO_BILL) AS READINGS
    FROM (
        SELECT DISTINCT w.ID_ITEM_TO_BILL, w.ID_BILLING_SERVICE, w.ID_BILL, wss.NISS
        FROM {READINGS_ITB_TABLE} r1
        JOIN {READINGS_ITB_TABLE} r2 ON r2.ID_READING = r1.ID_READING AND r2.ID_ITEM_TO_BILL <> r1.ID_ITEM_TO_BILL
        JOIN GCCOM_ITEMS_TO_BILL w WITH (NOLOCK) ON w.ID_ITEM_TO_BILL = r2.ID_ITEM_TO_BILL AND w.STATUS = '{ITB_BILLED}'
         AND w.ID_BILLING_PERIOD = sa.ID_BILLING_PERIOD AND w.INI_DATE = sa.INI_DATE AND w.END_DATE = sa.END_DATE
        JOIN GCCOM_BILLING_SERVICE wbs WITH (NOLOCK) ON wbs.ID_BILLING_SERVICE = w.ID_BILLING_SERVICE
        JOIN GCCOM_CONTRACTED_SERVICE wcs WITH (NOLOCK) ON wcs.ID_CONTRACTED_SERVICE = wbs.ID_CONTRACTED_SERVICE
         AND wcs.ID_OFFERED_SERVICE = {OFFERED_WATER}
        LEFT JOIN GCCOM_SECTOR_SUPPLY wss WITH (NOLOCK) ON wss.ID_SECTOR_SUPPLY = wcs.ID_SECTOR_SUPPLY
        WHERE r1.ID_ITEM_TO_BILL = sa.ID_ITEM_TO_BILL
    ) x
    HAVING COUNT(*) > 0
) wt
LEFT JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = sa.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = sa.ID_PAYMENT_FORM
ORDER BY CASE WHEN g.SAN_BILLED_IN_GROUP = 0 THEN 0 ELSE 1 END, ss.NISS, sa.INI_DATE
"""


# ---------------------------------------------------------------------------
# Tab 3 - "Subsidized, September not generated" (RJ 2026-10-12):
# "show all account which are subsized, (the tariff is bahraini first account
# for water or electricity), where they have already atleast 6 bill which is
# cycle and not rebilled, and credit note for the service. mainly those where
# the September bill is still not generated for all the services of the account"
#
#   subsidized service = active contracted service (STATUS NOT IN ESTSC00001/4/5)
#       whose GCCOM_BILLING_SERVICE.ID_FARE is a "Domestic - Bahraini First
#       Account" fare (GCCOM_FARE, IND_SUBSIDY = 1, live): 10000000003 E-D2,
#       10000000004 W-D2, 10000000006 E-D2-NM (net-metering)
#   qualifying = that service has >= 6 GCCOM_BILL rows with BILL_TYPE TFGEN00001
#       (cycle), BILLING_STATUS not Rebilled ESTFAC0042 / Cancelled ESTFAC0007,
#       BILLING_TYPE not Credit Note TIPFAC0011
#   per account: every active service, and whether it has a bill in the period
#       (same status / credit-note exclusions). Only accounts where at least one
#       service has no bill are returned; NONE = no service billed (main focus),
#       PARTIAL = some services billed.
#   RJ 2026-10-12 (same day): the account must also have a not-yet-billed
#       reading in the period on one of its active services (PENDING_READ_STATUSES
#       x PENDING_READING_TYPES); MISSING_BEFORE_READING_CHECK = accounts that
#       qualified before this reading condition.
# ---------------------------------------------------------------------------
SUBSIDY_FARES = {10000000003: "E-D2", 10000000004: "W-D2", 10000000006: "E-D2-NM"}
MIN_CYCLE_BILLS = 6
BILL_TYPE_CYCLE = "TFGEN00001"
BILL_EXCLUDED_STATUSES = ("ESTFAC0042", "ESTFAC0007")   # Rebilled, Cancelled
BILLING_TYPE_CREDIT_NOTE = "TIPFAC0011"
BILL_STATUS_INVOICED = "ESTFAC0005"
OFFERED_RATE = 176
OFFERED_SERVICE_TABLE = "OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE"
# RJ 2026-10-12: the account must also have a not-yet-billed reading in the
# period - status (GCGT_RE_READ_STATUS, live) and type (GCGT_RE_READING_TYPE):
PENDING_READ_STATUSES = {"5000STSRED": "Anomalous", "1000STSRED": "Available", "6000STSRED": "Sent to bill"}
PENDING_READING_TYPES = {"TIPTL00003": "Cycle", "TIPTL00017": "Distribution", "TIPTL00005": "Direct Connection"}


def build_subsidy_missing_bill_query(billing_period: int = DEFAULT_BILLING_PERIOD) -> str:
    bp = int(billing_period)
    fares = ", ".join(str(f) for f in SUBSIDY_FARES)
    cs_excl = ", ".join(f"'{s}'" for s in EXCLUDED_CS_STATUSES)
    b_excl = ", ".join(f"'{s}'" for s in BILL_EXCLUDED_STATUSES)
    bill_ok = f"b.BILLING_STATUS NOT IN ({b_excl}) AND ISNULL(b.BILLING_TYPE, '') <> '{BILLING_TYPE_CREDIT_NOTE}'"
    pend_stat = ", ".join(f"'{s}'" for s in PENDING_READ_STATUSES)
    pend_types = ", ".join(f"'{s}'" for s in PENDING_READING_TYPES)
    fare_name = "CASE c.ID_FARE " + " ".join(f"WHEN {f} THEN '{n}'" for f, n in SUBSIDY_FARES.items()) + " END"
    st_name = "CASE p.READ_STATUS " + " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in PENDING_READ_STATUSES.items()) + " ELSE p.READ_STATUS END"
    ty_name = "CASE p.READING_TYPE " + " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in PENDING_READING_TYPES.items()) + " ELSE p.READING_TYPE END"
    svc_name = f"COALESCE(os.NAME_TYPE, CAST(v.ID_OFFERED_SERVICE AS varchar(10)))"
    # RJ 2026-10-12 speed-up: CTEs were re-evaluated for every reference
    # (timed out). A temp-table batch computes each step once (live ~22s
    # vs ~110s): accounts missing a bill are found FIRST, then cycle bills
    # and readings are looked at only for those accounts. #temp tables are
    # session-local tempdb objects - nothing is written to the OUC database.
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#sub') IS NOT NULL DROP TABLE #sub;
IF OBJECT_ID('tempdb..#svc') IS NOT NULL DROP TABLE #svc;
IF OBJECT_ID('tempdb..#miss') IS NOT NULL DROP TABLE #miss;
IF OBJECT_ID('tempdb..#cyc') IS NOT NULL DROP TABLE #cyc;
IF OBJECT_ID('tempdb..#rdp') IS NOT NULL DROP TABLE #rdp;

-- subsidized (Bahraini First Account) active services
SELECT cs.ID_PAYMENT_FORM, cs.ID_CONTRACTED_SERVICE, bs.ID_FARE
INTO #sub
FROM GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
JOIN GCCOM_BILLING_SERVICE bs WITH (NOLOCK) ON bs.ID_CONTRACTED_SERVICE = cs.ID_CONTRACTED_SERVICE
WHERE bs.ID_FARE IN ({fares}) AND cs.STATUS NOT IN ({cs_excl});

-- every active service of those accounts + does it have a bill in the period
SELECT cs.ID_PAYMENT_FORM, cs.ID_CONTRACTED_SERVICE, cs.ID_OFFERED_SERVICE, cs.ID_SECTOR_SUPPLY,
       CASE WHEN EXISTS (SELECT 1 FROM GCCOM_BILL b WITH (NOLOCK)
                          WHERE b.ID_CONTRACTED_SERVICE = cs.ID_CONTRACTED_SERVICE
                            AND b.ID_BILLING_PERIOD = {bp} AND {bill_ok}) THEN 1 ELSE 0 END AS HAS_BILL
INTO #svc
FROM GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
WHERE cs.STATUS NOT IN ({cs_excl})
  AND cs.ID_PAYMENT_FORM IN (SELECT ID_PAYMENT_FORM FROM #sub);

-- accounts where at least one active service has no bill in the period
SELECT ID_PAYMENT_FORM, COUNT(*) AS SERVICES, SUM(HAS_BILL) AS SERVICES_BILLED
INTO #miss
FROM #svc GROUP BY ID_PAYMENT_FORM HAVING SUM(HAS_BILL) < COUNT(*);

-- subsidized services of those accounts with >= {MIN_CYCLE_BILLS} cycle bills
SELECT s.ID_PAYMENT_FORM, s.ID_CONTRACTED_SERVICE, s.ID_FARE, COUNT(*) AS CYCLE_BILLS,
       MAX(b.ID_BILLING_PERIOD) AS LAST_CYCLE_PERIOD
INTO #cyc
FROM #sub s
JOIN #miss m ON m.ID_PAYMENT_FORM = s.ID_PAYMENT_FORM
JOIN GCCOM_BILL b WITH (NOLOCK) ON b.ID_CONTRACTED_SERVICE = s.ID_CONTRACTED_SERVICE
WHERE b.BILL_TYPE = '{BILL_TYPE_CYCLE}' AND {bill_ok}
GROUP BY s.ID_PAYMENT_FORM, s.ID_CONTRACTED_SERVICE, s.ID_FARE
HAVING COUNT(*) >= {MIN_CYCLE_BILLS};

-- RJ 2026-10-12: "we need to consider that there is a reading that is either
-- anomalous or available or sent to bill (cycle, distribution or direct
-- connection) ... that is not yet billed" - readings of the period on the
-- account's active services' sector supplies.
SELECT v.ID_PAYMENT_FORM, v.HAS_BILL, {svc_name} AS SVC,
       r.ID_READING, r.READ_STATUS, r.READING_TYPE
INTO #rdp
FROM #svc v
JOIN #miss m ON m.ID_PAYMENT_FORM = v.ID_PAYMENT_FORM
JOIN GCGT_RE_READING r WITH (NOLOCK) ON r.ID_SECTOR_SUPPLY = v.ID_SECTOR_SUPPLY
 AND r.ID_BILLING_PERIOD = {bp}
 AND r.READ_STATUS IN ({pend_stat})
 AND r.READING_TYPE IN ({pend_types})
LEFT JOIN {OFFERED_SERVICE_TABLE} os ON os.ID_OFFERED_SERVICE = v.ID_OFFERED_SERVICE;

SELECT pf.REFERENCE AS ACCOUNT, m.ID_PAYMENT_FORM, m.SERVICES, m.SERVICES_BILLED,
       CASE WHEN m.SERVICES_BILLED = 0 THEN 'NONE' ELSE 'PARTIAL' END AS PERIOD_BILL_STATUS,
       (SELECT STRING_AGG({svc_name}, ', ') FROM #svc v
          LEFT JOIN {OFFERED_SERVICE_TABLE} os ON os.ID_OFFERED_SERVICE = v.ID_OFFERED_SERVICE
          WHERE v.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM AND v.HAS_BILL = 0) AS MISSING_SERVICES,
       (SELECT STRING_AGG({svc_name}, ', ') FROM #svc v
          LEFT JOIN {OFFERED_SERVICE_TABLE} os ON os.ID_OFFERED_SERVICE = v.ID_OFFERED_SERVICE
          WHERE v.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM AND v.HAS_BILL = 1) AS BILLED_SERVICES,
       (SELECT STRING_AGG({fare_name} + ' (' + CAST(c.CYCLE_BILLS AS varchar(10)) + ' bills)', ', ')
          FROM #cyc c WHERE c.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM) AS SUBSIDIZED_TARIFFS,
       (SELECT MIN(c.CYCLE_BILLS) FROM #cyc c WHERE c.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM) AS MIN_CYCLE_BILLS,
       -- RJ 2026-10-12: "show description of the period not the id"
       (SELECT bp2.DESCRIPTION FROM GCCOM_BILLING_PERIOD bp2 WITH (NOLOCK)
         WHERE bp2.ID_BILLING_PERIOD = (SELECT MAX(c.LAST_CYCLE_PERIOD) FROM #cyc c WHERE c.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM)) AS LAST_CYCLE_PERIOD,
       (SELECT COUNT(*) FROM #rdp p WHERE p.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM AND p.HAS_BILL = 0) AS PENDING_READINGS_ON_MISSING,
       (SELECT STRING_AGG(p.SVC + ': ' + CAST(p.ID_READING AS varchar(20)) + ' ' + {st_name} + ' / ' + {ty_name}, '; ')
          FROM #rdp p WHERE p.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM) AS PENDING_READINGS,
       li.ID_BILLING_PERIOD AS LAST_INVOICED_PERIOD_ID,
       lbp.DESCRIPTION AS LAST_INVOICED_PERIOD,
       CASE li.ID_OFFERED_SERVICE WHEN {OFFERED_ELECTRICITY} THEN 'Electricity' WHEN {OFFERED_WATER} THEN 'Water'
            WHEN {OFFERED_RATE} THEN 'Rate' END AS LAST_INVOICED_FROM,
       (SELECT COUNT(DISTINCT c.ID_PAYMENT_FORM) FROM #cyc c) AS MISSING_BEFORE_READING_CHECK
FROM #miss m
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM
-- RJ 2026-10-12: "latest invoiced period for the account, initially check
-- electricity, if no ele, check water, if no water check rate" - latest
-- Invoiced (ESTFAC0005, not Credit Note) bill period of the first of those
-- services (in that order) that has one.
OUTER APPLY (
    SELECT TOP 1 x.ID_OFFERED_SERVICE, x.ID_BILLING_PERIOD
    FROM (SELECT v.ID_OFFERED_SERVICE, MAX(b.ID_BILLING_PERIOD) AS ID_BILLING_PERIOD
            FROM #svc v
            JOIN GCCOM_BILL b WITH (NOLOCK) ON b.ID_CONTRACTED_SERVICE = v.ID_CONTRACTED_SERVICE
             AND b.BILLING_STATUS = '{BILL_STATUS_INVOICED}'
             AND ISNULL(b.BILLING_TYPE, '') <> '{BILLING_TYPE_CREDIT_NOTE}'
           WHERE v.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM
             AND v.ID_OFFERED_SERVICE IN ({OFFERED_ELECTRICITY}, {OFFERED_WATER}, {OFFERED_RATE})
           GROUP BY v.ID_OFFERED_SERVICE) x
    ORDER BY CASE x.ID_OFFERED_SERVICE WHEN {OFFERED_ELECTRICITY} THEN 1 WHEN {OFFERED_WATER} THEN 2 ELSE 3 END
) li
LEFT JOIN GCCOM_BILLING_PERIOD lbp WITH (NOLOCK) ON lbp.ID_BILLING_PERIOD = li.ID_BILLING_PERIOD
WHERE EXISTS (SELECT 1 FROM #cyc c WHERE c.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM)
  AND EXISTS (SELECT 1 FROM #rdp p WHERE p.ID_PAYMENT_FORM = m.ID_PAYMENT_FORM)
ORDER BY CASE WHEN m.SERVICES_BILLED = 0 THEN 0 ELSE 1 END, pf.REFERENCE;

DROP TABLE #sub; DROP TABLE #svc; DROP TABLE #miss; DROP TABLE #cyc; DROP TABLE #rdp;
"""


# ---------------------------------------------------------------------------
# Tab 4 - "Water vs Sanitary status" (RJ 2026-10-14): "find me cases of
# account where the water service is active but the sanitary is pending
# termination, or another case where the water is cancelled, but the
# sanitary is pending".
# GCCOM_CONTRACT_SERV_STATUS (live): ESTSC00001 Pending, 00002 Active,
# 00003 Pending termination, 00004 Terminated, 00005 Cancelled, 00007 Suspended.
#   Case A = Water (19) Active   + Sanitary (190) Pending termination
#   Case B = Water (19) Cancelled + Sanitary (190) Pending
# RJ 2026-10-15 correction: "i want for same account and the supply of water
# and sanitary is the same" - the first version paired by NISS base across
# accounts (wrong). Now: SAME payment form AND the same supply - the Water and
# Sanitary sector supplies share GCCOM_SECTOR_SUPPLY.ID_SUPPLY (live: Water
# 10221619-301 / Sanitary 10221619-701 -> ID_SUPPLY 221619).
#   Case A = Sanitary Pending termination (ESTSC00003) + Water Active (ESTSC00002)
#   Case B = Water Cancelled (ESTSC00005) + Sanitary Pending (ESTSC00001)
# ---------------------------------------------------------------------------
CS_STATUS_NAMES = {"ESTSC00001": "Pending", "ESTSC00002": "Active", "ESTSC00003": "Pending termination",
                   "ESTSC00004": "Terminated", "ESTSC00005": "Cancelled", "ESTSC00007": "Suspended"}


def build_water_sanitary_status_query() -> str:
    return f"""
SELECT CASE WHEN s.STATUS = 'ESTSC00003' THEN 'A' ELSE 'B' END AS CASE_TYPE,
       pf.REFERENCE AS ACCOUNT, s.ID_PAYMENT_FORM, sss.ID_SUPPLY,
       sss.NISS AS SAN_NISS, s.ID_CONTRACTED_SERVICE AS SAN_CS, s.STATUS AS SAN_STATUS,
       s.FROM_DATE AS SAN_FROM, s.DROP_DATE AS SAN_DROP,
       wss.NISS AS WATER_NISS, w.ID_CONTRACTED_SERVICE AS WATER_CS, w.STATUS AS WATER_STATUS,
       w.FROM_DATE AS WATER_FROM, w.DROP_DATE AS WATER_DROP
FROM GCCOM_CONTRACTED_SERVICE s WITH (NOLOCK)
JOIN GCCOM_SECTOR_SUPPLY sss WITH (NOLOCK) ON sss.ID_SECTOR_SUPPLY = s.ID_SECTOR_SUPPLY
JOIN GCCOM_CONTRACTED_SERVICE w WITH (NOLOCK)
     ON w.ID_PAYMENT_FORM = s.ID_PAYMENT_FORM AND w.ID_OFFERED_SERVICE = {OFFERED_WATER}
JOIN GCCOM_SECTOR_SUPPLY wss WITH (NOLOCK)
     ON wss.ID_SECTOR_SUPPLY = w.ID_SECTOR_SUPPLY AND wss.ID_SUPPLY = sss.ID_SUPPLY
JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = s.ID_PAYMENT_FORM
WHERE s.ID_OFFERED_SERVICE = {OFFERED_SANITARY}
  AND ((s.STATUS = 'ESTSC00003' AND w.STATUS = 'ESTSC00002')
    OR (s.STATUS = 'ESTSC00001' AND w.STATUS = 'ESTSC00005'))
ORDER BY 1, pf.REFERENCE
"""


# ---------------------------------------------------------------------------
# Tab 5 "DDI eligibility x complaints" (RJ 2026-10-18): "create new temp
# dashboard with all this customer select * from EWA_DDI_ELIGIBILITY, connect
# to gccc_complaint, with complaint type 4102COMTYP and 4103COMTYP".
# Live 2026-10-18: OUC_COMMON_ADMIN.EWA_DDI_ELIGIBILITY 153,236 rows (one per
# id_payment_form); GCCC_COMPLAINT.ID_ACCOUNT = GCCOM_PAYMENT_FORM.ID_PAYMENT_FORM;
# GCCC_COMPLAINT_TYPE.COD_DEVELOP 4102COMTYP "Direct Debit Set Up" (id
# 10000000103), 4103COMTYP "Internal - Direct Debit Request" (10000000106);
# 39,965 eligible accounts have at least one of those complaints.
# One row per eligible account: every EWA column + complaint counts, the latest
# complaint (any of the two types) and the latest status per type. Scope
# "with" (default) / "without" / "all"; "without"/"all" capped at DDI_ROW_CAP.
# ---------------------------------------------------------------------------
DDI_TABLE = "OUC_COMMON_ADMIN.EWA_DDI_ELIGIBILITY"
DDI_COMPLAINT_TYPES = {"4102COMTYP": "Direct Debit Set Up", "4103COMTYP": "Internal - Direct Debit Request"}
DDI_SCOPES = ("with", "without", "all")
DDI_ROW_CAP = 50000


def build_ddi_complaint_query(scope: str = "with") -> str:
    scope = scope if scope in DDI_SCOPES else "with"
    types = ", ".join(f"'{t}'" for t in DDI_COMPLAINT_TYPES)   # fixed codes, plain varchar literals
    where = {"with": "WHERE ag.N_COMPLAINTS IS NOT NULL",
             "without": "WHERE ag.N_COMPLAINTS IS NULL",
             "all": ""}[scope]
    top = "" if scope == "with" else f"TOP {DDI_ROW_CAP} "
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#t') IS NOT NULL DROP TABLE #t;
IF OBJECT_ID('tempdb..#c') IS NOT NULL DROP TABLE #c;
IF OBJECT_ID('tempdb..#ag') IS NOT NULL DROP TABLE #ag;
DECLARE @all int, @with int;

SELECT t.ID_COMPLAINT_TYPE, t.COD_DEVELOP, COALESCE(d.TEXT, t.NAME_TYPE) AS TYPE_EN
INTO #t
FROM OUC_ADMIN.GCCC_COMPLAINT_TYPE t
LEFT JOIN GCTS_DICTIONARY d ON d.ID = t.NAME_TYPE_XI18N AND d.LOCALE = 'EN'
WHERE t.COD_DEVELOP IN ({types});

SELECT c.ID_ACCOUNT, c.ID_COMPLAINT, c.COD_REF_COMPLAINT, t.COD_DEVELOP AS TYPE_CODE, t.TYPE_EN,
       c.COD_STATUS_COMPLAINT, c.CREATE_DATE, c.UPDATE_DATE,
       ROW_NUMBER() OVER (PARTITION BY c.ID_ACCOUNT ORDER BY c.CREATE_DATE DESC, c.ID_COMPLAINT DESC) AS RN,
       ROW_NUMBER() OVER (PARTITION BY c.ID_ACCOUNT, t.COD_DEVELOP ORDER BY c.CREATE_DATE DESC, c.ID_COMPLAINT DESC) AS RN_TYPE
INTO #c
FROM OUC_ADMIN.GCCC_COMPLAINT c WITH (NOLOCK)
JOIN #t t ON t.ID_COMPLAINT_TYPE = c.ID_COMPLAINT_TYPE
JOIN {DDI_TABLE} e WITH (NOLOCK) ON e.id_payment_form = c.ID_ACCOUNT;

SELECT ID_ACCOUNT, COUNT(*) AS N_COMPLAINTS,
       SUM(CASE WHEN TYPE_CODE = '4102COMTYP' THEN 1 ELSE 0 END) AS N_4102,
       SUM(CASE WHEN TYPE_CODE = '4103COMTYP' THEN 1 ELSE 0 END) AS N_4103,
       MIN(CREATE_DATE) AS FIRST_COMPLAINT_DATE,
       MAX(CASE WHEN TYPE_CODE = '4102COMTYP' AND RN_TYPE = 1 THEN COD_STATUS_COMPLAINT END) AS LAST_4102_STATUS,
       MAX(CASE WHEN TYPE_CODE = '4103COMTYP' AND RN_TYPE = 1 THEN COD_STATUS_COMPLAINT END) AS LAST_4103_STATUS
INTO #ag
FROM #c GROUP BY ID_ACCOUNT;

SELECT @all = COUNT(*) FROM {DDI_TABLE};
SELECT @with = COUNT(*) FROM #ag;

SELECT {top}e.*,
       ISNULL(ag.N_COMPLAINTS, 0) AS N_COMPLAINTS, ISNULL(ag.N_4102, 0) AS N_4102, ISNULL(ag.N_4103, 0) AS N_4103,
       ag.FIRST_COMPLAINT_DATE,
       lc.COD_REF_COMPLAINT AS LAST_COMPLAINT_REF, lc.TYPE_CODE AS LAST_TYPE_CODE, lc.TYPE_EN AS LAST_TYPE,
       lc.COD_STATUS_COMPLAINT AS LAST_STATUS, COALESCE(ds.TEXT, st.NAME_TYPE) AS LAST_STATUS_EN,
       lc.CREATE_DATE AS LAST_COMPLAINT_DATE, lc.UPDATE_DATE AS LAST_COMPLAINT_UPDATE,
       ag.LAST_4102_STATUS, COALESCE(d1.TEXT, s1.NAME_TYPE) AS LAST_4102_STATUS_EN,
       ag.LAST_4103_STATUS, COALESCE(d2.TEXT, s2.NAME_TYPE) AS LAST_4103_STATUS_EN,
       @all AS EWA_TOTAL, @with AS EWA_WITH_COMPLAINT
FROM {DDI_TABLE} e WITH (NOLOCK)
LEFT JOIN #ag ag ON ag.ID_ACCOUNT = e.id_payment_form
LEFT JOIN #c lc ON lc.ID_ACCOUNT = e.id_payment_form AND lc.RN = 1
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_STATUS st ON st.COD_DEVELOP = lc.COD_STATUS_COMPLAINT
LEFT JOIN GCTS_DICTIONARY ds ON ds.ID = st.NAME_TYPE_XI18N AND ds.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_STATUS s1 ON s1.COD_DEVELOP = ag.LAST_4102_STATUS
LEFT JOIN GCTS_DICTIONARY d1 ON d1.ID = s1.NAME_TYPE_XI18N AND d1.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_STATUS s2 ON s2.COD_DEVELOP = ag.LAST_4103_STATUS
LEFT JOIN GCTS_DICTIONARY d2 ON d2.ID = s2.NAME_TYPE_XI18N AND d2.LOCALE = 'EN'
{where}
ORDER BY e.account;

DROP TABLE #t; DROP TABLE #c; DROP TABLE #ag;
"""


# ---------------------------------------------------------------------------
# RJ 2026-10-22: Temp Dashboard tab 6 "RCCS list check" - "extract details for me for the
# following excel ... use rccs and account, give me the id_complaint, status, id_wallet"
# + "add for me the wallet amount and generate a temp dashboard".
# A pasted list of (RCCS reference, account) -> ONE output row per input row, same order
# (duplicates kept): complaint matched on COD_REF_COMPLAINT AND ID_ACCOUNT = the account's
# payment form (one RCCS can exist once per account), its status (EN), type, and the
# subsidy wallet (ID_WALLET, BALANCE, COD_BEHAVIOUR) of the payment form.
# Live 2026-10-22 on CANCEL_RCCS_NBB_BENIFIT (189 rows): 186 matched, 3 SR... refs not found.
RCCS_LIST_MAX = 5000
_REF_RE = re.compile(r"^[A-Za-z0-9]{1,40}$")
_ACC_RE = re.compile(r"^[0-9]{1,15}$")


def parse_rccs_list(text: str | None) -> list[dict]:
    """Rows pasted from Excel (tab / comma / ; separated). With a header line holding
    RCCS + ACCOUNT the named columns are used (plus REASON / COMMENTS as the group);
    without one, the first RCCS-like token and the first 7-15 digit number are taken.
    Every non-empty line becomes one row (invalid ones keep their place with a note)."""
    lines = [ln for ln in str(text or "").replace("\r", "").split("\n") if ln.strip()]
    if not lines:
        return []
    split = lambda ln: [c.strip() for c in re.split(r"\t|,|;", ln)]
    head = [h.upper() for h in split(lines[0])]
    rows: list[dict] = []
    if "RCCS" in head and "ACCOUNT" in head:
        ir, ia = head.index("RCCS"), head.index("ACCOUNT")
        ig = head.index("COMMENTS") if "COMMENTS" in head else (head.index("REASON") if "REASON" in head else -1)
        for ln in lines[1:]:
            c = split(ln)
            get = lambda i: c[i] if 0 <= i < len(c) else ""
            rows.append({"rccs": get(ir), "account": get(ia), "group": get(ig)})
    else:
        for ln in lines:
            toks = [t for t in split(ln) if t]
            ref = next((t for t in toks if re.match(r"^(RCCS|RFRON|SR)\w+$", t, re.I)), toks[0] if toks else "")
            acc = next((t for t in toks if t != ref and re.match(r"^\d{7,15}$", t)), "")
            rest = [t for t in toks if t not in (ref, acc)]
            rows.append({"rccs": ref, "account": acc, "group": rest[-1] if rest else ""})
    for i, r in enumerate(rows, 1):
        r["n"] = i
        r["rccs"] = r["rccs"].strip().upper()
        r["account"] = r["account"].strip()
        r["valid"] = bool(_REF_RE.match(r["rccs"]) and _ACC_RE.match(r["account"]))
    return rows[:RCCS_LIST_MAX]


def build_rccs_list_query(rows: list[dict]) -> str:
    """One output row per VALID input row (N = input position), in input order."""
    vals = ",".join(f"({int(r['n'])},'{r['rccs']}','{r['account']}')" for r in rows if r.get("valid"))
    if not vals:
        vals = "(0,'','')"
    return f"""
SET NOCOUNT ON;
WITH inp(N, RCCS, ACC) AS (SELECT * FROM (VALUES {vals}) v(N, RCCS, ACC))
SELECT i.N, pf.ID_PAYMENT_FORM,
       ex.ID_COMPLAINT, ex.COD_STATUS_COMPLAINT AS STATUS_CODE, COALESCE(ds.TEXT, st.NAME_TYPE) AS STATUS_EN,
       ex.TYPE_CODE AS COMPLAINT_TYPE, ex.RCCS_DATE,
       (SELECT COUNT(*) FROM OUC_ADMIN.GCCC_COMPLAINT c2 WITH (NOLOCK) WHERE c2.COD_REF_COMPLAINT = i.RCCS) AS N_REF_ANY_ACCOUNT,
       w.ID_WALLET, w.BALANCE AS WALLET_AMOUNT, w.COD_BEHAVIOUR, bv.TXT AS BEHAVIOUR_DESC
FROM inp i
OUTER APPLY (SELECT TOP 1 p.ID_PAYMENT_FORM FROM GCCOM_PAYMENT_FORM p WITH (NOLOCK)
             WHERE p.REFERENCE = i.ACC ORDER BY p.ID_PAYMENT_FORM DESC) pf
OUTER APPLY (SELECT TOP 1 c.ID_COMPLAINT, c.COD_STATUS_COMPLAINT, c.CREATE_DATE AS RCCS_DATE, ct.COD_DEVELOP AS TYPE_CODE
             FROM OUC_ADMIN.GCCC_COMPLAINT c WITH (NOLOCK)
             LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_TYPE ct ON ct.ID_COMPLAINT_TYPE = c.ID_COMPLAINT_TYPE
             WHERE c.COD_REF_COMPLAINT = i.RCCS AND c.ID_ACCOUNT = pf.ID_PAYMENT_FORM
             ORDER BY c.ID_COMPLAINT DESC) ex
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_STATUS st ON st.COD_DEVELOP = ex.COD_STATUS_COMPLAINT
LEFT JOIN GCTS_DICTIONARY ds ON ds.ID = st.NAME_TYPE_XI18N AND ds.LOCALE = 'EN'
LEFT JOIN OUC_COMMON_ADMIN.EWA_GCCOM_SUBSIDY_WALLET w WITH (NOLOCK) ON w.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM
LEFT JOIN (SELECT v.COD_DEVELOP, COALESCE(d.TEXT, v.NAME_TYPE) AS TXT
           FROM OUC_COMMON_ADMIN.GCGT_ATTRIBUTE_VALUES v
           LEFT JOIN GCTS_DICTIONARY d ON d.ID = v.NAME_TYPE_XI18N AND d.LOCALE = 'EN'
           WHERE v.COD_MASTER_ATTRIBUTE = 'RCCSCRDUSG') bv ON bv.COD_DEVELOP = w.COD_BEHAVIOUR
ORDER BY i.N;
"""


def rccs_list_note(r: dict) -> str:
    if not r.get("valid"):
        return "Invalid RCCS / account in the pasted row"
    if not str(r.get("id_payment_form") or "").strip():
        return "Account not found"
    if not str(r.get("id_complaint") or "").strip():
        if str(r.get("rccs", "")).startswith("SR"):
            return "Not found - reference is a Service Request (SR...), not an RCCS"
        if int(str(r.get("n_ref_any_account") or "0") or 0) > 0:
            return "RCCS exists but on another account"
        return "RCCS not found"
    if not str(r.get("id_wallet") or "").strip():
        return "No subsidy wallet for this payment form"
    return ""


# ---------------------------------------------------------------------------
# Tab 7 "Sanitary: last bill date > billing date" (RJ 2026-10-24): "find me all sanitary
# service bill where the last bill date is > billing date, show me status billing period
# account bill number and bill amount and pending amount".
#   GCCOM_BILL with ID_OFFERED_SERVICE = 190 (Sanitary) and LAST_BILLING_DATE > BILLING_DATE.
#   Status EN = OUC_ADMIN.GCCOM_BILL_STATUS via GCTS_DICTIONARY. Live 2026-10-24: 139 bills, ~3 s.
# ---------------------------------------------------------------------------
def build_sanitary_last_bill_date_query() -> str:
    return f"""
SELECT pf.REFERENCE AS ACCOUNT, ss.NISS, b.ID_BILL, b.BILL_NUMBER,
       b.BILLING_STATUS, COALESCE(dst.TEXT, st.NAME_TYPE) AS BILL_STATUS,
       b.BILL_TYPE, b.ID_BILLING_PERIOD, COALESCE(dbp.TEXT, bp.DESCRIPTION) AS BILLING_PERIOD,
       b.BILLING_DATE, b.LAST_BILLING_DATE,
       DATEDIFF(day, b.BILLING_DATE, b.LAST_BILLING_DATE) AS DAYS_AFTER,
       b.TOTAL_AMOUNT AS BILL_AMOUNT, b.PENDING_AMOUNT
FROM OUC_COMMON_ADMIN.GCCOM_BILL b WITH (NOLOCK)
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
LEFT JOIN GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK) ON cs.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE
LEFT JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
LEFT JOIN OUC_ADMIN.GCCOM_BILL_STATUS st ON st.COD_DEVELOP = b.BILLING_STATUS
LEFT JOIN GCTS_DICTIONARY dst ON dst.ID = st.NAME_TYPE_XI18N AND dst.LOCALE = 'EN'
LEFT JOIN GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = b.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY dbp ON dbp.ID = bp.PERIOD_NAME_XI18N AND dbp.LOCALE = 'EN'
WHERE b.ID_OFFERED_SERVICE = {OFFERED_SANITARY}
  AND b.LAST_BILLING_DATE > b.BILLING_DATE
ORDER BY b.ID_BILLING_PERIOD DESC, pf.REFERENCE;
"""


def build_duplicate_rccs_query(since: str | None = None) -> str:
    """RJ 2026-10-25: "Duplicate RCCS, this will be the ones that we use in FIX Billing campaign,
    vs the one from the non campaign.. list them down with the dates. the idea is they have
    same cod_complaint".

    Campaign RCCS = the RCCS reference of the FIX BILLING CAMPAIGN 2026 requests
    (GCGE_EXTERNAL_MESSAGES_IN, '/mobileapp/complaint', since the campaign date - same parsing
    as fix_billing_campaign.build_query). Every GCCC_COMPLAINT sharing that COD_REF_COMPLAINT is
    listed when the reference exists as 2+ complaints. RCCS_ROLE per complaint:
      CAMPAIGN                - (reference, account) = the account's latest campaign request
      CAMPAIGN (older request)- an earlier campaign request of that account used this reference
      NON-CAMPAIGN            - no campaign request of that account used this reference
    RJ 2026-10-25 (later): "the duplicating RCCS should be not from campaign" / "the main rccs is
    the campaign, but the duplicationg should be non camapaign" - only references with at least
    one NON-CAMPAIGN complaint are returned (main = campaign, duplicate = non-campaign).
    Live 2026-10-25: 4 references / 8 complaints (all duplicates 3084COMTYP "Resolved (Applicable)",
    created before the campaign request), ~20-50s.
    """
    from app.core import fix_billing_campaign as fbc
    since = fbc._since(since)
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#r') IS NOT NULL DROP TABLE #r;
IF OBJECT_ID('tempdb..#c') IS NOT NULL DROP TABLE #c;

-- all campaign requests since the date (RN = 1 = latest per account)
SELECT mi.ID_SEQ, mi.CREATE_DATE AS REQ_DATE, j.ID_ACCOUNT_REF, j.RCCS_REF,
       ROW_NUMBER() OVER (PARTITION BY j.ID_ACCOUNT_REF ORDER BY mi.CREATE_DATE DESC, mi.ID_SEQ DESC) AS RN
INTO #r
FROM {fbc.MESSAGES_TABLE} mi WITH (NOLOCK)
CROSS APPLY (SELECT
    CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.idAccount') END AS ID_ACCOUNT_REF,
    COALESCE(CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.rccsReference') END,
             LEFT(mi.RESPONSEENTITY, 60)) AS RCCS_REF
) j
WHERE mi.REQUESTURL = '{fbc.REQUEST_URL}' AND mi.CREATE_DATE > '{since}'
  AND {fbc.REQUEST_OK_SQL};   -- RJ 2026-10-25: error responses (RESPONSEHEADER filled) left out

-- every complaint carrying a campaign RCCS reference + how many complaints share it
SELECT c.COD_REF_COMPLAINT, c.ID_COMPLAINT, c.ID_ACCOUNT, c.CREATE_DATE, c.UPDATE_DATE, c.UPDATE_USER,
       c.COD_STATUS_COMPLAINT, c.ID_COMPLAINT_TYPE, c.COD_ORIGIN,
       COUNT(*) OVER (PARTITION BY c.COD_REF_COMPLAINT) AS N_COMPLAINTS
INTO #c
FROM OUC_ADMIN.GCCC_COMPLAINT c WITH (NOLOCK)
WHERE c.COD_REF_COMPLAINT IN (SELECT RCCS_REF FROM #r WHERE RN = 1);

IF OBJECT_ID('tempdb..#x') IS NOT NULL DROP TABLE #x;
SELECT c.COD_REF_COMPLAINT AS RCCS, c.N_COMPLAINTS,
       CASE WHEN rq.RN = 1 THEN 'CAMPAIGN' WHEN rq.RN > 1 THEN 'CAMPAIGN (older request)'
            ELSE 'NON-CAMPAIGN' END AS RCCS_ROLE,
       pf.REFERENCE AS ACCOUNT, c.ID_COMPLAINT,
       c.CREATE_DATE AS RCCS_DATE, c.UPDATE_DATE AS RCCS_UPDATED, c.UPDATE_USER,
       rq.REQ_DATE, rq.ID_SEQ AS REQ_ID,
       c.COD_STATUS_COMPLAINT AS RCCS_STATUS, COALESCE(ds.TEXT, st.NAME_TYPE) AS RCCS_STATUS_EN,
       ct.COD_DEVELOP AS RCCS_TYPE, c.COD_ORIGIN,
       lr.RCCS_REF AS ACCOUNT_LATEST_CAMPAIGN_RCCS, lr.REQ_DATE AS ACCOUNT_LATEST_REQ_DATE,
       c.CREATE_DATE AS SORT_DATE
INTO #x
FROM #c c
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = c.ID_ACCOUNT
OUTER APPLY (SELECT TOP 1 r.RN, r.REQ_DATE, r.ID_SEQ FROM #r r
             WHERE r.RCCS_REF = c.COD_REF_COMPLAINT AND r.ID_ACCOUNT_REF = pf.REFERENCE ORDER BY r.RN) rq
LEFT JOIN #r lr ON lr.ID_ACCOUNT_REF = pf.REFERENCE AND lr.RN = 1
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_TYPE ct ON ct.ID_COMPLAINT_TYPE = c.ID_COMPLAINT_TYPE
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_STATUS st ON st.COD_DEVELOP = c.COD_STATUS_COMPLAINT
LEFT JOIN GCTS_DICTIONARY ds ON ds.ID = st.NAME_TYPE_XI18N AND ds.LOCALE = 'EN'
WHERE c.N_COMPLAINTS > 1;

-- RJ 2026-10-25 (later): "the duplicating RCCS should be not from campaign" - keep only the
-- references where at least one complaint is NON-CAMPAIGN (campaign-vs-campaign pairs dropped).
SELECT RCCS, N_COMPLAINTS, RCCS_ROLE, ACCOUNT, ID_COMPLAINT, RCCS_DATE, RCCS_UPDATED, UPDATE_USER,
       REQ_DATE, REQ_ID, RCCS_STATUS, RCCS_STATUS_EN, RCCS_TYPE, COD_ORIGIN,
       ACCOUNT_LATEST_CAMPAIGN_RCCS, ACCOUNT_LATEST_REQ_DATE
FROM #x
WHERE RCCS IN (SELECT RCCS FROM #x WHERE RCCS_ROLE = 'NON-CAMPAIGN')
ORDER BY RCCS, SORT_DATE, ID_COMPLAINT;

DROP TABLE #r; DROP TABLE #c; DROP TABLE #x;
"""


def parse_accounts(text: str | None) -> list[str]:
    """Accounts pasted as one per line / comma / space separated -> unique list
    (RJ 2026-10-11: "let's focus on this accounts for now" - 387 pasted)."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in str(text or "").replace(",", " ").replace(";", " ").split():
        acc = "".join(ch for ch in tok if ch.isalnum())
        if acc and acc.lower() != "account" and acc not in seen:
            seen.add(acc)
            out.append(acc)
    return out


def build_query(billing_period: int = DEFAULT_BILLING_PERIOD, account: str | list[str] | None = None) -> str:
    bp = int(billing_period)
    cs_excl = ", ".join(f"'{s}'" for s in EXCLUDED_CS_STATUSES)
    rtypes = ", ".join(f"'{s}'" for s in READING_TYPES)
    rstat = ", ".join(f"'{s}'" for s in READING_STATUSES)
    accounts = account if isinstance(account, list) else parse_accounts(account)
    acc_filter = ""
    if len(accounts) == 1:
        acc_filter = f"\n      AND o.ACCOUNT = '{accounts[0]}'"
    elif accounts:
        acc_filter = "\n      AND o.ACCOUNT IN (" + ", ".join(f"'{a}'" for a in accounts) + ")"
    return f"""
WITH acc AS (
    SELECT o.ACCOUNT, o.ID_PAYMENT_FORM
    FROM OUC_COMMON_ADMIN.EWA_DDI_ELIGIBILITY o
    WHERE ISNULL(o.{BILL_AMOUNT_COLUMN}, 0) = 0{acc_filter}
    GROUP BY o.ACCOUNT, o.ID_PAYMENT_FORM
),
svc AS (
    SELECT a.ID_PAYMENT_FORM, cs.ID_CONTRACTED_SERVICE, cs.ID_OFFERED_SERVICE, cs.ID_SECTOR_SUPPLY
    FROM acc a
    JOIN GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
      ON cs.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM
     AND cs.STATUS NOT IN ({cs_excl})
     AND cs.ID_OFFERED_SERVICE IN ({OFFERED_ELECTRICITY}, {OFFERED_WATER})
),
rd AS (
    SELECT DISTINCT r.ID_SECTOR_SUPPLY
    FROM GCGT_RE_READING r WITH (NOLOCK)
    WHERE r.ID_BILLING_PERIOD = {bp}
      AND r.READ_STATUS IN ({rstat})
      AND r.READING_TYPE IN ({rtypes})
      AND r.ID_SECTOR_SUPPLY IN (SELECT ID_SECTOR_SUPPLY FROM svc)
),
-- RJ 2026-10-11: "add 2 columns, 1 to check if ele is billed, if water is
-- billed" + "i am saying read_status = '7000STSRED'": billed = the service's
-- sector supply has a reading of the same period / reading types whose
-- READ_STATUS is 7000STSRED (Billed).
rb AS (
    SELECT DISTINCT r.ID_SECTOR_SUPPLY
    FROM GCGT_RE_READING r WITH (NOLOCK)
    WHERE r.ID_BILLING_PERIOD = {bp}
      AND r.READ_STATUS = '{BILLED_READ_STATUS}'
      AND r.READING_TYPE IN ({rtypes})
      AND r.ID_SECTOR_SUPPLY IN (SELECT ID_SECTOR_SUPPLY FROM svc)
)
SELECT a.ACCOUNT,
       CASE WHEN MAX(CASE WHEN s.ID_OFFERED_SERVICE = {OFFERED_ELECTRICITY} THEN 1 ELSE 0 END) = 1 THEN 'YES' ELSE 'NO' END AS WITH_ELE,
       CASE WHEN MAX(CASE WHEN s.ID_OFFERED_SERVICE = {OFFERED_ELECTRICITY} AND rd.ID_SECTOR_SUPPLY IS NOT NULL THEN 1 ELSE 0 END) = 1 THEN 'YES' ELSE 'NO' END AS WITH_READING_ELE,
       CASE WHEN MAX(CASE WHEN s.ID_OFFERED_SERVICE = {OFFERED_WATER} THEN 1 ELSE 0 END) = 1 THEN 'YES' ELSE 'NO' END AS WITH_WATER,
       CASE WHEN MAX(CASE WHEN s.ID_OFFERED_SERVICE = {OFFERED_WATER} AND rd.ID_SECTOR_SUPPLY IS NOT NULL THEN 1 ELSE 0 END) = 1 THEN 'YES' ELSE 'NO' END AS WITH_READING_WATER,
       CASE WHEN MAX(CASE WHEN s.ID_OFFERED_SERVICE = {OFFERED_ELECTRICITY} AND rb.ID_SECTOR_SUPPLY IS NOT NULL THEN 1 ELSE 0 END) = 1 THEN 'YES' ELSE 'NO' END AS ELE_BILLED,
       CASE WHEN MAX(CASE WHEN s.ID_OFFERED_SERVICE = {OFFERED_WATER} AND rb.ID_SECTOR_SUPPLY IS NOT NULL THEN 1 ELSE 0 END) = 1 THEN 'YES' ELSE 'NO' END AS WATER_BILLED,
       a.ID_PAYMENT_FORM
FROM acc a
LEFT JOIN svc s ON s.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM
LEFT JOIN rd ON rd.ID_SECTOR_SUPPLY = s.ID_SECTOR_SUPPLY
LEFT JOIN rb ON rb.ID_SECTOR_SUPPLY = s.ID_SECTOR_SUPPLY
GROUP BY a.ACCOUNT, a.ID_PAYMENT_FORM
ORDER BY a.ACCOUNT
"""
