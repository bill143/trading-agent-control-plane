// Trading System desktop UI. Talks only to the local control API (same origin, HTTPS).
// The session token lives in memory (and sessionStorage for reloads); it never touches disk.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = { token: sessionStorage.getItem("tacp.token") || "", me: null, last: null, view: "dashboard", timer: null };

// ---------- API ----------
async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 401 && path !== "/api/auth/login") { signedOut("Your session ended. Please sign in again."); throw new Error("signed out"); }
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) throw new Error(typeof data === "object" && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `HTTP ${res.status}`);
  return data;
}

function toast(msg, isError = false) {
  const el = document.createElement("div");
  el.textContent = msg;
  if (isError) el.className = "err";
  $("#toast").append(el);
  setTimeout(() => el.remove(), isError ? 7000 : 3500);
}

function confirmDialog(title, text, { input = null, okLabel = "Confirm", danger = false } = {}) {
  return new Promise((resolve) => {
    const dlg = $("#confirm");
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = text;
    $("#confirm-ok").textContent = okLabel;
    $("#confirm-ok").className = danger ? "kill" : "primary";
    const wrap = $("#confirm-input-label");
    wrap.hidden = !input;
    if (input) { $("#confirm-input-caption").textContent = input; $("#confirm-input").value = ""; }
    dlg.onclose = () => resolve(dlg.returnValue === "ok" ? (input ? $("#confirm-input").value.trim() || null : true) : null);
    dlg.showModal();
  });
}

// ---------- formatting ----------
const money = (v) => (v === null || v === undefined || v === "" ? "—" : `${Number(v) < 0 ? "-" : ""}$${Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const num = (v, d = 2) => (v === null || v === undefined ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const signCls = (v) => (Number(v) > 0 ? "good" : Number(v) < 0 ? "bad" : "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ctTime = (iso) => (iso ? new Date(iso).toLocaleTimeString("en-US", { timeZone: "America/Chicago", hour12: false }) : "—");
const ctDateTime = (iso) => (iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/Chicago", hour12: false, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) + " CT" : "—");
const can = (perm) => !!state.me && state.me.permissions.includes(perm);

// ---------- auth ----------
async function boot() {
  const health = await api("/api/health");
  if (health.setup_required) return showAuth("setup");
  if (!state.token) return showAuth("login");
  try { state.me = await api("/api/auth/me"); enterApp(); } catch { showAuth("login"); }
}

function showAuth(which) {
  $("#app").hidden = true; $("#auth").hidden = false;
  $("#login-form").hidden = which !== "login";
  $("#setup-form").hidden = which !== "setup";
  $("#enroll").hidden = which !== "enroll";
  const first = $(`#${which === "enroll" ? "enroll-done" : which + "-form input"}`);
  if (first) first.focus();
}

function signedOut(msg) {
  state.token = ""; state.me = null; sessionStorage.removeItem("tacp.token");
  clearInterval(state.timer);
  showAuth("login");
  if (msg) $("#login-form .form-error").textContent = msg;
}

$("#setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target), err = $("#setup-form .form-error");
  if (f.get("password") !== f.get("password2")) { err.textContent = "Passwords do not match."; return; }
  try {
    const r = await api("/api/auth/setup", { method: "POST", body: { username: f.get("username"), password: f.get("password") } });
    $("#enroll-qr").innerHTML = r.qr_svg; // server-generated SVG (segno), same origin
    $("#enroll-uri").textContent = r.otpauth_uri;
    $("#login-form [name=username]").value = r.username;
    showAuth("enroll");
  } catch (ex) { err.textContent = ex.message; }
});
$("#enroll-done").addEventListener("click", () => showAuth("login"));

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target), err = $("#login-form .form-error");
  err.textContent = "";
  try {
    const r = await api("/api/auth/login", { method: "POST", body: { username: f.get("username"), password: f.get("password"), code: f.get("code") } });
    state.token = r.token; sessionStorage.setItem("tacp.token", r.token);
    e.target.reset();
    state.me = await api("/api/auth/me");
    enterApp();
  } catch (ex) { err.textContent = ex.message; }
});

$("#logout").addEventListener("click", async () => { try { await api("/api/auth/logout", { method: "POST" }); } catch {} signedOut(); });

function enterApp() {
  $("#auth").hidden = true; $("#app").hidden = false;
  $("#user-chip").textContent = `${state.me.username} · ${state.me.role}`;
  $$(".owner-only").forEach((el) => (el.hidden = !can("manage_users")));
  $("#btn-kill").disabled = !can("kill_switch");
  showView(state.view);
  refresh();
  clearInterval(state.timer);
  state.timer = setInterval(refresh, 1000);
}

// ---------- views ----------
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
function showView(name) {
  state.view = name;
  $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.view === name));
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
  window.scrollTo(0, 0);
  if (name === "backtests") loadBacktests();
  if (name === "settings") loadSettings();
  if (name === "activity") loadAudit();
}

// ---------- dashboard ----------
async function refresh() {
  if (!state.token) return;
  try {
    const s = await api("/api/state");
    state.last = s;
    renderTop(s); renderAccount(s); renderTiles(s); renderPositions(s);
    if (state.view === "dashboard") { renderDecisions(); renderTrades(); }
    if (state.view === "backtests") loadBacktests(true);
  } catch (ex) { if (ex.message !== "signed out") console.warn(ex); }
}

function renderTop(s) {
  const mode = $("#mode-badge");
  mode.textContent = s.mode.toUpperCase(); mode.className = `badge ${s.mode === "demo" ? "demo" : ""}`;
  const st = s.running ? s.progress.state : (s.progress.state === "idle" ? "idle" : "stopped");
  const eb = $("#engine-badge");
  eb.textContent = st; eb.className = `badge ${st === "running" ? "run" : st === "error" ? "stop" : ""}`;
  $("#clock").textContent = s.progress.market_time ? `Market time ${ctDateTime(s.progress.market_time)}` : "";
  $("#sample-banner").hidden = !s.sample_data;
  const ks = s.kill_switch, kb = $("#kill-banner");
  kb.hidden = !ks.tripped;
  if (ks.tripped) {
    const t = ks.trips[0] || {};
    kb.textContent = `KILL SWITCH TRIPPED (${t.source || "manual"}): ${t.reason || ""} — ${ks.flattened ? "all orders cancelled and positions closed" : "flattening…"}. New entries are blocked until the Owner resets it.`;
  }
  const kill = $("#btn-kill");
  kill.textContent = ks.tripped ? (can("reset_kill_switch") ? "RESET KILL SWITCH" : "KILL SWITCH TRIPPED") : "KILL SWITCH";
  kill.disabled = ks.tripped ? !can("reset_kill_switch") : !can("kill_switch");
  const canRun = can("start_stop");
  $("#btn-start").disabled = !canRun || s.running;
  $("#btn-stop").disabled = !canRun || !s.running;
  $("#btn-pause").disabled = !canRun || !s.running;
  $("#btn-pause").textContent = s.progress.state === "paused" ? "Resume" : "Pause";
  $("#speed").disabled = !canRun;
  const p = s.progress;
  if (!s.data_ready && !s.running) { $("#progress").textContent = "Preparing sample data (first start, about 10 seconds)…"; return; }
  $("#progress").textContent = s.running || p.state === "finished" ? `Session ${p.day}/${p.days_total} · ${p.trade_date || ""} · ${p.speed}×` : (p.error ? `Error: ${p.error}` : "Engine stopped");
}

function renderAccount(s) {
  const a = s.account || {};
  const limit = Number(a.daily_loss_limit || 0), pnl = Number(a.pnl_today || 0);
  const used = limit ? Math.min(100, Math.max(0, (-pnl / limit) * 100)) : 0;
  const quotaTotal = a.daily_contract_quota_total ?? "—";
  const cards = [
    ["Equity", money(a.equity), ""],
    ["P&L today", money(a.pnl_today), signCls(a.pnl_today)],
    ["Daily loss used", `${num(used, 0)}% of ${money(limit)}`, used > 80 ? "bad" : "", used, used > 80],
    ["Usable buying power", money(a.usable_after_buffer), ""],
    ["Margin in use", money(a.margin_used), ""],
    ["Open positions", `${a.open_positions ?? 0} / 3`, ""],
    ["Contracts used today", `${a.contracts_used ?? 0} / ${quotaTotal}`, ""],
    ["Orders sent", `${s.orders_submitted}`, ""],
  ];
  $("#account").innerHTML = cards.map(([k, v, cls, meter, hot]) =>
    `<div class="stat"><div class="k">${k}</div><div class="v ${cls}">${v}</div>${meter !== undefined ? `<div class="meter ${hot ? "hot" : ""}"><b style="width:${meter}%"></b></div>` : ""}</div>`).join("");
}

function age(sec) { return sec >= 120 ? `${Math.round(sec / 60)} min` : `${Math.round(sec)} s`; }
function fmtPx(v, p) { return v === null || v === undefined ? "—" : Number(v).toFixed(p); }

function renderTiles(s) {
  if (!s.symbols.length) {
    $("#tiles").innerHTML = `<div class="card mut">Start the engine to see live status for every symbol.</div>`;
    return;
  }
  $("#tiles").innerHTML = s.symbols.map((t) => {
    const p = t.price_precision;
    const lean = t.book_lean === null ? 50 : (Number(t.book_lean) + 1) * 50;
    const pos = t.position > 0 ? "long" : t.position < 0 ? "short" : "";
    const stale = t.data_age_seconds !== null && t.data_age_seconds > 10 && t.session_open;
    return `<article class="tile ${pos}" aria-label="${esc(t.root)}">
      <div class="head"><span class="sym">${esc(t.root)}</span><span class="mut small">${esc(t.symbol)} · ${esc(t.name)}</span>
        <span class="price mono">${fmtPx(t.price, p)}</span></div>
      <div class="row small"><span class="pill ${t.session_open ? "ok" : "idle"}">${t.session_open ? "open" : "closed"}</span>
        ${stale ? `<span class="pill no">no data for ${age(t.data_age_seconds)}</span>` : ""}
        <span class="spacer"></span><span class="mut">bid ${fmtPx(t.bid, p)} · ask ${fmtPx(t.ask, p)} · ${t.spread_ticks ?? "—"} tick</span></div>
      <div title="Book lean: share of top-of-book size on the bid (right) vs the ask (left)">
        <div class="row small mut"><span>sellers</span><span class="spacer"></span><span>book lean ${t.book_lean === null ? "—" : num(t.book_lean, 2)}</span><span class="spacer"></span><span>buyers</span></div>
        <div class="lean"><i style="left:${lean}%"></i></div></div>
      <dl>
        <dt>VWAP</dt><dd>${fmtPx(t.vwap, p)}${t.vwap_band !== null ? ` ± ${fmtPx(t.vwap_band, p)}` : ""}</dd>
        <dt>ATR (1-min, 14)</dt><dd>${fmtPx(t.atr, p)} ${t.atr_ticks !== null ? `(${num(t.atr_ticks, 1)} ticks)` : ""}</dd>
        <dt>Prior day H / L / C</dt><dd>${fmtPx(t.prior_high, p)} / ${fmtPx(t.prior_low, p)} / ${fmtPx(t.prior_close, p)}</dd>
        <dt>Overnight H / L</dt><dd>${fmtPx(t.overnight_high, p)} / ${fmtPx(t.overnight_low, p)}</dd>
        <dt>Session H / L</dt><dd>${fmtPx(t.session_high, p)} / ${fmtPx(t.session_low, p)}</dd>
        <dt>Position</dt><dd class="${signCls(t.position)}">${t.position === 0 ? "flat" : `${t.position > 0 ? "long" : "short"} ${Math.abs(t.position)} @ ${fmtPx(t.avg_price, p)}`}</dd>
        <dt>P&amp;L (open / today)</dt><dd><span class="${signCls(t.unrealized_pnl)}">${money(t.unrealized_pnl)}</span> / <span class="${signCls(t.realized_pnl)}">${money(t.realized_pnl)}</span></dd>
        <dt>Contracts used</dt><dd>${t.contracts_used} / ${t.contracts_quota}</dd>
      </dl></article>`;
  }).join("");
}

function renderPositions(s) {
  const rows = s.symbols.filter((t) => t.position !== 0).map((t) => `<tr><td>${esc(t.symbol)}</td><td class="${signCls(t.position)}">${t.position > 0 ? "Long" : "Short"}</td><td>${Math.abs(t.position)}</td><td>${fmtPx(t.avg_price, t.price_precision)}</td><td>${fmtPx(t.price, t.price_precision)}</td><td class="${signCls(t.unrealized_pnl)}">${money(t.unrealized_pnl)}</td></tr>`);
  $("#positions tbody").innerHTML = rows.join("") || `<tr><td colspan="6" class="mut left">No open positions.</td></tr>`;
}

let decisionsBusy = false;
async function renderDecisions() {
  if (decisionsBusy) return; decisionsBusy = true;
  try {
    const list = await api("/api/decisions?n=60");
    const s = state.last;
    $("#decision-count").textContent = s ? `${s.decisions.approved} sent · ${s.decisions.refused} refused` : "";
    $("#decisions").innerHTML = list.reverse().map((d) => {
      const failed = d.checks.filter((c) => !c.passed);
      return `<li><b class="${d.approved ? "good" : "bad"}">${d.approved ? "SENT" : "REFUSED"}</b> ${esc(d.side.toUpperCase())} ${d.qty} ${esc(d.root)} @ ${esc(d.entry)} · stop ${esc(d.stop)} · target ${esc(d.target)} <span class="mut">${ctTime(d.at)}</span>
        ${failed.length ? `<div class="why">${failed.map((c) => `${esc(c.name)}: ${esc(c.detail)}`).join(" · ")}</div>` : ""}</li>`;
    }).join("") || `<li class="mut">No decisions yet.</li>`;
  } finally { decisionsBusy = false; }
}

async function renderTrades() {
  const list = await api("/api/trades");
  $("#trades tbody").innerHTML = list.slice(-40).reverse().map((t) => `<tr><td>${ctTime(t.closed)}</td><td>${esc(t.symbol)}</td><td>${esc(t.side)}</td><td>${t.entry}</td><td>${t.exit}</td><td class="${signCls(t.pnl)}">${money(t.pnl)}</td></tr>`).join("") || `<tr><td colspan="6" class="mut left">No completed trades yet.</td></tr>`;
}

// ---------- controls ----------
$("#btn-start").addEventListener("click", async () => {
  try { await api("/api/engine/start", { method: "POST", body: { speed: Number($("#speed").value) } }); toast("Engine started"); refresh(); } catch (ex) { toast(ex.message, true); }
});
$("#btn-stop").addEventListener("click", async () => {
  if (!(await confirmDialog("Stop the engine?", "Replay stops. Open demo positions are discarded with the simulated account."))) return;
  try { await api("/api/engine/stop", { method: "POST" }); toast("Engine stopped"); refresh(); } catch (ex) { toast(ex.message, true); }
});
$("#btn-pause").addEventListener("click", async () => {
  const paused = state.last && state.last.progress.state === "paused";
  try { await api(`/api/engine/${paused ? "resume" : "pause"}`, { method: "POST" }); refresh(); } catch (ex) { toast(ex.message, true); }
});
$("#speed").addEventListener("change", async (e) => {
  if (!state.last || !state.last.running) return;
  try { await api("/api/engine/speed", { method: "POST", body: { speed: Number(e.target.value) } }); toast(`Speed ${e.target.value}×`); } catch (ex) { toast(ex.message, true); }
});
$("#btn-kill").addEventListener("click", async () => {
  const tripped = state.last && state.last.kill_switch.tripped;
  if (tripped) {
    const note = await confirmDialog("Reset the kill switch?", "Trading may resume. Record why it is safe.", { input: "Reason for reset", okLabel: "Reset" });
    if (!note) return;
    try { await api("/api/kill/reset", { method: "POST", body: { note } }); toast("Kill switch reset"); refresh(); } catch (ex) { toast(ex.message, true); }
    return;
  }
  const reason = await confirmDialog("Trip the kill switch?", "Cancels every working order and closes every position immediately. New entries stay blocked until the Owner resets it.", { input: "Reason", okLabel: "Trip kill switch", danger: true });
  if (!reason) return;
  try { await api("/api/kill", { method: "POST", body: { reason } }); toast("Kill switch tripped — flattening"); refresh(); } catch (ex) { toast(ex.message, true); }
});

// ---------- backtests ----------
async function loadBacktests(quiet = false) {
  try {
    const r = await api("/api/backtests");
    const st = r.status.state;
    $("#bt-status").textContent = st === "running" ? "Backtest running… (about a minute)" : st === "error" ? `Last run failed: ${r.status.error}` : "";
    $("#bt-run").disabled = st === "running" || !can("run_backtest");
    $("#bt-list tbody").innerHTML = r.runs.map((x) => `<tr><td>${esc(x.run_id)}</td><td>${x.dates.length}</td><td>${x.trades}</td><td class="${signCls(x.net_pnl)}">${money(x.net_pnl)}</td><td>${x.profit_factor === null ? "—" : num(x.profit_factor)}</td><td>${money(x.max_drawdown)}</td><td><button data-run="${esc(x.run_id)}">Open report</button></td></tr>`).join("") || `<tr><td colspan="7" class="mut left">No backtests yet. Press “Run backtest”.</td></tr>`;
    $$("#bt-list [data-run]").forEach((b) => b.addEventListener("click", () => openReport(b.dataset.run)));
  } catch (ex) { if (!quiet) toast(ex.message, true); }
}
async function openReport(id) {
  try {
    const html = await api(`/api/backtests/${encodeURIComponent(id)}/report`);
    $("#bt-report").srcdoc = html;
    $("#bt-report-title").textContent = `Report ${id}`;
    $("#bt-report-card").hidden = false;
    $("#bt-report-card").scrollIntoView({ behavior: "smooth" });
  } catch (ex) { toast(ex.message, true); }
}
$("#bt-close").addEventListener("click", () => { $("#bt-report-card").hidden = true; $("#bt-report").srcdoc = ""; });
$("#bt-run").addEventListener("click", async () => { try { await api("/api/backtests", { method: "POST" }); toast("Backtest started"); loadBacktests(); } catch (ex) { toast(ex.message, true); } });

// ---------- settings ----------
const FIELDS = [
  ["daily_loss_limit", "Daily loss limit ($)"], ["per_trade_loss_limit", "Per-trade loss limit ($)"],
  ["buying_power_buffer", "Buying-power buffer (0.30–0.50)"], ["max_open_positions", "Max open positions (portfolio)"],
  ["max_positions_per_symbol", "Max positions per symbol"], ["max_same_direction", "Max groups in the same direction"],
  ["max_contracts_per_order", "Max contracts per order"], ["daily_contract_quota_total", "Daily contract quota (total)"],
  ["fee_filter_multiple", "Fee filter: target ≥ this × round-trip fees"], ["news_blackout_before_min", "News blackout before (min)"],
  ["news_blackout_after_min", "News blackout after (min)"], ["no_entry_before_close_min", "No entries before close (min)"],
  ["data_stale_seconds", "Kill switch if data silent for (s)"],
];
let settingsLoaded = null;
async function loadSettings() {
  try {
    const r = await api("/api/settings/risk");
    settingsLoaded = r.settings;
    const dis = r.editable ? "" : "disabled";
    $("#settings-lock").textContent = r.editable ? "Owner: editable" : "Locked: Owner only";
    const basic = FIELDS.map(([k, label]) => `<label>${label}<input name="${k}" value="${esc(r.settings[k])}" ${dis}></label>`).join("");
    const quotas = Object.entries(r.settings.daily_contract_quota).map(([root, q]) => `<label>${esc(root)} daily contract quota<input name="quota:${esc(root)}" value="${q}" ${dis}></label>`).join("");
    const targets = Object.entries(r.settings.target_ticks).map(([root, q]) => `<label>${esc(root)} target (ticks)<input name="target:${esc(root)}" value="${q}" ${dis}></label>`).join("");
    $("#risk-form").innerHTML = basic + quotas + targets;
    $("#risk-save").hidden = !r.editable;
    if (can("manage_secrets")) { const s = await api("/api/secrets"); $("#secret-list").innerHTML = s.names.map((n) => `<li>${esc(n)} ✓ stored</li>`).join("") || `<li class="mut">No connections saved yet.</li>`; }
    if (can("manage_users")) loadUsers();
    const roots = (state.last?.symbols || []).map((t) => t.root);
    $("#fault-root").innerHTML = roots.map((r2) => `<option>${esc(r2)}</option>`).join("") || "<option value=''>start the engine first</option>";
  } catch (ex) { toast(ex.message, true); }
}
$("#risk-save").addEventListener("click", async () => {
  const changes = {}, quota = { ...settingsLoaded.daily_contract_quota }, target = { ...settingsLoaded.target_ticks };
  for (const inp of $$("#risk-form input")) {
    const [kind, root] = inp.name.split(":");
    if (kind === "quota") quota[root] = Number(inp.value);
    else if (kind === "target") target[root] = Number(inp.value);
    else if (String(settingsLoaded[inp.name]) !== inp.value) changes[inp.name] = inp.value;
  }
  if (JSON.stringify(quota) !== JSON.stringify(settingsLoaded.daily_contract_quota)) changes.daily_contract_quota = quota;
  if (JSON.stringify(target) !== JSON.stringify(settingsLoaded.target_ticks)) changes.target_ticks = target;
  if (!Object.keys(changes).length) { toast("No changes"); return; }
  if (!(await confirmDialog("Save risk settings?", `Changing: ${Object.keys(changes).join(", ")}. The change is written to the audit log.`))) return;
  try { await api("/api/settings/risk", { method: "PUT", body: changes }); $("#risk-error").textContent = ""; toast("Risk settings saved"); loadSettings(); } catch (ex) { $("#risk-error").textContent = ex.message; }
});
$("#secret-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try { await api(`/api/secrets/${encodeURIComponent(f.get("name"))}`, { method: "PUT", body: { value: f.get("value") } }); e.target.reset(); toast("Saved to the encrypted vault"); loadSettings(); } catch (ex) { toast(ex.message, true); }
});
async function loadUsers() {
  const list = await api("/api/users");
  $("#users tbody").innerHTML = list.map((u) => `<tr><td>${esc(u.username)}</td><td>${esc(u.role)}</td><td>${u.totp_enrolled ? "on" : "off"}</td><td>${u.last_login ? ctDateTime(u.last_login) : "never"}</td><td>${u.disabled ? "disabled" : u.locked ? "locked" : "active"}</td><td>${u.locked ? `<button data-unlock="${esc(u.username)}">Unlock</button>` : ""}</td></tr>`).join("");
  $$("#users [data-unlock]").forEach((b) => b.addEventListener("click", async () => { try { await api(`/api/users/${encodeURIComponent(b.dataset.unlock)}/unlock`, { method: "POST" }); loadUsers(); } catch (ex) { toast(ex.message, true); } }));
}
$("#user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const r = await api("/api/users", { method: "POST", body: { username: f.get("username"), password: f.get("password"), role: f.get("role") } });
    e.target.reset();
    const box = $("#user-enroll");
    box.innerHTML = `<div class="qr">${r.qr_svg}</div><p>Have <b>${esc(r.username)}</b> scan this code with an authenticator app now. It is shown only once.</p>`;
    box.hidden = false;
    loadUsers();
  } catch (ex) { toast(ex.message, true); }
});
async function fault(kind) {
  try { await api("/api/demo/fault", { method: "POST", body: { kind, root: $("#fault-root").value } }); toast(kind === "clear" ? "Simulated faults cleared" : "Fault injected — watch the kill switch"); } catch (ex) { toast(ex.message, true); }
}
$("#fault-data").addEventListener("click", () => fault("data_feed"));
$("#fault-broker").addEventListener("click", () => fault("broker_disconnect"));
$("#fault-clear").addEventListener("click", () => fault("clear"));

// ---------- activity ----------
async function loadAudit() {
  if (!can("manage_users")) { $("#audit tbody").innerHTML = `<tr><td colspan="6" class="mut left">The audit log is visible to the Owner.</td></tr>`; return; }
  try {
    const r = await api("/api/audit?n=300");
    const v = $("#audit-verify");
    v.textContent = `chain verified · ${r.verified_records} records`; v.className = "badge run";
    $("#audit tbody").innerHTML = r.records.map((x) => `<tr><td>${x.seq}</td><td>${esc(x.ts.slice(0, 19).replace("T", " "))}</td><td>${esc(x.category)}</td><td class="left">${esc(x.action)}</td><td class="left">${esc(x.actor)}</td><td class="left small">${esc(JSON.stringify(x.details)).slice(0, 160)}</td></tr>`).join("");
  } catch (ex) { $("#audit-verify").textContent = `chain check failed: ${ex.message}`; $("#audit-verify").className = "badge stop"; }
}

boot().catch((ex) => { document.body.textContent = `Cannot reach the engine: ${ex.message}`; });
