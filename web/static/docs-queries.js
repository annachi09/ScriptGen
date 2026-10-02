/* =====================================================================
   ScriptGen - Query library (hand-written copies, RJ 2026-10-02).
   Each entry: id, menu, title, purpose, source (builder in app/core),
   params [[name, meaning, default]], sql, example {input, columns, rows,
   note}. Example rows are real 2026-10-02 results, accounts/NISS masked.
   Keep in sync with app/core when a builder changes.
   ===================================================================== */
window.SG_DOCS = window.SG_DOCS || {};

SG_DOCS.queries = [
/* ---------------------------------------------------------------- */
{
  id: "wb-case1", menu: "Wrong Bill", title: "Case 1 - Unusual high Sanitary",
  purpose: "Sanitary bills (SANITARY concept) above 14,158 that are also higher than the same account's Water bill for the same billing period and billing date. BASE = amount / 0.155 (the implied consumption).",
  source: "app/core/unusual_sanitary.py · build_query()",
  params: [
    ["MIN_BILLING_PERIOD", "Only periods after this one", "10000000228"],
    ["AMOUNT_THRESHOLD", "Sanitary bill total must exceed", "14158"],
    ["Excluded statuses", "Rebilled, Cancelled", "ESTFAC0042, ESTFAC0007"],
  ],
  sql: `SELECT pf.REFERENCE                     AS REFERENCE,
       nis.NISS                         AS NISS,
       b.ID_BILLING_PERIOD, b.ID_BILL, b.BILL_NUMBER, b.BILLING_DATE, b.CREATE_DATE, b.BILLING_STATUS,
       bc.COD_CONCEPT, bc.PRINT_DESCRIPTION,
       b.TOTAL_AMOUNT,
       b.TOTAL_AMOUNT / 0.155           AS BASE,
       wb.WATER_ID_BILL, wb.WATER_BILL_COUNT, wb.WATER_TOTAL_AMOUNT,
       b.TOTAL_AMOUNT - wb.WATER_TOTAL_AMOUNT AS SANITARY_MINUS_WATER
FROM GCCOM_BILLING_CONCEPT bc WITH (NOLOCK)
JOIN GCCOM_BILL b WITH (NOLOCK)
     ON b.ID_BILL = bc.ID_BILL AND b.ID_BILLING_PERIOD > 10000000228
JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
CROSS APPLY (                                   -- the account's Water bill(s), same period + billing date
    SELECT COUNT(*) AS WATER_BILL_COUNT, SUM(w.TOTAL_AMOUNT) AS WATER_TOTAL_AMOUNT, MIN(w.ID_BILL) AS WATER_ID_BILL
    FROM GCCOM_BILL w WITH (NOLOCK)
    JOIN GCCOM_CONTRACTED_SERVICE wcs WITH (NOLOCK)
         ON wcs.ID_CONTRACTED_SERVICE = w.ID_CONTRACTED_SERVICE AND wcs.ID_OFFERED_SERVICE = 19
    WHERE w.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
      AND w.ID_BILLING_PERIOD = b.ID_BILLING_PERIOD
      AND w.BILLING_DATE = b.BILLING_DATE
      AND ISNULL(w.BILLING_TYPE, '') <> 'TIPFAC0011'
      AND w.BILLING_STATUS NOT IN ('ESTFAC0042', 'ESTFAC0007')
) wb
OUTER APPLY (                                   -- NISS of the account's Water supply
    SELECT TOP 1 ss.NISS
    FROM GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
    JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
    WHERE cs.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM AND cs.ID_OFFERED_SERVICE = 19
    ORDER BY cs.ID_CONTRACTED_SERVICE DESC
) nis
WHERE bc.COD_CONCEPT = 'SANITARY'
  AND b.BILLING_STATUS NOT IN ('ESTFAC0042', 'ESTFAC0007')
  AND b.TOTAL_AMOUNT > 14158
  AND wb.WATER_BILL_COUNT > 0
  AND b.TOTAL_AMOUNT > wb.WATER_TOTAL_AMOUNT
ORDER BY b.TOTAL_AMOUNT DESC;`,
  example: {
    input: "No input (system-wide).",
    columns: ["REFERENCE", "ID_BILL", "TOTAL_AMOUNT", "BASE (= amount / 0.155)"],
    rows: [["1040•••947", "1075383385", "16,594.46", "107,061.03"]],
    note: "Row = the top case found 2026-09-30, before the Water comparison was added. Live 2026-10-02 the full query returns 0 rows (no sanitary bill is both > 14,158 and above its Water bill).",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "wb-case2", menu: "Wrong Bill", title: "Case 2 - % distribution primary with metered secondary",
  purpose: "Primary measuring points on calculation module 1150 (percentage distribution) that have a non-inactive secondary with a current device. Main coupled (TIPEQM0005) secondaries are expected to be metered and do not flag the primary. Returns every non-inactive secondary of a flagged primary (detail rows).",
  source: "app/core/wrong_bill_perc_dist.py · build_query()",
  params: [["Calc module", "Percentage distribution", "1150"], ["Inactive MP status", "Excluded", "3000STAMPO"], ["Main coupled type", "Never flags", "TIPEQM0005"]],
  sql: `WITH flagged AS (
    SELECT DISTINCT p.ID_MEASURING_POINT
    FROM OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT p WITH (NOLOCK)
    JOIN OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT c WITH (NOLOCK)
         ON c.ID_MAIN_MP = p.ID_MEASURING_POINT AND c.ID_MEASURING_POINT <> p.ID_MEASURING_POINT
    WHERE p.ID_CALCULATION_MODULE = 1150
      AND ISNULL(p.STATUS, '') <> '3000STAMPO'
      AND ISNULL(c.STATUS, '') <> '3000STAMPO'
      AND ISNULL(c.MP_TYPE, '') <> 'TIPEQM0005'
      AND EXISTS (SELECT 1 FROM OUC_COMMON_ADMIN.GCGT_RE_MEASURING_POINT_DEVICE d WITH (NOLOCK)
                  WHERE d.ID_MEASURING_POINT = c.ID_MEASURING_POINT AND d.REMOVAL_DATE IS NULL)
)
SELECT p.ID_MEASURING_POINT AS P_ID_MP, pss.NISS AS P_NISS, pacc.REFERENCE AS P_ACCOUNT,
       p.MP_TYPE AS P_MP_TYPE, p.STATUS AS P_STATUS, p.PERC_DIST AS P_PERC_DIST, pdev.ID_DEVICE AS P_ID_DEVICE,
       c.ID_MEASURING_POINT AS S_ID_MP, css.NISS AS S_NISS, c.MP_TYPE AS S_MP_TYPE, c.STATUS AS S_STATUS,
       c.PERC_DIST AS S_PERC_DIST, cdev.ID_DEVICE AS S_ID_DEVICE, dv.SERIAL_NUM AS S_SERIAL_NUM,
       cdev.INSTALLATION_DATE AS S_INSTALLATION_DATE,
       CASE WHEN cdev.ID_DEVICE IS NULL OR ISNULL(c.MP_TYPE, '') = 'TIPEQM0005' THEN 0 ELSE 1 END AS S_HAS_DEVICE
       -- (+ English type/status/module descriptions via GCTS_DICTIONARY in the app)
FROM flagged f
JOIN OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT p WITH (NOLOCK) ON p.ID_MEASURING_POINT = f.ID_MEASURING_POINT
JOIN OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT c WITH (NOLOCK)
     ON c.ID_MAIN_MP = p.ID_MEASURING_POINT AND c.ID_MEASURING_POINT <> p.ID_MEASURING_POINT
    AND ISNULL(c.STATUS, '') <> '3000STAMPO'
LEFT JOIN GCCOM_SECTOR_SUPPLY pss WITH (NOLOCK) ON pss.ID_SECTOR_SUPPLY = p.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_SECTOR_SUPPLY css WITH (NOLOCK) ON css.ID_SECTOR_SUPPLY = c.ID_SECTOR_SUPPLY
OUTER APPLY (SELECT TOP 1 pf.REFERENCE FROM GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
             JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
             WHERE cs.ID_SECTOR_SUPPLY = p.ID_SECTOR_SUPPLY ORDER BY cs.ID_CONTRACTED_SERVICE DESC) pacc
OUTER APPLY (SELECT TOP 1 d.ID_DEVICE FROM OUC_COMMON_ADMIN.GCGT_RE_MEASURING_POINT_DEVICE d WITH (NOLOCK)
             WHERE d.ID_MEASURING_POINT = p.ID_MEASURING_POINT AND d.REMOVAL_DATE IS NULL
             ORDER BY d.INSTALLATION_DATE DESC) pdev
OUTER APPLY (SELECT TOP 1 d.ID_DEVICE, d.INSTALLATION_DATE FROM OUC_COMMON_ADMIN.GCGT_RE_MEASURING_POINT_DEVICE d WITH (NOLOCK)
             WHERE d.ID_MEASURING_POINT = c.ID_MEASURING_POINT AND d.REMOVAL_DATE IS NULL
             ORDER BY d.INSTALLATION_DATE DESC) cdev
LEFT JOIN OUCW_ADMIN.GCGT_ME_DEVICE dv WITH (NOLOCK) ON dv.ID_DEVICE = cdev.ID_DEVICE
ORDER BY p.ID_MEASURING_POINT, S_HAS_DEVICE DESC, c.ID_MEASURING_POINT;`,
  example: {
    input: "No input (system-wide).",
    columns: ["P_ID_MP", "P_NISS", "P_ACCOUNT", "Metered secondaries", "Active secondaries", "Σ %"],
    rows: [["1935", "1017••••-301", "1103•••214", "6", "7", "100"]],
    note: "Live: 2 primaries. The page groups the detail rows into one row per primary.",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "wb-case3", menu: "Wrong Bill", title: "Case 3 - Sanitary 0 with water consumption",
  purpose: "Sanitary bills on a charging tariff (not 10000000021 Zero / 10000000022 Subsidy, checked on the billing service AND on the billed SANITARY concept) whose SANITARY amount is 0 or missing, while the same account's Water bill (same period + billing date) has CONCSMO003 water consumption > 0.",
  source: "app/core/wrong_bill_sanitary_zero.py · build_query()",
  params: [
    ["Billing periods", "One or more (default: current)", "10000000238"],
    ["Creation dates", "Optional CREATE_DATE range, max 92 days (an ID_BILL window is resolved first)", "-"],
    ["Excluded fares", "Zero tariff, Subsidy", "10000000021, 10000000022"],
  ],
  sql: `WITH san AS (                                  -- sanitary bills on a charging tariff
  SELECT b1.ID_BILL, b1.BILL_NUMBER, b1.ID_PAYMENT_FORM, b1.ID_BILLING_PERIOD, b1.BILLING_DATE, b1.CREATE_DATE,
         b1.BILLING_STATUS, b1.BILLING_TYPE, b1.ID_BILLING_SERVICE, bs.ID_FARE AS BS_FARE, b1.TOTAL_AMOUNT, cs1.ID_SECTOR_SUPPLY
  FROM OUC_COMMON_ADMIN.GCCOM_BILL b1 WITH (NOLOCK)
  JOIN OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs1 WITH (NOLOCK)
       ON cs1.ID_CONTRACTED_SERVICE = b1.ID_CONTRACTED_SERVICE AND cs1.ID_OFFERED_SERVICE = 190
  JOIN OUC_COMMON_ADMIN.GCCOM_BILLING_SERVICE bs WITH (NOLOCK)
       ON bs.ID_BILLING_SERVICE = b1.ID_BILLING_SERVICE AND bs.ID_FARE NOT IN (10000000021, 10000000022)
  WHERE b1.ID_BILLING_PERIOD IN (10000000238)
    AND ISNULL(b1.BILLING_TYPE, '') <> 'TIPFAC0011'
    AND b1.BILLING_STATUS NOT IN ('ESTFAC0007', 'ESTFAC0042')
),
zero AS (                                       -- SANITARY = 0 / missing, and not billed on 21/22 either
  SELECT s.ID_BILL, ISNULL(SUM(bc.CONCEPT_AMOUNT), 0) AS SANITARY_AMOUNT,
         COUNT(bc.ID_BILLING_CONCEPT) AS SANITARY_CONCEPT_COUNT, MAX(bc.ID_FARE) AS CONCEPT_FARE
  FROM san s
  LEFT JOIN GCCOM_BILLING_CONCEPT bc WITH (NOLOCK) ON bc.ID_BILL = s.ID_BILL AND bc.COD_CONCEPT = 'SANITARY'
  GROUP BY s.ID_BILL
  HAVING ISNULL(SUM(bc.CONCEPT_AMOUNT), 0) = 0
     AND SUM(CASE WHEN bc.ID_FARE IN (10000000021, 10000000022) THEN 1 ELSE 0 END) = 0
)
SELECT pf.REFERENCE, ss.NISS, s.ID_BILLING_PERIOD, s.BILLING_DATE, s.CREATE_DATE,
       s.ID_BILL AS SANITARY_ID_BILL, s.BILL_NUMBER AS SANITARY_BILL_NUMBER, s.BILLING_STATUS, s.BILLING_TYPE,
       COALESCE(z.CONCEPT_FARE, s.BS_FARE) AS ID_FARE, z.SANITARY_AMOUNT, z.SANITARY_CONCEPT_COUNT,
       wc.WATER_ID_BILL, wc.WATER_BILL_COUNT, wc.WATER_CONSUMPTION
FROM san s
JOIN zero z ON z.ID_BILL = s.ID_BILL
CROSS APPLY (                                   -- the Water bill's consumption (CONCSMO003)
  SELECT SUM(bc.CONCEPT_AMOUNT) AS WATER_CONSUMPTION, MIN(w.ID_BILL) AS WATER_ID_BILL, COUNT(DISTINCT w.ID_BILL) AS WATER_BILL_COUNT
  FROM OUC_COMMON_ADMIN.GCCOM_BILL w WITH (NOLOCK)
  JOIN OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE wcs WITH (NOLOCK)
       ON wcs.ID_CONTRACTED_SERVICE = w.ID_CONTRACTED_SERVICE AND wcs.ID_OFFERED_SERVICE = 19
  JOIN GCCOM_BILLING_CONCEPT bc WITH (NOLOCK) ON bc.ID_BILL = w.ID_BILL AND bc.COD_CONCEPT = 'CONCSMO003'
  WHERE w.ID_PAYMENT_FORM = s.ID_PAYMENT_FORM
    AND w.ID_BILLING_PERIOD = s.ID_BILLING_PERIOD
    AND w.BILLING_DATE = s.BILLING_DATE
    AND ISNULL(w.BILLING_TYPE, '') <> 'TIPFAC0011'
    AND w.BILLING_STATUS NOT IN ('ESTFAC0007', 'ESTFAC0042')
) wc
JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = s.ID_PAYMENT_FORM
LEFT JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = s.ID_SECTOR_SUPPLY
WHERE wc.WATER_CONSUMPTION > 0
ORDER BY wc.WATER_CONSUMPTION DESC;`,
  example: {
    input: "Billing period 10000000238 (10-October 2026).",
    columns: ["REFERENCE", "NISS", "SANITARY_ID_BILL", "Status", "ID_FARE", "SANITARY_AMOUNT", "WATER_ID_BILL", "WATER_CONSUMPTION"],
    rows: [
      ["1075•••747", "1060••••-701", "1075785073", "Invoiced", "10000000020", "0", "1075785072", "78.663"],
      ["1069•••870", "1010••••-701", "1075494845", "Invoiced", "10000000020", "0", "1075494803", "56.854"],
    ],
    note: "Live: 50 bills on 47 accounts for October 2026 (~8 s).",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "wb-case4", menu: "Wrong Bill", title: "Case 4 - First bill regularized",
  purpose: "The first bill of a contracted service (LAST_BILLING_DATE = contracted service FROM_DATE) on Electricity/Water/Sanitary that carries a regularization concept (REGAGUA, REGCC210, REGCC220, REGSANT) ≠ 0, plus the bills it regularized (GCCOM_BILL.ID_REG_BILL = this bill). Credit notes and Cancelled/Rebilled excluded on both sides.",
  source: "app/core/wrong_bill_first_regularized.py · build_bounds_query / build_first_bills_query / build_detail_query",
  params: [
    ["Billing periods", "One or more (default: current); each scanned in parallel", "10000000238"],
    ["Creation dates", "Alternative scope, e.g. 📅 Last 7 days", "-"],
    ["Offered services", "Electricity, Water, Sanitary", "1, 19, 190"],
  ],
  sql: `-- Step 1: ID_BILL window holding ~98.5% of each period's bills (index only)
WITH x AS (
  SELECT ID_BILLING_PERIOD AS P, ID_BILL,
         ROW_NUMBER() OVER (PARTITION BY ID_BILLING_PERIOD ORDER BY ID_BILL) AS RN,
         COUNT(*)     OVER (PARTITION BY ID_BILLING_PERIOD) AS N
  FROM OUC_COMMON_ADMIN.GCCOM_BILL WITH (NOLOCK)
  WHERE ID_BILLING_PERIOD IN (10000000238)
)
SELECT P, MAX(N) AS N,
       MIN(CASE WHEN RN >= N * 0.005 THEN ID_BILL END) AS LO,
       MAX(CASE WHEN RN <= N * 0.99  THEN ID_BILL END) AS HI
FROM x GROUP BY P;

-- Step 2 (per period, LO/HI from step 1): flagged first bills
WITH pb AS (
  SELECT ID_BILL FROM OUC_COMMON_ADMIN.GCCOM_BILL WITH (NOLOCK)
  WHERE ID_BILL BETWEEN @LO AND @HI AND ID_BILLING_PERIOD = 10000000238
  UNION ALL
  SELECT ID_BILL FROM OUC_COMMON_ADMIN.GCCOM_BILL WITH (NOLOCK)
  WHERE ID_BILLING_PERIOD = 10000000238 AND (ID_BILL < @LO OR ID_BILL > @HI)
)
SELECT b.ID_BILL
FROM pb
JOIN OUC_COMMON_ADMIN.GCCOM_BILL b WITH (NOLOCK) ON b.ID_BILL = pb.ID_BILL
JOIN OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
     ON cs.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE AND cs.ID_OFFERED_SERVICE IN (1, 19, 190)
WHERE b.LAST_BILLING_DATE >= CAST(cs.FROM_DATE AS DATE)
  AND b.LAST_BILLING_DATE <  DATEADD(DAY, 1, CAST(cs.FROM_DATE AS DATE))
  AND ISNULL(b.BILLING_TYPE, '') <> 'TIPFAC0011'
  AND b.BILLING_STATUS NOT IN ('ESTFAC0007', 'ESTFAC0042')
  AND EXISTS (SELECT 1 FROM GCCOM_BILLING_CONCEPT bc WITH (NOLOCK)
              WHERE bc.ID_BILL = b.ID_BILL
                AND bc.COD_CONCEPT IN ('REGAGUA', 'REGCC210', 'REGCC220', 'REGSANT')
                AND bc.CONCEPT_AMOUNT <> 0);

-- Step 3: detail for the flagged ids + the bills they regularized
SELECT pf.REFERENCE, ss.NISS, b.ID_BILLING_PERIOD, cs.ID_OFFERED_SERVICE, b.ID_BILL, b.BILLING_STATUS,
       b.LAST_BILLING_DATE, cs.FROM_DATE AS CS_FROM_DATE, rc.REG_CONCEPTS, rc.REG_AMOUNT,
       r.ID_BILL AS R_ID_BILL, r.ID_BILLING_PERIOD AS R_ID_BILLING_PERIOD, r.BILLING_STATUS AS R_BILLING_STATUS,
       CASE WHEN r.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE THEN 1 ELSE 0 END AS R_SAME_CS
FROM OUC_COMMON_ADMIN.GCCOM_BILL b WITH (NOLOCK)
JOIN OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK) ON cs.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE
CROSS APPLY (SELECT STRING_AGG(bc.COD_CONCEPT + ' ' + CAST(CAST(bc.CONCEPT_AMOUNT AS DECIMAL(18,3)) AS VARCHAR(30)), '; ') AS REG_CONCEPTS,
                    SUM(bc.CONCEPT_AMOUNT) AS REG_AMOUNT
             FROM GCCOM_BILLING_CONCEPT bc WITH (NOLOCK)
             WHERE bc.ID_BILL = b.ID_BILL AND bc.COD_CONCEPT IN ('REGAGUA', 'REGCC210', 'REGCC220', 'REGSANT')
               AND bc.CONCEPT_AMOUNT <> 0) rc
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_BILL r WITH (NOLOCK)
       ON r.ID_REG_BILL = b.ID_BILL
      AND ISNULL(r.BILLING_TYPE, '') <> 'TIPFAC0011'
      AND r.BILLING_STATUS NOT IN ('ESTFAC0007', 'ESTFAC0042')
JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
LEFT JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
WHERE b.ID_BILL IN (/* ids from step 2 */);`,
  example: {
    input: "Billing period 10000000238 (10-October 2026).",
    columns: ["REFERENCE", "Service", "ID_BILL", "Status", "LAST_BILLING_DATE = FROM_DATE", "REG_CONCEPTS", "R_ID_BILL", "R period", "Same CS?"],
    rows: [
      ["1103•••219", "Electricity", "1075461855", "Invoicing", "2026-08-25", "REGCC210 -8.297; REGCC210 18.425", "1073724066", "8-August 2026", "Other CS"],
      ["1103•••219", "Water", "1075461894", "Invoicing", "2026-08-25", "REGAGUA -2.016; REGAGUA 6.048", "1073724067", "8-August 2026", "Other CS"],
    ],
    note: "Live: October 2026 = 14 bills (~6 s), August 2026 = 204. Every regularized bill sits on another (the previous) contracted service.",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "tnb-cycle-disc", menu: "TNB CYCLE/DISC Analysis", title: "Cycle + Disconnection on the same date, one TNB",
  purpose: "Pairs of a Cycle and a Disconnection reading on the same supply, reading date and usage type where at least one is Terminated Not Billed and at least one has ready usage ≠ 0. IN_CONTRACT = a non-cancelled contract covers the reading date.",
  source: "app/core/tnb_cycle_disc.py · build_tnb_cycle_disc_query()",
  params: [["Reading types", "Cycle, Disconnection", "TIPTL00003, TIPTL00010"], ["TNB status", "Terminated Not Billed", "8000STSRED"]],
  sql: `WITH tnb AS (                                   -- keys that have a TNB cycle or disconnection
    SELECT id_sector_supply, reading_date, usage_type
    FROM gcgt_re_reading WITH (NOLOCK)
    WHERE read_status = '8000STSRED' AND reading_type IN ('TIPTL00003', 'TIPTL00010')
    GROUP BY id_sector_supply, reading_date, usage_type
)
SELECT c.id_sector_supply, ss.niss, c.usage_type AS usage_code,
       COALESCE(c.id_billing_period, d.id_billing_period) AS id_billing_period,
       CASE WHEN EXISTS (SELECT 1 FROM OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
                         WHERE cs.ID_SECTOR_SUPPLY = c.id_sector_supply AND cs.STATUS <> 'ESTSC00005'
                           AND cs.FROM_DATE <= c.reading_date
                           AND (cs.END_DATE IS NULL OR cs.END_DATE >= c.reading_date))
            THEN 1 ELSE 0 END AS in_contract,
       c.id_reading AS c_id_reading, c.reading_prev_date AS c_reading_prev_date, c.reading_date AS c_reading_date,
       c.prev_value AS c_prev_value, c.VALUE AS c_value, c.ready_usage AS c_ready_usage, c.read_status AS c_read_status,
       d.id_reading AS d_id_reading, d.reading_prev_date AS d_reading_prev_date, d.reading_date AS d_reading_date,
       d.prev_value AS d_prev_value, d.VALUE AS d_value, d.ready_usage AS d_ready_usage, d.read_status AS d_read_status
       -- (+ English reading type / status / billing period per side in the app)
FROM tnb
JOIN gcgt_re_reading c WITH (NOLOCK) ON c.id_sector_supply = tnb.id_sector_supply AND c.reading_date = tnb.reading_date
     AND c.usage_type = tnb.usage_type AND c.reading_type = 'TIPTL00003'
JOIN gcgt_re_reading d WITH (NOLOCK) ON d.id_sector_supply = tnb.id_sector_supply AND d.reading_date = tnb.reading_date
     AND d.usage_type = tnb.usage_type AND d.reading_type = 'TIPTL00010'
LEFT JOIN gccom_sector_supply ss WITH (NOLOCK) ON ss.id_sector_supply = c.id_sector_supply
WHERE (c.read_status = '8000STSRED' OR d.read_status = '8000STSRED')
  AND (ISNULL(c.ready_usage, 0) <> 0 OR ISNULL(d.ready_usage, 0) <> 0)
ORDER BY COALESCE(c.id_billing_period, d.id_billing_period) DESC, ss.niss, c.reading_date DESC;`,
  example: {
    input: "No input (filters by period / NISS are applied in the page).",
    columns: ["NISS", "Usage", "In contract", "Cycle reading", "Cycle ready", "Cycle status", "Disc reading", "Disc ready", "Disc status"],
    rows: [
      ["1002••••-101", "Active Energy", "1", "1047645809", "12", "Terminated Not Billed", "1047760219", "962", "Billed"],
      ["1003••••-101", "Active Energy", "1", "1047551849", "999,997", "Terminated Not Billed", "1047625329", "7,956", "Billed"],
    ],
    note: "Live: 2,300 pairs (~6 s). The 2nd row is a meter rollover (value went 189,866 → 189,863).",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "disc-tnb", menu: "Disconnection TNB", title: "Disconnection readings TNB with ready usage",
  purpose: "Disconnection readings in Terminated Not Billed with ready usage ≠ 0, with the contracted service whose END_DATE is nearest to the reading date (open contracts last) and the gap in days.",
  source: "app/core/disconnection_tnb.py · build_disconnection_tnb_query()",
  params: [["Reading type", "Disconnection", "TIPTL00010"], ["Read status", "TNB", "8000STSRED"]],
  sql: `SELECT r.ID_SECTOR_SUPPLY, ss.NISS, r.ID_READING, r.ID_BILLING_PERIOD,
       r.READING_PREV_DATE, r.READING_DATE, r.PREV_VALUE, r.VALUE,
       r.READING_USAGE, r.CORRECTED_USAGE, r.READY_USAGE, r.READ_STATUS, r.IND_ESTIMATE,
       ncs.ID_CONTRACTED_SERVICE, ncs.STATUS AS CONTRACT_STATUS,
       ncs.FROM_DATE AS CONTRACT_FROM_DATE, ncs.END_DATE AS CONTRACT_END_DATE,
       DATEDIFF(day, ncs.END_DATE, r.READING_DATE) AS DAYS_FROM_END
FROM GCGT_RE_READING r
LEFT JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
OUTER APPLY (                                   -- nearest contract by end date
    SELECT TOP 1 cs.ID_CONTRACTED_SERVICE, cs.STATUS, cs.FROM_DATE, cs.END_DATE
    FROM OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs
    WHERE cs.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
    ORDER BY CASE WHEN cs.END_DATE IS NULL THEN 1 ELSE 0 END,
             ABS(DATEDIFF(day, cs.END_DATE, r.READING_DATE)),
             cs.ID_CONTRACTED_SERVICE DESC
) ncs
WHERE r.READING_TYPE = 'TIPTL00010'
  AND r.READ_STATUS = '8000STSRED'
  AND ISNULL(r.READY_USAGE, 0) <> 0
ORDER BY r.READING_DATE DESC, ss.NISS;`,
  example: {
    input: "No input (system-wide).",
    columns: ["NISS", "ID_READING", "READING_DATE", "PREV → VALUE", "READY_USAGE", "Contract", "END_DATE", "DAYS_FROM_END"],
    rows: [
      ["1007••••-101", "1048038451", "2026-09-30", "16,014 → 16,052", "38", "Terminated", "2026-09-27", "3"],
      ["1009••••-101", "1048038135", "2026-09-30", "24,289 → 24,352", "63", "Terminated", "2026-09-27", "3"],
    ],
    note: "Live: 5,045 readings (~4 s).",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "double-itb", menu: "DOUBLE ITB", title: "Billed + Anomalous item to bill, same dates",
  purpose: "An Anomalous item to bill (STTOBILL00) whose billing service already has a Billed item (STTOBILL07) with the same INI_DATE and END_DATE. NEEDS_REBILLING = the anomalous item carries ready usage.",
  source: "app/core/double_itb.py · build_double_itb_query()",
  params: [["Anomalous / Billed", "Item-to-bill statuses", "STTOBILL00 / STTOBILL07"]],
  sql: `SELECT ss.NISS, pf.REFERENCE AS ACCOUNT, os.NAME_TYPE AS OFFERED_SERVICE,
       a.ID_BILLING_SERVICE, a.ID_BILLING_PERIOD, a.INI_DATE, a.END_DATE,
       a.ID_ITEM_TO_BILL AS ANOM_ID_ITEM_TO_BILL, a.STATUS AS ANOM_STATUS, a.BILLING_DATE AS ANOM_BILLING_DATE,
       b.ID_ITEM_TO_BILL AS BILLED_ID_ITEM_TO_BILL, b.STATUS AS BILLED_STATUS, b.ID_BILL AS BILLED_ID_BILL,
       ru.READY_USAGE AS ANOM_READY_USAGE, ru.READING_COUNT AS ANOM_READING_COUNT,
       CASE WHEN ISNULL(ru.READY_USAGE, 0) <> 0 THEN 1 ELSE 0 END AS NEEDS_REBILLING
FROM GCCOM_ITEMS_TO_BILL a
JOIN GCCOM_ITEMS_TO_BILL b ON b.ID_BILLING_SERVICE = a.ID_BILLING_SERVICE AND b.STATUS = 'STTOBILL07'
     AND b.INI_DATE = a.INI_DATE AND b.END_DATE = a.END_DATE AND b.ID_ITEM_TO_BILL <> a.ID_ITEM_TO_BILL
JOIN GCCOM_BILLING_SERVICE bs ON bs.ID_BILLING_SERVICE = a.ID_BILLING_SERVICE
JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
LEFT JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE os ON os.ID_OFFERED_SERVICE = cs.ID_OFFERED_SERVICE
OUTER APPLY (SELECT SUM(rit.READY_USAGE) AS READY_USAGE, COUNT(*) AS READING_COUNT
             FROM OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL rit
             WHERE rit.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL) ru
WHERE a.STATUS = 'STTOBILL00'
ORDER BY a.ID_BILLING_PERIOD DESC, ss.NISS;`,
  example: {
    input: "No input (system-wide).",
    columns: ["NISS", "Account", "Service", "INI → END", "Anomalous ITB", "Billed ITB", "Billed bill", "Ready usage", "Needs rebilling"],
    rows: [
      ["1002••••-301", "1103•••045", "Water", "2026-08-28 → 2026-09-20", "1040678433", "1040623000", "1075619661", "12.257", "Yes"],
      ["1002••••-701", "1103•••045", "Sanitary", "2026-08-28 → 2026-09-20", "1040678434", "1040623001", "1075619662", "12.257", "Yes"],
    ],
    note: "Live: 109 need rebilling (~1.4 s).",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "wrong-stuck", menu: "Wrong Stuck in Hierarchy ITB", title: "Secondary held in hierarchy, primary already billed",
  purpose: "Items to bill held in hierarchy (STTOBILL09) whose measuring point's main (primary) point has its item in the SAME period already billed (STTOBILL07); the account's Sanitary (190) item of the same period is shown alongside. Contracts Active / Pending termination / Suspended only.",
  source: "app/core/wrong_stuck_hierarchy.py · build_wrong_stuck_query()",
  params: [["Billing period", "Optional - all periods by default", "-"], ["Contract statuses", "Active, Pending termination, Suspended", "ESTSC00002, 03, 07"]],
  sql: `WITH primary_mp AS (                            -- primaries whose item is billed, per period
    SELECT DISTINCT mp.ID_MEASURING_POINT, itb.ID_BILLING_PERIOD
    FROM GCCOM_ITEMS_TO_BILL itb
    JOIN GCCOM_BILLING_SERVICE bs ON bs.ID_BILLING_SERVICE = itb.ID_BILLING_SERVICE
    JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
         AND cs.STATUS IN ('ESTSC00002', 'ESTSC00003', 'ESTSC00007')
    JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
    JOIN GCGT_RE_MEASUREMENT_POINT mp ON mp.ID_SECTOR_SUPPLY = ss.ID_SECTOR_SUPPLY
    WHERE itb.STATUS = 'STTOBILL07'
)
SELECT pf.REFERENCE, ss.NISS, itb.ID_ITEM_TO_BILL, mp.ID_MAIN_MP, itb.ID_BILLING_PERIOD, bp.DESCRIPTION,
       itb1.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL_190, itb1.STATUS AS STATUS_190
FROM GCCOM_ITEMS_TO_BILL itb
JOIN GCCOM_BILLING_SERVICE bs ON bs.ID_BILLING_SERVICE = itb.ID_BILLING_SERVICE
JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
     AND cs.STATUS IN ('ESTSC00002', 'ESTSC00003', 'ESTSC00007')
JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
JOIN GCGT_RE_MEASUREMENT_POINT mp ON mp.ID_SECTOR_SUPPLY = ss.ID_SECTOR_SUPPLY
JOIN primary_mp pmp ON pmp.ID_MEASURING_POINT = mp.ID_MAIN_MP AND pmp.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
JOIN GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
LEFT JOIN GCCOM_CONTRACTED_SERVICE cs1 ON cs1.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM AND cs1.ID_OFFERED_SERVICE = 190
LEFT JOIN GCCOM_BILLING_SERVICE bs1 ON bs1.ID_CONTRACTED_SERVICE = cs1.ID_CONTRACTED_SERVICE
LEFT JOIN GCCOM_ITEMS_TO_BILL itb1 ON itb1.ID_BILLING_SERVICE = bs1.ID_BILLING_SERVICE
     AND itb1.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
WHERE itb.STATUS = 'STTOBILL09'
ORDER BY itb.ID_BILLING_PERIOD DESC, mp.ID_MAIN_MP DESC;

-- Correction (generated, never executed by the app):
-- UPDATE GCCOM_ITEMS_TO_BILL SET STATUS = 'STTOBILL01', UPDATE_PROGRAM = 'WRONG_ITB_HIERARCHY_STATUS',
--        UPDATE_USER = 'RMA', UPDATE_DATE = GETDATE()
-- WHERE ID_ITEM_TO_BILL IN (...) AND STATUS = 'STTOBILL09';`,
  example: { input: "No input (all periods).", columns: ["REFERENCE", "NISS", "ID_ITEM_TO_BILL", "ID_MAIN_MP", "Period", "ITB 190", "Status 190"], rows: [], note: "Live 2026-10-02: 0 rows (nothing stuck). See the Sanitary-vs-Water query below for the companion tab." },
},
/* ---------------------------------------------------------------- */
{
  id: "wrong-stuck-san", menu: "Wrong Stuck in Hierarchy ITB", title: "Sanitary held while Water is done",
  purpose: "Sanitary (190) items held in hierarchy (STTOBILL09) while the account's Water (19) item of the same period and billing date is already Billed or Pending (STTOBILL07 / STTOBILL01).",
  source: "app/core/wrong_stuck_hierarchy.py · build_sanitary_stuck_water_done_query()",
  params: [],
  sql: `SELECT pf.REFERENCE, ss.NISS AS SANITARY_NISS, itb.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL_SANITARY, itb.STATUS AS STATUS_SANITARY,
       itb.ID_BILLING_PERIOD, bp.DESCRIPTION, itb.BILLING_DATE,
       ssw.NISS AS WATER_NISS, itbw.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL_WATER, itbw.STATUS AS STATUS_WATER
FROM GCCOM_ITEMS_TO_BILL itb
JOIN GCCOM_BILLING_SERVICE bs ON bs.ID_BILLING_SERVICE = itb.ID_BILLING_SERVICE
JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
     AND cs.ID_OFFERED_SERVICE = 190 AND cs.STATUS IN ('ESTSC00002', 'ESTSC00003', 'ESTSC00007')
JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
JOIN GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
JOIN GCCOM_CONTRACTED_SERVICE csw ON csw.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM AND csw.ID_OFFERED_SERVICE = 19
JOIN GCCOM_BILLING_SERVICE bsw ON bsw.ID_CONTRACTED_SERVICE = csw.ID_CONTRACTED_SERVICE
JOIN GCCOM_ITEMS_TO_BILL itbw ON itbw.ID_BILLING_SERVICE = bsw.ID_BILLING_SERVICE
     AND itbw.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
     AND CAST(itbw.BILLING_DATE AS date) = CAST(itb.BILLING_DATE AS date)
     AND itbw.STATUS IN ('STTOBILL07', 'STTOBILL01')
LEFT JOIN GCCOM_SECTOR_SUPPLY ssw ON ssw.ID_SECTOR_SUPPLY = csw.ID_SECTOR_SUPPLY
WHERE itb.STATUS = 'STTOBILL09'
ORDER BY itb.ID_BILLING_PERIOD DESC, pf.REFERENCE;`,
  example: {
    input: "No input.",
    columns: ["REFERENCE", "Sanitary NISS", "Sanitary ITB", "Status", "Period", "Billing date", "Water NISS", "Water ITB", "Water status"],
    rows: [
      ["1040•••067", "1036••••-701", "1040386126", "STTOBILL09", "10-October 2026", "2026-09-19", "1036••••-301", "1040386124", "STTOBILL07"],
      ["1042•••643", "1016••••-702", "1040586707", "STTOBILL09", "10-October 2026", "2026-09-23", "149•••6", "1040586612", "STTOBILL07"],
    ],
    note: "Live: 356 items (~1.8 s).",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "da-detect", menu: "DIFF DATES Anomaly", title: "Single NISS - anomalous readings + correct date",
  purpose: "(1) every reading of the NISS stuck in 6000STSRED above the billing-period threshold (excluding TIPTL00004), and (2) the 'correct date' = the most recent correctly billed (7000STSRED) reading, ordered by reading date first. Both run in parallel.",
  source: "app/core/date_anomaly.py · build_detect_query() + build_correct_date_query()",
  params: [["NISS", "Supply to analyse", "e.g. 20022221-101"], ["Threshold", "Only billing periods above", "set on the page"]],
  sql: `-- (1) anomalous readings
SELECT r.*, rt.DESCRIPTION AS READING_TYPE_DESC,
       CASE WHEN r.READING_TYPE IN ('TIPTL00003', 'TIPTL00005') THEN 1 ELSE 0 END AS IS_CYCLE_READING
FROM OUC_COMMON_ADMIN.GCGT_RE_READING r
JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING_TYPE rt ON rt.COD_DEVELOP = r.READING_TYPE
WHERE ss.NISS = '20022221-101'
  AND r.ID_BILLING_PERIOD > @THRESHOLD
  AND r.READING_TYPE <> 'TIPTL00004'
  AND r.READ_STATUS = '6000STSRED'
ORDER BY r.ID_BILLING_PERIOD DESC;

-- (2) correct date = most recent billed reading
SELECT TOP 1 r.ID_READING, r.ID_BILLING_PERIOD, r.READING_DATE
FROM OUC_COMMON_ADMIN.GCGT_RE_READING r
JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
WHERE ss.NISS = '20022221-101'
  AND r.ID_BILLING_PERIOD > @THRESHOLD
  AND r.READING_TYPE <> 'TIPTL00004'
  AND r.READ_STATUS = '7000STSRED'
ORDER BY r.READING_DATE DESC, r.ID_BILLING_PERIOD DESC, r.ID_READING DESC;`,
  example: { input: "NISS 2002••••-101.", columns: ["Query", "Returns"], rows: [["(1)", "every 6000STSRED reading of the NISS, newest period first"], ["(2)", "1 row: ID_READING / ID_BILLING_PERIOD / READING_DATE used as the correct date"]], note: "Reading date is the primary sort key: a lower billing period can hold a later reading (removal / reinstall)." },
},
/* ---------------------------------------------------------------- */
{
  id: "da-detect-all", menu: "DIFF DATES Anomaly", title: "Detect All - open Diff Date anomalies",
  purpose: "Every open (ESTAN00009 / ESTAN00001) anomaly of type 201/202 with item status, account, supply, contract status, ALL_CYCLE (all linked readings are cycle), the number of billing periods its readings span, and which non-cycle reading types it contains.",
  source: "app/core/date_anomaly.py · build_detect_all_anomalies_query()",
  params: [["Type ids", "Diff Date System", "202, 201"], ["Open statuses", "", "ESTAN00009, ESTAN00001"], ["Row cap", "TOP", "1000"]],
  sql: `SELECT TOP (1000)
       GA.ID_ITEM_TO_BILL, GA.ANOMALOUS_STATUS, GAS.NAME_TYPE AS ANOMALOUS_STATUS_DESC,
       GA.ID_PRINCIPAL_ANOMALY, GBAC.ANOMALY_COD AS ANOMALOUS_TYPE_CODE, GBAC.DESCRIPTION AS ANOMALOUS_TYPE_DESC,
       GITB.STATUS AS ITEM_STATUS, PF.REFERENCE AS ACCOUNT, SS.NISS AS SUPPLY,
       CS.ID_OFFERED_SERVICE AS OFFERED_SERVICE, CS.STATUS AS CONTRACT_STATUS, GA.DETECTION_DATE,
       CASE WHEN EXISTS (SELECT 1 FROM OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL GRI
                         JOIN OUC_COMMON_ADMIN.GCGT_RE_READING GR ON GR.ID_READING = GRI.ID_READING
                         WHERE GRI.ID_ITEM_TO_BILL = GA.ID_ITEM_TO_BILL
                           AND GR.READING_TYPE NOT IN ('TIPTL00003', 'TIPTL00005'))
            THEN 0 ELSE 1 END AS ALL_CYCLE,
       (SELECT COUNT(DISTINCT GR2.ID_BILLING_PERIOD) FROM OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL GRI2
        JOIN OUC_COMMON_ADMIN.GCGT_RE_READING GR2 ON GR2.ID_READING = GRI2.ID_READING
        WHERE GRI2.ID_ITEM_TO_BILL = GA.ID_ITEM_TO_BILL) AS BILLING_PERIOD_COUNT,
       (SELECT STUFF((SELECT DISTINCT ', ' + COALESCE(RT3.DESCRIPTION, GR3.READING_TYPE)
                      FROM OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL GRI3
                      JOIN OUC_COMMON_ADMIN.GCGT_RE_READING GR3 ON GR3.ID_READING = GRI3.ID_READING
                      LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING_TYPE RT3 ON RT3.COD_DEVELOP = GR3.READING_TYPE
                      WHERE GRI3.ID_ITEM_TO_BILL = GA.ID_ITEM_TO_BILL
                        AND GR3.READING_TYPE NOT IN ('TIPTL00003', 'TIPTL00005')
                      FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), 1, 2, '')) AS NON_CYCLE_READING_TYPES
FROM OUC_ADMIN.GCCOM_ANOMALOUS GA
LEFT JOIN OUC_ADMIN.GCCOM_ITEMS_TO_BILL GITB ON GITB.ID_ITEM_TO_BILL = GA.ID_ITEM_TO_BILL
LEFT JOIN GCCOM_ANOMALOUS_STATUS GAS ON GAS.COD_DEVELOP = GA.ANOMALOUS_STATUS
LEFT JOIN GCCOM_BILL_ANOMALY_COMPANY GBAC ON GBAC.ID_BILL_ANOM_COMP = GA.ID_PRINCIPAL_ANOMALY
LEFT JOIN GCCOM_BILLING_SERVICE BS ON BS.ID_BILLING_SERVICE = GA.ID_BILLING_SERVICE
LEFT JOIN GCCOM_CONTRACTED_SERVICE CS ON CS.ID_CONTRACTED_SERVICE = BS.ID_CONTRACTED_SERVICE
LEFT JOIN GCCOM_PAYMENT_FORM PF ON PF.ID_PAYMENT_FORM = CS.ID_PAYMENT_FORM
LEFT JOIN GCCOM_SECTOR_SUPPLY SS ON SS.ID_SECTOR_SUPPLY = CS.ID_SECTOR_SUPPLY
WHERE GA.ID_PRINCIPAL_ANOMALY IN (202, 201)
  AND GA.ANOMALOUS_STATUS IN ('ESTAN00009', 'ESTAN00001')
ORDER BY GA.DETECTION_DATE;`,
  example: { input: "No input (system-wide).", columns: ["ID_ITEM_TO_BILL", "Status", "Type", "Account", "Supply", "ALL_CYCLE", "Periods", "Non-cycle types"], rows: [], note: "Live 2026-10-02: 0 open 201/202 anomalies (~1.3 s)." },
},
/* ---------------------------------------------------------------- */
{
  id: "hier-pending", menu: "Hierarchy Analysis", title: "Pending primaries",
  purpose: "Every Main / Main coupled measuring point (Connected or Disconnected) with a Cycle/Distribution reading in Available, Anomalous or Sent to bill after period 10000000193 - the OLDEST such reading per point - with its secondary count, how many secondaries are still not sent, and whether the period is already billed.",
  source: "app/core/hierarchy_analysis.py · build_pending_primaries_query()",
  params: [["Pending read statuses", "Available, Anomalous, Sent to bill", "1000/5000/6000STSRED"], ["Reading types", "Cycle, Distribution", "TIPTL00003, TIPTL00017"], ["Period floor", "", "10000000193"], ["Row cap", "TOP", "1000"]],
  sql: `SELECT TOP (1000) A.*,
       CASE WHEN EXISTS (                       -- period already billed?
           SELECT 1 FROM OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE pcs
           JOIN OUC_COMMON_ADMIN.GCCOM_BILL pb ON pb.ID_CONTRACTED_SERVICE = pcs.ID_CONTRACTED_SERVICE
           WHERE pcs.ID_SECTOR_SUPPLY = A.ID_SECTOR_SUPPLY AND pcs.STATUS <> 'ESTSC00005'
             AND pcs.FROM_DATE <= A.READING_DATE AND (pcs.END_DATE IS NULL OR pcs.END_DATE >= A.READING_DATE)
             AND pb.BILL_TYPE = 'TFGEN00001'
             AND CAST(pb.BILLING_DATE AS date) = CAST(A.READING_DATE AS date)
             AND CAST(pb.LAST_BILLING_DATE AS date) IN (CAST(A.READING_PREV_DATE AS date),
                                                        DATEADD(day, 1, CAST(A.READING_PREV_DATE AS date)))
       ) THEN 1 ELSE 0 END AS PERIOD_BILLED
FROM (
    SELECT mp.ID_MAIN_MP, mp.ID_CALCULATION_MODULE, cm.NAME_TYPE AS CALC_MODULE_TYPE, mp.ID_MEASURING_POINT,
           (SELECT COUNT(*) FROM OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT c
            WHERE c.ID_MAIN_MP = mp.ID_MEASURING_POINT) AS SECONDARY_COUNT,
           (SELECT COUNT(*) FROM OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT c
            WHERE c.ID_MAIN_MP = mp.ID_MEASURING_POINT AND (
                EXISTS (SELECT 1 FROM OUC_COMMON_ADMIN.GCGT_RE_READING r2
                        WHERE r2.ID_MEASURING_POINT = c.ID_MEASURING_POINT
                          AND r2.READ_STATUS IN ('1000STSRED', '5000STSRED')
                          AND r2.READING_TYPE IN ('TIPTL00003', 'TIPTL00017') AND r2.ID_BILLING_PERIOD > 10000000193)
                OR NOT EXISTS (SELECT 1 FROM OUC_COMMON_ADMIN.GCGT_RE_READING r3
                        WHERE r3.ID_MEASURING_POINT = c.ID_MEASURING_POINT
                          AND r3.READING_TYPE IN ('TIPTL00003', 'TIPTL00017') AND r3.ID_BILLING_PERIOD > 10000000193)
            )) AS SECONDARIES_NOT_SENT_COUNT,
           mp.ID_SECTOR_SUPPLY, mp.ID_METER, mp.PERC_DIST, mp.MP_TYPE, mp.STATUS AS MP_STATUS, ss.NISS,
           r.ID_BILLING_PERIOD, r.ID_READING, r.READING_PREV_DATE, r.READING_DATE, r.READING_TYPE, r.READ_STATUS,
           r.PREV_VALUE, r.VALUE, r.READY_USAGE, r.READING_USAGE, r.CORRECTED_USAGE, r.USAGE_TYPE, r.IND_ESTIMATE,
           ROW_NUMBER() OVER (PARTITION BY mp.ID_MEASURING_POINT ORDER BY r.ID_BILLING_PERIOD ASC) AS RN
    FROM OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT mp
    JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = mp.ID_SECTOR_SUPPLY
    JOIN OUC_COMMON_ADMIN.GCGT_RE_READING r
         ON r.ID_MEASURING_POINT = mp.ID_MEASURING_POINT
        AND r.READ_STATUS IN ('1000STSRED', '5000STSRED', '6000STSRED')
        AND r.READING_TYPE IN ('TIPTL00003', 'TIPTL00017')
        AND r.ID_BILLING_PERIOD > 10000000193
    LEFT JOIN OUC_COMMON_ADMIN.GCCOM_CALCULATION_MODULE cm ON cm.ID_CALCULATION_MODULE = mp.ID_CALCULATION_MODULE
    WHERE mp.MP_TYPE IN ('TIPEQM0003', 'TIPEQM0005')
      AND mp.STATUS IN ('1000STAMPO', '2000STAMPO')
) A
WHERE RN = 1
ORDER BY ID_BILLING_PERIOD;`,
  example: {
    input: "No input (system-wide, capped at 1000).",
    columns: ["ID_MEASURING_POINT", "NISS", "Calc module", "Secondaries", "Not sent", "Period", "Reading type", "Status", "Ready usage", "Period billed"],
    rows: [
      ["222572", "1043••••-301", "Difference billed to the main supply", "24", "0", "2-February 2023", "Distribution", "6000STSRED", "1", "Yes"],
      ["100907", "1027••••-301", "Difference billed to the main supply", "18", "0", "3-March 2023", "Distribution", "6000STSRED", "124", "No"],
    ],
    note: "Live: 1000 rows returned (cap reached, ~3.8 s).",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "rv-readings", menu: "Reading Validation/Modif", title: "All readings of one supply",
  purpose: "Every reading of a sector supply (load-curve usage types excluded) with device/model, units, usages, multiplier, power factor, reader, origin and English descriptions - newest first. Optional narrowing by measuring point and device.",
  source: "app/core/reading_validation.py · build_readings_query()",
  params: [["ID_SECTOR_SUPPLY", "From the NISS / account search", "required"], ["ID_MEASURING_POINT", "Optional", "-"], ["ID_DEVICE", "Optional", "-"]],
  sql: `SELECT r.id_sector_supply, r.id_measuring_point, r.id_reading, r.id_last_reading, r.id_device,
       MOD.model_name, d.serial_num AS company_meter_num,
       r.reading_type AS reading_type_code, r.reading_prev_date, r.reading_date, r.reading_time_ts,
       r.usage_type AS usage_code, r.read_status, r.prev_value, r.VALUE AS reading,
       r.reading_usage AS metered_usage, r.corrected_usage, r.ready_usage AS bill_ready_usage,
       umult.MULTIPLIER AS usage_multiplier, r.ind_estimate, r.ind_negative_usage,
       ss.niss, ss.id_supply,
       CASE WHEN r.ID_REBILLING_ACTIVITY IS NOT NULL THEN 1 ELSE 0 END AS ind_modreb
       -- (+ English descriptions, units, power factor, reader, origin, digitizer in the app)
FROM gcgt_re_reading r
INNER JOIN gcgt_re_reading_type rtype ON rtype.cod_develop = r.reading_type
INNER JOIN gccom_consum_type consum   ON consum.cod_develop = r.usage_type
INNER JOIN gcgt_re_read_status st     ON st.cod_develop = r.read_status
LEFT JOIN gccom_sector_supply ss      ON ss.id_sector_supply = r.id_sector_supply
LEFT JOIN gcgt_me_device d            ON d.id_device = r.id_device
LEFT JOIN gcgt_me_device_model MOD    ON MOD.id_model = d.id_model
LEFT JOIN GCGT_ME_USAGE_TYPE_METER umult ON umult.ID_DEVICE = r.id_device AND umult.COD_USAGE_TYPE = r.usage_type
WHERE r.id_sector_supply = @ID_SECTOR_SUPPLY
  AND (consum.ind_load_curve IS NULL OR consum.ind_load_curve = 0)
ORDER BY r.reading_date DESC, r.reading_time_ts DESC, d.comp_serial_num DESC, r.usage_type;`,
  example: { input: "NISS → its ID_SECTOR_SUPPLY (account search lists every supply of the account).", columns: ["id_reading", "reading_type", "prev → reading", "metered", "corrected", "bill ready", "read_status"], rows: [], note: "Interactive per supply - run it from the page; edits there generate an UPDATE + rollback script." },
},
/* ---------------------------------------------------------------- */
{
  id: "ibp-detect", menu: "Incorrect Billing Period", title: "Anomalies with LAST_BILLING_DATE after BILLING_DATE",
  purpose: "Open anomalies (not ESTAN00003/04/05/08) whose LAST_BILLING_DATE is later than BILLING_DATE, with item, reading and the contract that ended exactly on the reading date (NEXT_BILLING_SERVICE). Grouped by offered service on the page.",
  source: "app/core/incorrect_billing_period.py · build_detect_query()",
  params: [["Excluded statuses", "", "ESTAN00004, ESTAN00005, ESTAN00008, ESTAN00003"], ["Row cap", "TOP", "2000"]],
  sql: `SELECT TOP (2000)
       AN.ID_ANOMALOUS, AN.ANOMALOUS_STATUS, AN.ID_BILLING_SERVICE AS ANOMALY_BILLING_SERVICE,
       AN.LAST_BILLING_DATE, AN.BILLING_DATE,
       CS.ID_OFFERED_SERVICE, OS.NAME_TYPE AS OFFERED_SERVICE_DESC, PF.REFERENCE AS ACCOUNT, SS.NISS,
       ITB.ID_ITEM_TO_BILL, ITB.ID_BILLING_SERVICE AS ITEM_BILLING_SERVICE, ITB.STATUS AS ITEM_STATUS,
       RR.ID_BILLING_PERIOD, RR.ID_READING, RR.READ_STATUS, RR.READING_TYPE, RR.READY_USAGE,
       RR.READING_PREV_DATE, RR.READING_DATE,
       SS1.NISS AS NISS_AT_READING, BS1.ID_BILLING_SERVICE AS NEXT_BILLING_SERVICE
FROM OUC_ADMIN.GCCOM_ANOMALOUS AN
JOIN GCCOM_BILLING_SERVICE BS ON BS.ID_BILLING_SERVICE = AN.ID_BILLING_SERVICE
JOIN GCCOM_CONTRACTED_SERVICE CS ON CS.ID_CONTRACTED_SERVICE = BS.ID_CONTRACTED_SERVICE
JOIN GCCOM_SECTOR_SUPPLY SS ON SS.ID_SECTOR_SUPPLY = CS.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_PAYMENT_FORM PF ON PF.ID_PAYMENT_FORM = CS.ID_PAYMENT_FORM
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE OS ON OS.ID_OFFERED_SERVICE = CS.ID_OFFERED_SERVICE
LEFT JOIN OUC_ADMIN.GCCOM_ITEMS_TO_BILL ITB ON ITB.ID_ITEM_TO_BILL = AN.ID_ITEM_TO_BILL
LEFT JOIN OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL RIT ON RIT.ID_ITEM_TO_BILL = ITB.ID_ITEM_TO_BILL
LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING RR ON RR.ID_READING = RIT.ID_READING
LEFT JOIN GCCOM_SECTOR_SUPPLY SS1 ON SS1.ID_SECTOR_SUPPLY = RR.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_CONTRACTED_SERVICE CS1 ON CS1.ID_SECTOR_SUPPLY = RR.ID_SECTOR_SUPPLY AND RR.READING_DATE = CS1.END_DATE
LEFT JOIN GCCOM_BILLING_SERVICE BS1 ON BS1.ID_CONTRACTED_SERVICE = CS1.ID_CONTRACTED_SERVICE
WHERE AN.LAST_BILLING_DATE > AN.BILLING_DATE
  AND AN.ANOMALOUS_STATUS NOT IN ('ESTAN00004', 'ESTAN00005', 'ESTAN00008', 'ESTAN00003')
ORDER BY CS.ID_OFFERED_SERVICE, AN.ID_ANOMALOUS;

-- Correction (generated for the selected anomalies only):
-- UPDATE GCCOM_ANOMALOUS SET UPDATE_DATE = GETDATE(), UPDATE_USER = 'RMA',
--        UPDATE_PROGRAM = 'RATE_INCORRECT_BILLPERIOD', ANOMALOUS_STATUS = 'ESTAN00005'
-- WHERE ID_ANOMALOUS IN (...) AND ANOMALOUS_STATUS IN ('ESTAN00001', 'ESTAN00009');`,
  example: {
    input: "No input.",
    columns: ["Service", "ID_ANOMALOUS", "Status", "Account", "NISS", "LAST_BILLING_DATE", "BILLING_DATE", "Reading", "Ready usage"],
    rows: [
      ["Electricity", "1309954", "ESTAN00009", "1101•••081", "1042••••-101", "2026-06-24", "2026-06-21", "1044269305", "39,630"],
      ["Water", "1322641", "ESTAN00009", "1102•••122", "1036••••-301", "2026-06-21", "2026-05-04", "1042499251", "0"],
    ],
    note: "Live: 49 anomalies (~1.1 s).",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "biss-case1", menu: "Bill Issuance Validator", title: "Case 1 - Stuck bills (Rate bill blocking)",
  purpose: "Accounts with a notice pending validation on their Rate (176) bill, where that Rate bill is the ONLY cycle bill still invoicing in its period, and an Electricity/Water bill 1-11 periods later is Waiting for other services (ESTFAC0015). The closest later period wins, Electricity before Water.",
  source: "app/core/bill_issuance_validator.py · build_stuck_bills_query()",
  params: [["Periods ahead", "Next Electricity/Water bill window", "1 - 11"], ["Row cap", "TOP", "2000"]],
  sql: `WITH period_counts AS (                        -- cycle bills still invoicing per account + period
  SELECT ID_PAYMENT_FORM, ID_BILLING_PERIOD, COUNT(*) AS BILL_COUNT
  FROM OUC_COMMON_ADMIN.GCCOM_BILL
  WHERE BILL_TYPE = 'TFGEN00001' AND BILLING_STATUS = 'ESTFAC0012'
  GROUP BY ID_PAYMENT_FORM, ID_BILLING_PERIOD
),
candidate AS (                                  -- pending notice on a Rate bill that is alone in its period
  SELECT DISTINCT pf.ID_PAYMENT_FORM, pf.REFERENCE, nt.UPDATE_DATE AS NOTICE_UPDATE_DATE,
         b.ID_BILL AS ID_BILL_RATE, b.ID_BILLING_PERIOD AS PERIOD_RATE
  FROM OUC_ADMIN.GCCOM_NOTICE_TMP nt
  JOIN OUC_COMMON_ADMIN.GCCOM_BILL b ON b.ID_BILL = nt.ID_BILL
  JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
  JOIN period_counts pc ON pc.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM AND pc.ID_BILLING_PERIOD = b.ID_BILLING_PERIOD
  WHERE nt.COD_STATUS = '5000NOTEMP' AND b.BILLING_STATUS = 'ESTFAC0012'
    AND b.ID_OFFERED_SERVICE = 176 AND pc.BILL_COUNT = 1
),
matched AS (                                    -- next Electricity/Water bill waiting for other services
  SELECT c.*, nb.ID_BILL AS ID_BILL_NEXT, nb.ID_OFFERED_SERVICE AS OFFERED_SERVICE_NEXT,
         nb.ID_BILLING_PERIOD AS PERIOD_NEXT, nb.BILLING_STATUS AS STATUS_NEXT,
         nb.ID_BILLING_PERIOD - c.PERIOD_RATE AS PERIODS_AHEAD,
         ROW_NUMBER() OVER (PARTITION BY c.ID_PAYMENT_FORM, c.PERIOD_RATE
                            ORDER BY nb.ID_BILLING_PERIOD ASC, CASE WHEN nb.ID_OFFERED_SERVICE = 1 THEN 0 ELSE 1 END) AS RN
  FROM candidate c
  JOIN OUC_COMMON_ADMIN.GCCOM_BILL nb ON nb.ID_PAYMENT_FORM = c.ID_PAYMENT_FORM
       AND nb.ID_BILLING_PERIOD BETWEEN c.PERIOD_RATE + 1 AND c.PERIOD_RATE + 11
       AND nb.ID_OFFERED_SERVICE IN (1, 19)
  WHERE nb.BILLING_STATUS = 'ESTFAC0015'
)
SELECT TOP (2000) m.ID_PAYMENT_FORM, m.REFERENCE, m.NOTICE_UPDATE_DATE, m.ID_BILL_RATE, m.PERIOD_RATE,
       m.ID_BILL_NEXT, m.OFFERED_SERVICE_NEXT, os.NAME_TYPE AS OFFERED_SERVICE_NEXT_DESC,
       m.PERIOD_NEXT, m.STATUS_NEXT, m.PERIODS_AHEAD, bs.NAME_TYPE AS STATUS_NEXT_DESC
FROM matched m
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE os ON os.ID_OFFERED_SERVICE = m.OFFERED_SERVICE_NEXT
LEFT JOIN OUC_ADMIN.GCCOM_BILL_STATUS bs ON bs.COD_DEVELOP = m.STATUS_NEXT
WHERE m.RN = 1
ORDER BY m.NOTICE_UPDATE_DATE;

-- Release script (generated):
-- UPDATE GCCOM_NOTICE_TMP SET COD_STATUS = '1000NOTEMP', UPDATE_DATE = GETDATE(), UPDATE_USER = 'RMA',
--        UPDATE_PROGRAM = 'VALIDATION RELEASE_TERMINATED'
-- WHERE ID_NOTICE_TMP IN (SELECT nt.ID_NOTICE_TMP FROM GCCOM_NOTICE_TMP nt JOIN GCCOM_BILL b ON b.ID_BILL = nt.ID_BILL
--                         WHERE nt.COD_STATUS = '5000NOTEMP' AND b.BILLING_STATUS = 'ESTFAC0012' AND b.ID_BILL IN (...))
--   AND COD_STATUS = '5000NOTEMP';`,
  example: {
    input: "No input.",
    columns: ["Account", "Notice updated", "Rate bill", "Rate period", "Next bill", "Service", "Next period", "Ahead"],
    rows: [
      ["1103•••160", "2026-09-24 19:41", "1075416672", "10000000236", "1075850591", "Electricity", "10000000238", "2"],
      ["1103•••783", "2026-09-29 19:49", "1075211928", "10000000237", "1075736263", "Electricity", "10000000238", "1"],
    ],
    note: "Live: 2,424 rows incl. New Contract Match (both queries run in parallel).",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "biss-ncm", menu: "Bill Issuance Validator", title: "Case 1 - New Contract Match",
  purpose: "A new Rate (176) contract's first bill (LAST_BILLING_DATE = contract FROM_DATE, contract Active) still invoicing with a pending notice - the account's ONLY pending notice and the only cycle bill invoicing in that period - and no later bill Waiting for other services (that is covered by Stuck bills).",
  source: "app/core/bill_issuance_validator.py · build_new_contract_match_query()",
  params: [["Row cap", "TOP", "2000"]],
  sql: `WITH pending_counts AS (
  SELECT b2.ID_PAYMENT_FORM, COUNT(*) AS PENDING_COUNT
  FROM OUC_ADMIN.GCCOM_NOTICE_TMP t2
  JOIN OUC_COMMON_ADMIN.GCCOM_BILL b2 ON b2.ID_BILL = t2.ID_BILL
  WHERE t2.COD_STATUS = '5000NOTEMP'
  GROUP BY b2.ID_PAYMENT_FORM
),
period_counts AS (
  SELECT ID_PAYMENT_FORM, ID_BILLING_PERIOD, COUNT(*) AS BILL_COUNT
  FROM OUC_COMMON_ADMIN.GCCOM_BILL
  WHERE BILL_TYPE = 'TFGEN00001' AND BILLING_STATUS = 'ESTFAC0012'
  GROUP BY ID_PAYMENT_FORM, ID_BILLING_PERIOD
)
SELECT TOP (2000) pf.ID_PAYMENT_FORM, pf.REFERENCE, t.UPDATE_DATE AS NOTICE_UPDATE_DATE,
       b.ID_BILL, b.BILLING_STATUS, b.LAST_BILLING_DATE, b.BILLING_DATE, b.ID_BILLING_PERIOD,
       cs.ID_CONTRACTED_SERVICE, cs.FROM_DATE AS CONTRACT_FROM_DATE, cs.STATUS AS CONTRACT_STATUS
FROM OUC_ADMIN.GCCOM_NOTICE_TMP t
JOIN OUC_COMMON_ADMIN.GCCOM_BILL b ON b.ID_BILL = t.ID_BILL
JOIN OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE
JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
JOIN pending_counts pc ON pc.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
JOIN period_counts prc ON prc.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM AND prc.ID_BILLING_PERIOD = b.ID_BILLING_PERIOD
WHERE b.BILLING_STATUS = 'ESTFAC0012' AND t.COD_STATUS = '5000NOTEMP'
  AND b.LAST_BILLING_DATE = cs.FROM_DATE AND b.BILL_TYPE = 'TFGEN00001'
  AND cs.STATUS = 'ESTSC00002' AND cs.ID_OFFERED_SERVICE = 176
  AND pc.PENDING_COUNT = 1 AND prc.BILL_COUNT = 1
  AND NOT EXISTS (SELECT 1 FROM OUC_COMMON_ADMIN.GCCOM_BILL b3
                  WHERE b3.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
                    AND b3.ID_BILLING_PERIOD > b.ID_BILLING_PERIOD
                    AND b3.BILLING_STATUS = 'ESTFAC0015')
ORDER BY t.UPDATE_DATE;`,
  example: { input: "No input.", columns: ["Account", "ID_BILL", "LAST_BILLING_DATE", "CONTRACT_FROM_DATE", "Period"], rows: [], note: "Shown in the same Case 1 table with pattern 'New contract match'." },
},
/* ---------------------------------------------------------------- */
{
  id: "biss-case2", menu: "Bill Issuance Validator", title: "Case 2 - Terminated account period mismatch",
  purpose: "Accounts whose every service is Terminated (recently, default 60 days) and that have a pending notice on an invoicing bill. Each service's final cycle bill (billing date = termination day, Invoicing/Invoiced) is compared with the account's latest final-bill period (TARGET_PERIOD); a missing final bill or a still-invoicing bill in another period needs an update.",
  source: "app/core/bill_issuance_validator.py · build_terminated_period_mismatch_query()",
  params: [["Look-back", "Terminated in the last N days", "60"], ["Row cap", "TOP", "8000"]],
  sql: `WITH terminated_services AS (
  SELECT cs.ID_CONTRACTED_SERVICE, cs.ID_PAYMENT_FORM, cs.ID_OFFERED_SERVICE, cs.END_DATE
  FROM OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs
  WHERE cs.STATUS = 'ESTSC00004'
    AND cs.END_DATE >= DATEADD(DAY, -60, CAST(GETDATE() AS DATE))
    AND EXISTS (SELECT 1 FROM OUC_ADMIN.GCCOM_NOTICE_TMP nt
                JOIN OUC_COMMON_ADMIN.GCCOM_BILL nb ON nb.ID_BILL = nt.ID_BILL
                WHERE nt.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
                  AND nt.COD_STATUS = '5000NOTEMP' AND nb.BILLING_STATUS = 'ESTFAC0012')
),
matched AS (                                    -- each service's own final bill (date-only match)
  SELECT ts.*, b.ID_BILL, b.ID_BILLING_PERIOD, b.BILLING_STATUS,
         ROW_NUMBER() OVER (PARTITION BY ts.ID_CONTRACTED_SERVICE ORDER BY b.ID_BILL DESC) AS RN
  FROM terminated_services ts
  LEFT JOIN OUC_COMMON_ADMIN.GCCOM_BILL b
         ON b.ID_CONTRACTED_SERVICE = ts.ID_CONTRACTED_SERVICE
        AND b.BILLING_DATE >= CAST(ts.END_DATE AS DATE)
        AND b.BILLING_DATE <  DATEADD(DAY, 1, CAST(ts.END_DATE AS DATE))
        AND b.BILLING_STATUS IN ('ESTFAC0012', 'ESTFAC0005')
        AND b.BILL_TYPE = 'TFGEN00001'
),
with_target AS (
  SELECT *, MAX(ID_BILLING_PERIOD) OVER (PARTITION BY ID_PAYMENT_FORM) AS TARGET_PERIOD
  FROM matched WHERE RN = 1
),
flagged AS (
  SELECT *,
         CASE WHEN ID_BILL IS NULL THEN 1
              WHEN ID_BILLING_PERIOD <> TARGET_PERIOD AND BILLING_STATUS = 'ESTFAC0012' THEN 1 ELSE 0 END AS NEEDS_UPDATE,
         MAX(CASE WHEN ID_BILL IS NULL THEN 1
                  WHEN ID_BILLING_PERIOD <> TARGET_PERIOD AND BILLING_STATUS = 'ESTFAC0012' THEN 1 ELSE 0 END)
             OVER (PARTITION BY ID_PAYMENT_FORM) AS ACCOUNT_HAS_ISSUE
  FROM with_target
),
account_status AS (                             -- only accounts with no live service left
  SELECT cs.ID_PAYMENT_FORM,
         SUM(CASE WHEN cs.STATUS NOT IN ('ESTSC00004', 'ESTSC00005') THEN 1 ELSE 0 END) AS ACTIVE_COUNT
  FROM OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs
  WHERE cs.ID_PAYMENT_FORM IN (SELECT DISTINCT ID_PAYMENT_FORM FROM terminated_services)
  GROUP BY cs.ID_PAYMENT_FORM
)
SELECT TOP (8000) f.ID_PAYMENT_FORM, pf.REFERENCE, f.ID_OFFERED_SERVICE, f.ID_CONTRACTED_SERVICE,
       f.END_DATE, f.ID_BILL, f.ID_BILLING_PERIOD, f.BILLING_STATUS, f.TARGET_PERIOD, f.NEEDS_UPDATE, f.ACCOUNT_HAS_ISSUE
FROM flagged f
JOIN account_status acct ON acct.ID_PAYMENT_FORM = f.ID_PAYMENT_FORM AND acct.ACTIVE_COUNT = 0
JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = f.ID_PAYMENT_FORM
ORDER BY f.ID_PAYMENT_FORM, f.ID_OFFERED_SERVICE;`,
  example: {
    input: "Look-back 60 days.",
    columns: ["Account", "Target period", "Services", "Need update", "Missing bill", "Period mismatch", "Complete"],
    rows: [["1076•••337", "10000000237", "4", "0", "0", "0", "Yes"], ["1081•••405", "10000000238", "4", "3", "0", "3", "No"]],
    note: "Rows grouped per account on the page (live: 260 accounts needing action). Case 1 accounts are excluded.",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "biss-case3", menu: "Bill Issuance Validator", title: "Case 3 - All contract status, bills complete",
  purpose: "Per account and billing period: the account's contracted services (Active, Suspended, Pending termination, Terminated) equal its cycle bills still invoicing in that period - nothing missing, yet a notice is pending. WITH_ACTIVE_CONTRACT says if any non-terminated service remains.",
  source: "app/core/bill_issuance_validator.py · build_bills_complete_query()",
  params: [["Year", "Periods whose INITIAL_DATE is in this year", "2026"], ["Billing period", "Optional single period instead of the year", "-"], ["Row cap", "TOP", "5000"]],
  sql: `WITH pending_accounts AS (
  SELECT DISTINCT pf1.ID_PAYMENT_FORM
  FROM OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf1
  JOIN OUC_COMMON_ADMIN.GCCOM_BILL b ON b.ID_PAYMENT_FORM = pf1.ID_PAYMENT_FORM
  JOIN OUC_ADMIN.GCCOM_NOTICE_TMP tmp ON tmp.ID_BILL = b.ID_BILL
  WHERE b.BILLING_STATUS = 'ESTFAC0012' AND tmp.COD_STATUS = '5000NOTEMP' AND tmp.UPDATE_DATE < GETDATE()
),
period_bills AS (
  SELECT B.ID_PAYMENT_FORM, B.ID_BILLING_PERIOD, COUNT(*) AS BILLS
  FROM OUC_COMMON_ADMIN.GCCOM_BILL B
  WHERE B.BILLING_STATUS = 'ESTFAC0012' AND B.BILL_TYPE = 'TFGEN00001'
    AND B.ID_BILLING_PERIOD IN (SELECT ID_BILLING_PERIOD FROM OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD
                                WHERE YEAR(INITIAL_DATE) = 2026)
    AND B.ID_PAYMENT_FORM IN (SELECT ID_PAYMENT_FORM FROM pending_accounts)
  GROUP BY B.ID_PAYMENT_FORM, B.ID_BILLING_PERIOD
),
contract_counts AS (
  SELECT ID_PAYMENT_FORM, COUNT(*) AS CONTRACTED_SERVICES
  FROM OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE
  WHERE STATUS IN ('ESTSC00002', 'ESTSC00007', 'ESTSC00003', 'ESTSC00004')
    AND ID_PAYMENT_FORM IN (SELECT DISTINCT ID_PAYMENT_FORM FROM period_bills)
  GROUP BY ID_PAYMENT_FORM
)
SELECT TOP (5000) pf.REFERENCE, cc.ID_PAYMENT_FORM, cc.CONTRACTED_SERVICES, pb.BILLS,
       cc.CONTRACTED_SERVICES - pb.BILLS AS MISSING_BILLS, pb.ID_BILLING_PERIOD, bp.PERIOD_NAME AS BILLING_PERIOD_NAME,
       CASE WHEN EXISTS (SELECT 1 FROM OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE r
                         WHERE r.ID_PAYMENT_FORM = cc.ID_PAYMENT_FORM
                           AND r.STATUS IN ('ESTSC00002', 'ESTSC00007', 'ESTSC00003'))
            THEN 'YES' ELSE 'NO' END AS WITH_ACTIVE_CONTRACT
FROM period_bills pb
JOIN contract_counts cc ON cc.ID_PAYMENT_FORM = pb.ID_PAYMENT_FORM
JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = cc.ID_PAYMENT_FORM
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = pb.ID_BILLING_PERIOD
WHERE cc.CONTRACTED_SERVICES = pb.BILLS
ORDER BY pb.ID_BILLING_PERIOD, pf.REFERENCE;`,
  example: {
    input: "Year 2026.",
    columns: ["Account", "Services", "Bills", "Missing", "Period", "With active contract"],
    rows: [["1061•••022", "4", "4", "0", "8-August 2026", "YES"], ["1040•••086", "4", "4", "0", "10-October 2026", "YES"]],
    note: "Live: 176 rows. Accounts already in Case 1 or Case 2 are excluded (queries run in parallel).",
  },
},
/* ---------------------------------------------------------------- */
{
  id: "biss-case4", menu: "Bill Issuance Validator", title: "Case 4 - Unclassified",
  purpose: "Every currently-invoicing bill of an account with a pending notice. The page removes the accounts already explained by Case 1 (Stuck + New Contract Match), Case 2 and Case 3 - all five queries run in parallel.",
  source: "app/core/bill_issuance_validator.py · build_unclassified_query()",
  params: [["Row cap", "Called unlimited by the page", "-"]],
  sql: `WITH pending_accounts AS (
  SELECT DISTINCT pf1.ID_PAYMENT_FORM
  FROM OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf1
  JOIN OUC_COMMON_ADMIN.GCCOM_BILL b ON b.ID_PAYMENT_FORM = pf1.ID_PAYMENT_FORM
  JOIN OUC_ADMIN.GCCOM_NOTICE_TMP tmp ON tmp.ID_BILL = b.ID_BILL
  WHERE b.BILLING_STATUS = 'ESTFAC0012' AND tmp.COD_STATUS = '5000NOTEMP'
)
SELECT pf.REFERENCE, pa.ID_PAYMENT_FORM, b.ID_BILL, b.ID_BILLING_PERIOD, b.BILL_TYPE,
       b.ID_OFFERED_SERVICE, os.NAME_TYPE AS OFFERED_SERVICE_DESC,
       b.BILLING_STATUS, bs.NAME_TYPE AS BILLING_STATUS_DESC, b.BILLING_DATE
FROM pending_accounts pa
JOIN OUC_COMMON_ADMIN.GCCOM_BILL b ON b.ID_PAYMENT_FORM = pa.ID_PAYMENT_FORM AND b.BILLING_STATUS = 'ESTFAC0012'
JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = pa.ID_PAYMENT_FORM
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE os ON os.ID_OFFERED_SERVICE = b.ID_OFFERED_SERVICE
LEFT JOIN OUC_ADMIN.GCCOM_BILL_STATUS bs ON bs.COD_DEVELOP = b.BILLING_STATUS
ORDER BY pf.REFERENCE, b.ID_BILLING_PERIOD;`,
  example: { input: "No input.", columns: ["Account", "Bills invoicing", "Services"], rows: [], note: "Live: 781 unclassified accounts (was ~14 s; ~6 s with the parallel run)." },
},
/* ---------------------------------------------------------------- */
{
  id: "wbc", menu: "Wrong Billed Consumption", title: "Three-way usage comparison per bill",
  purpose: "For each Invoiced/Generated, non-credit-note bill of the period: (1) calculation base of CONCSMO003/CC210, (2) ready usage of its READINGS_ITEMSTOBILL rows, (3) ready usage of the distinct GCGT_RE_READING rows (Active Energy / Water only). Bills where any two differ by more than 0.001.",
  source: "app/core/wrong_billed_consumption.py · build_wrong_billed_consumption_query()",
  params: [["Billing period", "Default: current", "10000000238"], ["Bill range", "ID_BILL chunk (50,000 bills each, 3 in parallel)", "@FROM - @TO"]],
  sql: `WITH cb AS (                                   -- 1. calculation base per bill
    SELECT b.ID_BILL, SUM(bd.CALCULATION_BASE) AS CALCULATION_BASE
    FROM OUC_COMMON_ADMIN.GCCOM_BILL b
    JOIN OUC_ADMIN.GCCOM_BILLING_CONCEPT bc ON bc.ID_BILL = b.ID_BILL
    JOIN OUC_ADMIN.GCCOM_BILLING_CONCEPT_DETAIL bd ON bd.ID_BILLING_CONCEPT = bc.ID_BILLING_CONCEPT
    WHERE b.ID_BILLING_PERIOD = 10000000238 AND b.ID_BILL BETWEEN @FROM AND @TO
      AND bc.COD_CONCEPT IN ('CONCSMO003', 'CC210')
      AND b.BILLING_STATUS IN ('ESTFAC0005', 'ESTFAC0008')
      AND ISNULL(b.BILLING_TYPE, '') <> 'TIPFAC0011'
    GROUP BY b.ID_BILL
),
lk AS (                                         -- bill -> items to bill -> reading links
    SELECT itb.ID_BILL, rit.ID_ITEM_TO_BILL, rit.ID_READING, rit.READY_USAGE
    FROM cb
    JOIN GCCOM_ITEMS_TO_BILL itb ON itb.ID_BILL = cb.ID_BILL
    JOIN OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL rit ON rit.ID_ITEM_TO_BILL = itb.ID_ITEM_TO_BILL
    JOIN GCGT_RE_READING r1 ON r1.ID_READING = rit.ID_READING AND r1.USAGE_TYPE IN ('TPCONS0001', 'TPCONS0006')
),
rs AS (SELECT ID_BILL, SUM(READY_USAGE) AS RIT_READY_USAGE FROM lk GROUP BY ID_BILL),     -- 2.
rr AS (SELECT x.ID_BILL, SUM(r.READY_USAGE) AS READING_READY_USAGE                          -- 3.
       FROM (SELECT DISTINCT ID_BILL, ID_READING FROM lk) x
       JOIN GCGT_RE_READING r ON r.ID_READING = x.ID_READING
       GROUP BY x.ID_BILL)
SELECT cb.ID_BILL, cb.CALCULATION_BASE, rs.RIT_READY_USAGE, rr.READING_READY_USAGE,
       cb.CALCULATION_BASE - ISNULL(rs.RIT_READY_USAGE, 0)          AS DIFF_CALC_VS_RIT,
       cb.CALCULATION_BASE - ISNULL(rr.READING_READY_USAGE, 0)      AS DIFF_CALC_VS_READING,
       ISNULL(rs.RIT_READY_USAGE, 0) - ISNULL(rr.READING_READY_USAGE, 0) AS DIFF_RIT_VS_READING
       -- (+ NISS, statuses, reading details, MP type in the app)
FROM cb
LEFT JOIN rs ON rs.ID_BILL = cb.ID_BILL
LEFT JOIN rr ON rr.ID_BILL = cb.ID_BILL
WHERE ABS(cb.CALCULATION_BASE - ISNULL(rs.RIT_READY_USAGE, 0)) > 0.001
   OR ABS(cb.CALCULATION_BASE - ISNULL(rr.READING_READY_USAGE, 0)) > 0.001
   OR ABS(ISNULL(rs.RIT_READY_USAGE, 0) - ISNULL(rr.READING_READY_USAGE, 0)) > 0.001
ORDER BY ABS(cb.CALCULATION_BASE - ISNULL(rs.RIT_READY_USAGE, 0)) DESC;`,
  example: { input: "Billing period + one ID_BILL chunk.", columns: ["ID_BILL", "Calc base", "RIT ready", "Reading ready", "Diff"], rows: [], note: "A full period is ~1.7 M bills → ~34 chunks run as a background job (progress shown on the page)." },
},
/* ---------------------------------------------------------------- */
{
  id: "bulk-pending", menu: "Bulk Checker", title: "Bulk accounts for a billing cycle",
  purpose: "One row per bulk (grouped) account with active services in the date range: lot/file details for the period (NULL file_number = no lot generated yet) and flags for services with no bill or with a bill not yet in a sent lot.",
  source: "app/core/bulk_checker.py · build_pending_bulks_sql()",
  params: [["Billing period", "", "e.g. 10000000238"], ["Date from / to", "Contract active in the range", "cycle dates"], ["Status filter", "all / pending / generated / missing_bill / in_invoicing", "all"]],
  sql: `SELECT DISTINCT ab.ID_PAYMENT_FORM_BUNCHER, pf2.NEXT_GROUP_DATE, pf2.reference AS ACCOUNT_NUMBER, pf2.IND_GROUP_BILLS,
       BDET.send_date, BDET.total_reg, BDET.total_amount, BDET.pending_amount, BDET.process_date,
       BDET.file_number, BDET.num_account,
       ISNULL(BILLAGG.has_missing_bill, 0) AS has_missing_bill,
       ISNULL(BILLAGG.has_bill_in_invoicing, 0) AS has_bill_in_invoicing
FROM GCCOM_ACCOUNT_BUNCHER ab
JOIN GCCOM_PAYMENT_FORM pf  ON ab.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM
JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_PAYMENT_FORM = ab.ID_PAYMENT_FORM
JOIN GCCOM_PAYMENT_FORM pf2 ON pf2.ID_PAYMENT_FORM = ab.ID_PAYMENT_FORM_BUNCHER
LEFT JOIN (                                     -- lot / file generated for the period
    SELECT DISTINCT bl.ID_PAYMENT_FORM_BUNCHER, send_date, total_reg, bl.total_amount, bl.pending_amount,
           process_date, file_number, COUNT(DISTINCT b.id_payment_form) AS num_account
    FROM GCCOM_BUNCHER_LOT bl
    JOIN GCCOM_BUNCHER_LOT_DETAIL bld ON bld.ID_BUNCHER_LOT = bl.ID_BUNCHER_LOT
    JOIN gccom_bill b ON b.ID_BILL = bld.ID_BILL
    WHERE b.ID_BILLING_PERIOD = @PERIOD
    GROUP BY bl.ID_PAYMENT_FORM_BUNCHER, send_date, total_reg, bl.total_amount, bl.pending_amount, process_date, file_number
) BDET ON BDET.ID_PAYMENT_FORM_BUNCHER = ab.ID_PAYMENT_FORM_BUNCHER
LEFT JOIN (                                     -- per-bulk bill roll-up (missing / still invoicing)
    SELECT ab2.ID_PAYMENT_FORM_BUNCHER,
           MAX(CASE WHEN b2.id_bill IS NULL THEN 1 ELSE 0 END) AS has_missing_bill,
           MAX(CASE WHEN b2.id_bill IS NOT NULL AND bl2.FILE_NUMBER IS NULL THEN 1 ELSE 0 END) AS has_bill_in_invoicing
    FROM GCCOM_ACCOUNT_BUNCHER ab2
    JOIN GCCOM_PAYMENT_FORM pf1b ON pf1b.ID_PAYMENT_FORM = ab2.ID_PAYMENT_FORM
    JOIN GCCOM_CONTRACTED_SERVICE cs2 ON cs2.ID_PAYMENT_FORM = ab2.ID_PAYMENT_FORM
    JOIN gccom_billing_service bs2 ON bs2.ID_CONTRACTED_SERVICE = cs2.id_contracted_service
    LEFT JOIN gccom_bill b2 ON b2.id_payment_form = pf1b.id_payment_form AND b2.ID_BILLING_SERVICE = bs2.ID_BILLING_SERVICE
         AND b2.bill_type = 'TFGEN00001' AND b2.billing_status <> 'ESTFAC0007'
         AND b2.ID_BILLING_PERIOD = @PERIOD AND b2.billing_type IN ('TIPFAC0001', 'TIPFAC0002')
    LEFT JOIN GCCB_NOTICE_BILL nb2 ON nb2.id_bill = b2.id_bill
    LEFT JOIN gccb_notice n2 ON n2.id_notice = nb2.ID_NOTICE AND n2.ID_PAYMENT_FORM = pf1b.ID_PAYMENT_FORM
    LEFT JOIN GCCOM_BUNCHER_LOT_DETAIL bld2 ON bld2.ID_NOTICE = n2.ID_NOTICE
    LEFT JOIN GCCOM_BUNCHER_LOT bl2 ON bl2.ID_BUNCHER_LOT = bld2.ID_BUNCHER_LOT
    WHERE NOT EXISTS (SELECT cs3.nisc FROM GCCOM_CONTRACTED_SERVICE cs3
                      WHERE cs3.id_sector_supply = cs2.id_sector_supply AND cs3.end_date > cs2.end_date)
      AND ab2.END_DATE IS NULL AND cs2.STATUS <> 'ESTSC00005'
      AND cs2.FROM_DATE < @DATE_TO AND cs2.END_DATE >= @DATE_FROM
    GROUP BY ab2.ID_PAYMENT_FORM_BUNCHER
) BILLAGG ON BILLAGG.ID_PAYMENT_FORM_BUNCHER = ab.ID_PAYMENT_FORM_BUNCHER
WHERE cs.FROM_DATE < @DATE_TO AND cs.END_DATE >= @DATE_FROM
  AND ab.END_DATE IS NULL AND cs.STATUS <> 'ESTSC00005'
  /* + status filter, e.g. pending: AND BDET.file_number IS NULL */;`,
  example: { input: "Period + cycle dates + status filter.", columns: ["Bulk account", "File number", "Total amount", "Accounts", "Missing bill", "In invoicing"], rows: [], note: "Interactive - run it from the page." },
},
];
