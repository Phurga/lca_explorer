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
  if (!response.ok) throw new Error(await response.text());
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
    return `other inputs of ${parent ? shortLabel(parent, byId) : ""}`;
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

// ---------------------------------------------------------------- functional unit search
function FunctionalUnit({ root, onSelect }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!query.trim()) { setResults([]); return; }
    const timer = setTimeout(() => request(`/api/search?q=${encodeURIComponent(query)}`).then(setResults), 250);
    return () => clearTimeout(timer);
  }, [query]);
  const current = root ? `${root.reference_product} | ${root.name} | ${root.location}` : "";
  return html`
    <div class="fu">
      <input placeholder=${current || "Search a process (name, product, location)…"}
             value=${query} onInput=${(e) => { setQuery(e.target.value); setOpen(true); }}
             onFocus=${() => setOpen(true)} onBlur=${() => setTimeout(() => setOpen(false), 150)}
             title=${current} aria-label="Functional unit" />
      ${open && results.length > 0 && html`
        <div class="search-results">
          ${results.map((r) => html`
            <div onMouseDown=${() => { onSelect(r); setQuery(""); setOpen(false); }}>
              ${r.reference_product} | ${r.name} <small>${r.location} · ${r.unit}</small>
            </div>`)}
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
function Tree({ rows, rootImpact, selection, onToggle, onSelect, onUngroup, ungroupable }) {
  if (!rows.length) return html`<div class="empty">Choose a functional unit to start.</div>`;
  return html`
    <div class="tree">
      <table>
        <colgroup><col /><col style="width:130px" /><col style="width:90px" /><col style="width:150px" /></colgroup>
        <thead><tr><th>Process</th><th class="num">Amount</th><th class="num">Impact</th><th>Share</th></tr></thead>
        <tbody>
          ${rows.map((r) => {
            const share = rootImpact ? r.impacts[0] / rootImpact : 0;
            const amount = r.kind !== "process" ? ""
              : r.waste ? `${fmt(-r.amount)} ${unit(r.unit)} treated` : `${fmt(r.amount)} ${unit(r.unit)}`;
            const [product, name, location] = r.kind === "process" ? r.label.split(" | ") : [r.label];
            return html`
              <tr key=${r.id} class="${r.kind} ${selection.has(r.id) ? "selected" : ""}"
                  onClick=${(e) => onSelect(r, e.ctrlKey || e.metaKey || e.shiftKey)}>
                <td>
                  <div class="name" style=${{ paddingLeft: `${r.depth * 16}px` }}>
                    ${r.expandable
                      ? html`<button class="toggle" aria-label=${r.expanded ? "Collapse" : "Expand"}
                               onClick=${(e) => { e.stopPropagation(); onToggle(r); }}>${r.expanded ? "▾" : "▸"}</button>`
                      : html`<span class="toggle"></span>`}
                    <span class="label" title=${r.label}>
                      ${r.kind === "process" ? html`${name} <span class="loc">${location}</span> <span class="loc">· ${product}</span>` : r.label}
                    </span>
                    ${r.kind === "group" && ungroupable(r) && html`
                      <button class="chip" onClick=${(e) => { e.stopPropagation(); onUngroup(r); }}>ungroup</button>`}
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

// ---------------------------------------------------------------- chart panel
function ChartPanel({ data, info, method, chartMethods, setChartMethods }) {
  const [mode, setMode] = useState("single");
  const [family, setFamily] = useState(info.methods[method].name[0]);
  const byId = useMemo(() => new Map(data.rows.map((r) => [r.id, r])), [data]);
  const parts = useMemo(() => {
    const seen = new Map();
    return frontier(data.rows)
      .map((r) => {
        let label = truncate(shortLabel(r, byId), 60);
        const n = (seen.get(label) || 0) + 1;
        seen.set(label, n);
        if (n > 1) label = `${label} #${n}`;
        return { ...r, chartLabel: label };
      })
      .sort((a, b) => Math.abs(b.impacts[0]) - Math.abs(a.impacts[0]));
  }, [data, byId]);
  const unit = info.methods[method].unit;

  const singleOption = useMemo(() => {
    const o = baseOption();
    const items = [...parts].reverse();
    o.title = { text: `${methodLabel(info.methods[method])} [${unit}]`, textStyle: { fontSize: 12, color: cssVar("--muted") } };
    o.yAxis = { ...o.yAxis, type: "category", data: items.map((p) => p.chartLabel) };
    o.xAxis = { ...o.xAxis, type: "value", name: unit };
    o.tooltip.valueFormatter = (v) => `${fmt(v)} ${unit}`;
    o.series = [{
      type: "bar", data: items.map((p) => ({
        value: p.impacts[0],
        itemStyle: { color: p.impacts[0] < 0 ? cssVar("--bar-negative") : p.kind === "group" ? cssVar("--group") : cssVar("--bar") },
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
    const segments = top.map((p) => ({ name: p.chartLabel, impacts: p.impacts }));
    if (rest.length) {
      segments.push({ name: `${rest.length} others`, impacts: data.methods.map((_, k) => rest.reduce((s, p) => s + p.impacts[k], 0)) });
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
    const header = ["row", "kind", "amount", "unit", ...data.methods.map((m) => `"${methodLabel(info.methods[m])} [${info.methods[m].unit}]"`)];
    const lines = parts.map((p) => [`"${p.chartLabel.replace(/"/g, '""')}"`, p.kind, p.amount, p.unit, ...p.impacts].join(","));
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
const OPS = [["contains", "contains"], ["eq", "is"], ["ne", "is not"], ["in", "is one of"], ["startswith", "starts with"], ["regex", "matches regex"]];
const SUGGEST = new Set(["location", "activity_type", "isic_division", "cpc_section", "unit", "database"]);

function useValues(field) {
  const [values, setValues] = useState([]);
  useEffect(() => {
    if (!SUGGEST.has(field)) { setValues([]); return; }
    request(`/api/values/${field}`).then(setValues).catch(() => setValues([]));
  }, [field]);
  return values;
}

function Condition({ condition, fields, onChange, onRemove }) {
  const values = useValues(condition.field);
  const listId = `values-${condition.field}`;
  return html`
    <div class="row condition">
      <select value=${condition.field} onChange=${(e) => onChange({ ...condition, field: e.target.value })} aria-label="Field">
        ${fields.map((f) => html`<option value=${f}>${f}</option>`)}
      </select>
      <select value=${condition.op} onChange=${(e) => onChange({ ...condition, op: e.target.value })} aria-label="Operator">
        ${OPS.map(([op, label]) => html`<option value=${op}>${label}</option>`)}
      </select>
      <input value=${condition.value} list=${listId} placeholder=${condition.op === "in" ? "a, b, c" : "value"}
             onInput=${(e) => onChange({ ...condition, value: e.target.value })} aria-label="Value" />
      <datalist id=${listId}>${values.slice(0, 500).map((v) => html`<option value=${v} />`)}</datalist>
      <button onClick=${onRemove} aria-label="Remove condition">×</button>
    </div>`;
}

function toPredicate(conditions, combinator) {
  const parts = conditions.filter((c) => c.value.trim()).map((c) => ({
    field: c.field, op: c.op,
    value: c.op === "in" ? c.value.split(",").map((v) => v.trim()).filter(Boolean) : c.value.trim(),
  }));
  if (parts.length === 1) return parts[0];
  return { [combinator]: parts };
}

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
  const [combinator, setCombinator] = useState("all");
  const [conditions, setConditions] = useState([{ field: "name", op: "contains", value: "" }]);
  const [scope, setScope] = useState("*");

  const scopeTarget = selectedRow && selectedRow.kind === "process" ? selectedRow : null;
  const add = (rule) => setRules([...rules, { scope: "*", ...rule }]);
  function addCustom() {
    const ruleScope = scope === "selected" && scopeTarget ? scopeTarget.id : "*";
    if (kind === "named") {
      const predicate = toPredicate(conditions, combinator);
      if (!predicate || (predicate[combinator] && !predicate[combinator].length)) return;
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
      <h3>Active rules</h3>
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
        <div class="row"><input placeholder="Group name" value=${name} onInput=${(e) => setName(e.target.value)} aria-label="Group name" />
          <select value=${combinator} onChange=${(e) => setCombinator(e.target.value)} aria-label="Combine conditions">
            <option value="all">all conditions</option><option value="any">any condition</option>
          </select></div>
        ${conditions.map((c, i) => html`
          <${Condition} condition=${c} fields=${info.query_fields}
            onChange=${(next) => setConditions(conditions.map((x, j) => (j === i ? next : x)))}
            onRemove=${() => setConditions(conditions.filter((_, j) => j !== i))} />`)}
        <div class="row"><button onClick=${() => setConditions([...conditions, { field: "location", op: "eq", value: "" }])}>+ condition</button></div>
      ` : html`
        <div class="row"><select value=${by} onChange=${(e) => setBy(e.target.value)} aria-label="Group by field">
          ${info.group_by_fields.map((f) => html`<option value=${f}>${f}</option>`)}
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
  const [expanded, setExpanded] = useState(new Set());
  const [rules, setRules] = useState([]);
  const [chartMethods, setChartMethods] = useState([]);
  const [selection, setSelection] = useState(new Set());
  const [data, setData] = useState({ methods: [], rows: [] });
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("chart");
  const [threshold, setThreshold] = useState(5);
  const [groupName, setGroupName] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    request("/api/info").then((i) => {
      setInfo(i);
      const saved = loadSaved();
      if (saved && saved.root && saved.root.index < i.activities && saved.method < i.methods.length) {
        setRoot(saved.root); setAmount(saved.amount); setMethod(saved.method); setCutoff(saved.cutoff);
        setExpanded(new Set(saved.expanded)); setRules(saved.rules); setChartMethods(saved.chartMethods || []);
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
    setRules((current) => current.filter((rule) => rule.scope === "*"));
    setSelection(new Set());
  }

  const viewState = (exp = expanded) => ({
    root: [root.index], amount, expanded: [...exp], rules, cutoff: cutoff / 100, method, methods: chartMethods,
  });

  useEffect(() => {
    if (!root || !info) return;
    let cancelled = false;
    request("/api/rows", viewState())
      .then((d) => { if (!cancelled) { setData(d); setError(null); } })
      .catch((e) => !cancelled && setError(e.message));
    save({ root, amount, method, cutoff, expanded: [...expanded], rules, chartMethods });
    return () => { cancelled = true; };
  }, [root, amount, method, cutoff, expanded, rules, chartMethods, info]);

  const rootImpact = data.rows[0]?.impacts[0] || 0;
  const rowsById = useMemo(() => new Map(data.rows.map((r) => [r.id, r])), [data]);
  const selectedRows = [...selection].map((id) => rowsById.get(id)).filter(Boolean);
  const canGroup = selectedRows.length >= 2 && selectedRows.every((r) => r.kind === "process" && r.parent === selectedRows[0].parent)
    && !selectedRows[0].parent.includes("/g:");

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
    const next = new Set(additive ? selection : []);
    additive && selection.has(row.id) ? next.delete(row.id) : next.add(row.id);
    setSelection(next);
  }
  function groupSelected() {
    const parent = selectedRows[0].parent;
    const name = groupName.trim() || `group ${rules.filter((r) => r.predicate?.products).length + 1}`;
    setRules([...rules, { name, predicate: { products: selectedRows.map((r) => productOf(r.id)) }, scope: parent }]);
    setExpanded(new Set([...expanded, `${parent}/g:${name}`]));
    setSelection(new Set());
    setGroupName("");
  }
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
        const candidates = d.rows.filter((r) => r.kind === "process" && !r.expanded && total
          && Math.abs(r.impacts[0] / total) >= threshold / 100);
        if (!candidates.length) break;
        next = new Set([...next, ...candidates.map((r) => r.id)]);
      }
      setExpanded(next);
    } catch (e) { setError(e.message); }
    setBusy(false);
  }
  function exportView() {
    download("lca-view.json", JSON.stringify({ root, amount, method: info.methods[method].name, cutoff, expanded: [...expanded], rules }, null, 2), "application/json");
  }

  if (!info) return html`<div class="empty">${error || "Loading model…"}</div>`;
  const methodInfo = info.methods[method];
  return html`
    <div class="app">
      <header>
        <h1>LCA Process Explorer</h1>
        <${FunctionalUnit} root=${root} onSelect=${selectRoot} />
        <label>Amount <input class="amount" type="number" value=${amount} min="0" step="any"
          onChange=${(e) => setAmount(Number(e.target.value) || 1)} />${root ? root.unit : ""}</label>
        <${MethodSelect} methods=${info.methods} value=${method} onChange=${setMethod} />
        <label>Cut-off <input class="cutoff" type="number" value=${cutoff} min="0" max="50" step="0.5"
          onChange=${(e) => setCutoff(Math.max(0, Number(e.target.value)))} />%</label>
      </header>
      <main>
        <section>
          <div class="toolbar">
            <button onClick=${() => setExpanded(new Set(root ? [String(root.index)] : []))}>Collapse all</button>
            <button onClick=${expandAbove} disabled=${busy || !root}>Expand above</button>
            <input class="cutoff" type="number" value=${threshold} min="0.1" step="0.5" aria-label="Expansion threshold"
              onChange=${(e) => setThreshold(Math.max(0.1, Number(e.target.value)))} />%
            <span class="spacer"></span>
            <input placeholder="group name" value=${groupName} onInput=${(e) => setGroupName(e.target.value)} style="width:120px" aria-label="New group name" />
            <button disabled=${!canGroup} onClick=${groupSelected}
              title="Select at least two sibling rows with Ctrl/⌘-click">Group selected</button>
            <button onClick=${exportView} disabled=${!root}>Save view</button>
          </div>
          ${error && html`<div class="error">${error}</div>`}
          <${Tree} rows=${data.rows} rootImpact=${rootImpact} selection=${selection} onToggle=${toggle} onSelect=${select}
            onUngroup=${ungroup} ungroupable=${(r) => manualRuleFor(r) >= 0} />
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
              chartMethods=${chartMethods} setChartMethods=${setChartMethods} />`}
          ${tab === "groups" && html`<${GroupPanel} info=${info} rules=${rules} setRules=${setRules}
              selectedRow=${selectedRows.length === 1 ? selectedRows[0] : null} />`}
          ${tab === "global" && html`<${GlobalPanel} info=${info} root=${root} amount=${amount} method=${method} rules=${rules} />`}
        </section>
      </main>
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
