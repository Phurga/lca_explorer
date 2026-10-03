import { h, render } from "preact";
import { useState, useEffect, useMemo, useRef } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);
const STORAGE_KEY = "lca_viewer.view";

// ---------------------------------------------------------------- helpers
async function request(url, body) {
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

function shortLabel(row, byId) {
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

function loadSaved() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch { return null; }
}
function loadPreference(name, fallback) {
  try { return localStorage.getItem(`lca_viewer.${name}`) || fallback; } catch { return fallback; }
}
function savePreference(name, value) {
  try { localStorage.setItem(`lca_viewer.${name}`, value); } catch { /* storage unavailable */ }
}
function save(state) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* storage unavailable */ }
}

function describePredicate(p) {
  if (!p) return "";
  if (p.all) return p.all.map(describePredicate).join(" and ");
  if (p.any) return p.any.map(describePredicate).join(" or ");
  if (p.not) return `not (${describePredicate(p.not)})`;
  if (p.products) return `${p.products.length} selected processes`;
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
function Chart({ option, height = 420 }) {
  const ref = useRef(null);
  const chart = useRef(null);
  useEffect(() => {
    chart.current = echarts.init(ref.current);
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
function Tree({ rows, rootImpact, selection, onToggle, onSelect, onUngroup, ungroupable, onUnpin, onMoveToOthers, onHover, onInfo, onUnmerge, view }) {
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  if (!rows.length) return html`<div class="empty">Choose a functional unit to start.</div>`;
  return html`
    <div class="tree">
      <table>
        <colgroup><col /><col style="width:130px" /><col style="width:90px" /><col style="width:150px" /></colgroup>
        <thead><tr><th>Process</th><th class="num">Amount</th><th class="num">Impact</th><th>Share</th></tr></thead>
        <tbody>
          ${rows.map((r) => {
            const share = rootImpact ? r.impacts[0] / rootImpact : 0;
            const amount = r.kind === "merged" || r.kind === "group" ? (r.unit ? `${fmt(r.amount)} ${unit(r.unit)}` : "")
              : r.kind !== "process" ? ""
              : r.waste ? `${fmt(-r.amount)} ${unit(r.unit)} treated` : `${fmt(r.amount)} ${unit(r.unit)}`;
            const [product, name, location] = r.kind === "process" ? r.label.split(" | ") : [r.label];
            return html`
              <tr key=${r.id} class="${r.kind} ${selection.has(r.id) ? "selected" : ""}"
                  onClick=${(e) => onSelect(r, e.ctrlKey || e.metaKey || e.shiftKey)}
                  onMouseEnter=${() => onHover(r)} onMouseLeave=${() => onHover(null)}
                  onContextMenu=${(e) => onInfo(r, e)}>
                <td>
                  <div class="name" style=${{ paddingLeft: `${r.depth * 16}px` }}>
                    ${r.expandable
                      ? html`<button class="toggle" aria-label=${r.expanded ? "Collapse" : "Expand"}
                               onClick=${(e) => { e.stopPropagation(); onToggle(r); }}>${r.expanded ? "▾" : "▸"}</button>`
                      : html`<span class="toggle"></span>`}
                    ${movable(r)
                      ? html`<button class="to-others" title="Move to others (out of the tree and the chart)" aria-label="Move to others"
                               onClick=${(e) => { e.stopPropagation(); onMoveToOthers([r.id]); }}>×</button>`
                      : html`<span class="to-others"></span>`}
                    <span class="label" title=${r.label}>
                      ${(() => { const n = rowNames(r, byId, view); return html`${n.main}${n.qual && html` <span class="loc">${n.qual}</span>`}`; })()}
                    </span>
                    ${r.kind === "group" && ungroupable(r) && html`
                      <button class="chip" onClick=${(e) => { e.stopPropagation(); onUngroup(r); }}>ungroup</button>`}
                    ${r.kind === "merged" && html`
                      <button class="chip merge" title="Split back into the original processes"
                        onClick=${(e) => { e.stopPropagation(); onUnmerge(r); }}>merged ×</button>`}
                    ${r.pinned && html`
                      <button class="chip pin" title="Shown despite the cut-off — click to hide again"
                        onClick=${(e) => { e.stopPropagation(); onUnpin(r); }}>pinned ×</button>`}
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

// Tidy tree: leaves take consecutive slots, a parent is centred on its children
function layoutTree(rows, orientation) {
  const children = new Map();
  for (const r of rows) {
    if (r.parent == null) continue;
    if (!children.has(r.parent)) children.set(r.parent, []);
    children.get(r.parent).push(r);
  }
  const gap = GAP[orientation];
  const depthStep = (orientation === "horizontal" ? BOX.w : BOX.h) + gap.depth;
  const slotStep = (orientation === "horizontal" ? BOX.h : BOX.w) + gap.slot;
  const nodes = [];
  const edges = [];
  let nextSlot = 0;
  function place(row, depth) {
    const kids = children.get(row.id) || [];
    const slot = kids.length
      ? (() => { const s = kids.map((k) => place(k, depth + 1)); return (s[0] + s[s.length - 1]) / 2; })()
      : nextSlot++;
    const [x, y] = orientation === "horizontal" ? [depth * depthStep, slot * slotStep] : [slot * slotStep, depth * depthStep];
    nodes.push({ row, x, y });
    return slot;
  }
  if (rows.length) place(rows[0], 0);
  const byId = new Map(nodes.map((n) => [n.row.id, n]));
  for (const n of nodes) {
    const parent = byId.get(n.row.parent);
    if (parent) edges.push({ from: parent, to: n });
  }
  const width = Math.max(...nodes.map((n) => n.x)) + BOX.w;
  const height = Math.max(...nodes.map((n) => n.y)) + BOX.h;
  return { nodes, edges, width, height };
}

function edgePath(from, to, orientation) {
  if (orientation === "horizontal") {
    const x1 = from.x + BOX.w, y1 = from.y + BOX.h / 2, x2 = to.x, y2 = to.y + BOX.h / 2, xm = (x1 + x2) / 2;
    return `M${x1},${y1} C${xm},${y1} ${xm},${y2} ${x2},${y2}`;
  }
  const x1 = from.x + BOX.w / 2, y1 = from.y + BOX.h, x2 = to.x + BOX.w / 2, y2 = to.y, ym = (y1 + y2) / 2;
  return `M${x1},${y1} C${x1},${ym} ${x2},${ym} ${x2},${y2}`;
}

function TreeGraph({ rows, rootImpact, selection, onToggle, onSelect, onUngroup, ungroupable, onUnpin, onMoveToOthers, onHover, onInfo, onUnmerge, view, orientation, setOrientation }) {
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const container = useRef(null);
  const drag = useRef(null);
  const [transform, setTransform] = useState({ k: 1, x: 16, y: 16 });
  const graph = useMemo(() => layoutTree(rows, orientation), [rows, orientation]);
  const rootId = rows[0]?.id;

  // Whole tree in view; with ``readable``, never zoom out below 60 % and centre on the root instead
  function fit(readable = false) {
    const el = container.current;
    if (!el || !graph.nodes.length) return;
    const margin = 24;
    const whole = Math.min(1, (el.clientWidth - 2 * margin) / graph.width, (el.clientHeight - 2 * margin) / graph.height);
    const k = readable ? Math.max(whole, 0.6) : Math.max(whole, 0.1);
    const root = graph.nodes.find((n) => n.row.id === rootId);
    setTransform(orientation === "horizontal"
      ? { k, x: margin, y: el.clientHeight / 2 - (root.y + BOX.h / 2) * k }
      : { k, x: el.clientWidth / 2 - (root.x + BOX.w / 2) * k, y: margin });
  }
  // Re-fit when the functional unit or the orientation changes, not on every expand
  useEffect(() => fit(true), [rootId, orientation]);

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
  const share = (r) => (rootImpact ? r.impacts[0] / rootImpact : 0);
  return html`
    <div class="graph" ref=${container} onWheel=${onWheel} onPointerDown=${onPointerDown}
         onPointerMove=${onPointerMove} onPointerUp=${onPointerUp}>
      <div class="graph-controls">
        <button class=${orientation === "horizontal" ? "primary" : ""} onClick=${() => setOrientation("horizontal")}
          title="Root on the left">→</button>
        <button class=${orientation === "vertical" ? "primary" : ""} onClick=${() => setOrientation("vertical")}
          title="Root on top">↓</button>
        <button onClick=${() => fit(false)}>Fit</button>
        <button onClick=${() => setTransform((t) => ({ ...t, k: 1 }))}>100 %</button>
      </div>
      <svg width="100%" height="100%">
        <g transform=${`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
          ${graph.edges.map(({ from, to }) => {
            const s = share(to.row);
            return html`<path key=${`e-${to.row.id}`} class="edge ${s < 0 ? "neg" : ""}" d=${edgePath(from, to, orientation)}
              stroke-width=${1 + 14 * Math.min(1, Math.abs(s))} />`;
          })}
          ${graph.nodes.map(({ row: r, x, y }) => {
            const s = share(r);
            const [product, name, location] = r.kind === "process" ? r.label.split(" | ") : [r.label];
            const names = rowNames(r, byId, view);
            const title = names.main;
            const amountText = r.kind === "process" ? (r.waste ? `${fmt(-r.amount)} ${unit(r.unit)} treated` : `${fmt(r.amount)} ${unit(r.unit)}`)
              : (r.kind === "merged" || r.kind === "group") && r.unit ? `${fmt(r.amount)} ${unit(r.unit)}` : "";
            const qualifier = r.kind === "process" && view.naming === "full" ? location : names.qual;
            const subtitle = [qualifier, r.kind === "merged" && "merged", r.kind === "group" && !r.by_product && "group",
              r.kind === "direct" && "of the parent process", amountText].filter(Boolean).join(" · ");
            const toggleAt = orientation === "horizontal" ? [x + BOX.w, y + BOX.h / 2] : [x + BOX.w / 2, y + BOX.h];
            const barWidth = BOX.w - 20 - 52;
            return html`
              <g key=${r.id} class="node ${r.kind} ${selection.has(r.id) ? "selected" : ""}"
                 onClick=${(e) => onSelect(r, e.ctrlKey || e.metaKey || e.shiftKey)}
                 onDblClick=${() => r.expandable && onToggle(r)}
                 onMouseEnter=${() => onHover(r)} onMouseLeave=${() => onHover(null)}
                 onContextMenu=${(e) => onInfo(r, e)}>
                <title>${r.kind === "process" ? `${product} | ${name} | ${location}` : r.label}\n${fmt(r.impacts[0])} (${pct(s)})</title>
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
                ${r.kind === "merged" && html`
                  <text x=${x + BOX.w - 10} y=${y + 17} class="unmerge" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onUnmerge(r); }}>unmerge</text>`}
                ${r.pinned && html`
                  <text x=${x + BOX.w - 10} y=${y + 17} class="unpin" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onUnpin(r); }}>pinned ×</text>`}
                ${movable(r) && html`
                  <g class="to-others" onClick=${(e) => { e.stopPropagation(); onMoveToOthers([r.id]); }}>
                    <title>Move to others (out of the tree and the chart)</title>
                    <circle cx=${x + BOX.w} cy=${y} r="8" />
                    <text x=${x + BOX.w} y=${y + 4} text-anchor="middle">×</text>
                  </g>`}
                ${r.expandable && html`
                  <g class="toggle" onClick=${(e) => { e.stopPropagation(); onToggle(r); }}>
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

// ---------------------------------------------------------------- "others" details
// Shows what an "others" row contains (below the cut-off, or moved there by the user);
// ticking a process brings it back into the tree and the chart.
function OthersDetails({ rest, rows, rootImpact, cutoff, cutoffBasis, pinned, setPinned, others, setOthers, onClose, sticky, onMouseEnter, onMouseLeave }) {
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const parent = byId.get(rest.parent);
  const shownPins = rows.filter((r) => r.parent === rest.parent && r.pinned);
  const items = [...shownPins, ...rest.members].sort((a, b) => Math.abs(b.impacts[0]) - Math.abs(a.impacts[0]));
  const nMoved = rest.members.filter((m) => m.moved).length;
  const nCut = rest.members.length - nMoved;
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
  const reasons = [nCut && `${nCut} in the bottom ${cutoff} % of ${cutoffBasis === "total" ? "the total" : "their parent"}`, nMoved && `${nMoved} moved here by you`].filter(Boolean).join(", ");
  return html`
    <div class="details ${sticky ? "sticky" : "preview"}" onMouseEnter=${onMouseEnter} onMouseLeave=${onMouseLeave}>
      <div class="details-head">
        <span class="details-title"><b>Others</b>${parent ? ` under ${shortLabel(parent, byId)}` : ""}${`: ${reasons} · ${fmt(rest.impacts[0])} (${pct(share(rest))})`}</span>
        <span class="details-actions">
          <button onClick=${() => setShown(rest.members.map((m) => m.id), true)}>Show all</button>
          <button disabled=${!shownPins.length} onClick=${() => setShown(shownPins.map((m) => m.id), false)}>Hide pinned</button>
          ${sticky && html`<button onClick=${onClose} aria-label="Close">×</button>`}
        </span>
      </div>
      <p class="note">Tick a process to show it in the tree and the contribution chart; untick to put it back in others.${sticky ? "" : " Click the others row to keep this panel open."}</p>
      <div class="details-list">
        <table>
          <tbody>
            ${items.map((r) => {
              const [product, name, location] = r.kind === "process" ? r.label.split(" | ") : [r.label];
              const amount = r.kind !== "process" ? "" : r.waste ? `${fmt(-r.amount)} ${unit(r.unit)} treated` : `${fmt(r.amount)} ${unit(r.unit)}`;
              const shown = pinned.has(r.id) && !others.has(r.id);
              return html`
                <tr key=${r.id}>
                  <td><input type="checkbox" checked=${shown} aria-label="Show in tree and chart"
                        onChange=${(e) => setShown([r.id], e.target.checked)} /></td>
                  <td class="name-cell" title=${r.label}>${r.kind === "process" ? html`${name} <span class="loc">${location} · ${product}</span>` : r.kind === "direct" ? "direct emissions" : r.label}</td>
                  <td class="reason">${r.moved ? "moved" : r.pinned ? "pinned" : "cut-off"}</td>
                  <td class="num">${amount}</td>
                  <td class="num">${fmt(r.impacts[0])}</td>
                  <td class="num">${pct(share(r))}</td>
                </tr>`;
            })}
          </tbody>
        </table>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- view bar (names and product grouping)
function ViewBar({ view, setView, example }) {
  const set = (patch) => setView({ ...view, ...patch });
  return html`
    <div class="view-bar">
      <div class="row">
        <button class=${view.groupProducts ? "primary" : ""} onClick=${() => set({ groupProducts: !view.groupProducts })}
          title="Tree: bundle siblings of the same product (other regions or routes). Chart: also merge them across the tree">Group products</button>
        <span class="segmented" role="group" aria-label="Row names">
          ${[["full", "Full names"], ["short", "Short names"], ["custom", "Custom name"]].map(([id, label]) => html`
            <button class=${view.naming === id ? "primary" : ""} onClick=${() => set({ naming: id })}>${label}</button>`)}
        </span>
      </div>
      ${view.naming === "custom" && html`<${RecipeEditor} recipe=${view.recipe} setRecipe=${(recipe) => set({ recipe })} example=${example} />`}
    </div>`;
}

// ---------------------------------------------------------------- chart panel
// ---------------------------------------------------------------- activity info pop-up (right click)
const DETAIL_SECTIONS = [
  ["comment", "Description"], ["product_information", "Product"],
  ["included_activities_start", "Starts with"], ["included_activities_end", "Ends with"],
];

function InfoPopup({ target, method, methodInfo, rootImpact, rowsById, onClose, onUseAsRoot, onMoveToOthers }) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const box = useRef(null);
  useEffect(() => {
    setInfo(null);
    request(`/api/activity/${target.index}?method=${method}`).then(setInfo).catch((e) => setError(e.message));
  }, [target.index, method]);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    const onDown = (e) => box.current && !box.current.contains(e.target) && onClose();
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onDown); };
  }, []);

  const width = Math.min(560, window.innerWidth - 24);
  const left = Math.max(12, Math.min(target.x + 8, window.innerWidth - width - 12));
  const top = Math.max(12, Math.min(target.y + 8, window.innerHeight - 320));
  const row = target.row;
  const path = [];
  for (let r = row; r; r = rowsById.get(r.parent)) if (r.meta && r.kind === "process") path.unshift(r.meta.name);
  const d = info?.details || {};
  const unitPer = info ? `${methodInfo.unit} / ${unit(info.unit)}` : "";
  const directTotal = info ? info.direct_flows.reduce((sum, f) => sum + f.impact, 0) : 0;
  return html`
    <div class="info-popup" ref=${box} style=${{ left: `${left}px`, top: `${top}px`, width: `${width}px`, maxHeight: `calc(100vh - ${top + 12}px)` }}
         role="dialog" aria-label="Activity information">
      <div class="info-head">
        <div>
          <div class="info-title">${target.direct ? "Direct emissions of " : ""}${info?.name ?? "…"} ${info && html`<span class="loc-chip">${info.location}</span>`}</div>
          ${info && html`<div class="muted">${info.reference_product} · ${unit(info.unit)} · ${info.activity_type}</div>`}
        </div>
        <button onClick=${onClose} aria-label="Close">×</button>
      </div>
      ${error && html`<div class="error">${error}</div>`}
      ${info && html`
        <div class="info-body">
          ${row && (row.kind === "process" || row.kind === "direct") && html`
            <section>
              <h4>In this tree</h4>
              <table class="kv">
                ${row.kind === "process" && html`<tr><th>Amount</th><td>${row.waste ? `${fmt(-row.amount)} ${unit(row.unit)} treated` : `${fmt(row.amount)} ${unit(row.unit)}`}</td></tr>`}
                <tr><th>Impact</th><td>${fmt(row.impacts[0])} ${methodInfo.unit} (${pct(rootImpact ? row.impacts[0] / rootImpact : 0)} of the total)</td></tr>
                <tr><th>Path</th><td>${path.join(" → ")}</td></tr>
              </table>
            </section>`}
          <section>
            <h4>Per ${unit(info.unit)} of ${info.reference_product}</h4>
            <table class="kv">
              <tr><th>Cumulative</th><td>${fmt(info.cumulative_per_unit)} ${unitPer}</td></tr>
              <tr><th>Direct</th><td>${fmt(info.direct_per_unit)} ${unitPer}${info.cumulative_per_unit ? ` (${pct(info.direct_per_unit / info.cumulative_per_unit)} of cumulative)` : ""}</td></tr>
            </table>
            ${info.direct_flows.length > 0 && html`
              <table class="flows">
                <thead><tr><th>Main direct emissions (${methodLabel(methodInfo)})</th><th class="num">${unitPer}</th><th class="num">share</th></tr></thead>
                <tbody>${info.direct_flows.map((f) => html`
                  <tr><td>${f.name} <span class="muted">${f.categories.join(" / ")}</span></td>
                      <td class="num">${fmt(f.impact)}</td><td class="num">${pct(directTotal ? f.impact / directTotal : 0)}</td></tr>`)}
                </tbody>
              </table>`}
          </section>
          ${DETAIL_SECTIONS.filter(([k]) => d[k]).map(([k, title]) => html`
            <section><h4>${title}</h4><p class="text">${String(d[k]).split("\n").filter((line) => line.trim() !== "None").join("\n").trim()}</p></section>`)}
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
          </section>
        </div>
        <div class="info-actions">
          <button onClick=${() => { onUseAsRoot(info); onClose(); }}>Use as functional unit</button>
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
const DEFAULT_RECIPE = "{product} ({location})";

function joinValues(values) {
  const unique = [...new Set(values.filter(Boolean))];
  return unique.length <= 3 ? unique.join(", ") : `${unique.slice(0, 3).join(", ")} +${unique.length - 3}`;
}
function applyRecipe(recipe, metas) {
  return recipe
    .replace(/\{(\w+)\}/g, (token, key) => (RECIPE_TOKENS[key] ? joinValues(metas.map((m) => m[RECIPE_TOKENS[key]])) : token))
    .replace(/\(\s*\)/g, "").replace(/\s+/g, " ").trim();
}

function chartItems(rows, byId, groupProducts) {
  const items = [];
  const merged = new Map();
  for (const r of frontier(rows)) {
    const parent = byId.get(r.parent);
    const parentMeta = parent?.meta;
    const metas = r.meta ? [r.meta] : r.metas ? r.metas : r.kind === "direct" && parent?.metas ? parent.metas : [];
    let key = null;
    if (groupProducts) {
      if (r.kind === "process") key = `p|${r.meta.reference_product}`;
      else if (r.kind === "direct" && r.meta) key = `d|${r.meta.reference_product}`;
      else if (r.kind === "rest") key = "o|";
      else if (r.kind === "group") key = `g|${r.label}`;
    }
    if (key && merged.has(key)) {
      const item = merged.get(key);
      item.rows.push(r);
      item.impacts = item.impacts.map((v, k) => v + r.impacts[k]);
      item.metas.push(...metas);
      if (parentMeta) item.parentMetas.push(parentMeta);
      continue;
    }
    const item = { kind: r.kind, rows: [r], impacts: [...r.impacts], metas, parentMetas: parentMeta ? [parentMeta] : parent?.metas || [] };
    if (key) merged.set(key, item);
    items.push(item);
  }
  return items.sort((a, b) => Math.abs(b.impacts[0]) - Math.abs(a.impacts[0]));
}

function nameItem(item, naming, recipe, byId) {
  const { kind, metas, parentMetas, rows } = item;
  const many = rows.length > 1;
  const pick = (field) => joinValues(metas.map((m) => m[field]));
  const byProduct = kind === "group" && rows.every((r) => r.by_product);
  if (kind === "group" && !byProduct) return { main: rows[0].label, qual: many ? `${rows.length} groups` : "" };
  if (kind === "rest") {
    if (many) return { main: "others", qual: `under ${rows.length} processes` };
    if (naming === "short") return { main: "others", qual: `under ${joinValues(parentMetas.map((m) => m.reference_product))}` };
    if (naming === "custom" && parentMetas.length) return { main: `others under ${applyRecipe(recipe, parentMetas)}`, qual: "" };
    return { main: shortLabel(rows[0], byId), qual: "" };
  }
  if (!metas.length) return { main: rows[0].label, qual: "" };
  const direct = kind === "direct";
  if (naming === "short" || (byProduct && naming === "full")) {
    return { main: pick("reference_product"), qual: [direct && "direct emissions", pick("location")].filter(Boolean).join(" · ") };
  }
  if (naming === "custom") return { main: `${applyRecipe(recipe, metas)}${direct ? " — direct" : ""}`, qual: "" };
  if (kind === "merged" && !many) return { main: rows[0].label, qual: "" };  // "steel (RER + RoW)"
  return { main: `${direct ? "direct: " : ""}${pick("name")} (${pick("location")})`, qual: "" };
}

// Name of a tree row (list and tree view), following the view's naming choice
function rowNames(r, byId, view) {
  if (r.kind === "process" && view.naming === "full") {
    const [product, name, location] = r.label.split(" | ");
    return { main: name, qual: `${location} · ${product}` };
  }
  if (r.kind === "direct") return { main: "direct emissions", qual: "" };
  if (r.kind === "rest") return { main: r.label, qual: "" };
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
let measureContext = null;
function textWidth(text) {
  measureContext = measureContext || document.createElement("canvas").getContext("2d");
  measureContext.font = `12px ${cssVar("--font")}`;
  return measureContext.measureText(text).width;
}

function RecipeEditor({ recipe, setRecipe, example }) {
  const input = useRef(null);
  function insert(token) {
    const el = input.current;
    const at = el ? el.selectionStart : recipe.length;
    const next = `${recipe.slice(0, at)}{${token}}${recipe.slice(at)}`;
    setRecipe(next);
    setTimeout(() => { el?.focus(); el?.setSelectionRange(at + token.length + 2, at + token.length + 2); });
  }
  return html`
    <div class="recipe">
      <div class="row">
        <input ref=${input} value=${recipe} onInput=${(e) => setRecipe(e.target.value)} aria-label="Row name recipe" />
        <button onClick=${() => setRecipe(DEFAULT_RECIPE)}>Default</button>
      </div>
      <div class="row tokens">Insert:
        ${Object.keys(RECIPE_TOKENS).map((t) => html`<button class="token" onClick=${() => insert(t)}>{${t}}</button>`)}
      </div>
      ${example && html`<p class="note">e.g. ${example}</p>`}
    </div>`;
}

// ---------------------------------------------------------------- chart panel
function ChartPanel({ data, info, method, chartMethods, setChartMethods, view }) {
  const { naming, recipe, groupProducts } = view;
  const [mode, setMode] = useState("single");
  const [family, setFamily] = useState(info.methods[method].name[0]);

  const byId = useMemo(() => new Map(data.rows.map((r) => [r.id, r])), [data]);
  const parts = useMemo(
    () => nameItems(chartItems(data.rows, byId, groupProducts), naming, recipe, byId),
    [data, byId, groupProducts, naming, recipe],
  );
  const unit = info.methods[method].unit;
  const total = parts.reduce((s, p) => s + p.impacts[0], 0);

  const singleOption = useMemo(() => {
    const o = baseOption();
    const items = [...parts].reverse();
    const groupColor = cssVar("--group");
    const shown = items.map((p) => [truncate(p.main, 52), p.qual ? truncate(p.qual, 48) : ""]);
    o.grid = { ...o.grid, containLabel: false, top: 36, bottom: 28,
      left: Math.min(460, Math.max(...shown.map(([m, q]) => textWidth(q ? `${m} ${q}` : m))) + 16) };
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
        itemStyle: { color: p.impacts[0] < 0 ? cssVar("--bar-negative") : p.kind === "group" ? groupColor : cssVar("--bar") },
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
        Expand, collapse or group rows to change the breakdown. ${parts.length} segments${groupProducts ? " (same products merged across the tree)" : ""}.
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
  { label: "Waste treatment", rule: { name: "waste treatment", predicate: { field: "activity_type", op: "eq", value: "treatment" } } },
  { label: "By location", rule: { by: "location" } },
  { label: "By activity type", rule: { by: "activity_type" } },
  { label: "By ISIC division", rule: { by: "isic_division" } },
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
  const move = (i, d) => {
    const next = [...rules];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setRules(next);
  };

  return html`
    <div class="panel">
      <h3>Active rules ${rules.length > 1 && html`<button class="link" onClick=${() => setRules([])}>remove all</button>`}</h3>
      <p class="note">Rules bundle sibling processes in the tree. Named groups are applied first, then group-by rules, each in list order; a process goes to the first group that claims it. Numbers never change, only how they are bundled.</p>
      ${rules.length === 0 ? html`<p class="note">No grouping. Select several sibling rows in the tree (Ctrl/⌘-click) and use "Group selected", or add a rule below.</p>` : html`
        <ul class="rules">
          ${rules.map((r, i) => html`
            <li>
              <span class="desc">
                ${r.by ? html`<b>group by ${r.by}</b>` : html`<b>${r.name}</b> <small>← ${describePredicate(r.predicate)}</small>`}
                <br /><small>${r.scope === "*" ? "everywhere in the tree" : `under ${r.scope}`}</small>
              </span>
              <button disabled=${i === 0} onClick=${() => move(i, -1)} aria-label="Move up">↑</button>
              <button disabled=${i === rules.length - 1} onClick=${() => move(i, 1)} aria-label="Move down">↓</button>
              <button onClick=${() => setRules(rules.filter((_, j) => j !== i))} aria-label="Remove rule">×</button>
            </li>`)}
        </ul>`}

      <h3>Quick groups</h3>
      <div class="row">${PRESETS.map((p) => html`<button onClick=${() => add(p.rule)}>${p.label}</button>`)}</div>

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
function GlobalPanel({ info, root, amount, method, rules }) {
  const [rule, setRule] = useState("first_match");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const groups = useMemo(() => Object.fromEntries(rules.filter((r) => r.predicate).map((r) => [r.name, r.predicate])), [rules]);
  const k = method;

  useEffect(() => {
    if (!root) return;
    setError(null);
    request("/api/global", { root: root.index, amount, groups, rule }).then(setResult).catch((e) => setError(e.message));
  }, [root, amount, groups, rule]);

  const unit = info.methods[k].unit;
  const entries = result ? Object.entries(result).map(([n, v]) => [n, v[k]]) : [];
  const total = entries.reduce((s, [, v]) => s + v, 0);
  const option = useMemo(() => {
    const o = baseOption();
    o.yAxis = { ...o.yAxis, type: "category", data: entries.map(([n]) => n), inverse: true };
    o.xAxis = { ...o.xAxis, type: "value", name: unit };
    o.tooltip.valueFormatter = (v) => `${fmt(v)} ${unit} (${pct(v / total)})`;
    o.series = [{ type: "bar", colorBy: "data",
      data: entries.map(([n, v]) => (n === "other" ? { value: v, itemStyle: { color: cssVar("--muted") } } : v)),
      label: { show: true, position: "right", formatter: (d) => pct(d.value / total), color: cssVar("--muted") } }];
    return o;
  }, [result, k]);

  return html`
    <div class="panel">
      <p class="note">
        Whole supply chain, using the named groups defined in the Grouping tab (${Object.keys(groups).length} groups).
        Groups nest (electricity uses transport, which uses electricity), so choose how impacts are attributed:
      </p>
      <div class="row">
        <label><input type="radio" checked=${rule === "first_match"} onChange=${() => setRule("first_match")} />
          <b>First match</b>: the first matching process on each supply path takes its whole cumulative impact</label>
      </div>
      <div class="row">
        <label><input type="radio" checked=${rule === "direct"} onChange=${() => setRule("direct")} />
          <b>Direct</b>: each process's own emissions go to its group</label>
      </div>
      ${error && html`<div class="error">${error}</div>`}
      ${result && html`
        <${Chart} option=${option} height=${Math.max(200, entries.length * 34 + 60)} />
        <table class="results">
          <thead><tr><th>Group</th><th class="num">${unit}</th><th class="num">Share</th></tr></thead>
          <tbody>${entries.map(([n, v]) => html`<tr><td>${n}</td><td class="num">${fmt(v)}</td><td class="num">${pct(v / total)}</td></tr>`)}</tbody>
        </table>`}
    </div>`;
}

// ---------------------------------------------------------------- app
function App() {
  const [info, setInfo] = useState(null);
  const [root, setRoot] = useState(null);
  const [amount, setAmount] = useState(1);
  const [method, setMethod] = useState(0);
  const [cutoff, setCutoff] = useState(1);
  const [cutoffBasis, setCutoffBasis] = useState("parent");
  const [undo, setUndo] = useState(null);
  const [hoverRest, setHoverRest] = useState(null);
  const [infoTarget, setInfoTarget] = useState(null);
  const hoverTimer = useRef(null);
  const [expanded, setExpanded] = useState(new Set());
  const [pinned, setPinned] = useState(new Set());
  const [others, setOthers] = useState(new Set());
  const [merges, setMerges] = useState([]);
  const [view, setView] = useState(() => ({
    naming: loadPreference("naming", "full"),
    recipe: loadPreference("recipe", DEFAULT_RECIPE),
    groupProducts: loadPreference("groupProducts", "0") === "1",
  }));
  useEffect(() => {
    savePreference("naming", view.naming);
    savePreference("recipe", view.recipe);
    savePreference("groupProducts", view.groupProducts ? "1" : "0");
  }, [view]);
  const [rules, setRules] = useState([]);
  const [chartMethods, setChartMethods] = useState([]);
  const [selection, setSelection] = useState(new Set());
  const [data, setData] = useState({ methods: [], rows: [] });
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("chart");
  const [threshold, setThreshold] = useState(5);
  const [groupName, setGroupName] = useState("");
  const [busy, setBusy] = useState(false);
  const [display, setDisplay] = useState(() => loadPreference("display", "list"));
  const [orientation, setOrientation] = useState(() => loadPreference("orientation", "horizontal"));
  useEffect(() => savePreference("display", display), [display]);
  useEffect(() => savePreference("orientation", orientation), [orientation]);

  useEffect(() => {
    request("/api/info").then((i) => {
      setInfo(i);
      const saved = loadSaved();
      if (saved && saved.root && saved.root.index < i.activities && saved.method < i.methods.length) {
        setRoot(saved.root); setAmount(saved.amount); setMethod(saved.method); setCutoff(saved.cutoff);
        setExpanded(new Set(saved.expanded)); setRules(saved.rules); setChartMethods(saved.chartMethods || []);
        setPinned(new Set(saved.pinned || [])); setOthers(new Set(saved.others || []));
        setCutoffBasis(saved.cutoffBasis || "parent");
        setMerges(saved.merges || []);
        return;
      }
      setMethod(i.default_method);
      const family = i.methods[i.default_method].name[0];
      setChartMethods(i.methods.map((m, k) => [m, k]).filter(([m]) => m.name[0] === family).map(([, k]) => k));
      if (i.default_root) selectRoot(i.default_root);
    }).catch((e) => setError(e.message));
  }, []);

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
  function resetView() {
    const custom = describeCustom(pinned, others);
    setUndo({ message: `View reset${custom ? ` (${custom})` : ""}.`, state: { expanded, pinned, others } });
    setExpanded(new Set([String(root.index)]));
    setPinned(new Set());
    setOthers(new Set());
    setSelection(new Set());
  }
  function applyUndo() {
    const state = undo.state;
    if (state.cutoff !== undefined) setCutoff(state.cutoff);
    if (state.cutoffBasis !== undefined) setCutoffBasis(state.cutoffBasis);
    if (state.expanded) setExpanded(state.expanded);
    setPinned(state.pinned);
    setOthers(state.others);
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
    save({ root, amount, method, cutoff, cutoffBasis, expanded: [...expanded], rules, chartMethods, pinned: [...pinned], others: [...others], merges });
    return () => { cancelled = true; };
  }, [root, amount, method, cutoff, cutoffBasis, expanded, rules, chartMethods, pinned, others, merges, view.groupProducts, info]);

  const rootImpact = data.rows[0]?.impacts[0] || 0;
  const rowsById = useMemo(() => new Map(data.rows.map((r) => [r.id, r])), [data]);
  const selectedRows = [...selection].map((id) => rowsById.get(id)).filter(Boolean);
  const siblings = selectedRows.length >= 2 && selectedRows.every((r) => r.parent === selectedRows[0].parent)
    && !selectedRows[0].parent.includes("/g:");
  const canGroup = siblings && selectedRows.every((r) => r.kind === "process");
  const canMerge = siblings && selectedRows.every((r) => r.kind === "process" || r.kind === "merged");
  function mergeSelected() {
    const scope = selectedRows[0].parent;
    const products = [...new Set(selectedRows.flatMap((r) => (r.kind === "merged" ? r.products : [productOf(r.id)])))];
    // Merging a merged row with more processes replaces its merge
    setMerges([...merges.filter((m) => m.scope !== scope || !m.products.some((p) => products.includes(p))), { scope, products }]);
    setSelection(new Set());
  }
  function unmerge(row) {
    setMerges(merges.filter((m) => !(m.scope === row.parent && m.products.length === row.products.length
      && m.products.every((p) => row.products.includes(p)))));
  }

  function toggle(row) {
    // Same semantics as View.expand / View.collapse ("!" marks a group collapsed on purpose)
    const next = new Set(expanded);
    if (row.expanded) {
      next.delete(row.id);
      if (row.kind === "group") next.add(`!${row.id}`);
    } else {
      next.delete(`!${row.id}`);
      next.add(row.id);
    }
    setExpanded(next);
  }
  function select(row, additive) {
    // Clicking the only selected row again unselects it (closes the others panel)
    if (!additive && selection.size === 1 && selection.has(row.id)) { setSelection(new Set()); return; }
    const next = new Set(additive ? selection : []);
    additive && selection.has(row.id) ? next.delete(row.id) : next.add(row.id);
    setSelection(next);
  }
  // Hovering an others row previews its panel; leaving it (and the panel) closes the preview
  function hover(row) {
    clearTimeout(hoverTimer.current);
    if (row && row.kind === "rest") hoverTimer.current = setTimeout(() => setHoverRest(row.id), 150);
    else hoverTimer.current = setTimeout(() => setHoverRest(null), 300);
  }
  const keepHover = () => clearTimeout(hoverTimer.current);
  function showInfo(row, e) {
    e.preventDefault();
    if (row.kind === "rest") { setSelection(new Set([row.id])); return; }
    if (!row.meta) return;
    setInfoTarget({ index: row.meta.index, row, direct: row.kind === "direct", x: e.clientX, y: e.clientY });
  }
  function groupSelected() {
    const parent = selectedRows[0].parent;
    const name = groupName.trim() || `group ${rules.filter((r) => r.predicate?.products).length + 1}`;
    setRules([...rules, { name, predicate: { products: selectedRows.map((r) => productOf(r.id)) }, scope: parent }]);
    setExpanded(new Set([...expanded, `${parent}/g:${name}`]));
    setSelection(new Set());
    setGroupName("");
  }
  const unpin = (row) => { const next = new Set(pinned); next.delete(row.id); setPinned(next); };
  function moveToOthers(ids) {
    setOthers(new Set([...others, ...ids]));
    const nextPinned = new Set(pinned);
    ids.forEach((id) => nextPinned.delete(id));
    setPinned(nextPinned);
    setSelection(new Set());
  }
  const movableSelection = selectedRows.filter(movable);
  const selectedRest = selectedRows.length === 1 && selectedRows[0].kind === "rest" ? selectedRows[0] : null;
  const hoveredRest = hoverRest && hoverRest !== selectedRest?.id ? rowsById.get(hoverRest) : null;
  const shownRest = hoveredRest || selectedRest;
  const manualRuleFor = (groupRow) => rules.findIndex((r) => r.predicate && r.scope === groupRow.parent && `${groupRow.parent}/g:${r.name}` === groupRow.id);
  function ungroup(groupRow) {
    const i = manualRuleFor(groupRow);
    if (i >= 0) setRules(rules.filter((_, j) => j !== i));
  }
  async function expandAbove() {
    setBusy(true);
    try {
      let next = new Set(expanded);
      for (let level = 0; level < 12; level++) {
        const d = await request("/api/rows", viewState(next));
        const total = d.rows[0].impacts[0];
        const candidates = d.rows.filter((r) => ["process", "group", "merged"].includes(r.kind) && !r.expanded && total
          && Math.abs(r.impacts[0] / total) >= threshold / 100);
        if (!candidates.length) break;
        next = new Set([...next, ...candidates.map((r) => r.id)]);
      }
      setExpanded(next);
    } catch (e) { setError(e.message); }
    setBusy(false);
  }
  function exportView() {
    download("lca-view.json", JSON.stringify({ root, amount, method: info.methods[method].name, cutoff, expanded: [...expanded], rules, cutoffBasis, pinned: [...pinned], others: [...others], merges }, null, 2), "application/json");
  }

  if (!info) return html`<div class="empty">${error || "Loading model…"}</div>`;
  const methodInfo = info.methods[method];
  return html`
    <div class="app">
      <header>
        <h1>LCA Process Explorer</h1>
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
      <main>
        <section>
          <div class="toolbar">
            <div class="segmented" role="group" aria-label="Display">
              <button class=${display === "list" ? "primary" : ""} onClick=${() => setDisplay("list")}>List</button>
              <button class=${display === "graph" ? "primary" : ""} onClick=${() => setDisplay("graph")}>Tree</button>
            </div>
            <button onClick=${() => setExpanded(new Set(root ? [String(root.index)] : []))} title="Collapse everything below the root">Collapse</button>
            <button onClick=${resetView} disabled=${!root} title="Collapse the tree and clear pinned rows and rows moved to others (groups are kept)">Reset view</button>
            <button onClick=${expandAbove} disabled=${busy || !root}>Expand above</button>
            <${NumberInput} class="cutoff" value=${threshold} min=${0.1} step="0.5" aria-label="Expansion threshold"
              onValue=${setThreshold} />%
            <span class="spacer"></span>
            <button onClick=${exportView} disabled=${!root} title="Download this view (tree, groups, pins) as JSON">Save</button>
          </div>
          <${ViewBar} view=${view} setView=${setView}
            example=${(() => { const r = data.rows.find((x) => x.kind === "process" && x.depth > 0); return r ? rowNames(r, rowsById, view).main : ""; })()} />
          ${selection.size > 0 && !selectedRest && html`
            <div class="selection-bar">
              <span>${selection.size} selected${selection.size === 1 ? " · Ctrl/⌘-click to add siblings" : ""}</span>
              <input placeholder="group name" value=${groupName} onInput=${(e) => setGroupName(e.target.value)} aria-label="New group name" />
              <button disabled=${!canGroup} onClick=${groupSelected}
                title="Bundle the selected sibling rows under one row; expanding it shows them">Group</button>
              <button disabled=${!canMerge} onClick=${mergeSelected}
                title="Combine the selected sibling processes into one process: inventories added, common inputs summed">Merge</button>
              <button disabled=${!movableSelection.length} onClick=${() => moveToOthers(movableSelection.map((r) => r.id))}
                title="Move the selected rows to their parent's others row">To others</button>
              <button onClick=${() => setSelection(new Set())} aria-label="Clear selection">×</button>
            </div>`}
          ${undo && html`
            <div class="notice" role="status">
              <span>${undo.message}</span>
              <button onClick=${applyUndo}>Undo</button>
              <button onClick=${() => setUndo(null)} aria-label="Dismiss">×</button>
            </div>`}
          ${error && html`<div class="error">${error}</div>`}
          ${display === "list"
            ? html`<${Tree} rows=${data.rows} rootImpact=${rootImpact} selection=${selection} onToggle=${toggle} onSelect=${select}
                onUngroup=${ungroup} ungroupable=${(r) => manualRuleFor(r) >= 0} onUnpin=${unpin} onMoveToOthers=${moveToOthers} onHover=${hover} onInfo=${showInfo} onUnmerge=${unmerge} view=${view} />`
            : html`<${TreeGraph} rows=${data.rows} rootImpact=${rootImpact} selection=${selection} onToggle=${toggle} onSelect=${select}
                onUngroup=${ungroup} ungroupable=${(r) => manualRuleFor(r) >= 0} onUnpin=${unpin} onMoveToOthers=${moveToOthers} onHover=${hover} onInfo=${showInfo} onUnmerge=${unmerge} view=${view}
                orientation=${orientation} setOrientation=${setOrientation} />`}
          ${shownRest && html`<${OthersDetails} rest=${shownRest} rows=${data.rows} rootImpact=${rootImpact}
              cutoff=${cutoff} cutoffBasis=${cutoffBasis} pinned=${pinned} setPinned=${setPinned} others=${others} setOthers=${setOthers}
              sticky=${shownRest === selectedRest} onMouseEnter=${keepHover} onMouseLeave=${() => hover(null)}
              onClose=${() => setSelection(new Set())} />`}
          ${infoTarget && html`<${InfoPopup} target=${infoTarget} method=${method} methodInfo=${methodInfo}
              rootImpact=${rootImpact} rowsById=${rowsById} onClose=${() => setInfoTarget(null)}
              onUseAsRoot=${selectRoot} onMoveToOthers=${moveToOthers} />`}
          <div class="toolbar"><span class="hint">
            ${root ? `Total: ${fmt(rootImpact)} ${methodInfo.unit} — ${methodLabel(methodInfo)}` : ""}
          </span></div>
        </section>
        <section class="side">
          <div class="tabs" role="tablist">
            ${[["chart", "Contribution chart"], ["groups", `Grouping${rules.length ? ` (${rules.length})` : ""}`], ["global", "Whole supply chain"]].map(([id, label]) => html`
              <button role="tab" class=${tab === id ? "active" : ""} onClick=${() => setTab(id)}>${label}</button>`)}
          </div>
          ${tab === "chart" && data.rows.length > 0 && html`<${ChartPanel} data=${data} info=${info} method=${method}
              chartMethods=${chartMethods} setChartMethods=${setChartMethods} view=${view} />`}
          ${tab === "groups" && html`<${GroupPanel} info=${info} rules=${rules} setRules=${setRules}
              selectedRow=${selectedRows.length === 1 ? selectedRows[0] : null} />`}
          ${tab === "global" && html`<${GlobalPanel} info=${info} root=${root} amount=${amount} method=${method} rules=${rules} />`}
        </section>
      </main>
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
