/* Research & Tech Intelligence Aggregator — frontend logic (vanilla JS) */
(() => {
  "use strict";

  const FEED_URL = "data/feed.json";
  const REFRESH_MS = 5 * 60 * 1000;
  const PAGE_SIZE = 24;
  const LS = { saved: "rtia.saved", read: "rtia.read", theme: "rtia.theme", auto: "rtia.auto", seen: "rtia.lastSeen" };

  const DOMAIN_META = {
    math: { label: "Mathematics", icon: "∑" },
    quantum: { label: "Quantum", icon: "⚛" },
    ai: { label: "LLMs & AI", icon: "🧠" },
  };
  const TYPE_LABEL = { paper: "Paper", blog: "Blog", release: "Release" };

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  const state = {
    items: [],
    meta: {},
    saved: loadJSON(LS.saved, {}), // id -> item snapshot
    read: new Set(loadJSON(LS.read, [])),
    lastSeen: localStorage.getItem(LS.seen) || null,
    filters: { domain: "all", type: "all", range: "0", sort: "score", q: "", view: "all", unread: false, tag: "" },
    visible: PAGE_SIZE,
    auto: localStorage.getItem(LS.auto) !== "false",
    timer: null,
    loading: false,
  };

  /* ------------------------------ utilities ------------------------------ */
  function loadJSON(key, fallback) {
    try { const v = JSON.parse(localStorage.getItem(key)); return v ?? fallback; } catch { return fallback; }
  }
  function saveJSON(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* quota */ } }
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function timeAgo(iso) {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
    return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }
  function toast(msg, onClick) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    t.onclick = onClick ? () => { onClick(); t.classList.remove("show"); } : null;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 4500);
  }

  /* ------------------------------- search -------------------------------- */
  function parseQuery(q) {
    const include = [], exclude = [];
    (q.match(/-?"[^"]+"|\S+/g) || []).forEach((tok) => {
      const neg = tok.startsWith("-") && tok.length > 1;
      const term = (neg ? tok.slice(1) : tok).replace(/^"|"$/g, "").toLowerCase();
      if (term) (neg ? exclude : include).push(term);
    });
    return { include, exclude };
  }
  const haystack = (it) =>
    (it._h ??= [it.title, it.summary, it.source, (it.tags || []).join(" "), (it.authors || []).join(" ")].join(" \n ").toLowerCase());

  function getFiltered() {
    const f = state.filters;
    const { include, exclude } = parseQuery(f.q);
    const cutoff = f.range !== "0" ? Date.now() - Number(f.range) * 86400000 : 0;
    const pool = f.view === "saved" ? Object.values(state.saved) : state.items;
    let out = pool.filter((it) => {
      if (f.domain !== "all" && it.domain !== f.domain) return false;
      if (f.type !== "all" && it.type !== f.type) return false;
      if (cutoff && new Date(it.published).getTime() < cutoff) return false;
      if (f.unread && state.read.has(it.id)) return false;
      if (f.tag && !(it.tags || []).includes(f.tag)) return false;
      if (include.length || exclude.length) {
        const h = haystack(it);
        if (!include.every((t) => h.includes(t))) return false;
        if (exclude.some((t) => h.includes(t))) return false;
      }
      return true;
    });
    out.sort(f.sort === "newest"
      ? (a, b) => new Date(b.published) - new Date(a.published)
      : (a, b) => b.score - a.score || new Date(b.published) - new Date(a.published));
    return { out, include };
  }

  function highlight(text, terms) {
    const safe = esc(text);
    if (!terms.length) return safe;
    const rx = new RegExp(`(${terms.map((t) => escRe(esc(t))).join("|")})`, "gi");
    return safe.replace(rx, "<mark>$1</mark>");
  }

  /* ------------------------------ rendering ------------------------------ */
  function isNew(it) {
    return state.lastSeen && it.first_seen && it.first_seen > state.lastSeen && !state.read.has(it.id);
  }

  function cardHTML(it, terms, idx) {
    const saved = !!state.saved[it.id];
    const read = state.read.has(it.id);
    const dm = DOMAIN_META[it.domain] || { label: it.domain, icon: "•" };
    const authors = (it.authors || []).join(", ");
    const tags = (it.tags || []).map((t) => `<button class="tag-btn" data-tag="${esc(t)}">#${esc(t)}</button>`).join("");
    const also = (it.also_in || []).length ? `<span title="Also seen in: ${esc(it.also_in.join(", "))}">+${it.also_in.length} source${it.also_in.length > 1 ? "s" : ""}</span>` : "";
    return `
    <article class="card${read ? " read" : ""}" data-id="${esc(it.id)}" data-domain="${esc(it.domain)}" style="animation-delay:${Math.min(idx, 12) * 35}ms">
      <div class="meta">
        <span class="badge">${dm.icon} ${esc(dm.label)}</span>
        <span class="badge" style="--c:var(--fg)">${esc(TYPE_LABEL[it.type] || it.type)}</span>
        ${isNew(it) ? '<span class="badge new">New</span>' : ""}
        <span class="score" title="Relevance score ${it.score}/100"><span class="score-bar"><i style="width:${Math.round(it.score)}%"></i></span>${Math.round(it.score)}</span>
      </div>
      <h2><a href="${esc(it.url)}" target="_blank" rel="noopener noreferrer" data-open>${highlight(it.title, terms)}</a></h2>
      <div class="meta"><strong>${esc(it.source)}</strong><span>${timeAgo(it.published)}</span>${authors ? `<span>${highlight(authors, terms)}</span>` : ""}${also}</div>
      ${it.summary ? `<p class="summary">${highlight(it.summary, terms)}</p>` : ""}
      ${tags ? `<div class="flex flex-wrap gap-1.5">${tags}</div>` : ""}
      <div class="card-actions">
        <a class="act primary" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer" data-open>Open ↗</a>
        <button class="act${saved ? " saved" : ""}" data-save aria-pressed="${saved}">${saved ? "★ Saved" : "☆ Save"}</button>
        <button class="act" data-expand>More</button>
        <button class="act" data-read>${read ? "Mark unread" : "Mark read"}</button>
      </div>
    </article>`;
  }

  function render() {
    const { out, include } = getFiltered();
    const feed = $("#feed");
    const shown = out.slice(0, state.visible);
    feed.innerHTML = shown.map((it, i) => cardHTML(it, include, i)).join("");
    $("#empty").classList.toggle("hidden", out.length > 0);
    $("#more-btn").classList.toggle("hidden", out.length <= state.visible);
    $("#result-count").textContent = `${out.length} item${out.length === 1 ? "" : "s"}${state.filters.view === "saved" ? " saved" : ""}${state.filters.q ? ` matching “${state.filters.q}”` : ""}`;
    $("#saved-count").textContent = Object.keys(state.saved).length;
    renderTagCloud();
  }

  function renderStats() {
    const items = state.items;
    const day = Date.now() - 86400000;
    const counts = { math: 0, quantum: 0, ai: 0 };
    items.forEach((i) => { if (i.domain in counts) counts[i.domain]++; });
    const last24 = items.filter((i) => new Date(i.published).getTime() >= day).length;
    const card = (n, l, c) => `<div class="stat glass" style="--c:${c}"><div class="n">${n}</div><div class="l">${esc(l)}</div></div>`;
    $("#stats").innerHTML =
      card(items.length, `items tracked · ${last24} in last 24h`, "var(--accent)") +
      card(counts.math, "Mathematics (Algebra)", "var(--math)") +
      card(counts.quantum, "Quantum Computing", "var(--quantum)") +
      card(counts.ai, "LLMs & AI Architectures", "var(--ai)");
  }

  function renderTagCloud() {
    const freq = new Map();
    const pool = state.filters.view === "saved" ? Object.values(state.saved) : state.items;
    pool.forEach((it) => {
      if (state.filters.domain !== "all" && it.domain !== state.filters.domain) return;
      (it.tags || []).forEach((t) => { if (!/^(math|cs|quant-ph|stat|physics)[.\w-]*$/i.test(t)) freq.set(t, (freq.get(t) || 0) + 1); });
    });
    const top = [...freq.entries()].sort((a, b) => b[1] - a[1]).slice(0, 14);
    $("#tag-cloud").innerHTML = top
      .map(([t, n]) => `<button class="tag-btn${state.filters.tag === t ? " on" : ""}" data-tag="${esc(t)}">#${esc(t)} <span class="opacity-60">${n}</span></button>`)
      .join("");
  }

  function renderUpdated() {
    const g = state.meta.generated_at;
    $("#updated-label").textContent = g
      ? `Updated ${timeAgo(g)} · ${new Date(g).toLocaleString()} · ${state.items.length} items`
      : "No data";
    $("#sample-banner").classList.toggle("hidden", !state.meta.sample);
  }

  function syncControls() {
    const f = state.filters;
    $$("#domain-chips .chip").forEach((b) => b.classList.toggle("active", b.dataset.domain === f.domain));
    $("#tab-all").classList.toggle("active", f.view === "all");
    $("#tab-saved").classList.toggle("active", f.view === "saved");
    $("#unread-btn").setAttribute("aria-pressed", String(f.unread));
    $("#auto-btn").setAttribute("aria-pressed", String(state.auto));
    $("#search").value = f.q;
    $("#type-select").value = f.type;
    $("#range-select").value = f.range;
    $("#sort-select").value = f.sort;
  }

  /* ------------------------------ data loading --------------------------- */
  async function load({ manual = false } = {}) {
    if (state.loading) return;
    state.loading = true;
    $("#refresh-icon").classList.add("spin");
    try {
      const res = await fetch(`${FEED_URL}?t=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const items = Array.isArray(data.items) ? data.items : [];
      const prevIds = new Set(state.items.map((i) => i.id));
      const firstLoad = state.items.length === 0;
      const changed = data.meta?.generated_at !== state.meta.generated_at;
      state.items = items;
      state.meta = data.meta || {};
      // Keep bookmark snapshots fresh when the item still exists.
      let dirty = false;
      items.forEach((it) => { if (state.saved[it.id]) { state.saved[it.id] = it; dirty = true; } });
      if (dirty) saveJSON(LS.saved, state.saved);

      renderStats(); renderUpdated(); render();
      if (!firstLoad && changed) {
        const added = items.filter((i) => !prevIds.has(i.id)).length;
        if (added) toast(`${added} new item${added > 1 ? "s" : ""} — click to view`, () => window.scrollTo({ top: 0, behavior: "smooth" }));
      } else if (manual) {
        toast("Feed is up to date");
      }
    } catch (err) {
      console.error(err);
      if (!state.items.length) {
        $("#feed").innerHTML = "";
        $("#empty").classList.remove("hidden");
        $("#empty").querySelector("p").textContent = "Could not load data/feed.json";
      }
      toast(`Refresh failed: ${err.message}`);
    } finally {
      state.loading = false;
      $("#refresh-icon").classList.remove("spin");
    }
  }

  function markSeenOnLeave() {
    // Items first_seen after this stamp are flagged "New" next visit.
    const g = state.meta.generated_at;
    if (g) localStorage.setItem(LS.seen, g);
  }

  function schedule() {
    clearInterval(state.timer);
    if (state.auto) state.timer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
  }

  /* ------------------------------ interactions --------------------------- */
  function setFilter(patch) {
    Object.assign(state.filters, patch);
    state.visible = PAGE_SIZE;
    syncControls();
    render();
  }

  function toggleSave(id) {
    if (state.saved[id]) delete state.saved[id];
    else {
      const it = state.items.find((i) => i.id === id);
      if (it) { const { _h, ...copy } = it; state.saved[id] = copy; }
    }
    saveJSON(LS.saved, state.saved);
  }
  function setRead(id, val) {
    val ? state.read.add(id) : state.read.delete(id);
    saveJSON(LS.read, [...state.read].slice(-3000));
  }

  function bind() {
    let st;
    $("#search").addEventListener("input", (e) => { clearTimeout(st); st = setTimeout(() => setFilter({ q: e.target.value }), 150); });
    $("#type-select").addEventListener("change", (e) => setFilter({ type: e.target.value }));
    $("#range-select").addEventListener("change", (e) => setFilter({ range: e.target.value }));
    $("#sort-select").addEventListener("change", (e) => setFilter({ sort: e.target.value }));
    $("#domain-chips").addEventListener("click", (e) => { const b = e.target.closest("[data-domain]"); if (b) setFilter({ domain: b.dataset.domain, tag: "" }); });
    $("#tab-all").addEventListener("click", () => setFilter({ view: "all" }));
    $("#tab-saved").addEventListener("click", () => setFilter({ view: "saved" }));
    $("#unread-btn").addEventListener("click", () => setFilter({ unread: !state.filters.unread }));
    $("#reset-btn").addEventListener("click", () => setFilter({ domain: "all", type: "all", range: "0", sort: "score", q: "", view: "all", unread: false, tag: "" }));
    $("#more-btn").addEventListener("click", () => { state.visible += PAGE_SIZE; render(); });
    $("#refresh-btn").addEventListener("click", () => load({ manual: true }));
    $("#auto-btn").addEventListener("click", () => {
      state.auto = !state.auto; localStorage.setItem(LS.auto, String(state.auto)); syncControls(); schedule();
      toast(state.auto ? "Live refresh on (every 5 min)" : "Live refresh paused");
    });
    $("#theme-btn").addEventListener("click", toggleTheme);

    $("#tag-cloud").addEventListener("click", tagClick);
    $("#feed").addEventListener("click", (e) => {
      if (e.target.closest(".tag-btn")) return tagClick(e);
      const card = e.target.closest(".card");
      if (!card) return;
      const id = card.dataset.id;
      if (e.target.closest("[data-open]")) { setRead(id, true); card.classList.add("read"); return; }
      if (e.target.closest("[data-save]")) {
        toggleSave(id);
        if (state.filters.view === "saved") render(); else {
          const b = card.querySelector("[data-save]"), on = !!state.saved[id];
          b.classList.toggle("saved", on); b.textContent = on ? "★ Saved" : "☆ Save"; b.setAttribute("aria-pressed", String(on));
          $("#saved-count").textContent = Object.keys(state.saved).length;
        }
        return;
      }
      if (e.target.closest("[data-expand]")) {
        const s = card.querySelector(".summary"); if (!s) return;
        const open = s.classList.toggle("open"); e.target.textContent = open ? "Less" : "More"; return;
      }
      if (e.target.closest("[data-read]")) {
        const nowRead = !state.read.has(id); setRead(id, nowRead);
        card.classList.toggle("read", nowRead); e.target.textContent = nowRead ? "Mark unread" : "Mark read";
        if (state.filters.unread) render();
      }
    });

    $("#export-btn").addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(Object.values(state.saved), null, 2)], { type: "application/json" });
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "saved-items.json" });
      a.click(); URL.revokeObjectURL(a.href);
    });
    $("#import-btn").addEventListener("click", () => $("#import-file").click());
    $("#import-file").addEventListener("change", async (e) => {
      const file = e.target.files[0]; if (!file) return;
      try {
        const arr = JSON.parse(await file.text());
        let n = 0;
        (Array.isArray(arr) ? arr : []).forEach((it) => { if (it && it.id && it.title && it.url) { state.saved[it.id] = it; n++; } });
        saveJSON(LS.saved, state.saved); render(); toast(`Imported ${n} saved item${n === 1 ? "" : "s"}`);
      } catch { toast("Invalid JSON file"); }
      e.target.value = "";
    });

    document.addEventListener("keydown", (e) => {
      const typing = /input|textarea|select/i.test(document.activeElement.tagName);
      if (e.key === "/" && !typing) { e.preventDefault(); $("#search").focus(); }
      else if (e.key === "Escape" && document.activeElement === $("#search")) { $("#search").blur(); }
      else if (!typing && e.key === "r" && !e.ctrlKey && !e.metaKey) load({ manual: true });
      else if (!typing && e.key === "t") toggleTheme();
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && state.auto) load(); });
    window.addEventListener("pagehide", markSeenOnLeave);
  }

  function tagClick(e) {
    const b = e.target.closest("[data-tag]"); if (!b) return;
    setFilter({ tag: state.filters.tag === b.dataset.tag ? "" : b.dataset.tag });
  }

  function toggleTheme() {
    const light = document.documentElement.classList.toggle("dark") === false;
    localStorage.setItem(LS.theme, light ? "light" : "dark");
  }

  /* --------------------------------- init -------------------------------- */
  function init() {
    $("#feed").innerHTML = Array.from({ length: 6 }, () => '<div class="skeleton"></div>').join("");
    bind(); syncControls(); schedule();
    load().then(() => {
      // Defer updating the "last seen" marker so "New" badges persist for this visit.
      setTimeout(markSeenOnLeave, 0);
    });
  }
  init();
})();
