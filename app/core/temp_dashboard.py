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
