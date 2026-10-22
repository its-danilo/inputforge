"use strict";
/* InputForge window. Talks to the engine through the GTK host (app.py):
   window.webkit.messageHandlers.inputforge.postMessage(json) -> __ifReply / __ifEvent.
   Opened in a plain browser it runs on a mock engine (design previews). */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const icon = (n, cls = "") => `<svg class="${cls}"><use href="#i-${n}"/></svg>`;
const clone = (o) => JSON.parse(JSON.stringify(o));
const uid = () => Math.random().toString(16).slice(2, 10);
const params = new URLSearchParams(location.search);
if (params.has("static")) document.documentElement.classList.add("static");
if (params.has("overlay")) document.documentElement.classList.add("overlay"); // editor over the game

const MOD_CODE = { ctrl: "KEY_LEFTCTRL", shift: "KEY_LEFTSHIFT", alt: "KEY_LEFTALT", altgr: "KEY_RIGHTALT", super: "KEY_LEFTMETA" };
const MOD_LABEL = { ctrl: "Ctrl", shift: "Shift", alt: "Alt", altgr: "AltGr", super: "Super" };
const MOUSE = [["BTN_LEFT", "Esquerdo"], ["BTN_RIGHT", "Direito"], ["BTN_MIDDLE", "Meio"], ["BTN_SIDE", "Lateral trás"], ["BTN_EXTRA", "Lateral frente"]];
const MODES = [["toggle", "Alternar", "Aperta liga, aperta de novo desliga"], ["hold", "Segurar", "Roda enquanto você segura"], ["once", "Uma vez", "Cada toque roda uma vez"]];
const TYPES = {
  clicker: { icon: "click", name: "Autoclicker", desc: "Cliques (ou toques de tecla) em ritmo fixo, no cursor ou em pontos da tela." },
  macro: { icon: "macro", name: "Macro", desc: "Uma sequência de teclas, cliques, esperas e movimentos. Dá pra gravar jogando." },
  remap: { icon: "remap", name: "Remapear", desc: "A tecla ou botão vira outra coisa enquanto o perfil está ativo." },
  command: { icon: "terminal", name: "Comando", desc: "Roda um comando do shell no atalho (scripts, notify-send, hyprctl…)." },
};
const STEP = {
  key: { icon: "keyboard", name: "Tecla" }, button: { icon: "mouse", name: "Clique" }, wait: { icon: "clock", name: "Esperar" },
  move: { icon: "move", name: "Mover" }, moveto: { icon: "crosshair", name: "Ir para" }, scroll: { icon: "scroll", name: "Rolar" },
  text: { icon: "type", name: "Texto" }, command: { icon: "terminal", name: "Comando" },
  wait_screen: { icon: "eye", name: "Esperar tela" }, if_screen: { icon: "branch", name: "Se a tela" },
};
const CONDS = [["match", "igual à referência"], ["differ", "diferente da referência"], ["change", "mudou"]];
const CONDS_SHORT = { match: "igual", differ: "diferente", change: "mudou" };
const DESKTOP = /^(Alacritty|zen|yazi|com\.rtosta\.zapzap|com\.ayugram\.desktop|AyuGram|vesktop|io\.github\.its_danilo\..*|org\.calos\..*|Calos|Keybinds|TUI\.float|Impala|steam|Neovim|clipse)$/i;

/* ================================================================ bridge */
const Bridge = (() => {
  const native = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.inputforge;
  let seq = 0;
  const pending = new Map();
  const listeners = new Set();
  window.__ifReply = (id, ok, result) => {
    const p = pending.get(id);
    if (!p) return;
    pending.delete(id);
    ok ? p.res(result) : p.rej(new Error(result || "erro"));
  };
  window.__ifEvent = (msg) => listeners.forEach((fn) => fn(msg));
  function call(cmd, args = {}) {
    if (!native) return Mock.call(cmd, args);
    return new Promise((res, rej) => {
      const id = ++seq;
      pending.set(id, { res, rej });
      native.postMessage(JSON.stringify({ id, cmd, ...args }));
    });
  }
  return { call, on: (fn) => listeners.add(fn), native: !!native };
})();

/* ================================================================ state */
const S = {
  cfg: null, st: null, labels: {}, route: { page: "home" }, drawer: null,
  capturing: null, editSeq: 0, savedSeq: 0, engineUp: true, recordFor: null, pickFor: null, env: {},
};

const profiles = () => S.cfg.profiles;
const findProfile = (id) => profiles().find((p) => p.id === id);
const findBinding = (pid, bid) => findProfile(pid)?.bindings.find((b) => b.id === bid);
const globalProfile = () => profiles()[0];
const label = (name) => S.labels[name] || (name || "").replace(/^(KEY|BTN)_/, "");
const runsOf = (bid) => (S.st?.runs || []).filter((r) => r.binding === bid);
/* screen regions: a binding may use its profile's and the global profile's */
const regionsFor = (pid) => {
  const own = findProfile(pid)?.regions || [];
  return pid === "global" ? own : own.concat(globalProfile().regions || []);
};
const regionById = (rid) => {
  for (const p of profiles()) for (const r of p.regions || []) if (r.id === rid) return r;
  return null;
};

let saveTimer = null;
function save(now = false) {
  S.editSeq++;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flush, now ? 0 : 350);
}
async function flush() {
  const seq = S.editSeq;
  try {
    const cfg = await Bridge.call("set_config", { config: S.cfg });
    S.savedSeq = seq;
    if (seq === S.editSeq) adoptConfig(cfg);
  } catch (e) {
    toast("Não salvou: " + e.message, "err");
  }
}
function adoptConfig(cfg) {
  // normalized copy from the engine (fills ids/defaults); keep the user's newer local edits
  if (S.editSeq !== S.savedSeq) return;
  S.cfg = cfg;
}

/* ================================================================ helpers */
function keycaps(trig, big = false) {
  if (trig && trig.kind === "screen") {
    const r = regionById(trig.region);
    return `<span class="keys"><span class="kbd eye ${big ? "big accent" : ""}">${icon("eye")} ${esc(r ? r.name : "região?")} · ${CONDS_SHORT[trig.cond] || ""}</span></span>`;
  }
  if (!trig || !trig.key) return `<span class="kbd-none">nenhum</span>`;
  const parts = (trig.mods || []).map((m) => `<span class="kbd mod ${big ? "big" : ""}">${MOD_LABEL[m]}</span><span class="plus">+</span>`);
  return `<span class="keys">${parts.join("")}<span class="kbd ${big ? "big accent" : ""}">${esc(label(trig.key))}</span></span>`;
}
function codecaps(codes) {
  if (!codes || !codes.length) return `<span class="kbd-none">nada</span>`;
  return `<span class="keys">${codes.map((c) => `<span class="kbd">${esc(label(c))}</span>`).join('<span class="plus">+</span>')}</span>`;
}
function hue(str) {
  let h = 0;
  for (const ch of str || "") h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}
function avatar(p, lg = false) {
  const armed = S.st?.armed?.id === p.id;
  const inner = p.id === "global" ? icon("globe") : esc((p.name || "?").trim().slice(0, 2).toUpperCase() || "?");
  const style = p.id === "global" ? "" : `style="--h: hsl(${hue(p.name)} 70% 58%)"`;
  return `<span class="avatar ${lg ? "lg" : ""}" ${style}>${inner}${armed ? '<i class="armed-dot"></i>' : ""}</span>`;
}
function fmt(n, d = 0) {
  return Number(n).toLocaleString("pt-BR", { maximumFractionDigits: d });
}
function rateText(a) {
  return a.rate.unit === "cps" ? `${fmt(a.rate.value, 2)} CPS` : `a cada ${fmt(a.rate.value)} ms`;
}
function summary(b) {
  const a = b.action;
  if (a.type === "clicker") {
    const target = MOUSE.find((m) => m[0] === a.target)?.[1];
    const what = target ? `Clique ${target.toLowerCase()}` : `Tecla ${label(a.target)}`;
    const where = a.where === "points" ? `em ${a.points.length} ponto${a.points.length === 1 ? "" : "s"}` : "no cursor";
    const lim = a.limit.kind === "count" ? ` · ${fmt(a.limit.value)} cliques` : a.limit.kind === "time" ? ` · por ${fmt(a.limit.value)} s` : "";
    return `${what} · ${rateText(a)} · ${where}${lim}${a.double ? " · duplo" : ""}${a.jitter ? ` · ±${a.jitter}%` : ""}`;
  }
  if (a.type === "macro") {
    const rep = a.repeat === 0 ? "repete até parar" : a.repeat === 1 ? "roda 1 vez" : `repete ${a.repeat}×`;
    const eyes = a.steps.some((s) => s.t === "wait_screen" || s.t === "if_screen");
    return `${a.steps.length} passo${a.steps.length === 1 ? "" : "s"} · ${rep}${eyes ? " · olha a tela" : ""}${a.speed !== 1 ? ` · ${fmt(a.speed, 2)}× velocidade` : ""}`;
  }
  if (a.type === "remap") return "";
  return a.cmd ? `<code style="font-family:var(--mono);font-size:12px">${esc(a.cmd.slice(0, 80))}</code>` : "Nenhum comando ainda";
}
function prettyClass(cls, title) {
  if (/^steam_app_/.test(cls)) return (title || "Jogo Steam").slice(0, 40);
  const last = (cls || "").split(".").pop();
  return last ? last.charAt(0).toUpperCase() + last.slice(1) : title || "Novo perfil";
}
const reEscape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
}
function setPath(obj, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  const target = keys.reduce((o, k) => o[k], obj);
  target[last] = value;
}

/* ================================================================ toasts/banner */
function toast(text, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<span class="ti">${icon(kind === "err" ? "x" : "check")}</span>${esc(text)}`;
  $("#toasts").append(el);
  setTimeout(() => el.classList.add("out"), 2600);
  setTimeout(() => el.remove(), 3000);
}
function banner(html, cls = "") {
  const b = $("#banner");
  if (!html) { b.className = "banner"; return; }
  b.className = `banner show ${cls}`;
  b.innerHTML = html;
}

/* ================================================================ chrome */
function renderNav() {
  const r = S.route;
  const total = profiles().reduce((n, p) => n + p.bindings.length, 0);
  $("#nav").innerHTML = `
    <button class="nav-item ${r.page === "home" ? "active" : ""}" data-go="home">${icon("home")} Início</button>
    <button class="nav-item ${r.page === "profile" && r.id === "global" ? "active" : ""}" data-go="profile:global">${icon("globe")} Qualquer janela <span class="count">${globalProfile().bindings.length}</span></button>
    <button class="nav-item ${r.page === "library" ? "active" : ""}" data-go="library">${icon("layers")} Todos os atalhos <span class="count">${total}</span></button>`;
  $("#side-foot").innerHTML = `
    <button class="nav-item ${r.page === "settings" ? "active" : ""}" data-go="settings">${icon("settings")} Ajustes</button>`;
}
function renderProfiles() {
  const list = profiles().slice(1);
  $("#profile-list").innerHTML = list.length ? list.map((p) => `
    <button class="profile-item ${S.route.page === "profile" && S.route.id === p.id ? "active" : ""} ${p.enabled ? "" : "off"}" data-go="profile:${p.id}">
      ${avatar(p)}
      <span class="meta"><span class="name">${esc(p.name)}</span><span class="sub">${S.st?.armed?.id === p.id ? "ativo agora" : `${p.bindings.length} atalho${p.bindings.length === 1 ? "" : "s"}`}</span></span>
    </button>`).join("") : `<div class="hint" style="padding:6px 10px">Crie um perfil por jogo: ele liga sozinho quando o jogo está em foco.</div>`;
}
function renderCrumbs() {
  const r = S.route;
  let trail = `<span>InputForge</span>${icon("chev")}`;
  if (r.page === "home") trail += "<b>Início</b>";
  else if (r.page === "settings") trail += "<b>Ajustes</b>";
  else if (r.page === "library") trail += "<b>Todos os atalhos</b>";
  else if (r.page === "profile") trail += `<span>Perfis</span>${icon("chev")}<b>${esc(findProfile(r.id)?.name || "")}</b>`;
  $("#crumbs").innerHTML = trail;
}
function renderEngineChip() {
  const st = S.st;
  let chip;
  if (!S.engineUp) chip = `<span class="chip red">${icon("power")} Motor parado</span>`;
  else if (st?.recording) chip = `<span class="chip red"><span class="pulse"></span>Gravando</span>`;
  else if (st?.runs?.length) chip = `<span class="chip accent"><span class="pulse"></span>${st.runs.length} rodando</span>`;
  else if (st?.armed) chip = `<span class="chip accent">${icon("shield")} ${esc(st.armed.name)}</span>`;
  else chip = `<span class="chip">${icon("shield")} Em espera</span>`;
  $("#engine-chip").innerHTML = chip;
}

/* ================================================================ router */
function go(route) {
  const [page, id] = route.split(":");
  S.route = { page, id };
  closeDrawer(false);
  renderNav(); renderProfiles(); renderCrumbs(); renderEngineChip();
  renderView(true);
  $("#view").scrollTop = 0;
}
function renderAll() {
  renderNav();
  renderProfiles();
  renderCrumbs();
  renderEngineChip();
  renderView();
}
function renderView(animate = false) {
  const r = S.route;
  let html = "";
  if (r.page === "home") html = viewHome();
  else if (r.page === "settings") html = viewSettings();
  else if (r.page === "library") html = viewLibrary();
  else if (r.page === "profile") {
    if (!findProfile(r.id)) { S.route = { page: "home" }; return renderAll(); }
    html = viewProfile(findProfile(r.id));
  }
  $("#view").innerHTML = `<div class="page ${animate ? "enter" : ""}">${html}</div>`;
  renderLive();
}

/* ================================================================ live bits */
const LIVE = {
  hero() {
    const st = S.st || {};
    const armed = st.armed;
    const runs = st.runs || [];
    const active = runs.length > 0 || !!st.recording;
    const focus = st.focus;
    const title = !S.engineUp ? "Motor <span class=\"grad\">parado.</span>"
      : runs.length ? `${runs.length === 1 ? "Um atalho" : `${runs.length} atalhos`} <span class="grad">em ação.</span>`
      : armed && armed.id !== "global" ? `Pronto pra <span class="grad">${esc(armed.name)}.</span>`
      : "Forje seus <span class=\"grad\">reflexos.</span>";
    const lead = !S.engineUp ? "O serviço inputforge não está respondendo. Inicie de novo pelo botão abaixo."
      : armed && armed.id !== "global" ? `O perfil liga sozinho com o jogo em foco: ${armed.bindings} atalho${armed.bindings === 1 ? "" : "s"} esperando o seu comando.`
      : "Autoclicker, macros e remapeamento por jogo. Os atalhos funcionam até no meio do jogo em tela cheia.";
    const chips = [
      S.engineUp ? `<span class="chip accent"><span class="pulse"></span>Motor online</span>` : `<button class="btn sm primary" data-action="engine-start">${icon("power")} Iniciar motor</button>`,
      armed ? `<span class="chip">${icon("shield")} Perfil: ${esc(armed.name)}</span>` : `<span class="chip">${icon("shield")} Nenhum perfil ativo</span>`,
      focus ? `<span class="chip">${icon("monitor")} ${esc(prettyClass(focus.class, focus.title))}</span>` : "",
      st.gamemode && st.gamemode !== "off" ? `<span class="chip accent">${icon("gamepad")} Game Mode ${st.gamemode === "on" ? "ligado" : "pausado"}</span>` : "",
      st.overlay && params.has("overlay") ? `<span class="chip">${icon("pause")} Atalhos em pausa enquanto você edita · Esc volta pro jogo</span>` : "",
    ].join("");
    return `<div class="status-orb ${active ? "" : "idle"}"><div class="ring"></div><div class="ring r2"></div><div class="core"></div></div>
      <div class="eyebrow">${runs.length ? "Ao vivo" : "Painel"}</div>
      <h1 class="h1">${title}</h1>
      <p class="lead">${lead}</p>
      <div class="chips">${chips}</div>`;
  },
  quickbtn() {
    const b = quickBinding();
    const running = b && runsOf(b.id).length;
    const n = S.cfg.settings.countdown;
    return running
      ? `<button class="btn lg start running" data-action="run" data-pid="global" data-bid="${b.id}">${icon("stop")} Parar</button>`
      : `<button class="btn lg primary start" data-action="run" data-pid="global" data-bid="${b ? b.id : ""}">${icon("play")} Iniciar${n ? ` em ${n} s` : ""}</button>`;
  },
  runs() {
    const runs = S.st?.runs || [];
    if (!runs.length) return `<div class="empty">Nada rodando. Aperte o atalho no jogo ou use ${icon("play")} num cartão.</div>`;
    return runs.map((r) => `
      <div class="run ${r.status}">
        <span class="bars"><i></i><i></i><i></i><i></i></span>
        <div class="stack" style="gap:2px;flex:1;min-width:0">
          <b>${esc(r.name)}</b>
          <span class="small faint">${esc(r.profile_name)} · ${r.status === "paused" ? "pausado (o jogo saiu do foco)" : r.status === "countdown" ? "começando…" : esc(r.detail)}</span>
        </div>
        ${keycaps(r.trigger)}
        <button class="icon-btn danger" data-action="stop-run" data-run="${r.id}" title="Parar">${icon("stop")}</button>
      </div>`).join("");
  },
  badge(ds) {
    const runs = runsOf(ds.bid);
    if (!runs.length) return "";
    const r = runs[0];
    return r.status === "paused" ? `<span class="chip">${icon("pause")} pausado</span>`
      : r.status === "countdown" ? `<span class="chip accent"><span class="pulse"></span>já vai</span>`
      : `<span class="chip accent"><span class="pulse"></span>rodando</span>`;
  },
  play(ds) {
    const running = runsOf(ds.bid).length;
    return `<button class="icon-btn" data-action="run" data-pid="${ds.pid}" data-bid="${ds.bid}" title="${running ? "Parar" : "Testar agora"}">${icon(running ? "stop" : "play")}</button>`;
  },
  tiles() {
    const st = S.st || {};
    const total = profiles().reduce((n, p) => n + p.bindings.filter((b) => b.enabled).length, 0);
    const games = profiles().length - 1;
    return [
      ["zap", "Atalhos ativos", total],
      ["gamepad", "Perfis de jogo", games],
      ["click", "Rodando agora", (st.runs || []).length],
      ["shield", "Pânico", `<span style="font-size:15px;font-weight:650">${keycaps(S.cfg.settings.panic)} ${S.cfg.settings.panic.hold_ms ? `<span class="small faint">segure ${fmt(S.cfg.settings.panic.hold_ms / 1000, 1)} s</span>` : ""}</span>`],
    ].map(([ic, l, v]) => `<div class="card tile"><div class="lbl">${icon(ic)} ${l}</div><div class="val">${v}</div></div>`).join("");
  },
  detect() {
    // the newest recently focused window that looks like a game and has no profile yet
    const recent = S.st?.recent || (S.st?.last_game ? [S.st.last_game] : []);
    const g = recent.find((w) => !DESKTOP.test(w.class) && !profiles().some((p) => p.id !== "global" && matches(p, w)));
    if (!g) return "";
    return `<div class="card detect">
      <span class="avatar lg" style="--h: hsl(${hue(g.class)} 70% 58%)">${icon("gamepad")}</span>
      <div class="stack" style="gap:4px;flex:1;min-width:0">
        <div class="eyebrow">Janela detectada</div>
        <div class="h3">${esc(prettyClass(g.class, g.title))} ainda não tem perfil</div>
        <div class="small faint" style="font-family:var(--mono)">${esc(g.class)}${g.title ? " · " + esc(g.title.slice(0, 60)) : ""}</div>
      </div>
      <button class="btn primary" data-action="create-from" data-class="${esc(g.class)}" data-title="${esc(g.title)}">${icon("sparkle")} Criar perfil</button>
    </div>`;
  },
};
function renderLive() {
  for (const el of $$("[data-live]")) {
    const fn = LIVE[el.dataset.live];
    if (!fn) continue;
    const html = fn(el.dataset);
    if (el._html !== html) { el.innerHTML = html; el._html = html; } // keeps CSS animations running
  }
  renderEngineChip();
}
function matches(p, w) {
  try {
    if (!p.match.class && !p.match.title) return false;
    if (p.match.class && !new RegExp(p.match.class).test(w.class)) return false;
    if (p.match.title && !new RegExp(p.match.title).test(w.title)) return false;
    return true;
  } catch { return false; }
}

/* ================================================================ home */
function quickBinding() {
  return globalProfile().bindings.find((b) => b.action.type === "clicker");
}
function cpsToSlider(v) { return Math.round(Math.log(Math.max(1, v)) / Math.log(200) * 100); }
function sliderToCps(p) { return Math.max(1, Math.round(Math.exp(p / 100 * Math.log(200)))); }

function viewHome() {
  const b = quickBinding();
  let quick;
  if (!b) {
    quick = `<div class="card quick"><div class="eyebrow">Clique rápido</div><p class="muted">O perfil “Qualquer janela” não tem autoclicker.</p>
      <button class="btn primary" data-action="quick-create">${icon("plus")} Criar autoclicker</button></div>`;
  } else {
    const a = b.action;
    const cps = a.rate.unit === "cps" ? a.rate.value : 1000 / a.rate.value;
    quick = `<div class="card quick">
      <div class="top"><div class="eyebrow">Clique rápido · qualquer janela</div>
        <button class="btn sm ghost" data-action="edit" data-pid="global" data-bid="${b.id}">${icon("edit")} Mais opções</button></div>
      <div class="row" style="justify-content:space-between;align-items:flex-end">
        <div class="cps"><span class="num" id="quick-num">${fmt(cps, cps < 10 ? 1 : 0)}</span><span class="unit">CPS</span></div>
        <div class="stack" style="gap:6px;align-items:flex-end">
          <span class="label">Atalho</span>
          <button class="capture ${S.capturing === "quick" ? "listening" : ""}" data-action="capture" data-target="quick">${keycaps(b.trigger)}<span class="go">${S.capturing === "quick" ? "aperte…" : icon("edit")}</span></button>
        </div>
      </div>
      <input type="range" class="range" min="0" max="100" value="${cpsToSlider(cps)}" data-quick="cps" style="--p:${cpsToSlider(cps)}%">
      <div class="seg full" data-quick-seg>
        ${MOUSE.slice(0, 3).map(([c, n]) => `<button class="${a.target === c ? "on" : ""}" data-action="quick-target" data-code="${c}">${n}</button>`).join("")}
      </div>
      <div data-live="quickbtn"></div>
    </div>`;
  }
  const others = profiles().slice(1);
  return `
    <section class="hero">
      <div class="card hero-main" data-live="hero"></div>
      ${quick}
    </section>
    <div data-live="detect"></div>
    <div class="tiles" data-live="tiles"></div>
    <div class="section-head"><h2 class="h2">${icon("zap")} Rodando agora</h2></div>
    <div class="runs" data-live="runs"></div>
    <div class="section-head"><h2 class="h2">${icon("gamepad")} Perfis de jogo</h2>
      <button class="btn" data-action="new-profile">${icon("plus")} Novo perfil</button></div>
    <div class="pgrid">
      ${others.map((p) => `
        <div class="card pcard interactive" data-go="profile:${p.id}">
          <div class="row">${avatar(p)}<div class="stack" style="gap:2px;min-width:0"><div class="h3">${esc(p.name)}</div>
            <div class="small faint" style="font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.match.class || p.match.title || "sem regra")}</div></div></div>
          <div class="row wrap">${p.bindings.slice(0, 4).map((b) => keycaps(b.trigger)).join("")}${p.bindings.length > 4 ? `<span class="chip">+${p.bindings.length - 4}</span>` : ""}</div>
          <div class="row small faint" style="margin-top:auto">${p.bindings.length} atalho${p.bindings.length === 1 ? "" : "s"} <span class="spacer"></span>${S.st?.armed?.id === p.id ? '<span class="chip accent"><span class="pulse"></span>ativo</span>' : p.enabled ? "" : '<span class="chip">desligado</span>'}</div>
        </div>`).join("")}
      <div class="card pcard add-card interactive" data-action="new-profile"><div class="inner"><span class="plusc">${icon("plus")}</span>Novo perfil de jogo</div></div>
    </div>`;
}

/* ================================================================ profile */
function viewProfile(p) {
  const isGlobal = p.id === "global";
  const enabled = isGlobal ? S.cfg.settings.global_enabled : p.enabled;
  return `
    <div class="card phead">
      ${avatar(p, true)}
      <div class="stack" style="gap:4px;flex:1;min-width:0">
        <div class="eyebrow">${isGlobal ? "Perfil global" : "Perfil de jogo"}</div>
        ${isGlobal ? `<div class="h2" style="font-size:28px;letter-spacing:-.04em">Qualquer janela</div>`
          : `<input class="title-input" value="${esc(p.name)}" data-pbind="name" spellcheck="false">`}
        <div class="small faint">${isGlobal ? "Vale em todo lugar quando nenhum perfil de jogo está ativo — o autoclicker clássico." : "Liga sozinho quando a janela do jogo está em foco."}</div>
      </div>
      <div class="row">
        <span class="small muted">${enabled ? "Ligado" : "Desligado"}</span>
        <label class="switch"><input type="checkbox" ${enabled ? "checked" : ""} data-action="profile-enabled" data-pid="${p.id}"><span class="track"></span><span class="knob"></span></label>
        ${isGlobal ? "" : `<button class="icon-btn danger" data-action="delete-profile" data-pid="${p.id}" title="Apagar perfil">${icon("trash")}</button>`}
      </div>
    </div>
    ${isGlobal ? "" : `
    <div class="card match">
      <div class="field"><label>Classe da janela (regex)</label><input class="input mono" value="${esc(p.match.class)}" data-pbind="match.class" placeholder="^org\\.vinegarhq\\.Sober$" spellcheck="false"></div>
      <div class="field"><label>Título (regex, opcional)</label><input class="input mono" value="${esc(p.match.title)}" data-pbind="match.title" placeholder="Roblox" spellcheck="false"></div>
      <button class="btn" data-action="pick-window" data-pid="${p.id}">${icon("search")} Escolher janela</button>
      <div class="field" style="grid-column:1/-1;flex-direction:row;align-items:center;gap:14px">
        <span class="label">Quando o jogo sai do foco</span>
        <div class="seg">
          <button class="${p.on_blur === "pause" ? "on" : ""}" data-action="on-blur" data-value="pause">${icon("pause")} Pausar e retomar</button>
          <button class="${p.on_blur === "stop" ? "on" : ""}" data-action="on-blur" data-value="stop">${icon("stop")} Parar</button>
        </div>
      </div>
    </div>`}
    <div class="section-head"><h2 class="h2">${icon("eye")} Regiões da tela</h2>
      <button class="btn" data-action="region-new" data-pid="${p.id}">${icon("scan")} Nova região</button></div>
    ${regionsSection(p)}
    <div class="section-head"><h2 class="h2">${icon("zap")} Atalhos</h2>
      <button class="btn primary" data-action="new-binding" data-pid="${p.id}">${icon("plus")} Novo atalho</button></div>
    <div class="bgrid">
      ${p.bindings.map((b) => bindingCard(p, b)).join("")}
      <div class="card add-card interactive" data-action="new-binding" data-pid="${p.id}"><div class="inner"><span class="plusc">${icon("plus")}</span>Novo atalho</div></div>
    </div>`;
}
function sigPreview(ref) {
  if (!ref) return `<div class="sig empty-sig">sem referência</div>`;
  return `<div class="sig">${ref.map((c) => `<i style="background:rgb(${c[0]},${c[1]},${c[2]})"></i>`).join("")}</div>`;
}
function regionsSection(p) {
  const regs = p.regions || [];
  if (!regs.length) {
    return `<div class="card hintcard"><div class="row" style="gap:16px">${icon("eye", "big-eye")}<div class="stack" style="gap:4px">
      <div class="h3">O InputForge pode olhar a tela</div>
      <div class="small muted">Marque um pedaço da tela do jogo (um slot de skill, uma barra de vida, um botão) enquanto ele mostra o estado que interessa. Depois use "Esperar tela", "Se a tela…" nas macros ou um gatilho por tela.</div></div></div></div>`;
  }
  return `<div class="rgrid">${regs.map((r) => `
    <div class="card rcard">
      <div class="row" style="gap:14px;align-items:flex-start">
        ${sigPreview(r.ref)}
        <div class="stack" style="gap:6px;flex:1;min-width:0">
          <input class="title-input sm" value="${esc(r.name)}" data-rbind="name" data-pid="${p.id}" data-rid="${r.id}" spellcheck="false">
          <div class="small faint" style="font-family:var(--mono)">${r.w ? `${r.x}, ${r.y} · ${r.w}×${r.h}` : "não marcada"}</div>
          <div class="field" style="gap:4px"><label>Tolerância <b id="tol-${r.id}">${r.tolerance}%</b></label>
            <input type="range" class="range" min="0" max="40" value="${r.tolerance}" data-rbind="tolerance" data-pid="${p.id}" data-rid="${r.id}" style="--p:${r.tolerance / 40 * 100}%"></div>
        </div>
      </div>
      <div class="row wrap" style="gap:6px">
        <button class="btn sm" data-action="region-mark" data-pid="${p.id}" data-rid="${r.id}">${icon("scan")} Marcar de novo</button>
        <button class="btn sm" data-action="region-capture" data-pid="${p.id}" data-rid="${r.id}">${icon("rec")} Recapturar</button>
        <button class="btn sm ghost" data-action="region-test" data-pid="${p.id}" data-rid="${r.id}">${icon("eye")} Testar</button>
        <span class="spacer"></span>
        <button class="icon-btn sm danger" data-action="region-del" data-pid="${p.id}" data-rid="${r.id}" title="Apagar região">${icon("trash")}</button>
      </div>
    </div>`).join("")}</div>`;
}
function bindingCard(p, b) {
  const t = b.action.type;
  const mode = MODES.find((m) => m[0] === b.mode)?.[1];
  const body = t === "remap"
    ? `<div class="row wrap" style="gap:8px">${keycaps(b.trigger)} ${icon("arrow", "faint")} ${codecaps(b.action.to)}</div>`
    : `<div class="summary">${summary(b)}</div>`;
  return `
    <div class="card bcard interactive ${b.enabled ? "" : "off"}" data-action="edit" data-pid="${p.id}" data-bid="${b.id}">
      <span class="running-badge" data-live="badge" data-bid="${b.id}"></span>
      <div class="row"><span class="type ${t}">${icon(TYPES[t].icon)}</span>
        <div class="stack" style="gap:2px;min-width:0"><div class="h3">${esc(b.name)}</div><div class="small faint">${TYPES[t].name}${t === "remap" || t === "command" ? "" : ` · ${mode}`}</div></div></div>
      ${body}
      <div class="foot">
        ${t === "remap" ? "" : keycaps(b.trigger)}
        <span class="spacer"></span>
        <label class="switch" data-stop><input type="checkbox" ${b.enabled ? "checked" : ""} data-action="binding-enabled" data-pid="${p.id}" data-bid="${b.id}"><span class="track"></span><span class="knob"></span></label>
        ${t === "remap" ? "" : `<span data-live="play" data-pid="${p.id}" data-bid="${b.id}" data-stop></span>`}
      </div>
    </div>`;
}

/* ================================================================ library */
function viewLibrary() {
  const rows = [];
  for (const p of profiles()) for (const b of p.bindings) rows.push({ p, b });
  return `
    <div class="eyebrow">Biblioteca</div>
    <h1 class="h1" style="font-size:34px">Todos os <span class="grad">atalhos.</span></h1>
    <p class="lead">Cada atalho de cada perfil num lugar só. Também aparecem no SUPER + K, na aba Jogos.</p>
    <div class="card" style="margin-top:22px;padding:6px 20px">
      ${rows.length ? rows.map(({ p, b }) => `
        <div class="setrow" style="cursor:pointer" data-action="edit" data-pid="${p.id}" data-bid="${b.id}">
          <span class="type ${b.action.type}" style="width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:var(--soft2);color:var(--accent)">${icon(TYPES[b.action.type].icon)}</span>
          <div class="txt"><div class="h3">${esc(b.name)}</div><div class="hint">${esc(p.name)} · ${TYPES[b.action.type].name}</div></div>
          ${b.action.type === "remap" ? `<div class="row">${keycaps(b.trigger)} ${icon("arrow", "faint")} ${codecaps(b.action.to)}</div>` : keycaps(b.trigger)}
          <span data-live="badge" data-bid="${b.id}"></span>
          ${b.enabled ? "" : '<span class="chip">desligado</span>'}
        </div>`).join("") : `<div class="empty" style="margin:14px 0">Nenhum atalho ainda.</div>`}
    </div>`;
}

/* ================================================================ settings */
function viewSettings() {
  const s = S.cfg.settings;
  const st = S.st || {};
  const capBtn = (target, trig) => `<button class="capture ${S.capturing === target ? "listening" : ""}" data-action="capture" data-target="${target}">${keycaps(trig)}<span class="go">${S.capturing === target ? "aperte…" : icon("edit")}</span></button>`;
  return `
    <div class="eyebrow">Ajustes</div>
    <h1 class="h1" style="font-size:34px">Do seu <span class="grad">jeito.</span></h1>
    <p class="lead">Tudo aqui salva na hora. O motor roda como serviço do sistema e sobrevive a fechar esta janela.</p>

    <div class="section-head"><h2 class="h2">${icon("shield")} Segurança</h2></div>
    <div class="card" style="padding:4px 22px">
      <div class="setrow"><div class="txt"><div class="h3">Tecla de pânico</div><div class="hint">Para tudo que está rodando e solta todas as teclas. Funciona sempre, até no meio da gravação.</div></div>
        ${capBtn("settings.panic", s.panic)}
        <div class="field" style="width:120px"><label>Segurar (ms)</label><input class="input num-input" type="number" min="0" max="5000" step="100" value="${s.panic.hold_ms}" data-sbind="panic.hold_ms" data-type="int"></div></div>
      <div class="setrow"><div class="txt"><div class="h3">Parar a gravação</div><div class="hint">A tecla que encerra o gravador de macros (ela mesma não entra na gravação).</div></div>${capBtn("settings.record_stop", s.record_stop)}</div>
      <div class="setrow"><div class="txt"><div class="h3">Contagem antes de começar</div><div class="hint">Quando você inicia pelo ${icon("play")} desta janela: tempo pra levar o mouse até o jogo.</div></div>
        <div class="row"><input type="range" class="range" style="width:160px;--p:${s.countdown / 10 * 100}%" min="0" max="10" value="${s.countdown}" data-sbind="countdown" data-type="int" data-out="countdown-out"><b id="countdown-out" style="width:40px;text-align:right">${s.countdown} s</b></div></div>
    </div>

    <div class="section-head"><h2 class="h2">${icon("globe")} Perfil global</h2></div>
    <div class="card" style="padding:4px 22px">
      <div class="setrow"><div class="txt"><div class="h3">“Qualquer janela” ligado</div><div class="hint">Os atalhos do perfil global valem fora dos jogos. Desligado, o InputForge só escuta o teclado com um jogo de perfil em foco.</div></div>
        <label class="switch"><input type="checkbox" ${s.global_enabled ? "checked" : ""} data-sbind="global_enabled" data-type="bool"><span class="track"></span><span class="knob"></span></label></div>
    </div>

    <div class="section-head"><h2 class="h2">${icon("monitor")} HUD no jogo</h2></div>
    <div class="card" style="padding:4px 22px">
      <div class="setrow"><div class="txt"><div class="h3">Mostrar o HUD</div><div class="hint">Pílulas discretas por cima do jogo com o que está rodando, a contagem e a gravação.</div></div>
        <label class="switch"><input type="checkbox" ${s.hud.enabled ? "checked" : ""} data-sbind="hud.enabled" data-type="bool"><span class="track"></span><span class="knob"></span></label></div>
      <div class="setrow"><div class="txt"><div class="h3">Posição</div><div class="hint">Canto da tela do monitor em foco.</div></div>
        <div class="hud-pos">${["top-left", "top", "top-right", "bottom-left", "bottom", "bottom-right"].map((pos) => `<button class="${s.hud.position === pos ? "on" : ""}" data-action="hud-pos" data-value="${pos}" title="${pos}"><i></i></button>`).join("")}</div></div>
      <div class="setrow"><div class="txt"><div class="h3">Avisar o perfil ao entrar no jogo</div><div class="hint">“Perfil Roblox ativo” por 2 segundos quando o jogo ganha foco.</div></div>
        <label class="switch"><input type="checkbox" ${s.hud.announce ? "checked" : ""} data-sbind="hud.announce" data-type="bool"><span class="track"></span><span class="knob"></span></label></div>
    </div>

    <div class="section-head"><h2 class="h2">${icon("bulb")} Integrações</h2></div>
    <div class="card" style="padding:4px 22px">
      ${S.env.aurum ? `<div class="setrow"><div class="txt"><div class="h3">Aurum V60 acende</div><div class="hint">A tecla do atalho brilha no teclado enquanto ele roda (F1–F12 = a fileira de números, como no Fn). Usa o orçamento diário de escritas do OpenAurum e só aparece com as cenas dele ativas.</div></div>
        <label class="switch"><input type="checkbox" ${s.aurum.enabled ? "checked" : ""} data-sbind="aurum.enabled" data-type="bool"><span class="track"></span><span class="knob"></span></label></div>` : ""}
      ${S.env.calos ? `<div class="setrow"><div class="txt"><div class="h3">Waybar, SUPER + K e Game Mode</div><div class="hint">Ícone na waybar enquanto algo roda (clique abre, direito para tudo). Os atalhos aparecem em SUPER + K › Jogos. No jogo, SUPER + CTRL + A abre o painel rápido por cima dele (E abre este editor).</div></div>` : ""}
      ${!S.env.aurum && !S.env.calos ? `<div class="setrow"><div class="txt"><div class="h3">Waybar</div><div class="hint">Um módulo custom com <code>"exec": "inputforge waybar"</code>, <code>"signal": 14</code> e <code>"return-type": "json"</code> mostra um ícone enquanto algo roda.</div></div></div>` : ""}
        <span class="chip accent">${icon("check")} sempre</span></div>
    </div>

    <div class="section-head"><h2 class="h2">${icon("terminal")} Motor</h2></div>
    <div class="card">
      <dl class="kv">
        <dt>Estado</dt><dd>${S.engineUp ? "online" : "parado"} · ${st.listening ? "escutando o teclado" : "sem ler o teclado"}</dd>
        <dt>Dispositivos presos</dt><dd>${(st.grabbed || []).length ? st.grabbed.map(esc).join(", ") : "nenhum"}</dd>
        <dt>Janela em foco</dt><dd>${st.focus ? esc(st.focus.class) : "—"}</dd>
        <dt>Config</dt><dd>${esc(S.env.config || "~/.config/inputforge/config.json")}</dd>
        <dt>Serviço</dt><dd>${esc(S.env.unit || "inputforge.service")} (systemctl --user)</dd>
        <dt>CLI</dt><dd>inputforge toggle "Nome do atalho" · stop · panic · status</dd>
      </dl>
    </div>`;
}

/* ================================================================ drawer */
function openDrawer(pid, bid) {
  S.drawer = { pid, bid };
  renderDrawer();
  $("#drawer").classList.add("open");
  $("#scrim").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
}
function closeDrawer(rerender = true) {
  if (!S.drawer) return;
  S.drawer = null;
  $("#drawer").classList.remove("open");
  $("#scrim").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  if (rerender) renderView();
}
function drawerBinding() {
  return S.drawer && findBinding(S.drawer.pid, S.drawer.bid);
}
function renderDrawer() {
  const b = drawerBinding();
  if (!b) return closeDrawer();
  const t = b.action.type;
  const body = $("#drawer .drawer-body");
  const scroll = body ? body.scrollTop : 0;
  $("#drawer").innerHTML = `
    <div class="drawer-head">
      <span class="type ${t}" style="width:42px;height:42px;border-radius:13px;display:grid;place-items:center;background:var(--grad);color:var(--on-accent);flex:none">${icon(TYPES[t].icon)}</span>
      <div class="stack" style="gap:2px;flex:1;min-width:0">
        <div class="eyebrow">${TYPES[t].name} · ${esc(findProfile(S.drawer.pid).name)}</div>
        <input class="name-input" value="${esc(b.name)}" data-bind="name" spellcheck="false">
      </div>
      <label class="switch" title="Ligado"><input type="checkbox" ${b.enabled ? "checked" : ""} data-bind="enabled" data-type="bool" data-rerender><span class="track"></span><span class="knob"></span></label>
      <button class="icon-btn" data-action="close-drawer">${icon("x")}</button>
    </div>
    <div class="drawer-body">
      ${triggerGroup(b)}
      ${t === "clicker" ? clickerGroups(b) : t === "macro" ? macroGroups(b) : t === "remap" ? remapGroups(b) : commandGroups(b)}
    </div>
    <div class="drawer-foot">
      <button class="btn ghost danger" data-action="delete-binding">${icon("trash")} Apagar</button>
      <button class="btn ghost" data-action="dup-binding">${icon("copy")} Duplicar</button>
      <span class="spacer"></span>
      ${t === "remap" ? "" : `<span data-live="play" data-pid="${S.drawer.pid}" data-bid="${b.id}"></span>`}
      <button class="btn primary" data-action="close-drawer">${icon("check")} Concluir</button>
    </div>`;
  $("#drawer .drawer-body").scrollTop = scroll;
  renderLive();
}
function capButton(target, inner) {
  const on = S.capturing === target;
  return `<button class="capture ${on ? "listening" : ""}" data-action="capture" data-target="${target}">${inner}<span class="go">${on ? "aperte a tecla…" : `${icon("keyboard")} trocar`}</span></button>`;
}
function regionOptions(pid, selected) {
  const regs = regionsFor(pid);
  if (!regs.length) return `<option value="">(nenhuma região)</option>`;
  return regs.map((r) => `<option value="${r.id}" ${r.id === selected ? "selected" : ""}>${esc(r.name)}${r.ref ? "" : " (sem referência)"}</option>`).join("");
}
function condOptions(selected, short = false) {
  return CONDS.map(([v, l]) => `<option value="${v}" ${v === selected ? "selected" : ""}>${short ? { match: "igual à ref.", differ: "diferente", change: "mudou" }[v] : l}</option>`).join("");
}
const SCREEN_MODES = { toggle: "Cada vez que a tela fica assim, liga/desliga", hold: "Roda enquanto a tela estiver assim e para quando mudar", once: "Roda quando a tela fica assim" };
function triggerGroup(b) {
  const t = b.action.type;
  const tr = b.trigger;
  const screen = t !== "remap" && tr.kind === "screen";
  const kindSeg = t === "remap" ? "" : `<div class="seg"><button class="${screen ? "" : "on"}" data-set="trigger.kind" data-value='"key"'>${icon("keyboard")} Tecla</button><button class="${screen ? "on" : ""}" data-set="trigger.kind" data-value='"screen"'>${icon("eye")} Tela</button></div>`;
  if (screen) {
    const regs = regionsFor(S.drawer.pid);
    return `<div class="group">
    <div class="group-title">${icon("zap")} Gatilho</div>
    ${kindSeg}
    ${regs.length ? `<div class="cols">
      <div class="field"><label>Região</label><select class="select" data-bind="trigger.region">${regionOptions(S.drawer.pid, tr.region)}</select></div>
      <div class="field"><label>Dispara quando ficar</label><select class="select" data-bind="trigger.cond">${condOptions(tr.cond)}</select></div>
    </div>` : `<div class="hint">Este perfil ainda não tem regiões da tela. Crie uma na página do perfil (Regiões da tela → Nova região).</div>`}
    ${t === "command" ? "" : `<div class="field"><label>Modo</label><div class="seg full">${MODES.map(([v, n]) => `<button class="${b.mode === v ? "on" : ""}" data-set="mode" data-value='"${v}"'>${n}</button>`).join("")}</div>
      <div class="hint">${SCREEN_MODES[b.mode]}. Só vale com o perfil ativo (o jogo em foco).</div></div>`}
  </div>`;
  }
  return `<div class="group">
    <div class="group-title">${icon("zap")} ${t === "remap" ? "Tecla de origem" : "Gatilho"}</div>
    ${kindSeg}
    <div class="row wrap" style="gap:14px">${capButton("trigger", keycaps(tr, true))}
      <div class="hint" style="flex:1;min-width:180px">${t === "remap" ? "A tecla ou botão que vai virar outra coisa. Ela some pro jogo enquanto o perfil está ativo." : "Teclado ou mouse (botões laterais são ótimos). Segure Ctrl/Shift/Alt junto pra um combo. Clique esquerdo cancela."}</div></div>
    ${t === "remap" || t === "command" ? "" : `
    <div class="field"><label>Modo</label><div class="seg full">${MODES.map(([v, n, d]) => `<button class="${b.mode === v ? "on" : ""}" data-set="mode" data-value='"${v}"' title="${d}">${n}</button>`).join("")}</div>
      <div class="hint">${MODES.find((m) => m[0] === b.mode)[2]}.</div></div>`}
    ${t === "remap" ? "" : `
    <div class="cols">
      <div class="setrow" style="padding:4px 0;border:none"><div class="txt"><div class="h3" style="font-size:13px">Bloquear a tecla original</div><div class="hint">O jogo não recebe ${esc(label(tr.key))}.</div></div>
        <label class="switch"><input type="checkbox" ${tr.block ? "checked" : ""} data-bind="trigger.block" data-type="bool"><span class="track"></span><span class="knob"></span></label></div>
      <div class="field"><label>Segurar antes de disparar (ms)</label><input class="input" type="number" min="0" max="5000" step="50" value="${tr.hold_ms}" data-bind="trigger.hold_ms" data-type="int"></div>
    </div>`}
  </div>`;
}
function clickerGroups(b) {
  const a = b.action;
  const isMouse = MOUSE.some((m) => m[0] === a.target);
  const cps = a.rate.unit === "cps";
  const max = cps ? 100 : 2000;
  return `
  <div class="group">
    <div class="group-title">${icon("click")} O que clicar</div>
    <div class="seg full">${MOUSE.map(([c, n]) => `<button class="${a.target === c ? "on" : ""}" data-set="action.target" data-value='"${c}"'>${n}</button>`).join("")}
      <button class="${isMouse ? "" : "on"}" data-action="capture" data-target="clicker-target">${icon("keyboard")} ${isMouse ? "Tecla…" : esc(label(a.target))}</button></div>
    ${isMouse ? "" : `<div class="hint">Auto-tecla: toca ${esc(label(a.target))} no ritmo abaixo.</div>`}
  </div>
  <div class="group">
    <div class="group-title">${icon("zap")} Velocidade</div>
    <div class="row" style="gap:14px">
      <div class="seg"><button class="${cps ? "on" : ""}" data-action="rate-unit" data-value="cps">CPS</button><button class="${cps ? "" : "on"}" data-action="rate-unit" data-value="ms">Intervalo</button></div>
      <input class="input num-input" type="number" step="${cps ? "0.5" : "1"}" min="${cps ? "0.01" : "1"}" value="${a.rate.value}" data-bind="action.rate.value" data-type="float" data-sync="rate">
      <span class="faint">${cps ? "cliques por segundo" : "ms entre cliques"}</span>
    </div>
    <input type="range" class="range" min="${cps ? 1 : 10}" max="${max}" step="1" value="${Math.min(max, a.rate.value)}" data-bind="action.rate.value" data-type="float" data-sync="rate" style="--p:${Math.min(100, a.rate.value / max * 100)}%">
    <div class="cols">
      <div class="field"><label>Duração de cada clique (ms)</label><input class="input" type="number" min="1" max="10000" value="${a.hold_ms}" data-bind="action.hold_ms" data-type="int"></div>
      <div class="field"><label>Variação humana <b id="jit-out">±${a.jitter}%</b></label><input type="range" class="range" min="0" max="60" value="${a.jitter}" data-bind="action.jitter" data-type="int" data-out="jit-out" data-fmt="±{}%" style="--p:${a.jitter / 60 * 100}%"></div>
    </div>
    <div class="setrow" style="padding:4px 0;border:none"><div class="txt"><div class="h3" style="font-size:13px">Clique duplo</div><div class="hint">Cada batida vira dois cliques.</div></div>
      <label class="switch"><input type="checkbox" ${a.double ? "checked" : ""} data-bind="action.double" data-type="bool"><span class="track"></span><span class="knob"></span></label></div>
  </div>
  <div class="group">
    <div class="group-title">${icon("target")} Onde</div>
    <div class="seg full"><button class="${a.where === "cursor" ? "on" : ""}" data-set="action.where" data-value='"cursor"'>${icon("click")} No cursor</button><button class="${a.where === "points" ? "on" : ""}" data-set="action.where" data-value='"points"'>${icon("crosshair")} Em pontos fixos</button></div>
    ${a.where === "points" ? `
      <div class="points">${a.points.map((pt, i) => `<span class="point"><b>${i + 1}</b>${pt.x}, ${pt.y}<button class="icon-btn sm" data-action="del-point" data-i="${i}">${icon("x")}</button></span>`).join("") || '<span class="hint">Nenhum ponto ainda.</span>'}</div>
      <div class="row wrap"><button class="btn" data-action="pick-points">${icon("crosshair")} Marcar pontos na tela</button>
        ${a.points.length ? `<button class="btn ghost" data-action="clear-points">Limpar</button>` : ""}
        <span class="spacer"></span>
        <div class="field" style="width:150px"><label>Espalhar (± px)</label><input class="input" type="number" min="0" max="200" value="${a.point_jitter}" data-bind="action.point_jitter" data-type="int"></div></div>
      <div class="hint">Clique em cada ponto (o clique não chega no app), botão direito termina. Os pontos se revezam a cada clique. Em jogos de mira travada (câmera), use “No cursor”.</div>` : ""}
  </div>
  <div class="group">
    <div class="group-title">${icon("clock")} Limite</div>
    <div class="row wrap" style="gap:14px">
      <div class="seg">${[["none", "Sem limite"], ["count", "Nº de cliques"], ["time", "Tempo"]].map(([v, n]) => `<button class="${a.limit.kind === v ? "on" : ""}" data-set="action.limit.kind" data-value='"${v}"'>${n}</button>`).join("")}</div>
      ${a.limit.kind === "none" ? "" : `<input class="input num-input" type="number" min="0" value="${a.limit.value}" data-bind="action.limit.value" data-type="float"><span class="faint">${a.limit.kind === "count" ? "cliques" : "segundos"}</span>`}
    </div>
  </div>`;
}
function macroGroups(b) {
  const a = b.action;
  const rec = S.recordFor && S.recordFor.bid === b.id;
  return `
  <div class="group">
    <div class="group-title">${icon("rec")} Gravar</div>
    <div class="row wrap" style="gap:10px">
      <button class="btn ${rec ? "danger" : "primary"}" data-action="${rec ? "record-stop" : "record"}">${icon(rec ? "stop" : "rec")} ${rec ? "Parar gravação" : "Gravar jogando"}</button>
      <select class="select" style="width:auto" id="rec-motion">
        <option value="none">Sem movimento do mouse</option>
        <option value="relative">Movimento relativo (câmera 3D)</option>
        <option value="absolute">Posição dos cliques (menus)</option>
      </select>
      <select class="select" style="width:auto" id="rec-mode">
        <option value="replace">Substituir passos</option>
        <option value="append">Adicionar no fim</option>
      </select>
    </div>
    <div class="hint">Depois da contagem, tudo que você apertar vira passo, com os tempos. ${keycaps(S.cfg.settings.record_stop)} termina.</div>
  </div>
  <div class="group">
    <div class="group-title">${icon("macro")} Passos <span class="faint" style="letter-spacing:0;text-transform:none;font-weight:600">· ${a.steps.length}</span></div>
    <div class="timeline" id="timeline">${a.steps.length ? stepRows(a.steps) : `<div class="empty">Grave jogando ou adicione passos abaixo.</div>`}</div>
    <div class="addbar">${Object.entries(STEP).map(([k, v]) => `<button class="btn sm" data-action="add-step" data-type="${k}">${icon(v.icon)} ${v.name}</button>`).join("")}</div>
  </div>
  <div class="group">
    <div class="group-title">${icon("infinity")} Repetição</div>
    <div class="row wrap" style="gap:14px">
      <div class="seg"><button class="${a.repeat === 0 ? "" : "on"}" data-action="repeat-kind" data-value="n">Vezes</button><button class="${a.repeat === 0 ? "on" : ""}" data-action="repeat-kind" data-value="inf">${icon("infinity")} Até parar</button></div>
      ${a.repeat === 0 ? "" : `<input class="input num-input" type="number" min="1" value="${a.repeat}" data-bind="action.repeat" data-type="int">`}
      <div class="field" style="width:170px"><label>Pausa entre voltas (ms)</label><input class="input" type="number" min="0" value="${a.gap_ms}" data-bind="action.gap_ms" data-type="int"></div>
    </div>
    <div class="field"><label>Velocidade <b id="speed-out">${fmt(a.speed, 2)}×</b></label>
      <input type="range" class="range" min="10" max="400" value="${Math.round(a.speed * 100)}" data-bind="action.speed" data-type="pct" data-out="speed-out" data-fmt="{}×" style="--p:${(a.speed * 100 - 10) / 390 * 100}%"></div>
    ${b.mode === "hold" && a.repeat === 1 ? `<div class="hint">No modo Segurar, “1 vez” repete enquanto você segura.</div>` : ""}
  </div>`;
}
function stepRows(steps) {
  // steps an "if" can skip get indented under it, so the "senão pula N" reads as a block
  let covered = 0;
  return steps.map((s, i) => {
    const html = stepRow(s, i, covered > 0);
    covered = s.t === "if_screen" ? s.skip : Math.max(0, covered - 1);
    return html;
  }).join("");
}
function stepRow(s, i, nested = false) {
  const meta = STEP[s.t];
  let body = "";
  const n = (field, v, extra = "") => `<input class="input n" type="number" value="${v}" data-sbind="${field}" ${extra}>`;
  if (s.t === "key") {
    body = `<button class="capture" style="min-height:34px;padding:3px 3px 3px 5px" data-action="capture" data-target="step:${i}">${codecaps(s.codes)}</button>
      <select class="select" data-sbind="act">${[["tap", "Tocar"], ["down", "Pressionar"], ["up", "Soltar"]].map(([v, l]) => `<option value="${v}" ${s.act === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      ${s.act === "tap" ? `${n("hold_ms", s.hold_ms, 'min="0"')}<span class="unit">ms</span>` : ""}`;
  } else if (s.t === "button") {
    body = `<select class="select" data-sbind="codes">${MOUSE.map(([c, l]) => `<option value="${c}" ${s.codes[0] === c ? "selected" : ""}>${l}</option>`).join("")}</select>
      <select class="select" data-sbind="act">${[["tap", "Clique"], ["double", "Duplo"], ["down", "Pressionar"], ["up", "Soltar"]].map(([v, l]) => `<option value="${v}" ${s.act === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      ${s.act === "tap" || s.act === "double" ? `${n("hold_ms", s.hold_ms, 'min="0"')}<span class="unit">ms</span>` : ""}`;
  } else if (s.t === "wait") {
    body = `${n("ms", s.ms, 'min="0"')}<span class="unit">ms</span><span class="unit">±</span>${n("rand_ms", s.rand_ms, 'min="0"')}<span class="unit">aleatório</span>`;
  } else if (s.t === "move") {
    body = `<span class="unit">x</span>${n("dx", s.dx)}<span class="unit">y</span>${n("dy", s.dy)}<span class="unit">em</span>${n("ms", s.ms, 'min="0"')}<span class="unit">ms</span>`;
  } else if (s.t === "moveto") {
    body = `<span class="unit">x</span>${n("x", s.x)}<span class="unit">y</span>${n("y", s.y)}<button class="btn sm ghost" data-action="pick-step" data-i="${i}">${icon("crosshair")} marcar</button>`;
  } else if (s.t === "scroll") {
    body = `${n("dy", s.dy)}<span class="unit">notches (+ cima, − baixo)</span>`;
  } else if (s.t === "text") {
    body = `<input class="input w" value="${esc(s.value)}" data-sbind="value" placeholder="gg wp" spellcheck="false">`;
  } else if (s.t === "command") {
    body = `<input class="input w mono" value="${esc(s.cmd)}" data-sbind="cmd" placeholder="notify-send oi" spellcheck="false">`;
  } else if (s.t === "wait_screen") {
    body = `<span class="unit">até</span><select class="select" data-sbind="region">${regionOptions(S.drawer.pid, s.region)}</select>
      <span class="unit">ficar</span><select class="select" data-sbind="cond">${condOptions(s.cond, true)}</select>
      <span class="unit">máx.</span>${n("timeout_ms", s.timeout_ms, 'min="0" title="0 = sem limite"')}<span class="unit">ms, depois</span>
      <select class="select" data-sbind="on_timeout"><option value="continue" ${s.on_timeout === "continue" ? "selected" : ""}>segue</option><option value="stop" ${s.on_timeout === "stop" ? "selected" : ""}>para a macro</option></select>`;
  } else if (s.t === "if_screen") {
    body = `<span class="unit">se</span><select class="select" data-sbind="region">${regionOptions(S.drawer.pid, s.region)}</select>
      <select class="select" data-sbind="cond">${condOptions(s.cond, true)}</select>
      <span class="unit">senão pula</span>${n("skip", s.skip, 'min="1" max="100" style="width:52px"')}`;
  }
  return `<div class="step ${s.t === "if_screen" ? "cond" : ""} ${nested ? "nested" : ""}" data-i="${i}">
    <span class="grip" data-grip title="Arrastar">${icon("grip")}</span>
    <span class="sicon ${s.t === "wait" ? "wait" : s.t.endsWith("_screen") ? "eye" : ""}" title="${meta.name}">${icon(meta.icon)}</span>
    <div class="sbody">${body}</div>
    <div class="sactions"><button class="icon-btn sm" data-action="dup-step" data-i="${i}" title="Duplicar">${icon("copy")}</button><button class="icon-btn sm danger" data-action="del-step" data-i="${i}" title="Apagar">${icon("trash")}</button></div>
  </div>`;
}
function remapGroups(b) {
  const a = b.action;
  return `
  <div class="group">
    <div class="group-title">${icon("remap")} Vira</div>
    <div class="row wrap" style="gap:14px">
      ${keycaps(b.trigger, true)} ${icon("arrow", "faint")}
      ${capButton("remap-to", a.to.length ? `<span class="keys">${a.to.map((c) => `<span class="kbd big">${esc(label(c))}</span>`).join('<span class="plus">+</span>')}</span>` : `<span class="kbd-none">nada (desativada)</span>`)}
    </div>
    <div class="row wrap" style="gap:6px">
      ${MOUSE.map(([c, n]) => `<button class="btn sm ${a.to.length === 1 && a.to[0] === c ? "primary" : ""}" data-action="remap-to" data-value="${c}">${icon("mouse")} ${n}</button>`).join("")}
      <button class="btn sm ${a.to.length ? "" : "primary"}" data-action="remap-to" data-value="">${icon("x")} Desativar tecla</button>
    </div>
    <div class="hint">Combos funcionam: capture “Ctrl + C” e a tecla manda os dois. Enquanto o perfil está ativo o teclado (ou mouse) passa pelo InputForge, que repassa todo o resto sem mudar nada.</div>
  </div>`;
}
function commandGroups(b) {
  return `
  <div class="group">
    <div class="group-title">${icon("terminal")} Comando</div>
    <textarea class="textarea mono" rows="4" data-bind="action.cmd" spellcheck="false" placeholder="notify-send 'InputForge' 'olá do atalho'">${esc(b.action.cmd)}</textarea>
    <div class="hint">Roda com sh -c, sem esperar terminar, com o mesmo PATH da sua sessão (hyprctl, notify-send…).</div>
  </div>`;
}

/* ================================================================ modals */
function modal(html) {
  const m = $("#modal");
  if (!html) { m.className = "modal-wrap"; m.innerHTML = ""; return; }
  m.innerHTML = `<div class="card modal">${html}</div>`;
  m.className = "modal-wrap open";
}
function typePicker(pid) {
  modal(`<div class="row"><div class="stack" style="gap:4px;flex:1"><div class="eyebrow">Novo atalho</div><h2 class="h2">O que ele faz?</h2></div>
      <button class="icon-btn" data-action="close-modal">${icon("x")}</button></div>
    <div class="types">${Object.entries(TYPES).map(([k, v]) => `
      <button class="type-opt" data-action="create-binding" data-pid="${pid}" data-type="${k}">
        <span class="type">${icon(v.icon)}</span><span><div class="t">${v.name}</div><div class="d">${v.desc}</div></span></button>`).join("")}</div>`);
}
async function windowPicker(pid) {
  modal(`<div class="row"><div class="stack" style="gap:4px;flex:1"><div class="eyebrow">${pid ? "Regra do perfil" : "Novo perfil"}</div><h2 class="h2">Qual janela é o jogo?</h2></div>
      <button class="icon-btn" data-action="close-modal">${icon("x")}</button></div>
    <p class="muted small" style="margin:10px 0 0">Abra o jogo antes. A regra usa a classe da janela (o que o Hyprland chama de class).</p>
    <div class="stack" id="win-list" style="margin-top:16px;gap:8px"><div class="empty">Procurando janelas…</div></div>
    ${pid ? "" : `<div class="row" style="margin-top:16px"><span class="spacer"></span><button class="btn" data-action="create-blank">${icon("plus")} Perfil em branco</button></div>`}`);
  let res;
  try { res = await Bridge.call("windows"); } catch { res = { clients: [] }; }
  const seen = new Set();
  const wins = (res.clients || []).filter((w) => !seen.has(w.class) && seen.add(w.class));
  wins.sort((a, b) => (DESKTOP.test(a.class) ? 1 : 0) - (DESKTOP.test(b.class) ? 1 : 0));
  $("#win-list").innerHTML = wins.length ? wins.map((w) => `
    <button class="type-opt" data-action="${pid ? "set-window" : "create-from"}" data-pid="${pid || ""}" data-class="${esc(w.class)}" data-title="${esc(w.title)}">
      <span class="avatar" style="--h: hsl(${hue(w.class)} 70% 58%)">${icon(DESKTOP.test(w.class) ? "monitor" : "gamepad")}</span>
      <span style="min-width:0"><div class="t">${esc(prettyClass(w.class, w.title))}</div><div class="d" style="font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(w.class)} · ${esc((w.title || "").slice(0, 60))}</div></span>
    </button>`).join("") : `<div class="empty">Nenhuma janela aberta.</div>`;
}

/* ================================================================ actions */
function newBinding(type) {
  const base = {
    id: uid(), name: TYPES[type].name, enabled: true, mode: "toggle",
    trigger: { kind: "key", key: type === "remap" ? "KEY_CAPSLOCK" : "KEY_F8", mods: [], hold_ms: 0, block: type === "remap", region: "", cond: "match" },
    action: { type },
  };
  if (type === "clicker") Object.assign(base.action, { target: "BTN_LEFT", rate: { unit: "cps", value: 15 }, hold_ms: 12, double: false, jitter: 0, where: "cursor", points: [], point_jitter: 0, limit: { kind: "none", value: 0 } });
  if (type === "macro") Object.assign(base.action, { steps: [], repeat: 1, speed: 1, gap_ms: 0 });
  if (type === "remap") Object.assign(base.action, { to: ["KEY_LEFTSHIFT"] });
  if (type === "command") { base.mode = "once"; Object.assign(base.action, { cmd: "" }); }
  return base;
}
function newStep(t) {
  const s = { t, id: uid() };
  if (t === "key") Object.assign(s, { codes: ["KEY_E"], act: "tap", hold_ms: 20 });
  if (t === "button") Object.assign(s, { codes: ["BTN_LEFT"], act: "tap", hold_ms: 20 });
  if (t === "wait") Object.assign(s, { ms: 100, rand_ms: 0 });
  if (t === "move") Object.assign(s, { dx: 0, dy: 0, ms: 0 });
  if (t === "moveto") Object.assign(s, { x: 0, y: 0 });
  if (t === "scroll") Object.assign(s, { dy: -1, dx: 0 });
  if (t === "text") Object.assign(s, { value: "" });
  if (t === "command") Object.assign(s, { cmd: "" });
  const firstRegion = (regionsFor(S.drawer ? S.drawer.pid : "global")[0] || {}).id || "";
  if (t === "wait_screen") Object.assign(s, { region: firstRegion, cond: "match", timeout_ms: 0, on_timeout: "continue" });
  if (t === "if_screen") Object.assign(s, { region: firstRegion, cond: "match", skip: 1 });
  return s;
}
function createProfile(cls, title) {
  const p = {
    id: uid(), name: prettyClass(cls, title), enabled: true, icon: "",
    match: { class: cls ? `^${reEscape(cls)}$` : "", title: "" }, on_blur: "pause", bindings: [],
  };
  profiles().push(p);
  save(true);
  modal(null);
  go(`profile:${p.id}`);
  toast(`Perfil ${p.name} criado`);
}

async function capture(target) {
  if (S.capturing) { Bridge.call("capture_cancel").catch(() => {}); return; }
  S.capturing = target;
  rerenderForCapture();
  let res = null;
  try { res = await Bridge.call("capture"); } catch (e) { toast(e.message, "err"); }
  S.capturing = null;
  if (res && res.key === "BTN_LEFT" && !res.mods.length) res = null; // left click = cancel
  if (res) applyCapture(target, res);
  rerenderForCapture();
}
function rerenderForCapture() {
  if (S.drawer) renderDrawer();
  else renderView();
}
function applyCapture(target, res) {
  const trig = { key: res.key, mods: res.mods };
  const codes = [...res.mods.map((m) => MOD_CODE[m]), res.key];
  if (target === "quick") {
    const b = quickBinding();
    Object.assign(b.trigger, trig);
  } else if (target.startsWith("settings.")) {
    Object.assign(S.cfg.settings[target.slice(9)], trig);
  } else {
    const b = drawerBinding();
    if (!b) return;
    if (target === "trigger") Object.assign(b.trigger, b.action.type === "remap" ? { key: res.key, mods: [] } : trig);
    else if (target === "clicker-target") b.action.target = res.key;
    else if (target === "remap-to") b.action.to = codes;
    else if (target.startsWith("step:")) b.action.steps[+target.slice(5)].codes = codes;
  }
  save(true);
}

const ACTIONS = {
  "close": () => Bridge.call("close").catch(() => window.close()),
  "panic": async () => { await Bridge.call("panic"); toast("Tudo parado"); },
  "engine-start": async () => { await Bridge.call("engine_start"); },
  "new-profile": () => windowPicker(null),
  "create-from": (el) => createProfile(el.dataset.class, el.dataset.title),
  "create-blank": () => createProfile("", ""),
  "close-modal": () => modal(null),
  "pick-window": (el) => windowPicker(el.dataset.pid),
  "set-window": (el) => {
    const p = findProfile(el.dataset.pid);
    p.match.class = `^${reEscape(el.dataset.class)}$`;
    save(true); modal(null); renderView();
  },
  "delete-profile": (el) => {
    const p = findProfile(el.dataset.pid);
    if (!confirmTwice(el, "Apagar o perfil?")) return;
    S.cfg.profiles = profiles().filter((x) => x.id !== p.id);
    save(true); go("home"); toast(`Perfil ${p.name} apagado`);
  },
  "profile-enabled": (el) => {
    if (el.dataset.pid === "global") S.cfg.settings.global_enabled = el.checked;
    else findProfile(el.dataset.pid).enabled = el.checked;
    save(); renderAll();
  },
  "on-blur": (el) => { findProfile(S.route.id).on_blur = el.dataset.value; save(); renderView(); },
  "new-binding": (el) => typePicker(el.dataset.pid),
  "create-binding": (el) => {
    const b = newBinding(el.dataset.type);
    findProfile(el.dataset.pid).bindings.push(b);
    save(true); modal(null); renderView(); renderProfiles(); openDrawer(el.dataset.pid, b.id);
  },
  "edit": (el) => openDrawer(el.dataset.pid, el.dataset.bid),
  "close-drawer": () => closeDrawer(),
  "binding-enabled": (el) => { findBinding(el.dataset.pid, el.dataset.bid).enabled = el.checked; save(); renderView(); },
  "run": async (el) => {
    if (!el.dataset.bid) return;
    try {
      const started = await Bridge.call("run", { binding: el.dataset.bid });
      // started (not stopped): the window gets out of the way and the game comes back;
      // the countdown shows on the HUD
      if (started && Bridge.native) Bridge.call("return_to_game").catch(() => {});
    } catch (e) { toast(e.message, "err"); }
  },
  "stop-run": (el) => Bridge.call("stop", { run: el.dataset.run }),
  "delete-binding": (el) => {
    if (!confirmTwice(el, "Clique de novo pra apagar")) return;
    const p = findProfile(S.drawer.pid);
    p.bindings = p.bindings.filter((b) => b.id !== S.drawer.bid);
    save(true); closeDrawer(); renderProfiles();
  },
  "dup-binding": () => {
    const p = findProfile(S.drawer.pid);
    const b = clone(drawerBinding());
    b.id = uid(); b.name += " (cópia)";
    p.bindings.push(b);
    save(true); renderView(); openDrawer(p.id, b.id); toast("Duplicado");
  },
  "capture": (el) => capture(el.dataset.target),
  "rate-unit": (el) => {
    const a = drawerBinding().action;
    if (a.rate.unit === el.dataset.value) return;
    const v = a.rate.value;
    a.rate = el.dataset.value === "ms" ? { unit: "ms", value: Math.round(1000 / v) } : { unit: "cps", value: Math.round(1000 / v * 100) / 100 };
    save(); renderDrawer();
  },
  "pick-points": async () => {
    S.pickFor = { bid: S.drawer.bid, pid: S.drawer.pid, kind: "points" };
    await Bridge.call("pick_start");
    banner(`<span class="live" style="background:var(--accent);box-shadow:0 0 12px var(--accent)"></span> Clique nos pontos da tela · botão direito termina <button class="btn sm" data-action="pick-stop">Pronto</button>`);
  },
  "pick-step": async (el) => {
    S.pickFor = { bid: S.drawer.bid, pid: S.drawer.pid, kind: "step", i: +el.dataset.i };
    await Bridge.call("pick_start");
    banner(`<span class="live" style="background:var(--accent)"></span> Clique no ponto da tela <button class="btn sm" data-action="pick-stop">Cancelar</button>`);
  },
  "pick-stop": () => Bridge.call("pick_stop"),
  "del-point": (el) => { drawerBinding().action.points.splice(+el.dataset.i, 1); save(); renderDrawer(); },
  "clear-points": () => { drawerBinding().action.points = []; save(); renderDrawer(); },
  "record": async () => {
    S.recordFor = { pid: S.drawer.pid, bid: S.drawer.bid, mode: $("#rec-mode").value };
    await Bridge.call("record_start", { motion: $("#rec-motion").value });
    renderDrawer();
    const n = S.cfg.settings.countdown;
    banner(`<span class="live"></span> Gravando${n ? ` em ${n} s` : ""}… ${keycaps(S.cfg.settings.record_stop)} termina <button class="btn sm danger" data-action="record-stop">${icon("stop")} Parar</button>`, "rec");
  },
  "record-stop": async () => {
    if (S.recordFor) S.recordFor.viaUi = true;
    await Bridge.call("record_stop");
  },
  "add-step": (el) => { drawerBinding().action.steps.push(newStep(el.dataset.type)); save(); renderDrawer(); scrollTimelineEnd(); },
  "del-step": (el) => { drawerBinding().action.steps.splice(+el.dataset.i, 1); save(); renderDrawer(); },
  "dup-step": (el) => { const st = drawerBinding().action.steps; const c = clone(st[+el.dataset.i]); c.id = uid(); st.splice(+el.dataset.i + 1, 0, c); save(); renderDrawer(); },
  "repeat-kind": (el) => { const a = drawerBinding().action; a.repeat = el.dataset.value === "inf" ? 0 : Math.max(1, a.repeat || 1); save(); renderDrawer(); },
  "remap-to": (el) => { drawerBinding().action.to = el.dataset.value ? [el.dataset.value] : []; save(); renderDrawer(); },
  "region-new": (el) => regionMark(el.dataset.pid, uid(), "Nova região"),
  "region-mark": (el) => regionMark(el.dataset.pid, el.dataset.rid, "Marcar de novo"),
  "region-capture": async (el) => {
    await Bridge.call("region_capture", { profile: el.dataset.pid, region: el.dataset.rid, delay: S.cfg.settings.countdown || 3 });
    if (Bridge.native) Bridge.call("return_to_game").catch(() => {});
    else toast("Capturando em alguns segundos");
  },
  "region-test": async (el) => {
    try {
      const r = await Bridge.call("region_test", { profile: el.dataset.pid, region: el.dataset.rid });
      toast(r.differ == null ? "Sem referência ainda" : `Agora: ${fmt(r.differ, 1)}% diferente → ${r.match ? "igual à referência" : "diferente"}`);
    } catch (e) { toast(e.message, "err"); }
  },
  "region-del": (el) => {
    if (!confirmTwice(el, "")) return;
    const p = findProfile(el.dataset.pid);
    p.regions = (p.regions || []).filter((r) => r.id !== el.dataset.rid);
    save(true); renderView();
  },
  "hud-pos": (el) => { S.cfg.settings.hud.position = el.dataset.value; save(); renderView(); },
  "quick-target": (el) => { quickBinding().action.target = el.dataset.code; save(); renderView(); },
  "quick-create": () => { globalProfile().bindings.unshift({ ...newBinding("clicker"), name: "Autoclick rápido" }); save(true); renderView(); },
};
async function regionMark(pid, rid, what) {
  // the engine picks two corners on the screen and captures the reference itself (this window
  // closes so the game shows); it saves the region into the config
  await Bridge.call("region_mark", { profile: pid, region: rid });
  if (Bridge.native) Bridge.call("return_to_game").catch(() => {});
  else toast(`${what}: clique nos dois cantos na tela`);
}
function confirmTwice(el, text) {
  if (el.dataset.armed) return true;
  el.dataset.armed = "1";
  const old = el.innerHTML;
  el.innerHTML = `${icon("trash")} ${text}`;
  setTimeout(() => { if (el.isConnected) { delete el.dataset.armed; el.innerHTML = old; } }, 2500);
  return false;
}
function scrollTimelineEnd() {
  const body = $("#drawer .drawer-body");
  const tl = $("#timeline");
  if (body && tl) body.scrollTop = tl.offsetTop + tl.offsetHeight - body.clientHeight / 2;
}

/* ================================================================ events */
document.addEventListener("click", (ev) => {
  const stop = ev.target.closest("[data-stop]");
  const actionEl = ev.target.closest("[data-action]");
  if (actionEl && !(actionEl.tagName === "INPUT")) {
    const name = actionEl.dataset.action;
    if (stop && !stop.contains(actionEl)) return;
    if (ACTIONS[name]) { ev.preventDefault(); ACTIONS[name](actionEl, ev); return; }
  }
  if (stop) return;
  const set = ev.target.closest("[data-set]");
  if (set && S.drawer) {
    setPath(drawerBinding(), set.dataset.set, JSON.parse(set.dataset.value));
    save(); renderDrawer(); return;
  }
  const goEl = ev.target.closest("[data-go]");
  if (goEl) go(goEl.dataset.go);
});
document.addEventListener("change", (ev) => {
  const el = ev.target;
  if (el.matches("input[type=checkbox][data-action]")) { ACTIONS[el.dataset.action]?.(el); return; }
  if (el.matches("[data-sbind]") && el.closest(".step")) { stepInput(el, true); return; }
  if (el.matches("[data-bind]") && el.dataset.type === "bool") { bindInput(el); if (el.hasAttribute("data-rerender") || true) renderDrawer(); return; }
  if (el.matches("[data-sbind]") && el.dataset.type === "bool") { settingsInput(el); renderView(); }
});
document.addEventListener("input", (ev) => {
  const el = ev.target;
  if (el.matches("[data-quick=cps]")) {
    const b = quickBinding();
    const cps = sliderToCps(+el.value);
    b.action.rate = { unit: "cps", value: cps };
    el.style.setProperty("--p", el.value + "%");
    $("#quick-num").textContent = fmt(cps);
    save();
    return;
  }
  if (el.matches("[data-sbind]") && el.closest(".step")) { stepInput(el, false); return; }
  if (el.matches("[data-bind]") && el.dataset.type !== "bool") { bindInput(el); return; }
  if (el.matches("[data-sbind]") && el.dataset.type !== "bool") { settingsInput(el); return; }
  if (el.matches("[data-rbind]")) {
    const p = findProfile(el.dataset.pid);
    const r = (p.regions || []).find((x) => x.id === el.dataset.rid);
    if (!r) return;
    if (el.dataset.rbind === "tolerance") {
      r.tolerance = Math.round(+el.value);
      paintRange(el);
      $("#tol-" + r.id).textContent = r.tolerance + "%";
    } else r.name = el.value;
    save();
    return;
  }
  if (el.matches("[data-pbind]")) {
    setPath(findProfile(S.route.id), el.dataset.pbind, el.value);
    save();
    if (el.dataset.pbind === "name") { renderProfiles(); renderCrumbs(); }
  }
});
function readValue(el) {
  const t = el.dataset.type;
  if (t === "bool") return el.checked;
  if (t === "int") return Math.round(+el.value || 0);
  if (t === "float") return +el.value || 0;
  if (t === "pct") return Math.round(+el.value) / 100;
  return el.value;
}
function paintRange(el) {
  if (el.type !== "range") return;
  const p = (el.value - el.min) / (el.max - el.min) * 100;
  el.style.setProperty("--p", p + "%");
}
function bindInput(el) {
  const b = drawerBinding();
  if (!b) return;
  const v = readValue(el);
  setPath(b, el.dataset.bind, v);
  paintRange(el);
  if (el.dataset.out) $("#" + el.dataset.out).textContent = (el.dataset.fmt || "{}").replace("{}", el.dataset.type === "pct" ? fmt(v, 2) : v);
  if (el.dataset.sync) for (const o of $$(`[data-sync="${el.dataset.sync}"]`)) if (o !== el) { o.value = v; paintRange(o); }
  save();
}
function settingsInput(el) {
  const v = readValue(el);
  setPath(S.cfg.settings, el.dataset.sbind, v);
  paintRange(el);
  if (el.dataset.out) $("#" + el.dataset.out).textContent = `${v} s`;
  save();
}
function stepInput(el, structural) {
  const b = drawerBinding();
  const i = +el.closest(".step").dataset.i;
  const s = b.action.steps[i];
  const f = el.dataset.sbind;
  if (f === "codes") s.codes = [el.value];
  else if (["value", "cmd", "act", "region", "cond", "on_timeout"].includes(f)) s[f] = el.value;
  else s[f] = Math.round(+el.value || 0);
  save();
  if (structural && (f === "act" || f === "skip")) renderDrawer();
}
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    if ($("#modal").classList.contains("open")) modal(null);
    else if (S.drawer) closeDrawer();
    else if (params.has("overlay") && !S.capturing) Bridge.call("close"); // back to the game
  }
});

/* drag to reorder macro steps (pointer events: works the same in WebKitGTK and browsers) */
document.addEventListener("pointerdown", (ev) => {
  const grip = ev.target.closest("[data-grip]");
  if (!grip) return;
  const row = grip.closest(".step");
  const tl = row.parentElement;
  const from = +row.dataset.i;
  let to = from;
  row.classList.add("dragging");
  grip.setPointerCapture(ev.pointerId);
  const move = (e) => {
    const rows = $$(".step", tl);
    rows.forEach((r) => r.classList.remove("drop-before", "drop-after"));
    let target = rows.length - 1;
    for (let k = 0; k < rows.length; k++) {
      const r = rows[k].getBoundingClientRect();
      if (e.clientY < r.top + r.height / 2) { target = k; break; }
      target = k + 1;
    }
    to = target;
    if (rows[target]) rows[target].classList.add("drop-before");
    else rows[rows.length - 1].classList.add("drop-after");
  };
  const up = () => {
    grip.removeEventListener("pointermove", move);
    grip.removeEventListener("pointerup", up);
    const steps = drawerBinding().action.steps;
    const [s] = steps.splice(from, 1);
    steps.splice(to > from ? to - 1 : to, 0, s);
    save(); renderDrawer();
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("pointerup", up);
});

/* ================================================================ engine events */
Bridge.on((msg) => {
  if (msg.event === "state") {
    const prev = S.st;
    S.st = msg.state;
    S.engineUp = true;
    if (!prev || prev.armed?.id !== S.st.armed?.id || prev.last_game?.class !== S.st.last_game?.class) renderProfiles();
    renderLive();
    if (S.recordFor && !S.st.recording && !msg.state.recording) {
      // finished elsewhere (stop key / panic): the "recorded" event carries the steps
    }
  } else if (msg.event === "config") {
    adoptConfig(msg.config);
    if (S.editSeq === S.savedSeq && !S.drawer) renderAll();
  } else if (msg.event === "recorded") {
    banner(null);
    const rf = S.recordFor;
    S.recordFor = null;
    const b = rf && findBinding(rf.pid, rf.bid);
    if (b) {
      let steps = msg.steps;
      if (rf.viaUi) { // the click on "Parar" got recorded too
        while (steps.length && (steps[steps.length - 1].t === "button" || steps[steps.length - 1].t === "wait" || steps[steps.length - 1].t === "moveto")) steps = steps.slice(0, -1);
      }
      b.action.steps = rf.mode === "append" ? b.action.steps.concat(steps) : steps;
      save(true);
      toast(`${steps.length} passos gravados`);
      if (S.drawer && S.drawer.bid === b.id) renderDrawer();
    }
  } else if (msg.event === "pick") {
    const pf = S.pickFor;
    const b = pf && findBinding(pf.pid, pf.bid);
    if (b && pf.kind === "points") { b.action.points = msg.points.slice(); if (S.drawer) renderDrawer(); }
    if (b && pf.kind === "step" && msg.points.length) {
      Object.assign(b.action.steps[pf.i], msg.points[0]);
      Bridge.call("pick_stop");
    }
    if (msg.done) { banner(null); S.pickFor = null; if (b) { save(true); if (S.drawer) renderDrawer(); } }
  } else if (msg.event === "theme") {
    loadTheme();
  } else if (msg.event === "engine") {
    S.engineUp = msg.up;
    if (!msg.up) S.st = null;
    renderAll();
    if (msg.up) boot(false);
  }
});

/* ================================================================ boot */
async function loadTheme() {
  try {
    const c = params.get("theme") ? JSON.parse(params.get("theme")) : await Bridge.call("theme_colors");
    const root = document.documentElement.style;
    for (const [k, v] of Object.entries(c)) root.setProperty("--" + k.replace("_", "-"), v);
  } catch {}
}
async function boot(first = true) {
  if (first) await loadTheme();
  try {
    const res = await Bridge.call("get");
    S.cfg = res.config; S.st = res.state; S.labels = res.labels; S.env = res.env || {};
    S.engineUp = true;
  } catch (e) {
    S.engineUp = false;
    if (!S.cfg) { document.body.insertAdjacentHTML("beforeend", `<div class="banner show">Motor indisponível: ${esc(e.message)}</div>`); return; }
  }
  if (first && params.get("page")) S.route = { page: params.get("page"), id: params.get("id") };
  renderAll();
  if (first && params.get("drawer")) openDrawer(params.get("id"), params.get("drawer"));
  if (first && params.get("modal") === "types") typePicker(params.get("id") || "global");
  if (first && params.get("scroll") === "steps") scrollTimelineEnd(); // previews
}

/* ================================================================ mock engine (browser previews) */
const MOCK_SOBER = {
  id: "sob", name: "Arena", enabled: true, icon: "", match: { class: "^org\\.vinegarhq\\.Sober$", title: "" }, on_blur: "pause",
  regions: ["E", "R", "F", "C"].map((k, i) => ({ id: "slot" + k, name: "Slot " + k, x: 882 + 85 * i, y: 1012, w: 72, h: 52, tolerance: 6,
    ref: Array.from({ length: 64 }, (_, j) => [11 + (j % 3), 3, 0]) })),
  bindings: [{ id: "rot", name: "Rotação E R F C", enabled: true, mode: "toggle",
    trigger: { kind: "key", key: "KEY_F8", mods: [], hold_ms: 0, block: false, region: "", cond: "match" },
    action: { type: "macro", repeat: 0, speed: 1, gap_ms: 0, steps: ["E", "R", "F", "C"].flatMap((k) => [
      { t: "if_screen", id: "i" + k, region: "slot" + k, cond: "match", skip: 2 },
      { t: "key", id: "k" + k, codes: ["KEY_" + k], act: "tap", hold_ms: 60 },
      { t: "wait", id: "w" + k, ms: 700, rand_ms: 0 }]) } }],
};
const Mock = (() => {
  const labels = {};
  "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").forEach((c) => (labels["KEY_" + c] = c));
  for (let i = 1; i <= 12; i++) labels["KEY_F" + i] = "F" + i;
  Object.assign(labels, { KEY_ESC: "Esc", KEY_SPACE: "Espaço", KEY_LEFTSHIFT: "Shift", KEY_LEFTCTRL: "Ctrl", KEY_CAPSLOCK: "Caps Lock", KEY_TAB: "Tab", KEY_1: "1", KEY_2: "2",
    BTN_LEFT: "Clique esquerdo", BTN_RIGHT: "Clique direito", BTN_MIDDLE: "Botão do meio", BTN_SIDE: "Lateral trás", BTN_EXTRA: "Lateral frente" });
  const st = (o) => Object.assign({ cfg: null }, o);
  const cfg = {
    version: 1,
    settings: { global_enabled: true, countdown: 3, panic: { key: "KEY_ESC", mods: [], hold_ms: 800, block: false }, record_stop: { key: "KEY_F12", mods: [], hold_ms: 0, block: false },
      hud: { enabled: true, position: "top-right", announce: true }, aurum: { enabled: true } },
    profiles: [
      { id: "global", name: "Qualquer janela", enabled: true, icon: "", match: { class: "", title: "" }, on_blur: "pause", bindings: [
        { id: "q1", name: "Autoclick rápido", enabled: true, mode: "toggle", trigger: { key: "KEY_F8", mods: [], hold_ms: 0, block: false },
          action: { type: "clicker", target: "BTN_LEFT", rate: { unit: "cps", value: 15 }, hold_ms: 12, double: false, jitter: 0, where: "cursor", points: [], point_jitter: 0, limit: { kind: "none", value: 0 } } }] },
      { id: "rbx", name: "Roblox", enabled: true, icon: "", match: { class: "^org\\.vinegarhq\\.Sober$", title: "" }, on_blur: "pause", bindings: [
        { id: "b1", name: "Farm de cliques", enabled: true, mode: "hold", trigger: { key: "BTN_EXTRA", mods: [], hold_ms: 0, block: true },
          action: { type: "clicker", target: "BTN_LEFT", rate: { unit: "cps", value: 20 }, hold_ms: 12, double: false, jitter: 15, where: "cursor", points: [], point_jitter: 0, limit: { kind: "none", value: 0 } } },
        { id: "b2", name: "Combo de skills", enabled: true, mode: "once", trigger: { key: "KEY_G", mods: ["ctrl"], hold_ms: 0, block: true },
          action: { type: "macro", repeat: 1, speed: 1, gap_ms: 0, steps: [
            { t: "key", id: "s1", codes: ["KEY_Q"], act: "tap", hold_ms: 20 }, { t: "wait", id: "s2", ms: 120, rand_ms: 30 },
            { t: "key", id: "s3", codes: ["KEY_E"], act: "tap", hold_ms: 20 }, { t: "button", id: "s4", codes: ["BTN_RIGHT"], act: "tap", hold_ms: 15 },
            { t: "wait", id: "s5", ms: 250, rand_ms: 0 }, { t: "move", id: "s6", dx: 120, dy: 0, ms: 200 }, { t: "text", id: "s7", value: "gg" }] } },
        { id: "b3", name: "Caps vira Shift", enabled: true, mode: "toggle", trigger: { key: "KEY_CAPSLOCK", mods: [], hold_ms: 0, block: true }, action: { type: "remap", to: ["KEY_LEFTSHIFT"] } },
        { id: "b4", name: "Pular em loop", enabled: false, mode: "toggle", trigger: { key: "KEY_F9", mods: [], hold_ms: 0, block: false },
          action: { type: "macro", repeat: 0, speed: 1, gap_ms: 400, steps: [{ t: "key", id: "s8", codes: ["KEY_SPACE"], act: "tap", hold_ms: 40 }] } }] },
      MOCK_SOBER,
      { id: "gta", name: "GTA V", enabled: true, icon: "", match: { class: "^steam_app_3240220$", title: "" }, on_blur: "pause", bindings: [
        { id: "c1", name: "Lateral vira E", enabled: true, mode: "toggle", trigger: { key: "BTN_SIDE", mods: [], hold_ms: 0, block: true }, action: { type: "remap", to: ["KEY_E"] } },
        { id: "c2", name: "Notificar", enabled: true, mode: "once", trigger: { key: "KEY_F10", mods: [], hold_ms: 0, block: false }, action: { type: "command", cmd: "notify-send 'GTA' 'lembrete'" } }] },
    ],
  };
  const now = Date.now() / 1000;
  const state = {
    armed: { id: "rbx", name: "Roblox", bindings: 3 }, focus: { class: "org.vinegarhq.Sober", title: "Roblox", workspace: "special:game" },
    last_game: { class: "steam_app_1172470", title: "Apex Legends" },
    runs: params.has("idle") ? [] : [{ id: "r1", binding: "b1", profile: "rbx", profile_name: "Roblox", name: "Farm de cliques", kind: "clicker", status: "running", detail: "20 CPS", started: now - 42, countdown_until: 0, trigger: { key: "BTN_EXTRA", mods: [] }, mode: "hold" }],
    recording: null, capturing: false, picking: null, toast: null, listening: true, grabbed: ["Compx 2.4G Receiver Mouse"], gamemode: "on",
  };
  async function call(cmd, args) {
    if (!params.has("static")) await new Promise((r) => setTimeout(r, 30));
    if (cmd === "get") return { config: clone(cfg), state: clone(state), labels, env: { calos: true, aurum: true } };
    if (cmd === "theme_colors") return {};
    if (cmd === "set_config") return clone(args.config);
    if (cmd === "windows") return { clients: [{ class: "org.vinegarhq.Sober", title: "Roblox" }, { class: "steam_app_1172470", title: "Apex Legends" }, { class: "Alacritty", title: "claude" }, { class: "zen", title: "YouTube" }] };
    if (cmd === "capture") { await new Promise((r) => setTimeout(r, 900)); return { key: "KEY_G", mods: ["ctrl"], label: "G" }; }
    return true;
  }
  return { call };
})();

boot();
