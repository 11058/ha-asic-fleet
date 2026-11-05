// ASIC Fleet sidebar panel. Plain web component, no build step. Talks to the
// integration over websocket (asic_fleet/overview, /subscribe, /action,
// /set_plug, /rename) and draws with Home Assistant's theme variables.

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Telemetry is in MH/s; show it in the unit that keeps 3 significant digits.
function rate(mh) {
  if (mh === null || mh === undefined) return "—";
  if (mh >= 1e6) return `${(mh / 1e6).toFixed(mh >= 1e8 ? 0 : 1)} TH/s`;
  if (mh >= 1e3) return `${(mh / 1e3).toFixed(mh >= 1e5 ? 0 : 1)} GH/s`;
  return `${mh.toFixed(0)} MH/s`;
}
const kw = (w) => (w === null || w === undefined ? "—" : w >= 1000 ? `${(w / 1000).toFixed(1)} кВт` : `${Math.round(w)} Вт`);
function hours(h) {
  if (h === null || h === undefined) return "—";
  if (h >= 48) return `${Math.floor(h / 24)} д`;
  return `${Math.floor(h)} ч`;
}
const STATUS = {
  ok: ["ok", "работает"], low: ["warn", "низкий хешрейт"], off: ["bad", "не майнит"],
  error: ["bad", "ошибка"], blocked: ["blk", "заблокирован"], unreachable: ["unk", "нет связи"],
};
const PROBLEM = {
  offline: "нет связи", overheat: "перегрев", overheat_critical: "критический перегрев",
  hashrate_low: "низкий хешрейт", hashrate_zero: "нет хешрейта", fan: "вентиляторы",
  chips: "чипы", hw_errors: "аппаратные ошибки", block_desync: "блокировка не применилась",
  ip_changed: "сменился IP", unnamed: "без имени",
};
const ALGO = { sha256: "SHA-256", scrypt: "Scrypt", unknown: "?" };

class AsicFleetPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = { fleets: [], plugs: [] };
    this._place = "*";
    this._onlyProblems = false;
    this._q = "";
    this._open = null; // {entry, mac}
    this._busy = new Set();
    this._confirm = null;
    this._error = "";
  }

  set hass(h) { const first = !this._hass; this._hass = h; if (first) this._start(); }
  set narrow(v) { this._narrow = v; }
  set panel(v) {}
  get _admin() { return !!(this._hass && this._hass.user && this._hass.user.is_admin); }

  async _start() {
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <div class="toolbar"><ha-menu-button></ha-menu-button><div class="title">ASIC</div></div>
      <div id="body" class="body"><div class="muted">Загрузка…</div></div><div id="modal"></div>`;
    const m = this.shadowRoot.querySelector("ha-menu-button");
    if (m) { m.hass = this._hass; m.narrow = this._narrow; }
    await this._load();
    try {
      this._unsub = await this._hass.connection.subscribeMessage(() => {
        clearTimeout(this._deb); this._deb = setTimeout(() => this._load(), 500);
      }, { type: "asic_fleet/subscribe" });
    } catch (e) { /* polling only */ }
    this._timer = setInterval(() => this._load(), 60000);
  }

  disconnectedCallback() { if (this._unsub) this._unsub(); clearInterval(this._timer); this._hass = undefined; }

  async _load() {
    if (!this._hass) return;
    try { this._data = await this._hass.callWS({ type: "asic_fleet/overview" }); this._error = ""; }
    catch (e) { this._error = e.message || String(e); }
    this._render();
  }

  async _call(msg, key) {
    this._busy.add(key); this._render();
    try { await this._hass.callWS(msg); }
    catch (e) { this._error = `${e.message || e}`; }
    this._busy.delete(key);
    await this._load();
  }

  _fleet(entry) { return this._data.fleets.find((f) => f.entry_id === entry); }
  _miner(entry, mac) { const f = this._fleet(entry); return f && f.miners.find((m) => m.mac === mac); }

  _render() {
    const body = this.shadowRoot.getElementById("body");
    if (!body) return;
    const typing = this.shadowRoot.activeElement && ["q", "rn-name", "rn-place"].includes(this.shadowRoot.activeElement.id);
    if (typing && this.shadowRoot.activeElement.id !== "q") { this._renderModal(); return; }
    body.innerHTML = (this._error ? `<div class="error">${esc(this._error)}</div>` : "")
      + (this._data.fleets.length ? this._data.fleets.map((f) => this._fleetView(f)).join("")
        : `<div class="muted">Нет настроенных ферм. Добавьте интеграцию ASIC Fleet.</div>`);
    this._bind(body);
    if (typing) { const q = this.shadowRoot.getElementById("q"); if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); } }
    this._renderModal();
  }

  _summary(miners) {
    const online = miners.filter((m) => m.online).length;
    const blocked = miners.filter((m) => m.blocked).length;
    const problems = miners.filter((m) => Object.keys(m.problems || {}).length && !m.blocked).length;
    const byAlgo = {};
    let power = 0;
    for (const m of miners) {
      if (m.rate && !m.blocked) byAlgo[m.algorithm] = (byAlgo[m.algorithm] || 0) + m.rate;
      const p = m.plug && m.plug.power !== null && m.plug.power !== undefined ? m.plug.power : m.power;
      if (p && (m.online || (m.plug && m.plug.power))) power += p;
    }
    const rates = Object.entries(byAlgo).map(([a, r]) => `<b>${rate(r)}</b> <span class="sub">${esc(ALGO[a] || a)}</span>`).join(" · ");
    return `<div class="sum">
      <div><span class="big">${online}</span><span class="sub">/${miners.length} в сети</span></div>
      <div>${rates || '<span class="sub">нет хешрейта</span>'}</div>
      <div><b>${kw(power)}</b> <span class="sub">${power ? "≈ мощность" : ""}</span></div>
      ${blocked ? `<div class="blk-t"><b>${blocked}</b> <span class="sub">заблок.</span></div>` : ""}
      ${problems ? `<div class="bad"><b>${problems}</b> <span class="sub">с проблемами</span></div>` : ""}
    </div>`;
  }

  _fleetView(f) {
    const places = [...new Set(f.miners.map((m) => m.place || "—"))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    const tabs = ["*", ...places].map((p) => {
      const n = p === "*" ? f.miners.length : f.miners.filter((m) => (m.place || "—") === p).length;
      return `<button class="tab ${this._place === p ? "on" : ""}" data-do="place" data-p="${esc(p)}">${p === "*" ? "Все" : esc(p)} <span class="badge">${n}</span></button>`;
    }).join("");
    const q = this._q.toLowerCase();
    const shown = f.miners.filter((m) => (this._place === "*" || (m.place || "—") === this._place)
      && (!this._onlyProblems || (Object.keys(m.problems || {}).length && !m.blocked) || !m.online)
      && (!q || [m.name, m.ip, m.mac, m.model, m.place].join(" ").toLowerCase().includes(q)));
    const groups = {};
    for (const m of shown) (groups[m.place || "—"] = groups[m.place || "—"] || []).push(m);
    const keys = Object.keys(groups).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    const body = keys.map((p) => {
      const list = groups[p].sort((a, b) => (a.index ?? 999) - (b.index ?? 999) || a.name.localeCompare(b.name));
      const on = list.filter((m) => m.online).length;
      const bulk = this._admin && list.length > 1 ? (this._confirm && this._confirm.bulk === p
        ? `<span class="sub">${this._confirm.action === "block" ? "Отключить от интернета" : "Вернуть в работу"} все ${list.length}?</span>
           <button class="act primary" data-do="bulk-go" data-entry="${esc(f.entry_id)}" data-p="${esc(p)}">Да</button>
           <button class="act" data-do="cancel">Нет</button>`
        : `<button class="act sm" data-do="bulk-ask" data-p="${esc(p)}" data-a="block">⛔ все</button>
           <button class="act sm" data-do="bulk-ask" data-p="${esc(p)}" data-a="unblock">▶ все</button>`) : "";
      return `<section class="group">
        <div class="ghead"><h2>${esc(p)}</h2><span class="sub">${on}/${list.length} в сети · ${this._groupRate(list)}</span><span class="grow"></span>${bulk}</div>
        <div class="tiles">${list.map((m) => this._tile(f, m)).join("")}</div>
      </section>`;
    }).join("");
    return `
      <div class="fhead"><h1>${esc(f.title)}</h1>
        <span class="sub">${f.router.online ? "роутер на связи" : `<span class="bad">роутер: ${esc(f.router.error || "нет связи")}</span>`}</span></div>
      ${this._summary(f.miners)}
      <div class="tabs">${tabs}</div>
      <div class="filters">
        <input id="q" placeholder="Поиск: имя, IP, MAC, модель" value="${esc(this._q)}">
        <label><input type="checkbox" id="onlyp" ${this._onlyProblems ? "checked" : ""}> только проблемы</label>
      </div>
      ${body || '<div class="muted">Нет майнеров</div>'}`;
  }

  _groupRate(list) {
    const by = {};
    for (const m of list) if (m.rate && !m.blocked) by[m.algorithm] = (by[m.algorithm] || 0) + m.rate;
    return Object.values(by).map(rate).join(" + ") || "—";
  }

  _tile(f, m) {
    const [cls, label] = STATUS[m.status] || ["unk", m.status];
    const probs = Object.keys(m.problems || {}).filter((k) => k !== "offline");
    const why = (m.issues || []).map((i) => i.text).join("\n") || m.error || "";
    const num = m.index ?? "·";
    const plug = m.plug ? `<span title="${esc(m.plug.name)}" class="${m.plug.state === "on" ? "good" : "sub"}">🔌${m.plug.power !== null && m.plug.power !== undefined ? ` ${Math.round(m.plug.power)}Вт` : ""}</span>` : "";
    return `<button class="tile ${cls}" title="${esc(why)}" data-do="open" data-entry="${esc(f.entry_id)}" data-mac="${esc(m.mac)}">
      <div class="trow"><span class="num">${esc(num)}</span><span class="nm">${esc(m.name)}</span></div>
      <div class="trow"><b>${m.blocked ? "⛔ стоп" : m.online ? rate(m.rate) : "нет связи"}</b>
        <span class="grow"></span>${m.temp_max !== null && m.temp_max !== undefined ? `<span class="${m.temp_status === "critical" ? "bad" : m.temp_status === "warning" ? "warn" : ""}">${m.temp_max}°</span>` : ""}</div>
      <div class="trow sub">${esc(label)}${probs.length && (PROBLEM[probs[0]] || probs[0]) !== label ? ` · <span class="warn">${esc(PROBLEM[probs[0]] || probs[0])}</span>` : ""}<span class="grow"></span>${plug}</div>
      ${why && m.status !== "ok" ? `<div class="twhy">${esc(why.split("\n")[0])}</div>` : ""}
    </button>`;
  }

  _renderModal() {
    const host = this.shadowRoot.getElementById("modal");
    if (!host) return;
    if (!this._open) { host.innerHTML = ""; return; }
    const f = this._fleet(this._open.entry);
    const m = this._miner(this._open.entry, this._open.mac);
    if (!f || !m) { host.innerHTML = ""; return; }
    const typing = this.shadowRoot.activeElement && ["rn-name", "rn-place"].includes(this.shadowRoot.activeElement.id);
    if (typing) return;
    const key = m.mac;
    const busy = this._busy.has(key);
    const [cls, label] = STATUS[m.status] || ["unk", m.status];
    const c = this._confirm && this._confirm.mac === m.mac ? this._confirm : null;
    const ask = (action, text, primary = false) => c && c.action === action
      ? `<span class="sub">${text}?</span><button class="act primary" data-do="go" data-a="${action}">Да</button><button class="act" data-do="cancel">Нет</button>`
      : `<button class="act ${primary ? "primary" : ""}" data-do="ask" data-a="${action}" ${busy ? "disabled" : ""}>${text}</button>`;
    let actions = "";
    if (this._admin) {
      actions += m.blocked ? ask("unblock", "▶ Вернуть в работу", true) : ask("block", "⛔ Отключить от интернета");
      actions += `<button class="act" data-do="go1" data-a="blink" ${busy ? "disabled" : ""}>💡 Мигнуть</button>`;
      if (f.allow_reboot) actions += ask("reboot", "↻ Перезагрузить");
    }
    const plugOpts = `<option value="">— нет —</option>` + this._data.plugs.map((p) =>
      `<option value="${esc(p.entity_id)}" ${m.plug && m.plug.entity_id === p.entity_id ? "selected" : ""}>${esc(p.name)}${p.device && p.device !== p.name ? ` (${esc(p.device)})` : ""}</option>`).join("");
    let plugCtl = "";
    if (m.plug) {
      plugCtl = `<div>${m.plug.state === "on" ? '<span class="good">включена</span>' : m.plug.state === "off" ? '<span class="bad">выключена</span>' : esc(m.plug.state)}${
        m.plug.power !== null && m.plug.power !== undefined ? ` · ${Math.round(m.plug.power)} Вт` : ""}</div>`;
      if (this._admin) plugCtl += `<div class="actions">${ask("power_cycle", "⏻ Передёрнуть питание")}
        ${m.plug.state === "on" ? ask("plug_off", "Выключить розетку") : `<button class="act" data-do="go1" data-a="plug_on">Включить розетку</button>`}</div>`;
    }
    const row = (k, v) => `<tr><td class="sub">${k}</td><td>${v}</td></tr>`;
    host.innerHTML = `<div class="overlay" data-do="close-bg"><div class="sheet" role="dialog">
      <div class="shead"><span class="num big">${esc(m.index ?? "·")}</span>
        <div><div class="title2">${esc(m.name)}</div><div class="sub">${esc(m.place || "")} · ${esc(m.model || "модель ?")} · ${esc(m.ip || "нет IP")}</div></div>
        <span class="grow"></span><button class="x" data-do="close">✕</button></div>
      <div class="pill ${cls}">${esc(label)}</div>
      ${(m.issues || []).length ? `<ul class="issues">${m.issues.map((i) =>
        `<li class="${i.severity === "critical" ? "bad" : i.severity === "info" ? "sub" : "warn"}">${esc(i.text)}</li>`).join("")}</ul>`
        : m.error && !m.blocked ? `<div class="sub bad">${esc(m.error)}</div>` : ""}
      ${(m.miner_says || []).length ? `<div class="sub">Майнер сообщает: <span class="warn">${m.miner_says.map(esc).join(" · ")}</span></div>` : ""}
      ${m.blocked && !m.block_synced ? `<div class="sub bad">Блокировка ещё не применилась к текущему IP</div>` : ""}
      <table class="kv">
        ${row("Хешрейт", `${rate(m.rate)}${m.rate_ideal ? ` <span class="sub">из ${rate(m.rate_ideal)}</span>` : ""}`)}
        ${row("Температура", m.temp_max !== null && m.temp_max !== undefined ? `${m.temp_max} °C` : "—")}
        ${row("Вентиляторы", m.fan_min ? `${m.fan_min}–${m.fan_max} об/мин` : "—")}
        ${row("Чипы", m.chips && m.chips[1] ? `${m.chips[0]}/${m.chips[1]}` : "—")}
        ${row("Мощность", m.plug && m.plug.power !== null && m.plug.power !== undefined
          ? `${kw(m.plug.power)} <span class="sub">(по розетке)</span>`
          : `${kw(m.power)}${m.power && !m.power_measured ? ' <span class="sub">(оценка)</span>' : ""}`)}
        ${row("Аптайм", hours(m.uptime_h))}
        ${row("Пул", m.pool ? `${esc(m.pool)} <span class="sub">${esc(m.pool_status || "")}</span>` : "—")}
        ${row("MAC", esc(m.mac))}
      </table>
      ${actions ? `<div class="actions">${actions}</div>` : ""}
      <h3>Розетка</h3>
      ${this._admin ? `<select id="plugsel">${plugOpts}</select> <button class="act" data-do="setplug">Сохранить</button>` : ""}
      ${plugCtl}
      ${this._admin ? `<h3>Имя и место</h3>
        <div class="rn"><input id="rn-name" value="${esc(m.name)}" placeholder="Имя, напр. Garazh-ASIC2">
        <input id="rn-place" value="${esc(m.place || "")}" placeholder="Место / ряд">
        <button class="act" data-do="rename">Сохранить</button></div>
        <div class="sub">Имя вида «Место-ASICномер» (Garazh-ASIC2, R1-ASIC3) само задаёт место и номер.</div>` : ""}
    </div></div>`;
    host.querySelectorAll("[data-do]").forEach((b) => b.addEventListener("click", (ev) => {
      const d = b.dataset.do;
      if (d === "close-bg" && ev.target !== b) return;
      ev.stopPropagation();
      this._act(d, b.dataset, f, m);
    }));
  }

  _act(d, ds, f, m) {
    switch (d) {
      case "close": case "close-bg": this._open = null; this._confirm = null; break;
      case "ask": this._confirm = { mac: m.mac, action: ds.a }; break;
      case "cancel": this._confirm = null; break;
      case "go": case "go1":
        this._confirm = null;
        this._call({ type: "asic_fleet/action", entry_id: f.entry_id, mac: m.mac, action: ds.a }, m.mac);
        return;
      case "setplug":
        this._call({ type: "asic_fleet/set_plug", entry_id: f.entry_id, mac: m.mac,
          plug: this.shadowRoot.getElementById("plugsel").value }, m.mac);
        return;
      case "rename":
        this._call({ type: "asic_fleet/rename", entry_id: f.entry_id, mac: m.mac,
          name: this.shadowRoot.getElementById("rn-name").value,
          place: this.shadowRoot.getElementById("rn-place").value }, m.mac);
        return;
    }
    this._render();
  }

  _bind(root) {
    root.querySelectorAll("[data-do]").forEach((b) => b.addEventListener("click", async () => {
      const d = b.dataset;
      switch (d.do) {
        case "place": this._place = d.p; break;
        case "open": this._open = { entry: d.entry, mac: d.mac }; this._confirm = null; break;
        case "bulk-ask": this._confirm = { bulk: d.p, action: d.a }; break;
        case "cancel": this._confirm = null; break;
        case "bulk-go": {
          const c = this._confirm; this._confirm = null;
          const f = this._fleet(d.entry);
          const list = f.miners.filter((m) => (m.place || "—") === c.bulk && m.blocked !== (c.action === "block"));
          for (const m of list) {
            await this._call({ type: "asic_fleet/action", entry_id: f.entry_id, mac: m.mac, action: c.action }, m.mac);
          }
          return;
        }
      }
      this._render();
    }));
    const q = root.querySelector("#q");
    if (q) q.addEventListener("input", () => { this._q = q.value; clearTimeout(this._qt); this._qt = setTimeout(() => this._render(), 250); });
    const op = root.querySelector("#onlyp");
    if (op) op.addEventListener("change", () => { this._onlyProblems = op.checked; this._render(); });
  }
}

const STYLE = `
  :host { display:block; background: var(--primary-background-color); color: var(--primary-text-color);
    min-height: 100vh; font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); overflow-x: hidden; }
  .toolbar { display:flex; align-items:center; height:56px; padding:0 12px; gap:8px;
    background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); }
  .title { font-size: 20px; }
  .body { padding: 16px; max-width: 1400px; margin: 0 auto; box-sizing: border-box; }
  h1 { font-size: 22px; font-weight: 500; margin: 0; }
  h2 { font-size: 16px; font-weight: 600; margin: 0; }
  h3 { font-size: 13px; color: var(--secondary-text-color); margin: 14px 0 6px; font-weight: 500; }
  .fhead { display:flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin: 4px 0 10px; }
  .sub { font-size: 12px; color: var(--secondary-text-color); }
  .muted { color: var(--secondary-text-color); padding: 8px; }
  .error { background: var(--error-color); color: #fff; padding: 8px 12px; border-radius: 8px; margin-bottom: 12px; }
  .good { color: var(--success-color, #2e7d32); } .warn { color: var(--warning-color, #ef6c00); }
  .issues { margin: 6px 0; padding-left: 18px; } .issues li { margin: 2px 0; }
  .twhy { font-size: 11px; line-height: 1.25; color: var(--secondary-text-color); margin-top: 2px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; text-align: left; }
  .bad { color: var(--error-color, #c62828); } .blk-t { color: #7e57c2; }
  .sum { display:flex; flex-wrap: wrap; gap: 8px 22px; align-items: baseline; padding: 12px 14px; margin-bottom: 12px;
    background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 12px; }
  .big { font-size: 26px; font-weight: 600; }
  .tabs { display:flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
  .tab { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color);
    border-radius: 16px; padding: 4px 12px; cursor: pointer; font-size: 13px; }
  .tab.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  .badge { opacity: .8; margin-left: 4px; }
  .filters { display:flex; gap: 12px; align-items:center; margin-bottom: 12px; flex-wrap: wrap; }
  .filters input:not([type]) { flex: 1; min-width: 0; padding: 6px 10px; border-radius: 8px; border: 1px solid var(--divider-color);
    background: var(--secondary-background-color); color: var(--primary-text-color); }
  .group { margin-bottom: 16px; }
  .ghead { display:flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 6px 2px 8px; }
  .grow { flex: 1; }
  .tiles { display:grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 8px; }
  .tile { text-align: left; display:flex; flex-direction: column; gap: 4px; padding: 8px 10px; border-radius: 10px; cursor: pointer;
    background: var(--card-background-color); color: var(--primary-text-color); border: 1px solid var(--divider-color);
    border-left: 5px solid var(--disabled-text-color, #999); font-size: 13px; min-width: 0; }
  .tile.ok { border-left-color: var(--success-color, #2e7d32); } .tile.warn { border-left-color: var(--warning-color, #ef6c00); }
  .tile.bad { border-left-color: var(--error-color, #c62828); } .tile.blk { border-left-color: #7e57c2; opacity: .85; }
  .trow { display:flex; align-items: center; gap: 6px; min-width: 0; }
  .num { display:inline-flex; min-width: 26px; height: 26px; padding: 0 4px; box-sizing: border-box; border-radius: 13px;
    background: var(--secondary-background-color); align-items:center; justify-content:center; font-weight: 700; font-size: 14px; }
  .num.big { min-width: 40px; height: 40px; border-radius: 20px; font-size: 18px; }
  .nm { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .act { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color);
    border-radius: 8px; padding: 5px 10px; cursor: pointer; font-size: 13px; }
  .act.sm { padding: 2px 8px; font-size: 12px; }
  .act.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  .act[disabled] { opacity: .5; }
  .actions { display:flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 10px; }
  .overlay { position: fixed; inset: 0; background: rgba(0,0,0,.45); display:flex; justify-content:center; align-items:flex-start;
    padding: 24px 10px; overflow-y: auto; z-index: 10; }
  .sheet { background: var(--card-background-color); color: var(--primary-text-color); border-radius: 16px; width: 100%; max-width: 520px;
    padding: 16px; box-sizing: border-box; }
  .shead { display:flex; gap: 12px; align-items: center; }
  .title2 { font-size: 18px; font-weight: 600; }
  .x { border: none; background: transparent; color: var(--secondary-text-color); font-size: 18px; cursor: pointer; }
  .pill { display:inline-block; margin: 10px 0 6px; padding: 2px 10px; border-radius: 10px; font-size: 12px; background: var(--secondary-background-color); }
  .pill.ok { color: var(--success-color, #2e7d32); } .pill.bad { color: var(--error-color, #c62828); }
  .pill.warn { color: var(--warning-color, #ef6c00); } .pill.blk { color: #7e57c2; }
  .kv { width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 6px; }
  .kv td { padding: 4px 4px; border-bottom: 1px solid var(--divider-color); }
  select, .rn input { padding: 5px 8px; border-radius: 8px; border: 1px solid var(--divider-color);
    background: var(--secondary-background-color); color: var(--primary-text-color); max-width: 100%; }
  .rn { display:flex; gap: 6px; flex-wrap: wrap; } .rn input { flex: 1; min-width: 120px; }
  @media (max-width: 600px) {
    .body { padding: 8px; } .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
    .tile { padding: 7px 8px; } .big { font-size: 22px; }
  }
`;

if (!customElements.get("asic-fleet-panel")) customElements.define("asic-fleet-panel", AsicFleetPanel);
