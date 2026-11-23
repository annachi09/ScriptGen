/* =====================================================================
   ScriptGen - Reprocess EFTS (RJ 2026-11-23): "create a new temp menu, reprocess efts ... execute in an ssh
   ... the ssh is the tunnel itself ... can be 1 or many id_efts i need to see the result also".
   Admin only. Find: ID_EFTS_INPUT(s) -> RJ's query builds each payload (base64 message + reference) and the
   curl. Reprocess: ticked rows only, after a confirm; the server re-reads them live and POSTs each one ONCE to
   localhost:7005 on the SSH server (Settings > SSH tunnel login). Results per row + saved in History.
   API: POST /api/efts/find, /api/efts/send, /api/efts/route-check. UI standard: docs/UI_STANDARD.md.
   ===================================================================== */
(function () {
  const S = { rows: [], missing: [], sel: new Set(), results: new Map(), filter: null, lastIds: "" };
  const el = (id) => document.getElementById(id);
  const esc = (s) => escapeHtml(String(s ?? ""));
  const fmt = (n) => Number(n || 0).toLocaleString();
  if (!el("efts-find-btn")) return;

  const fields = () => ({
    receiver: el("efts-receiver").value.trim(), sender: el("efts-sender").value.trim(),
    message_type: el("efts-type").value.trim(), priority: el("efts-priority").value.trim(),
  });

  function resultCell(r) {
    const x = S.results.get(r.id_efts_input);
    if (!x) return `<span class="hint-text">not sent</span>`;
    const tone = x.ok ? "ok" : "bad";
    const label = x.ok ? `Sent · HTTP ${x.status}` : (x.sent ? `Failed · HTTP ${x.status ?? "?"}` : "Not sent");
    return `<span class="sg-pill" data-tone="${tone}" title="${esc(x.via || "")}">${esc(label)}</span>` +
      `<small class="hint-text"> ${esc(x.ms)} ms${x.via ? ` · ${esc(x.via)}` : ""}</small>` +
      (x.body ? `<pre class="efts-resp">${esc(String(x.body).slice(0, 600))}</pre>` : "");
  }

  function visibleRows() {
    const f = S.filter;
    return S.rows.filter((r) => {
      const x = S.results.get(r.id_efts_input);
      if (f === "sent") return x && x.ok;
      if (f === "failed") return x && !x.ok;
      if (f === "empty") return r.empty;
      if (f === "selected") return S.sel.has(r.id_efts_input);
      return true;
    });
  }

  function render() {
    const res = [...S.results.values()];
    sgStd.kpis(el("efts-kpis"), [
      { key: null, label: "Found", value: S.rows.length, tone: "info", sub: S.missing.length ? `${S.missing.length} not found` : "" },
      { key: "selected", label: "Ticked", value: S.sel.size, tone: "neutral" },
      { key: "empty", label: "Empty message", value: S.rows.filter((r) => r.empty).length, tone: "warn" },
      { key: "sent", label: "Sent OK", value: res.filter((x) => x.ok).length, tone: "ok" },
      { key: "failed", label: "Failed", value: res.filter((x) => !x.ok).length, tone: "bad" },
    ], { active: S.filter, onPick: (k) => { S.filter = S.filter === k ? null : k; render(); } });

    const rows = visibleRows();
    el("efts-shown").textContent = `${fmt(rows.length)} of ${fmt(S.rows.length)} rows shown`;
    el("efts-table").querySelector("tbody").innerHTML = rows.map((r) => {
      const id = r.id_efts_input;
      return `<tr>
        <td><input type="checkbox" data-efts-id="${esc(id)}" ${S.sel.has(id) ? "checked" : ""} ${r.empty ? "disabled" : ""} /></td>
        <td><b>${esc(id)}</b></td>
        <td>${esc(r.msg_ref)}</td>
        <td>${fmt(r.message_bytes)} B<br><small class="hint-text">${fmt(r.b64_chars)} base64 chars</small></td>
        <td class="efts-preview" title="${esc(r.preview)}">${r.empty ? `<span class="sg-pill" data-tone="warn">Empty</span>` : esc(r.preview)}</td>
        <td><button type="button" class="btn btn-pill-sm" data-efts-copy="${esc(id)}" title="${esc(r.curl)}">📋 curl</button></td>
        <td>${resultCell(r)}</td></tr>`;
    }).join("") || `<tr><td colspan="7" class="hint-text">${S.rows.length ? "No row matches the filter." : "Enter ID_EFTS_INPUT(s) and press Enter / Find."}</td></tr>`;
    el("efts-check-all").checked = S.rows.some((r) => !r.empty) && S.rows.filter((r) => !r.empty).every((r) => S.sel.has(r.id_efts_input));
    const b = el("efts-send-btn");
    b.disabled = !S.sel.size; b.textContent = `🚀 Reprocess (${fmt(S.sel.size)})`;
    el("efts-export-btn").disabled = !S.rows.length;
  }

  async function find() {
    const ids = (el("efts-ids").value || "").trim();
    if (!ids) { showToast("Enter at least one ID_EFTS_INPUT.", true); return; }
    const btn = el("efts-find-btn"); if (btn.disabled) return;
    btn.disabled = true; el("efts-meta").textContent = "Searching…";
    try {
      const d = await api("/api/efts/find", { method: "POST", body: { ids, ...fields() } });
      S.rows = d.rows || []; S.missing = d.missing || []; S.lastIds = ids; S.results = new Map(); S.filter = null;
      S.sel = new Set(S.rows.filter((r) => !r.empty).map((r) => r.id_efts_input));
      sgStd.scanMeta(el("efts-meta"), { rows: d.count, elapsedMs: d.elapsed_ms,
        note: S.missing.length ? `not found: ${S.missing.join(", ")}` : "" });
      render();
    } catch (err) { el("efts-meta").textContent = `⚠ ${err.message}`; showToast(err.message, true); }
    finally { btn.disabled = false; }
  }

  async function send() {
    const ids = S.rows.map((r) => r.id_efts_input).filter((id) => S.sel.has(id));
    if (!ids.length) return;
    const f = fields();
    if (!confirm(`Re-send ${ids.length} EFTS message(s) to the EFTS service?\n\n` +
      `receiver ${f.receiver} · sender ${f.sender} · type ${f.message_type} · priority ${f.priority}\n` +
      `ids: ${ids.slice(0, 15).join(", ")}${ids.length > 15 ? " …" : ""}\n\nEach message is posted once. This can't be undone.`)) return;
    const btn = el("efts-send-btn"); btn.disabled = true; btn.textContent = `Sending ${ids.length}…`;
    try {
      const d = await api("/api/efts/send", { method: "POST", body: { ids: ids.join(","), confirm_count: ids.length, ...f } });
      (d.results || []).forEach((x) => S.results.set(String(x.id_efts_input), x));
      el("efts-send-info").textContent = `${fmt(d.ok)} sent OK · ${fmt(d.failed)} failed · saved in History`;
      showToast(`${d.ok} sent OK, ${d.failed} failed.`, d.failed > 0);
    } catch (err) { el("efts-send-info").textContent = `⚠ ${err.message}`; showToast(err.message, true); }
    finally { render(); }
  }

  async function routeCheck() {
    const btn = el("efts-route-btn"); btn.disabled = true;
    try {
      const d = await api("/api/efts/route-check", { method: "POST", body: {} });
      el("efts-route-info").innerHTML = d.ok ? `✅ ${esc(d.message)} (${esc(d.ms)} ms)`
        : `⚠ ${esc(d.message)} - Reprocess will try running the curl on the SSH server instead.`;
    } catch (err) { el("efts-route-info").textContent = `⚠ ${err.message}`; }
    finally { btn.disabled = false; }
  }

  function exportCsv() {
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [["ID_EFTS_INPUT", "REFERENCE", "MESSAGE_BYTES", "RESULT", "HTTP_STATUS", "VIA", "RESPONSE", "CURL"].join(",")];
    S.rows.forEach((r) => {
      const x = S.results.get(r.id_efts_input) || {};
      lines.push([r.id_efts_input, r.msg_ref, r.message_bytes, x.ok ? "OK" : (x.sent === undefined ? "NOT SENT" : "FAILED"),
        x.status ?? "", x.via ?? "", x.body ?? "", r.curl].map(q).join(","));
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\r\n")], { type: "text/csv" }));
    a.download = `reprocess_efts_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  el("efts-find-btn").addEventListener("click", find);
  el("efts-send-btn").addEventListener("click", send);
  el("efts-route-btn").addEventListener("click", routeCheck);
  el("efts-export-btn").addEventListener("click", exportCsv);
  el("efts-table").addEventListener("change", (e) => {
    const cb = e.target.closest("[data-efts-id]"); if (!cb) return;
    if (cb.checked) S.sel.add(cb.dataset.eftsId); else S.sel.delete(cb.dataset.eftsId);
    render();
  });
  el("efts-check-all").addEventListener("change", (e) => {
    S.rows.filter((r) => !r.empty).forEach((r) => { if (e.target.checked) S.sel.add(r.id_efts_input); else S.sel.delete(r.id_efts_input); });
    render();
  });
  el("efts-table").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-efts-copy]"); if (!b) return;
    const r = S.rows.find((x) => x.id_efts_input === b.dataset.eftsCopy);
    try { await navigator.clipboard.writeText(r?.curl || ""); showToast("curl copied."); }
    catch (_) { showToast("Couldn't copy - hover the button to see it.", true); }
  });
  render();
  window.eftsReprocess = { find, state: S };
})();
