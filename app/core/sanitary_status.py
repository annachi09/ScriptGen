"""
Sanitary Status dashboard (RJ, 2026-11-24).

RJ: "create a new menu for new dashboard, sanitary status, first case is to find all sanitary where the premise
is disconnected but the tariff is sanitary billing not subsidized or zero tarif".

Case 1 - "Disconnected premise, still charged" - rules, confirmed live 2026-11-24:
  - Premise disconnected = the premise's LATEST row in EWA_GCGT_NS_SANITARY_PREMISE (by FROM_DATE, then
    ID_SAN_PREMISE) has ACTION_TYPE = 'Disconnected'. NOT "TO_DATE IS NULL": live, every Disconnected row is a
    one-day event (FROM_DATE = TO_DATE or TO_DATE a few days later) - only Connected rows stay open. Live: 882
    premises whose latest action is Disconnected (vs 169,762 Connected, 108,673 Not Connected).
  - Premise -> sanitary service: GCCOM_SUPPLY.ID_PREMISE -> GCCOM_SECTOR_SUPPLY.ID_SUPPLY ->
    GCCOM_CONTRACTED_SERVICE (ID_OFFERED_SERVICE = 190 Sanitary), contract not Pending / Terminated / Cancelled
    (ESTSC00001 / 4 / 5 - same "active" rule as Temp Dashboard).
  - Tariff = GCCOM_BILLING_SERVICE.ID_FARE not 10000000021 (W-SAN02 Sanitary Zero tariff) and not 10000000022
    (W-SAN03 Sanitary Charges Subsidy) - i.e. 10000000020 (W-SAN01 Sanitary Charges), the only other sanitary
    fare live.
  Live 2026-11-24: 36 contracted services on 7 premises (1.8 s). Same premises: 208 active services correctly on
  the zero tariff, 9 on the subsidy (left out - RJ: "not subsidized or zero tariff").
  Checked, not assumed: premise 122657 has only a Disconnected event (2026-01-01); its billing service
  20713676 was on W-SAN02 until 2025-12-31 (GCCOM_BILLING_SERVICE_HIST) and then on W-SAN01, charged every month
  since (bill 1078234255, 2026-11-02, 9.131).
Extra columns (same rows): bills issued on the service since the disconnection date (not Cancelled ESTFAC0007 /
Rebilled ESTFAC0042 / credit note TIPFAC0011), their total concept amount, the last one.

Pure logic: builds SELECT text only. Read only (Monitor) - no script.
"""
from __future__ import annotations

OFFERED_SANITARY = 190
DISCONNECTED = "Disconnected"
ZERO_FARE = 10000000021        # W-SAN02 Sanitary Zero tariff
SUBSIDY_FARE = 10000000022     # W-SAN03 Sanitary Charges Subsidy
EXCLUDED_CS_STATUSES = ("ESTSC00001", "ESTSC00004", "ESTSC00005")   # Pending, Terminated, Cancelled
BILL_EXCLUDED_STATUSES = ("ESTFAC0007", "ESTFAC0042")              # Cancelled, Rebilled
BILLING_TYPE_CREDIT_NOTE = "TIPFAC0011"

CS_STATUS_NAMES = {"ESTSC00002": "Active", "ESTSC00003": "Pending termination", "ESTSC00007": "Suspended"}


def build_disc_charged_query() -> str:
    cs_excl = ", ".join(f"'{s}'" for s in EXCLUDED_CS_STATUSES)
    b_excl = ", ".join(f"'{s}'" for s in BILL_EXCLUDED_STATUSES)
    return f"""
WITH lp AS (
  SELECT ID_PREMISE, ACTION_TYPE, FROM_DATE,
         ROW_NUMBER() OVER (PARTITION BY ID_PREMISE ORDER BY FROM_DATE DESC, ID_SAN_PREMISE DESC) AS rn
  FROM EWA_GCGT_NS_SANITARY_PREMISE WITH (NOLOCK)),
disc AS (SELECT ID_PREMISE, FROM_DATE AS DISCONNECTED_DATE FROM lp WHERE rn = 1 AND ACTION_TYPE = '{DISCONNECTED}')
SELECT cs.ID_PAYMENT_FORM, pf.REFERENCE, ss.NISS, d.ID_PREMISE, d.DISCONNECTED_DATE,
       cs.ID_CONTRACTED_SERVICE, cs.STATUS AS CS_STATUS,
       bs.ID_BILLING_SERVICE, bs.ID_FARE, fa.RATE_CODE, fa.NAME_TYPE AS FARE_NAME, bs.UPDATE_DATE AS BS_UPDATE_DATE,
       ISNULL(lb.BILLS_SINCE, 0) AS BILLS_SINCE, lb.AMOUNT_SINCE, lb.LAST_BILL_DATE, lb.LAST_ID_BILL
FROM disc d
JOIN GCCOM_SUPPLY sp WITH (NOLOCK) ON sp.ID_PREMISE = d.ID_PREMISE
JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SUPPLY = sp.ID_SUPPLY
JOIN GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
     ON cs.ID_SECTOR_SUPPLY = ss.ID_SECTOR_SUPPLY AND cs.ID_OFFERED_SERVICE = {OFFERED_SANITARY}
    AND cs.STATUS NOT IN ({cs_excl})
JOIN GCCOM_BILLING_SERVICE bs WITH (NOLOCK)
     ON bs.ID_CONTRACTED_SERVICE = cs.ID_CONTRACTED_SERVICE AND bs.ID_FARE NOT IN ({ZERO_FARE}, {SUBSIDY_FARE})
JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_FARE fa ON fa.ID_FARE = bs.ID_FARE
OUTER APPLY (
  SELECT COUNT(*) AS BILLS_SINCE, SUM(x.AMT) AS AMOUNT_SINCE, MAX(x.BILLING_DATE) AS LAST_BILL_DATE, MAX(x.ID_BILL) AS LAST_ID_BILL
  FROM (SELECT b.ID_BILL, b.BILLING_DATE,
               (SELECT SUM(c.CONCEPT_AMOUNT) FROM GCCOM_BILLING_CONCEPT c WITH (NOLOCK) WHERE c.ID_BILL = b.ID_BILL) AS AMT
        FROM GCCOM_BILL b WITH (NOLOCK)
        WHERE b.ID_CONTRACTED_SERVICE = cs.ID_CONTRACTED_SERVICE AND b.BILLING_DATE >= d.DISCONNECTED_DATE
          AND b.BILLING_STATUS NOT IN ({b_excl}) AND ISNULL(b.BILLING_TYPE, '') <> '{BILLING_TYPE_CREDIT_NOTE}') x
) lb
ORDER BY d.DISCONNECTED_DATE DESC, cs.ID_PAYMENT_FORM
"""
