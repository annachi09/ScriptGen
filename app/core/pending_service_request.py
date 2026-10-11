"""
Pending Service Request (RJ, 2026-10-09).

RJ's query, kept as given (menu + Overview card): service requests whose
responsible entity belongs to employee 10000000620 or 10000008456, that have a
workflow activity comment mentioning JIRA, and whose status is not one of
SREQSTA207 / SREQSTA003 / SREQSTA004 / SREQSTA203. Status in English from
GCTS_DICTIONARY; the first JIRA comment is shown.

Additive columns only (same rows): CREATION_SR_DATE, age in days, the status
code, SERVREQ_TYPE, the responsible employee. The Jira number is parsed from
the comment client-side. Live 2026-10-09: 33 rows, ~2.5s.

Pure logic: builds SELECT text only.
"""
from __future__ import annotations

EMPLOYEES = (10000000620, 10000008456)
EXCLUDED_STATUSES = ("SREQSTA207", "SREQSTA003", "SREQSTA004", "SREQSTA203")


def build_query() -> str:
    emps = ", ".join(str(int(e)) for e in EMPLOYEES)
    excl = ", ".join(f"'{s}'" for s in EXCLUDED_STATUSES)
    return f"""
SELECT SR.SR_CODE,
       d.TEXT AS STATUS,
       (SELECT TOP 1 COMMENTS FROM GCGT_WF_PACTIVITY
         WHERE ID_OBJECT = SR.ID_SERVREQ AND COMMENTS LIKE '%JIRA%') AS JIRA_COMMENT,
       SR.SERVREQ_STATUS,
       SR.SERVREQ_TYPE,
       SR.CREATION_SR_DATE,
       DATEDIFF(DAY, SR.CREATION_SR_DATE, GETDATE()) AS AGE_DAYS,
       SR.ID_SERVREQ,
       ER.ID_EMPLOYEE AS RESPONSIBLE_EMPLOYEE
FROM OUC_COMMON_ADMIN.GCCOM_SERVICE_REQUEST AS SR
     INNER JOIN OUCEWA.OUC_ADMIN.GCGT_WF_ENTITY_RESPONSIBLE AS ER ON ER.ID_ENTITY_RESPONSIBLE = SR.ID_RESPONSIBLE
     INNER JOIN OUCEWA.OUC_ADMIN.GCCOM_SERVREQUEST_STATUS AS SRS ON SRS.COD_DEVELOP = SR.SERVREQ_STATUS
     INNER JOIN GCTS_DICTIONARY d ON d.ID = SRS.NAME_TYPE_XI18N AND d.LOCALE = 'EN'
WHERE ER.ID_EMPLOYEE IN ({emps})
  AND EXISTS (SELECT 1 FROM GCGT_WF_PACTIVITY WHERE ID_OBJECT = SR.ID_SERVREQ AND COMMENTS LIKE '%JIRA%')
  AND SR.SERVREQ_STATUS NOT IN ({excl})
ORDER BY SR.CREATION_SR_DATE
"""
