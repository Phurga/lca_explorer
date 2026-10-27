import { h, render, Fragment } from "preact";
import { useState, useEffect, useMemo, useRef } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);
// A server with several models serves each one under /db/<key>/ (see server.create_multi_app)
const DATABASE = location.pathname.match(/^\/db\/([^/]+)/)?.[1] ?? null;
const API_BASE = DATABASE ? `/db/${DATABASE}` : "";
const STORAGE_KEY = DATABASE ? `lca_viewer.view.${DATABASE}` : "lca_viewer.view";

// ---------------------------------------------------------------- helpers
// The API of the database shown (under /db/<key>/)
const request = (url, body) => serverRequest(url.startsWith("/api/") ? API_BASE + url : url, body);

// The server's own API (databases loaded, Load database)
async function serverRequest(url, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = await response.text();
    try { detail = JSON.parse(detail).detail ?? detail; } catch { /* not JSON */ }
    if (response.status === 404 || response.status === 405) {
      detail = `The server does not know this request (${detail}). It is probably running older code: restart it.`;
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.json();
}

function fmt(x) {
  if (x === 0) return "0";
  const a = Math.abs(x);
  return a >= 1e4 || a < 1e-3 ? x.toExponential(2) : x.toPrecision(3);
}
const pct = (x) => `${(x * 100).toFixed(1)}%`;
const methodLabel = (m) => m.name.join(" | ");
const productOf = (rowId) => Number(rowId.split("/").pop());
const truncate = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const UNITS = {
  kilogram: "kg", "kilowatt hour": "kWh", megajoule: "MJ", "cubic meter": "m3", hectare: "ha",
  "ton kilometer": "tkm", "square meter": "m2", "square meter-year": "m2·a", "meter-year": "m·a",
  "cubic meter-year": "m3·a", "kilogram day": "kg·d", "person kilometer": "pkm", "vehicle kilometer": "vkm",
  "metric ton*km": "tkm", meter: "m", unit: "unit", hour: "h", litre: "l", "kilo becquerel": "kBq",
};
const unit = (u) => UNITS[u] || u;
const movable = (r) => r.depth > 0 && r.kind !== "rest";
// Rows with details: a pop-up (processes, direct emissions, flows) or a members panel (bundles)
const hasDetails = (r) => ["rest", "merged", "group", "flow"].includes(r.kind) || !!r.meta;
// Key of a row's custom name: a process or a flow is renamed wherever it appears (by
// activity / flow), a merged or group row where it is; null: cannot be renamed
function labelKey(r) {
  if (r.kind === "process" && r.meta) return `p:${r.meta.index}`;
  if (r.kind === "flow") return `f:${r.flow.index}`;
  if (r.kind === "merged" || r.kind === "group") return `r:${r.id}`;
  return null;
}
// Rows with their custom name (``custom``), others rows' members included
function withLabels(rows, labels) {
  if (!Object.keys(labels).length) return rows;
  return rows.map((r) => {
    const custom = labelKey(r) && labels[labelKey(r)];
    const members = r.members && withLabels(r.members, labels);
    return custom || members ? { ...r, custom: custom || undefined, members: members || r.members } : r;
  });
}

// Inline editor of a row's name: Enter or leaving the field saves, Esc cancels; an empty
// name goes back to the original one
function RenameInput({ value, renamed, onDone }) {
  const input = useRef(null);
  const done = useRef(false);
  useEffect(() => { input.current?.focus(); input.current?.select(); }, []);
  const finish = (name) => { if (!done.current) { done.current = true; onDone(name); } };
  return html`
    <input class="rename" ref=${input} value=${value} aria-label="New name"
      onClick=${(e) => e.stopPropagation()} onMouseDown=${(e) => e.stopPropagation()}
      onKeyDown=${(e) => { if (e.key === "Enter") finish(e.target.value); else if (e.key === "Escape") { e.stopPropagation(); finish(null); } }}
      onBlur=${(e) => finish(e.target.value)} />
    ${renamed && html`<button class="rename-reset" title="Back to the original name" aria-label="Reset the name"
      onMouseDown=${(e) => { e.preventDefault(); e.stopPropagation(); }}
      onClick=${(e) => { e.stopPropagation(); finish(""); }}>↺</button>`}`;
}
// The row whose children a row is: its parent, or for a row shown inside an expanded
// others row, that row's parent (scope of merges and group rules)
const ownerOf = (r) => (r.parent?.endsWith("/rest") ? r.parent.slice(0, -"/rest".length) : r.parent);
// Shown inside an expanded others row: it can be pinned (taken out of others) rather than moved there
const inOthers = (r) => !!r.parent?.endsWith("/rest");
const compartmentOf = (flow) => [flow.compartment, flow.subcompartment].filter(Boolean).join(", ");
// Share of a row in the tree: of the total score, or of its parent row (the cut-off
// basis switch, "total | parent")
function shareOf(r, byId, rootImpact, basis) {
  const reference = basis === "parent" && r.parent != null ? byId.get(r.parent)?.impacts[0] : rootImpact;
  return reference ? r.impacts[0] / reference : 0;
}
// Amount column of a row: product supplied, waste treated, or elementary flow emitted
function rowAmount(r) {
  if (r.kind === "merged" || r.kind === "group" || r.kind === "flow") return r.unit ? `${fmt(r.amount)} ${unit(r.unit)}` : "";
  if (r.kind !== "process") return "";
  return r.waste ? `${fmt(-r.amount)} ${unit(r.unit)} treated` : `${fmt(r.amount)} ${unit(r.unit)}`;
}

function shortLabel(row, byId) {
  if (row.custom) return row.custom;
  if (row.kind === "direct") {
    const parent = byId.get(row.parent);
    return `direct: ${parent ? shortLabel(parent, byId) : ""}`;
  }
  if (row.kind === "process") {
    const [, name, location] = row.label.split(" | ");
    return `${name} (${location})`;
  }
  if (row.kind === "rest") {
    const parent = byId.get(row.parent);
    return `others under ${parent ? shortLabel(parent, byId) : ""}`;
  }
  return row.label;
}

function frontier(rows) {
  return rows.filter((r) => !(r.expandable && r.expanded));
}

function download(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}

// The single view saved before tabs existed (read once, to open it as the first tab)
function loadSaved() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch { return null; }
}
function loadPreference(name, fallback) {
  try { return localStorage.getItem(`lca_viewer.${name}`) || fallback; } catch { return fallback; }
}
function savePreference(name, value) {
  try { localStorage.setItem(`lca_viewer.${name}`, value); } catch { /* storage unavailable */ }
}

function describePredicate(p) {
  if (!p) return "";
  if (p.all) return p.all.map(describePredicate).join(" and ");
  if (p.any) return p.any.map(describePredicate).join(" or ");
  if (p.not) return `not (${describePredicate(p.not)})`;
  if (p.products) return `${p.products.length} selected processes`;
  if (p.flows) return `${p.flows.length} selected flows`;
  const value = Array.isArray(p.value) ? p.value.join(", ") : p.value;
  return `${p.field} ${p.op} "${value}"`;
}

const PALETTE = [
  "#2f7d6d", "#d1793f", "#5b6fb5", "#c9a227", "#9c5a9e", "#4f9fc4", "#b5525c",
  "#7a9a3a", "#8c6d52", "#4c8a8f", "#d4a0c0", "#777777",
];
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// ---------------------------------------------------------------- number input
// Applies the value while typing (after a short pause), not only on blur/Enter
function NumberInput({ value, onValue, min, max, step, delay = 400, ...props }) {
  const [text, setText] = useState(String(value));
  const timer = useRef(null);
  useEffect(() => { if (Number(text) !== value) setText(String(value)); }, [value]);
  function commit(raw) {
    clearTimeout(timer.current);
    const x = Number(raw);
    if (raw.trim() === "" || !Number.isFinite(x)) return;
    const clamped = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, x));
    if (clamped !== value) onValue(clamped);
  }
  return html`<input type="number" value=${text} min=${min} max=${max} step=${step} ...${props}
    onInput=${(e) => { const raw = e.target.value; setText(raw); clearTimeout(timer.current); timer.current = setTimeout(() => commit(raw), delay); }}
    onChange=${(e) => commit(e.target.value)}
    onBlur=${() => setText(String(value))} />`;
}

// ---------------------------------------------------------------- chart
function Chart({ option, height = 420, onClick }) {
  const ref = useRef(null);
  const chart = useRef(null);
  const clickHandler = useRef(onClick);
  clickHandler.current = onClick;
  useEffect(() => {
    chart.current = echarts.init(ref.current);
    chart.current.on("click", (e) => clickHandler.current?.(e));
    const observer = new ResizeObserver(() => chart.current.resize());
    observer.observe(ref.current);
    return () => { observer.disconnect(); chart.current.dispose(); };
  }, []);
  useEffect(() => { chart.current?.setOption(option, true); }, [option]);
  return html`<div class="chart" style=${{ height: `${height}px` }} ref=${ref}></div>`;
}

function baseOption() {
  const text = cssVar("--text"), muted = cssVar("--muted"), border = cssVar("--border");
  return {
    color: PALETTE,
    textStyle: { color: text, fontFamily: cssVar("--font") },
    grid: { left: 8, right: 64, top: 36, bottom: 8, containLabel: true },
    toolbox: { right: 8, feature: { saveAsImage: { title: "PNG", pixelRatio: 2 } } },
    tooltip: { trigger: "item", confine: true },
    xAxis: { axisLabel: { color: muted }, splitLine: { lineStyle: { color: border } } },
    yAxis: { axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
  };
}

// ---------------------------------------------------------------- shared query builder
// Used both by the process search and by the grouping rules, so filters read the same everywhere.
const OPS = [["contains", "contains"], ["eq", "is"], ["ne", "is not"], ["in", "is one of"], ["startswith", "starts with"], ["regex", "matches regex"]];
const FIELD_LABELS = {
  name: "activity name", reference_product: "product", location: "location", unit: "unit",
  activity_type: "activity type", isic: "ISIC class", isic_division: "ISIC division",
  cpc: "CPC class", cpc_section: "CPC section", database: "database",
};
const fieldLabel = (f) => FIELD_LABELS[f] || f;
let conditionIds = 0;

function useValues(field, text) {
  const [values, setValues] = useState([]);
  useEffect(() => {
    const timer = setTimeout(() => {
      request(`/api/values/${field}?q=${encodeURIComponent(text)}&limit=100`).then(setValues).catch(() => setValues([]));
    }, 150);
    return () => clearTimeout(timer);
  }, [field, text]);
  return values;
}

function Condition({ condition, fields, onChange, onRemove }) {
  const listId = useMemo(() => `values-${++conditionIds}`, []);
  // For "is one of", suggest values for the item being typed
  const typed = condition.op === "in" ? condition.value.split(",").pop().trim() : condition.value;
  const values = useValues(condition.field, typed);
  return html`
    <div class="row condition">
      <select value=${condition.field} onChange=${(e) => onChange({ ...condition, field: e.target.value })} aria-label="Field">
        ${fields.map((f) => html`<option value=${f}>${fieldLabel(f)}</option>`)}
      </select>
      <select value=${condition.op} onChange=${(e) => onChange({ ...condition, op: e.target.value })} aria-label="Operator">
        ${OPS.map(([op, label]) => html`<option value=${op}>${label}</option>`)}
      </select>
      <input value=${condition.value} list=${listId} placeholder=${condition.op === "in" ? "a, b, c" : "value"}
             onInput=${(e) => onChange({ ...condition, value: e.target.value })} aria-label="Value" />
      <datalist id=${listId}>
        ${values.map((v) => html`<option value=${condition.op === "in" ? condition.value.replace(/[^,]*$/, (condition.value.includes(",") ? " " : "") + v) : v} />`)}
      </datalist>
      <button onClick=${onRemove} aria-label="Remove condition">×</button>
    </div>`;
}

function QueryBuilder({ query, setQuery, fields }) {
  const { conditions, combinator } = query;
  const update = (patch) => setQuery({ ...query, ...patch });
  return html`
    <div class="query-builder">
      ${conditions.length > 1 && html`
        <div class="row">Match
          <select value=${combinator} onChange=${(e) => update({ combinator: e.target.value })} aria-label="Combine conditions">
            <option value="all">all conditions</option><option value="any">any condition</option>
          </select></div>`}
      ${conditions.map((c, i) => html`
        <${Condition} key=${c.id} condition=${c} fields=${fields}
          onChange=${(next) => update({ conditions: conditions.map((x, j) => (j === i ? next : x)) })}
          onRemove=${() => update({ conditions: conditions.filter((_, j) => j !== i) })} />`)}
      <div class="row"><button onClick=${() => update({ conditions: [...conditions, newCondition("location", "eq")] })}>+ condition</button></div>
    </div>`;
}

const newCondition = (field = "name", op = "contains", value = "") => ({ id: ++conditionIds, field, op, value });
const emptyQuery = (field, op) => ({ combinator: "all", conditions: [newCondition(field, op)] });

function toPredicate({ conditions, combinator }) {
  const parts = conditions.filter((c) => c.value.trim()).map((c) => ({
    field: c.field, op: c.op,
    value: c.op === "in" ? c.value.split(",").map((v) => v.trim()).filter(Boolean) : c.value.trim(),
  }));
  if (parts.length === 0) return null;
  if (parts.length === 1) return parts[0];
  return { [combinator]: parts };
}

// ---------------------------------------------------------------- functional unit search
function Highlight({ text, words }) {
  if (!words.length || !text) return text;
  const pattern = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "ig");
  return text.split(pattern).map((part, i) => (i % 2 ? html`<mark>${part}</mark>` : part));
}

function FunctionalUnit({ root, onSelect, fields }) {
  const [text, setText] = useState("");
  const [filters, setFilters] = useState(() => emptyQuery("location", "eq"));
  const [showFilters, setShowFilters] = useState(false);
  const [open, setOpen] = useState(false);
  const [found, setFound] = useState({ total: 0, results: [] });
  const [active, setActive] = useState(0);
  const [error, setError] = useState(null);
  const box = useRef(null);
  const predicate = useMemo(() => toPredicate(filters), [filters]);
  const activeFilters = filters.conditions.filter((c) => c.value.trim()).length;
  const searching = Boolean(text.trim() || predicate);

  useEffect(() => {
    const close = (e) => box.current && !box.current.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  useEffect(() => {
    if (!open || !searching) { setFound({ total: 0, results: [] }); return; }
    const timer = setTimeout(() => {
      request("/api/search", { text, predicate, limit: 100 })
        .then((f) => { setFound(f); setActive(0); setError(null); })
        .catch((e) => setError(e.message));
    }, 200);
    return () => clearTimeout(timer);
  }, [text, predicate, open]);

  function choose(r) {
    onSelect(r);
    setText("");
    setOpen(false);
  }
  function onKeyDown(e) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, found.results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && found.results[active]) choose(found.results[active]);
    else if (e.key === "Escape") setOpen(false);
  }
  useEffect(() => {
    box.current?.querySelector(".search-results .active")?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const current = root ? `${root.name} | ${root.location} — ${root.reference_product}` : "";
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  return html`
    <div class="fu" ref=${box}>
      <div class="fu-input">
        <input placeholder=${current || "Search a process: name, product, location…"} value=${text} title=${current}
               onInput=${(e) => { setText(e.target.value); setOpen(true); }} onFocus=${() => setOpen(true)}
               onKeyDown=${onKeyDown} aria-label="Functional unit" />
        <button class=${activeFilters ? "primary" : ""} onClick=${() => { setShowFilters(!showFilters || !open); setOpen(true); }}
          title="Filter by location, activity type, ISIC, …">Filters${activeFilters ? ` (${activeFilters})` : ""}</button>
      </div>
      ${open && html`
        <div class="search-panel">
          ${showFilters && html`
            <div class="search-filters">
              <${QueryBuilder} query=${filters} setQuery=${setFilters} fields=${fields} />
              ${activeFilters > 0 && html`<button onClick=${() => setFilters(emptyQuery("location", "eq"))}>Clear filters</button>`}
            </div>`}
          ${error && html`<div class="error">${error}</div>`}
          ${searching
            ? html`
              <div class="search-meta">${found.total} match${found.total === 1 ? "" : "es"}${found.total > found.results.length ? `, first ${found.results.length} shown` : ""} · ↑↓ Enter</div>
              <div class="search-results" role="listbox">
                ${found.results.map((r, i) => html`
                  <div role="option" aria-selected=${i === active} class=${i === active ? "active" : ""}
                       onMouseEnter=${() => setActive(i)} onMouseDown=${(e) => { e.preventDefault(); choose(r); }}>
                    <div><b><${Highlight} text=${r.name} words=${words} /></b> <span class="loc-chip">${r.location}</span></div>
                    <small><${Highlight} text=${r.reference_product} words=${words} /> · ${unit(r.unit)}</small>
                  </div>`)}
              </div>`
            : html`<div class="search-meta">Type words of the activity name or product (a location code like "fr" or "glo" also works), or use filters.</div>`}
        </div>`}
    </div>`;
}

const openDatabase = (key) => { location.href = `/db/${encodeURIComponent(key)}/`; };
const count = (n) => n.toLocaleString("en-US");

function DatabaseSelect({ databases }) {
  // The databases loaded in the server; switching reloads the page on the other one
  if (!databases || !databases.some((d) => d.key === DATABASE)) return null;
  if (databases.length < 2) return html`<span class="database-title">${databases[0].title}</span>`;
  return html`
    <select class="database" value=${DATABASE} aria-label="Database" onChange=${(e) => openDatabase(e.target.value)}>
      ${databases.map((d) => html`<option value=${d.key}>${d.title}</option>`)}
    </select>`;
}

// Load database: the models already built (data/models.json, e.g. ecoinvent from the setup) open
// in seconds; a database of a Brightway project is first built into a model (once, kept in
// data/cache). Loads run on the server, the dialog follows them and opens the database when ready.
function LoadDatabase({ onClose, missing, onDatabases }) {
  const [sources, setSources] = useState(null);
  const [error, setError] = useState(null);
  const [job, setJob] = useState(null);
  const [folder, setFolder] = useState(() => loadPreference("brightwayFolder", ""));
  const [typed, setTyped] = useState(folder);
  const [projects, setProjects] = useState(null);
  const [projectsError, setProjectsError] = useState(null);
  const [showLog, setShowLog] = useState(false);
  const box = useRef(null);

  const refresh = () => serverRequest("/api/sources").then((s) => {
    setSources(s);
    if (s.jobs.length) setJob((j) => j || s.jobs[0]);  // a load started before (dialog closed meanwhile)
    if (!folder && s.brightway.length) setFolder(s.brightway[0].path);
  }).catch((e) => setError(e.message));
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    if (onClose) {
      const onKey = (e) => e.key === "Escape" && onClose();
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }
  }, [onClose]);

  function listProjects(path) {
    setProjects(null);
    setProjectsError(null);
    if (!path) return;
    serverRequest(`/api/brightway?folder=${encodeURIComponent(path)}`)
      .then((r) => { setProjects(r); savePreference("brightwayFolder", path); })
      .catch((e) => setProjectsError(e.message));
  }
  useEffect(() => { if (folder) listProjects(folder); }, [folder]);

  // Follow the load under way; open the database when it is ready
  useEffect(() => {
    if (!job || job.status !== "running") return;
    const timer = setTimeout(() => serverRequest(`/api/jobs/${job.id}`).then((j) => {
      setJob(j);
      if (j.status === "done") openDatabase(j.key);
    }).catch((e) => setJob({ ...job, status: "failed", error: e.message })), 700);
    return () => clearTimeout(timer);
  }, [job]);

  const start = (body) => { setShowLog(false); serverRequest("/api/load", body).then(setJob).catch((e) => setJob({ status: "failed", error: e.message, log: [] })); };
  const busy = job?.status === "running" || job?.status === "done";
  const unload = (key) => serverRequest("/api/unload", { key }).then((list) => {
    if (key === DATABASE) { location.href = "/"; return; }
    onDatabases?.(list);
    refresh();
    listProjects(folder);
  }).catch((e) => setError(e.message));

  const known = sources?.brightway || [];
  const isKnown = known.some((d) => d.path === folder);
  const dbAction = (p, d) => {
    if (!p.twofive) return null;
    if (!d.processes) return html`<span class="muted">elementary flows</span>`;
    const body = { brightway: projects.folder, project: p.name, database: d.name };
    if (d.model?.key) return html`<button onClick=${() => openDatabase(d.model.key)}>${d.model.key === DATABASE ? "Shown" : "Switch to"}</button>`;
    if (d.model && !d.model.outdated) return html`<button class="primary" disabled=${busy} onClick=${() => start(body)}>Open</button>`;
    return html`
      ${d.model && html`<button disabled=${busy} title="Open the model built before the database changed"
        onClick=${() => start(body)}>Open old build</button>`}
      <button class="primary" disabled=${busy} onClick=${() => start({ ...body, rebuild: !!d.model })}
        title="Builds the model the app reads (about a minute for ecoinvent), kept for next time in data/cache">
        ${d.model ? "Rebuild and open" : "Build and open"}</button>`;
  };

  const content = html`
    ${missing && html`<p class="load-missing">The database <b>${missing}</b> is not loaded (the server was restarted, or it was closed). Load it again below.</p>`}
    ${error && html`<div class="error">${error}</div>`}
    ${job && html`
      <div class=${`load-job ${job.status}`}>
        <div class="load-job-head">
          ${job.status === "running" && html`<span class="spinner" aria-hidden="true"></span>`}
          <span>${job.status === "failed" ? html`<b>Could not load ${job.title || "the database"}.</b> ${job.error}` : job.message}</span>
          ${job.log?.length > 0 && html`<button class="link" onClick=${() => setShowLog(!showLog)}>${showLog ? "Hide details" : "Details"}</button>`}
          ${job.status === "failed" && html`<button class="link" onClick=${() => setJob(null)}>Dismiss</button>`}
        </div>
        ${showLog && html`<pre class="load-log">${job.log.slice(-40).join("\n")}</pre>`}
      </div>`}
    ${!sources ? (!error && html`<div class="empty">Looking for databases…</div>`) : html`
      ${sources.loaded.length > 0 && html`
        <section>
          <h3>Loaded</h3>
          <table class="load-table">
            ${sources.loaded.map((d) => html`
              <tr key=${d.key}>
                <td><b>${d.title}</b>${d.key === DATABASE && html` <span class="loc-chip">shown</span>`}</td>
                <td class="num muted">${count(d.activities)} activities</td>
                <td class="actions">
                  ${d.key !== DATABASE && html`<button onClick=${() => openDatabase(d.key)}>Switch to</button>`}
                  <button title="Stop serving this database, to free its memory" onClick=${() => unload(d.key)}>Close</button>
                </td>
              </tr>`)}
          </table>
        </section>`}
      <section>
        <h3>Built models <span class="muted">ready in a few seconds</span></h3>
        ${sources.models.length === 0
          ? html`<p class="muted">None yet. The setup (setup.cmd or setup.sh) builds ecoinvent, and every Brightway database you load below is added here.</p>`
          : html`<table class="load-table">
              ${sources.models.map((m) => html`
                <tr key=${m.path}>
                  <td><b>${m.title}</b>${m.source?.project && html`<div class="muted small">Brightway project ${m.source.project}</div>`}</td>
                  <td class="num muted">${m.built ? "" : "not built"}</td>
                  <td class="actions">
                    ${m.key
                      ? html`<button onClick=${() => openDatabase(m.key)}>${m.key === DATABASE ? "Shown" : "Switch to"}</button>`
                      : html`<button class="primary" disabled=${busy || !m.built} onClick=${() => start({ path: m.path })}>Open</button>`}
                  </td>
                </tr>`)}
            </table>`}
      </section>
      <section>
        <h3>Brightway projects <span class="muted">databases you imported with Brightway 2.5 or Activity Browser</span></h3>
        <div class="load-folder">
          <select aria-label="Brightway data folder" value=${isKnown ? folder : "other"}
            onChange=${(e) => { if (e.target.value === "other") { setTyped(folder); setFolder(""); setProjects(null); } else setFolder(e.target.value); }}>
            ${known.map((d) => html`<option value=${d.path}>${d.label} — ${d.path}</option>`)}
            <option value="other">Other folder…</option>
          </select>
          ${!isKnown && html`
            <form class="load-other" onSubmit=${(e) => { e.preventDefault(); setFolder(typed.trim().replace(/^["']|["']$/g, "")); }}>
              <input value=${typed} placeholder="Brightway data folder (with projects.db), e.g. the BRIGHTWAY2_DIR of your projects"
                onInput=${(e) => setTyped(e.target.value)} />
              <button type="submit">List projects</button>
            </form>`}
        </div>
        ${projectsError && html`<div class="error">${projectsError}</div>`}
        ${folder && !projects && !projectsError && html`<div class="empty">Reading the projects…</div>`}
        ${projects && html`
          <table class="load-table">
            ${projects.projects.filter((p) => p.databases.length).map((p) => html`
              <tr class="project" key=${`p:${p.name}`}>
                <td colspan="3"><b>${p.name}</b>
                  <span class="muted"> · ${p.twofive ? `${count(p.methods)} impact methods` : "Brightway 2 project: lca_viewer reads Brightway 2.5 projects only (upgrade it in Activity Browser 3 or Brightway 2.5)"}</span></td>
              </tr>
              ${p.databases.map((d) => html`
                <tr key=${`d:${p.name}/${d.name}`} class=${p.twofive && d.processes ? "" : "muted"}>
                  <td class="indent">${d.name}${d.model?.outdated && html`<div class="muted small">${d.model.outdated}${d.model.key ? ": close it to rebuild it" : ""}</div>`}</td>
                  <td class="num muted">${d.processes ? `${count(d.processes)} processes` : `${count(d.flows)} flows`}</td>
                  <td class="actions">${dbAction(p, d)}</td>
                </tr>`)}`)}
          </table>
          ${!projects.projects.some((p) => p.databases.length) && html`<p class="muted">No project with databases in this folder.</p>`}`}
        ${!folder && !known.length && html`<p class="muted">No Brightway data folder found on this computer: give one with <i>Other folder…</i>.</p>`}
      </section>
      <p class="muted small">Other formats (an ecoinvent ecoSpold2 release, a SimaPro CSV export such as Agribalyse) are imported into a Brightway project first, with <code>lca-viewer import</code>: see the README.</p>`}`;

  if (!onClose) return html`<div class="load-page"><h2>Load a database</h2>${content}</div>`;
  return html`
    <div class="modal-backdrop" onMouseDown=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="load-dialog" ref=${box} role="dialog" aria-label="Load a database">
        <div class="info-head">
          <div class="info-title">Load a database</div>
          <button onClick=${onClose} aria-label="Close">×</button>
        </div>
        <div class="load-body">${content}</div>
      </div>
    </div>`;
}

function MethodSelect({ methods, value, onChange }) {
  const families = useMemo(() => {
    const map = new Map();
    methods.forEach((m, i) => {
      if (!map.has(m.name[0])) map.set(m.name[0], []);
      map.get(m.name[0]).push(i);
    });
    return [...map.entries()];
  }, [methods]);
  return html`
    <select class="method" value=${value} onChange=${(e) => onChange(Number(e.target.value))} aria-label="Impact method">
      ${families.map(([family, indices]) => html`
        <optgroup label=${family}>
          ${indices.map((i) => html`<option value=${i}>${methods[i].name.slice(1).join(" | ")}</option>`)}
        </optgroup>`)}
    </select>`;
}

// ---------------------------------------------------------------- tree
function Tree({ rows, rootImpact, shareBasis, selection, onToggle, onSelect, onUngroup, ungroupable, onMergeGroup, onUnpin, onPin, onMoveToOthers, onInfo, onUnmerge, view, focus, renaming, onStartRename, onRename }) {
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const box = useRef(null);
  const scrolledTo = useRef(null);
  // Scroll once to a row found from elsewhere ("Go to in tree"), when it has been rendered
  useEffect(() => {
    const el = focus && focus !== scrolledTo.current && box.current?.querySelector(`[data-id="${CSS.escape(focus)}"]`);
    if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); scrolledTo.current = focus; }
  }, [focus, rows]);
  if (!rows.length) return html`<div class="empty">Choose a functional unit to start.</div>`;
  return html`
    <div class="tree" ref=${box}>
      <table>
        <colgroup><col /><col style="width:130px" /><col style="width:90px" /><col style="width:150px" /></colgroup>
        <thead><tr><th>Process</th><th class="num">Amount</th><th class="num">Impact</th>
          <th title="Switched with the cut-off basis (total | parent)">Share of ${shareBasis === "parent" ? "parent" : "total"}</th></tr></thead>
        <tbody>
          ${rows.map((r) => {
            const share = shareOf(r, byId, rootImpact, shareBasis);
            const amount = rowAmount(r);
            const [product, name, location] = r.kind === "process" ? r.label.split(" | ") : [r.label];
            return html`
              <tr key=${r.id} data-id=${r.id} class="${r.kind} ${selection.has(r.id) ? "selected" : ""} ${focus === r.id ? "focus" : ""}"
                  onClick=${(e) => onSelect(r, e.ctrlKey || e.metaKey || e.shiftKey)}
                  onContextMenu=${(e) => onInfo(r, e)}>
                <td style=${{ "--depth": r.depth }}>
                  <div class="name" style=${{ paddingLeft: `${r.depth * 16}px` }}>
                    ${r.expandable
                      ? html`<button class="toggle" aria-label=${r.expanded ? "Collapse" : "Expand"}
                               onClick=${(e) => { e.stopPropagation(); onToggle(r); }}>${r.expanded ? "▾" : "▸"}</button>`
                      : html`<span class="toggle"></span>`}
                    ${renaming === r.id
                      ? html`<${RenameInput} value=${rowNames(r, byId, view).main} renamed=${!!r.custom} onDone=${(name) => onRename(r, name)} />`
                      : html`<span class="label" title=${r.custom ? `${r.custom} — renamed from ${r.label}` : r.label}>
                          ${(() => { const n = rowNames(r, byId, view); return html`${n.main}${n.qual && html` <span class="loc">${n.qual}</span>`}`; })()}
                        </span>`}
                    <span class=${`row-actions ${renaming === r.id ? "renaming" : ""}`}>
                      ${hasDetails(r) && html`
                        <button class="info-btn" title="Details (or right-click, or Space)" aria-label="Details"
                          onClick=${(e) => { e.stopPropagation(); onInfo(r, e); }}>i</button>`}
                      ${labelKey(r) && html`
                        <button class="rename-btn" title="Rename in this view (r) — the database is not changed" aria-label="Rename"
                          onClick=${(e) => { e.stopPropagation(); onStartRename(r); }}>✎</button>`}
                      ${r.kind === "group" && ungroupable(r) && html`
                        <button class="chip" onClick=${(e) => { e.stopPropagation(); onUngroup(r); }}>ungroup</button>`}
                      ${r.kind === "group" && r.products && html`
                        <button class="chip merge" title="Merge into one process whose inventory is the union of the members'"
                          onClick=${(e) => { e.stopPropagation(); onMergeGroup(r); }}>merge</button>`}
                      ${r.kind === "merged" && (r.by_rule
                        ? html`<span class="chip merge" title="Merged by a grouping rule (see the Grouping tab)">merged</span>`
                        : html`<button class="chip merge" title="Split back into the original processes"
                            onClick=${(e) => { e.stopPropagation(); onUnmerge(r); }}>merged ×</button>`)}
                      ${r.pinned && html`
                        <button class="chip pin" title="Shown despite the cut-off — click to hide again"
                          onClick=${(e) => { e.stopPropagation(); onUnpin(r); }}>pinned ×</button>`}
                      ${movable(r) && (inOthers(r)
                        ? html`<button class="chip pin" title="Pin: take it out of others, shown in the tree and the chart despite the cut-off"
                            onClick=${(e) => { e.stopPropagation(); onPin([r.id]); }}>pin</button>`
                        : html`<button class="to-others" title="Move to others (out of the tree and the chart)" aria-label="Move to others"
                            onClick=${(e) => { e.stopPropagation(); onMoveToOthers([r.id]); }}>×</button>`)}
                    </span>
                  </div>
                </td>
                <td class="num" title=${amount}>${amount}</td>
                <td class="num">${fmt(r.impacts[0])}</td>
                <td>
                  <div class="share">
                    <div class="track"><div class="fill ${share < 0 ? "neg" : ""}"
                      style=${{ width: `${Math.min(100, Math.abs(share) * 100)}%` }}></div></div>
                    <span>${pct(share)}</span>
                  </div>
                </td>
              </tr>`;
          })}
        </tbody>
      </table>
    </div>`;
}

// ---------------------------------------------------------------- tree graph (nodes and edges)
const BOX = { w: 210, h: 58 };
const GAP = {
  horizontal: { depth: 70, slot: 14 }, // root on the left, inputs to the right
  vertical: { depth: 56, slot: 16 }, // root on top, inputs below
};

// Tidy tree: leaves take consecutive slots, a parent is centred on its children.
// Sets x, y and depth on each node reachable from ``root`` through ``childrenOf``.
function tidyLayout(root, childrenOf, orientation) {
  const gap = GAP[orientation];
  const depthStep = (orientation === "horizontal" ? BOX.w : BOX.h) + gap.depth;
  const slotStep = (orientation === "horizontal" ? BOX.h : BOX.w) + gap.slot;
  let nextSlot = 0;
  function place(node, depth) {
    const kids = childrenOf(node);
    const slot = kids.length
      ? (() => { const s = kids.map((k) => place(k, depth + 1)); return (s[0] + s[s.length - 1]) / 2; })()
      : nextSlot++;
    [node.x, node.y] = orientation === "horizontal" ? [depth * depthStep, slot * slotStep] : [slot * slotStep, depth * depthStep];
    node.depth = depth;
    return slot;
  }
  place(root, 0);
}

function graphSize(nodes) {
  return { width: Math.max(...nodes.map((n) => n.x)) + BOX.w, height: Math.max(...nodes.map((n) => n.y)) + BOX.h };
}

// One node per row, one edge per parent → child
function layoutTree(rows, orientation, share, ofTotal) {
  const nodes = rows.map((row) => ({ key: row.id, row, rows: [row], share: share(row) }));
  const byId = new Map(nodes.map((n) => [n.key, n]));
  const children = new Map();
  for (const n of nodes) {
    const parent = byId.get(n.row.parent);
    if (!parent) continue;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(n);
  }
  tidyLayout(nodes[0], (n) => children.get(n) || [], orientation);
  const edges = nodes.filter((n) => byId.has(n.row.parent))
    .map((n) => ({ key: n.key, from: byId.get(n.row.parent), to: n, rows: [n.row], share: ofTotal(n.row) }));
  return { nodes, edges, ...graphSize(nodes) };
}

// Key of the graph node a row belongs to: a process is one node wherever it is used;
// rows under it (direct emissions, flows, groups, others) are keyed by that node
function nodeKey(r, keys) {
  const parent = keys.get(r.parent) ?? "";
  if (r.kind === "process" && r.meta) return `p:${r.meta.index}`;
  if (r.kind === "flow" && r.flow) return `f:${parent}:${r.flow.index}`;
  if (r.kind === "merged") return `m:${parent}:${(r.products || []).join(",")}`;
  if (r.kind === "direct" || r.kind === "rest") return `${r.kind}:${parent}`;
  return `${r.kind}:${parent}:${r.label}`;
}

// Impacts of some rows, without counting twice a row inside another one's supply
// chain (a loop: the outer row's cumulative impact already includes it)
function outerImpacts(rows, rowById) {
  const ids = new Set(rows.map((r) => r.id));
  const outer = rows.filter((r) => {
    for (let p = rowById.get(r.parent); p; p = rowById.get(p.parent)) if (ids.has(p.id)) return false;
    return true;
  });
  return outer[0].impacts.map((_, i) => outer.reduce((sum, r) => sum + r.impacts[i], 0));
}

// Supply chain graph: a process used in several places is one node with several edges
// into it, and a loop is an edge back to a node already shown (a cycle). Laid out as
// the tidy tree of a breadth-first spanning tree (each node at its shortest depth); the
// other edges are drawn on top. A node's amount sums its places, its impact counts
// loops once; its share follows the basis when it has one parent, else of the total.
function layoutGraph(rows, orientation, rootImpact, shareBasis) {
  const rowById = new Map(rows.map((r) => [r.id, r]));
  const keys = new Map();
  const nodes = new Map();
  for (const r of rows) {
    const key = nodeKey(r, keys);
    keys.set(r.id, key);
    if (!nodes.has(key)) nodes.set(key, { key, rows: [], children: [], parents: new Set() });
    nodes.get(key).rows.push(r);
  }
  const links = new Map();
  for (const r of rows) {
    if (r.parent == null || !rowById.has(r.parent)) continue;
    const from = nodes.get(keys.get(r.parent)), to = nodes.get(keys.get(r.id));
    const key = `${from.key}→${to.key}`;
    if (!links.has(key)) {
      links.set(key, { key, from, to, rows: [] });
      from.children.push(to);
      to.parents.add(from);
    }
    links.get(key).rows.push(r);
  }
  for (const n of nodes.values()) {
    const impacts = outerImpacts(n.rows, rowById);
    const amount = n.rows.reduce((sum, r) => sum + r.amount, 0);
    n.row = { ...n.rows[0], impacts, amount, expanded: n.rows.some((r) => r.expanded) };
  }
  const total = (impact) => (rootImpact ? impact / rootImpact : 0);
  const root = nodes.get(keys.get(rows[0].id));
  for (const n of nodes.values()) {
    const [parent] = n.parents;
    const reference = shareBasis === "parent" && n !== root && n.parents.size === 1 ? parent.row.impacts[0] : rootImpact;
    n.share = reference ? n.row.impacts[0] / reference : 0;
  }
  // Breadth-first spanning tree from the root
  const tree = new Map([[root, []]]);
  for (const queue = [root]; queue.length;) {
    const n = queue.shift();
    for (const c of n.children) {
      if (tree.has(c)) continue;
      tree.set(c, []);
      tree.get(n).push(c);
      queue.push(c);
    }
  }
  tidyLayout(root, (n) => tree.get(n), orientation);
  const edges = [...links.values()].map((e) => ({
    ...e, share: total(outerImpacts(e.rows, rowById)[0]), back: e.to.depth <= e.from.depth,
  }));
  const shown = [...nodes.values()];
  return { nodes: shown, edges, ...graphSize(shown) };
}

// An edge to a node at the same depth or nearer the root (a loop) leaves the source
// box on its far side and comes round to the target's near side
function edgePath(from, to, orientation, back = false) {
  if (back) {
    if (orientation === "horizontal") {
      const x1 = from.x + BOX.w, y1 = from.y + BOX.h / 2, x2 = to.x, y2 = to.y + BOX.h / 2, d = BOX.h * 1.4;
      return `M${x1},${y1} C${x1 + 90},${y1 + d} ${x2 - 90},${y2 + d} ${x2},${y2}`;
    }
    const x1 = from.x + BOX.w / 2, y1 = from.y + BOX.h, x2 = to.x + BOX.w / 2, y2 = to.y, d = BOX.w * 0.7;
    return `M${x1},${y1} C${x1 + d},${y1 + 80} ${x2 + d},${y2 - 80} ${x2},${y2}`;
  }
  if (orientation === "horizontal") {
    const x1 = from.x + BOX.w, y1 = from.y + BOX.h / 2, x2 = to.x, y2 = to.y + BOX.h / 2, xm = (x1 + x2) / 2;
    return `M${x1},${y1} C${xm},${y1} ${xm},${y2} ${x2},${y2}`;
  }
  const x1 = from.x + BOX.w / 2, y1 = from.y + BOX.h, x2 = to.x + BOX.w / 2, y2 = to.y, ym = (y1 + y2) / 2;
  return `M${x1},${y1} C${x1},${ym} ${x2},${ym} ${x2},${y2}`;
}

function TreeGraph({ rows, rootImpact, shareBasis, selection, onToggle, onSelect, onUngroup, ungroupable, onMergeGroup, onUnpin, onPin, onMoveToOthers, onInfo, onUnmerge, view, orientation, setOrientation, cycles, setCycles, focus }) {
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const container = useRef(null);
  const drag = useRef(null);
  const [transform, setTransform] = useState({ k: 1, x: 16, y: 16 });
  // Boxes show the share of the total or of the parent; edges always the share of the
  // total, so that the main supply paths stand out
  const graph = useMemo(() => {
    if (!rows.length) return { nodes: [], edges: [] };
    if (cycles) return layoutGraph(rows, orientation, rootImpact, shareBasis);
    const ofTotal = (r) => (rootImpact ? r.impacts[0] / rootImpact : 0);
    return layoutTree(rows, orientation, (r) => shareOf(r, byId, rootImpact, shareBasis), ofTotal);
  }, [rows, orientation, cycles, rootImpact, shareBasis, byId]);
  const rootId = rows[0]?.id;

  // Whole tree in view; with ``readable``, never zoom out below 60 % and centre on the root instead
  function fit(readable = false) {
    const el = container.current;
    if (!el || !graph.nodes.length) return;
    const margin = 24;
    const whole = Math.min(1, (el.clientWidth - 2 * margin) / graph.width, (el.clientHeight - 2 * margin) / graph.height);
    const k = readable ? Math.max(whole, 0.6) : Math.max(whole, 0.1);
    const root = graph.nodes.find((n) => n.rows.some((r) => r.id === rootId));
    setTransform(orientation === "horizontal"
      ? { k, x: margin, y: el.clientHeight / 2 - (root.y + BOX.h / 2) * k }
      : { k, x: el.clientWidth / 2 - (root.x + BOX.w / 2) * k, y: margin });
  }
  // Re-fit when the functional unit, the orientation or the layout changes, not on every expand
  useEffect(() => fit(true), [rootId, orientation, cycles]);
  // Centre once on a node found from elsewhere ("Go to in tree"), when it has been laid out
  const centredOn = useRef(null);
  useEffect(() => {
    const node = focus && focus !== centredOn.current && graph.nodes.find((n) => n.rows.some((r) => r.id === focus));
    const el = container.current;
    if (!node || !el) return;
    centredOn.current = focus;
    setTransform((t) => {
      const k = Math.max(t.k, 0.8);
      return { k, x: el.clientWidth / 2 - (node.x + BOX.w / 2) * k, y: el.clientHeight / 2 - (node.y + BOX.h / 2) * k };
    });
  }, [focus, graph]);

  function onWheel(e) {
    e.preventDefault();
    const rect = container.current.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    setTransform((t) => {
      const k = Math.min(2.5, Math.max(0.1, t.k * Math.exp(-e.deltaY * 0.0015)));
      return { k, x: mx - ((mx - t.x) * k) / t.k, y: my - ((my - t.y) * k) / t.k };
    });
  }
  function onPointerDown(e) {
    if (e.target.closest(".node, .graph-controls")) return;
    drag.current = { x: e.clientX, y: e.clientY, t: transform };
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function onPointerMove(e) {
    const d = drag.current;
    if (d) setTransform({ ...d.t, x: d.t.x + e.clientX - d.x, y: d.t.y + e.clientY - d.y });
  }
  const onPointerUp = () => { drag.current = null; };

  if (!rows.length) return html`<div class="empty">Choose a functional unit to start.</div>`;
  const edgeTitle = ({ from, to, rows: links, share: s, back }) =>
    `${rowNames(to.row, byId, view).main} → ${rowNames(from.row, byId, view).main}${back ? " (loop)" : ""}\n`
    + `${pct(s)} of the total${links.length > 1 ? ` · ${links.length} places in the tree` : ""}`;
  return html`
    <div class="graph" ref=${container} onWheel=${onWheel} onPointerDown=${onPointerDown}
         onPointerMove=${onPointerMove} onPointerUp=${onPointerUp}>
      <div class="graph-controls">
        <button class=${cycles ? "primary" : ""} onClick=${() => setCycles(!cycles)}
          title="Show each process once: a process used in several places has several edges into it, and loops become cycles">Cycles</button>
        <button class=${orientation === "horizontal" ? "primary" : ""} onClick=${() => setOrientation("horizontal")}
          title="Root on the left">→</button>
        <button class=${orientation === "vertical" ? "primary" : ""} onClick=${() => setOrientation("vertical")}
          title="Root on top">↓</button>
        <button onClick=${() => fit(false)}>Fit</button>
        <button onClick=${() => setTransform((t) => ({ ...t, k: 1 }))}>100 %</button>
      </div>
      <svg width="100%" height="100%">
        <g transform=${`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
          ${graph.edges.map((e) => html`
            <path key=${`e-${e.key}`} class="edge ${e.share < 0 ? "neg" : ""} ${e.back ? "back" : ""}"
              d=${edgePath(e.from, e.to, orientation, e.back)} stroke-width=${1 + 14 * Math.min(1, Math.abs(e.share))}>
              <title>${edgeTitle(e)}</title>
            </path>`)}
          ${graph.nodes.map(({ key, row: r, rows: places, share: s, x, y }) => {
            const [product, name, location] = r.kind === "process" ? r.label.split(" | ") : [r.label];
            const names = rowNames(r, byId, view);
            const title = names.main;
            const amountText = rowAmount(r);
            const qualifier = r.kind === "process" && view.naming === "full" ? location : names.qual;
            const subtitle = [places.length > 1 && `×${places.length}`, qualifier, r.kind === "merged" && "merged", r.kind === "group" && "group",
              r.kind === "direct" && "of the parent process", amountText].filter(Boolean).join(" · ");
            const toggleAt = orientation === "horizontal" ? [x + BOX.w, y + BOX.h / 2] : [x + BOX.w / 2, y + BOX.h];
            const barWidth = BOX.w - 20 - 52;
            // A node shown in several places expands at its first place, and collapses everywhere
            const toggle = () => onToggle(r.expanded ? places.filter((p) => p.expanded) : [places.find((p) => p.expandable)]);
            const first = places[0];
            return html`
              <g key=${key} data-id=${first.id} class="node ${r.kind} ${places.some((p) => selection.has(p.id)) ? "selected" : ""} ${places.some((p) => focus === p.id) ? "focus" : ""}"
                 onClick=${(e) => onSelect(first, e.ctrlKey || e.metaKey || e.shiftKey)}
                 onDblClick=${() => r.expandable && toggle()}
                 onContextMenu=${(e) => onInfo(first, e)}>
                <title>${r.kind === "process" ? `${product} | ${name} | ${location}` : r.label}\n${fmt(r.impacts[0])} (${pct(s)})${places.length > 1 ? `\nIn ${places.length} places in the tree: amounts summed, loops counted once` : ""}</title>
                <rect x=${x} y=${y} width=${BOX.w} height=${BOX.h} rx="6" />
                <text x=${x + 10} y=${y + 17} class="title">${truncate(title, 30)}</text>
                <text x=${x + 10} y=${y + 32} class="subtitle">${truncate(subtitle, 34)}</text>
                <rect x=${x + 10} y=${y + 41} width=${barWidth} height="7" rx="3.5" class="track" />
                <rect x=${x + 10} y=${y + 41} width=${barWidth * Math.min(1, Math.abs(s))} height="7" rx="3.5"
                      class="fill ${s < 0 ? "neg" : ""}" />
                <text x=${x + BOX.w - 10} y=${y + 48} class="share" text-anchor="end">${pct(s)}</text>
                ${r.kind === "group" && ungroupable(r) && html`
                  <text x=${x + BOX.w - 10} y=${y + 17} class="ungroup" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onUngroup(r); }}>ungroup</text>`}
                ${r.kind === "group" && r.products && html`
                  <text x=${x + BOX.w - (ungroupable(r) ? 62 : 10)} y=${y + 17} class="unmerge" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onMergeGroup(r); }}>merge</text>`}
                ${r.kind === "merged" && !r.by_rule && html`
                  <text x=${x + BOX.w - 10} y=${y + 17} class="unmerge" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onUnmerge(r); }}>unmerge</text>`}
                ${r.pinned && html`
                  <text x=${x + BOX.w - 10} y=${y + 17} class="unpin" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onUnpin(r); }}>pinned ×</text>`}
                ${movable(r) && (inOthers(r)
                  ? html`
                    <g class="to-others pin" onClick=${(e) => { e.stopPropagation(); onPin([r.id]); }}>
                      <title>Pin: take it out of others, shown in the tree and the chart despite the cut-off</title>
                      <circle cx=${x + BOX.w} cy=${y} r="8" />
                      <text x=${x + BOX.w} y=${y + 4} text-anchor="middle">↑</text>
                    </g>`
                  : html`
                    <g class="to-others" onClick=${(e) => { e.stopPropagation(); onMoveToOthers([r.id]); }}>
                      <title>Move to others (out of the tree and the chart)</title>
                      <circle cx=${x + BOX.w} cy=${y} r="8" />
                      <text x=${x + BOX.w} y=${y + 4} text-anchor="middle">×</text>
                    </g>`)}
                ${r.expandable && html`
                  <g class="toggle" onClick=${(e) => { e.stopPropagation(); toggle(); }}>
                    <circle cx=${toggleAt[0]} cy=${toggleAt[1]} r="8" />
                    <text x=${toggleAt[0]} y=${toggleAt[1] + 4} text-anchor="middle">${r.expanded ? "−" : "+"}</text>
                  </g>`}
              </g>`;
          })}
        </g>
      </svg>
      <div class="graph-hint">Drag to pan · wheel to zoom · double-click or ± to expand · Ctrl/⌘-click to select</div>
    </div>`;
}

// ---------------------------------------------------------------- bundle pop-ups (others, merged, group)
// Opened like a process's pop-up (right-click, (i), Space). Clicking a member opens its
// own pop-up (← comes back).

// What an "others" row contains (below the cut-off, or moved there by the user);
// ticking a member brings it back into the tree and the chart (pins it).
function OthersPopup({ target, rest, rows, rootImpact, cutoff, cutoffBasis, pinned, setPinned, others, setOthers, onMember, view, onClose, onBack }) {
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const parent = byId.get(rest.parent);
  const shownPins = rows.filter((r) => r.parent === rest.parent && r.pinned);
  const items = [...shownPins, ...rest.members].sort((a, b) => Math.abs(b.impacts[0]) - Math.abs(a.impacts[0]));
  const nMoved = rest.members.filter((m) => m.moved).length;
  const nZero = rest.members.filter((m) => !m.moved && m.kind === "flow" && m.impacts[0] === 0).length;
  const nCut = rest.members.length - nMoved - nZero;
  const share = (r) => (rootImpact ? r.impacts[0] / rootImpact : 0);
  // Showing = not in others, and pinned in case it is below the cut-off
  function setShown(ids, on) {
    const nextPinned = new Set(pinned), nextOthers = new Set(others);
    ids.forEach((id) => {
      if (on) { nextOthers.delete(id); nextPinned.add(id); } else nextPinned.delete(id);
    });
    setPinned(nextPinned);
    setOthers(nextOthers);
  }
  const reasons = [nCut && `${nCut} in the bottom ${cutoff} % of ${cutoffBasis === "total" ? "the total" : "their parent"}`,
    nZero && `${nZero} without impact for this method`, nMoved && `${nMoved} moved here by you`].filter(Boolean).join(", ");
  const things = rest.label.includes("flow") ? "flow" : "process";
  return html`
    <${PopupFrame} target=${target} label="Others" className="others-popup" onClose=${onClose} onBack=${onBack}
      title=${html`Others${parent ? html` <span class="muted">under ${shortLabel(parent, byId)}</span>` : ""}`}
      subtitle=${`${reasons} · ${fmt(rest.impacts[0])} (${pct(share(rest))})`}
      footer=${html`
        <button onClick=${() => setShown(rest.members.map((m) => m.id), true)}>Show all</button>
        <button disabled=${!shownPins.length} onClick=${() => setShown(shownPins.map((m) => m.id), false)}>Hide pinned</button>`}>
      <p class="note">Tick a ${things} to pin it: shown in the tree and the contribution chart; untick to put it back in others. Click a ${things} for its details.</p>
      <div class="details-list">
        <table>
          <tbody>
            ${items.map((r) => {
              const names = rowNames(r, byId, view);
              const amount = rowAmount(r);
              const shown = pinned.has(r.id) && !others.has(r.id);
              return html`
                <tr key=${r.id} class="member" onClick=${(e) => !e.target.closest("input") && onMember(r)}
                    onContextMenu=${(e) => { e.preventDefault(); onMember(r); }}>
                  <td><input type="checkbox" checked=${shown} aria-label="Pin: show in tree and chart" title="Pin: show in tree and chart"
                        onChange=${(e) => setShown([r.id], e.target.checked)} /></td>
                  <td class="name-cell" title=${r.label}>${names.main}${names.qual && html` <span class="loc">${names.qual}</span>`}</td>
                  <td class="reason">${r.moved ? "moved" : r.pinned ? "pinned" : r.kind === "flow" && r.impacts[0] === 0 ? "no impact" : "cut-off"}</td>
                  <td class="num">${amount}</td>
                  <td class="num">${fmt(r.impacts[0])}</td>
                  <td class="num">${pct(share(r))}</td>
                </tr>`;
            })}
          </tbody>
        </table>
      </div>
    <//>`;
}

// The members of a merged or group row (processes, or flows for a group of flows) with
// their own amounts and impacts, absolute and as a share of the row.
function BundlePopup({ target, row, methodInfo, rootImpact, actions, onPart, onClose, onBack }) {
  const parts = [...row.parts].sort((a, b) => Math.abs(b.impacts[0]) - Math.abs(a.impacts[0]));
  // Amounts only add up (and have a share) when every member delivers the same product
  const sameUnit = !!row.unit;
  const total = row.impacts[0];
  const amountOf = (p) => `${fmt(Math.abs(p.amount))} ${unit(p.unit)}${p.waste ? " treated" : ""}`;
  const things = parts.some((p) => p.index === undefined) ? "flows" : "processes";
  const kind = row.kind === "merged" ? "Merged" : "Group";
  return html`
    <${PopupFrame} target=${target} label=${`${kind} row`} className="bundle-details" onClose=${onClose} onBack=${onBack}
      title=${html`${row.custom || row.label} <span class="loc-chip">${kind.toLowerCase()}</span>`}
      subtitle=${`${parts.length} ${things} · ${fmt(total)} ${methodInfo.unit} (${pct(rootImpact ? total / rootImpact : 0)} of the total)`}
      footer=${actions}>
      <p class="note">Each member's own amount and impact, and its share of the ${kind.toLowerCase()} row.${sameUnit ? "" : ` The ${things} are of different kinds, so amounts do not add up.`} Click a ${things === "processes" ? "process" : "flow"} for its details.</p>
      <div class="details-list">
        <table>
          <thead><tr>
            <th>${things === "processes" ? "Process" : "Flow"}</th><th class="num">amount</th><th class="num pct">share</th>
            <th class="num">${methodInfo.unit}</th><th class="num pct">share</th>
          </tr></thead>
          <tbody>
            ${parts.map((p) => html`
              <tr key=${p.index ?? `f${p.flow}`} class="part" onClick=${() => onPart(p)}
                  onContextMenu=${(e) => { e.preventDefault(); onPart(p); }}
                  title=${p.flow !== undefined ? `${p.name} | ${p.location}` : `${p.reference_product} | ${p.name} | ${p.location}`}>
                <td class="name-cell">${p.name} <span class="loc">${p.location}</span></td>
                <td class="num">${amountOf(p)}</td>
                <td class="num pct">${sameUnit && row.amount ? pct(p.amount / row.amount) : "–"}</td>
                <td class="num">${fmt(p.impacts[0])}</td>
                <td class="num pct">${total ? pct(p.impacts[0] / total) : "–"}</td>
              </tr>`)}
          </tbody>
          <tfoot><tr>
            <td class="name-cell">${kind}</td>
            <td class="num">${sameUnit ? amountOf(row) : ""}</td><td class="num pct">${sameUnit ? "100%" : ""}</td>
            <td class="num">${fmt(total)}</td><td class="num pct">100%</td>
          </tr></tfoot>
        </table>
      </div>
    <//>`;
}

// ---------------------------------------------------------------- save / load menu
function SaveMenu({ disabled, onSave, onLoad, onCopy }) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  const file = useRef(null);
  useEffect(() => {
    if (!open) return;
    const close = (e) => box.current && !box.current.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return html`
    <span class="split" ref=${box}>
      <button disabled=${disabled} onClick=${onSave} title="Download this view (functional unit, tree, groups, pins, names) as JSON">Save</button>
      <button class="split-arrow" onClick=${() => setOpen(!open)} aria-label="More view options" aria-expanded=${open}>▾</button>
      ${open && html`
        <div class="menu" role="menu">
          <button role="menuitem" onClick=${() => { setOpen(false); file.current.click(); }}>Load view…</button>
          <button role="menuitem" disabled=${disabled} onClick=${() => { setOpen(false); onCopy(); }}>Copy view as JSON</button>
        </div>`}
      <input type="file" accept=".json,application/json" ref=${file} style="display:none" aria-label="View file"
             onChange=${(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) onLoad(f); }} />
    </span>`;
}

// ---------------------------------------------------------------- view bar (names and product grouping)
function ViewBar({ view, setView, example }) {
  const set = (patch) => setView({ ...view, ...patch });
  return html`
    <div class="view-bar">
      <div class="row">
        <button class=${view.groupProducts ? "primary" : ""} onClick=${() => set({ groupProducts: !view.groupProducts })}
          title="Bundle sibling inputs of the same product (other regions or routes) under one row">Group by product</button>
        <span class="segmented" role="group" aria-label="Row names">
          ${[["full", "Full names"], ["short", "Short names"], ["custom", "Custom name"]].map(([id, label]) => html`
            <button class=${view.naming === id ? "primary" : ""} onClick=${() => set({ naming: id })}>${label}</button>`)}
        </span>
      </div>
      ${view.naming === "custom" && html`<${RecipeEditor} recipe=${view.recipe} setRecipe=${(recipe) => set({ recipe })} example=${example} />`}
    </div>`;
}

// ---------------------------------------------------------------- chart panel
// ---------------------------------------------------------------- exchange metadata (info pop-up)
// Brightway's uncertainty type ids (stats_arrays)
const UNCERTAINTY_TYPES = ["undefined", "none", "lognormal", "normal", "uniform", "triangular", "Bernoulli",
  "discrete uniform", "Weibull", "gamma", "beta", "generalized extreme value", "Student's t"];
const PEDIGREE = [["reliability", "reliability"], ["completeness", "completeness"], ["temporal correlation", "time"],
  ["geographical correlation", "geography"], ["further technological correlation", "technology"]];

function uncertaintyText(e) {
  const type = e["uncertainty type"];
  if (type === undefined || type <= 1) return null;
  if (type === 2 && e.scale !== undefined) {
    const basic = e["scale without pedigree"];
    return `lognormal, σ = ${fmt(e.scale)} (GSD² = ${fmt(Math.exp(2 * e.scale))})${basic ? `; basic σ = ${fmt(basic)}` : ""}`;
  }
  if (type === 3 && e.scale !== undefined) return `normal, sd = ${fmt(e.scale)}`;
  const params = [["min", e.minimum], [type === 5 ? "mode" : "loc", type === 4 ? undefined : e.loc], ["max", e.maximum],
    ["scale", type === 5 ? undefined : e.scale], ["shape", e.shape]].filter(([, v]) => v !== undefined);
  return `${UNCERTAINTY_TYPES[type] ?? `type ${type}`}${params.length ? `: ${params.map(([k, v]) => `${k} ${fmt(v)}`).join(", ")}` : ""}`;
}

const hasExchangeMeta = (e) => e.comment || e.pedigree || e.formula || e["uncertainty type"] > 1;

function ExchangeMeta({ e }) {
  const uncertainty = uncertaintyText(e);
  return html`
    <div class="exchange-meta">
      ${e.comment && html`<p class="text">${e.comment.trim()}</p>`}
      <table class="kv">
        ${uncertainty && html`<tr><th>Uncertainty</th><td>${uncertainty}</td></tr>`}
        ${e.pedigree && html`<tr><th>Pedigree</th><td title="Data quality scores, 1 (best) to 5">
          ${PEDIGREE.map(([k, label]) => `${label} ${e.pedigree[k] ?? "–"}`).join(" · ")}</td></tr>`}
        ${e.formula && html`<tr><th>Formula</th><td class="mono">${e.formula}</td></tr>`}
        ${e["variable name"] && html`<tr><th>Variable</th><td class="mono">${e["variable name"]}</td></tr>`}
      </table>
    </div>`;
}

// Inputs or elementary flows of an activity as in its dataset; click a row for its comment and uncertainty
// ``onOpen(index)``: items are products (technosphere), their (i) and name open the activity's
// pop-up; ``onFlow(index, event)``: items are elementary flows, their (i) opens the flow pop-up
function ExchangeTable({ title, items, cumulative, unitPer, onOpen, onFlow, limit = 10 }) {
  const [open, setOpen] = useState(() => new Set());
  const [all, setAll] = useState(false);
  const toggle = (i) => setOpen((s) => { const next = new Set(s); next.has(i) ? next.delete(i) : next.add(i); return next; });
  const shown = all ? items : items.slice(0, limit);
  return html`
    <section>
      <h4>${title} · ${items.length}</h4>
      <table class="flows exchanges">
        <thead><tr>
          <th>${onOpen ? "Product" : "Flow"} <span class="muted">· click a row for its comment and uncertainty, (i) for its details</span></th>
          <th class="num" title="Amount in the dataset">amount</th><th class="num" title=${unitPer}>impact</th><th class="num">share</th>
        </tr></thead>
        <tbody>${shown.map((e, i) => {
          const meta = hasExchangeMeta(e);
          return html`
            <tr key=${i} class=${`exchange ${meta ? "has-meta" : ""} ${open.has(i) ? "open" : ""}`} onClick=${() => meta && toggle(i)}>
              <td><span class="caret">${meta ? (open.has(i) ? "▾" : "▸") : ""}</span>
                <button class="info-btn" title="Details" aria-label="Details"
                  onClick=${(ev) => { ev.stopPropagation(); onOpen ? onOpen(e.index) : onFlow?.(e.index, ev); }}>i</button>
                ${onOpen
                  ? html`<a href="#" title=${`${e.name} | ${e.location}`} onClick=${(ev) => { ev.preventDefault(); ev.stopPropagation(); onOpen(e.index); }}>${e.reference_product}</a>
                      <span class="loc-chip">${e.location}</span>`
                  : html`<span title=${`${e.name} | ${e.categories.join(" / ")}`}>${e.name} <span class="muted">${e.categories.join(" / ")}</span></span>`}</td>
              <td class="num">${fmt(Math.abs(e.amount))} ${unit(e.unit)}</td>
              <td class="num">${e.impact ? fmt(e.impact) : "–"}</td>
              <td class="num">${e.impact && cumulative ? pct(e.impact / cumulative) : ""}</td>
            </tr>
            ${open.has(i) && html`<tr class="exchange-detail"><td colspan="4"><${ExchangeMeta} e=${e} /></td></tr>`}`;
        })}
        </tbody>
      </table>
      ${items.length > shown.length && html`<button class="link" onClick=${() => setAll(true)}>Show all ${items.length}</button>`}
    </section>`;
}

// ---------------------------------------------------------------- activity info pop-up (right click)
const DETAIL_SECTIONS = [
  ["comment", "Description"], ["product_information", "Product"],
  ["included_activities_start", "Starts with"], ["included_activities_end", "Ends with"],
];

// Esc or a click outside closes a pop-up; Backspace (or ←) goes back to the previous one
// when a link inside it was followed
function usePopupKeys(box, onClose, onBack) {
  const back = useRef(onBack);
  back.current = onBack;
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "Backspace" && back.current && !e.target.closest?.("input, textarea, select, [contenteditable]")) {
        e.preventDefault();
        back.current();
      }
    };
    const onDown = (e) => box.current && !box.current.contains(e.target) && onClose();
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onDown); };
  }, []);
}
const BackButton = ({ onBack }) => onBack && html`
  <button class="back" onClick=${onBack} title="Back (Backspace)" aria-label="Back">←</button>`;

// Pop-up chrome: placed at the click, header (title, ←, ×), scrolling body, actions
function PopupFrame({ target, label, className = "", title, subtitle, footer, onClose, onBack, children, maxWidth = 620 }) {
  const box = useRef(null);
  usePopupKeys(box, onClose, onBack);
  const width = Math.min(maxWidth, window.innerWidth - 24);
  const left = Math.max(12, Math.min(target.x + 8, window.innerWidth - width - 12));
  const top = Math.max(12, Math.min(target.y + 8, window.innerHeight - 320));
  return html`
    <div class=${`info-popup ${className}`} ref=${box} role="dialog" aria-label=${label}
         style=${{ left: `${left}px`, top: `${top}px`, width: `${width}px`, maxHeight: `calc(100vh - ${top + 12}px)` }}>
      <div class="info-head">
        <div>
          <div class="info-title">${title}</div>
          ${subtitle && html`<div class="muted">${subtitle}</div>`}
        </div>
        <span class="info-head-actions"><${BackButton} onBack=${onBack} /><button onClick=${onClose} aria-label="Close">×</button></span>
      </div>
      <div class="info-body">${children}</div>
      ${footer && html`<div class="info-actions">${footer}</div>`}
    </div>`;
}

// Right-click on an elementary flow: the exchanges of the processes emitting it (amount,
// comment, uncertainty as recorded in their datasets), its characterisation factors and
// its metadata. ``target.flows``: several flows of one substance (switch compartment).
function FlowInfoPopup({ target, method, methods, onClose, onOpen, onBack }) {
  const [current, setCurrent] = useState(target.flow);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState(null);
  const [filter, setFilter] = useState("");
  const box = useRef(null);
  useEffect(() => { setCurrent(target.flow); setTab(null); }, [target.flow, target.x, target.y]);
  useEffect(() => {
    setInfo(null);
    setError(null);
    request(`/api/flow/${current}?method=${method}&activities=${(target.activities || []).join(",")}`)
      .then(setInfo).catch((e) => setError(e.message));
  }, [current, method, target.activities]);
  usePopupKeys(box, onClose, onBack);

  const width = Math.min(560, window.innerWidth - 24);
  const left = Math.max(12, Math.min(target.x + 8, window.innerWidth - width - 12));
  const top = Math.max(12, Math.min(target.y + 8, window.innerHeight - 320));
  const compartment = info ? [info.compartment, info.subcompartment].filter(Boolean).join(", ") : "";
  const hasExchanges = info && info.exchanges.length > 0;
  const tabs = info ? [
    hasExchanges && { id: "exchange", label: info.exchanges.length > 1 ? `Exchanges · ${info.exchanges.length}` : "Exchange" },
    { id: "factors", label: `Characterisation · ${info.factors.length}` },
    { id: "metadata", label: "Metadata" },
  ].filter(Boolean) : [];
  const shown = tabs.some((t) => t.id === tab) ? tab : tabs[0]?.id;
  const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
  const factors = info ? info.factors.filter((f) => words.every((w) => methodLabel(methods[f.method]).toLowerCase().includes(w))) : [];
  return html`
    <div class="info-popup" ref=${box} style=${{ left: `${left}px`, top: `${top}px`, width: `${width}px`, maxHeight: `calc(100vh - ${top + 12}px)` }}
         role="dialog" aria-label="Elementary flow information">
      <div class="info-head">
        <div>
          <div class="info-title">${info?.name ?? "…"} ${info && html`<span class="loc-chip">${compartment || "no compartment"}</span>`}</div>
          ${info && html`<div class="muted">Elementary flow · ${unit(info.unit)} · ${fmt(info.cf)} ${methods[method].unit} / ${unit(info.unit)} in ${methodLabel(methods[method])}</div>`}
          ${target.flows?.length > 1 && html`
            <div class="flow-compartments" role="group" aria-label="Compartment">${target.flows.map((f, i) => html`
              <button class=${`chip ${f === current ? "active" : ""}`} onClick=${() => setCurrent(f)}>${target.labels?.[i] || `flow ${i + 1}`}</button>`)}</div>`}
        </div>
        <span class="info-head-actions"><${BackButton} onBack=${onBack} /><button onClick=${onClose} aria-label="Close">×</button></span>
      </div>
      ${error && html`<div class="error">${error}</div>`}
      ${info && html`
        <div class="tabs info-tabs" role="tablist">
          ${tabs.map((t) => html`
            <button role="tab" aria-selected=${t.id === shown} class=${t.id === shown ? "active" : ""}
              onClick=${() => setTab(t.id)}>${t.label}</button>`)}
        </div>
        <div class="info-body">
          ${shown === "exchange" && info.exchanges.map(({ activity, exchanges }) => html`
            <section key=${activity.index}>
              <h4>In the dataset of <a href="#" onClick=${(e) => { e.preventDefault(); onOpen(activity.index); }}>${activity.name}</a>
                <span class="loc-chip">${activity.location}</span></h4>
              ${exchanges.length === 0 && html`<p class="text muted">${info.exchanges_available
                ? "No exchange of this flow recorded in the dataset."
                : "This model has no exchange metadata — rebuild it to see comments and uncertainty."}</p>`}
              ${exchanges.map((e) => html`
                <table class="kv"><tr><th>Amount</th><td>${fmt(e.amount)} ${unit(e.unit || info.unit)} <span class="muted">as recorded in the dataset</span></td></tr></table>
                ${hasExchangeMeta(e) ? html`<${ExchangeMeta} e=${e} />` : html`<p class="text muted">No comment or uncertainty recorded for this exchange.</p>`}`)}
            </section>`)}
          ${shown === "factors" && html`
            <section>
              <h4>Characterisation factors</h4>
              ${info.factors.length === 0 ? html`<p class="text muted">No method of this model characterises this flow.</p>` : html`
                ${info.factors.length > 8 && html`<input class="factor-filter" placeholder="Filter methods" value=${filter}
                  onInput=${(e) => setFilter(e.target.value)} aria-label="Filter methods" />`}
                <table class="flows factors">
                  <thead><tr><th>Method</th><th class="num">factor</th></tr></thead>
                  <tbody>${factors.map((f) => html`
                    <tr key=${f.method} class=${f.method === method ? "current" : ""}>
                      <td title=${methodLabel(methods[f.method])}>${methodLabel(methods[f.method])}</td>
                      <td class="num">${fmt(f.cf)} ${methods[f.method].unit} / ${unit(info.unit)}</td>
                    </tr>`)}
                  </tbody>
                </table>`}
            </section>`}
          ${shown === "metadata" && html`
            <section>
              <h4>Metadata</h4>
              <table class="kv">
                <tr><th>Name</th><td>${info.name}</td></tr>
                <tr><th>Compartment</th><td>${info.compartment || "–"}</td></tr>
                <tr><th>Subcompartment</th><td>${info.subcompartment || "–"}</td></tr>
                <tr><th>Unit</th><td>${info.unit}</td></tr>
                ${info.id && html`<tr><th>Database id</th><td class="mono">${info.id}</td></tr>`}
              </table>
            </section>`}
        </div>`}
    </div>`;
}

function InfoPopup({ target, method, methodInfo, rootImpact, rowsById, onClose, onUseAsRoot, onOpenTab, onMoveToOthers, root, amount, onOpen, onGoTo, onFlow, onBack }) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [users, setUsers] = useState(null);
  const [allUsers, setAllUsers] = useState(false);
  // Open tab, back to the default on each right-click (following a link inside keeps it)
  const [tab, setTab] = useState(null);
  const box = useRef(null);
  useEffect(() => setTab(null), [target.x, target.y]);
  useEffect(() => {
    setInfo(null);
    request(`/api/activity/${target.index}?method=${method}`).then(setInfo).catch((e) => setError(e.message));
  }, [target.index, method]);
  useEffect(() => { setAllUsers(false); box.current?.querySelector(".info-body")?.scrollTo(0, 0); }, [target.index]);
  // Exchanges as in the dataset, and for a tree row, the parent's input of this product (comment, uncertainty)
  const [exchanges, setExchanges] = useState(null);
  const [link, setLink] = useState(null);
  const parent = target.row?.kind === "process" ? rowsById.get(target.row.parent) : null;
  const parentMeta = parent?.kind === "process" ? parent.meta : null;
  useEffect(() => {
    setExchanges(null);
    request(`/api/activity/${target.index}/exchanges?method=${method}`).then(setExchanges).catch(() => setExchanges(null));
  }, [target.index, method]);
  useEffect(() => {
    setLink(null);
    if (parentMeta) {
      request(`/api/activity/${parentMeta.index}/exchanges?method=${method}&product=${target.index}`).then(setLink).catch(() => setLink(null));
    }
  }, [target.index, parentMeta?.index, method]);
  useEffect(() => {
    setUsers(null);
    const fu = root ? `&root=${root.index}&amount=${amount}` : "";
    request(`/api/activity/${target.index}/users?method=${method}&limit=${allUsers ? 1000 : 15}${fu}`)
      .then(setUsers).catch((e) => setError(e.message));
  }, [target.index, method, root, amount, allUsers]);
  usePopupKeys(box, onClose, onBack);

  const width = Math.min(560, window.innerWidth - 24);
  const left = Math.max(12, Math.min(target.x + 8, window.innerWidth - width - 12));
  const top = Math.max(12, Math.min(target.y + 8, window.innerHeight - 320));
  const row = target.row;
  const path = [];
  for (let r = row; r; r = rowsById.get(r.parent)) if (r.meta && r.kind === "process") path.unshift(r.meta.name);
  const d = info?.details || {};
  const unitPer = info ? `${methodInfo.unit} / ${unit(info.unit)}` : "";
  const directTotal = info ? info.direct_flows.reduce((sum, f) => sum + f.impact, 0) : 0;
  const production = exchanges?.production;
  // Tabs: for a tree row, the exchange (the parent's input of this process: comment,
  // uncertainty) comes first and is opened; a direct emissions row opens on its flows
  const inTree = row && (row.kind === "process" || row.kind === "direct");
  const hasDescription = DETAIL_SECTIONS.some(([k]) => d[k])
    || (production && (production.comment || production.properties || production["production volume"]));
  const tabs = info ? [
    inTree && { id: "tree", label: parentMeta ? "Exchange" : "In this tree" },
    { id: "results", label: "Results" },
    exchanges?.available && { id: "exchanges", label: `Exchanges · ${(production ? 1 : 0) + exchanges.inputs.length + exchanges.flows.length}` },
    { id: "users", label: `${users?.waste ? "Treats" : "Used by"}${users ? ` · ${users.count}` : ""}` },
    hasDescription && { id: "description", label: "Description" },
    { id: "metadata", label: "Metadata" },
  ].filter(Boolean) : [];
  const has = (id) => tabs.some((t) => t.id === id);
  const shown = has(tab) ? tab : target.direct && has("exchanges") ? "exchanges" : has("tree") ? "tree" : tabs[0]?.id;
  // Exchanges by direction, as in the dataset: products, inputs (from the technosphere,
  // from nature), outputs (waste to treatment, emissions)
  const sections = useMemo(() => {
    if (!exchanges?.available || !info) return [];
    const isProduct = (e) => ["production", "substitution", "generic production"].includes(e.type);
    const resource = (f) => (f.categories[0] || "").toLowerCase().includes("resource");
    const technosphere = exchanges.inputs.filter((e) => !isProduct(e));
    const products = [
      ...(production ? [{ ...production, index: target.index, name: info.name, reference_product: info.reference_product,
        location: info.location, unit: production.unit || info.unit, impact: 0 }] : []),
      ...exchanges.inputs.filter(isProduct),
    ];
    const list = [
      ["products", "Products", products, true],
      ["inputs", "Inputs from technosphere", technosphere.filter((e) => e.amount >= 0), true],
      ["resources", "Inputs from nature", exchanges.flows.filter(resource), false],
      ["waste", "Outputs to treatment (waste)", technosphere.filter((e) => e.amount < 0), true],
      ["emissions", "Emissions", exchanges.flows.filter((f) => !resource(f)), false],
    ].filter(([, , items]) => items.length);
    // A direct emissions row is about the emissions: those first
    return target.direct ? [...list.filter(([k]) => k === "emissions"), ...list.filter(([k]) => k !== "emissions")] : list;
  }, [exchanges, info, target.direct]);
  useEffect(() => { box.current?.querySelector(".info-body")?.scrollTo(0, 0); }, [shown]);
  return html`
    <div class="info-popup" ref=${box} style=${{ left: `${left}px`, top: `${top}px`, width: `${width}px`, maxHeight: `calc(100vh - ${top + 12}px)` }}
         role="dialog" aria-label="Activity information">
      <div class="info-head">
        <div>
          <div class="info-title">${target.direct ? "Direct emissions of " : ""}${(target.direct ? rowsById.get(row?.parent)?.custom : row?.custom) || (info?.name ?? "…")} ${info && html`<span class="loc-chip">${info.location}</span>`}</div>
          ${info && (target.direct ? rowsById.get(row?.parent)?.custom : row?.custom) && html`<div class="muted">renamed in this view · ${info.name}</div>`}
          ${info && html`<div class="muted">${info.reference_product} · ${unit(info.unit)} · ${info.activity_type}</div>`}
        </div>
        <span class="info-head-actions"><${BackButton} onBack=${onBack} /><button onClick=${onClose} aria-label="Close">×</button></span>
      </div>
      ${error && html`<div class="error">${error}</div>`}
      ${info && html`
        <div class="tabs info-tabs" role="tablist">
          ${tabs.map((t) => html`
            <button role="tab" aria-selected=${t.id === shown} class=${t.id === shown ? "active" : ""}
              onClick=${() => setTab(t.id)}>${t.label}</button>`)}
        </div>
        <div class="info-body">
          ${shown === "tree" && html`
            <section>
              <h4>In this tree</h4>
              <table class="kv">
                ${row.kind === "process" && html`<tr><th>Amount</th><td>${row.waste ? `${fmt(-row.amount)} ${unit(row.unit)} treated` : `${fmt(row.amount)} ${unit(row.unit)}`}</td></tr>`}
                <tr><th>Impact</th><td>${fmt(row.impacts[0])} ${methodInfo.unit} (${pct(rootImpact ? row.impacts[0] / rootImpact : 0)} of the total)</td></tr>
                <tr><th>Path</th><td>${path.join(" → ")}</td></tr>
                ${link?.inputs.map((e) => html`
                  <tr><th>Input</th><td>${fmt(e.amount)} ${unit(e.unit)} in the dataset of ${parentMeta.name}
                    <span class="loc-chip">${parentMeta.location}</span>${e.type !== "technosphere" ? html` <span class="muted">(${e.type})</span>` : ""}</td></tr>`)}
              </table>
              ${link?.inputs.filter(hasExchangeMeta).map((e) => html`<${ExchangeMeta} e=${e} />`)}
              ${link && !link.inputs.some(hasExchangeMeta) && html`
                <p class="text muted">No comment or uncertainty recorded for this exchange.</p>`}
            </section>`}
          ${shown === "results" && html`
          <section>
            <h4>Per ${unit(info.unit)} of ${info.reference_product}</h4>
            <table class="kv">
              <tr><th>Cumulative</th><td>${fmt(info.cumulative_per_unit)} ${unitPer}</td></tr>
              <tr><th>Direct</th><td>${fmt(info.direct_per_unit)} ${unitPer}${info.cumulative_per_unit ? ` (${pct(info.direct_per_unit / info.cumulative_per_unit)} of cumulative)` : ""}</td></tr>
            </table>
            ${!exchanges?.available && info.direct_flows.length > 0 && html`
              <table class="flows">
                <thead><tr><th>Main direct emissions (${methodLabel(methodInfo)})</th><th class="num">${unitPer}</th><th class="num">share</th></tr></thead>
                <tbody>${info.direct_flows.map((f) => html`
                  <tr><td>${f.name} <span class="muted">${f.categories.join(" / ")}</span></td>
                      <td class="num">${fmt(f.impact)}</td><td class="num">${pct(directTotal ? f.impact / directTotal : 0)}</td></tr>`)}
                </tbody>
              </table>`}
          </section>`}
          ${shown === "exchanges" && sections.map(([key, title, items, products]) => html`
            <${ExchangeTable} key=${`${key}-${target.index}`} title=${title} items=${items}
              cumulative=${info.cumulative_per_unit} unitPer=${unitPer}
              onOpen=${products ? onOpen : null} onFlow=${products ? null : (flow, ev) => onFlow(flow, target.index, ev)} />`)}
          ${shown === "users" && users && html`
            <section>
              <h4>${users.waste ? "Treats the waste of" : "Used by"} · ${users.count} ${users.count === 1 ? "activity" : "activities"}${root ? `, ${users.in_supply_chain} in this supply chain` : ""}</h4>
              ${users.count === 0 ? html`<p class="text muted">No activity in the database uses this product.</p>` : html`
                <table class="flows users">
                  <thead><tr>
                    <th>Activity <span class="muted">· click for its details</span></th>
                    <th class="num" title=${`${unit(info.unit)} ${users.waste ? "of waste" : ""} per unit of the activity's product`}>per unit</th>
                    ${root && html`<th class="num" title="Amount going to this activity in the supply chain of the functional unit">in this tree</th><th class="num">share</th><th></th>`}
                  </tr></thead>
                  <tbody>${users.users.map((u) => html`
                    <tr key=${u.index}>
                      <td class="user" title=${`${u.reference_product} | ${u.name} | ${u.location}`}>
                        <span class="user-cell"><a href="#" onClick=${(e) => { e.preventDefault(); onOpen(u.index); }}>${u.name}</a>
                        <span class="loc-chip">${u.location}</span></span></td>
                      <td class="num">${fmt(Math.abs(u.per_unit))} ${unit(info.unit)}/${unit(u.unit)}</td>
                      ${root && html`
                        <td class="num">${u.flow ? `${fmt(Math.abs(u.flow))} ${unit(info.unit)}` : "–"}</td>
                        <td class="num">${u.flow ? pct(u.share) : ""}</td>
                        <td>${u.flow !== 0 && html`<button class="chip" title="Open the tree at this activity (largest supply path)"
                              onClick=${() => { onGoTo(u.index); onClose(); }}>go to</button>`}</td>`}
                    </tr>`)}
                  </tbody>
                </table>
                ${users.count > users.users.length && html`
                  <button class="link" onClick=${() => setAllUsers(true)}>Show all ${users.count}</button>`}`}
            </section>`}
          ${shown === "description" && html`
          ${DETAIL_SECTIONS.filter(([k]) => d[k]).map(([k, title]) => html`
            <section><h4>${title}</h4><p class="text">${String(d[k]).split("\n").filter((line) => line.trim() !== "None").join("\n").trim()}</p></section>`)}
          ${production && (production.comment || production.properties || production["production volume"]) && html`
            <section>
              <h4>Reference product exchange</h4>
              <table class="kv">
                ${production["production volume"] !== undefined && html`
                  <tr><th>Production volume</th><td>${fmt(production["production volume"])} ${unit(production.unit)} per year</td></tr>`}
                ${production.comment && html`<tr><th>Comment</th><td>${production.comment}</td></tr>`}
                ${Object.entries(production.properties || {}).map(([name, p]) => html`
                  <tr><th>${name}</th><td title=${p.comment || ""}>${typeof p.amount === "number" ? fmt(p.amount) : p.amount} ${p.unit ?? ""}
                    ${p.comment && html`<span class="muted">· ${truncate(p.comment, 80)}</span>`}</td></tr>`)}
              </table>
            </section>`}`}
          ${shown === "metadata" && html`
          <section>
            <h4>Metadata</h4>
            <table class="kv">
              ${(d.start_date || d.end_date) && html`<tr><th>Time period</th><td>${d.start_date ?? "?"} – ${d.end_date ?? "?"}</td></tr>`}
              ${(d.classifications || []).map(([system, value]) => html`<tr><th>${system}</th><td>${value}</td></tr>`)}
              ${d.synonyms && html`<tr><th>Synonyms</th><td>${d.synonyms.join("; ")}</td></tr>`}
              ${d.authors && Object.entries(d.authors).map(([role, name]) => html`<tr><th>${role}</th><td>${name}</td></tr>`)}
              <tr><th>Database</th><td>${info.database}</td></tr>
              ${d.filename && html`<tr><th>File</th><td class="muted">${d.filename}</td></tr>`}
            </table>
          </section>`}
        </div>
        <div class="info-actions">
          <button onClick=${() => { onUseAsRoot(info); onClose(); }}>Use as functional unit</button>
          <button onClick=${() => { onOpenTab(info); onClose(); }} title="Open this activity as the functional unit of a new tab">Open in new tab</button>
          ${row && movable(row) && html`<button onClick=${() => { onMoveToOthers([row.id]); onClose(); }}>Move to others</button>`}
        </div>`}
    </div>`;
}

// ---------------------------------------------------------------- chart row naming
// Chart items come from the tree frontier; "group products" merges items of the same
// reference product. Each item gets a main name and grey qualifiers.
const RECIPE_TOKENS = {
  product: "reference_product", name: "name", location: "location", unit: "unit",
  type: "activity_type", isic: "isic", cpc: "cpc", database: "database",
};
// Elementary flows have their own tokens and recipe
const FLOW_RECIPE_TOKENS = { substance: "name", compartment: "compartment", subcompartment: "subcompartment" };
// Custom names: a main part (normal text) and a secondary part (grey), for processes
// and for elementary flows
const DEFAULT_RECIPE = { main: "{product}", secondary: "{location}", flowMain: "{substance}", flowSecondary: "{compartment}" };

function joinValues(values, limit = 3) {
  const unique = [...new Set(values.filter(Boolean))];
  return unique.length <= limit ? unique.join(", ") : `${unique.slice(0, limit).join(", ")} +${unique.length - limit}`;
}
function applyRecipe(recipe, metas, limit = 3) {
  return recipe
    .replace(/\{(\w+)\}/g, (token, key) => (RECIPE_TOKENS[key] ? joinValues(metas.map((m) => m[RECIPE_TOKENS[key]]), limit) : token))
    .replace(/\(\s*\)/g, "").replace(/\s+/g, " ").trim();
}

function applyFlowRecipe(recipe, flow) {
  return recipe
    .replace(/\{(\w+)\}/g, (token, key) => (FLOW_RECIPE_TOKENS[key] ? flow[FLOW_RECIPE_TOKENS[key]] || "" : token))
    .replace(/\(\s*\)/g, "").replace(/\s+/g, " ").replace(/^[\s,·-]+|[\s,·-]+$/g, "").trim();
}
// Name of an elementary flow: full (substance, compartment in grey), short (substance
// only) or custom (flow recipe)
function flowNames(flow, naming, recipe) {
  if (naming === "short") return { main: flow.name, qual: "" };
  if (naming === "custom") {
    return { main: applyFlowRecipe(recipe.flowMain ?? DEFAULT_RECIPE.flowMain, flow) || flow.name,
      qual: applyFlowRecipe(recipe.flowSecondary ?? DEFAULT_RECIPE.flowSecondary, flow) };
  }
  return { main: flow.name, qual: compartmentOf(flow) };
}

function chartItems(rows, byId) {
  return frontier(rows)
    .map((r) => {
      const parent = byId.get(r.parent);
      const metas = r.meta ? [r.meta] : r.metas ? r.metas : r.emitters ? r.emitters : r.kind === "direct" && parent?.metas ? parent.metas : [];
      return { kind: r.kind, rows: [r], impacts: [...r.impacts], metas, parentMetas: parent?.meta ? [parent.meta] : parent?.metas || [] };
    })
    .sort((a, b) => Math.abs(b.impacts[0]) - Math.abs(a.impacts[0]));
}

function nameItem(item, naming, recipe, byId) {
  const names = baseNameItem(item, naming, recipe, byId);
  // A custom name replaces the main part; the grey qualifier still tells where it is
  if (item.rows.length !== 1) return names;
  const [row] = item.rows;
  if (row.custom) return { ...names, main: row.custom };
  // Direct emissions, and their flows, go by the name of their process
  const parent = row.kind === "direct" && byId.get(row.parent)?.custom;
  if (parent) return { ...names, main: naming === "full" ? `direct: ${parent}` : parent };
  if (row.emitters) {
    let p = byId.get(row.parent);
    while (p && p.kind !== "process" && p.kind !== "merged") p = byId.get(p.parent);
    if (p?.custom && row.kind === "rest") return { ...names, main: `others under ${p.custom}, direct` };
    if (p?.custom) return { ...names, qual: [`direct: ${p.custom}`, row.kind === "flow" && flowNames(row.flow, naming, recipe).qual].filter(Boolean).join(" · ") };
  }
  return names;
}
function baseNameItem(item, naming, recipe, byId) {
  const { kind, metas, parentMetas, rows } = item;
  const many = rows.length > 1;
  const pick = (field) => joinValues(metas.map((m) => m[field]));
  if (rows[0].emitters) {
    // Elementary flows (or a group or others row of them): the flow, then whose direct emissions
    const emitter = naming === "short" ? pick("reference_product")
      : naming === "custom" ? applyRecipe(recipe.main, metas) : `${pick("name")} (${pick("location")})`;
    // Others of flows: "others under <process>, direct"
    if (kind === "rest") return { main: `others under ${emitter}, direct`, qual: "" };
    const flow = kind === "flow" && flowNames(rows[0].flow, naming, recipe);
    // the emitter first: labels that do not fit lose the end of the qualifier
    return { main: flow ? flow.main : rows[0].label, qual: [`direct: ${emitter}`, flow && flow.qual].filter(Boolean).join(" · ") };
  }
  if (kind === "group") return { main: rows[0].label, qual: many ? `${rows.length} groups` : "" };
  if (kind === "rest") {
    if (many) return { main: "others", qual: `under ${rows.length} processes` };
    if (naming === "short") return { main: "others", qual: `under ${joinValues(parentMetas.map((m) => m.reference_product))}` };
    if (naming === "custom" && parentMetas.length) {
      return { main: `others under ${applyRecipe(recipe.main, parentMetas)}`, qual: applyRecipe(recipe.secondary, parentMetas, 6) };
    }
    return { main: shortLabel(rows[0], byId), qual: "" };
  }
  if (!metas.length) return { main: rows[0].label, qual: "" };
  const direct = kind === "direct";
  if (naming === "short") {
    return { main: pick("reference_product"), qual: [direct && "direct emissions", pick("location")].filter(Boolean).join(" · ") };
  }
  if (naming === "custom") {
    return { main: applyRecipe(recipe.main, metas), qual: [direct && "direct emissions", applyRecipe(recipe.secondary, metas, 6)].filter(Boolean).join(" · ") };
  }
  if (kind === "merged" && !many) return { main: rows[0].label, qual: "" };  // "steel (RER + RoW)"
  return { main: `${direct ? "direct: " : ""}${pick("name")} (${pick("location")})`, qual: "" };
}

// Name of a tree row (list and tree view), following the view's naming choice
function rowNames(r, byId, view) {
  const names = baseRowNames(r, byId, view);
  return r.custom ? { ...names, main: r.custom } : names;
}
function baseRowNames(r, byId, view) {
  if (r.kind === "process" && view.naming === "full") {
    const [product, name, location] = r.label.split(" | ");
    return { main: name, qual: `${location} · ${product}` };
  }
  if (r.kind === "direct") return { main: "direct emissions", qual: "" };
  if (r.kind === "flow") return flowNames(r.flow, view.naming, view.recipe);
  if (r.kind === "rest" || r.kind === "group") return { main: r.label, qual: "" };
  const parent = byId.get(r.parent);
  const metas = r.meta ? [r.meta] : r.metas || [];
  return nameItem({ kind: r.kind, rows: [r], metas, parentMetas: parent?.meta ? [parent.meta] : [] }, view.naming, view.recipe, byId);
}

function nameItems(items, naming, recipe, byId) {
  const named = items.map((item) => ({ ...item, ...nameItem(item, naming, recipe, byId) }));
  // Rows that still read the same get the activity name, then a number, as qualifiers
  const text = (it) => `${it.main}|${it.qual}`;
  const count = (list) => list.reduce((m, it) => m.set(text(it), (m.get(text(it)) || 0) + 1), new Map());
  const qualify = (extra) => {
    const counts = count(named);
    for (const it of named) {
      const q = counts.get(text(it)) > 1 ? extra(it) : "";
      if (q) it.qual = [it.qual, q].filter(Boolean).join(" · ");
    }
  };
  if (naming !== "full") qualify((it) => (it.metas.length ? joinValues(it.metas.map((m) => m.name)) : ""));
  qualify((it) => {
    const parents = it.rows.map((r) => byId.get(r.kind === "direct" ? byId.get(r.parent)?.parent : r.parent)?.meta).filter(Boolean);
    return parents.length ? `under ${joinValues(parents.map((m) => `${m.name} (${m.location})`))}` : "";
  });
  const counts = count(named);
  const seen = new Map();
  for (const it of named) {
    if (counts.get(text(it)) > 1) {
      const n = (seen.get(text(it)) || 0) + 1;
      seen.set(text(it), n);
      it.qual = [it.qual, `#${n}`].filter(Boolean).join(" ");
    }
    it.label = it.qual ? `${it.main} (${it.qual})` : it.main;
  }
  return named;
}

const richSafe = (text) => String(text).replace(/[{}|]/g, " ");
const LABEL_WIDTH = 440;  // px available for chart row labels
// Shorten a two-tone label to fit: the grey qualifier first, then the main text
function fitLabel(main, qual, width) {
  const fits = (m, q) => textWidth(q ? `${m} ${q}` : m) <= width;
  let m = main, q = qual || "";
  while (q && !fits(m, q)) q = q.length > 2 ? `${q.slice(0, -2)}…` : "";
  while (!fits(m, q) && m.length > 4) m = `${m.slice(0, -2)}…`;
  return [m, q];
}
let measureContext = null;
function textWidth(text) {
  measureContext = measureContext || document.createElement("canvas").getContext("2d");
  measureContext.font = `12px ${cssVar("--font")}`;
  return measureContext.measureText(text).width;
}

function RecipeEditor({ recipe, setRecipe, example }) {
  const inputs = { main: useRef(null), secondary: useRef(null), flowMain: useRef(null), flowSecondary: useRef(null) };
  const [target, setTarget] = useState("main");  // field receiving inserted tokens
  function insert(token) {
    const el = inputs[target].current;
    const text = recipe[target] ?? DEFAULT_RECIPE[target];
    const at = el ? el.selectionStart : text.length;
    setRecipe({ ...recipe, [target]: `${text.slice(0, at)}{${token}}${text.slice(at)}` });
    setTimeout(() => { el?.focus(); el?.setSelectionRange(at + token.length + 2, at + token.length + 2); });
  }
  const field = (key, label) => html`
    <label class="recipe-field">${label}
      <input ref=${inputs[key]} value=${recipe[key] ?? DEFAULT_RECIPE[key]} class=${target === key ? "target" : ""} onFocus=${() => setTarget(key)}
             onInput=${(e) => setRecipe({ ...recipe, [key]: e.target.value })} aria-label=${`${label} name recipe`} />
    </label>`;
  return html`
    <div class="recipe">
      <div class="row">
        <span class="recipe-kind">Processes</span>
        ${field("main", "Main")}
        ${field("secondary", "Secondary (grey)")}
        <button onClick=${() => setRecipe(DEFAULT_RECIPE)}>Default</button>
      </div>
      <div class="row">
        <span class="recipe-kind">Flows</span>
        ${field("flowMain", "Main")}
        ${field("flowSecondary", "Secondary (grey)")}
      </div>
      <div class="row tokens">Insert in ${target.startsWith("flow") ? "flow" : "process"} ${target.endsWith("ain") ? "main" : "secondary"}:
        ${Object.keys(target.startsWith("flow") ? FLOW_RECIPE_TOKENS : RECIPE_TOKENS).map((t) => html`<button class="token" onClick=${() => insert(t)}>{${t}}</button>`)}
      </div>
      ${example && html`<p class="note">e.g. <span class="example-main">${example.main}</span>${example.qual && html` <span class="muted">${example.qual}</span>`}${" — when items are combined, their values are listed comma-separated"}</p>`}
    </div>`;
}

// ---------------------------------------------------------------- chart panel
function ChartPanel({ data, info, method, chartMethods, setChartMethods, view }) {
  const { naming, recipe } = view;
  const [mode, setMode] = useState("single");
  const [family, setFamily] = useState(info.methods[method].name[0]);

  const byId = useMemo(() => new Map(data.rows.map((r) => [r.id, r])), [data]);
  const parts = useMemo(
    () => nameItems(chartItems(data.rows, byId), naming, recipe, byId),
    [data, byId, naming, recipe],
  );
  const unit = info.methods[method].unit;
  const total = parts.reduce((s, p) => s + p.impacts[0], 0);

  const singleOption = useMemo(() => {
    const o = baseOption();
    const items = [...parts].reverse();
    const shown = items.map((p) => fitLabel(p.main, p.qual, LABEL_WIDTH));
    o.grid = { ...o.grid, containLabel: false, top: 36, bottom: 28,
      left: Math.max(...shown.map(([m, q]) => textWidth(q ? `${m} ${q}` : m))) + 16 };
    o.title = { text: `${methodLabel(info.methods[method])} [${unit}]`, textStyle: { fontSize: 12, color: cssVar("--muted") } };
    o.yAxis = {
      ...o.yAxis, type: "category", data: items.map((_, i) => String(i)),
      axisLabel: {
        formatter: (v) => {
          const [m, q] = shown[Number(v)];
          return `{m|${richSafe(m)}}${q ? ` {q|${richSafe(q)}}` : ""}`;
        },
        rich: { m: { color: cssVar("--text") }, q: { color: cssVar("--muted") } },
      },
    };
    o.xAxis = { ...o.xAxis, type: "value", name: unit };
    o.tooltip.formatter = (d) => {
      const p = items[d.dataIndex];
      return `${p.label}<br/><b>${fmt(p.impacts[0])} ${unit}</b> (${pct(total ? p.impacts[0] / total : 0)})${p.rows.length > 1 ? `<br/>${p.rows.length} rows merged` : ""}`;
    };
    o.series = [{
      type: "bar", data: items.map((p) => ({
        value: p.impacts[0],
        itemStyle: { color: p.impacts[0] < 0 ? cssVar("--bar-negative") : cssVar("--bar") },
      })),
      label: { show: true, position: "right", formatter: (d) => fmt(d.value), color: cssVar("--muted") },
    }];
    return o;
  }, [parts, method]);

  const multiOption = useMemo(() => {
    const o = baseOption();
    const positions = chartMethods.map((m) => data.methods.indexOf(m)).filter((i) => i >= 0);
    const top = parts.slice(0, 11);
    const rest = parts.slice(11);
    const segments = top.map((p) => ({ name: truncate(p.label, 60), impacts: p.impacts }));
    if (rest.length) {
      segments.push({ name: `${rest.length} more rows`, impacts: data.methods.map((_, k) => rest.reduce((s, p) => s + p.impacts[k], 0)) });
    }
    const totals = positions.map((k) => parts.reduce((s, p) => s + p.impacts[k], 0));
    const categories = positions.map((k) => truncate(info.methods[data.methods[k]].name.slice(1).join(" | "), 50));
    o.legend = { type: "scroll", bottom: 0, textStyle: { color: cssVar("--text") } };
    o.grid.bottom = 40;
    o.yAxis = { ...o.yAxis, type: "category", data: categories, inverse: true };
    o.xAxis = { ...o.xAxis, type: "value", axisLabel: { color: cssVar("--muted"), formatter: (v) => `${v}%` } };
    o.tooltip.valueFormatter = (v) => `${v.toFixed(1)}%`;
    o.series = segments.map((s) => ({
      type: "bar", stack: "total", name: s.name,
      data: positions.map((k, i) => (totals[i] ? (100 * s.impacts[k]) / totals[i] : 0)),
    }));
    return o;
  }, [parts, chartMethods, data]);

  const families = [...new Set(info.methods.map((m) => m.name[0]))];
  const familyMethods = info.methods.map((m, i) => [m, i]).filter(([m]) => m.name[0] === family);

  function exportCsv() {
    const quote = (v) => `"${String(v).replace(/"/g, '""')}"`;
    const header = ["row", "kind", "merged rows", ...data.methods.map((m) => quote(`${methodLabel(info.methods[m])} [${info.methods[m].unit}]`))];
    const lines = parts.map((p) => [quote(p.label), p.kind, p.rows.length, ...p.impacts].join(","));
    download("contributions.csv", [header.join(","), ...lines].join("\n"), "text/csv");
  }

  return html`
    <div class="panel">
      <div class="row">
        <button class=${mode === "single" ? "primary" : ""} onClick=${() => setMode("single")}>Current method</button>
        <button class=${mode === "multi" ? "primary" : ""} onClick=${() => setMode("multi")}>Several methods (100 %)</button>
        <span class="spacer" style="flex:1"></span>
        <button onClick=${exportCsv}>Export CSV</button>
      </div>
      <p class="note">
        Built from the tree as displayed: each visible row that is not expanded is a bar segment.
        Expand, collapse or group rows to change the breakdown. ${parts.length} segments.
      </p>
      ${mode === "single"
        ? html`<${Chart} option=${singleOption} height=${Math.max(260, parts.length * 26 + 60)} />`
        : html`
          <div class="row">
            <select value=${family} onChange=${(e) => setFamily(e.target.value)} aria-label="Method family">
              ${families.map((f) => html`<option value=${f}>${f}</option>`)}
            </select>
            <button onClick=${() => setChartMethods(familyMethods.map(([, i]) => i))}>All of family</button>
            <button onClick=${() => setChartMethods([])}>None</button>
          </div>
          <div class="methods-list">
            ${familyMethods.map(([m, i]) => html`
              <label><input type="checkbox" checked=${chartMethods.includes(i)}
                onChange=${(e) => setChartMethods(e.target.checked ? [...chartMethods, i] : chartMethods.filter((x) => x !== i))} />
                ${m.name.slice(1).join(" | ")}</label>`)}
          </div>
          ${chartMethods.length === 0
            ? html`<div class="empty">Select methods to compare.</div>`
            : html`<${Chart} option=${multiOption} height=${Math.max(300, chartMethods.length * 28 + 120)} />`}`}
    </div>`;
}

// ---------------------------------------------------------------- grouping panel
const PRESETS = [
  { label: "Transport", rule: { name: "transport", predicate: { field: "name", op: "contains", value: "transport" } } },
  { label: "Electricity", rule: { name: "electricity", predicate: { field: "reference_product", op: "startswith", value: "electricity" } } },
  { label: "Heat & fuels", rule: { name: "heat & fuels", predicate: { any: [
    { field: "reference_product", op: "startswith", value: "heat" },
    { field: "reference_product", op: "regex", value: "^(diesel|petrol|natural gas|hard coal|light fuel oil|heavy fuel oil)" },
  ] } } },
  // ecoinvent 3 has no infrastructure flag: constructions (CPC section 5), equipment and
  // vehicles counted per unit (CPC section 4), and machinery and vehicles inventoried in kg
  { label: "Infrastructure", rule: { name: "infrastructure", predicate: { any: [
    { field: "cpc_section", op: "eq", value: "5" },
    { all: [{ field: "cpc_section", op: "eq", value: "4" }, { field: "unit", op: "eq", value: "unit" }] },
    { field: "reference_product", op: "regex",
      value: "^(tractor|harvester|forestry harvester|agricultural machinery|lorry|bus|locomotive|freight wagon|tram|aircraft|barge|transoceanic|ferry|passenger car|electric bicycle|electric scooter|light commercial vehicle|industrial machine)\\b" },
  ] } } },
  { label: "Waste treatment", rule: { name: "waste treatment", predicate: { field: "activity_type", op: "eq", value: "treatment" } } },
  // Agricultural inputs (ecoinvent CPC classes: 3461-3465 fertilisers, 3466 pesticides)
  { label: "Pesticides", rule: { name: "pesticides", predicate: { any: [
    { field: "cpc", op: "regex", value: "^3466" },
    { field: "reference_product", op: "regex", value: "^pesticide" },
  ] } } },
  { label: "Fertilisers", rule: { name: "fertilisers", predicate: { field: "cpc", op: "regex", value: "^346[1-5]" } } },
  // The machines, and the field operations that use them (tillage, sowing, harvesting, …)
  { label: "Agricultural machinery", rule: { name: "agricultural machinery", predicate: { field: "reference_product", op: "regex",
    value: "^(tractor, 4-wheel, agricultural|harvester|agricultural machinery|agricultural trailer|liquid manure tank trailer"
      + "|tillage|sowing|planting|potato planting|harvesting|combine harvesting|fertilising, by|application of plant protection product"
      + "|mowing|mulching|hoeing|haying|swath|baling|bale loading|chopping|fodder loading|potato haulm cutting|furrow"
      + "|field leveling|limestone and gypsum application|liquid manure spreading|solid manure loading and spreading"
      + "|sugarcane (loading|transfer)|transport, tractor and trailer|diesel, burned in agricultural machinery"
      + "|petrol, unleaded, burned in machinery)" } } },
  { label: "By location", rule: { by: "location" } },
  { label: "By activity type", rule: { by: "activity_type" } },
  { label: "By ISIC division", rule: { by: "isic_division" } },
];
// Rules on the elementary flows shown when a "direct emissions" row is expanded
const FLOW_PRESETS = [
  { label: "By substance", rule: { by: "name", target: "flows" } },
  { label: "By compartment", rule: { by: "compartment", target: "flows" } },
];

function GroupPanel({ info, rules, setRules, selectedRow }) {
  const [kind, setKind] = useState("named");
  const [name, setName] = useState("");
  const [by, setBy] = useState("location");
  const [query, setQuery] = useState(() => emptyQuery("name", "contains"));
  const [preview, setPreview] = useState(null);
  const predicate = useMemo(() => toPredicate(query), [query]);
  useEffect(() => {
    if (kind !== "named" || !predicate) { setPreview(null); return; }
    const timer = setTimeout(() => request("/api/search", { predicate, limit: 5 }).then(setPreview).catch(() => setPreview(null)), 250);
    return () => clearTimeout(timer);
  }, [predicate, kind]);
  const [scope, setScope] = useState("*");

  const scopeTarget = selectedRow && selectedRow.kind === "process" ? selectedRow : null;
  const add = (rule) => setRules([...rules, { scope: "*", ...rule }]);
  function addCustom() {
    const ruleScope = scope === "selected" && scopeTarget ? scopeTarget.id : "*";
    if (kind === "named") {
      if (!predicate) return;
      add({ name: name.trim() || describePredicate(predicate), predicate, scope: ruleScope });
    } else {
      add({ by, scope: ruleScope });
    }
    setName("");
  }
  // Disabling keeps a rule (to switch it back on); merging turns each of its groups into a merged process
  const toggle = (i, key) => setRules(rules.map((r, j) => {
    if (j !== i) return r;
    const { [key]: on, ...rest } = r;
    return on ? rest : { ...rest, [key]: true };
  }));
  const move = (i, d) => {
    const next = [...rules];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setRules(next);
  };

  return html`
    <div class="panel">
      <h3>Active rules ${rules.length > 1 && html`<button class="link" onClick=${() => setRules([])}>remove all</button>`}</h3>
      <p class="note">Rules bundle sibling processes in the tree (flow rules: the elementary flows of expanded direct emissions). Named groups are applied first, then group-by rules, each in list order; a process goes to the first group that claims it. Numbers never change, only how they are bundled.</p>
      ${rules.length === 0 ? html`<p class="note">No grouping. Select several sibling rows in the tree (Ctrl/⌘-click) and use "Group selected", or add a rule below.</p>` : html`
        <ul class="rules">
          ${rules.map((r, i) => html`
            <li class=${r.disabled ? "disabled" : ""}>
              <span class="desc">
                ${r.target === "flows" && "flows: "}${r.by ? html`<b>group by ${r.by === "name" && r.target === "flows" ? "substance" : r.by}</b>` : html`<b>${r.name}</b> <small>← ${describePredicate(r.predicate)}</small>`}
                <br /><small>${r.scope === "*" ? "everywhere in the tree" : `under ${r.scope}`}${r.merge ? " · merged" : ""}${r.disabled ? " · disabled" : ""}</small>
              </span>
              ${r.target !== "flows" && html`
                <button class=${r.merge ? "primary" : ""} aria-pressed=${!!r.merge} onClick=${() => toggle(i, "merge")}
                  title=${r.merge ? "Show this rule's groups as groups again" : "Turn each group of this rule into one merged process (inventories added, common inputs summed)"}>merge</button>`}
              <button onClick=${() => toggle(i, "disabled")}
                title=${r.disabled ? "Apply this rule again" : "Stop applying this rule, keeping it in the list"}>${r.disabled ? "enable" : "disable"}</button>
              <button disabled=${i === 0} onClick=${() => move(i, -1)} aria-label="Move up">↑</button>
              <button disabled=${i === rules.length - 1} onClick=${() => move(i, 1)} aria-label="Move down">↓</button>
              <button onClick=${() => setRules(rules.filter((_, j) => j !== i))} aria-label="Remove rule">×</button>
            </li>`)}
        </ul>`}

      <h3>Quick groups</h3>
      <div class="row">${PRESETS.map((p) => html`<button onClick=${() => add(p.rule)}>${p.label}</button>`)}</div>
      <div class="row"><span class="muted">Direct emission flows:</span>
        ${FLOW_PRESETS.map((p) => html`<button onClick=${() => add(p.rule)}>${p.label}</button>`)}</div>

      <h3>New rule</h3>
      <div class="row">
        <label><input type="radio" checked=${kind === "named"} onChange=${() => setKind("named")} /> Named group from a query</label>
        <label><input type="radio" checked=${kind === "by"} onChange=${() => setKind("by")} /> Group by a field</label>
      </div>
      ${kind === "named" ? html`
        <div class="row"><input placeholder="Group name" value=${name} onInput=${(e) => setName(e.target.value)} aria-label="Group name" /></div>
        <${QueryBuilder} query=${query} setQuery=${setQuery} fields=${info.query_fields} />
        ${preview && html`<p class="note">${`Matches ${preview.total} activities in the database, e.g. `
          + preview.results.slice(0, 3).map((r) => `${r.name} (${r.location})`).join("; ")
          + (preview.total > 3 ? "…" : "")}</p>`}
      ` : html`
        <div class="row"><select value=${by} onChange=${(e) => setBy(e.target.value)} aria-label="Group by field">
          ${info.group_by_fields.map((f) => html`<option value=${f}>${fieldLabel(f)}</option>`)}
        </select></div>`}
      <div class="row">
        <label><input type="radio" checked=${scope === "*"} onChange=${() => setScope("*")} /> Everywhere</label>
        <label><input type="radio" checked=${scope === "selected"} disabled=${!scopeTarget} onChange=${() => setScope("selected")} />
          Only under the selected row${scopeTarget ? `: ${truncate(scopeTarget.label.split(" | ")[1], 40)}` : ""}</label>
      </div>
      <div class="row"><button class="primary" onClick=${addCustom}>Add rule</button></div>
    </div>`;
}

// ---------------------------------------------------------------- global grouping panel
// Right-click menu of a process listed in the side panel: go to it in the tree, or its details
function useProcessMenu(goTo, onInfo, onExchange) {
  const [menu, setMenu] = useState(null);
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (e) => e.key === "Escape" && close();
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", onKey); };
  }, [menu]);
  const open = (e, item) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, item }); };
  const element = menu && html`
    <div class="menu context" style=${{ left: `${Math.min(menu.x, window.innerWidth - 200)}px`, top: `${Math.min(menu.y, window.innerHeight - 90)}px` }}
         onMouseDown=${(e) => e.stopPropagation()}>
      <button onClick=${() => { goTo(menu.item); setMenu(null); }}>Go to in tree</button>
      <button onClick=${() => { onInfo(menu.item.index, menu.x, menu.y); setMenu(null); }}>Details…</button>
      ${onExchange && html`<button onClick=${() => { onExchange(menu.item, menu); setMenu(null); }}>Exchange…</button>`}
    </div>`;
  return { open, element };
}

function GlobalPanel({ info, root, amount, method, rules, onLocate, onInfo }) {
  const [rule, setRule] = useState("direct");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [group, setGroup] = useState(null);
  const [contributors, setContributors] = useState(null);
  const contributorsBox = useRef(null);
  // Named rules on activities are activity groups; named rules on elementary flows are
  // flow groups, which split what the activity groups leave (their flows emitted outside them)
  const active = rules.filter((r) => r.predicate && !r.disabled);
  const groups = useMemo(() => Object.fromEntries(active.filter((r) => r.target !== "flows").map((r) => [r.name, r.predicate])), [rules]);
  const flowGroups = useMemo(() => Object.fromEntries(active.filter((r) => r.target === "flows")
    .map((r) => [r.name in groups ? `${r.name} (flows)` : r.name, r.predicate])), [rules, groups]);
  const k = method;

  useEffect(() => {
    if (!root) return;
    let cancelled = false;
    setError(null);
    request("/api/global", { root: root.index, amount, groups, flow_groups: flowGroups, rule, method: k })
      .then((d) => !cancelled && setResult(d)).catch((e) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [root, amount, groups, flowGroups, rule, k]);
  useEffect(() => { if (group && result && !(group in result)) setGroup(null); }, [result]);
  // Processes making up the chosen group
  useEffect(() => {
    setContributors(null);
    if (!root || !group) return;
    let cancelled = false;
    request("/api/global/contributors", { root: root.index, amount, groups, flow_groups: flowGroups, rule, group, method: k, limit: 50 })
      .then((d) => !cancelled && setContributors(d)).catch((e) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [root, amount, groups, flowGroups, rule, group, k]);
  useEffect(() => { contributorsBox.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [group, !!contributors]);

  const unit = info.methods[k].unit;
  // The current method only; largest |impact| first, "other" last
  const entries = result ? Object.entries(result).map(([n, v]) => [n, v[0]])
    .sort(([a, x], [b, y]) => (a === "other") - (b === "other") || Math.abs(y) - Math.abs(x)) : [];
  const total = entries.reduce((s, [, v]) => s + v, 0);
  const choose = (name) => setGroup(name === group ? null : name);
  const goTo = (c) => onLocate(c.index, { groups, rule });
  const menu = useProcessMenu(goTo, onInfo);
  const ofTotal = (v) => pct(total ? v / total : 0);
  const ofGroup = (v) => pct(contributors?.total ? v / contributors.total : 0);
  const option = useMemo(() => {
    const o = baseOption();
    o.yAxis = { ...o.yAxis, type: "category", data: entries.map(([n]) => n), inverse: true, triggerEvent: true };
    o.xAxis = { ...o.xAxis, type: "value", name: unit };
    o.tooltip.valueFormatter = (v) => `${fmt(v)} ${unit} (${pct(v / total)})`;
    o.series = [{ type: "bar", colorBy: "data", cursor: "pointer",
      data: entries.map(([n, v]) => ({
        value: v,
        itemStyle: { ...(n === "other" ? { color: cssVar("--muted") } : {}), opacity: group && n !== group ? 0.35 : 1 },
      })),
      label: { show: true, position: "right", formatter: (d) => pct(d.value / total), color: cssVar("--muted") } }];
    return o;
  }, [result, k, group]);

  return html`
    <div class="panel">
      <p class="note">
        Whole supply chain, using the named groups defined in the Grouping tab (${Object.keys(groups).length} groups of
        processes${Object.keys(flowGroups).length ? `, ${Object.keys(flowGroups).length} of elementary flows` : ""}).
        Process groups come first: a group keeps every flow it reaches first. Flow groups (e.g. a substance, added from
        the Flow contribution tab) then take their flows from what is left, the emissions of processes outside the groups.
        Process groups nest (electricity uses transport, which uses electricity), so choose how impacts are attributed:
      </p>
      <div class="row">
        <label><input type="radio" checked=${rule === "direct"} onChange=${() => setRule("direct")} />
          <b>Direct</b>: each process's own emissions go to its group</label>
      </div>
      <div class="row">
        <label><input type="radio" checked=${rule === "first_match"} onChange=${() => setRule("first_match")} />
          <b>First match</b>: the first matching process on each supply path takes its whole cumulative impact</label>
      </div>
      ${error && html`<div class="error">${error}</div>`}
      ${result && html`
        <${Chart} option=${option} height=${Math.max(200, entries.length * 34 + 60)}
          onClick=${(e) => choose(e.componentType === "yAxis" ? e.value : e.name)} />
        <table class="results">
          <thead><tr><th>Group <span class="muted">· click to see its processes</span></th><th class="num">${unit}</th><th class="num">Share</th></tr></thead>
          <tbody>${entries.map(([n, v]) => html`
            <tr class="clickable ${n === group ? "selected" : ""}" onClick=${() => choose(n)}>
              <td>${n === group ? "▾" : "▸"} ${n}${n in flowGroups && html` <span class="loc-chip">flows</span>`}</td>
              <td class="num">${fmt(v)}</td><td class="num">${pct(v / total)}</td>
            </tr>`)}
          </tbody>
        </table>`}
      ${group && html`
        <div class="contributors" ref=${contributorsBox}>
          <div class="details-head">
            <span class="details-title"><b>${group}</b>${contributors
              ? `: ${contributors.count} ${contributors.count === 1 ? "process" : "processes"} · ${fmt(contributors.total)} ${unit} (${ofTotal(contributors.total)})`
              : " …"}</span>
            <button onClick=${() => setGroup(null)} aria-label="Close">×</button>
          </div>
          ${contributors && html`
            <p class="note">${contributors.flow_group
              ? `Processes emitting these flows outside the process groups${rule === "first_match" ? " and their supply chains" : ""} (their own emissions).`
              : contributors.cumulative
              ? "Each process with its supply chain (the first process of the group on each supply path)."
              : `Each process's own emissions${group === "other" && Object.keys(flowGroups).length ? ", without the flows of the flow groups" : ""}.`} Right-click a process (or double-click) to go to it in the tree, along its largest supply path.</p>
            <div class="contributors-list">
              <table>
                <thead><tr><th>Process</th><th class="num">${unit}</th><th class="pct">of group</th><th class="pct">of total</th></tr></thead>
                <tbody>
                  ${contributors.contributors.map((c) => html`
                    <tr key=${c.index} title=${`${c.reference_product} | ${c.name} | ${c.location}`}
                        onContextMenu=${(e) => menu.open(e, c)}
                        onDblClick=${() => goTo(c)}>
                      <td>${c.name}<span class="loc">${c.location}</span></td>
                      <td class="num">${fmt(c.impact)}</td><td class="pct">${ofGroup(c.impact)}</td><td class="pct">${ofTotal(c.impact)}</td>
                    </tr>`)}
                  ${contributors.others.count > 0 && html`
                    <tr class="more">
                      <td>+ ${contributors.others.count} more ${contributors.others.count === 1 ? "process" : "processes"}</td>
                      <td class="num">${fmt(contributors.others.impact)}</td>
                      <td class="pct">${ofGroup(contributors.others.impact)}</td><td class="pct">${ofTotal(contributors.others.impact)}</td>
                    </tr>`}
                </tbody>
              </table>
            </div>`}
        </div>`}
      ${menu.element}
    </div>`;
}

// ---------------------------------------------------------------- elementary flow contribution
// The whole inventory of the functional unit, characterised: which substances make the
// score. Clicking a flow lists the processes emitting it.
function FlowPanel({ info, root, amount, method, onLocate, onInfo, onFlowInfo, rules, setRules }) {
  const [by, setBy] = useState("substance");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [flow, setFlow] = useState(null);
  const [contributors, setContributors] = useState(null);
  const contributorsBox = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    request("/api/flows", { root: root.index, amount, method, by, limit: 25 })
      .then((d) => !cancelled && setResult(d)).catch((e) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [root, amount, method, by]);
  useEffect(() => setFlow(null), [root, by]);
  // Processes emitting the chosen flow (all its compartments, by substance)
  useEffect(() => {
    setContributors(null);
    if (!flow) return;
    let cancelled = false;
    request("/api/flows/contributors", { root: root.index, amount, method, flows: flow.flows, limit: 50 })
      .then((d) => !cancelled && setContributors(d)).catch((e) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [root, amount, method, flow]);
  useEffect(() => { contributorsBox.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [flow, !!contributors]);

  const goTo = (c) => onLocate(c.index);
  // Right-click a substance or flow: its pop-up (factors, metadata); a process emitting
  // it: also its exchange of that flow (comment, uncertainty)
  const flowTarget = (it) => ({ flow: it.flows[0], flows: it.flows, labels: it.compartments || [it.compartment] });
  const menu = useProcessMenu(goTo, onInfo, (c, at) =>
    onFlowInfo({ ...flowTarget(flow), flow: c.flow, activities: [c.index] }, { preventDefault() {}, clientX: at.x, clientY: at.y }));
  const methodUnit = info.methods[method].unit;
  const total = result?.total || 0;
  const ofTotal = (v) => pct(total ? v / total : 0);
  const ofFlow = (v) => pct(contributors?.total ? v / contributors.total : 0);
  const keyOf = (it) => it.flows.join(",");
  const isChosen = (it) => flow && keyOf(flow) === keyOf(it);
  const choose = (it) => setFlow(isChosen(it) ? null : it);
  const things = by === "substance" ? "substances" : "flows";
  const entries = result ? [
    ...result.items.map((it) => [by === "flow" ? `${it.name} (${it.compartment})` : it.name, it.impact, it]),
    ...(result.others.count ? [[`others (${result.others.count} ${things})`, result.others.impact, null]] : []),
  ] : [];
  const option = useMemo(() => {
    const o = baseOption();
    o.yAxis = { ...o.yAxis, type: "category", data: entries.map(([n]) => n), inverse: true, triggerEvent: true,
      axisLabel: { ...o.yAxis.axisLabel, width: 240, overflow: "truncate" } };
    o.xAxis = { ...o.xAxis, type: "value", name: methodUnit };
    o.tooltip.valueFormatter = (v) => `${fmt(v)} ${methodUnit} (${ofTotal(v)})`;
    o.series = [{ type: "bar", cursor: "pointer",
      data: entries.map(([, v, it]) => ({
        value: v,
        itemStyle: { color: cssVar(!it ? "--muted" : v < 0 ? "--bar-negative" : "--bar"),
          opacity: flow && !(it && isChosen(it)) ? 0.35 : 1 },
      })),
      label: { show: true, position: "right", formatter: (d) => ofTotal(d.value), color: cssVar("--muted") } }];
    return o;
  }, [result, flow]);

  return html`
    <div class="panel">
      <p class="note">
        Contribution of elementary flows to the score: everything the whole supply chain emits or takes
        from nature, times its characterisation factor (${methodLabel(info.methods[method])}).
      </p>
      <div class="row">
        <span class="segmented" role="group" aria-label="Flow grouping">
          <button class=${by === "substance" ? "primary" : ""} onClick=${() => setBy("substance")}
            title="One row per substance, compartments summed">By substance</button>
          <button class=${by === "flow" ? "primary" : ""} onClick=${() => setBy("flow")}
            title="One row per flow (substance and compartment)">By flow</button>
        </span>
      </div>
      ${error && html`<div class="error">${error}</div>`}
      ${result && html`
        <${Chart} option=${option} height=${Math.max(200, entries.length * 26 + 60)}
          onClick=${(e) => { const name = e.componentType === "yAxis" ? e.value : e.name; const it = entries.find(([n]) => n === name)?.[2]; if (it) choose(it); }} />
        <table class="results flow-results">
          <thead><tr>
            <th>${by === "substance" ? "Substance" : "Flow"} <span class="muted">· click for the processes emitting it</span></th>
            <th class="num">amount</th><th class="num">${methodUnit}</th><th class="num">Share</th>
          </tr></thead>
          <tbody>${result.items.map((it) => html`
            <tr key=${keyOf(it)} class="clickable ${isChosen(it) ? "selected" : ""}" onClick=${() => choose(it)}
                onContextMenu=${(e) => onFlowInfo(flowTarget(it), e)} title="Click for the processes emitting it, right-click for its details">
              <td>${isChosen(it) ? "▾" : "▸"} ${it.name} <span class="muted">${it.compartment}</span></td>
              <td class="num">${fmt(it.amount)} ${unit(it.unit)}</td><td class="num">${fmt(it.impact)}</td><td class="num">${ofTotal(it.impact)}</td>
            </tr>`)}
            ${result.others.count > 0 && html`
              <tr class="muted">
                <td>+ ${result.others.count} more ${things}</td><td></td>
                <td class="num">${fmt(result.others.impact)}</td><td class="num">${ofTotal(result.others.impact)}</td>
              </tr>`}
          </tbody>
          <tfoot><tr>
            <th>Total · ${result.count} ${things} with an impact</th><th></th>
            <th class="num">${fmt(total)}</th><th class="num">100%</th>
          </tr></tfoot>
        </table>`}
      ${flow && html`
        <div class="contributors" ref=${contributorsBox}>
          <div class="details-head">
            <span class="details-title"><b>${flow.name}</b> <span class="muted">${flow.compartment}</span>${contributors
              ? `: ${contributors.count} ${contributors.count === 1 ? "process" : "processes"} · ${fmt(contributors.total)} ${methodUnit} (${ofTotal(contributors.total)})`
              : " …"}</span>
            ${(() => {
              // A substance: all its compartments, by name; a flow: that flow only
              const rule = by === "substance"
                ? { name: flow.name, predicate: { field: "name", op: "eq", value: flow.name }, target: "flows", scope: "*" }
                : { name: `${flow.name} (${flow.compartment})`, predicate: { flows: flow.flows }, target: "flows", scope: "*" };
              const exists = rules.some((r) => r.name === rule.name && r.target === "flows");
              return html`<button disabled=${exists} onClick=${() => setRules([...rules, rule])}
                title="A flow group: in the Whole supply chain tab it takes these flows from what the process groups leave; in the tree it bundles them under direct emissions">
                ${exists ? "In groups" : "Add as a group"}</button>`;
            })()}
            <button onClick=${() => setFlow(null)} aria-label="Close">×</button>
          </div>
          ${contributors && html`
            <p class="note">Processes of the supply chain emitting it (their own emissions). Right-click a process to go to it in the tree (or double-click), see its details or its exchange of this flow (comment, uncertainty).</p>
            <div class="contributors-list">
              <table>
                <thead><tr><th>Process</th><th class="num">${unit(flow.unit)}</th><th class="num">${methodUnit}</th><th class="pct">of flow</th><th class="pct">of total</th></tr></thead>
                <tbody>
                  ${contributors.contributors.map((c) => html`
                    <tr key=${c.index} title=${`${c.reference_product} | ${c.name} | ${c.location}`}
                        onContextMenu=${(e) => menu.open(e, c)} onDblClick=${() => goTo(c)}>
                      <td>${c.name}<span class="loc">${c.location}</span></td>
                      <td class="num">${fmt(c.amount)}</td><td class="num">${fmt(c.impact)}</td>
                      <td class="pct">${ofFlow(c.impact)}</td><td class="pct">${ofTotal(c.impact)}</td>
                    </tr>`)}
                  ${contributors.others.count > 0 && html`
                    <tr class="more">
                      <td>+ ${contributors.others.count} more ${contributors.others.count === 1 ? "process" : "processes"}</td><td></td>
                      <td class="num">${fmt(contributors.others.impact)}</td>
                      <td class="pct">${ofFlow(contributors.others.impact)}</td><td class="pct">${ofTotal(contributors.others.impact)}</td>
                    </tr>`}
                </tbody>
              </table>
            </div>`}
        </div>`}
      ${menu.element}
    </div>`;
}

// ---------------------------------------------------------------- portal
// Renders children into a DOM element elsewhere in the page (the shared sidebar slot of
// a tab), as a separate Preact root that keeps its state across re-renders
function Portal({ slot, children }) {
  useEffect(() => {
    const el = document.getElementById(slot);
    if (el) render(h(Fragment, null, children), el);
  });
  useEffect(() => () => {
    const el = document.getElementById(slot);
    if (el) render(null, el);
  }, [slot]);
  return null;
}

// ---------------------------------------------------------------- functional unit view (one tab)
// The tree that takes keyboard navigation: the one clicked last (several can be visible in split panes)
let keyboardOwner = null;
// ``initial``: { state } a saved view, { inherit, root? } settings taken from another tab,
// or { defaultRoot: true } (first start: the model's default functional unit)
function FUView({ id, info, initial, visible, onSave, onTitle, onOpenTab, onCollapseSide }) {
  const box = useRef(null);
  const treeSection = useRef(null);
  // Keyboard navigation: the row the arrows move from (and its position, kept when it
  // disappears, e.g. moved to others) and the start of a Shift+arrow range
  const cursor = useRef({ id: null, index: 0 });
  const anchor = useRef(null);
  // Set by o / m / g: once the rows are back, select the cursor row, or the row now at its
  // position (moved to others, unmerged, ungrouped), so that the keys keep going
  const followCursor = useRef(false);
  const [root, setRoot] = useState(null);
  const [amount, setAmount] = useState(1);
  const [method, setMethod] = useState(0);
  const [cutoff, setCutoff] = useState(1);
  const [cutoffBasis, setCutoffBasis] = useState("parent");
  const [undo, setUndo] = useState(null);
  const [infoTarget, setInfoTarget] = useState(null);
  // Pop-ups visited by following links inside the open one (back button / Backspace);
  // opening a pop-up from outside, or closing it, starts over
  const [infoHistory, setInfoHistory] = useState([]);
  useEffect(() => { setInfoHistory([]); }, [infoTarget?.x, infoTarget?.y, !infoTarget]);
  const followInfo = (next) => {
    setInfoHistory([...infoHistory, infoTarget]);
    setInfoTarget({ ...next, x: infoTarget.x, y: infoTarget.y });
  };
  const backInfo = infoHistory.length ? () => {
    setInfoTarget(infoHistory[infoHistory.length - 1]);
    setInfoHistory(infoHistory.slice(0, -1));
  } : null;
  const [focus, setFocus] = useState(null);
  const focusTimer = useRef(null);
  const [expanded, setExpanded] = useState(new Set());
  const [pinned, setPinned] = useState(new Set());
  const [others, setOthers] = useState(new Set());
  const [merges, setMerges] = useState([]);
  const [view, setView] = useState(() => ({
    naming: loadPreference("naming", "full"),
    recipe: {
      main: loadPreference("recipeMain", DEFAULT_RECIPE.main), secondary: loadPreference("recipeSecondary", DEFAULT_RECIPE.secondary),
      flowMain: loadPreference("recipeFlowMain", DEFAULT_RECIPE.flowMain), flowSecondary: loadPreference("recipeFlowSecondary", DEFAULT_RECIPE.flowSecondary),
    },
    groupProducts: loadPreference("groupProducts", "0") === "1",
  }));
  useEffect(() => {
    savePreference("naming", view.naming);
    savePreference("recipeMain", view.recipe.main);
    savePreference("recipeSecondary", view.recipe.secondary);
    savePreference("recipeFlowMain", view.recipe.flowMain ?? DEFAULT_RECIPE.flowMain);
    savePreference("recipeFlowSecondary", view.recipe.flowSecondary ?? DEFAULT_RECIPE.flowSecondary);
    savePreference("groupProducts", view.groupProducts ? "1" : "0");
  }, [view]);
  const [rules, setRules] = useState([]);
  const [chartMethods, setChartMethods] = useState([]);
  const [selection, setSelection] = useState(new Set());
  const [rawData, setData] = useState({ methods: [], rows: [] });
  // Custom row names (labelKey → name), part of the tab's view; applied to the rows here
  // so that the tree, the pop-ups and the chart all read them
  const [labels, setLabels] = useState({});
  const [renaming, setRenaming] = useState(null);
  const data = useMemo(() => ({ ...rawData, rows: withLabels(rawData.rows, labels) }), [rawData, labels]);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("chart");
  const [groupName, setGroupName] = useState("");
  const [display, setDisplay] = useState(() => loadPreference("display", "list"));
  const [orientation, setOrientation] = useState(() => loadPreference("orientation", "horizontal"));
  useEffect(() => savePreference("display", display), [display]);
  useEffect(() => savePreference("orientation", orientation), [orientation]);
  const [cycles, setCycles] = useState(() => loadPreference("cycles", "off") === "on");
  useEffect(() => savePreference("cycles", cycles ? "on" : "off"), [cycles]);

  useEffect(() => {
    if (initial?.state?.root) restoreTab(initial.state);
    else startTab();
  }, []);
  useEffect(() => onTitle(id, root), [root]);

  const methodIndex = (name) => info.methods.findIndex((m) => m.name.join("|") === (name || []).join("|"));
  // Where a saved functional unit is in this database: { target, same }, same when it is still
  // at its saved position, else found by name and location; null when it is not there
  async function locateRoot(saved) {
    if (saved.index < info.activities) {
      const there = await request(`/api/activity/${saved.index}`);
      if (there.name === saved.name && there.location === saved.location) return { target: saved, same: true };
    }
    const found = await request("/api/search", { text: "", predicate: { all: [
      { field: "name", op: "eq", value: saved.name }, { field: "location", op: "eq", value: saved.location }] }, limit: 1 });
    return found.results.length ? { target: found.results[0], same: false } : null;
  }

  // A tab of the saved workspace. Its state refers to activities, flows and methods by position:
  // when the database was rebuilt since with another layout (positions may have moved), the
  // functional unit and methods are found again by name, and what refers to other positions
  // (expanded rows, pins, moved rows, merges, names, groups of this tree) is not restored.
  // Tabs saved before layouts were recorded: as long as the functional unit did not move.
  async function restoreTab(saved) {
    const sameLayout = saved.layout === info.layout;
    const found = sameLayout ? { target: saved.root, same: true } : await locateRoot(saved.root).catch(() => null);
    if (!found) {
      startTab();
      setError(`${saved.root.name} (${saved.root.location}), the functional unit of this tab, is not in this database any more.`);
      return;
    }
    const { target } = found;
    const same = found.same && (sameLayout || !saved.layout);
    const method = (k, name) => (sameLayout || !name ? (k < info.methods.length ? k : -1) : methodIndex(name));
    const m = method(saved.method, saved.methodName);
    setRoot(target); setAmount(saved.amount ?? 1); setMethod(m >= 0 ? m : info.default_method);
    setChartMethods((saved.chartMethods || []).map((k, n) => method(k, saved.chartMethodNames?.[n])).filter((k) => k >= 0));
    setCutoff(saved.cutoff ?? 1); setCutoffBasis(saved.cutoffBasis || "parent");
    setRules(same ? saved.rules || [] : (saved.rules || []).filter((r) => r.scope === "*"));
    setExpanded(new Set(same ? saved.expanded : [String(target.index)]));
    setPinned(new Set(same ? saved.pinned || [] : []));
    setOthers(new Set(same ? saved.others || [] : []));
    setMerges(same ? saved.merges || [] : []);
    setLabels(same ? saved.labels || {} : {});
    if (!same) setUndo({ message: "This database was rebuilt since the tab was saved: expansions, pins, moved rows, merges and names were not restored.", state: {} });
  }

  function startTab() {
    const i = info;
    // A new tab keeps the method, cut-off and tree-wide rules of the tab it was opened from
    const base = initial?.inherit;
    if (base && base.method < i.methods.length) {
      setMethod(base.method);
      setChartMethods(base.chartMethods || []);
      setCutoff(base.cutoff ?? 1);
      setCutoffBasis(base.cutoffBasis || "parent");
      setRules((base.rules || []).filter((rule) => rule.scope === "*"));
    } else {
      setMethod(i.default_method);
      const family = i.methods[i.default_method].name[0];
      setChartMethods(i.methods.map((m, k) => [m, k]).filter(([m]) => m.name[0] === family).map(([, k]) => k));
    }
    if (initial?.root) selectRoot(initial.root);
    else if (initial?.defaultRoot && i.default_root) selectRoot(i.default_root);
    else box.current?.querySelector(".fu-input input")?.focus({ preventScroll: true });
  }

  function selectRoot(r) {
    setRoot(r);
    setExpanded(new Set([String(r.index)]));
    setPinned(new Set());
    setOthers(new Set());
    setMerges([]);
    setRules((current) => current.filter((rule) => rule.scope === "*"));
    setSelection(new Set());
    setUndo(null);
  }

  // Pins are exceptions to one cut-off, and moved rows mix with the cut-off rows in
  // "others": a new cut-off starts from a clean slate, with an undo.
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const describeCustom = (p, o) => [p.size && `${plural(p.size, "pinned row")}`, o.size && `${plural(o.size, "row")} moved to others`].filter(Boolean).join(" and ");
  function changeCutoff(next, basis = cutoffBasis) {
    if (pinned.size || others.size) {
      setUndo({ message: `Cut-off changed: ${describeCustom(pinned, others)} reset.`, state: { cutoff, cutoffBasis, pinned, others } });
      setPinned(new Set());
      setOthers(new Set());
    }
    setCutoff(next);
    setCutoffBasis(basis);
  }
  // Back to the plain tree: collapsed, no pins or moved rows, no groups (rules, quick
  // groups, group by product), no merges and no custom names; the notice can undo it all
  function resetView() {
    const removed = [describeCustom(pinned, others), rules.length && plural(rules.length, "grouping rule"),
      merges.length && plural(merges.length, "merge"), view.groupProducts && "group by product",
      Object.keys(labels).length && plural(Object.keys(labels).length, "custom name")].filter(Boolean).join(", ");
    setUndo({ message: `View reset${removed ? ` (${removed} removed)` : ""}.`, state: { expanded, pinned, others, rules, merges, labels, view } });
    setExpanded(new Set([String(root.index)]));
    setPinned(new Set());
    setOthers(new Set());
    setRules([]);
    setMerges([]);
    setLabels({});
    if (view.groupProducts) setView({ ...view, groupProducts: false });
    setSelection(new Set());
    setInfoTarget(null);
  }
  function applyUndo() {
    const state = undo.state;
    const setters = {
      root: setRoot, amount: setAmount, method: setMethod, cutoff: setCutoff, cutoffBasis: setCutoffBasis,
      expanded: setExpanded, rules: setRules, pinned: setPinned, others: setOthers, merges: setMerges,
      labels: setLabels, chartMethods: setChartMethods, view: setView,
    };
    Object.entries(state).forEach(([key, value]) => setters[key]?.(value));
    setUndo(null);
  }

  const viewState = (exp = expanded) => ({
    root: [root.index], amount, expanded: [...exp], rules, cutoff: cutoff / 100, cutoff_basis: cutoffBasis, method, methods: chartMethods,
    pinned: [...pinned], others: [...others], merges, group_products: view.groupProducts,
  });

  useEffect(() => {
    if (!root || !info) return;
    let cancelled = false;
    request("/api/rows", viewState())
      .then((d) => { if (!cancelled) { setData(d); setError(null); } })
      .catch((e) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [root, amount, method, cutoff, cutoffBasis, expanded, rules, chartMethods, pinned, others, merges, view.groupProducts, info]);
  // The tab's state is kept for the next visit (renaming does not reload the rows)
  useEffect(() => {
    if (!root || !info) return;
    onSave(id, { root, amount, method, cutoff, cutoffBasis, expanded: [...expanded], rules, chartMethods, pinned: [...pinned], others: [...others], merges, labels,
      layout: info.layout, methodName: info.methods[method]?.name, chartMethodNames: chartMethods.map((k) => info.methods[k]?.name) });
  }, [root, amount, method, cutoff, cutoffBasis, expanded, rules, chartMethods, pinned, others, merges, labels, info]);
  function startRename(row) {
    if (!labelKey(row)) return;
    // The tree view has no inline editor: ask
    if (display === "graph") {
      const name = window.prompt("Rename (empty: original name)", rowNames(row, rowsById, view).main);
      if (name !== null) rename(row, name);
    } else setRenaming(row.id);
  }
  function rename(row, name) {
    setRenaming(null);
    if (name === null) return;
    const key = labelKey(row);
    const next = { ...labels };
    const original = baseRowNames(row, rowsById, view).main;
    if (!name.trim() || name.trim() === original) delete next[key];
    else next[key] = name.trim();
    setLabels(next);
  }

  const rootImpact = data.rows[0]?.impacts[0] || 0;
  const rowsById = useMemo(() => new Map(data.rows.map((r) => [r.id, r])), [data]);
  const selectedRows = [...selection].map((id) => rowsById.get(id)).filter(Boolean);
  const siblings = selectedRows.length >= 2 && selectedRows.every((r) => ownerOf(r) === ownerOf(selectedRows[0]))
    && !ownerOf(selectedRows[0]).includes("/g:");
  const canGroup = siblings && (selectedRows.every((r) => r.kind === "process") || selectedRows.every((r) => r.kind === "flow"));
  // Groups of processes carry their members' products, so they can be merged too: one
  // group alone (turned into a merged process), or with sibling processes
  const mergeable = (r) => r.kind === "process" || r.kind === "merged" || (r.kind === "group" && r.products);
  const canMerge = (siblings && selectedRows.every(mergeable))
    || (selectedRows.length === 1 && selectedRows[0].kind === "group" && !!selectedRows[0].products);
  const mergeSelected = () => mergeRows(selectedRows);
  // Merges come before rules, so a merged group's rule no longer claims its members; a
  // manual group of this parent has no use left and is removed
  function mergeRows(rows) {
    const scope = ownerOf(rows[0]);
    const products = [...new Set(rows.flatMap((r) => (r.products || [productOf(r.id)])))];
    // Merging a merged row with more processes replaces its merge
    setMerges([...merges.filter((m) => m.scope !== scope || !m.products.some((p) => products.includes(p))), { scope, products }]);
    const groups = rows.filter((r) => r.kind === "group");
    const manual = new Set(groups.map(manualRuleFor).filter((i) => i >= 0));
    if (manual.size) setRules(rules.filter((_, j) => !manual.has(j)));
    const mergedId = `${scope}/m:${[...products].sort((a, b) => a - b).join("+")}`;
    if (groups.some((g) => g.expanded)) {
      const groupIds = new Set(groups.flatMap((g) => [g.id, `!${g.id}`]));
      setExpanded(new Set([...[...expanded].filter((id) => !groupIds.has(id)), mergedId]));
    }
    setSelection(new Set());
    return mergedId;
  }
  function unmerge(row) {
    setMerges(merges.filter((m) => !(m.scope === ownerOf(row) && m.products.length === row.products.length
      && m.products.every((p) => row.products.includes(p)))));
  }

  function toggle(rows) {
    // Same semantics as View.expand / View.collapse ("!" marks a group collapsed on purpose);
    // several rows for a node of the tree view's cycles mode, shown in several places
    const next = new Set(expanded);
    for (const row of Array.isArray(rows) ? rows : [rows]) {
      if (row.expanded) {
        next.delete(row.id);
        if (row.kind === "group") next.add(`!${row.id}`);
      } else {
        next.delete(`!${row.id}`);
        next.add(row.id);
      }
    }
    setExpanded(next);
  }
  function select(row, additive) {
    // Clicking the only selected row again unselects it (closes the others panel)
    if (!additive && selection.size === 1 && selection.has(row.id)) { setSelection(new Set()); return; }
    const next = new Set(additive ? selection : []);
    additive && selection.has(row.id) ? next.delete(row.id) : next.add(row.id);
    setSelection(next);
    cursor.current = { id: row.id, index: data.rows.indexOf(row) };
    if (!additive) anchor.current = row.id;
  }
  // ``target``: { flow, flows?, activities? } (activities whose exchange of the flow is shown)
  const showFlowInfo = (target, e) => { e.preventDefault(); setInfoTarget({ ...target, x: e.clientX, y: e.clientY }); };
  // The pop-up of a row: a process (or direct emissions: its emitter), a flow, or the
  // members of a bundle (others, merged, group); null when there is nothing to show
  function infoTargetOf(row) {
    if (row.kind === "rest" || row.kind === "merged" || row.kind === "group") return { bundle: row.id };
    if (row.kind === "flow") return { flow: row.flow.index, activities: (row.emitters || []).map((m) => m.index) };
    return row.meta ? { index: row.meta.index, row, direct: row.kind === "direct" } : null;
  }
  function showInfo(row, e) {
    e.preventDefault();
    const target = infoTargetOf(row);
    if (target) setInfoTarget({ ...target, x: e.clientX, y: e.clientY });
  }
  // A visible row, or one bundled in an others row (bundle pop-ups follow the rows)
  const findRow = (rowId) => rowsById.get(rowId) || data.rows.flatMap((r) => r.members || []).find((m) => m.id === rowId);
  // Open the tree down to a process found elsewhere (Whole supply chain panel)
  async function goTo(product, context = {}) {
    try {
      const found = await request("/api/locate", { ...viewState(), product, ...context });
      const target = found.path[found.path.length - 1];
      setUndo({
        message: `Opened the tree at ${target.name} (${target.location}), ${found.path.length - 1} levels down along its largest supply path${found.found ? "" : " — shown as far as merged rows allow"}.`,
        state: { expanded, pinned, others },
      });
      setExpanded(new Set(found.expanded));
      setPinned(new Set(found.pinned));
      setOthers(new Set(found.others));
      setFocus(found.target);
      clearTimeout(focusTimer.current);
      focusTimer.current = setTimeout(() => setFocus(null), 4000);
      setError(null);
    } catch (e) { setError(e.message); }
  }
  function groupSelected() {
    const parent = ownerOf(selectedRows[0]);
    const name = groupName.trim() || `group ${rules.filter((r) => r.predicate?.products || r.predicate?.flows).length + 1}`;
    const rule = selectedRows[0].kind === "flow"
      ? { predicate: { flows: selectedRows.map((r) => r.flow.index) }, target: "flows" }
      : { predicate: { products: selectedRows.map((r) => productOf(r.id)) } };
    setRules([...rules, { name, ...rule, scope: parent }]);
    setExpanded(new Set([...expanded, `${parent}/g:${name}`]));
    setSelection(new Set());
    setGroupName("");
    return `${parent}/g:${name}`;
  }
  const unpin = (row) => { const next = new Set(pinned); next.delete(row.id); setPinned(next); };
  // Out of others: no longer moved there, and pinned in case it is below the cut-off
  function pin(ids) {
    setOthers(new Set([...others].filter((id) => !ids.includes(id))));
    setPinned(new Set([...pinned, ...ids]));
  }
  function moveToOthers(ids) {
    setOthers(new Set([...others, ...ids]));
    const nextPinned = new Set(pinned);
    ids.forEach((id) => nextPinned.delete(id));
    setPinned(nextPinned);
    setSelection(new Set());
  }
  const movableSelection = selectedRows.filter(movable);
  const bundle = infoTarget?.bundle ? findRow(infoTarget.bundle) : null;
  // A bundle that disappeared (all its members shown, unmerged, ungrouped) closes its pop-up
  useEffect(() => { if (infoTarget?.bundle && !bundle) setInfoTarget(null); }, [infoTarget, bundle]);
  const activeRules = rules.filter((r) => !r.disabled).length;
  const manualRuleFor = (groupRow) => rules.findIndex((r) => r.predicate && !r.disabled && r.scope === ownerOf(groupRow) && `${ownerOf(groupRow)}/g:${r.name}` === groupRow.id);
  function ungroup(groupRow) {
    const i = manualRuleFor(groupRow);
    if (i >= 0) setRules(rules.filter((_, j) => j !== i));
  }
  // Saved views store names (method, functional unit) as well as positions: node ids are
  // positions in the model, valid only for the model the view was saved from.
  function viewFile() {
    return {
      format: "lca_viewer view", version: 1,
      model: { title: info.title, activities: info.activities },
      root, amount, method: info.methods[method].name, chartMethods: chartMethods.map((k) => info.methods[k].name),
      cutoff, cutoffBasis, expanded: [...expanded], rules, pinned: [...pinned], others: [...others], merges, labels,
      display: { naming: view.naming, recipe: view.recipe, groupProducts: view.groupProducts },
    };
  }
  function exportView() {
    const name = root ? `${root.name}-${root.location}`.replace(/[^\w.-]+/g, "_").slice(0, 60) : "view";
    download(`lca-view-${name}.json`, JSON.stringify(viewFile(), null, 2), "application/json");
  }
  async function copyView() {
    try { await navigator.clipboard.writeText(JSON.stringify(viewFile(), null, 2)); setUndo({ message: "View copied to the clipboard as JSON.", state: {} }); }
    catch { setError("The browser did not allow copying to the clipboard."); }
  }
  async function loadView(file) {
    try {
      const saved = JSON.parse(await file.text());
      if (!saved.root || !Array.isArray(saved.expanded)) throw new Error("This file is not a saved view.");
      // Same functional unit at the same position: everything applies; otherwise find it by name
      const found = await locateRoot(saved.root);
      if (!found) throw new Error(`${saved.root.name} (${saved.root.location}) is not in this database.`);
      const { target, same: sameModel } = found;
      setUndo({
        message: `View loaded from ${file.name}${sameModel ? "" : " — saved from another model: expansions, pins, moved rows, merges and names were not restored"}.`,
        state: { root, amount, method, cutoff, cutoffBasis, expanded, rules, pinned, others, merges, labels, chartMethods, view },
      });
      setRoot(target);
      setAmount(saved.amount ?? 1);
      const m = methodIndex(saved.method);
      if (m >= 0) setMethod(m);
      setChartMethods((saved.chartMethods || []).map(methodIndex).filter((k) => k >= 0));
      setCutoff(saved.cutoff ?? 1);
      setCutoffBasis(saved.cutoffBasis || "parent");
      setRules(sameModel ? saved.rules || [] : (saved.rules || []).filter((r) => r.scope === "*"));
      setExpanded(new Set(sameModel ? saved.expanded : [String(target.index)]));
      setPinned(new Set(sameModel ? saved.pinned || [] : []));
      setOthers(new Set(sameModel ? saved.others || [] : []));
      setMerges(sameModel ? saved.merges || [] : []);
      setLabels(sameModel ? saved.labels || {} : {});
      if (saved.display) setView({ ...view, ...saved.display });
      setSelection(new Set());
      setError(null);
    } catch (e) {
      setError(`Could not load the view: ${e.message}`);
    }
  }

  // Keys on the selected row (see docs/ui.md); returns true when the key was used
  function onTreeKey(e) {
    const rows = data.rows;
    if (!visible || !rows.length || e.ctrlKey || e.metaKey || e.altKey) return false;
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const found = rows.findIndex((r) => r.id === cursor.current.id);
    const row = rows[found >= 0 ? found : Math.min(cursor.current.index, rows.length - 1)];
    const moveTo = (r) => {
      cursor.current = { id: r.id, index: rows.indexOf(r) };
      anchor.current = r.id;
      setSelection(new Set([r.id]));
      setInfoTarget(null);
    };
    // After g / m the new group or merged row is selected, so that the same key undoes it
    const selectNew = (rowId) => {
      cursor.current = { id: rowId, index: found };
      anchor.current = rowId;
      setSelection(new Set([rowId]));
    };
    if (key === "Escape") {
      if (infoTarget || !selection.size) return false;  // pop-ups close themselves
      setSelection(new Set());
      return true;
    }
    // Nothing selected yet (or the row is gone): the arrows start from the last position
    if (found < 0 || !selection.size) {
      if (!key.startsWith("Arrow")) return false;
      moveTo(row);
      return true;
    }
    if ((key === "ArrowDown" || key === "ArrowUp") && e.shiftKey) {
      // Extend over siblings only: what Group and Merge act on
      const siblings = rows.filter((r) => r.parent === row.parent);
      const next = siblings[siblings.indexOf(row) + (key === "ArrowDown" ? 1 : -1)];
      if (!next) return true;
      const start = siblings.findIndex((r) => r.id === anchor.current);
      const from = start >= 0 ? start : siblings.indexOf(row);
      const to = siblings.indexOf(next);
      setSelection(new Set(siblings.slice(Math.min(from, to), Math.max(from, to) + 1).map((r) => r.id)));
      if (start < 0) anchor.current = row.id;
      cursor.current = { id: next.id, index: rows.indexOf(next) };
      return true;
    }
    switch (key) {
      case "ArrowDown": if (rows[found + 1]) moveTo(rows[found + 1]); return true;
      case "ArrowUp": if (found > 0) moveTo(rows[found - 1]); return true;
      case "ArrowRight":
        // Expand; if already open, go to the first child
        if (row.expandable && !row.expanded) toggle(row);
        else if (rows[found + 1]?.parent === row.id) moveTo(rows[found + 1]);
        return true;
      case "ArrowLeft":
        // Collapse; if already closed, go to the parent
        if (row.expandable && row.expanded) toggle(row);
        else if (rowsById.has(row.parent)) moveTo(rowsById.get(row.parent));
        return true;
      case " ": {
        if (infoTarget) { setInfoTarget(null); return true; }
        const el = treeSection.current?.querySelector(`[data-id="${CSS.escape(row.id)}"]`)?.getBoundingClientRect();
        showInfo(row, { preventDefault() {}, clientX: el ? el.left + 40 : 200, clientY: el ? el.bottom : 200 });
        return true;
      }
      case "o":
        // Inside an expanded others row, o pins (takes out of others) instead
        if (movableSelection.length && movableSelection.every(inOthers)) pin(movableSelection.map((r) => r.id));
        else if (movableSelection.length) { followCursor.current = true; moveToOthers(movableSelection.filter((r) => !inOthers(r)).map((r) => r.id)); }
        return true;
      case "m":
        followCursor.current = true;
        if (selectedRows.length === 1 && row.kind === "merged" && !row.by_rule) unmerge(row);
        else if (canMerge) selectNew(mergeSelected());
        else followCursor.current = false;
        return true;
      case "r":
        if (selectedRows.length === 1) startRename(row);
        return true;
      case "g":
        followCursor.current = true;
        if (selectedRows.length === 1 && row.kind === "group" && manualRuleFor(row) >= 0) ungroup(row);
        else if (canGroup) selectNew(groupSelected());
        else followCursor.current = false;
        return true;
      default:
        return false;
    }
  }
  const treeKey = useRef(onTreeKey);
  treeKey.current = onTreeKey;
  useEffect(() => {
    const onKey = (e) => {
      if (keyboardOwner !== id || e.target.closest?.("input, textarea, select, [contenteditable]")) return;
      // A tree button clicked last (▸, a chip) would also react to Space
      if (document.activeElement?.tagName === "BUTTON" && treeSection.current?.contains(document.activeElement)) document.activeElement.blur();
      if (treeKey.current(e)) e.preventDefault();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [id]);
  useEffect(() => {
    if (!followCursor.current || !data.rows.length) return;
    followCursor.current = false;
    const row = rowsById.get(cursor.current.id) || data.rows[Math.min(cursor.current.index, data.rows.length - 1)];
    cursor.current = { id: row.id, index: data.rows.indexOf(row) };
    anchor.current = row.id;
    setSelection(new Set([row.id]));
  }, [data]);
  // Keep the row moved to in view (list display)
  useEffect(() => {
    const el = cursor.current.id && treeSection.current?.querySelector(`.tree [data-id="${CSS.escape(cursor.current.id)}"]`);
    if (el && selection.has(cursor.current.id)) el.scrollIntoView({ block: "nearest" });
  }, [selection, data]);

  const methodInfo = info.methods[method];
  return html`
    <div class="fu-view" ref=${box} style=${{ display: visible ? "" : "none" }}>
      <header class="fu-header">
        <${FunctionalUnit} root=${root} onSelect=${selectRoot} fields=${info.query_fields} />
        <label>Amount <${NumberInput} class="amount" value=${amount} min=${1e-12} step="any"
          onValue=${setAmount} aria-label="Amount" />${root ? root.unit : ""}</label>
        <${MethodSelect} methods=${info.methods} value=${method} onChange=${setMethod} />
        <label>Cut-off <${NumberInput} class="cutoff" value=${cutoff} min=${0} max=${100} step="0.5"
          onValue=${(v) => changeCutoff(v)} aria-label="Cut-off" title="Changing the cut-off resets pinned rows and rows moved to others" />% of</label>
        <span class="segmented basis" role="group" aria-label="Cut-off basis">
          <button class=${cutoffBasis === "total" ? "primary" : ""} onClick=${() => cutoffBasis !== "total" && changeCutoff(cutoff, "total")}
            title="Under each parent, the smallest children together weighing at most the cut-off of the total go to others">total</button>
          <button class=${cutoffBasis === "parent" ? "primary" : ""} onClick=${() => cutoffBasis !== "parent" && changeCutoff(cutoff, "parent")}
            title="Under each parent, the smallest children together weighing at most the cut-off of that parent go to others">parent</button>
        </span>
      </header>
        <section class="tree-section" ref=${treeSection} onMouseDown=${() => { keyboardOwner = id; }}>
          <div class="toolbar">
            <div class="segmented" role="group" aria-label="Display">
              <button class=${display === "list" ? "primary" : ""} onClick=${() => setDisplay("list")}>List</button>
              <button class=${display === "graph" ? "primary" : ""} onClick=${() => setDisplay("graph")}>Tree</button>
            </div>
            <button onClick=${() => setExpanded(new Set(root ? [String(root.index)] : []))} title="Collapse everything below the root">Collapse</button>
            <button onClick=${resetView} disabled=${!root} title="Collapse the tree, clear pinned rows and rows moved to others, and remove all groups, merges and custom names (undoable)">Reset view</button>
            <span class="spacer"></span>
            <${SaveMenu} disabled=${!root} onSave=${exportView} onLoad=${loadView} onCopy=${copyView} />
          </div>
          <${ViewBar} view=${view} setView=${setView}
            example=${(() => { const r = data.rows.find((x) => x.kind === "process" && x.depth > 0); return r ? rowNames(r, rowsById, view) : null; })()} />
          ${selection.size > 0 && html`
            <div class="selection-bar">
              <span>${selection.size} selected${selection.size === 1 ? " · Ctrl/⌘-click to add siblings" : ""}</span>
              <input placeholder="group name" value=${groupName} onInput=${(e) => setGroupName(e.target.value)} aria-label="New group name" />
              <button disabled=${!canGroup} onClick=${groupSelected}
                title="Bundle the selected sibling rows under one row; expanding it shows them">Group</button>
              <button disabled=${!canMerge} onClick=${mergeSelected}
                title="Combine the selected sibling processes (or the processes of a selected group) into one process: inventories added, common inputs summed">Merge</button>
              ${movableSelection.length && movableSelection.every(inOthers)
                ? html`<button onClick=${() => pin(movableSelection.map((r) => r.id))}
                    title="Take the selected rows out of others: shown in the tree and the chart despite the cut-off">Pin</button>`
                : html`<button disabled=${!movableSelection.length} onClick=${() => moveToOthers(movableSelection.filter((r) => !inOthers(r)).map((r) => r.id))}
                    title="Move the selected rows to their parent's others row">To others</button>`}
              <button onClick=${() => setSelection(new Set())} aria-label="Clear selection">×</button>
            </div>`}
          ${undo && html`
            <div class="notice" role="status">
              <span>${undo.message}</span>
              ${Object.keys(undo.state).length > 0 && html`<button onClick=${applyUndo}>Undo</button>`}
              <button onClick=${() => setUndo(null)} aria-label="Dismiss">×</button>
            </div>`}
          ${error && html`<div class="error">${error}</div>`}
          ${display === "list"
            ? html`<${Tree} rows=${data.rows} rootImpact=${rootImpact} shareBasis=${cutoffBasis} selection=${selection} onToggle=${toggle} onSelect=${select}
                renaming=${renaming} onStartRename=${startRename} onRename=${rename}
                onUngroup=${ungroup} ungroupable=${(r) => manualRuleFor(r) >= 0} onMergeGroup=${(r) => mergeRows([r])} onUnpin=${unpin} onPin=${pin} onMoveToOthers=${moveToOthers} onInfo=${showInfo} onUnmerge=${unmerge} view=${view} focus=${focus} />`
            : html`<${TreeGraph} rows=${data.rows} rootImpact=${rootImpact} shareBasis=${cutoffBasis} selection=${selection} onToggle=${toggle} onSelect=${select}
                onUngroup=${ungroup} ungroupable=${(r) => manualRuleFor(r) >= 0} onMergeGroup=${(r) => mergeRows([r])} onUnpin=${unpin} onPin=${pin} onMoveToOthers=${moveToOthers} onInfo=${showInfo} onUnmerge=${unmerge} view=${view}
                orientation=${orientation} setOrientation=${setOrientation} cycles=${cycles} setCycles=${setCycles} focus=${focus} />`}
          ${bundle?.kind === "rest" && html`<${OthersPopup} target=${infoTarget} rest=${bundle} rows=${data.rows} rootImpact=${rootImpact}
              cutoff=${cutoff} cutoffBasis=${cutoffBasis} pinned=${pinned} setPinned=${setPinned} others=${others} setOthers=${setOthers}
              view=${view} onClose=${() => setInfoTarget(null)} onBack=${backInfo}
              onMember=${(r) => { const t = infoTargetOf(r); if (t) followInfo(t); }} />`}
          ${bundle && bundle.kind !== "rest" && bundle.parts && html`<${BundlePopup} target=${infoTarget} row=${bundle}
              methodInfo=${methodInfo} rootImpact=${rootImpact} onClose=${() => setInfoTarget(null)} onBack=${backInfo}
              actions=${bundle.kind === "merged"
                ? !bundle.by_rule && html`<button onClick=${() => unmerge(bundle)} title="Split back into the original processes">Unmerge</button>`
                : html`
                  ${bundle.products && html`<button onClick=${() => mergeRows([bundle])} title="Merge into one process whose inventory is the union of the members'">Merge</button>`}
                  ${manualRuleFor(bundle) >= 0 && html`<button onClick=${() => ungroup(bundle)}>Ungroup</button>`}`}
              onPart=${(p) => followInfo(p.flow !== undefined
                ? { flow: p.flow, activities: (bundle.emitters || []).map((m) => m.index) }
                : { index: p.index, row: null })} />`}
          ${infoTarget?.flow !== undefined && html`<${FlowInfoPopup} target=${infoTarget} method=${method} methods=${info.methods}
              onClose=${() => setInfoTarget(null)} onOpen=${(index) => followInfo({ index, row: null })} onBack=${backInfo} />`}
          ${infoTarget && infoTarget.flow === undefined && !infoTarget.bundle && html`<${InfoPopup} target=${infoTarget} method=${method} methodInfo=${methodInfo}
              rootImpact=${rootImpact} rowsById=${rowsById} onClose=${() => setInfoTarget(null)}
              onUseAsRoot=${selectRoot} onOpenTab=${(r) => onOpenTab(id, r)} onMoveToOthers=${moveToOthers} root=${root} amount=${amount}
              onOpen=${(index) => followInfo({ ...infoTarget, index, row: null, direct: false })} onGoTo=${(index) => goTo(index)}
              onFlow=${(flow, activity) => followInfo({ flow, activities: [activity] })} onBack=${backInfo} />`}
          <div class="toolbar"><span class="hint">
            ${root ? `Total: ${fmt(rootImpact)} ${methodInfo.unit} — ${methodLabel(methodInfo)}` : ""}
          </span></div>
        </section>
      <${Portal} slot=${`side-${id}`}>
        <section class="side">
          <div class="tabs" role="tablist">
            ${[["chart", "Process contribution"], ["flows", "Flow contribution"], ["groups", `Grouping${activeRules ? ` (${activeRules})` : ""}`], ["global", "Whole supply chain"]].map(([key, label]) => html`
              <button role="tab" class=${tab === key ? "active" : ""} onClick=${() => setTab(key)}>${label}</button>`)}
            <span class="spacer"></span>
            <button class="collapse-side" onClick=${onCollapseSide} title="Hide the side panel" aria-label="Hide the side panel">»</button>
          </div>
          ${tab === "chart" && data.rows.length > 0 && html`<${ChartPanel} data=${data} info=${info} method=${method}
              chartMethods=${chartMethods} setChartMethods=${setChartMethods} view=${view} />`}
          ${tab === "flows" && root && html`<${FlowPanel} info=${info} root=${root} amount=${amount} method=${method}
              onLocate=${goTo} onInfo=${(index, x, y) => setInfoTarget({ index, row: null, x, y })} onFlowInfo=${showFlowInfo}
              rules=${rules} setRules=${setRules} />`}
          ${tab === "groups" && html`<${GroupPanel} info=${info} rules=${rules} setRules=${setRules}
              selectedRow=${selectedRows.length === 1 ? selectedRows[0] : null} />`}
          ${tab === "global" && html`<${GlobalPanel} info=${info} root=${root} amount=${amount} method=${method} rules=${rules}
              onLocate=${goTo} onInfo=${(index, x, y) => setInfoTarget({ index, row: null, x, y })} />`}
        </section>
      <//>
    </div>`;
}

// ---------------------------------------------------------------- workspace: tabs and split panes
// Like a notes app: each tab is one functional unit; panes hold tabs and can be split
// left/right or top/bottom by dragging a tab onto a pane's edge. One side panel (chart,
// grouping, whole supply chain) shows the active tab of the pane used last.
const WORKSPACE_KEY = `${STORAGE_KEY}.workspace`;
let idCounter = 0;
const newId = (prefix) => `${prefix}${Date.now().toString(36)}${(idCounter++).toString(36)}`;
function loadWorkspace() {
  try { return JSON.parse(localStorage.getItem(WORKSPACE_KEY) || "null"); } catch { return null; }
}
function saveWorkspace(workspace) {
  try { localStorage.setItem(WORKSPACE_KEY, JSON.stringify(workspace)); } catch { /* storage unavailable */ }
}

// Layout: { type: "pane", id, tabs, active } or { type: "split", id, dir: "row" | "column", children, sizes }
const panesOf = (node) => (node.type === "pane" ? [node] : node.children.flatMap(panesOf));
// Applies fn (pane → pane, split or null) to every pane; empty splits disappear, a split
// left with one child is replaced by it, a split inside a split of the same direction is flattened
function mapPanes(node, fn) {
  if (node.type === "pane") return fn(node);
  const children = [], sizes = [];
  node.children.forEach((child, i) => {
    const mapped = mapPanes(child, fn);
    if (!mapped) return;
    if (mapped.type === "split" && mapped.dir === node.dir) {
      const total = mapped.sizes.reduce((a, b) => a + b, 0);
      mapped.children.forEach((c, j) => { children.push(c); sizes.push(node.sizes[i] * mapped.sizes[j] / total); });
    } else {
      children.push(mapped);
      sizes.push(node.sizes[i]);
    }
  });
  if (!children.length) return null;
  if (children.length === 1) return children[0];
  return { ...node, children, sizes };
}
function removeFromPane(layout, paneId, tabId) {
  return mapPanes(layout, (p) => {
    if (p.id !== paneId) return p;
    const tabs = p.tabs.filter((t) => t !== tabId);
    if (!tabs.length) return null;
    const i = p.tabs.indexOf(tabId);
    return { ...p, tabs, active: p.active === tabId ? tabs[Math.min(i, tabs.length - 1)] : p.active };
  });
}
// zone: "center" (into the pane, before the tab ``before`` or last) or an edge: a new pane there
function insertTab(layout, paneId, tabId, zone, before = null) {
  return mapPanes(layout, (p) => {
    if (p.id !== paneId) return p;
    if (zone === "center") {
      const tabs = p.tabs.filter((t) => t !== tabId);
      const at = before ? tabs.indexOf(before) : -1;
      tabs.splice(at < 0 ? tabs.length : at, 0, tabId);
      return { ...p, tabs, active: tabId };
    }
    const pane = { type: "pane", id: newId("p"), tabs: [tabId], active: tabId };
    const first = zone === "left" || zone === "top";
    return { type: "split", id: newId("s"), dir: zone === "left" || zone === "right" ? "row" : "column",
             children: first ? [pane, p] : [p, pane], sizes: [1, 1] };
  });
}
function resizeSplit(node, splitId, sizes) {
  if (node.type === "pane") return node;
  if (node.id === splitId) return { ...node, sizes };
  return { ...node, children: node.children.map((c) => resizeSplit(c, splitId, sizes)) };
}
const paneOfTab = (layout, tabId) => panesOf(layout).find((p) => p.tabs.includes(tabId));

function SplitIcon({ dir }) {
  return html`<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3">
    <rect x="1.5" y="1.5" width="11" height="11" rx="1.5" />
    ${dir === "row" ? html`<line x1="7" y1="1.5" x2="7" y2="12.5" />` : html`<line x1="1.5" y1="7" x2="12.5" y2="7" />`}
  </svg>`;
}

function PaneView({ pane, focused, multiple, titles, dragging, drop, setDrop, actions, children }) {
  const body = useRef(null);
  function zoneAt(e) {
    const r = body.current.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    if (x < 0.25) return "left";
    if (x > 0.75) return "right";
    if (y < 0.25) return "top";
    if (y > 0.75) return "bottom";
    return "center";
  }
  const over = (zone, before = null) => (e) => {
    if (!dragging) return;
    e.preventDefault();
    e.stopPropagation();
    const z = typeof zone === "function" ? zone(e) : zone;
    if (drop?.pane !== pane.id || drop.zone !== z || drop.before !== before) setDrop({ pane: pane.id, zone: z, before });
  };
  const dropHere = (e) => {
    if (!dragging || !drop) return;
    e.preventDefault();
    actions.move(dragging, pane.id, drop.zone, drop.before);
  };
  const title = (id) => titles[id] ? titles[id].name : "New tab";
  return html`
    <div class="pane ${focused && multiple ? "focused" : ""}" onMouseDownCapture=${() => actions.focus(pane.id)}>
      <div class="pane-tabs" role="tablist" onDragOver=${over("center")} onDrop=${dropHere}>
        ${pane.tabs.map((id) => html`
          <div key=${id} role="tab" aria-selected=${id === pane.active} draggable="true"
               class="pane-tab ${id === pane.active ? "active" : ""} ${drop?.pane === pane.id && drop.before === id ? "drop-before" : ""}"
               title=${titles[id] ? `${titles[id].name} (${titles[id].location})` : "New tab: choose a functional unit"}
               onClick=${() => actions.select(pane.id, id)}
               onAuxClick=${(e) => e.button === 1 && actions.close(id)}
               onDragStart=${(e) => { e.dataTransfer.setData("text/plain", id); e.dataTransfer.effectAllowed = "move"; actions.drag(id); }}
               onDragEnd=${() => actions.drag(null)}
               onDragOver=${over("center", id)}>
            <span class="pane-tab-title">${title(id)}</span>
            ${titles[id] && html`<span class="loc">${titles[id].location}</span>`}
            <button class="close" aria-label="Close tab" title="Close (middle-click)"
              onClick=${(e) => { e.stopPropagation(); actions.close(id); }}>×</button>
          </div>`)}
        <button class="new-tab" onClick=${() => actions.add(pane.id)} title="New tab" aria-label="New tab">+</button>
        <span class="spacer"></span>
        <button class="icon" onClick=${() => actions.split(pane.id, "right")} title="Split right: open a copy of this tab on the right"
          aria-label="Split right"><${SplitIcon} dir="row" /></button>
        <button class="icon" onClick=${() => actions.split(pane.id, "bottom")} title="Split down: open a copy of this tab below"
          aria-label="Split down"><${SplitIcon} dir="column" /></button>
      </div>
      <div class="pane-body" ref=${body} onDragOver=${over(zoneAt)} onDrop=${dropHere}
           onDragLeave=${(e) => !body.current.contains(e.relatedTarget) && drop?.pane === pane.id && setDrop(null)}>
        ${children}
        ${dragging && drop?.pane === pane.id && !drop.before && html`<div class="drop-zone ${drop.zone}"></div>`}
      </div>
    </div>`;
}

function LayoutView({ node, renderPane, onResize }) {
  const ref = useRef(null);
  if (node.type === "pane") return renderPane(node);
  function startResize(e, i) {
    e.preventDefault();
    const total = node.sizes.reduce((a, b) => a + b, 0);
    const rect = ref.current.getBoundingClientRect();
    const span = node.dir === "row" ? rect.width : rect.height;
    const start = node.dir === "row" ? e.clientX : e.clientY;
    const [a, b] = [node.sizes[i - 1], node.sizes[i]];
    const move = (ev) => {
      const delta = ((node.dir === "row" ? ev.clientX : ev.clientY) - start) / span * total;
      const d = Math.max(-a + 0.1 * total, Math.min(b - 0.1 * total, delta));
      const sizes = [...node.sizes];
      sizes[i - 1] = a + d;
      sizes[i] = b - d;
      onResize(node.id, sizes);
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }
  return html`
    <div class="layout-split layout-${node.dir}" ref=${ref}>
      ${node.children.map((child, i) => [
        i > 0 && html`<div key=${`d${child.id}`} class="layout-divider" onPointerDown=${(e) => startResize(e, i)}></div>`,
        html`<div key=${child.id} class="layout-child" style=${{ flex: `${node.sizes[i]} 1 0` }}>
          <${LayoutView} node=${child} renderPane=${renderPane} onResize=${onResize} />
        </div>`,
      ])}
    </div>`;
}

function App() {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  // Databases loaded in the server; null: a server without Load database (one model, plain URLs)
  const [databases, setDatabases] = useState(undefined);
  const [loadOpen, setLoadOpen] = useState(false);
  const [layout, setLayout] = useState(null);
  const [focus, setFocus] = useState(null);
  const [titles, setTitles] = useState({});
  const [dragging, setDragging] = useState(null);
  const [drop, setDrop] = useState(null);
  const [sideOpen, setSideOpen] = useState(() => loadPreference("sideOpen", "1") === "1");
  const [sideWidth, setSideWidth] = useState(() => Number(loadPreference("sideWidth", "0")) || Math.round(window.innerWidth * 0.42));
  useEffect(() => savePreference("sideOpen", sideOpen ? "1" : "0"), [sideOpen]);
  useEffect(() => savePreference("sideWidth", String(sideWidth)), [sideWidth]);
  const states = useRef({});  // tab id → its saved view (what a remounted tab starts from)
  const starts = useRef({});  // tab id → how a tab without a saved view starts
  const current = useRef({});
  current.current = { layout, focus };
  const persist = () => current.current.layout && saveWorkspace({ layout: current.current.layout, focus: current.current.focus, tabs: states.current });
  useEffect(persist, [layout, focus]);

  useEffect(() => {
    serverRequest("/api/databases").catch(() => null).then((list) => {
      if (list?.length && !DATABASE) { openDatabase(list[0].key); return; }  // "/" opens the first one loaded
      setDatabases(list);
      if (!list || list.some((d) => d.key === DATABASE)) showDatabase();
    });
  }, []);
  const showDatabase = () => {
    request("/api/info").then((i) => {
      const saved = loadWorkspace();
      if (saved?.layout && panesOf(saved.layout).some((p) => p.tabs.length)) {
        states.current = saved.tabs || {};
        setTitles(Object.fromEntries(Object.entries(states.current).map(([id, s]) => [id, s?.root ? { name: s.root.name, location: s.root.location } : null])));
        setLayout(saved.layout);
        setFocus(panesOf(saved.layout).some((p) => p.id === saved.focus) ? saved.focus : panesOf(saved.layout)[0].id);
      } else {
        // First start, or a view saved before tabs existed
        const id = newId("t"), old = loadSaved();
        starts.current[id] = old ? { state: old } : { defaultRoot: true };
        const pane = { type: "pane", id: newId("p"), tabs: [id], active: id };
        setLayout(pane);
        setFocus(pane.id);
      }
      setInfo(i);
    }).catch((e) => setError(e.message));
  };

  const initialOf = (id) => (states.current[id] ? { state: states.current[id] } : starts.current[id]);
  const activeTabOf = (paneId) => (layout ? panesOf(layout).find((p) => p.id === paneId)?.active : null);
  const onSave = (id, state) => { states.current[id] = state; persist(); };
  const onTitle = (id, root) => setTitles((t) => ({ ...t, [id]: root ? { name: root.name, location: root.location } : null }));
  function openTab(paneId, start, zone = "center") {
    const id = newId("t");
    starts.current[id] = start;
    setLayout((l) => {
      const pane = panesOf(l).find((p) => p.id === paneId);
      // a new tab goes right after the pane's active one
      const after = pane.tabs[pane.tabs.indexOf(pane.active) + 1] ?? null;
      const next = insertTab(l, paneId, id, zone, after);
      setFocus(paneOfTab(next, id).id);
      return next;
    });
  }
  const actions = {
    focus: (paneId) => paneId !== focus && setFocus(paneId),
    select: (paneId, id) => setLayout((l) => mapPanes(l, (p) => (p.id === paneId ? { ...p, active: id } : p))),
    add: (paneId) => openTab(paneId, { inherit: states.current[activeTabOf(paneId)] }),
    // Split = a copy of the active tab in a new pane (same functional unit, to compare settings)
    split: (paneId, zone) => {
      const source = activeTabOf(paneId);
      openTab(paneId, states.current[source] ? { state: structuredClone(states.current[source]) } : { inherit: null }, zone);
    },
    close: (id) => {
      delete states.current[id];
      delete starts.current[id];
      setLayout((l) => {
        const next = removeFromPane(l, paneOfTab(l, id).id, id);
        if (next) {
          setFocus((f) => (panesOf(next).some((p) => p.id === f) ? f : panesOf(next)[0].id));
          return next;
        }
        // Last tab closed: start again from an empty tab
        const fresh = newId("t");
        starts.current[fresh] = { inherit: null };
        const pane = { type: "pane", id: newId("p"), tabs: [fresh], active: fresh };
        setFocus(pane.id);
        return pane;
      });
    },
    drag: (id) => { setDragging(id); if (!id) setDrop(null); },
    move: (id, paneId, zone, before) => {
      setDragging(null);
      setDrop(null);
      setLayout((l) => {
        const source = paneOfTab(l, id);
        if (source.id === paneId && zone !== "center" && source.tabs.length === 1) return l;  // nothing to split off
        let next = insertTab(l, paneId, id, zone, before);
        if (zone !== "center" || source.id !== paneId) next = removeFromPane(next, source.id, id);
        setFocus(paneOfTab(next, id).id);
        return next;
      });
    },
  };

  const topBar = (sidePanel) => html`
    <header class="top-bar">
      <h1>LCA Process Explorer</h1>
      <${DatabaseSelect} databases=${databases} />
      ${databases && html`<button onClick=${() => setLoadOpen(true)}
        title="Open another database: a model already built, or a database of your Brightway projects">Load database</button>`}
      <span class="spacer"></span>
      ${sidePanel}
    </header>
    ${loadOpen && html`<${LoadDatabase} onClose=${() => setLoadOpen(false)} onDatabases=${setDatabases} />`}`;
  if (databases && !databases.some((d) => d.key === DATABASE)) {
    // Nothing loaded yet, or the database of this address is not loaded any more
    return html`
      <div class="app">
        ${topBar(null)}
        <div class="welcome"><${LoadDatabase} missing=${DATABASE && decodeURIComponent(DATABASE)} /></div>
      </div>`;
  }
  if (!info || !layout) return html`<div class="empty">${error || "Loading model…"}</div>`;
  const allTabs = panesOf(layout).flatMap((p) => p.tabs);
  const focusedTab = activeTabOf(focus);
  function startSideResize(e) {
    e.preventDefault();
    const start = e.clientX, width = sideWidth;
    const move = (ev) => setSideWidth(Math.max(280, Math.min(window.innerWidth - 320, width + start - ev.clientX)));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }
  const renderPane = (pane) => html`
    <${PaneView} key=${pane.id} pane=${pane} focused=${pane.id === focus} multiple=${allTabs.length > 1 && panesOf(layout).length > 1}
      titles=${titles} dragging=${dragging} drop=${drop} setDrop=${setDrop} actions=${actions}>
      ${pane.tabs.map((id) => html`
        <${FUView} key=${id} id=${id} info=${info} initial=${initialOf(id)} visible=${id === pane.active}
          onSave=${onSave} onTitle=${onTitle} onCollapseSide=${() => setSideOpen(false)}
          onOpenTab=${(from, root) => openTab(paneOfTab(layout, from).id, { inherit: states.current[from], root })} />`)}
    <//>`;
  return html`
    <div class="app">
      ${topBar(html`<button class=${sideOpen ? "primary" : ""} onClick=${() => setSideOpen(!sideOpen)}
          title="Show or hide the side panel (process and flow contribution, grouping, whole supply chain)">Side panel</button>`)}
      <div class="body">
        <div class="workspace"><${LayoutView} node=${layout} renderPane=${renderPane}
          onResize=${(id, sizes) => setLayout((l) => resizeSplit(l, id, sizes))} /></div>
        ${sideOpen
          ? html`<div class="side-resizer" onPointerDown=${startSideResize} title="Drag to resize"></div>`
          : html`<button class="side-reopen" onClick=${() => setSideOpen(true)} title="Show the side panel" aria-label="Show the side panel">«</button>`}
        <aside class="sidebar" style=${{ width: `${sideWidth}px`, display: sideOpen ? "" : "none" }}>
          ${allTabs.map((id) => html`<div key=${id} id=${`side-${id}`} class="side-slot" style=${{ display: id === focusedTab ? "" : "none" }}></div>`)}
        </aside>
      </div>
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
