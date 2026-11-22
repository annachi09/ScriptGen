/* =====================================================================
   ScriptGen - Settings page (RJ 2026-11-19): "can you do the ssh tunnel inside of you, add also the settings of
   ssh in the settings, group all settings in one menu in nice way and modern".
   - Status strip (Database / SSH tunnel / AI / E-mail) - click jumps to the section.
   - Left section nav; one panel visible at a time; admin-only sections hidden for non-admins.
   - SSH tunnel panel: GET/POST /api/settings/ssh, POST /api/settings/ssh/test, /api/ssh/start|stop|status.
   Loaded after app.js (uses $, api, showToast, escapeHtml).
   ===================================================================== */
(function () {
  const page = document.getElementById("page-settings");
  if (!page) return;
  const el = (id) => document.getElementById(id);
  let current = "account";
  try { current = localStorage.getItem("scriptgen_settings_tab") || "account"; } catch (_) { /* ignore */ }

  const isAdmin = () => !el("admin-settings-section")?.hidden;

  function show(key) {
    if (!isAdmin() && key !== "account") key = "account";
    current = key;
    try { localStorage.setItem("scriptgen_settings_tab", key); } catch (_) { /* ignore */ }
    page.querySelectorAll(".set-nav-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.set === key));
    page.querySelectorAll(".set-panel").forEach((p) => p.classList.toggle("is-active", p.dataset.set === key));
    if (key === "ssh") loadSsh();
  }

  function applyAdmin() {
    const admin = isAdmin();
    page.querySelectorAll("[data-admin]").forEach((n) => { n.hidden = !admin; });
    el("set-status").hidden = !admin;
    show(current);
  }

  page.addEventListener("click", (e) => {
    const b = e.target.closest(".set-nav-btn, .set-stat"); if (!b) return;
    show(b.dataset.set || b.dataset.go);
  });
  new MutationObserver(applyAdmin).observe(el("admin-settings-section"), { attributes: true, attributeFilter: ["hidden"] });

  // ---------------- status strip ----------------
  const dot = (id, tone, txt) => { const d = el(id); if (d) d.dataset.tone = tone; const t = el(`${id}-txt`); if (t) t.textContent = txt; };
  async function refreshStatus() {
    if (!isAdmin() || !page.classList.contains("is-active")) return;
    try {
      const s = await api("/api/connection/status");
      dot("set-st-db", s.connected ? "ok" : "bad", s.connected ? `Connected · ${Math.round(s.elapsed_ms)} ms` : (s.message || "Not connected"));
    } catch (_) { dot("set-st-db", "bad", "Status check failed"); }
    try {
      const t = await api("/api/ssh/status");
      paintTunnel(t);
      dot("set-st-ssh", !t.enabled ? "off" : ["connected", "external"].includes(t.state) ? "ok" : t.state === "stopped" ? "off" : ["error", "external_down"].includes(t.state) ? "bad" : "warn",
        !t.enabled ? "Not used (external tunnel)" : t.state === "connected" ? `Built-in · ${t.channels || 0} open` : t.state === "external" ? "External tunnel in use" : t.state.replace("_", " "));
    } catch (_) { dot("set-st-ssh", "off", "-"); }
    try {
      const a = await api("/api/config/ai");
      dot("set-st-ai", a.has_key && a.enabled ? "ok" : "off", a.has_key ? (a.enabled ? `On · ${a.model}` : "Key saved, disabled") : "No key");
    } catch (_) { dot("set-st-ai", "off", "-"); }
    try {
      const m = await api("/api/settings/email");
      const ok = m.smtp_host && (m.recipients || []).length;
      dot("set-st-em", ok ? (m.alerts_enabled ? "ok" : "off") : "off", ok ? (m.alerts_enabled ? `Daily ${String(m.send_hour).padStart(2, "0")}:00 · ${m.recipients.length} recipient(s)` : "Configured, disabled") : "Not configured");
    } catch (_) { dot("set-st-em", "off", "-"); }
  }

  // ---------------- SSH tunnel ----------------
  function paintTunnel(t) {
    const pill = el("ssh-pill"); if (!pill) return;
    const st = t.state || "stopped";
    pill.textContent = { connected: "● Connected", connecting: "Connecting…", reconnecting: "Reconnecting…", retrying: "Retrying…",
      external: "● Using external tunnel", external_down: "External tunnel not answering", error: "Error", stopped: "Stopped" }[st] || st;
    pill.dataset.state = st === "external" ? "connected" : st === "external_down" ? "error" : st;
    el("ssh-status-line").textContent = [t.note || "", t.since ? `Since ${String(t.since).replace("T", " ")}` : "", t.local ? `${t.local} → ${t.ssh} → ${t.remote}` : "",
      t.reconnects ? `${t.reconnects} reconnect(s)` : "", t.last_error ? `Last message: ${t.last_error}` : ""].filter(Boolean).join(" · ");
  }
  function authToggle() {
    const key = el("ssh-auth").value === "key";
    page.querySelectorAll("[data-ssh-auth]").forEach((n) => { n.hidden = (n.dataset.sshAuth === "key") !== key; });
  }
  function diagram() {
    const lp = el("ssh-local-port").value || "1433";
    ["ssh-lp-echo", "ssh-lp-echo2", "ssh-d-lp"].forEach((id) => { if (el(id)) el(id).textContent = lp; });
    el("ssh-d-ssh").textContent = `${el("ssh-host").value || "host"}:${el("ssh-port").value || 22}`;
    el("ssh-d-db").textContent = `${el("ssh-remote-host").value || "host"}:${el("ssh-remote-port").value || 1433}`;
  }
  function body() {
    return {
      enabled: el("ssh-enabled").checked, auto_start: el("ssh-autostart").checked,
      ssh_host: el("ssh-host").value.trim(), ssh_port: Number(el("ssh-port").value) || 22, ssh_user: el("ssh-user").value.trim(),
      auth: el("ssh-auth").value, password: el("ssh-password").value || null,
      key_path: el("ssh-key-path").value.trim(), key_passphrase: el("ssh-key-pass").value || null,
      remote_host: el("ssh-remote-host").value.trim(), remote_port: Number(el("ssh-remote-port").value) || 1433,
      local_port: Number(el("ssh-local-port").value) || 1433, keepalive_seconds: Number(el("ssh-keepalive").value) || 30,
    };
  }
  async function loadSsh() {
    try {
      const s = await api("/api/settings/ssh");
      el("ssh-enabled").checked = !!s.enabled; el("ssh-autostart").checked = !!s.auto_start;
      el("ssh-host").value = s.ssh_host || ""; el("ssh-port").value = s.ssh_port || 22; el("ssh-user").value = s.ssh_user || "";
      el("ssh-auth").value = s.auth || "password"; el("ssh-key-path").value = s.key_path || "";
      el("ssh-password").value = ""; el("ssh-password").placeholder = s.has_password ? "(leave blank to keep existing)" : "(no password saved)";
      el("ssh-key-pass").value = ""; el("ssh-key-pass").placeholder = s.has_key_passphrase ? "(leave blank to keep existing)" : "(blank = none)";
      el("ssh-remote-host").value = s.remote_host || ""; el("ssh-remote-port").value = s.remote_port || 1433;
      el("ssh-local-port").value = s.local_port || 1433; el("ssh-keepalive").value = s.keepalive_seconds || 30;
      authToggle(); diagram(); paintTunnel(s.status || {});
    } catch (err) { el("ssh-result").textContent = `⚠ ${err.message}`; }
  }
  async function run(btn, fn) {
    btn.disabled = true;
    try { await fn(); } catch (err) { el("ssh-result").textContent = `⚠ ${err.message}`; showToast(err.message, true); }
    finally { btn.disabled = false; refreshStatus(); }
  }
  function bindSsh() {
    if (!el("ssh-save-btn")) return;
    el("ssh-auth").addEventListener("change", authToggle);
    ["ssh-host", "ssh-port", "ssh-remote-host", "ssh-remote-port", "ssh-local-port"].forEach((id) => el(id).addEventListener("input", diagram));
    el("ssh-save-btn").addEventListener("click", () => run(el("ssh-save-btn"), async () => {
      await api("/api/settings/ssh", { method: "POST", body: body() });
      el("ssh-result").textContent = "✓ Saved."; showToast("SSH tunnel settings saved.");
      await loadSsh();
    }));
    el("ssh-test-btn").addEventListener("click", () => run(el("ssh-test-btn"), async () => {
      el("ssh-result").textContent = "Testing…";
      const r = await api("/api/settings/ssh/test", { method: "POST", body: body() });
      el("ssh-result").textContent = `✓ ${r.message} (${r.ms} ms)`;
    }));
    el("ssh-start-btn").addEventListener("click", () => run(el("ssh-start-btn"), async () => {
      el("ssh-result").textContent = "Starting…";
      const t = await api("/api/ssh/start", { method: "POST" });
      paintTunnel(t); el("ssh-result").textContent = "✓ Tunnel started."; showToast("SSH tunnel started.");
      if (typeof refreshConnectionStatus === "function") refreshConnectionStatus();
    }));
    el("ssh-stop-btn").addEventListener("click", () => run(el("ssh-stop-btn"), async () => {
      const t = await api("/api/ssh/stop", { method: "POST" });
      paintTunnel(t); el("ssh-result").textContent = "Tunnel stopped.";
    }));
  }

  bindSsh();
  applyAdmin();
  // refresh the strip when the page opens and every 20 s while it is open
  document.addEventListener("click", (e) => { if (e.target.closest('.nav-item[data-page="settings"]')) setTimeout(refreshStatus, 200); });
  setInterval(refreshStatus, 20000);
  setTimeout(refreshStatus, 1500);
})();
