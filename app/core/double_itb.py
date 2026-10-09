"""
DOUBLE ITB: a billing service with two items to bill for the SAME
INI_DATE/END_DATE - one already Billed, the other stuck Anomalous.

RJ, 2026-09-27: "create a new menu: DOUBLE ITB, this will be
GCCOM_ITEMS_TO_BILL, where we have 2 or more records for the same
ID_BILLING_SERVICE, where one is in status BILLED, and the other one is in
status anomalous, both share the same INI_DATE, and END_DATE. SHOW ME THE
NISS, BILLING_PERIOD, AND ITB DETAILS, then connect the ITB with
READING_ITEMS_TO_BILL, for the one in status anomalous, show the
READY_USAGE, IF the ready usage <> 0, then add an indicator that this one
needs a rebilling."

Live-confirmed 2026-09-27:
  - GCCOM_ITEMS_TO_BILL_STATUS: STTOBILL07 = Billed ("Facturado"),
    STTOBILL00 = Anomalous ("Anómalo").
  - The reading link table is OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL
    (ID_ITEM_TO_BILL, ID_READING, READY_USAGE, ...). One item to bill can
    carry several readings, so READY_USAGE is SUMMED per anomalous item
    (OUTER APPLY), with the reading count alongside.
  - First live run: 141 anomalous items paired with a billed twin, 107 of
    them with non-zero ready usage (needs rebilling), ~2s.

Shape: one row per (anomalous item, billed twin) pair. NISS / account /
offered service come through GCCOM_BILLING_SERVICE -> GCCOM_CONTRACTED_
SERVICE -> GCCOM_SECTOR_SUPPLY / GCCOM_PAYMENT_FORM. The headline billing
period (English, via GCTS_DICTIONARY) is the anomalous item's; the billed
twin's own period id is shown too since the two can differ.

NEEDS_REBILLING = 1 when the anomalous item's summed READY_USAGE <> 0
(NULL counts as 0).

Pure logic: builds SELECT text only.
"""
from __future__ import annotations

from .sql_format import format_sql_literal

ITB_STATUS_BILLED = "STTOBILL07"
ITB_STATUS_ANOMALOUS = "STTOBILL00"
READINGS_ITEMS_TO_BILL_TABLE = "OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL"
OFFERED_SERVICE_TABLE = "OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE"


# ---------------------------------------------------------------------------
# Fix script (RJ 2026-10-05): "1 cancel the GCCOM_ITEMS_TO_BILL (status 0004)
# for the duplicate ITB, 2. update the gcgt_re_reading to Billed status. If the
# ready usage is > 0, insert a GCCOM_REBILLING_ACTIVITY and a REB ACTIVITY
# INFORMATION; the GCCOM_BILL is the one of the duplicated ITB that is billed"
# + "i want the rebilling to be processed by batch".
#   - duplicate ITB = the Anomalous twin (STTOBILL00) -> STTOBILL04 Cancelled
#   - its readings (GCCOM_READINGS_ITEMSTOBILL) -> READ_STATUS 7000STSRED
#     "Facturada" (Billed)
#   - rebilling: RJ's two INSERTs; ID_CUSTOMER = the billed bill's payment
#     form customer, ID_BILL = the billed twin's bill. Batch = the bill note
#     goes in EREALB0005 "Recalculado Batch" (RJ 2026-10-06; was 0006) (live
#     GCCOM_REB_DELIVERY_NOTE_STATUS) instead of EREALB0001 "Reclamado".
# ---------------------------------------------------------------------------
ITB_STATUS_CANCELLED = "STTOBILL04"
ITB_STATUS_PENDING = "STTOBILL01"
ANOMALOUS_OPEN_STATUSES = ("ESTAN00009", "ESTAN00001")   # same as date_anomaly
ANOMALOUS_STATUS_CANCELLED = "ESTAN00005"
# RJ 2026-10-06: "the main query should only pick water and electricity";
# a Water duplicate also carries its Sanitary twin (cancelled + 2nd bill).
OFFERED_ELECTRICITY = 1
OFFERED_WATER = 19
OFFERED_SANITARY = 190
READ_STATUS_BILLED = "7000STSRED"
READING_TABLE = "OUC_COMMON_ADMIN.GCGT_RE_READING"
REB_DN_STATUS_CLAIMED = "EREALB0001"        # Reclamado (RJ's template)
# RJ 2026-10-06: "REB DN STATUS should be inserted with EREALB0005"
REB_DN_STATUS_BATCH = "EREALB0005"          # Recalculado Batch
# RJ 2026-10-06: "the rebilling should be inserted as Pending status, then the
# gccom_bill should be updated to Disputed" (live GCCOM_REB_ACTIVITY_STATUS /
# GCCOM_BILL_STATUS).
# RJ 2026-10-06 (later): "insert as in process status, and the rebilling type
# is massive substitutive TIPREF0005" -> ESTREF0002 "En tratamiento".
REB_ACTIVITY_STATUS_PENDING = "ESTREF0002"  # En tratamiento (In process)
REB_ACTIVITY_TYPE = "TIPREF0005"            # Massive Substitutive
BILL_STATUS_DISPUTED = "ESTFAC0010"         # Reclamada / Disputed
# RJ 2026-10-08: "in the Description column, please add 'AUTO REBILLING DOUBLE
# ITB', then at the end of script generate add a commented query to get all
# with this comment, and the status is not resolved or rebilled ... create
# date, id_rebilling_activity and status in english". Live
# GCCOM_REB_ACTIVITY_STATUS: 0001 Pending, 0002 In process, 0003 Rejected
# (resolved), 0004 Rebilled -> the follow-up lists everything not 0003/0004.
# DESCRIPTION is varchar(300) -> plain literal (no N'...').
REB_DESCRIPTION = "AUTO REBILLING DOUBLE ITB"
REB_ACTIVITY_FINAL_STATUSES = ("ESTREF0003", "ESTREF0004")  # Rejected, Rebilled
FIX_PROGRAM_DEFAULT = "DOUBLE_ITB"


def build_followup_query() -> str:
    """Open rebilling activities created by this fix (DESCRIPTION tag), with
    the status in English. Appended to every fix script as a /* */ comment so
    it survives the 'clean' option (which only strips -- lines)."""
    final = ", ".join(format_sql_literal(s) for s in REB_ACTIVITY_FINAL_STATUSES)
    return f"""SELECT ra.ID_REBILLING_ACTIVITY,
       ra.CREATE_DATE,
       ra.REB_ACTIVITY_STATUS,
       COALESCE(d.TEXT, st.NAME_TYPE) AS STATUS_EN,
       ra.ID_CUSTOMER,
       ra.BILLS_NUMBER,
       ra.UPDATE_USER,
       ra.UPDATE_PROGRAM
FROM OUC_ADMIN.GCCOM_REBILLING_ACTIVITY ra WITH (NOLOCK)
LEFT JOIN OUC_ADMIN.GCCOM_REB_ACTIVITY_STATUS st ON st.COD_DEVELOP = ra.REB_ACTIVITY_STATUS
LEFT JOIN GCTS_DICTIONARY d ON d.ID = st.NAME_TYPE_XI18N AND d.LOCALE = 'EN'
WHERE ra.DESCRIPTION = {format_sql_literal(REB_DESCRIPTION)}
  AND ra.REB_ACTIVITY_STATUS NOT IN ({final})   -- not Rejected (resolved) / Rebilled
ORDER BY ra.CREATE_DATE DESC;"""
FIX_USER_DEFAULT = "RMA"
REB_CREATION_USER_DEFAULT = 10000008456


def build_fix_context_query(anom_ids) -> str:
    """Fresh state of the selected Anomalous items: billed twin + its bill,
    customer, summed ready usage and the reading ids. Only pairs still
    Anomalous / Billed come back (re-verified at generate time)."""
    ids = sorted({int(x) for x in anom_ids if str(x).strip()})
    if not ids:
        raise ValueError("No items selected.")
    billed = format_sql_literal(ITB_STATUS_BILLED)
    anomalous = format_sql_literal(ITB_STATUS_ANOMALOUS)
    rit_t = READINGS_ITEMS_TO_BILL_TABLE
    # RJ 2026-10-06: for a WATER duplicate, the Sanitary item to bill that
    # shares its reading(s) (GCCOM_READINGS_ITEMSTOBILL) is the Sanitary
    # duplicate - cancelled too - and the Sanitary bill = that Sanitary
    # billing service's Billed item (same INI/END dates) -> its ID_BILL.
    san_join = f"""
    FROM {rit_t} r1
    JOIN {rit_t} r2 ON r2.ID_READING = r1.ID_READING AND r2.ID_ITEM_TO_BILL <> r1.ID_ITEM_TO_BILL
    JOIN GCCOM_ITEMS_TO_BILL s ON s.ID_ITEM_TO_BILL = r2.ID_ITEM_TO_BILL AND s.STATUS = {anomalous}
    JOIN GCCOM_BILLING_SERVICE sbs ON sbs.ID_BILLING_SERVICE = s.ID_BILLING_SERVICE
    JOIN GCCOM_CONTRACTED_SERVICE scs ON scs.ID_CONTRACTED_SERVICE = sbs.ID_CONTRACTED_SERVICE AND scs.ID_OFFERED_SERVICE = {OFFERED_SANITARY}
    WHERE r1.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL AND cs.ID_OFFERED_SERVICE = {OFFERED_WATER}"""
    return f"""
SELECT a.ID_ITEM_TO_BILL AS ANOM_ID, cs.ID_OFFERED_SERVICE AS ID_OFFERED_SERVICE,
       b.ID_ITEM_TO_BILL AS BILLED_ID, b.ID_BILL AS BILLED_ID_BILL,
       bill.BILLING_STATUS AS BILL_STATUS, pf.REFERENCE AS ACCOUNT, pf.ID_CUSTOMER AS ID_CUSTOMER, ss.NISS AS NISS,
       ru.READY_USAGE AS READY_USAGE,
       (SELECT STRING_AGG(CAST(rit.ID_READING AS varchar(20)), ',') FROM {rit_t} rit
         WHERE rit.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL AND rit.ID_READING IS NOT NULL) AS READING_IDS,
       (SELECT STRING_AGG(CAST(x.ID_ITEM_TO_BILL AS varchar(20)), ',') FROM (SELECT DISTINCT s.ID_ITEM_TO_BILL {san_join}) x) AS SAN_ANOM_IDS,
       san.SAN_BILLED_ID, san.SAN_ID_BILL, sanbill.BILLING_STATUS AS SAN_BILL_STATUS,
       -- RJ 2026-10-06: every Anomalous Sanitary item of the same Sanitary
       -- billing service + INI/END dates (the duplicates to reduce to one)
       (SELECT STRING_AGG(CAST(g.ID_ITEM_TO_BILL AS varchar(20)), ',') WITHIN GROUP (ORDER BY g.ID_ITEM_TO_BILL)
          FROM GCCOM_ITEMS_TO_BILL g
         WHERE g.ID_BILLING_SERVICE = sk.SBS AND g.INI_DATE = sk.SINI AND g.END_DATE = sk.SEND
           AND g.STATUS = {anomalous}) AS SAN_GROUP_IDS
FROM GCCOM_ITEMS_TO_BILL a
CROSS APPLY (
    SELECT TOP 1 b.ID_ITEM_TO_BILL, b.ID_BILL FROM GCCOM_ITEMS_TO_BILL b
    WHERE b.ID_BILLING_SERVICE = a.ID_BILLING_SERVICE AND b.STATUS = {billed}
      AND b.INI_DATE = a.INI_DATE AND b.END_DATE = a.END_DATE AND b.ID_ITEM_TO_BILL <> a.ID_ITEM_TO_BILL
    ORDER BY b.ID_ITEM_TO_BILL DESC) b
LEFT JOIN GCCOM_BILL bill ON bill.ID_BILL = b.ID_BILL
JOIN GCCOM_BILLING_SERVICE bs ON bs.ID_BILLING_SERVICE = a.ID_BILLING_SERVICE
JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
LEFT JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = COALESCE(bill.ID_PAYMENT_FORM, cs.ID_PAYMENT_FORM)
OUTER APPLY (SELECT SUM(rit.READY_USAGE) AS READY_USAGE FROM {rit_t} rit
             WHERE rit.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL) ru
OUTER APPLY (
    SELECT TOP 1 sb.ID_ITEM_TO_BILL AS SAN_BILLED_ID, sb.ID_BILL AS SAN_ID_BILL
    FROM {rit_t} r1
    JOIN {rit_t} r2 ON r2.ID_READING = r1.ID_READING AND r2.ID_ITEM_TO_BILL <> r1.ID_ITEM_TO_BILL
    JOIN GCCOM_ITEMS_TO_BILL s ON s.ID_ITEM_TO_BILL = r2.ID_ITEM_TO_BILL AND s.STATUS = {anomalous}
    JOIN GCCOM_BILLING_SERVICE sbs ON sbs.ID_BILLING_SERVICE = s.ID_BILLING_SERVICE
    JOIN GCCOM_CONTRACTED_SERVICE scs ON scs.ID_CONTRACTED_SERVICE = sbs.ID_CONTRACTED_SERVICE AND scs.ID_OFFERED_SERVICE = {OFFERED_SANITARY}
    JOIN GCCOM_ITEMS_TO_BILL sb ON sb.ID_BILLING_SERVICE = s.ID_BILLING_SERVICE AND sb.STATUS = {billed}
         AND sb.INI_DATE = s.INI_DATE AND sb.END_DATE = s.END_DATE
    WHERE r1.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL AND cs.ID_OFFERED_SERVICE = {OFFERED_WATER}
    ORDER BY sb.ID_ITEM_TO_BILL DESC) san
LEFT JOIN GCCOM_BILL sanbill ON sanbill.ID_BILL = san.SAN_ID_BILL
OUTER APPLY (
    SELECT TOP 1 s.ID_BILLING_SERVICE AS SBS, s.INI_DATE AS SINI, s.END_DATE AS SEND {san_join}
    ORDER BY s.ID_ITEM_TO_BILL) sk
WHERE a.STATUS = {anomalous} AND a.ID_ITEM_TO_BILL IN ({', '.join(str(i) for i in ids)})
ORDER BY a.ID_ITEM_TO_BILL
"""


def build_fix_script(rows: list[dict], *, user: str = FIX_USER_DEFAULT, program: str = FIX_PROGRAM_DEFAULT,
                     creation_user: int = REB_CREATION_USER_DEFAULT, batch: bool = True,
                     clean: bool = False) -> dict:
    """rows: dicts from build_fix_context_query (lower-case keys). Returns
    {sql_text, itb_count, reading_count, rebilling_count, warnings}."""
    import datetime as _dt
    u, p = format_sql_literal(user), format_sql_literal(program)
    dn = REB_DN_STATUS_BATCH if batch else REB_DN_STATUS_CLAIMED
    warnings: list[str] = []
    blocks: list[str] = []
    n_read = n_reb = n_itb = n_info = n_kept = n_anom_items = 0
    for i, r in enumerate(rows, start=1):
        anom = r.get("anom_id")
        acct = r.get("account") or ""
        lines = [f"-- [{i}] Account {acct}, NISS {r.get('niss') or ''}: duplicate ITB {anom} (Anomalous) - billed twin "
                 f"{r.get('billed_id')} / bill {r.get('billed_id_bill')}"]
        is_water = str(r.get("id_offered_service") or "") == str(OFFERED_WATER)
        san_ids = [x for x in str(r.get("san_anom_ids") or "").split(",") if x.strip().isdigit()]
        san_group = [x for x in str(r.get("san_group_ids") or "").split(",") if x.strip().isdigit()] or san_ids
        cancel_ids = [str(int(anom))]
        keep_san = None
        if is_water:
            lines.append(f"-- Water: Sanitary duplicate ITB(s) sharing the reading: {', '.join(san_ids) or 'none found'}"
                         f"; Anomalous Sanitary items for those dates: {', '.join(san_group) or '-'}"
                         f" - Sanitary billed ITB {r.get('san_billed_id') or '-'} / bill {r.get('san_id_bill') or '-'}")
            if not san_ids:
                warnings.append(f"ITB {anom} (account {acct}, Water): no Anomalous Sanitary item to bill shares its reading.")
            if r.get("san_billed_id"):
                # a Billed (STTOBILL07) Sanitary item exists -> every Anomalous one is a duplicate
                cancel_ids += [x for x in san_group if x not in cancel_ids]
            elif san_group:
                # RJ 2026-10-06: no Billed Sanitary item for those dates -> keep ONE
                # (prefer one not tied to the cancelled Water duplicate), set it
                # Pending (STTOBILL01) + cancel its open GCCOM_ANOMALOUS, and
                # cancel the other(s) so only 1 Sanitary item remains.
                keep_san = next((x for x in san_group if x not in san_ids), san_group[0])
                cancel_ids += [x for x in san_group if x != keep_san and x not in cancel_ids]
        n_itb += len(cancel_ids)
        lines.append(
            f"UPDATE OUC_ADMIN.GCCOM_ITEMS_TO_BILL SET STATUS = {format_sql_literal(ITB_STATUS_CANCELLED)}, "
            f"UPDATE_DATE = GETDATE(), UPDATE_USER = {u}, UPDATE_PROGRAM = {p} "
            f"WHERE ID_ITEM_TO_BILL IN ({', '.join(cancel_ids)}) AND STATUS = {format_sql_literal(ITB_STATUS_ANOMALOUS)};")
        if keep_san:
            n_kept += 1
            lines += [
                f"-- No Billed Sanitary item for these dates: keep Sanitary ITB {keep_san} -> Pending ({ITB_STATUS_PENDING})",
                f"UPDATE OUC_ADMIN.GCCOM_ITEMS_TO_BILL SET STATUS = {format_sql_literal(ITB_STATUS_PENDING)}, "
                f"UPDATE_DATE = GETDATE(), UPDATE_USER = {u}, UPDATE_PROGRAM = {p} "
                f"WHERE ID_ITEM_TO_BILL = {int(keep_san)} AND STATUS = {format_sql_literal(ITB_STATUS_ANOMALOUS)};",
            ]
        # RJ 2026-10-06: "update all [GCCOM_ANOMALOUS] for the sanitary ITB" -
        # every Sanitary item of the group (the kept/Pending one AND the
        # cancelled ones) gets its open anomalies cancelled.
        if is_water and san_group:
            n_anom_items += len(san_group)
            lines += [
                f"-- Cancel open anomalies (GCCOM_ANOMALOUS) of every Sanitary item for these dates: {', '.join(san_group)}",
                f"UPDATE OUC_ADMIN.GCCOM_ANOMALOUS SET ANOMALOUS_STATUS = {format_sql_literal(ANOMALOUS_STATUS_CANCELLED)}, "
                f"UPDATE_DATE = GETDATE(), UPDATE_USER = {u}, UPDATE_PROGRAM = {p} "
                f"WHERE ID_ITEM_TO_BILL IN ({', '.join(str(int(x)) for x in san_group)}) "
                f"AND ANOMALOUS_STATUS IN ({', '.join(format_sql_literal(s) for s in ANOMALOUS_OPEN_STATUSES)});",
            ]
        reading_ids = [x for x in str(r.get("reading_ids") or "").split(",") if x.strip().isdigit()]
        if reading_ids:
            n_read += len(reading_ids)
            lines.append(
                f"UPDATE {READING_TABLE} SET READ_STATUS = {format_sql_literal(READ_STATUS_BILLED)}, "
                f"UPDATE_DATE = GETDATE(), UPDATE_USER = {u}, UPDATE_PROGRAM = {p} "
                f"WHERE ID_READING IN ({', '.join(reading_ids)}) AND READ_STATUS <> {format_sql_literal(READ_STATUS_BILLED)};")
        else:
            lines.append("-- (no readings linked to this item to bill)")
        try:
            usage = float(r.get("ready_usage") or 0)
        except (TypeError, ValueError):
            usage = 0.0
        if usage > 0:
            bill = r.get("billed_id_bill")
            cust = r.get("id_customer")
            if not bill or not cust:
                warnings.append(f"ITB {anom} (account {acct}): ready usage {usage:g} but no billed bill / customer - rebilling skipped.")
                lines.append("-- WARNING: ready usage > 0 but the billed bill or customer was not found - rebilling skipped")
            else:
                if r.get("bill_status") and r.get("bill_status") != "ESTFAC0005":
                    warnings.append(f"ITB {anom} (account {acct}): bill {bill} is {r.get('bill_status')}, not Invoiced (ESTFAC0005) - check before rebilling.")
                    lines.append(f"-- WARNING: bill {bill} is {r.get('bill_status')}, not Invoiced (ESTFAC0005) - check before running the rebilling")
                # RJ 2026-10-06: Water -> one activity with 2 bills (Water +
                # Sanitary); Electricity -> 1 bill.
                bills = [int(bill)]
                if is_water:
                    if r.get("san_id_bill"):
                        bills.append(int(r.get("san_id_bill")))
                        sst = r.get("san_bill_status")
                        if sst and sst != "ESTFAC0005":
                            warnings.append(f"ITB {anom} (account {acct}): Sanitary bill {r.get('san_id_bill')} is {sst}, not Invoiced (ESTFAC0005).")
                            lines.append(f"-- WARNING: Sanitary bill {r.get('san_id_bill')} is {sst}, not Invoiced (ESTFAC0005)")
                    else:
                        warnings.append(f"ITB {anom} (account {acct}, Water): no billed Sanitary bill found - rebilling has the Water bill only.")
                        lines.append("-- WARNING: no billed Sanitary bill found - rebilling has the Water bill only")
                n_reb += 1
                n_info += len(bills)
                var = f"@ID_REB_{i}"
                info_rows = []
                for bid in bills:
                    info_rows += [
                        "INSERT INTO OUC_ADMIN.GCCOM_REB_ACTIVITY_INFORMATION (CREATE_DATE, UPDATE_DATE, UPDATE_USER, UPDATE_PROGRAM, OPTIMIST_LOCK,",
                        "       ID_REB_ACTIVITY_INFORMATION, ID_REBILLING_ACTIVITY, ID_BILL, REB_DN_STATUS, IND_RECALCULATE, COD_EXP_DATE_TYPE,",
                        "       SESSION_ID, ID_REB_ACT_MASSIVE_ASSOC, IND_USG_MODIF)",
                        f"VALUES (GETDATE(), GETDATE(), {u}, {p}, 1, NEXT VALUE FOR OUC_ADMIN.SEC_GCCOM_REBACTIVITYINFORMAT1, {var},",
                        f"       {bid}, N'{dn}', 0, NULL, NULL, NULL, NULL);",
                    ]
                disputed = [
                    f"UPDATE OUC_COMMON_ADMIN.GCCOM_BILL SET BILLING_STATUS = N'{BILL_STATUS_DISPUTED}', "
                    f"UPDATE_DATE = GETDATE(), UPDATE_USER = {u}, UPDATE_PROGRAM = {p} "
                    f"WHERE ID_BILL IN ({', '.join(str(b) for b in bills)}) AND BILLING_STATUS <> N'{BILL_STATUS_DISPUTED}';"
                ]
                lines += [
                    f"-- ready usage {usage:g} > 0 -> rebilling of bill(s) {', '.join(str(b) for b in bills)}" + (" (processed by batch)" if batch else ""),
                    f"DECLARE {var} NUMERIC(15, 0) = NEXT VALUE FOR OUC_ADMIN.SEC_GCCOM_REBILLINGACTIVITY1;",
                    "INSERT INTO oucewa.OUC_ADMIN.GCCOM_REBILLING_ACTIVITY (CREATE_DATE, UPDATE_DATE, UPDATE_USER, UPDATE_PROGRAM,",
                    "       OPTIMIST_LOCK, ID_REBILLING_ACTIVITY, REB_ACTIVITY_STATUS, REB_ACTIVITY_TYPE, ID_RECLAMATION, INITIAL_CREATION_DATE,",
                    "       DIVERGENT_AMOUNT, ID_CURRENCY, ID_CUSTOMER, BILLS_NUMBER, AFFECTED_AMOUNT, DESCRIPTION, IND_COMPLEMENTARY, REASON_TYPE,",
                    "       CREATION_USER, RESOLUTION_USER, RESOLUTION_DATE, EXP_RESOLUTION_DATE, BATCH_NAME, SELECTED_FROM_DATE,",
                    "       SELECTED_TO_DATE, SELECTED_RATE_ID, OFF_CYCLE_STATEMENT, IND_BILLING_OR_CREATE_DATE, PROCESSING_INFO, ID_OFFICE,",
                    "       SESSION_ID, IND_CYCLE_PRINTING, SELECTED_ACCOUNT)",
                    f"VALUES (GETDATE(), GETDATE(), {u}, {p}, 1, {var}, N'{REB_ACTIVITY_STATUS_PENDING}', N'{REB_ACTIVITY_TYPE}', NULL, CAST(GETDATE() AS DATE),",
                    f"       NULL, NULL, {int(cust)}, {len(bills)}, 0.000000, {format_sql_literal(REB_DESCRIPTION)}, NULL, N'REBRES003', {int(creation_user)}, NULL, NULL,",
                    "       CAST(GETDATE() + 1 AS DATE), NULL, NULL, NULL, NULL, NULL, 1, NULL, 1, NULL, 1, NULL);",
                    *info_rows,
                    # RJ 2026-10-06: "then the gccom_bill should be updated to Disputed"
                    *disputed,
                ]
        else:
            lines.append("-- ready usage = 0 -> no rebilling")
        blocks.append("\n".join(lines))
    header = [
        "-- DOUBLE ITB fix script - generated by ScriptGen",
        f"-- Generated (UTC): {_dt.datetime.now(_dt.timezone.utc).isoformat()}",
        f"-- Selected duplicates: {len(rows)} - items to bill cancelled ({ITB_STATUS_ANOMALOUS} -> {ITB_STATUS_CANCELLED}, incl. Sanitary twins of Water): {n_itb}",
        f"-- Sanitary items kept and set Pending ({ITB_STATUS_PENDING}) where no Billed Sanitary item exists: {n_kept}",
        f"-- Sanitary items whose open GCCOM_ANOMALOUS (ESTAN00009/00001) -> {ANOMALOUS_STATUS_CANCELLED}: {n_anom_items}",
        f"-- Rebilling bill rows (GCCOM_REB_ACTIVITY_INFORMATION; Water = Water + Sanitary bill): {n_info}",
        f"-- Readings set to Billed ({READ_STATUS_BILLED}): {n_read}",
        f"-- Rebilling activities (ready usage > 0): {n_reb} - activity {REB_ACTIVITY_STATUS_PENDING} (In process) / {REB_ACTIVITY_TYPE} (Massive substitutive), bill notes in {dn}"
        + (" (batch recalculation)" if batch else "") + f", bill -> {BILL_STATUS_DISPUTED} (Disputed)",
        f"-- User / program: {user} / {program}",
        "-- Run as ONE batch (each @ID_REB_n links the activity to its information row).",
    ] + [f"-- WARNING: {w}" for w in warnings] + [""]
    text = "\n".join(header) + ("\n\n".join(blocks) if blocks else "-- Nothing to fix.") + "\n\n-- Review before running.\n"
    if clean:
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("--")) + "\n"
    if n_reb:
        # /* */ (not --) so it is kept even with the clean option.
        text += ("\n/* Follow-up: open rebilling activities created by this fix "
                 f"(DESCRIPTION = '{REB_DESCRIPTION}', not Rejected/Rebilled)\n"
                 + build_followup_query() + "\n*/\n")
    return {"sql_text": text, "itb_count": n_itb, "reading_count": n_read, "rebilling_count": n_reb,
            "rebilling_bill_count": n_info, "sanitary_kept_count": n_kept, "warnings": warnings}


def build_double_itb_query() -> str:
    billed = format_sql_literal(ITB_STATUS_BILLED)
    anomalous = format_sql_literal(ITB_STATUS_ANOMALOUS)
    return f"""
SELECT
    ss.NISS AS NISS,
    pf.REFERENCE AS ACCOUNT,
    os.NAME_TYPE AS OFFERED_SERVICE,
    a.ID_BILLING_SERVICE AS ID_BILLING_SERVICE,
    a.ID_BILLING_PERIOD AS ID_BILLING_PERIOD,
    COALESCE(dict_bp.text, bp.DESCRIPTION) AS BILLING_PERIOD,
    a.INI_DATE AS INI_DATE,
    a.END_DATE AS END_DATE,
    a.ID_ITEM_TO_BILL AS ANOM_ID_ITEM_TO_BILL,
    a.STATUS AS ANOM_STATUS,
    a.BILLING_DATE AS ANOM_BILLING_DATE,
    a.CREATION_DATE AS ANOM_CREATION_DATE,
    b.ID_ITEM_TO_BILL AS BILLED_ID_ITEM_TO_BILL,
    b.STATUS AS BILLED_STATUS,
    b.BILLING_DATE AS BILLED_BILLING_DATE,
    b.ID_BILL AS BILLED_ID_BILL,
    b.ID_BILLING_PERIOD AS BILLED_ID_BILLING_PERIOD,
    ru.READY_USAGE AS ANOM_READY_USAGE,
    ru.READING_COUNT AS ANOM_READING_COUNT,
    CASE WHEN ISNULL(ru.READY_USAGE, 0) <> 0 THEN 1 ELSE 0 END AS NEEDS_REBILLING,
    san.SAN_ANOM_IDS AS SAN_ANOM_ID_ITEM_TO_BILL,
    rtp.READING_TYPES AS READING_TYPES
FROM GCCOM_ITEMS_TO_BILL a
JOIN GCCOM_ITEMS_TO_BILL b ON b.ID_BILLING_SERVICE = a.ID_BILLING_SERVICE
    AND b.STATUS = {billed}
    AND b.INI_DATE = a.INI_DATE
    AND b.END_DATE = a.END_DATE
    AND b.ID_ITEM_TO_BILL <> a.ID_ITEM_TO_BILL
JOIN GCCOM_BILLING_SERVICE bs ON bs.ID_BILLING_SERVICE = a.ID_BILLING_SERVICE
JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
LEFT JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
LEFT JOIN {OFFERED_SERVICE_TABLE} os ON os.ID_OFFERED_SERVICE = cs.ID_OFFERED_SERVICE
LEFT JOIN GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = a.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY dict_bp ON dict_bp.id = bp.PERIOD_NAME_XI18N AND dict_bp.locale = 'EN'
OUTER APPLY (
    SELECT SUM(rit.READY_USAGE) AS READY_USAGE, COUNT(*) AS READING_COUNT
    FROM {READINGS_ITEMS_TO_BILL_TABLE} rit
    WHERE rit.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL
) ru
OUTER APPLY (
    -- Water only: the Sanitary duplicate(s) sharing the anomalous item's reading
    SELECT STRING_AGG(CAST(x.ID_ITEM_TO_BILL AS varchar(20)), ',') AS SAN_ANOM_IDS FROM (
      SELECT DISTINCT s.ID_ITEM_TO_BILL
      FROM {READINGS_ITEMS_TO_BILL_TABLE} r1
      JOIN {READINGS_ITEMS_TO_BILL_TABLE} r2 ON r2.ID_READING = r1.ID_READING AND r2.ID_ITEM_TO_BILL <> r1.ID_ITEM_TO_BILL
      JOIN GCCOM_ITEMS_TO_BILL s ON s.ID_ITEM_TO_BILL = r2.ID_ITEM_TO_BILL AND s.STATUS = {anomalous}
      JOIN GCCOM_BILLING_SERVICE sbs ON sbs.ID_BILLING_SERVICE = s.ID_BILLING_SERVICE
      JOIN GCCOM_CONTRACTED_SERVICE scs ON scs.ID_CONTRACTED_SERVICE = sbs.ID_CONTRACTED_SERVICE AND scs.ID_OFFERED_SERVICE = {OFFERED_SANITARY}
      WHERE r1.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL AND cs.ID_OFFERED_SERVICE = {OFFERED_WATER}) x
) san
OUTER APPLY (
    -- RJ 2026-10-06: reading type(s) sent to bill on the anomalous item
    -- (Cycle / Removal TIPTL00002 / Disconnection TIPTL00010 / Direct Connection / Distribution)
    SELECT STRING_AGG(y.RT, ', ') AS READING_TYPES FROM (
      SELECT DISTINCT COALESCE(drt.TEXT, rt.DESCRIPTION, rd.READING_TYPE) AS RT
      FROM {READINGS_ITEMS_TO_BILL_TABLE} rit2
      JOIN OUC_COMMON_ADMIN.GCGT_RE_READING rd ON rd.ID_READING = rit2.ID_READING
      LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING_TYPE rt ON rt.COD_DEVELOP = rd.READING_TYPE
      LEFT JOIN GCTS_DICTIONARY drt ON drt.ID = rt.DESCRIPTION_XI18N AND drt.LOCALE = 'EN'
      WHERE rit2.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL) y
) rtp
WHERE a.STATUS = {anomalous}
  -- RJ 2026-10-06: "the main query should only pick water and electricity"
  AND cs.ID_OFFERED_SERVICE IN ({OFFERED_ELECTRICITY}, {OFFERED_WATER})
ORDER BY a.ID_BILLING_PERIOD DESC, ss.NISS
"""
