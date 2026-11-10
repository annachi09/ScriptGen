/* =====================================================================
   ScriptGen - FIX BILLING CAMPAIGN 2026: idle background refresh (RJ 2026-11-08).
   RJ: "also try to have a cache of the last data loaded, and try to slowly load the data at the
   background when in rest mode".
   When nobody has touched the app for IDLE_MS (mouse / keys / scroll), the data sets of the FIX
   BILLING tabs whose server cache is older than STALE_S are re-run ONE AT A TIME (never while
   another request is running - RJ's "one query at a time" rule) with cache:"refresh", so the next
   time a tab is opened its "prefer" load is instant and recent. Nothing on screen is replaced
   while you work: when a tab you have open got newer data, a "🔄 newer data ... - show" link
   appears next to its summary.
   Loaded after app.js (uses $, _apiInflight).
   ===================================================================== */
(function () {
  const IDLE_MS = 90 * 1000;        // rest mode = no activity for 90 s
  const CHECK_MS = 20 * 1000;       // look every 20 s
  const STALE_S = 15 * 60;          // refresh data sets older than 15 min
  let lastActivity = Date.now(), running = false, stopped = false;
  ["mousemove", "mousedown", "keydown", "wheel", "touchstart", "scroll"].forEach((ev) =>
    window.addEventListener(ev, () => { lastActivity = Date.now(); }, { passive: true, capture: true }));

  const val = (sel, dflt) => { const el = document.querySelector(sel); return (el && el.value) || dflt; };
  const JOBS = [
    { kind: "detectfb", url: "/api/fix-billing-campaign/detect", sub: "campaign", btn: "#fbc-detect-btn", summary: "#fbc-summary",
      body: () => ({ scope: "all", since: val("#fbc-since", "2026-10-16"), cache: "refresh" }) },
    { kind: "planned", url: "/api/fix-billing-campaign/subsidy-planned", sub: "planned", btn: "#fsp-detect-btn", summary: "#fsp-summary",
      body: () => ({ billing_period: Number(val("#fsp-bp", "10000000237")) || 10000000237, cache: "refresh" }) },
    { kind: "bfa", url: "/api/fix-billing-campaign/bfa-categories", sub: "bfacat", btn: "#fbk-detect-btn", summary: "#fbk-summary",
      body: () => ({ billing_period: Number(val("#fbk-bp", "10000000239")), since: val("#fbk-since", "2026-10-16"),
                     include_all_others: !!document.querySelector("#fbk-all-others")?.checked, cache: "refresh" }) },
  ];
  const nextJobIdx = { i: 0 };

  async function staleKinds() {
    const r = await fetch("/api/fix-billing-campaign/cache-status", { credentials: "same-origin" });
    if (r.status === 401 || r.status === 403) { stopped = true; return []; }
    if (!r.ok) return [];
    const items = (await r.json()).items || [];
    const newest = {};
    items.forEach((it) => { newest[it.kind] = Math.min(newest[it.kind] ?? Infinity, it.age_seconds); });
    return JOBS.filter((j) => !(j.kind in newest) || newest[j.kind] > STALE_S).map((j) => j.kind);
  }

  function markNewer(job) {
    const sum = document.querySelector(job.summary);
    if (!sum) return;
    let tag = sum.parentElement.querySelector(`[data-fbc-bg="${job.kind}"]`);
    if (!tag) {
      tag = document.createElement("a");
      tag.href = "#"; tag.dataset.fbcBg = job.kind;
      tag.style.cssText = "display:inline-block;margin:4px 0;font-weight:700;color:var(--accent,#4f46e5);";
      tag.addEventListener("click", (e) => {
        e.preventDefault();
        const b = document.querySelector(job.btn);
        if (b && !b.disabled) { b.dataset.prefer = "1"; b.click(); }
        tag.remove();
      });
      sum.insertAdjacentElement("afterend", tag);
    }
    tag.textContent = `🔄 Newer data loaded in the background at ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} - click to show it`;
  }

  async function tick() {
    if (running || stopped) return;
    if (Date.now() - lastActivity < IDLE_MS) return;
    if (typeof _apiInflight !== "undefined" && _apiInflight > 0) return;   // something else is running
    running = true;
    try {
      const stale = await staleKinds();
      if (!stale.length) return;
      // round-robin over the stale ones, one job per tick
      let job = null;
      for (let k = 0; k < JOBS.length; k++) {
        const j = JOBS[(nextJobIdx.i + k) % JOBS.length];
        if (stale.includes(j.kind)) { job = j; nextJobIdx.i = (JOBS.indexOf(j) + 1) % JOBS.length; break; }
      }
      if (!job) return;
      const btn = document.querySelector(job.btn);
      if (btn && btn.disabled) return;   // that tab is loading right now
      const r = await fetch(job.url, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(job.body()) });
      if (r.status === 401 || r.status === 403) { stopped = true; return; }
      if (!r.ok) return;
      await r.arrayBuffer();   // body not needed - the server cache now holds it
      // a tab already showing data gets the "newer data" link (its data is NOT replaced under you)
      const summary = document.querySelector(job.summary);
      if (summary && summary.textContent.trim()) markNewer(job);
    } catch (_) { /* network blip - next tick retries */ }
    finally { running = false; }
  }
  setInterval(tick, CHECK_MS);
  window.fbcIdle = { tick, state: () => ({ idleFor: Date.now() - lastActivity, running, stopped }) };
})();
