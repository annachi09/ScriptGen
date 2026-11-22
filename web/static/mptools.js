/* =====================================================================
   ScriptGen - MP Tools (RJ 2026-11-18). Tab 1 "TMP Activate MP": MP ids / NISS -> MPs with their
   open status-hist row and readings in Calculated usage (4000STSRED). Script: readings -> Available
   (1000STSRED) + IND_USAGE_TO_CAL = 1; MP 3000STAMPO -> 1000STAMPO; open hist 3000STAMPO -> 1000STAMPO.
   Revert: MP + MP status hist only. Data: POST /api/mp-tools/tmp-activate/detect + /generate.
   ===================================================================== */
(function () {
  const S = { data: null, mps: [], sel: new Set(), inputs: "" };
  const MP_LABEL = { "0000STAMPO": "Non-existent", "1000STAMPO": "Connected", "2000STAMPO": "Disconnected", "3000STAMPO": "Inactive" };
  const esc = (s) => escapeHtml(String(s ?? ""));
  const fmt = (n) => Number(n || 0).toLocaleString();
  const el = (id) => document.getElementById(id);
  const day = (s) => String(s || "").slice(0, 10);
  const pill = (code) => `<span style="font-weight:700;color:${code === "3000STAMPO" ? "#ef4444" : code === "1000STAMPO" ? "#16a34a" : "#f59e0b"};">${esc(MP_LABEL[code] || code || "-")}</span> <small class="hint-text">${esc(code || "")}</small>`;

  document.querySelectorAll(".da-subnav-btn[data-mpt-sub]").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".da-subnav-btn[data-mpt-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    document.querySelectorAll(".da-subpage[data-mpt-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.mptSub === btn.dataset.mptSub));
  }));

  // one entry per MP (detect rows = MP x reading)
  function group(rows) {
    const m = new Map();
    rows.forEach((r) => {
      const k = String(r.id_measuring_point);
      if (!m.has(k)) m.set(k, { ...r, readings: [] });
      if (r.id_reading) m.get(k).readings.push(r);
    });
    return [...m.values()];
  }

  function renderKpis() {
    const d = S.data; if (!d) { el("mpt-kpi-row").innerHTML = ""; return; }
    const card = (icon, label, val, tip, color) => `<div class="kpi-card" style="--k:${color}" title="${esc(tip)}">` +
      `<div class="kpi-value">${val}</div><div class="kpi-label"><span class="kpi-card-icon">${icon}</span>${esc(label)}</div></div>`;
    el("mpt-kpi-row").innerHTML =
      card("🔌", "Measuring points", fmt(d.mp_count), "MPs found for the inputs", "#6366f1") +
      card("⛔", "MP Inactive", fmt(d.inactive_mps), "MP STATUS 3000STAMPO - will become 1000STAMPO", "#ef4444") +
      card("📜", "Open hist Inactive", fmt(d.inactive_hists), "GCGT_RE_MP_STATUS_HIST END_DATE null, 3000STAMPO", "#f59e0b") +
      card("📖", "Calculated-usage readings", fmt(d.calc_readings), "4000STSRED - will become Available 1000STSRED, IND_USAGE_TO_CAL = 1", "#0ea5e9") +
      // RJ 2026-11-18: Available readings that still have IND_USAGE_TO_CAL <> 1
      card("✅", "Available, usage-to-calc ≠ 1", fmt(d.avail_readings || 0), "1000STSRED - IND_USAGE_TO_CAL will be set to 1", "#8b5cf6") +
      card("☑", "Selected MPs", fmt(S.sel.size), "MPs that go in the script", "#16a34a");
  }

  function renderTable() {
    const tb = document.querySelector("#mpt-table tbody");
    tb.innerHTML = S.mps.map((m) => {
      const id = String(m.id_measuring_point);
      const rd = m.readings.map((r) => `<div>${esc(r.id_reading)} <small class="hint-text">${r.read_status === "1000STSRED" ? "Available" : "Calculated usage"} · ${esc(day(r.reading_date))} · period ${esc(r.id_billing_period)} · IND_USAGE_TO_CAL ${esc(r.ind_usage_to_cal ?? "null")}</small></div>`).join("")
        || `<span class="hint-text">none in Calculated usage / Available with IND_USAGE_TO_CAL ≠ 1</span>`;
      return `<tr style="box-shadow: inset 4px 0 0 ${m.mp_status === "3000STAMPO" ? "#ef4444" : "#94a3b8"};">
        <td><input type="checkbox" data-mpt-id="${esc(id)}" ${S.sel.has(id) ? "checked" : ""} /></td>
        <td>${esc(m.niss)}</td><td><b>${esc(id)}</b></td><td>${esc(m.mp_type)}${m.mp_type === "TIPEQM0002" && m.readings.length ? `<br><small style="color:#8b5cf6;font-weight:600;">Secondary → temporarily Normal (main MP ${esc(m.id_main_mp)})</small>` : ""}</td><td>${pill(m.mp_status)}</td>
        <td>${m.id_mp_status_hist ? `${esc(m.id_mp_status_hist)} · ${pill(m.hist_status)}<br><small class="hint-text">since ${esc(day(m.hist_initial_date))}</small>` : `<span class="hint-text">no open row</span>`}</td>
        <td>${rd}</td></tr>`;
    }).join("") || `<tr><td colspan="7" class="hint-text">${S.data ? "No measuring point found." : "Enter an MP id or NISS and press Enter / Scan."}</td></tr>`;
    el("mpt-gen-btn").disabled = !S.sel.size;
    el("mpt-gen-btn").textContent = `🛠 Generate script (${fmt(S.sel.size)} MP)`;
    el("mpt-check-all").checked = S.mps.length > 0 && S.mps.every((m) => S.sel.has(String(m.id_measuring_point)));
    renderKpis();
  }

  // RJ 2026-11-18: mode "all" = every Inactive MP with an Available / Calculated-usage reading (date window)
  async function scan(mode) {
    const all = mode === "all";
    const inputs = all ? "" : ((el("mpt-inputs").value || "").trim() || S.inputs);
    if (!all && !inputs) { showToast("Enter an ID_MEASURING_POINT or a NISS.", true); return; }
    const btn = el(all ? "mpt-all-btn" : "mpt-detect-btn"); if (btn.disabled) return;
    const since = Number(el("mpt-all-since")?.value || 6);
    btn.disabled = true; el("mpt-summary").textContent = all ? "Scanning every Inactive MP…" : "Scanning…"; el("mpt-out-wrap").hidden = true;
    try {
      const d = await api("/api/mp-tools/tmp-activate/detect", { method: "POST",
        body: all ? { mode: "all", since_months: since } : { inputs } });
      S.data = d; S.inputs = inputs; S.mps = group(d.rows || []);
      if (all) {
        el("mpt-summary").dataset.prefix = `Detect all - Inactive MPs with an Available / Calculated-usage reading ` +
          `(${since ? `readings of the last ${since} months` : "any reading date"})${d.capped ? " - first 3,000 only" : ""} · `;
      } else el("mpt-summary").dataset.prefix = "";
      // pre-select MPs that have something to activate
      S.sel = new Set(S.mps.filter((m) => m.mp_status === "3000STAMPO" || m.hist_status === "3000STAMPO" || m.readings.length).map((m) => String(m.id_measuring_point)));
      el("mpt-summary").innerHTML = `${esc(el("mpt-summary").dataset.prefix || "")}${fmt(d.mp_count)} measuring point(s) · ${fmt(d.calc_readings)} reading(s) in Calculated usage · ${fmt(d.avail_readings || 0)} Available with IND_USAGE_TO_CAL ≠ 1 · ${(d.elapsed_ms / 1000).toFixed(1)}s · read only` +
        (d.missing && d.missing.length ? ` · <span style="color:#f59e0b;">not found: ${esc(d.missing.join(", "))}</span>` : "");
      renderTable();
    } catch (err) {
      el("mpt-summary").textContent = `⚠ ${err.message}`; showToast(err.message, true);
    } finally { btn.disabled = false; }
  }

  async function generate() {
    if (!S.sel.size) return;
    const btn = el("mpt-gen-btn"); btn.disabled = true;
    el("mpt-out-wrap").hidden = false; el("mpt-out").textContent = "Re-checking live and generating…"; el("mpt-rb-out").textContent = "";
    try {
      const d = await api("/api/mp-tools/tmp-activate/generate", { method: "POST", body: {
        inputs: S.inputs, mp_ids: [...S.sel], program: el("mpt-program").value.trim() || "TMP_ACTIVATE_MP",
        audit_user: el("mpt-user").value.trim() || "RMA", clean: el("mpt-clean").checked } });
      el("mpt-out").textContent = d.sql; el("mpt-rb-out").textContent = d.rollback_sql;
      el("mpt-out-info").textContent = `${fmt(d.readings)} reading(s) → Available, ${fmt(d.available_readings || 0)} Available reading(s) → IND_USAGE_TO_CAL = 1, ${fmt(d.mps)} MP(s) → Connected, ${fmt(d.secondary_to_normal || 0)} secondary MP(s) → temporarily Normal, ${fmt(d.hists)} status-hist row(s) → 1000STAMPO · saved in History`;
    } catch (err) {
      el("mpt-out").textContent = `⚠ ${err.message}`; showToast(err.message, true);
    } finally { btn.disabled = !S.sel.size; }
  }

  function download(text, name) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text || ""], { type: "text/plain" }));
    a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function bind() {
    if (!el("mpt-detect-btn")) return;
    el("mpt-detect-btn").addEventListener("click", () => scan());
    el("mpt-all-btn")?.addEventListener("click", () => scan("all"));
    el("mpt-table").addEventListener("change", (e) => {
      const cb = e.target.closest("[data-mpt-id]"); if (!cb) return;
      if (cb.checked) S.sel.add(cb.dataset.mptId); else S.sel.delete(cb.dataset.mptId);
      renderTable();
    });
    el("mpt-check-all").addEventListener("change", (e) => {
      S.mps.forEach((m) => { const id = String(m.id_measuring_point); if (e.target.checked) S.sel.add(id); else S.sel.delete(id); });
      renderTable();
    });
    el("mpt-gen-btn").addEventListener("click", generate);
    const prog = () => (el("mpt-program").value.trim() || "TMP_ACTIVATE_MP");
    el("mpt-copy-btn").addEventListener("click", () => { navigator.clipboard.writeText(el("mpt-out").textContent || ""); showToast("Script copied"); });
    el("mpt-rb-copy-btn").addEventListener("click", () => { navigator.clipboard.writeText(el("mpt-rb-out").textContent || ""); showToast("Revert copied"); });
    el("mpt-download-btn").addEventListener("click", () => download(el("mpt-out").textContent, `${prog()}_tmp_activate_mp.sql`));
    el("mpt-rb-download-btn").addEventListener("click", () => download(el("mpt-rb-out").textContent, `${prog()}_tmp_activate_mp_REVERT.sql`));
    renderTable();
  }
  bind();
  window.mpTools = { scan, state: S };
})();
