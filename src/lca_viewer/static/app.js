// SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
import { h, render, Fragment, createContext } from "preact";
import { useState, useEffect, useMemo, useRef, useContext } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);
// A server with several models serves each one under /db/<key>/ (see server.create_multi_app)
const DATABASE = location.pathname.match(/^\/db\/([^/]+)/)?.[1] ?? null;
const API_BASE = DATABASE ? `/db/${DATABASE}` : "";
const STORAGE_KEY = DATABASE ? `lca_viewer.view.${DATABASE}` : "lca_viewer.view";
// Where the source of this version is (GNU AGPL, section 13): a modified version run for others must link to its own source
const SOURCE_URL = "https://github.com/Phurga/lca_explorer";

// ---------------------------------------------------------------- helpers
// The API of the database shown (under /db/<key>/)
// The calculations that only read the model (the rows of a tree, the contributions, the comparison) keep their answer
// for the same question: a panel opened again, or a comparison of what a tab already shows, does not calculate again.
// ``clearAnswers`` when the model changes.
const CACHED = /^\/api\/(rows|global|global\/contributors|flows|flows\/contributors|flows\/of-process|flows\/uncharacterised|compare)$/;
const answers = new Map();  // question -> { promise, value }
const clearAnswers = () => answers.clear();
function request(url, body) {
  const full = url.startsWith("/api/") ? API_BASE + url : url;
  if (body === undefined || !CACHED.test(url)) return serverRequest(full, body);
  const key = full + JSON.stringify(body);
  let entry = answers.get(key);
  if (entry) { answers.delete(key); answers.set(key, entry); return entry.promise; }  // the most recent last
  entry = { promise: null, value: undefined };
  entry.promise = serverRequest(full, body).then((value) => { entry.value = value; return value; }, (e) => { answers.delete(key); throw e; });
  answers.set(key, entry);
  if (answers.size > 120) answers.delete(answers.keys().next().value);
  return entry.promise;
}
// The answer to a question already asked, at once (undefined: not yet)
const peek = (url, body) => (body ? answers.get((url.startsWith("/api/") ? API_BASE + url : url) + JSON.stringify(body))?.value : undefined);
// The answer of the server to ``body`` (asked again when it changes; null: nothing asked), for a component: { data, error }.
// The last answer stays while the next one comes; one asked before is there at once.
function useRequest(url, body) {
  const key = body ? JSON.stringify(body) : null;
  const [state, setState] = useState(() => ({ data: body ? peek(url, body) ?? null : null, error: null }));
  useEffect(() => {
    if (!body) { setState({ data: null, error: null }); return; }
    let live = true;
    setState((s) => ({ ...s, error: null }));
    request(url, body).then((data) => live && setState({ data, error: null }), (e) => live && setState((s) => ({ ...s, error: e.message })));
    return () => { live = false; };
  }, [url, key]);
  return state;
}

// The server's own API (databases loaded, Projects)
async function serverRequest(url, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = await response.text();
    try { detail = JSON.parse(detail).detail ?? detail; } catch { /* not JSON */ }
    if ((response.status === 404 && detail === "Not Found") || response.status === 405) {  // an unknown route
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
// An amount: three digits at most, so scientific notation below 0.01 and from 1000
function fmtAmount(x) {
  if (x === 0) return "0";
  const a = Math.abs(x);
  return a >= 1e3 || a < 0.01 ? x.toExponential(2) : x.toPrecision(3);
}
// A tick of a chart axis, with the rule of the numbers of the tree: scientific from 10 000 and below 0.001
function fmtAxis(v) {
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e4 || a < 1e-3) return v.toExponential(2).replace(/\.?0+e/, "e").replace("e+", "e");
  return String(Number(v.toPrecision(3)));
}
const pct = (x) => `${(x * 100).toFixed(1)}%`;
// The parts of a method's name. Brightway names a method (family, category, indicator…); the methods that come with an
// ecoinvent release (bw2io 0.9) have the release first, their source: ("ecoinvent-3.10.1", family, category, indicator).
// A first part "<name>-<version>" in a name of four parts or more is that source. The methods of the app's library
// (Projects) have "library" first in a model: ("library", family, category, indicator), or "library/<source>" when
// they have a source of their own ("library/ecoinvent-3.10.1").
const METHOD_SOURCE = /^[a-z][\w]*-\d+(\.\d+)+$/i;
function methodPath(m) {
  const source = (m.name.length >= 4 && METHOD_SOURCE.test(m.name[0])) || (m.name.length >= 2 && /^library(\/|$)/.test(m.name[0])) ? m.name[0] : "";
  const rest = source ? m.name.slice(1) : m.name;
  return { source, family: rest[0] ?? "", category: rest[1] ?? "", indicator: rest.slice(2).join(" | "), rest };
}
// A family as the lists group the methods: the family, and its source when it has one (the same family in two releases
// stays apart)
const familyKey = (m) => { const p = methodPath(m); return p.source ? `${p.family} · ${p.source}` : p.family; };
// How the impact methods are named in the results (set in the Methods panel): from parts (the source, the family, the
// category, the indicator, the unit after the name) or from a format of one's own with the
// tokens {source}, {family}, {category}, {indicator} and {unit}
const DEFAULT_NAME_FORMAT = { source: false, family: true, category: true, indicator: true, unit: true, mode: "parts", recipe: "{category} | {indicator} [{unit}]" };
const METHOD_TOKENS = ["source", "family", "category", "indicator", "unit"];
let nameFormat = DEFAULT_NAME_FORMAT;
let nameStamp = "";  // changes with the format, for the memos that hold names
// A format applied: empty brackets and separators left by an empty part are dropped
function applyMethodRecipe(recipe, m) {
  const p = methodPath(m);
  const values = { source: p.source, family: p.family, category: p.category, indicator: p.indicator, unit: m.unit ?? "" };
  return recipe.replace(/\{(\w+)\}/g, (token, key) => (key in values ? values[key] : token))
    .replace(/\[\s*\]|\(\s*\)/g, "").replace(/(\s*\|\s*)+(?=\s*\|)/g, "").replace(/^[\s|,·-]+|[\s|,·-]+$/g, "").replace(/\s+/g, " ").trim();
}
const customName = () => nameFormat.mode === "custom";
// The name of a method without its unit
const methodLabel = (m) => {
  if (customName()) return applyMethodRecipe(nameFormat.recipe.replace(/\s*\[?\{unit\}\]?/g, ""), m) || m.name.join(" | ");
  const p = methodPath(m);
  const parts = [nameFormat.source && p.source, nameFormat.family && p.family, nameFormat.category && p.category, nameFormat.indicator && p.indicator].filter(Boolean);
  return (parts.length ? parts : [m.name.join(" | ")]).join(" | ");
};
// A method with its unit, for the titles and the headers of files
const methodTitle = (m) => (customName() ? applyMethodRecipe(nameFormat.recipe, m) || m.name.join(" | ")
  : `${methodLabel(m)}${nameFormat.unit ? ` [${m.unit}]` : ""}`);
// A method as a row or a bar of the several methods (the family is the same for all of them: not repeated unless asked)
const methodRow = (m) => {
  if (customName()) return applyMethodRecipe(nameFormat.recipe, m) || m.name.join(" | ");
  const p = methodPath(m);
  const parts = [nameFormat.category && p.category, nameFormat.indicator && p.indicator].filter(Boolean);
  return parts.length ? parts.join(" | ") : p.rest.slice(1).join(" | ") || p.family;
};
const productOf = (rowId) => Number(rowId.split("/").pop());
// The reference product of a process row: its label starts with it ("product | name | location")
const productNameOf = (row) => (row.kind === "process" ? row.label.split(" | ")[0] : "");
const truncate = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const UNITS = {
  kilogram: "kg", "kilowatt hour": "kWh", megajoule: "MJ", "cubic meter": "m3", hectare: "ha",
  "ton kilometer": "tkm", "square meter": "m2", "square meter-year": "m2·a", "meter-year": "m·a",
  "cubic meter-year": "m3·a", "kilogram day": "kg·d", "person kilometer": "pkm", "vehicle kilometer": "vkm",
  "metric ton*km": "tkm", meter: "m", kilometer: "km", unit: "unit", hour: "h", litre: "l", "kilo becquerel": "kBq", "standard cubic meter": "Sm3",
};
const unit = (u) => UNITS[u] || (typeof u === "string" && UNITS[u.toLowerCase()]) || u;
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
// Cut-off modes (the menu next to the cut-off number). The number is a share in percent,
// except for "top" where it is a count of rows. Shares are shown of the total only for "total".
const CUTOFF_MODES = {
  total: { label: "of total", unit: "%", hint: "Smallest rows, as a share of the total" },
  parent: { label: "of parent", unit: "%", hint: "Smallest rows, as a share of their parent" },
  cumulative: { label: "cumulative", unit: "%", hint: "Largest rows, until this share of the parent" },
  top: { label: "largest", unit: "rows", hint: "This many largest rows" },
};
const CUTOFF_DEFAULTS = { total: 1, parent: 1, cumulative: 90, top: 5 };
const cutoffValue = (value, basis) => (basis === "top" ? value : value / 100);
const shareBasisOf = (basis) => (basis === "total" ? "total" : "parent");
// The reason in the others pop-up, after "N" (N rows are in others because of the cut-off)
const cutoffReason = (value, basis) => ({
  total: `in the bottom ${value} % of the total`,
  parent: `in the bottom ${value} % of their parent`,
  cumulative: `after the largest rows reaching ${value} % of their parent`,
  top: `beyond the ${value} largest`,
})[basis];
// Colour per group: a group row and the rows below it (the nearest group wins) share one
// hue, derived from the rule that made the group (its name, or the field of a group-by
// rule) so that a group keeps its colour when others change, and matches its rule's swatch
const hueOf = (key) => [...key].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 0);
// The colours of the rules (the Settings pop-up, kept by the workspace with its rules): ``keys`` are the rules of the set
// in use, in order; the palette gives its colours in that order ("auto": a hue derived from the name), a colour picked for
// a rule (``picked``: key -> "#rrggbb") wins. ``paletteStamp`` changes with any of them, for the memos that hold colours.
let rulePalette = { name: "auto", keys: [], own: {}, picked: {} };
let paletteStamp = "auto";
// The colour of any colour key (see colorKeyOf): a colour picked for it; an exchange type: its own colour, or its place in
// a palette chosen; a rule or a value: see colorOf
function colorFor(key) {
  if (rulePalette.picked[key]) return rulePalette.picked[key];
  if (key.startsWith("type:")) {
    const type = key.slice(5), colors = RULE_PALETTES[rulePalette.name]?.colors || rulePalette.own[rulePalette.name];
    return colors ? colors[Object.keys(FLOW_TYPES).indexOf(type) % colors.length] : FLOW_TYPES[type]?.color || cssVar("--bar");
  }
  return colorOf(key);
}
function colorOf(key) {
  if (rulePalette.picked[key]) return rulePalette.picked[key];
  const colors = RULE_PALETTES[rulePalette.name]?.colors || rulePalette.own[rulePalette.name];
  if (!colors) return `hsl(${hueOf(key)} 55% 52%)`;
  const i = rulePalette.keys.indexOf(key);
  return colors[(i >= 0 ? i : hueOf(key)) % colors.length];
}
// Black or white, whichever reads on ``color``
function textOn(color) {
  const hex = colorHex(color), [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return 0.299 * r + 0.587 * g + 0.114 * b > 160 ? "#1f1f1f" : "#ffffff";
}
// "#rrggbb" of a colour, for the colour picker
function colorHex(color) {
  if (color.startsWith("#")) return color;
  const [h, s, l] = color.match(/[\d.]+/g).map(Number);
  const f = (n) => {
    const k = (n + h / 30) % 12, a = (s / 100) * Math.min(l / 100, 1 - l / 100);
    return Math.round(255 * (l / 100 - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)))).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}
// Colour mode "type" (``view.colorBy``): a row is coloured by the kind of exchange it stands for
const FLOW_TYPES = {
  product: { label: "Product", hint: "The process itself: its reference product, an output", color: "hsl(270 45% 58%)" },
  technosphere: { label: "Technosphere input", hint: "Input from another process", color: "hsl(212 65% 52%)" },
  waste: { label: "Waste", hint: "Output to treatment: the waste of the process above, treated by this one", color: "hsl(32 85% 50%)" },
  avoided: { label: "Avoided product", hint: "Negative input from another process: a product the process above replaces (substitution, as in consequential databases)", color: "hsl(320 50% 55%)" },
  resource: { label: "Resource", hint: "Input from nature (biosphere)", color: "hsl(135 45% 42%)" },
  emission: { label: "Emission", hint: "Output to nature (biosphere)", color: "hsl(2 65% 52%)" },
  direct: { label: "Direct", hint: "Direct emissions row: the exchanges with nature of the process above", color: "hsl(178 50% 38%)" },
};
// Columns of the list after the process (right-click the headers to choose); ``exchange``: what the exchange of the row
// says: supplying a process, in the dataset of the process above it; emitting a flow, in the dataset of its emitter
const COLUMNS = [
  { id: "amount", label: "Amount", width: 130 },
  { id: "impact", label: "Impact", width: 90 },
  { id: "share", label: "Share", width: 150 },
  { id: "comment", label: "Comment", width: 260, exchange: true, hint: "Comment on the exchange of the row: supplying a process (in the dataset of the process above it) or emitting a flow (in the dataset of the process emitting it); i: all of it" },
  { id: "uncertainty", label: "Uncertainty", width: 230, exchange: true, hint: "Uncertainty distribution recorded on the exchange (c: with the pedigree and the rest)",
    text: (r) => (r.exchanges || []).map(uncertaintyText).filter(Boolean).join("; ") },
  { id: "pedigree", label: "Pedigree", width: 110, exchange: true, hint: "Pedigree scores of the exchange: reliability, completeness, time, geography, technology",
    text: (r) => (r.exchanges || []).map((e) => e.pedigree && Object.values(e.pedigree).join(",")).filter(Boolean).join("; ") },
  { id: "formula", label: "Formula", width: 160, exchange: true, hint: "Formula of the amount, as recorded",
    text: (r) => (r.exchanges || []).map((e) => e.formula).filter(Boolean).join("; ") },
  { id: "variable", label: "Variable name", width: 130, exchange: true, hint: "Variable name of the exchange, as recorded",
    text: (r) => (r.exchanges || []).map((e) => e["variable name"]).filter(Boolean).join("; ") },
];
const DEFAULT_COLUMNS = ["amount", "impact", "share"];
// First start (nothing kept in the browser yet): the plain reading of a supply chain, and anything more complex one click
// away for whoever wants it. A tab shows a list with three columns (Comment, Uncertainty… in the columns menu), the rows
// all shown (no cut-off), nothing grouped, full names, no colours, the direct emissions organised by compartment. The page: the left panel (processes,
// methods, rules) closed, a tab's card on its Explore panel (see FIRST_PANELS). Once the user
// changes one of them, theirs is kept instead.
const DEFAULT_VIEW_COLUMNS = DEFAULT_COLUMNS;
const DEFAULT_CUTOFF = 0;
// The options of a tab's view, with their defaults: the preferences, the view files and the tabs' kept state follow this
// list (a switch is kept as "1"/"0", a list as JSON, a text as it is)
const DEFAULT_VIEW = {
  naming: "full", organize: "none", organizeFlows: "compartment", hideOthers: false, groupProducts: false, groupByRules: false, groupSubstance: false,
  groupCompartment: false, groupSubcompartment: false, groupFields: [], collapseSingletons: false, groupColors: false, colorBy: "group",
  columns: DEFAULT_VIEW_COLUMNS,
};
// The options the rows of a tree depend on (sent to /api/rows): kept with each tab, for the comparison of the tabs' trees
const TREE_OPTIONS = ["groupProducts", "groupByRules", "groupSubstance", "groupCompartment", "groupSubcompartment", "groupFields", "collapseSingletons"];
const pick = (object, keys) => Object.fromEntries(keys.filter((k) => k in object).map((k) => [k, object[k]]));
// The rules a workspace starts with
const DEFAULT_RULE_LABELS = ["Transport", "Electricity", "Heat & fuels", "Infrastructure", "Waste treatment"];
const defaultRules = () => DEFAULT_RULE_LABELS.map((label) => ({ scope: "*", ...PRESETS.find((q) => q.label === label).rule }));
const columnText = (c, r) => (c.text ? c.text(r) : r[c.id]) || "";
const fromNature = (compartment) => /resource/i.test(compartment || "");
// The type of a row (a key of FLOW_TYPES); none for an others row. A group or merged row takes the type of its first member.
function flowTypeOf(r) {
  if (r.kind === "rest") return null;
  if (r.kind === "direct") return "direct";
  if (r.kind === "flow") return fromNature(r.flow.compartment) ? "resource" : "emission";
  if (r.depth === 0) return "product";
  const part = r.parts?.[0];
  if (part?.flow !== undefined) return fromNature(part.location) ? "resource" : "emission";
  if (r.kind === "process" ? r.waste : part?.waste) return "waste";
  // a negative input of a product (not a waste): an avoided product
  return (r.kind === "process" ? r.amount : part ? part.amount : r.amount) < 0 ? "avoided" : "technosphere";
}
// Organise by: below each row, its rows in sections, the list showing a heading for each; nothing is summed or hidden
// (Group by makes rows). One choice for the rows below a process (``view.organize``) and one for the elementary flows of
// a direct emissions row (``view.organizeFlows``), "none" for neither. *Type*: the sections of the form of a new process
// (and the flow type colours), in that order; the others are in the order of their largest row. The others rows come
// last, with no heading.
const ORGANIZE = { type: "Type", location: "Location", activity_type: "Activity type" };
const ORGANIZE_HINTS = { type: "Sections by exchange type: inputs from technosphere, direct emissions, waste…", location: "Sections by location",
  activity_type: "Sections by activity type (market, production…)" };
const ORGANIZE_FLOWS = { compartment: "Compartment", subcompartment: "Subcompartment", type: "Type" };
const ORGANIZE_FLOWS_HINTS = { compartment: "Direct emissions in sections by compartment: air, water, soil, natural resource",
  subcompartment: "Direct emissions in sections by compartment and subcompartment", type: "Direct emissions in sections: inputs from nature, emissions" };
const SPLIT_SECTIONS = [
  ["technosphere", "Inputs from technosphere"], ["resource", "Inputs from nature"], ["emission", "Emissions"],
  ["direct", "Direct emissions"], ["waste", "Waste to treatment"], ["avoided", "Avoided products"],
];
const SECTION_LABELS = Object.fromEntries(SPLIT_SECTIONS);
const SECTION_RANK = { ...Object.fromEntries(SPLIT_SECTIONS.map(([key], i) => [key, i])), rest: SPLIT_SECTIONS.length };  // the others rows last, with no heading of their own
// A row of elementary flows: a flow, or a group of them (below a direct emissions row)
const isFlowRow = (r) => r.kind === "flow" || r.parts?.[0]?.flow !== undefined;
// The section of a row, as the options organise it: { key, label } (key "rest": an others row), none when not organised
function sectionOf(r, view) {
  if (r.kind === "rest") return { key: "rest", label: null };
  const flows = isFlowRow(r);
  const by = flows ? view.organizeFlows : view.organize;
  if (!by || by === "none" || r.depth === 0) return null;
  if (by === groupingOf(view)[flows ? "flows" : "processes"]) return null;  // grouped by it already: a heading per group row says nothing more
  if (by === "type") { const type = flowTypeOf(r); return { key: type, label: SECTION_LABELS[type] }; }
  if (!flows && r.kind === "direct") return { key: "direct", label: SECTION_LABELS.direct };
  const first = flows ? r.flow || r.parts[0] : r.meta || r.parts?.[0] || r.metas?.[0] || {};
  const value = by === "subcompartment" ? [first.compartment, first.subcompartment || "unspecified"].filter(Boolean).join(" · ")
    : (by === "location" && r.kind === "process" ? r.location : null) ?? first[by];
  const text = value || `no ${flows ? FLOW_FIELD_LABELS[by] || by : fieldLabel(by, false)}`;
  return { key: `${by}:${text}`, label: text };
}
const organizing = (view) => (view.organize && view.organize !== "none") || (view.organizeFlows && view.organizeFlows !== "none");
// The groups of the flows of a direct emissions row asked for by the options of the view (Group by substance, by
// compartment): rules on the elementary flows, applied after the others
const flowRulesOf = (view) => [
  ...(view.groupSubstance ? [{ by: "name", target: "flows", scope: "*", plain: true }] : []),
  ...(view.groupCompartment ? [{ by: "compartment", target: "flows", scope: "*", plain: true }] : []),
  ...(view.groupSubcompartment ? [{ by: "subcompartment", target: "flows", scope: "*", plain: true }] : []),
  // the fields of the By... menu: groups of processes by their location, activity type...
  ...(view.groupFields || []).map((by) => ({ by, scope: "*", plain: true })),
];
// Group by: the one grouping of the processes ("product", a field of the By… menu, or null) and of the flows ("name",
// "compartment", "subcompartment", or null) a view applies. A view saved when several could be chosen applies the first
// the server tries (the fields before the product; substance, compartment, subcompartment): ``oneGrouping`` keeps it.
function groupingOf(view) {
  const fields = view.groupFields || [];
  return {
    processes: fields[0] ?? (view.groupProducts ? "product" : null),
    flows: view.groupSubstance ? "name" : view.groupCompartment ? "compartment" : view.groupSubcompartment ? "subcompartment" : null,
  };
}
const processGrouping = (k) => ({ groupProducts: k === "product", groupFields: k && k !== "product" ? [k] : [] });
const flowGrouping = (k) => ({ groupSubstance: k === "name", groupCompartment: k === "compartment", groupSubcompartment: k === "subcompartment" });
const oneGrouping = (view) => { const g = groupingOf(view); return { ...view, ...processGrouping(g.processes), ...flowGrouping(g.flows) }; };
// Views kept before Organise by: *Split by type* organised the rows by type
const withOrganize = (view, kept) => (kept.splitByType && !("organize" in kept) ? { ...view, organize: "type", organizeFlows: "type" } : view);
// Hide others: the others rows and what an expanded one shows below it (its members, and their own rows). The rows are
// in display order, so what is below a row is what follows it, deeper.
function withoutOthers(rows) {
  const out = [];
  let below = null;  // depth of the others row whose rows are being skipped
  for (const r of rows) {
    if (below !== null && r.depth > below) continue;
    below = r.kind === "rest" ? r.depth : null;
    if (below === null) out.push(r);
  }
  return out;
}
// The rows with the rows below each row ordered by section (the order inside a section is kept): the sections of a type
// in their order, the others in the order of their first row (the rows come largest first)
function splitRows(rows, view) {
  const below = new Map();
  for (const r of rows) {
    if (!below.has(r.parent)) below.set(r.parent, []);
    below.get(r.parent).push(r);
  }
  const out = [];
  const place = (r) => {
    out.push(r);
    const first = new Map();
    const rank = (c) => {
      const section = sectionOf(c, view);
      if (!section) return 0;
      if (section.key === "rest") return 2e9;
      if (section.key === "direct" && view.organize !== "type") return 1e9;  // by location…: the direct emissions after the processes
      if (section.key in SECTION_RANK) return SECTION_RANK[section.key];
      if (!first.has(section.key)) first.set(section.key, SPLIT_SECTIONS.length + first.size);
      return first.get(section.key);
    };
    (below.get(r.id) || []).map((c) => [rank(c), c]).sort((a, b) => a[0] - b[0]).forEach(([, c]) => place(c));
  };
  rows.filter((r) => r.parent == null || !rows.some((q) => q.id === r.parent)).forEach(place);
  return out;
}
// Color by (one mode at a time): "group" (the rules of the Rules panel), "type" (of exchange), or a value of the row:
// "location", "activity_type" (processes), "compartment" (elementary flows). ``view.groupColors`` false: none.
const COLOR_MODES = { group: "Rules", type: "Type", location: "Location", activity_type: "Activity type", compartment: "Compartment" };
const COLOR_HINTS = { group: "By your rules: a group and the rows below it take its rule's colour", type: "By exchange type: technosphere, waste, emissions…",
  location: "Processes by their location", activity_type: "Processes by their activity type", compartment: "Direct emissions by compartment" };
const colorModeOf = (view) => (view.groupColors ? view.colorBy || "group" : "none");
// What gives a row its colour in ``mode``: { key (of colorFor, and of the colours picked), name (the legend's) }, or none
function colorKeyOf(r, mode) {
  if (r.kind === "rest" || mode === "none") return null;
  if (mode === "group") return r.rule ? { key: r.rule, name: r.rule } : null;
  if (mode === "type") { const t = flowTypeOf(r); return t ? { key: `type:${t}`, name: FLOW_TYPES[t].label } : null; }
  const flows = isFlowRow(r);
  if (mode === "compartment") {
    const value = flows ? (r.flow || r.parts[0]).compartment : null;
    return value ? { key: `compartment:${value}`, name: value } : null;
  }
  if (flows || r.kind === "direct") return null;
  const first = r.meta || r.parts?.[0] || r.metas?.[0];
  const value = (mode === "location" && r.kind === "process" ? r.location : null) ?? first?.[mode];
  return value ? { key: `${mode}:${value}`, name: value } : null;
}
// The colour of each row (row id -> colour), empty with no colours. By rules, the rows below a group take its colour.
function groupColors(rows, view) {
  const colors = new Map();
  const mode = colorModeOf(view);
  if (mode === "none") return colors;
  for (const r of rows) {
    const found = colorKeyOf(r, mode);
    if (found) colors.set(r.id, colorFor(found.key));
    else if (mode === "group" && colors.has(r.parent)) colors.set(r.id, colors.get(r.parent));
  }
  return colors;
}
// The colours in use in ``rows``, once each, in the order of the rows: [{ key, name, color }]
function colorLegend(rows, view) {
  const mode = colorModeOf(view), seen = new Map();
  for (const r of rows) {
    const found = colorKeyOf(r, mode);
    if (found && !seen.has(found.key)) seen.set(found.key, { ...found, color: colorFor(found.key) });
  }
  return [...seen.values()];
}
// Amount column of a row: product supplied, waste treated, or elementary flow emitted
function rowAmount(r) {
  if (r.kind === "merged" || r.kind === "group" || r.kind === "flow") return r.unit ? `${fmtAmount(r.amount)} ${unit(r.unit)}` : "";
  if (r.kind !== "process") return "";
  return r.waste ? `${fmtAmount(-r.amount)} ${unit(r.unit)} treated` : `${fmtAmount(r.amount)} ${unit(r.unit)}`;
}
// Rows made of processes: a process, a merged process, a group of processes (not of flows)
const ofProcesses = (r) => r.kind === "process" || r.kind === "merged" || (r.kind === "group" && !!r.products);
// Status bar totals of the selected processes, merged processes and groups of processes
// included (a group or merged row counts its member processes): amounts per unit (waste as
// treated, as in the Amount column; units are not added together) and the summed impact. A row
// inside another selected row is left out: its impact is already in that row's cumulative one.
function selectedProcessTotals(byId, selection) {
  const inSelectedRow = (r) => {
    for (let p = byId.get(r.parent); p; p = byId.get(p.parent)) if (ofProcesses(p) && selection.has(p.id)) return true;
    return false;
  };
  const counted = [...selection].map((id) => byId.get(id))
    .filter((r) => r && ofProcesses(r) && !inSelectedRow(r));
  const amounts = new Map();
  let count = 0;
  for (const r of counted) {
    for (const p of r.parts || [r]) {
      const key = p.waste ? `${unit(p.unit)} treated` : unit(p.unit);
      amounts.set(key, (amounts.get(key) || 0) + (p.waste ? -p.amount : p.amount));
      count++;
    }
  }
  return {
    count,
    amounts: [...amounts].map(([u, n]) => `${fmtAmount(n)} ${u}`).join(" + "),
    impact: counted.reduce((sum, r) => sum + r.impacts[0], 0),
  };
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

// A CSV file: the ``comments`` first (# …), the ``header``, then the ``rows`` (a number as it is, a text quoted)
const csvQuote = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
function downloadCsv(filename, header, rows, comments = []) {
  const line = (cells) => cells.map((v) => (typeof v === "number" ? String(v) : csvQuote(v))).join(",");
  download(filename, [...comments.map((c) => `# ${c}`), line(header), ...rows.map(line)].join("\n"), "text/csv");
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
  if (NUMBER_FIELDS.has(p.field)) return `${p.field} ${(NUMBER_OPS.find(([op]) => op === p.op) || [, p.op])[1]} ${value}`;
  return `${p.field} ${p.op} "${value}"`;
}

const PALETTE = [
  "#2f7d6d", "#d1793f", "#5b6fb5", "#c9a227", "#9c5a9e", "#4f9fc4", "#b5525c",
  "#7a9a3a", "#8c6d52", "#4c8a8f", "#d4a0c0", "#777777",
];
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const RULE_PALETTES = {
  auto: { label: "Automatic", hint: "A hue derived from each rule's or value's name; the exchange types their own colours", colors: null },
  categorical: { label: "Categorical", hint: "The colours of the charts", colors: PALETTE.slice(0, 11) },
  safe: { label: "Colour-blind safe", hint: "Eight colours told apart by most kinds of colour blindness (Okabe-Ito)",
    colors: ["#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#999999"] },
  vivid: { label: "Vivid", hint: "Saturated, far apart",
    colors: ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46c0c0", "#f032e6", "#9aaa00", "#008080", "#9a6324"] },
  soft: { label: "Soft", hint: "Pastel, for many groups",
    colors: ["#8dd3c7", "#bebada", "#fb8072", "#80b1d3", "#fdb462", "#b3de69", "#fccde5", "#bc80bd", "#ccebc5", "#ffed6f"] },
  earth: { label: "Earth", hint: "Greens, browns and ochres",
    colors: ["#6b8e23", "#8b5a2b", "#cd853f", "#2e8b57", "#a0522d", "#bdb76b", "#556b2f", "#d2b48c", "#8fbc8f", "#b8860b"] },
};

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
// ``exportRef``: where the chart is given to its export menu
function Chart({ option, height = 420, onClick, exportRef }) {
  const ref = useRef(null);
  const chart = useRef(null);
  const clickHandler = useRef(onClick);
  clickHandler.current = onClick;
  useEffect(() => {
    chart.current = echarts.init(ref.current);
    if (exportRef) exportRef.current = chart.current;
    chart.current.on("click", (e) => clickHandler.current?.(e));
    const observer = new ResizeObserver(() => chart.current.resize());
    observer.observe(ref.current);
    return () => { observer.disconnect(); if (exportRef) exportRef.current = null; chart.current.dispose(); };
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
    tooltip: { trigger: "item", confine: true },
    xAxis: { axisLabel: { color: muted, formatter: fmtAxis, hideOverlap: true }, splitLine: { lineStyle: { color: border } } },
    yAxis: { axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
  };
}

// ---------------------------------------------------------------- shared query builder
// Used both by the process search and by the grouping rules, so filters read the same everywhere.
const OPS = [["contains", "contains"], ["eq", "is"], ["ne", "is not"], ["in", "is one of"], ["startswith", "starts with"], ["regex", "matches regex"]];
// The operators of a number (the amount of an exchange)
const NUMBER_OPS = [["lt", "<"], ["le", "≤"], ["gt", ">"], ["ge", "≥"], ["eq", "="], ["ne", "≠"]];
const NUMBER_FIELDS = new Set(["exchange_amount"]);
const opsOf = (field) => (NUMBER_FIELDS.has(field) ? NUMBER_OPS : OPS);
// The fields of the exchange that brings a row in (rules of the tree only): its type (technosphere, avoided product, waste;
// resource, emission for a flow) and its amount, as recorded in the dataset of the process above, per unit of its product
const EXCHANGE_FIELDS = new Set(["exchange_type", "exchange_amount"]);
const usesExchange = (p) => !!p && (p.all || p.any ? (p.all || p.any).some(usesExchange) : p.not ? usesExchange(p.not) : EXCHANGE_FIELDS.has(p.field));
const FIELD_LABELS = {
  name: "activity name", reference_product: "product", location: "location", unit: "unit",
  activity_type: "activity type", isic: "ISIC class", isic_division: "ISIC division",
  cpc: "CPC class", cpc_section: "CPC section", database: "database",
  exchange_type: "exchange type", exchange_amount: "exchange amount",
};
// flow_type: "product" or "waste" (the sign of what the process produces), not a type of elementary flow
FIELD_LABELS.flow_type = "product / waste";
const FLOW_FIELD_LABELS = { name: "substance", compartment: "compartment", subcompartment: "subcompartment", unit: "unit", database: "database",
  exchange_type: "exchange type", exchange_amount: "exchange amount" };
const fieldLabel = (f, flows) => (flows ? FLOW_FIELD_LABELS[f] : FIELD_LABELS[f]) || f;
let conditionIds = 0;

// ``source``: where the values of the fields come from, when not the open model: ``{ options(field, text, limit) }``
// giving ``{ items: [{ value, name?, count }], named, total }`` (the edit-mode search reads its project)
function useValues(field, text, flows, source) {
  const [values, setValues] = useState([]);
  useEffect(() => {
    const timer = setTimeout(() => {
      (source
        ? source.options(field, text, 100).then((r) => r.items.map((i) => i.value))
        : request(`/api/values/${field}?q=${encodeURIComponent(text)}&limit=100${flows ? "&target=flows" : ""}`)
      ).then(setValues).catch(() => setValues([]));
    }, 150);
    return () => clearTimeout(timer);
  }, [field, text, flows, source]);
  return values;
}

// The (i) of a filter condition: what its field can hold. Locations come with the country or region (to find the
// code of a country), ISIC and CPC with the names of their codes, the others with their values and how many
// activities have each. A short list (unit, activity type, …) also shows when the pointer rests on the button;
// a click keeps any list open, with a box to search it, and a click on a row fills the value in.
// Children drawn in a box of the page itself (not clipped or moved by a transformed parent such as the search
// panel). Mouse presses in it stay in it, so that the panels closing on a press elsewhere keep their place.
function BodyPortal({ children }) {
  const host = useRef(null);
  if (!host.current) {
    host.current = document.createElement("div");
    host.current.addEventListener("mousedown", (e) => e.stopPropagation());
  }
  useEffect(() => {
    document.body.appendChild(host.current);
    return () => { render(null, host.current); host.current.remove(); };
  }, []);
  useEffect(() => { render(children, host.current); });
  return null;
}

// A popup with an explanation when the pointer rests on an element, drawn on the page (not clipped by the panel it is in):
// ``const [props, popup] = useTip(text)``, the props go on the element, the popup next to it
// ``aligned``: the popup starts where the header of the (i) starts, under it (not where the (i) is)
const TIP_HEADERS = "h3, h4, h5, legend, .fold-head, .view-group, .details-head, .info-title, label, .row";
function useTip(text, aligned = true) {
  const [box, setBox] = useState(null);
  const leave = useRef(null);
  const props = {
    onMouseEnter: (e) => {
      clearTimeout(leave.current);
      const own = e.currentTarget.getBoundingClientRect();
      const header = aligned ? e.currentTarget.closest(TIP_HEADERS) : null;
      setBox(header ? { left: header.getBoundingClientRect().left, bottom: own.bottom } : own);
    },
    onMouseLeave: () => { leave.current = setTimeout(() => setBox(null), 80); },
  };
  const width = Math.min(360, window.innerWidth - 16);
  const popup = box && html`<${BodyPortal}>
    <div class="tip" role="tooltip" style=${{ left: `${Math.max(8, Math.min(box.left, window.innerWidth - width - 8))}px`, top: `${box.bottom + 6}px`, width: `${width}px` }}>${text}</div>
  <//>`;
  return [props, popup];
}

// A tab whose name has its explanation in a popup
function TabButton({ tip, children, ...props }) {
  const [tipProps, popup] = useTip(tip, false);
  return html`<button ...${props} ...${tipProps}>${children}</button>${popup}`;
}

// A small (i) with an explanation in a popup
function InfoTip({ text, label = "What this is" }) {
  const [tipProps, popup] = useTip(text);
  return html`<button class="help-btn" aria-label=${label} ...${tipProps}>i</button>${popup}`;
}

// What the page says while it waits: an animated mark and the text
const Waiting = ({ text }) => html`<div class="empty waiting" role="status"><span class="spinner" aria-hidden="true"></span> ${text}</div>`;

const SHORT_FIELDS = new Set(["unit", "activity_type", "flow_type", "database", "compartment", "exchange_type"]);
// ISIC and CPC classes are written "code:name" in the data, so a code is matched on the start of it
const CLASS_FIELDS = new Set(["isic", "cpc"]);

function FieldHelp({ condition, flows, source, onPick }) {
  const { field } = condition;
  const [pinned, setPinned] = useState(false);
  const [hover, setHover] = useState(false);
  const [text, setText] = useState("");
  const [found, setFound] = useState(null);
  const [at, setAt] = useState(null);
  const button = useRef(null), leave = useRef(null), focusInput = useRef((el) => el && el.focus());
  const short = SHORT_FIELDS.has(field);
  const shown = pinned || (short && hover);
  useEffect(() => { setFound(null); setText(""); setPinned(false); setHover(false); }, [field]);
  useEffect(() => {
    if (!shown) return;
    const box = button.current.getBoundingClientRect();
    const width = Math.min(400, window.innerWidth - 16), top = box.bottom + 4;
    setAt({ left: Math.max(8, Math.min(box.left, window.innerWidth - width - 8)), top, width, maxHeight: Math.max(160, window.innerHeight - top - 12) });
  }, [shown]);
  useEffect(() => {
    if (!shown) return;
    const timer = setTimeout(() => {
      (source ? source.options(field, text, 300) : request(`/api/options/${field}?q=${encodeURIComponent(text)}&limit=300${flows ? "&target=flows" : ""}`))
        .then(setFound).catch(() => setFound({ items: [], total: 0, named: false }));
    }, found ? 150 : 0);
    return () => clearTimeout(timer);
  }, [shown, field, text, flows, source]);
  useEffect(() => {
    if (!pinned) return;
    const away = (e) => { if (!button.current?.parentElement.contains(e.target)) setPinned(false); };  // presses in the menu do not get here
    const esc = (e) => { if (e.key === "Escape") { e.stopPropagation(); setPinned(false); } };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc, true);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc, true); };
  }, [pinned]);
  const pick = (item) => {
    onPick(item);
    setPinned(false);
    setHover(false);
  };
  const items = found?.items || [];
  return html`
    <span class="help" onMouseEnter=${() => { clearTimeout(leave.current); setHover(true); }}
          onMouseLeave=${() => { clearTimeout(leave.current); leave.current = setTimeout(() => setHover(false), 200); }}>
      <button ref=${button} class=${`help-btn action ${pinned ? "on" : ""}`} aria-label=${`Values of ${fieldLabel(field, flows)}`} aria-expanded=${shown}
        title=${CLASS_FIELDS.has(field) || field === "location" || field.endsWith("division") || field.endsWith("section") ? "Codes and what they stand for" : "The values of this field"}
        onClick=${() => setPinned(!pinned)}>?</button>
      ${shown && at && html`<${BodyPortal}>
        <div class="help-menu" role="dialog" aria-label=${`Values of ${fieldLabel(field, flows)}`}
             onMouseEnter=${() => { clearTimeout(leave.current); setHover(true); }}
             onMouseLeave=${() => { clearTimeout(leave.current); leave.current = setTimeout(() => setHover(false), 200); }}
             style=${{ left: `${at.left}px`, top: `${at.top}px`, width: `${at.width}px`, maxHeight: `${at.maxHeight}px` }}>
          ${!short && html`<input ref=${focusInput.current} placeholder=${found?.named ? "Search a code or a name" : "Search"} value=${text} onInput=${(e) => setText(e.target.value)} aria-label="Search the values" />`}
          <div class="help-list">
            ${!found ? html`<div class="muted help-note">…</div>`
              : !items.length ? html`<div class="muted help-note">No value${text ? " matches" : ""}.</div>`
              : html`<table>${items.map((it) => html`
                  <tr key=${it.value} onClick=${() => pick(it)} title=${it.full || it.value}>
                    <td class="code">${it.value}</td>
                    ${found.named && html`<td class="what">${it.name}</td>`}
                    <td class="num muted">${it.count}</td>
                  </tr>`)}</table>`}
          </div>
          ${found && found.total > items.length && html`<div class="muted help-note">${items.length} of ${found.total}: search to narrow the list.</div>`}
        </div><//>`}
    </span>`;
}

function Condition({ condition, fields, flows, source, onChange, onRemove }) {
  const listId = useMemo(() => `values-${++conditionIds}`, []);
  // For "is one of", suggest values for the item being typed
  const typed = condition.op === "in" ? condition.value.split(",").pop().trim() : condition.value;
  const values = useValues(condition.field, typed, flows, source);
  // A value chosen in the (i): added to the list of "is one of"; for ISIC and CPC the code, matched on its start
  // (the data reads "0111:Growing of …"; "is not" has to be given the whole text)
  const pick = (item) => {
    const { field, op, value } = condition;
    if (CLASS_FIELDS.has(field)) {
      if (op === "ne") onChange({ ...condition, value: item.full });
      else onChange({ ...condition, value: item.value, op: op === "contains" || op === "regex" ? op : "startswith" });
    } else if (op === "in") {
      const list = value.split(",").map((v) => v.trim()).filter(Boolean);
      onChange({ ...condition, value: (list.includes(item.value) ? list : [...list, item.value]).join(", ") });
    } else onChange({ ...condition, value: item.value });
  };
  return html`
    <div class="row condition">
      <select value=${condition.field} aria-label="Field" onChange=${(e) => {
        const field = e.target.value, ops = opsOf(field);
        onChange({ ...condition, field, op: ops.some(([op]) => op === condition.op) ? condition.op : ops[0][0] });
      }}>
        ${fields.map((f) => html`<option value=${f}>${fieldLabel(f, flows)}</option>`)}
      </select>
      <select value=${condition.op} onChange=${(e) => onChange({ ...condition, op: e.target.value })} aria-label="Operator">
        ${opsOf(condition.field).map(([op, label]) => html`<option value=${op}>${label}</option>`)}
      </select>
      <input value=${condition.value} list=${listId} placeholder=${condition.op === "in" ? "a, b, c" : NUMBER_FIELDS.has(condition.field) ? "number" : "value"}
             inputmode=${NUMBER_FIELDS.has(condition.field) ? "decimal" : undefined}
             onInput=${(e) => onChange({ ...condition, value: e.target.value })} aria-label="Value" />
      <datalist id=${listId}>
        ${values.map((v) => html`<option value=${condition.op === "in" ? condition.value.replace(/[^,]*$/, (condition.value.includes(",") ? " " : "") + v) : v} />`)}
      </datalist>
      ${!NUMBER_FIELDS.has(condition.field) && html`<${FieldHelp} condition=${condition} flows=${flows} source=${source} onPick=${pick} />`}
      <button onClick=${onRemove} aria-label="Remove condition">×</button>
    </div>`;
}

function QueryBuilder({ query, setQuery, fields, flows, source }) {
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
        <${Condition} key=${c.id} condition=${c} fields=${fields} flows=${flows} source=${source}
          onChange=${(next) => update({ conditions: conditions.map((x, j) => (j === i ? next : x)) })}
          onRemove=${() => update({ conditions: conditions.filter((_, j) => j !== i) })} />`)}
      <div class="row"><button onClick=${() => update({ conditions: [...conditions, newCondition(flows ? "compartment" : "location", "eq")] })}>+ condition</button></div>
    </div>`;
}

const newCondition = (field = "name", op = "contains", value = "") => ({ id: ++conditionIds, field, op, value });
const emptyQuery = (field, op) => ({ combinator: "all", conditions: [newCondition(field, op)] });

function toPredicate({ conditions, combinator }) {
  const parts = conditions.filter((c) => c.value.trim()).map((c) => ({
    field: c.field, op: c.op,
    value: c.op === "in" ? c.value.split(",").map((v) => v.trim()).filter(Boolean)
      : NUMBER_FIELDS.has(c.field) ? Number(c.value.trim().replace(",", ".")) : c.value.trim(),
  })).filter((p) => !(NUMBER_FIELDS.has(p.field) && Number.isNaN(p.value)));
  if (parts.length === 0) return null;
  if (parts.length === 1) return parts[0];
  return { [combinator]: parts };
}

// ---------------------------------------------------------------- process search
function Highlight({ text, words }) {
  if (!words.length || !text) return text;
  const pattern = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "ig");
  return text.split(pattern).map((part, i) => (i % 2 ? html`<mark>${part}</mark>` : part));
}

// The box of the top bar is the value of the first filter, which starts as "activity name contains …": what is typed
// there fills it in (and the other way round); the other filters narrow the search
const searchQuery = () => emptyQuery("name", "contains");
function FunctionalUnit({ root, onSelect, fields }) {
  const [filters, setFilters] = useState(searchQuery);
  const first = filters.conditions[0];
  const text = first ? first.value : "";
  const setText = (value) => setFilters((q) => (q.conditions.length
    ? { ...q, conditions: q.conditions.map((c, i) => (i === 0 ? { ...c, value } : c)) }
    : { ...q, conditions: [newCondition("name", "contains", value)] }));
  const [open, setOpen] = useState(false);
  const [found, setFound] = useState({ total: 0, results: [] });
  const [active, setActive] = useState(0);
  const [error, setError] = useState(null);
  const box = useRef(null);
  const predicate = useMemo(() => toPredicate(filters), [filters]);
  const activeFilters = filters.conditions.filter((c) => c.value.trim()).length;
  const searching = Boolean(predicate);

  useEffect(() => {
    const close = (e) => box.current && !box.current.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  useEffect(() => {
    if (!open || !searching) { setFound({ total: 0, results: [] }); return; }
    const timer = setTimeout(() => {
      request("/api/search", { text: "", predicate, limit: 100 })
        .then((f) => { setFound(f); setActive(0); setError(null); })
        .catch((e) => setError(e.message));
    }, 200);
    return () => clearTimeout(timer);
  }, [predicate, open]);

  // ``inTab`` (Ctrl/⌘ with Enter or the click): the process opens in a new tab, this one stays as it is
  function choose(r, inTab) {
    onSelect(r, inTab);
    setText("");
    setOpen(false);
    // The keys of the tab (Ctrl+Z) are not for a field
    box.current?.querySelector("input")?.blur();
  }
  function onKeyDown(e) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, found.results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && found.results[active]) choose(found.results[active], e.ctrlKey || e.metaKey);
    else if (e.key === "Escape") setOpen(false);
  }
  useEffect(() => {
    box.current?.querySelector(".search-results .active")?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const current = root ? `${root.name} | ${root.location}${root.reference_product ? ` — ${root.reference_product}` : ""}` : "";
  const words = first && ["name", "reference_product"].includes(first.field) && first.op !== "regex" ? text.toLowerCase().split(/\s+/).filter(Boolean) : [];
  return html`
    <div class="fu" ref=${box}>
      <div class="fu-input">
        <input placeholder=${current || "Search a process: name, product, location…"} value=${text}
               title=${`${current ? `${current} — ` : ""}Search a process (s or Ctrl/⌘ + K); Enter shows it in the tab, Ctrl/⌘ + Enter opens it in a new tab`}
               onInput=${(e) => { setText(e.target.value); setOpen(true); }} onFocus=${() => setOpen(true)}
               onKeyDown=${onKeyDown} aria-label="Process" />
      </div>
      ${open && html`
        <div class="search-panel">
          <div class="search-filters">
            <${QueryBuilder} query=${filters} setQuery=${setFilters} fields=${fields} />
            ${activeFilters > 0 && html`<button onClick=${() => setFilters(searchQuery())}>Clear filters</button>`}
          </div>
          ${error && html`<div class="error">${error}</div>`}
          ${searching
            ? html`
              <div class="search-meta">${found.total} match${found.total === 1 ? "" : "es"}${found.total > found.results.length ? `, first ${found.results.length} shown` : ""} · ↑↓ Enter</div>
              <div class="search-results" role="listbox">
                ${found.results.map((r, i) => html`
                  <div role="option" aria-selected=${i === active} class=${i === active ? "active" : ""}
                       onMouseEnter=${() => setActive(i)} onMouseDown=${(e) => { e.preventDefault(); choose(r, e.ctrlKey || e.metaKey); }}>
                    <div><b><${Highlight} text=${r.name} words=${words} /></b> <span class="loc-chip">${r.location}</span></div>
                    <small><${Highlight} text=${r.reference_product} words=${words} /> · ${unit(r.unit)}</small>
                  </div>`)}
              </div>`
            : html`<div class="search-meta">Type part of an activity name: it fills the first filter above (change its field or add filters: location, product, ISIC…).</div>`}
        </div>`}
    </div>`;
}

// Started on your own computer (lca_explorer), the server stops with its last tab (see lifetime.py): each tab says it
// is open every 10 s, and whether it is hidden (the server then waits longer for the next time: browsers slow the
// timers of hidden tabs), and closing one asks first. The pings come from a worker: a dialog (confirm) or a long
// calculation holds up the page's own timers, not the worker's. ``leaving``: the page goes to another of its own pages
// (no question then); ``unreachable``: the last ping failed (no question either; the next ping tries again: one that
// fails, on waking up for instance, does not silence the tab).
let leaving = false, unreachable = false;
const goTo = (url) => { leaving = true; location.href = url; };
const LIFETIME = fetch("/api/app").then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
const TAB = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const PINGS = `let url = "", hidden = false;
const ping = () => url && fetch(url + (hidden ? "&hidden=1" : ""), { method: "POST" })
  .then((r) => postMessage(r.ok), () => postMessage(false));
onmessage = (e) => { ({ url, hidden } = e.data); if (e.data.now) ping(); };
setInterval(ping, 10000);`;
LIFETIME.then(({ stops_with_browser }) => {
  if (!stops_with_browser) return;
  const url = `${location.origin}/api/tab/alive?tab=${TAB}`;
  let worker = null;
  try {
    worker = new Worker(URL.createObjectURL(new Blob([PINGS], { type: "text/javascript" })));
    worker.onmessage = (e) => { unreachable = !e.data; };
  } catch { /* no worker: the page pings itself */ }
  // ``now``: ping at once (a tab going hidden does not: a tab that closes says nothing more than its goodbye)
  const alive = (now = true) => {
    if (worker) worker.postMessage({ url, hidden: document.hidden, now });
    else if (now) fetch(url + (document.hidden ? "&hidden=1" : ""), { method: "POST" })
      .then((r) => { unreachable = !r.ok; }, () => { unreachable = true; });
  };
  alive();
  if (!worker) setInterval(alive, 10000);
  addEventListener("visibilitychange", () => alive(!document.hidden));
  addEventListener("pageshow", (e) => { if (e.persisted) { leaving = false; alive(); } });  // back from the history
  addEventListener("pagehide", () => navigator.sendBeacon(`/api/tab/bye?tab=${TAB}`));
  // The browser's own question (its words: a page cannot choose them)
  addEventListener("beforeunload", (e) => { if (!leaving && !unreachable) { e.preventDefault(); e.returnValue = ""; } });
});

const openDatabase = (key) => goTo(`/db/${encodeURIComponent(key)}/`);
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

// A file chosen in the page, sent to the server (data/uploads) for an import: resolves to its path there
function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/upload?name=${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => (xhr.status < 300 ? resolve(JSON.parse(xhr.responseText).path) : reject(new Error(xhr.responseText)));
    xhr.onerror = () => reject(new Error("The upload failed"));
    xhr.send(file);
  });
}

// A path on the server's computer: typed or pasted, or a file chosen here and uploaded
function PathInput({ value, onChange, accept, placeholder }) {
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);
  const input = useRef(null);
  function choose(e) {
    const file = e.target.files[0];
    if (!file) return;
    setError(null);
    setProgress(0);
    uploadFile(file, setProgress).then((path) => { setProgress(null); onChange(path); })
      .catch((err) => { setProgress(null); setError(err.message); });
  }
  return html`
    <div class="path-input">
      <input value=${value} placeholder=${placeholder} onInput=${(e) => onChange(e.target.value.trim().replace(/^["']|["']$/g, ""))} />
      <input type="file" ref=${input} accept=${accept} style="display:none" onChange=${choose} />
      <button type="button" disabled=${progress !== null} onClick=${() => input.current.click()}
        title="Choose the file here: it is copied to the app's data folder for the import, then deleted">
        ${progress !== null ? `Uploading ${Math.round(progress * 100)} %` : "Choose file…"}</button>
    </div>
    ${error && html`<div class="error">${error}</div>`}`;
}

const ECOSPOLD_NAME = (path) => {
  // "ecoinvent 3.9.1_cutoff_ecoSpold02.7z" -> "ecoinvent-3.9.1-cutoff"
  const name = path.split(/[\\/]/).filter(Boolean).pop() || "";
  const version = name.match(/\d+\.\d+(\.\d+)?/)?.[0];
  const model = ["cutoff", "apos", "consequential", "en15804"].find((m) => name.toLowerCase().replace(/[-_ ]/g, "").includes(m));
  return version ? `ecoinvent-${version}${model ? `-${model}` : ""}` : name.replace(/\.(7z|csv)$/i, "");
};

// An ecoinvent release downloaded with the user's account, into the project. The password goes
// to the server once (checked with ecoinvent, then kept in the system's credential store, or in
// the server's memory only); the page only ever gets the user name back. ``biosphere``: a
// database of the project to link to, or null for the release's own biosphere and methods.
function EcoinventForm({ folder, project, existing, biosphere, unlinked, onJob, actions, head }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [checking, setChecking] = useState(false);
  const [versions, setVersions] = useState(null);
  const [version, setVersion] = useState("");
  const [models, setModels] = useState(null);
  const [model, setModel] = useState("cutoff");
  useEffect(() => { serverRequest("/api/ecoinvent").then(setStatus).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    if (!status?.username || versions) return;
    serverRequest("/api/ecoinvent/releases").then((r) => { setVersions(r.versions); setVersion(r.versions[0] || ""); })
      .catch((e) => setError(e.message));
  }, [status?.username]);
  useEffect(() => {
    setModels(null);
    if (!version) return;
    serverRequest(`/api/ecoinvent/releases?version=${encodeURIComponent(version)}`)
      .then((r) => { setModels(r.system_models); if (!r.system_models.includes(model)) setModel(r.system_models[0]); })
      .catch((e) => setError(e.message));
  }, [version]);

  function login(e) {
    e.preventDefault();
    setChecking(true);
    setError(null);
    serverRequest("/api/ecoinvent/login", { username, password, remember }).then((r) => {
      setPassword("");
      setVersions(r.versions);
      setVersion(r.versions[0] || "");
      setStatus(r);
    }).catch((err) => setError(err.message)).finally(() => setChecking(false));
  }
  const logout = () => serverRequest("/api/ecoinvent/logout", {}).then((s) => { setStatus(s); setVersions(null); setVersion(""); })
    .catch((e) => setError(e.message));

  if (!status) return html`${head()}${error ? html`<div class="error">${error}</div>` : html`<${Waiting} text="Checking the account…" />`}`;
  if (!status.installed) return html`
    ${head()}
    <p>The download needs <code>ecoinvent_interface</code> and <code>keyring</code> in the Python environment running
      lca_viewer: run <code>pip install ecoinvent_interface keyring</code> in it, then restart the app.</p>
    ${actions(null)}`;
  if (!status.username) return html`
    ${head(html`<${InfoTip} text="Your ecoquery.ecoinvent.org account, with a licence for the releases." label="About the account" />`)}
    <form class="source-form" onSubmit=${login}>
      <label>User name <input value=${username} onInput=${(e) => setUsername(e.target.value)} autocomplete="username" autofocus /></label>
      <label>Password <input type="password" value=${password} onInput=${(e) => setPassword(e.target.value)} autocomplete="current-password" /></label>
      <label class="check"><input type="checkbox" checked=${remember} disabled=${!status.store} onChange=${(e) => setRemember(e.target.checked)} />
        ${status.store ? `Remember on this computer, in ${status.store}` : "Remember: no secure credential store on this computer, kept until the app stops"}</label>
      ${error && html`<div class="error">${error}</div>`}
      ${actions(checking ? "Checking with ecoinvent…" : "Log in", checking || (!username.trim() || !password) && "Give your user name and password")}
    </form>`;
  const database = `ecoinvent-${version}-${model}`;
  const taken = version && existing.includes(database);
  return html`
    ${head(html`<${InfoTip} text=${`Downloads the release (about 1 GB, kept for next time) and imports it as ${version ? database : "ecoinvent-<version>-<model>"} (10 to 20 minutes).`} label="About the download" />`)}
    <form class="source-form" onSubmit=${(e) => { e.preventDefault(); onJob("/api/ecoinvent/import", { folder, project, version, system_model: model, biosphere }); }}>
      <p class="muted small">Account <b>${status.username}</b> ${status.remembered ? `(remembered in ${status.store})` : "(until the app stops)"} ·
        <button type="button" class="link" onClick=${logout}>${status.remembered ? "Forget it" : "Log out"}</button></p>
      <label>Version
        <select value=${version} onChange=${(e) => setVersion(e.target.value)} disabled=${!versions}>
          ${!versions && html`<option>Asking ecoinvent…</option>`}
          ${(versions || []).map((v) => html`<option value=${v}>${v}</option>`)}
        </select></label>
      <label>System model
        <select value=${model} onChange=${(e) => setModel(e.target.value)} disabled=${!models}>
          ${(models || [model]).map((m) => html`<option value=${m}>${m}</option>`)}
        </select></label>
      ${taken && html`<div class="error">${project} already has ${database}.</div>`}
      ${error && html`<div class="error">${error}</div>`}
      ${actions("Download and import", !versions || !models ? "Asking ecoinvent for its releases…" : !version ? "No release for this account"
        : taken || unlinked)}
    </form>`;
}

// The default biosphere and methods for an ecoinvent version named in a file or database name
// ("ecoinvent 3.10.1_cutoff…" -> "3.10"): the same version, else the same major.minor; with 3.9,
// biosphere3 when the project has it already (bundled with Brightway, nothing to download)
function defaultsFor(text, versions, hasBundled) {
  const found = (text || "").match(/\d+\.\d+(\.\d+)?/)?.[0];
  if (!found) return null;
  const minor = (v) => v.split(".").slice(0, 2).join(".");
  if (minor(found) === "3.9" && hasBundled) return "bundled";
  return versions.find((v) => v === found) || versions.find((v) => minor(v) === minor(found)) || null;
}
const defaultsLabel = (v) => (v === "bundled" ? "ecoinvent 3.9, bundled with Brightway (biosphere3, no download)" : `ecoinvent ${v}`);

const DATABASE_SOURCES = [
  { kind: "ecospold2", label: "ecoSpold2", hint: "An ecoinvent release (.7z) or its unpacked folder" },
  { kind: "simapro", label: "SimaPro CSV", hint: "A process export, such as Agribalyse" },
  { kind: "bafu", label: "BAFU", hint: "The Swiss LCI database, from files.brightway.dev" },
  { kind: "ecoinvent", label: "ecoinvent", hint: "Downloaded with your ecoinvent account" },
  { kind: "repository", label: "Brightway repository", hint: "Biosphere and impact methods, from files.brightway.dev" },
  { kind: "project", label: "Another project", hint: "A database of another project, or a copy of one of this project" },
];

// The forms of the Brightway projects section: a new project (empty, or from a backup such as
// BAFU on files.brightway.dev), a database added to a project (ecoSpold2, SimaPro CSV, BAFU, ecoinvent
// with the user's account, with or without the default biosphere and methods), a deletion.
// Each starts a job.
// Impact methods to choose from (``methods``: [{ name, unit }]), nested by family, with a filter. ``chosen``: a Set of
// their keys (``methodKey``).
const methodKey = (name) => name.join("\u001f");
function MethodPicker({ methods, chosen, setChosen }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(() => new Set());
  const families = useMemo(() => {
    const map = new Map();
    methods.forEach((m) => map.set(familyKey(m), [...(map.get(familyKey(m)) || []), m]));
    return [...map];
  }, [methods]);
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  const matches = (m) => !words.length || words.every((w) => m.name.join(" ").toLowerCase().includes(w));
  const tick = (list, on) => {
    const next = new Set(chosen);
    list.forEach((m) => (on ? next.add(methodKey(m.name)) : next.delete(methodKey(m.name))));
    setChosen(next);
  };
  const toggleOpen = (family) => setOpen((o) => { const next = new Set(o); next.has(family) ? next.delete(family) : next.add(family); return next; });
  return html`
    <input class="method-filter" placeholder="Filter the methods" value=${text} onInput=${(e) => setText(e.target.value)} aria-label="Filter the methods" />
    <div class="method-tree">
      ${families.map(([family, list]) => {
        const shown = list.filter(matches);
        if (!shown.length) return null;
        const expanded = open.has(family) || words.length > 0;
        const n = shown.filter((m) => chosen.has(methodKey(m.name))).length;
        return html`
          <div class="method-family" key=${family}>
            <div class="method-head">
              <button type="button" class="fold-caret" onClick=${() => toggleOpen(family)} aria-expanded=${expanded} aria-label=${`${expanded ? "Fold" : "Unfold"} ${family}`}>${expanded ? "▾" : "▸"}</button>
              <input type="checkbox" checked=${n === shown.length} ref=${(el) => el && (el.indeterminate = n > 0 && n < shown.length)}
                onChange=${(e) => tick(shown, e.target.checked)} aria-label=${`All of ${family}`} title="The whole family" />
              <span class="name" onClick=${() => toggleOpen(family)}>${family}</span>
              <span class="muted count">${n}/${shown.length}</span>
            </div>
            ${expanded && shown.map((m) => html`
              <div class="method-row" key=${methodKey(m.name)}>
                <input type="checkbox" checked=${chosen.has(methodKey(m.name))} onChange=${(e) => tick([m], e.target.checked)}
                  aria-label=${m.name.join(" | ")} />
                <span class="name" title=${`${m.name.join(" | ")}${m.unit ? ` (${m.unit})` : ""}${m.library ? `\nMethods library: ${m.matched} of ${m.factors} factors on flows of the project` : ""}`} onClick=${() => tick([m], !chosen.has(methodKey(m.name)))}>${methodParts(m).main}${methodParts(m).qual && html` <span class="muted">${methodParts(m).qual}</span>`}</span>
              </div>`)}
          </div>`;
      })}
    </div>`;
}

// The impact methods a database is built with: one list per project (its cache), those of its previous builds, else the
// family of the default method (EF v3.1 when the project has it). The impacts of each method are computed with the
// build: the fewer, the lighter.
function BuildForm({ form, folder, onJob, actions }) {
  const [listing, setListing] = useState(null);
  const [error, setError] = useState(null);
  const [chosen, setChosen] = useState(() => new Set());
  useEffect(() => {
    const q = new URLSearchParams({ folder, project: form.project, database: form.database });
    serverRequest(`/api/brightway/methods?${q}`).then((r) => {
      setListing({ ...r, methods: [...r.methods, ...(r.library || [])] });  // the methods library's after the project's
      setChosen(new Set((r.selected || r.default).map(methodKey)));
    }).catch((e) => setError(e.message));
  }, []);
  const names = listing ? listing.methods.filter((m) => chosen.has(methodKey(m.name))).map((m) => m.name) : [];
  const submit = (e) => {
    e.preventDefault();
    onJob("/api/load", { brightway: folder, project: form.project, database: form.database, rebuild: !!form.rebuild, methods: names });
  };
  const verb = form.rebuild ? "Rebuild" : "Build";
  return html`
    <form class="project-form" onSubmit=${submit}>
      <h4>${verb} ${form.database}: impact methods <${InfoTip} label="About the impact methods"
        text="The impacts of the methods ticked are computed with the build, for every process: the fewer, the faster and the lighter (a large database with hundreds of methods takes gigabytes). The project has one list for all its databases: methods unticked are dropped from all of them (no computing), methods ticked are computed for all of them. More can be added later, in the Methods panel of the open database: only their impacts are computed then. The methods of the Methods library (below the projects) follow the project's, under the source 'library'." /></h4>
      ${error && html`<div class="error">${error}</div>`}
      ${!listing ? !error && html`<${Waiting} text="Reading the impact methods of the project…" />` : html`
        <div class="row">
          <button type="button" onClick=${() => setChosen(new Set(listing.methods.map((m) => methodKey(m.name))))}>All ${listing.methods.length}</button>
          <button type="button" onClick=${() => setChosen(new Set())}>None</button>
          <span class="muted small">${listing.selected ? "Ticked: those of the project's databases built so far" : "Ticked: the family of the default method"}</span>
        </div>
        <${MethodPicker} methods=${listing.methods} chosen=${chosen} setChosen=${setChosen} />`}
      ${actions(`${verb} and open with ${names.length} ${names.length === 1 ? "method" : "methods"}`,
        !listing ? true : !names.length && "Choose at least one impact method")}
    </form>`;
}

function ProjectForm({ form, folder, projects, onJob, onCancel, onForm, busy }) {
  const project = projects?.projects.find((p) => p.name === form.project);
  const flowDbs = (project?.databases || []).filter((d) => !d.processes).map((d) => d.name);
  const processDbs = (project?.databases || []).filter((d) => d.processes).map((d) => d.name);
  const [name, setName] = useState("");
  const [origin, setOrigin] = useState("empty");  // of a new project: "empty", "remote" or "file" (a backup)
  const [defaults, setDefaults] = useState(!flowDbs.length);  // the default biosphere and impact methods
  const [path, setPath] = useState("");
  const [database, setDatabase] = useState("");
  const [biosphere, setBiosphere] = useState(flowDbs.includes("biosphere3") || !flowDbs.length ? "biosphere3" : flowDbs[0]);
  const [linkTo, setLinkTo] = useState(processDbs.filter((d) => /ecoinvent/i.test(d)).slice(0, 1));
  const [adapters, setAdapters] = useState(null);
  const [chosen, setChosen] = useState(null);  // null: the defaults for linkTo
  const [remote, setRemote] = useState(null);
  const [remoteName, setRemoteName] = useState("BAFU-2025-v2");
  const [confirm, setConfirm] = useState("");
  const [copyFrom, setCopyFrom] = useState(null);  // a database of a project to copy: "project\ndatabase"
  const [copyName, setCopyName] = useState(null);  // the name of the copy; null: the suggested one
  const [versions, setVersions] = useState(["3.12", "3.11", "3.10", "3.9.1", "3.8"]);  // until the server answers
  const [version, setVersion] = useState(null);  // of the default biosphere and methods; null: the detected one
  const source = form.kind === "add" ? form.source : null;
  useEffect(() => {
    if (source === "ecospold2" || source === "simapro" || source === "repository") serverRequest("/api/brightway/defaults").then((r) => setVersions(r.versions)).catch(() => {});
  }, [source]);
  useEffect(() => { if (path && !database) setDatabase(ECOSPOLD_NAME(path)); }, [path]);
  useEffect(() => {
    if (source === "simapro") {
      serverRequest(`/api/adapters?${linkTo.map((d) => `link_to=${encodeURIComponent(d)}`).join("&")}`).then(setAdapters).catch(() => setAdapters({ adapters: [], default: [] }));
    }
  }, [source, linkTo.join("|")]);
  useEffect(() => { if (origin === "remote" && !remote) serverRequest("/api/brightway/remote").then(setRemote).catch(() => setRemote([])); }, [origin]);
  const migrations = chosen ?? adapters?.default ?? [];
  // Adapter files of the user's (randonneur datapackages, JSON), added next to randonneur_data's: { name, path, transformations }
  const [custom, setCustom] = useState([]);
  const [customPath, setCustomPath] = useState("");
  const [customError, setCustomError] = useState(null);
  const [customReading, setCustomReading] = useState(false);
  const addCustom = () => {
    setCustomReading(true);
    serverRequest(`/api/adapter-file?path=${encodeURIComponent(customPath)}`)
      .then((found) => { setCustom((list) => (list.some((c) => c.path === found.path) ? list : [...list, found])); setCustomPath(""); setCustomError(null); })
      .catch((e) => setCustomError(e.message)).finally(() => setCustomReading(false));
  };
  const adapterList = [...migrations, ...custom.map((c) => c.path)];  // what the import applies, in this order
  const toggle = (list, item) => (list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);
  const submit = (url, body) => (e) => { e.preventDefault(); onJob(url, body); };
  // The buttons of a form; ``disabled``: false, or why the button is greyed out (true: no reason
  // to show, the form says it already)
  const actions = (label, disabled, danger) => html`
    <div class="form-actions">
      ${label && html`<button type="submit" class=${danger ? "danger" : "primary"} disabled=${!!disabled || busy}>${label}</button>`}
      <button type="button" onClick=${onCancel}>Cancel</button>
      ${label && (busy ? html`<span class="muted small">Wait for the job under way to end</span>`
        : typeof disabled === "string" && html`<span class="muted small">${disabled}</span>`)}
    </div>`;

  if (form.kind === "new") return html`
    <form class="project-form" onSubmit=${origin === "empty" ? submit("/api/brightway/projects", { folder, name: name.trim() })
      : submit("/api/brightway/restore", origin === "remote" ? { folder, remote: remoteName, name: name.trim() || null }
        : { folder, file: path, name: name.trim() || null })}>
      <h4>New Brightway project</h4>
      <label>Name <input value=${name} onInput=${(e) => setName(e.target.value)} autofocus
        placeholder=${origin === "empty" ? "Name" : "Name in the backup"} /></label>
      <fieldset>
        <legend>Start from</legend>
        <label class="check"><input type="radio" checked=${origin === "empty"} onChange=${() => setOrigin("empty")} />
          An empty project, to add databases to: ecoSpold2, SimaPro CSV, or ecoinvent with your account</label>
        <label class="check"><input type="radio" checked=${origin === "remote"} onChange=${() => setOrigin("remote")} />
          A Brightway backup from files.brightway.dev</label>
        ${origin === "remote" && html`
          <select value=${remoteName} onChange=${(e) => setRemoteName(e.target.value)} aria-label="Project to download">
            ${(remote || [{ name: "BAFU-2025-v2" }]).map((r) => html`<option value=${r.name}>${r.name}${r.name.startsWith("BAFU") ? " — Swiss LCI database (BAFU/UVEK)" : ""}</option>`)}
          </select>`}
        <label class="check"><input type="radio" checked=${origin === "file"} onChange=${() => setOrigin("file")} />
          A Brightway backup file (.tar.gz) you downloaded or made</label>
        ${origin === "file" && html`<${PathInput} value=${path} onChange=${setPath} accept=".gz,.tar.gz" placeholder="Path of the .tar.gz file" />`}
      </fieldset>
      ${actions(origin === "remote" ? "Download and create" : "Create project", origin === "empty" ? !name.trim() && "Give the project a name"
        : origin === "file" && !path && "Give the backup file")}
    </form>`;

  if (form.kind === "add") {
    const hasBundled = flowDbs.includes("biosphere3") && project?.methods > 0;
    const detectedFrom = source === "ecospold2" ? database || path : linkTo.find((d) => defaultsFor(d, versions, hasBundled));
    const detected = defaultsFor(detectedFrom, versions, hasBundled);
    const chosenVersion = version ?? detected ?? "";
    const already = chosenVersion === "bundled" ? hasBundled : flowDbs.includes(`ecoinvent-${chosenVersion}-biosphere`);
    // A biosphere for the elementary flows to link to (an ecoinvent download brings its own)
    const linked = defaults ? source === "ecoinvent" || !!chosenVersion : flowDbs.length > 0;
    const unlinked = !linked && (defaults ? "Choose the ecoinvent version of the biosphere above" : "Tick the box above to add a biosphere");
    const minor = (v) => (v === "bundled" ? "3.9" : v.split(".").slice(0, 2).join("."));
    const head = (tip = null) => html`
      <h4>Add a database to ${form.project} ${tip}</h4>
      <div class="source-choice" role="radiogroup" aria-label="Source of the database">
        ${DATABASE_SOURCES.map((s) => html`
          <button type="button" role="radio" aria-checked=${source === s.kind} class=${source === s.kind ? "selected" : ""} title=${s.hint}
            onClick=${() => onForm({ kind: "add", project: form.project, source: s.kind })}>
            <b>${s.label}</b></button>`)}
      </div>
      ${source && !["repository", "bafu", "project"].includes(source) && html`
        <label class="check"><input type="checkbox" checked=${defaults} onChange=${(e) => setDefaults(e.target.checked)} />
          <span>Add the default biosphere and impact methods ${source === "ecoinvent"
            && html`<${InfoTip} text="Those of the release: its elementary flows and impact methods." label="About the defaults" />`}</span></label>
        ${defaults && source !== "ecoinvent" && html`
          <label class="defaults-version">Of
            <select value=${chosenVersion} onChange=${(e) => setVersion(e.target.value)}>
              ${!chosenVersion && html`<option value="">Choose the ecoinvent version…</option>`}
              ${versions.map((v) => html`<option value=${v}>${defaultsLabel(v)}${v === detected ? " (detected)" : ""}</option>`)}
              <option value="bundled">${defaultsLabel("bundled")}${detected === "bundled" ? " (detected)" : ""}</option>
            </select>
            <${InfoTip} label="About the version" text=${`${!chosenVersion
              ? (source === "ecospold2" ? "Not found in the file name: choose the version of the release."
                : "No ecoinvent background ticked below: choose the ecoinvent version the export was made with.")
              : already ? "Already in this project: nothing to add."
                : chosenVersion === "bundled" ? "A few minutes, once per project."
                  : `ecoinvent's elementary flows and impact methods of ${chosenVersion}, from files.brightway.dev (about 20 MB, 3 minutes), once per project.`}${version === null && detected ? ` Detected from ${source === "ecospold2" ? "the file name" : detectedFrom}.` : ""}`} />
          </label>`}
        ${!defaults && (flowDbs.length ? html`
          <label>Elementary flows from
            <select value=${biosphere} onChange=${(e) => setBiosphere(e.target.value)}>
              ${flowDbs.map((d) => html`<option value=${d}>${d}</option>`)}
            </select></label>`
          : html`<p class="note">This project has no biosphere database yet: tick the box above to add one.</p>`)}`}`;
    if (!source) return html`<div class="project-form">${head()}${actions(null)}</div>`;
    if (source === "project") {
      const choices = (projects?.projects || []).filter((q) => q.twofive).flatMap((q) => q.databases.filter((d) => d.processes)
        .map((d) => ({ key: `${q.name}\n${d.name}`, project: q.name, database: d.name, depends: d.depends || [] })));
      const picked = choices.find((c) => c.key === copyFrom) || choices[0];
      const names = (project?.databases || []).map((d) => d.name);
      const suggestion = picked ? (picked.project === form.project ? `${picked.database} copy` : picked.database) : "";
      const target = (copyName ?? suggestion).trim();
      const missing = picked ? picked.depends.filter((d) => d !== picked.database && !names.includes(d)) : [];
      const taken = names.includes(target);
      return html`
        <div class="project-form">${head(html`<${InfoTip} text="Its processes and exchanges are copied, linked to the same databases of this project. About a minute for ecoinvent." label="About the copy" />`)}
          <form class="source-form" onSubmit=${submit("/api/brightway/databases/copy", { folder, project: form.project,
            database: picked?.database, new_name: target, from_project: picked?.project })}>
            <label>Database to copy
              <select value=${picked?.key} onChange=${(e) => { setCopyFrom(e.target.value); setCopyName(null); }}>
                ${choices.map((c) => html`<option value=${c.key}>${c.database} · project ${c.project}${c.project === form.project ? " (this one)" : ""}</option>`)}
              </select></label>
            <label>Name of the copy <input value=${copyName ?? suggestion} onInput=${(e) => setCopyName(e.target.value)} /></label>
            ${taken && html`<div class="error">${form.project} already has ${target}.</div>`}
            ${missing.length > 0 && html`<div class="error">${form.project} has no ${missing.join(", ")}, which ${picked.database} links to: add
              ${missing.length === 1 ? "it" : "them"} to this project first (the biosphere and impact methods: <i>Brightway repository</i>).</div>`}
            ${actions("Copy the database", !picked ? "No database to copy" : !target ? "Give the copy a name" : taken || missing.length > 0)}
          </form>
        </div>`;
    }
    if (source === "repository") {
      const repoVersion = version ?? versions[0] ?? "";
      const here = repoVersion === "bundled" ? hasBundled : flowDbs.includes(`ecoinvent-${repoVersion}-biosphere`);
      return html`
        <div class="project-form">${head(html`<${InfoTip} label="About the repository" text=${`${here ? "Its biosphere is already in this project: only the missing impact methods are added."
              : repoVersion === "bundled" ? "A few minutes, offline."
                : `ecoinvent's elementary flows and impact methods of ${repoVersion}, from files.brightway.dev (about 20 MB, 3 minutes).`} Databases imported afterwards can link to them.`} />`)}
          <form class="source-form" onSubmit=${submit("/api/brightway/defaults/install", { folder, project: form.project, version: repoVersion })}>
            <label>Biosphere and impact methods of
              <select value=${repoVersion} onChange=${(e) => setVersion(e.target.value)}>
                ${versions.map((v) => html`<option value=${v}>${defaultsLabel(v)}</option>`)}
                <option value="bundled">${defaultsLabel("bundled")}</option>
              </select></label>
            ${actions("Add from the repository", !repoVersion ? "Choose a version" : here && repoVersion === "bundled" && "Already in this project")}
          </form>
        </div>`;
    }
    if (source === "bafu") {
      const here = processDbs.includes(remoteName);
      const otherMethods = project?.methods > 0;
      return html`
        <div class="project-form">${head(html`<${InfoTip} label="About BAFU" text=${here ? `${remoteName} is already in this project.`
              : `${remoteName}: the Swiss LCI database of BAFU/UVEK (11,747 processes), with its elementary flows and the 21 Ecological Scarcity 2021 methods, from files.brightway.dev (about 60 MB).`} />`)}
          <form class="source-form" onSubmit=${submit("/api/brightway/remote/add", { folder, project: form.project, remote: remoteName })}>
            ${!here && otherMethods && html`<p class="note">BAFU has its own elementary flows: the methods already in this project
              score nothing on it, and its methods score nothing on the other databases.</p>`}
            ${actions("Add BAFU", here && `${remoteName} is already in this project`)}
          </form>
        </div>`;
    }
    if (source === "ecoinvent") return html`
      <div class="project-form">
        <${EcoinventForm} head=${head} folder=${folder} project=${form.project} existing=${processDbs} biosphere=${defaults ? null : biosphere}
          unlinked=${unlinked} onJob=${onJob} actions=${actions} />
      </div>`;
    if (source === "ecospold2") return html`
      <div class="project-form">${head(html`<${InfoTip} text="About 10 minutes for ecoinvent; an archive is unpacked first (2.6 GB, deleted afterwards)." label="About the import" />`)}
        <form class="source-form" onSubmit=${submit("/api/brightway/import", { folder, project: form.project, format: "ecospold2", path, database,
          biosphere, defaults: defaults ? chosenVersion : null })}>
          <label>ecoSpold2 release: the .7z archive, or the folder it was unpacked to
            <${PathInput} value=${path} onChange=${setPath} accept=".7z" placeholder="Path of the .7z or the folder" /></label>
          <label>Database name <input value=${database} onInput=${(e) => setDatabase(e.target.value)} placeholder="Database name" /></label>
          ${defaults && chosenVersion && (database.match(/\d+\.\d+/)?.[0] ?? minor(chosenVersion)) !== minor(chosenVersion) && html`
            <p class="note">The release is not ecoinvent ${minor(chosenVersion)}: with that biosphere, some elementary flows may
              not link and some impact factors may not match.</p>`}
          ${actions("Import", !path ? "Give the release first" : !database.trim() ? "Give the database a name" : unlinked)}
        </form>
      </div>`;
    return html`
      <div class="project-form">${head(html`<${InfoTip} text="Needs internet access (unit definitions). A large export takes a while: about 15 minutes for Agribalyse with its ecoinvent background." label="About the import" />`)}
        <form class="source-form" onSubmit=${submit("/api/brightway/import", {
          folder, project: form.project, format: "simapro-csv", path, database, biosphere, defaults: defaults ? chosenVersion : null,
          link_to: linkTo, migrations: adapterList,
          unlinked: form.unlinked || "fail" })}>
          <label>SimaPro CSV export (processes)
            <${PathInput} value=${path} onChange=${setPath} accept=".csv,.CSV" placeholder="Path of the CSV" /></label>
          <label>Database name <input value=${database} onInput=${(e) => setDatabase(e.target.value)} placeholder="Database name" /></label>
          <fieldset>
            <legend>Background databases its inputs link to</legend>
            ${processDbs.length ? processDbs.map((d) => html`
              <label class="check"><input type="checkbox" checked=${linkTo.includes(d)} onChange=${() => { setLinkTo(toggle(linkTo, d)); setChosen(null); }} /> ${d}</label>`)
              : html`<p class="muted small">None in this project: inputs from ecoinvent or another database will not link.</p>`}
          </fieldset>
          <details class="adapters" open=${adapterList.length > 0}>
            <summary>Adapters: ${adapterList.length ? [...migrations, ...custom.map((c) => c.name)].join(", ") : "none"}</summary>
            <${InfoTip} label="About adapters" text="Correspondence files (randonneur_data, PRé's SimaPro to ecoinvent names): they rename the inputs and flows to those of the background database before linking. Those of the databases chosen above are ticked." />
            <div class="adapter-list">
              ${(adapters?.adapters || []).map((a) => html`
                <label class="check" title=${a.description}><input type="checkbox" checked=${migrations.includes(a.name)}
                  onChange=${() => setChosen(toggle(migrations, a.name))} /> ${a.name}
                  <span class="muted small">${a.source} → ${a.target}</span></label>`)}
            </div>
            <h5>Your own adapter</h5>
            <${InfoTip} label="About your adapter" text="A file of your own in randonneur's format (a JSON datapackage of replace or update transformations with a source and a target). It is applied to the inputs left unlinked, after the others." />
            ${custom.map((c) => html`
              <div class="adapter-file" key=${c.path}>
                <span><b>${c.name}</b> <span class="muted small">${Object.entries(c.transformations).map(([v, n]) => `${n} ${v}`).join(", ") || "nothing to apply"}</span></span>
                <button type="button" class="link" aria-label="Remove" onClick=${() => setCustom(custom.filter((x) => x.path !== c.path))}>×</button>
              </div>`)}
            <div class="adapter-add">
              <${PathInput} value=${customPath} onChange=${(v) => { setCustomPath(v); setCustomError(null); }} accept=".json,application/json"
                placeholder="Path of an adapter file (.json)" />
              <button type="button" disabled=${!customPath || customReading} onClick=${addCustom}>${customReading ? "Reading it…" : "Add this adapter"}</button>
            </div>
            ${customError && html`<div class="error">${customError}</div>`}
          </details>
          <fieldset>
            <legend>If some inputs or flows can't be linked</legend>
            <label class="check"><input type="radio" checked=${form.unlinked !== "drop"} onChange=${() => onForm({ ...form, unlinked: "fail" })} />
              Stop and list them: nothing is written, to fix the links first</label>
            <label class="check"><input type="radio" checked=${form.unlinked === "drop"} onChange=${() => onForm({ ...form, unlinked: "drop" })} />
              Import without them, and list them</label>
          </fieldset>
          ${actions(form.unlinked === "drop" ? "Import without the unlinked exchanges" : "Import",
            !path ? "Give the CSV export first" : !database.trim() ? "Give the database a name" : unlinked)}
        </form>
      </div>`;
  }
  if (form.kind === "build") return html`<${BuildForm} form=${form} folder=${folder} onJob=${onJob} actions=${actions} />`;
  if (form.kind === "renameDb") {
    const users = (project?.databases || []).filter((d) => d.depends.includes(form.database)).map((d) => d.name);
    const taken = (project?.databases || []).some((d) => d.name === name.trim());
    return html`
      <form class="project-form" onSubmit=${submit("/api/brightway/databases/rename", { folder, project: form.project, database: form.database, new_name: name.trim() })}>
        <h4>Rename ${form.database} <${InfoTip} text="The links inside the database follow its name. A model already built from it stays in the app under the old name." label="About renaming" /></h4>
        <label>New name <input value=${name} onInput=${(e) => setName(e.target.value)} autofocus placeholder=${form.database} /></label>
        ${users.length > 0 && html`<div class="error">${`${users.join(", ")} ${users.length === 1 ? "links" : "link"} to it: it cannot be renamed while ${users.length === 1 ? "it does" : "they do"}.`}</div>`}
        ${taken && html`<div class="error">${form.project} already has ${name.trim()}.</div>`}
        ${actions("Rename database", !name.trim() || name.trim() === form.database || taken || users.length > 0)}
      </form>`;
  }
  if (form.kind === "renameProject" || form.kind === "copyProject") {
    const rename = form.kind === "renameProject";
    return html`
      <form class="project-form" onSubmit=${submit(`/api/brightway/projects/${rename ? "rename" : "copy"}`, { folder, project: form.project, new_name: name.trim() })}>
        <h4>${rename ? "Rename" : "Copy"} ${form.project} <${InfoTip} text=${rename ? "A model already built from it stays in the app under the old project name." : "Its databases and impact methods are copied: it can take a while."} label="About this" /></h4>
        <label>${rename ? "New name" : "Name of the copy"} <input value=${name} onInput=${(e) => setName(e.target.value)} autofocus placeholder=${form.project} /></label>
        ${actions(rename ? "Rename project" : "Copy project", !name.trim() || name.trim() === form.project)}
      </form>`;
  }
  if (form.kind === "removeDb") {
    const users = (project?.databases || []).filter((d) => d.depends.includes(form.database)).map((d) => d.name);
    return html`
      <form class="project-form danger" onSubmit=${submit("/api/brightway/databases/delete", { folder, project: form.project, database: form.database })}>
        <h4>Remove ${form.database} from ${form.project}</h4>
        <p>This deletes the database from the project, for Brightway and Activity Browser too. It cannot be undone.
          ${(project?.databases || []).find((d) => d.name === form.database)?.processes ? "The model already built from it stays in the app." : ""}</p>
        ${users.length > 0 && html`<div class="error">${`${users.join(", ")} ${users.length === 1 ? "links" : "link"} to it: remove ${users.length === 1 ? "it" : "them"} first.`}</div>`}
        ${actions("Remove database", users.length > 0, true)}
      </form>`;
  }
  if (form.kind === "graph") return project && html`<${DatabaseGraph} folder=${folder} project=${project} onClose=${onCancel} />`;
  if (form.kind === "delete") return html`
    <form class="project-form danger" onSubmit=${submit("/api/brightway/projects/delete", { folder, project: form.project, confirm })}>
      <h4>Delete the project ${form.project}</h4>
      <p>This deletes the project and its folder (${project?.databases.length || 0} databases), for Brightway and Activity
        Browser too. It cannot be undone. Models already built from it stay in the app.</p>
      <label>Type <b>${form.project}</b> to confirm <input value=${confirm} onInput=${(e) => setConfirm(e.target.value)} /></label>
      ${actions("Delete project", confirm !== form.project, true)}
    </form>`;
  return null;
}

// ---------------------------------------------------------------- database graph
const GRAPH_INFO = "An arrow goes from a database to one it uses. Changing a database rebuilds it and every database that uses it, "
  + "directly or not: editing ecoinvent rebuilds all the databases built on it. Databases that use each other are built "
  + "together. Lock the background databases you do not mean to change.";

// Strongly connected components (Tarjan): databases that use each other, directly or not. ``uses``: name -> [names];
// returns name -> the name standing for its component
function loopsOf(names, uses) {
  let index = 0;
  const at = {}, low = {}, on = new Set(), stack = [], comp = {};
  const visit = (v) => {
    at[v] = low[v] = index++; stack.push(v); on.add(v);
    for (const w of uses[v] || []) {
      if (!(w in at)) { visit(w); low[v] = Math.min(low[v], low[w]); } else if (on.has(w)) low[v] = Math.min(low[v], at[w]);
    }
    if (low[v] === at[v]) { let w; do { w = stack.pop(); on.delete(w); comp[w] = v; } while (w !== v); }
  };
  names.forEach((n) => { if (!(n in at)) visit(n); });
  return comp;
}

// The ECharts option of a project's database graph: the databases used by others at the bottom (the order they are built
// in). ``links``: [[database, database it uses, exchanges]] counted on the server, or null while they are counted
function graphOption(project, links) {
  const dbs = project.databases, names = dbs.map((d) => d.name), known = new Set(names);
  const counts = new Map((links || []).map(([o, i, n]) => [`${o}\n${i}`, n]));
  const edges = [];
  for (const d of dbs) for (const u of d.depends || []) if (known.has(u) && u !== d.name) edges.push({ from: d.name, to: u, listed: true });
  for (const [o, i] of links || []) {
    if (known.has(o) && known.has(i) && !edges.some((e) => e.from === o && e.to === i)) edges.push({ from: o, to: i, listed: false });
  }
  const uses = {};
  edges.forEach((e) => (uses[e.from] ||= []).push(e.to));
  const comp = loopsOf(names, uses);
  const size = {};
  names.forEach((n) => { size[comp[n]] = (size[comp[n]] || 0) + 1; });
  // Level of a component: 0 when it uses nothing, else one more than the highest one it uses
  const level = {};
  const levelOf = (c) => {
    if (c in level) return level[c];
    level[c] = 0;  // the components form a DAG: no cycle reaches back here
    let l = 0;
    for (const n of names) if (comp[n] === c) for (const u of uses[n] || []) if (comp[u] !== c) l = Math.max(l, levelOf(comp[u]) + 1);
    return (level[c] = l);
  };
  names.forEach((n) => levelOf(comp[n]));
  const top = Math.max(0, ...Object.values(level));
  const rows = {};
  names.forEach((n) => (rows[level[comp[n]]] ||= []).push(n));
  const text = cssVar("--text"), muted = cssVar("--muted"), accent = cssVar("--accent"), warn = cssVar("--bar-negative");
  const usedBy = (n) => edges.filter((e) => e.to === n).map((e) => e.from);
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const nodes = dbs.map((d) => {
    const row = rows[level[comp[d.name]]], i = row.indexOf(d.name);
    const state = d.model ? (d.model.key ? "open" : d.model.outdated ? "outdated" : "built") : d.processes ? "not built" : "";
    const together = size[comp[d.name]] > 1;
    const amount = d.processes ? `${d.processes.toLocaleString()} processes` : `${(d.flows || 0).toLocaleString()} flows`;
    return {
      name: d.name, x: (i - (row.length - 1) / 2) * 260, y: (top - level[comp[d.name]]) * 110,
      symbolSize: d.processes ? 16 + 6 * Math.log10(d.processes + 1) : 12,
      itemStyle: { color: !d.processes ? cssVar("--border") : state === "open" ? accent : state === "outdated" ? warn : state === "built" ? cssVar("--bar") : muted,
        borderColor: together ? warn : "transparent", borderWidth: together ? 3 : 0 },
      label: { show: true, position: "right", color: d.processes ? text : muted, fontSize: 12,
        formatter: `${d.name}${d.locked ? " (locked)" : ""}\n{m|${amount}${state ? ` · ${state}` : ""}}`, rich: { m: { color: muted, fontSize: 11 } } },
      tip: [`<b>${esc(d.name)}</b>`, d.processes ? amount : `biosphere: ${amount}`,
        state && `model: ${state}${d.model?.outdated ? ` (${esc(d.model.outdated)})` : ""}`, d.locked && "locked",
        together && "uses databases that use it: built together with them",
        (uses[d.name] || []).length && `uses: ${uses[d.name].map(esc).join(", ")}`,
        usedBy(d.name).length && `used by (rebuilt when it changes): ${usedBy(d.name).map(esc).join(", ")}`].filter(Boolean).join("<br>"),
    };
  });
  return {
    textStyle: { color: text, fontFamily: cssVar("--font") },
    tooltip: { trigger: "item", confine: true, formatter: (p) => p.dataType === "edge"
      ? `${esc(p.data.source)} uses ${esc(p.data.target)}${p.data.n != null ? `: ${p.data.n.toLocaleString()} exchanges` : ""}`
        + (p.data.listed ? "" : "<br>found in the exchanges, not listed in the project's metadata (depends)")
      : p.data.tip },
    series: [{
      type: "graph", layout: "none", roam: true, left: 40, right: 200, top: 30, bottom: 40, data: nodes,  // room for the labels edgeSymbol: ["none", "arrow"], edgeSymbolSize: 8,
      emphasis: { focus: "adjacency" },
      links: edges.map((e) => {
        const n = counts.get(`${e.from}\n${e.to}`);
        return { source: e.from, target: e.to, listed: e.listed, n,
          lineStyle: { color: muted, width: 1.2, type: e.listed ? "solid" : "dashed", curveness: 0.08 },
          label: { show: n != null, formatter: n != null ? n.toLocaleString() : "", color: muted, fontSize: 10 } };
      }),
    }],
  };
}

// The databases of a project and those each one uses (its ``depends``), opened from the project's options. The exchanges
// between them are counted on the server (seconds the first time), which also finds links ``depends`` does not list.
function DatabaseGraph({ folder, project, onClose }) {
  const [links, setLinks] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    serverRequest(`/api/brightway/links?folder=${encodeURIComponent(folder)}&project=${encodeURIComponent(project.name)}`)
      .then((r) => setLinks(r.links)).catch((e) => setError(e.message));
  }, [folder, project.name]);
  const option = useMemo(() => graphOption(project, links), [project, links]);
  const levels = new Set(option.series[0].data.map((n) => n.y)).size;
  const missing = links && option.series[0].links.some((l) => !l.listed);
  return html`
    <div class="project-form graph-form">
      <h4>Databases of ${project.name} <${InfoTip} label="About the databases graph" text=${GRAPH_INFO} /></h4>
      <p class="muted small">${!links && !error ? html`<span class="spinner" aria-hidden="true"></span> Counting the exchanges between databases (once; seconds for ecoinvent)… `
        : error ? html`<span class="error-inline">Exchanges not counted: ${error}</span> `
        : missing ? "Dashed: links found in the exchanges that the project's metadata does not list. " : "Numbers on the arrows: exchanges. "}
        Colours: open (accent), built, outdated (red), not built (grey); a red ring: built together. Scroll to zoom, drag to move.</p>
      <${Chart} option=${option} height=${Math.max(220, levels * 110 + 60)} />
      <div><button type="button" onClick=${onClose}>Close</button></div>
    </div>`;
}

// ---------------------------------------------------------------- storage
const fmtBytes = (n) => (n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : n >= 2 ** 20 ? `${Math.round(n / 2 ** 20)} MB` : n > 0 ? `${Math.max(1, Math.round(n / 2 ** 10))} kB` : "0");

// What the app and Brightway take on disk (/api/storage), and what can be deleted: the project caches (a segment per
// database, or per databases built together; its results, G, solved again at its next build), models made from files,
// caches of an older layout, what failed builds left in data/cache, downloads. Brightway projects are only measured:
// they are deleted with Delete project… in Projects.
function StorageDialog({ onClose }) {
  const folder = loadPreference("brightwayFolder", "");
  const [listing, setListing] = useState(null);
  const [error, setError] = useState(null);
  const [asking, setAsking] = useState(null);  // { kind, path, text }: a deletion waiting for its confirmation
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(null);  // what a rebuild under way says
  const read = () => serverRequest(`/api/storage${folder ? `?folder=${encodeURIComponent(folder)}` : ""}`).then(setListing).catch((e) => setError(e.message));
  useEffect(() => { read(); }, []);
  const remove = async ({ kind, path }) => {
    setBusy(true); setError(null);
    try { await serverRequest("/api/storage/delete", { kind, path }); await read(); } catch (e) { setError(e.message); }
    setAsking(null); setBusy(false);
  };
  // A segment read again from its project (built only, not opened); the segments above it are solved again
  const rebuild = async (s) => {
    setBusy(true); setError(null); setRunning(`Building ${s.title} again`);
    try {
      let job = await serverRequest("/api/load", { brightway: s.folder, project: s.project, database: s.databases[0], rebuild: true, open: false });
      while (job.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        job = await serverRequest(`/api/jobs/${job.id}`);
        setRunning(job.message);
      }
      if (job.status === "failed") throw new Error(job.error);
      await read();
    } catch (e) { setError(e.message); }
    setRunning(null); setBusy(false);
  };
  const ask = (kind, path, text) => setAsking({ kind, path, text });
  const confirmRow = (kind, path) => asking && asking.kind === kind && asking.path === path && html`
    <div class="storage-confirm">${asking.text} <button class="danger" disabled=${busy} onClick=${() => remove(asking)}>Delete</button>
      <button disabled=${busy} onClick=${() => setAsking(null)}>Cancel</button></div>`;
  const items = listing?.items || [];
  const of = (kind) => items.filter((i) => i.kind === kind);
  const projects = of("project"), models = of("model"), obsolete = of("obsolete"), leftovers = of("leftover"), downloads = of("download");
  const segments = Object.fromEntries(of("segment").map((s) => [s.path, s]));
  const sum = (list) => list.reduce((a, i) => a + i.size, 0);
  const anyOpen = [...projects, ...models, ...obsolete].some((i) => i.open);
  return html`
    <${Modal} title="Storage" className="storage-dialog" onClose=${onClose}>
      ${error && html`<div class="error">${error}</div>`}
      ${running && html`<${Waiting} text=${running} />`}
      ${!listing ? (!error && html`<${Waiting} text="Measuring the folders…" />`) : html`
        <p class="muted small">LCA Explorer: <b>${fmtBytes(listing.total)}</b> in <code>${listing.data}</code> and the download caches ·
          Brightway: <b>${fmtBytes(listing.brightway_total)}</b> in <code>${listing.brightway}</code>
          <${InfoTip} label="About storage" text="Each Brightway project has one cache, made of segments: one per database (databases that use each other are built together). A background database (ecoinvent) is stored once, and opening a database puts its segment and those it uses together. Results (G) are the cumulative impacts: dropped, they are solved again at the next build. A segment rebuilt is read again from its project, and the results of the segments that use it are solved again. Open databases must be closed first." /></p>
        <h3 class="load-head">Project caches <span class="muted small">${fmtBytes(sum(projects))}</span></h3>
        <table class="load-table storage-table">
          <tr><th>Database</th><th class="num">Size</th><th class="num">Results</th><th></th></tr>
          ${projects.map((p) => html`
            <tr key=${p.path} class=${"storage-project" + (p.orphan ? " muted" : "")}>
              <td title=${p.path}><b>${p.title}</b> <span class="muted small">· ${p.methods} impact methods · project files ${fmtBytes(p.files)}</span>
                ${p.orphan && html` <span class="small">· ${p.orphan}</span>`}</td>
              <td class="num">${fmtBytes(p.size)}</td><td></td>
              <td class="actions">
                <button class="link danger" disabled=${busy || p.open} title=${p.open ? "Close its databases first" : "Delete the cache of the project: its databases are built again when next opened"}
                  onClick=${() => ask("project", p.path, `Delete the cache of ${p.title} (${fmtBytes(p.size)})?`)}>Delete</button>
                ${confirmRow("project", p.path)}
              </td>
            </tr>
            ${p.segments.map((path) => segments[path]).filter(Boolean).map((s) => html`
              <tr key=${s.path} class=${"storage-segment" + (s.orphan ? " muted" : "")}>
                <td title=${s.path}>${s.title}${s.databases.length > 1 ? html` <span class="muted small" title="These databases use each other's products">· built together</span>` : ""}
                  ${s.open ? html` <span class="loc-chip">open</span>` : ""}
                  ${s.orphan && html` <span class="small" title="It cannot be built again from its project">· ${s.orphan}</span>`}</td>
                <td class="num">${fmtBytes(s.size)}</td><td class="num">${s.results ? fmtBytes(s.results) : "–"}</td>
                <td class="actions">
                  <button class="link" disabled=${busy || s.open || !!s.orphan} title=${s.open ? "Close it first" : "Read it again from its project; the results of the databases that use it are solved again"}
                    onClick=${() => rebuild(s)}>Rebuild</button>
                  <button class="link" disabled=${busy || !s.results} title="Delete its results (G): solved again at its next build"
                    onClick=${() => ask("results", s.path, `Drop the results of ${s.title} (${fmtBytes(s.results)})?`)}>Drop results</button>
                  <button class="link danger" disabled=${busy || s.open} title=${s.open ? "Close it first" : "Delete it: built again when a database that needs it is next opened"}
                    onClick=${() => ask("segment", s.path, `Delete ${s.title} (${fmtBytes(s.size)})?${s.users.length ? ` The databases that use it are deleted too: ${s.users.join("; ")}.` : ""}`)}>Delete</button>
                  ${confirmRow("results", s.path)}${confirmRow("segment", s.path)}
                </td>
              </tr>`)}`)}
        </table>
        ${models.length > 0 && html`
          <h3 class="load-head">Models made from files <span class="muted small">${fmtBytes(sum(models))}</span></h3>
          <table class="load-table storage-table">
            <tr><th>Model</th><th class="num">Size</th><th class="num">Results</th><th></th></tr>
            ${models.map((m) => html`
              <tr key=${m.path} class=${m.orphan ? "muted" : ""}>
                <td title=${m.path}>${m.title}${m.open ? html` <span class="loc-chip">open</span>` : ""}${m.orphan && html` <span class="small">· ${m.orphan}</span>`}</td>
                <td class="num">${fmtBytes(m.size)}</td><td class="num">${m.results ? fmtBytes(m.results) : "–"}</td>
                <td class="actions">
                  <button class="link" disabled=${busy || m.open || !m.results} title=${m.open ? "Close it first" : "Delete its results (G): computed again when it is next opened"}
                    onClick=${() => ask("results", m.path, `Drop the results of ${m.title} (${fmtBytes(m.results)})?`)}>Drop results</button>
                  <button class="link danger" disabled=${busy || m.open} title=${m.open ? "Close it first" : "Delete the model: it is made again from its files only"}
                    onClick=${() => ask("model", m.path, `Delete the model ${m.title} (${fmtBytes(m.size)})?`)}>Delete</button>
                  ${confirmRow("results", m.path)}${confirmRow("model", m.path)}
                </td>
              </tr>`)}
          </table>`}
        ${obsolete.length > 0 && html`
          <h3 class="load-head">Obsolete caches <span class="muted small">${fmtBytes(sum(obsolete))}</span>
            <${InfoTip} label="About obsolete caches" text="Models of one database and all it uses, built before the project cache, and caches of another version of LCA Explorer. They are not opened any more: their databases are built again in the project cache." /></h3>
          <table class="load-table storage-table">
            ${obsolete.map((o) => html`<tr key=${o.path}><td title=${o.path}>${o.title}${o.open ? html` <span class="loc-chip">open</span>` : ""}</td>
              <td class="muted small">${o.reason}</td><td class="num">${fmtBytes(o.size)}</td></tr>`)}
          </table>
          <div class="storage-bulk"><button class="link danger" disabled=${busy || obsolete.some((o) => o.open)}
            onClick=${() => ask("obsolete", null, `Delete the ${obsolete.length} obsolete caches (${fmtBytes(sum(obsolete))})?`)}>Delete all</button>
            ${confirmRow("obsolete", null)}</div>`}
        ${leftovers.length > 0 && html`
          <h3 class="load-head">Left-overs <span class="muted small">${fmtBytes(sum(leftovers))}</span></h3>
          <table class="load-table storage-table">
            ${leftovers.map((l) => html`<tr key=${l.path}><td title=${l.path}>${l.title}${l.open ? html` <span class="loc-chip">open</span>` : ""}</td><td class="muted">${l.reason}</td>
              <td class="num">${fmtBytes(l.size)}</td>
              <td class="actions"><button class="link danger" disabled=${busy || l.open} title=${l.open ? "An open database still reads it" : ""}
                onClick=${() => ask("leftover", l.path, `Delete ${l.title} (${fmtBytes(l.size)})?`)}>Delete</button>
                ${confirmRow("leftover", l.path)}</td></tr>`)}
          </table>
          ${leftovers.some((l) => l.bulk) && html`<div class="storage-bulk"><button class="link danger" disabled=${busy}
            onClick=${() => ask("leftovers", null, `Delete what unfinished builds and rebuilds left (${fmtBytes(sum(leftovers.filter((l) => l.bulk)))})?`)}>Delete what builds left</button>
            ${confirmRow("leftovers", null)}</div>`}`}
        ${(projects.length > 0 || models.length > 0 || obsolete.length > 0) && html`<div class="storage-bulk">
          <button class="link danger" disabled=${busy || anyOpen} title=${anyOpen ? "Close the open databases first" : "Delete every cache and model: each database is built again when next opened"}
            onClick=${() => ask("all", null, `Delete all the caches and models (${fmtBytes(sum([...projects, ...models, ...obsolete, ...leftovers.filter((l) => l.bulk)]))})?`)}>Clear all app caches…</button>
          ${confirmRow("all", null)}</div>`}
        ${downloads.length > 0 && html`
          <h3 class="load-head">Downloads</h3>
          <table class="load-table storage-table">
            ${downloads.map((d) => html`<tr key=${d.path}><td title=${d.path}>${d.title}</td><td class="num">${fmtBytes(d.size)}</td>
              <td class="actions"><button class="link danger" disabled=${busy || !d.size} title="Downloaded again when needed"
                onClick=${() => ask("download", d.path, `Delete ${d.title} (${fmtBytes(d.size)})? Downloaded again when needed.`)}>Delete</button>
                ${confirmRow("download", d.path)}</td></tr>`)}
          </table>`}
        <h3 class="load-head">Brightway projects <span class="muted small">${fmtBytes(sum(listing.projects))}</span>
          <${InfoTip} label="About Brightway projects" text="The projects themselves (their databases, impact methods, search index, backups), for Activity Browser too. Delete one with Delete project… in Projects." /></h3>
        <table class="load-table storage-table">
          ${[...listing.projects].sort((a, b) => b.size - a.size).map((p) => html`<tr key=${p.path}><td title=${p.path}>${p.name}${p.twofive ? "" : html` <span class="muted small">· Brightway 2</span>`}</td>
            <td class="num">${fmtBytes(p.size)}</td></tr>`)}
        </table>`}
    <//>`;
}

// What the Projects dialog last listed, shown at once when it opens again while it is read again
// (the server keeps the projects too: reading them takes seconds): /api/sources, and the projects
// of each Brightway data folder
const projectsCache = { sources: null, folders: {} };
const elapsed = (s) => (s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`);

// Projects: the databases already built open in seconds; a database of a Brightway project is first built
// into its project's cache (data/cache/project-<hash>: once, the databases it uses with it, each kept for the
// other databases that use them). Loads run on the server, the dialog follows them and opens the database when ready.
// The Brightway projects can also be changed here (new, deleted, restored, databases imported).
// The Storage dialog's button, next to + New project in the Projects dialog
function StorageButton({ onClick }) {
  return html`<button class="icon-plain" onClick=${onClick} aria-label="Storage"
    title="Storage: what the project caches and Brightway projects take on disk, and what can be deleted"><${StorageIcon} /></button>`;
}

function LoadDatabase({ onClose, missing, onDatabases, onLocks }) {
  const [sources, setSources] = useState(projectsCache.sources);
  const [error, setError] = useState(null);
  const [job, setJob] = useState(null);
  const [folder, setFolder] = useState(() => loadPreference("brightwayFolder", ""));
  const [typed, setTyped] = useState(folder);
  const [projects, setProjects] = useState(() => projectsCache.folders[folder] || null);
  const [projectsError, setProjectsError] = useState(null);
  const [reading, setReading] = useState(false);  // the projects are being read again (the list shown stays)
  const asked = useRef(folder);  // the folder whose projects were asked for last
  const [showLog, setShowLog] = useState(false);
  const [form, setForm] = useState(null);  // { kind, project }: a ProjectForm open
  const [storageOpen, setStorageOpen] = useState(false);  // the Storage dialog, over this one
  const [jobAt, setJobAt] = useState(null);  // the project a job was started from: its progress shows there
  // The projects a database was opened from, the last one first; the ones whose databases are shown (all folded at first)
  const [recent, setRecent] = useState(() => { try { return JSON.parse(loadPreference("recentProjects", "[]")); } catch { return []; } });
  const [unfolded, setUnfolded] = useState(() => { try { return new Set(JSON.parse(loadPreference("unfoldedProjects", "[]"))); } catch { return new Set(); } });
  const noteProject = (name) => {
    const next = [name, ...recent.filter((n) => n !== name)].slice(0, 30);
    setRecent(next);
    savePreference("recentProjects", JSON.stringify(next));
  };
  // A project with a database open comes first, then the ones opened from last to first, then the others
  const rank = (q) => (q.databases.some((d) => d.model?.key) ? -1 : recent.indexOf(q.name) >= 0 ? recent.indexOf(q.name) : 1e9);
  const toggleProject = (name) => {
    const next = new Set(unfolded);
    next.has(name) ? next.delete(name) : next.add(name);
    setUnfolded(next);
    savePreference("unfoldedProjects", JSON.stringify([...next]));
  };
  const box = useRef(null);

  const refresh = () => serverRequest("/api/sources").then((s) => {
    projectsCache.sources = s;
    setSources(s);
    if (s.jobs.length) setJob((j) => j || s.jobs[0]);  // a load started before (dialog closed meanwhile)
    if (!folder && s.brightway.length) setFolder(s.brightway[0].path);
  }).catch((e) => setError(e.message));
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    if (onClose && !storageOpen) {  // Escape closes the Storage dialog first
      const onKey = (e) => e.key === "Escape" && onClose();
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }
  }, [onClose, storageOpen]);

  // The projects of a folder: those listed before are shown while they are read again. ``again``:
  // the server reads them again too (changes made outside the app), else it answers from what it keeps.
  function listProjects(path, again = false) {
    asked.current = path;
    setProjectsError(null);
    setProjects((shown) => (shown && shown === projectsCache.folders[path] ? shown : projectsCache.folders[path] || null));
    if (!path) return;
    setReading(true);
    serverRequest(`/api/brightway?folder=${encodeURIComponent(path)}${again ? "&refresh=true" : ""}`)
      .then((r) => {
        projectsCache.folders[path] = r;
        if (asked.current !== path) return;
        setProjects(r);
        savePreference("brightwayFolder", path);
      })
      .catch((e) => asked.current === path && setProjectsError(e.message))
      .finally(() => asked.current === path && setReading(false));
  }
  useEffect(() => { if (folder) listProjects(folder); }, [folder]);

  // Follow the job under way: open the database a load made ready; after a change to the
  // projects, list them again
  useEffect(() => {
    if (!job?.id || job.status !== "running") return;
    const timer = setTimeout(() => serverRequest(`/api/jobs/${job.id}`).then((j) => {
      setJob(j);
      if (j.status === "done" && j.key) openDatabase(j.key);
      else if (j.status === "done") {  // a new empty project: add its first database
        setForm(j.project ? { kind: "add", project: j.project } : null);
        listProjects(folder);
      }
    }).catch((e) => setJob({ ...job, status: "failed", error: e.message })), 700);
    return () => clearTimeout(timer);
  }, [job]);

  // Shown as under way at once, until the server answers with the job (its checks can take a moment)
  const startJob = (url, body, at = form?.project) => {
    setShowLog(false);
    setJobAt(at || null);
    setJob({ status: "running", message: "Starting…", log: [] });
    serverRequest(url, body).then(setJob).catch((e) => setJob({ status: "failed", error: e.message, log: [] }));
  };
  const start = (body) => { if (body.project) noteProject(body.project); startJob("/api/load", body, body.project); };
  const busy = job?.status === "running" || (job?.status === "done" && !!job.key);
  const unload = (key) => serverRequest("/api/unload", { key }).then((list) => {
    if (key === DATABASE) { goTo("/"); return; }
    onDatabases?.(list);
    refresh();
    listProjects(folder);
  }).catch((e) => setError(e.message));

  // A locked database cannot be edited, whichever database is open when one of its processes is reached
  const [locking, setLocking] = useState(null);  // the database being locked or unlocked (dbKey)
  const dbKey = (p, d) => `${p.name}\n${d.name}`;
  const lockDatabase = (p, d) => {
    setLocking(dbKey(p, d));
    serverRequest("/api/brightway/lock", { folder: projects.folder, project: p.name, database: d.name, locked: !d.locked })
      .then(() => { listProjects(folder); onLocks?.(); }).catch((e) => setError(e.message)).finally(() => setLocking(null));
  };
  // The lock of a database: shown when it is locked (a click unlocks it), else on the row's hover (a click locks it)
  const lockButton = (p, d) => html`
    <button class=${`lock-btn${d.locked ? " locked" : ""}`} disabled=${locking === dbKey(p, d)} onClick=${() => lockDatabase(p, d)}
      aria-label=${d.locked ? `Unlock ${d.name}` : `Lock ${d.name}`} aria-pressed=${!!d.locked}
      title=${d.locked ? "Locked: what this database contains cannot be edited, deleted or added to, from any database. Click to unlock it."
        : "Lock: nothing in this database can be edited, deleted or added to, from any database"}><${LockIcon} open=${!d.locked} /></button>`;
  // Why the row of a database cannot be opened (greyed out), or null
  const greyed = (p, d) => (!p.twofive ? "A database of a Brightway 2 project: upgrade the project to Brightway 2.5 (Activity Browser 3) to open it"
    : !d.processes ? "Elementary flows only (a biosphere database): nothing to open, the databases of this project link to its flows" : null);
  const known = sources?.brightway || [];
  const isKnown = known.some((d) => d.path === folder);
  const dbAction = (p, d) => {
    if (!p.twofive || !d.processes) return null;
    const body = { brightway: projects.folder, project: p.name, database: d.name };
    if (d.model?.key) return html`<button onClick=${() => { noteProject(p.name); openDatabase(d.model.key); }}>${d.model.key === DATABASE ? "Shown" : "Switch to"}</button>`;
    if (d.model && !d.model.outdated) return html`<button class="primary" disabled=${busy} onClick=${() => start(body)}>Open</button>`;
    return html`
      <button class="primary" disabled=${busy} onClick=${() => { noteProject(p.name); setForm({ kind: "build", project: p.name, database: d.name, rebuild: !!d.model }); }}
        title="Choose the impact methods, then build the model the app reads (about a minute for ecoinvent), kept for next time in data/cache">
        ${d.model ? "Rebuild and open…" : "Build and open…"}</button>`;
  };

  // The options of a database: close it, rename it, remove it (locked with its lock icon)
  const dbOptions = (p, d) => {
    const open = d.model?.key;
    const body = { brightway: projects.folder, project: p.name, database: d.name };
    return [
      ...(open ? [{ label: "Close (free its memory)", title: "Stop serving this database", run: () => unload(open) }] : []),
      ...(d.model?.outdated && !open && d.processes ? [{ label: "Open the old build", disabled: busy, title: "Open the model built before the database changed", run: () => start(body) }] : []),
      // A build up to date can still lack what newer versions record (e.g. the factors of the methods outside the model)
      ...(d.model && !d.model.outdated && !open && d.processes ? [{ label: "Rebuild and open", disabled: busy,
        title: "Build the model again (about a minute for ecoinvent): for what a newer version of the app records, as the method factors on flows outside the model",
        run: () => start({ ...body, rebuild: true }) }] : []),
      { label: "Rename…", disabled: busy || d.locked || !!open, title: d.locked ? "Unlock it first" : open ? "Close it first" : "Give the database another name", run: () => setForm({ kind: "renameDb", project: p.name, database: d.name }) },
      { label: "Remove…", danger: true, disabled: busy || d.locked, title: "Delete this database from the project", run: () => setForm({ kind: "removeDb", project: p.name, database: d.name }) },
    ];
  };
  // Built models that no database of a listed project explains (made from files, or of a database renamed since)
  const otherModels = (sources?.models || []).filter((m) => !m.source?.project
    || (projects && !projects.projects.some((q) => q.name === m.source.project && q.databases.some((d) => d.name === m.source.database))));
  const jobBox = job && html`
      <div class=${`load-job ${job.status}`}>
        <div class="load-job-head">
          ${job.status === "running" && html`<span class="spinner" aria-hidden="true"></span>`}
          <span>${job.status === "failed" ? html`<b>${job.title || "It"} failed.</b> ${job.error}` : job.message}
            ${job.status === "running" && job.seconds >= 2 && html` <span class="muted small">· ${elapsed(job.seconds)}</span>`}</span>
          ${job.log?.length > 0 && html`<button class="link" onClick=${() => setShowLog(!showLog)}>${showLog ? "Hide details" : "Details"}</button>`}
          ${job.status !== "running" && html`<button class="link" onClick=${() => setJob(null)}>Dismiss</button>`}
        </div>
        ${showLog && html`<pre class="load-log">${job.log.slice(-40).join("\n")}</pre>`}
        ${job.report && html`
          <div class="unlinked">
            <p>${job.report.length}${job.report.length === 300 ? "+" : ""} distinct inputs or flows could not be linked
              ${job.status === "done" ? "and were left out" : ""} (listed in <code>${job.report_path}</code>).
              ${job.status === "done" ? "" : "Link them with other background databases or adapters, or import without them."}</p>
            ${job.status !== "done" && form?.source === "simapro" && html`<button onClick=${() => { setForm({ ...form, unlinked: "drop" }); setJob(null); }}>Import without them…</button>`}
            <div class="unlinked-list"><table class="load-table">
              <tr><th>Type</th><th>Name</th><th>Compartment</th><th>Unit</th><th class="num">Exchanges</th></tr>
              ${job.report.slice(0, 100).map((r) => html`<tr><td>${r.type}</td><td>${r.name}</td><td>${r.categories}</td>
                <td>${r.unit}</td><td class="num">${r.exchanges}</td></tr>`)}
            </table></div>
          </div>`}
      </div>`;
  // Forms on a project, and the jobs started from a project, open under its row (where it was clicked)
  const listed = (name) => !!name && !!projects?.projects.some((p) => p.name === name);
  const projectForm = form && html`<${ProjectForm} key=${`${form.kind}:${form.project || ""}:${form.database || ""}`} form=${form} folder=${folder}
    projects=${projects} busy=${busy} onJob=${startJob} onCancel=${() => setForm(null)} onForm=${setForm} />`;

  const content = html`
    ${missing && html`<p class="load-missing">The database <b>${missing}</b> is not loaded (the server was restarted, or it was closed). Load it again below.</p>`}
    ${error && html`<div class="error">${error}</div>`}
    ${!listed(jobAt) && jobBox}
    ${!sources ? (!error && html`<${Waiting} text="Looking for databases…" />`) : html`
      <div class="view-group load-folder" role="group" aria-label="Brightway data folder">
        <span class="view-group-label" title="The Brightway data folder: the one with projects.db">Folder</span>
        <select aria-label="Brightway data folder" value=${isKnown ? folder : "other"}
          onChange=${(e) => { if (e.target.value === "other") { setTyped(folder); setFolder(""); setProjects(null); } else setFolder(e.target.value); }}>
          ${known.map((d) => html`<option value=${d.path}>${d.label} — ${d.path}</option>`)}
          <option value="other">Other folder…</option>
        </select>
        ${!isKnown && html`
          <form class="load-other" onSubmit=${(e) => { e.preventDefault(); setFolder(typed.trim().replace(/^["']|["']$/g, "")); }}>
            <input value=${typed} placeholder="Another Brightway data folder (the one with projects.db)"
              onInput=${(e) => setTyped(e.target.value)} />
            <button type="submit">List projects</button>
          </form>`}
      </div>
      <section>
        <h3 class="load-head">Projects <${InfoTip} text="Brightway 2.5 projects and their databases. Import into a project first (+, then Add database in the project's ⋯ menu), then open a database. Your own datasets: Edit mode." label="About projects" />
          <span class="load-head-tools">
            ${folder && html`
              <button class="icon-plain" onClick=${() => setForm({ kind: "new" })} aria-label="New project"
                title="New project: an empty one, to add databases to, or one from a Brightway backup (BAFU, …)"><${PlusIcon} /></button>`}
            <${StorageButton} onClick=${() => setStorageOpen(true)} />
            ${folder && html`
              <button class="icon-plain" disabled=${reading} onClick=${() => { refresh(); listProjects(folder, true); }}
                aria-label=${reading ? "Reading the projects" : "Refresh"}
                title=${reading ? "Reading the projects…" : "Refresh: read the projects and databases again, for changes made outside the app (Activity Browser, a script…). Only the projects written since are counted again."}>
                ${reading ? html`<span class="spinner" aria-hidden="true"></span>` : html`<${RefreshIcon} />`}</button>`}
          </span></h3>
        ${!listed(form?.project) && projectForm}
        ${projectsError && html`<div class="error">${projectsError}</div>`}
        ${folder && !projects && !projectsError && html`<${Waiting} text="Reading the projects…" />`}
        ${projects && html`
          <table class="load-table">
            ${projects.projects.filter((p) => p.databases.length || p.twofive)
              .sort((a, b) => rank(a) - rank(b)).map((p) => { const shown = unfolded.has(p.name) || form?.project === p.name || jobAt === p.name;
              const opened = p.databases.filter((d) => d.model?.key); return html`
              <tr class="project" key=${`p:${p.name}`}>
                <td colspan="2"><button class="fold-caret" aria-expanded=${shown} aria-label=${shown ? "Fold the databases" : "Show the databases"}
                    title=${shown ? "Fold the databases" : "Show the databases"} onClick=${() => toggleProject(p.name)}>${shown ? "▾" : "▸"}</button>
                  <b>${p.name}</b>
                  <span class="muted"> · ${!p.twofive ? "Brightway 2 project: lca_viewer reads Brightway 2.5 projects only (upgrade it in Activity Browser 3 or Brightway 2.5)"
                    : p.databases.length ? `${p.databases.filter((d) => d.processes).length} ${p.databases.filter((d) => d.processes).length === 1 ? "database" : "databases"}, ${count(p.methods)} impact methods` : "empty"}
                    ${opened.length > 0 && html` · <span class="loc-chip" title="Databases of this project that are open">${opened.map((d) => d.name).join(", ")} open</span>`}</span></td>
                <td class="actions project-actions">
                  <${PopupMenu} label=${html`<${GearIcon} />`} className="gear-btn" title="Options of this project" items=${[
                    ...(p.twofive ? [{ label: "Add database…", title: "Import a database into this project: an ecoSpold2 release, a SimaPro CSV export, BAFU, ecoinvent with your account, or a copy of one of another project",
                      run: () => setForm({ kind: "add", project: p.name }) }] : []),
                    ...(p.twofive ? [
                      { label: "Rename…", disabled: busy, title: "Give the project another name", run: () => setForm({ kind: "renameProject", project: p.name }) },
                      { label: "Copy…", disabled: busy, title: "A copy of the project, with its databases", run: () => setForm({ kind: "copyProject", project: p.name }) },
                      { label: "Databases graph…", title: "The databases of this project, which ones each uses, and what a change rebuilds", run: () => setForm({ kind: "graph", project: p.name }) }] : []),
                    { label: "Delete project…", danger: true, title: "Delete this project", run: () => setForm({ kind: "delete", project: p.name }) }]} />
                </td>
              </tr>
              ${jobAt === p.name && jobBox && form?.project !== p.name && html`<tr class="form-row" key=${`j:${p.name}`}><td colspan="3">${jobBox}</td></tr>`}
              ${form?.project === p.name && html`<tr class="form-row" key=${`f:${p.name}`}><td colspan="3">${projectForm}${jobAt === p.name && jobBox}</td></tr>`}
              ${shown && p.databases.map((d) => html`
                <tr key=${`d:${p.name}/${d.name}`} class=${`database${greyed(p, d) ? " muted" : ""}`} title=${greyed(p, d)}>
                  <td class="indent"><span class="db-name">${d.name}${p.twofive && lockButton(p, d)}</span>${d.model?.outdated && html`<div class="muted small">${d.model.outdated}${d.model.key ? ": close it to rebuild it" : ""}</div>`}</td>
                  <td class="num muted">${d.processes ? `${count(d.processes)} processes` : `${count(d.flows)} flows`}</td>
                  <td class="actions">${dbAction(p, d)}
                    ${p.twofive && html`<${PopupMenu} label=${html`<${GearIcon} />`} className="gear-btn" title="Options of this database" items=${dbOptions(p, d)} />`}</td>
                </tr>`)}`; })}
          </table>
          ${!projects.projects.length && html`<p class="muted">No project in this folder yet: create one with <b>+</b> above.</p>`}`}
      </section>
      ${folder && html`<${MethodsLibrary} folder=${projects?.folder || folder} projects=${projects} />`}
      ${otherModels.length > 0 && html`
        <section>
          <h3>Other models <${InfoTip} text="Built from files, or from a database no longer listed." label="About other models" /></h3>
          <table class="load-table">
            ${otherModels.map((m) => html`
              <tr key=${m.path}>
                <td><b>${m.title}</b>${m.source?.project && html`<div class="muted small">Project ${m.source.project}, database ${m.source.database}</div>`}</td>
                <td class="num muted">${m.built ? "" : "not built"}</td>
                <td class="actions">
                  ${m.key
                    ? html`<button onClick=${() => openDatabase(m.key)}>${m.key === DATABASE ? "Shown" : "Switch to"}</button>
                        <button title="Stop serving this database, to free its memory" onClick=${() => unload(m.key)}>Close</button>`
                    : html`<button class="primary" disabled=${busy || !m.built} onClick=${() => start({ path: m.path })}>Open</button>`}
                </td>
              </tr>`)}
          </table>
        </section>`}`}`;

  const storage = storageOpen && html`<${StorageDialog} onClose=${() => setStorageOpen(false)} />`;
  if (!onClose) return html`<div class="load-page"><h2>Projects</h2>${content}</div>${storage}`;
  return html`
    <div class="modal-backdrop" onMouseDown=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="load-dialog" ref=${box} role="dialog" aria-label="Projects">
        <div class="info-head">
          <div class="info-title">Projects</div>
          <button onClick=${onClose} aria-label="Close">×</button>
        </div>
        <div class="load-body">${content}</div>
      </div>
    </div>
    ${storage}`;
}

// ---------------------------------------------------------------- methods library
// Impact methods kept by the app (data/methods), not by a Brightway project: copied from a project, made empty or
// imported from a CSV file, edited here, and offered at build next to the project's methods (their source: "library").
// Their factors are keyed by flow (database, code) and found in the project of a build by key, by code, else by name
// and compartments (lca_viewer.methods_library).
const LIBRARY_API = "/api/library/methods";
const PART_LABELS = ["Family", "Category", "Indicator"];
const libraryRest = (m) => m.name.slice(1).join(" | ") || m.name[0];
const flowProjects = (projects) => (projects?.projects || []).filter((p) => p.twofive && p.databases.some((d) => !d.processes));
const csvUrl = (id) => `${LIBRARY_API}/${id}/csv`;
const saveCsv = (id) => { const a = document.createElement("a"); a.href = csvUrl(id); a.download = ""; a.click(); };

// The parts of a method's name: family, category, indicator… (empty parts are dropped)
function NameParts({ parts, setParts }) {
  const set = (k, v) => setParts(parts.map((p, i) => (i === k ? v : p)));
  return html`
    <fieldset class="name-parts">
      <legend>Name <${InfoTip} label="About the name" text="A method is named by parts, as in Brightway: its family (e.g. EF v3.1), its category (climate change), its indicator. The method lists group by family; in a model the source 'library' comes first." /></legend>
      ${parts.map((p, k) => html`
        <label key=${k}>${PART_LABELS[k] || `Part ${k + 1}`}
          <span class="name-part"><input value=${p} onInput=${(e) => set(k, e.target.value)} autofocus=${k === 0} aria-label=${PART_LABELS[k] || `Part ${k + 1}`} />
            ${k > 0 && k === parts.length - 1 && html`<button type="button" class="link" aria-label="Remove this part" onClick=${() => setParts(parts.slice(0, -1))}>×</button>`}</span>
        </label>`)}
      <button type="button" class="link" onClick=${() => setParts([...parts, ""])}>+ part</button>
    </fieldset>`;
}
const cleanParts = (parts) => parts.map((p) => p.trim().replace(/\s+/g, " ")).filter(Boolean);

// The project whose elementary flows a method's factors are matched to, and the search of a flow looks through
function ProjectSelect({ projects, value, onChange, label = "Project" }) {
  const listed = flowProjects(projects);
  return html`
    <label>${label}
      <select value=${value} onChange=${(e) => onChange(e.target.value)}>
        ${!listed.some((p) => p.name === value) && html`<option value=${value}>${value || "Choose a project"}</option>`}
        ${listed.map((p) => html`<option value=${p.name}>${p.name}</option>`)}
      </select></label>`;
}

// The name of a library method in a model: "library" first, or "library/<source>" when it has a source of its own
const libraryModelName = (name, source) => [source ? `library/${source}` : "library", ...name];
// A library method's family as the method lists group it ("EF v3.1 · library/ecoinvent-3.10.1")
const libraryFamily = (m) => familyKey({ name: m.model_name || libraryModelName(m.name, m.source) });
const plural = (n, one, many = `${one}s`) => `${count(n)} ${n === 1 ? one : many}`;

function MethodsLibrary({ folder, projects }) {
  const [open, setOpen] = useState(() => loadPreference("methodsLibraryOpen", "0") === "1");
  const [methods, setMethods] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);  // { kind: "new" | "copy" | "import" | "duplicate" | "delete", method }
  const [editing, setEditing] = useState(null);  // the id of the method open in the editor
  const [curating, setCurating] = useState(false);  // the Curate names view in place of the list
  const [text, setText] = useState("");
  const list = () => serverRequest(LIBRARY_API).then(setMethods).catch((e) => setError(e.message));
  useEffect(() => { list(); }, []);
  const unfold = (on) => { setOpen(on); savePreference("methodsLibraryOpen", on ? "1" : "0"); };
  const toggle = () => unfold(!open);
  const show = (kind, method) => { unfold(true); setCurating(false); setForm({ kind, method }); };
  const done = (made) => { if (form?.kind === "delete") setEditing(null); setForm(null); list(); if (made?.id) setEditing(made.id); };
  const curate = () => { unfold(true); setForm(null); setEditing(null); setCurating(true); };
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (methods || []).filter((m) => words.every((w) => `${m.source} ${m.name.join(" ")} ${m.unit}`.toLowerCase().includes(w)));
  const families = [...new Set(shown.map(libraryFamily))];
  const props = { folder, projects, onCancel: () => setForm(null), onDone: done, onListed: list, onCurate: curate };
  const menu = (m) => [
    { label: "Edit…", run: () => setEditing(m.id) },
    { label: "Duplicate…", title: "A copy of this method under another name", run: () => show("duplicate", m) },
    { label: "Export CSV", title: "Its factors as a CSV file: name, compartment, subcompartment, unit, factor, database, code", run: () => saveCsv(m.id) },
    { label: "Delete…", danger: true, run: () => show("delete", m) },
  ];
  return html`
    <section class="methods-library">
      <h3 class="load-head">
        <button class="fold-caret" aria-expanded=${open} aria-label=${open ? "Fold the methods library" : "Show the methods library"} onClick=${toggle}>${open ? "▾" : "▸"}</button>
        <span class="library-title" onClick=${toggle}>Methods library</span>
        <span class="muted">${methods ? plural(methods.length, "method") : ""}</span>
        <${InfoTip} label="About the methods library" text="Impact methods of your own, kept by the app (in data/methods), not by a project: copy methods of a project to edit their factors, make one, or import CSV files of factors. Building a database (Build and open, Rebuild and open) offers them next to the project's methods, under the source 'library' (or 'library/<source>'), and Add impact methods in the Methods panel too. Their factors are found in the project of the build by flow (database and code), else by code, else by name and compartments. Curate names sets their source, family, category and indicator, several at once." />
        <span class="load-head-tools">
          ${methods?.length > 0 && html`<button class=${curating ? "selected" : ""} aria-pressed=${curating} onClick=${() => (curating ? setCurating(false) : curate())}
            title="The names of all the library's methods as a table of their parts: edit them, several at once, and see how the method lists will read them">Curate names</button>`}
          <${PopupMenu} label=${html`<${PlusIcon} />`} className="icon-plain" title="Add methods to the library" items=${[
            { label: "Copy methods of a project…", title: "Editable copies of methods of a Brightway project, several at once", run: () => show("copy") },
            { label: "New empty method…", title: "A method with no factors yet: add them from the flows of a project", run: () => show("new") },
            { label: "Import CSV files…", title: "Factors by flow name, compartment, subcompartment, unit, matched to the flows of a project; one method per file, or several in a file with a method column", run: () => show("import") }]} />
        </span>
      </h3>
      ${open && html`
        ${error && html`<div class="error">${error}</div>`}
        ${form && !["duplicate", "delete"].includes(form.kind) && html`<${LibraryForm} key=${form.kind} form=${form} ...${props} />`}
        ${!methods ? !error && html`<${Waiting} text="Reading the methods library…" />` : curating ? html`
          <${CurateNames} methods=${methods} onClose=${() => setCurating(false)} onApplied=${setMethods} />` : !methods.length ? !form && html`
          <p class="muted">No method yet: <button class="link" onClick=${() => show("copy")}>copy methods of a project</button>,
            <button class="link" onClick=${() => show("new")}>make one</button> or <button class="link" onClick=${() => show("import")}>import CSV files</button>.</p>` : html`
          ${methods.length > 8 && html`<input class="method-filter" placeholder="Filter the methods" value=${text} onInput=${(e) => setText(e.target.value)} aria-label="Filter the library" />`}
          <table class="load-table library-table">
            ${families.map((family) => {
              const members = shown.filter((m) => libraryFamily(m) === family);
              const p = methodPath({ name: members[0].model_name });
              return html`
              <tr class="project" key=${`f:${family}`}><td colspan="3"><b>${p.family}</b> <span class="muted">· ${p.source}</span></td></tr>
              ${members.map((m) => html`
                <tr class="database" key=${m.id}>
                  <td class="indent"><span class="library-name" title=${m.model_name.join(" | ")}>${libraryRest(m)}</span>
                    ${m.unit && html` <span class="muted small">${unit(m.unit)}</span>`}
                    ${m.origin?.project && html`<div class="muted small">${m.origin.method ? `Copied from ${m.origin.method.join(" | ")} (${m.origin.project})` : `Imported, matched to ${m.origin.project}`}</div>`}</td>
                  <td class="num muted">${plural(m.factors, "factor")}</td>
                  <td class="actions"><button onClick=${() => setEditing(editing === m.id ? null : m.id)} aria-expanded=${editing === m.id}>${editing === m.id ? "Close" : "Edit"}</button>
                    <${PopupMenu} label=${html`<${GearIcon} />`} className="gear-btn" title="Options of this method" items=${menu(m)} /></td>
                </tr>
                ${form?.method?.id === m.id && html`<tr class="form-row" key=${`form:${m.id}`}><td colspan="3"><${LibraryForm} key=${form.kind} form=${form} ...${props} /></td></tr>`}
                ${editing === m.id && html`<tr class="form-row" key=${`edit:${m.id}`}><td colspan="3">
                  <${MethodEditor} id=${m.id} folder=${folder} projects=${projects} onClose=${() => setEditing(null)} onChanged=${list}
                    onForm=${(kind) => show(kind, m)} /></td></tr>`}`)}`;
            })}
          </table>`}`}
    </section>`;
}

// CSV files, each typed or pasted (a path on the server's computer), or chosen here (several at once) and uploaded.
// ``files``: [{ path, name }], ``name`` the file's name as chosen (an uploaded file is renamed).
function PathsInput({ files, setFiles, accept }) {
  const [typed, setTyped] = useState("");
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);
  const input = useRef(null);
  const add = (more) => setFiles((now) => [...now, ...more.filter((f) => !now.some((g) => g.path === f.path))]);
  async function choose(e) {
    const chosen = [...e.target.files];
    e.target.value = "";
    if (!chosen.length) return;
    setError(null);
    const done = [];
    try {
      for (const [k, file] of chosen.entries()) {
        setProgress(`${k + 1}/${chosen.length}`);
        done.push({ path: await uploadFile(file, () => {}), name: file.name });
      }
    } catch (err) { setError(err.message); }
    setProgress(null);
    add(done);
  }
  const addTyped = () => {
    const path = typed.trim().replace(/^["']|["']$/g, "");
    if (path) add([{ path, name: "" }]);
    setTyped("");
  };
  return html`
    <div class="paths-input">
      ${files.map((f) => html`<div class="path-chip" key=${f.path}><span title=${f.path}>${f.name || f.path.split(/[\\/]/).pop()}</span>
        <button type="button" class="link" aria-label=${`Leave out ${f.name || f.path}`} onClick=${() => setFiles(files.filter((g) => g !== f))}>×</button></div>`)}
      <div class="path-input">
        <input value=${typed} placeholder=${files.length ? "Another file: its path" : "Path of a CSV file"} onInput=${(e) => setTyped(e.target.value)}
          onKeyDown=${(e) => { if (e.key === "Enter") { e.preventDefault(); addTyped(); } }} aria-label="Path of a CSV file" />
        ${typed.trim() && html`<button type="button" onClick=${addTyped}>Add</button>`}
        <input type="file" multiple ref=${input} accept=${accept} style="display:none" onChange=${choose} />
        <button type="button" disabled=${progress !== null} onClick=${() => input.current.click()}
          title="Choose the files here (several at once): they are copied to the app's data folder for the import, then deleted">
          ${progress !== null ? `Uploading ${progress}` : "Choose files…"}</button>
      </div>
      ${error && html`<div class="error">${error}</div>`}
    </div>`;
}

// The forms of the library: a new method, copies of a project's, CSV files, a duplicate, a deletion
function LibraryForm({ form, folder, projects, onCancel, onDone, onListed, onCurate }) {
  const listed = flowProjects(projects);
  const method = form.method;
  const [parts, setParts] = useState(() => (form.kind === "duplicate" ? [...method.name.slice(0, -1), `${method.name.at(-1)} (copy)`] : ["", "", ""]));
  const [unitText, setUnit] = useState("");
  const [description, setDescription] = useState("");
  const [project, setProject] = useState(listed[0]?.name || "");
  const [files, setFiles] = useState([]);  // the CSV files of an import: [{ path, name }]
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [report, setReport] = useState(null);  // of a copy or an import: what was added, skipped, left out
  // A copy: the methods of the project, those ticked
  const [source, setSource] = useState(null);
  const [chosen, setChosen] = useState(() => new Set());
  useEffect(() => { if (!project && listed[0]) setProject(listed[0].name); }, [listed.length]);  // the projects read after the form opened
  useEffect(() => {
    if (form.kind !== "copy" || !project) return;
    setSource(null); setChosen(new Set());
    serverRequest(`/api/brightway/methods?${new URLSearchParams({ folder, project })}`).then((r) => setSource(r.methods)).catch((e) => setError(e.message));
  }, [project]);
  const send = (url, body) => {
    setBusy(true); setError(null);
    return serverRequest(url, body).finally(() => setBusy(false));
  };
  const name = cleanParts(parts);
  const picked = (source || []).filter((m) => chosen.has(methodKey(m.name))).map((m) => m.name);
  const copyBody = { folder, project };
  const importBody = { folder, project, paths: files.map((f) => f.path), names: files.map((f) => f.name), unit: unitText };
  const reported = (kind, body) => (r) => {
    setReport({ kind, body, project, added: kind === "copy" ? r.added : r.methods, skipped: r.skipped, unmatched: r.unmatched || [] });
    onListed();
  };
  const submit = (e) => {
    e.preventDefault();
    const failed = (err) => setError(err.message);
    if (form.kind === "new") send(LIBRARY_API, { name, unit: unitText, description }).then(onDone).catch(failed);
    else if (form.kind === "copy") send(`${LIBRARY_API}/copy`, { ...copyBody, methods: picked }).then(reported("copy", copyBody)).catch(failed);
    else if (form.kind === "import") send(`${LIBRARY_API}/import`, importBody).then(reported("import", importBody)).catch(failed);
    else if (form.kind === "duplicate") send(`${LIBRARY_API}/${method.id}/duplicate`, { name }).then(onDone).catch(failed);
    else if (form.kind === "delete") send(`${LIBRARY_API}/${method.id}/delete`, {}).then(() => onDone()).catch(failed);
  };
  // The methods skipped because the library has their names, added again with a suffix to their last part
  const rename = (suffix) => {
    const taken = report.skipped.filter((s) => s.taken);
    const body = report.kind === "copy" ? { ...report.body, methods: taken.map((s) => s.method), on_conflict: "rename", suffix }
      : { ...report.body, on_conflict: "rename", suffix, only: taken.map((s) => libraryModelName(s.name, s.source)) };
    return send(`${LIBRARY_API}/${report.kind}`, body).then((r) => {
      setReport({ ...report, added: [...report.added, ...(report.kind === "copy" ? r.added : r.methods)],
        skipped: [...report.skipped.filter((s) => !s.taken), ...r.skipped], renamed: true });
      onListed();
    }).catch((err) => setError(err.message));
  };
  const actions = (label, disabled, danger) => html`
    <div class="form-actions">
      <button type="submit" class=${danger ? "danger" : "primary"} disabled=${!!disabled || busy}>${busy ? "…" : label}</button>
      <button type="button" onClick=${onCancel}>Cancel</button>
      ${typeof disabled === "string" && html`<span class="muted small">${disabled}</span>`}
    </div>`;
  const noName = !name.length && "Give the method a name";
  if (report) return html`<${AddReport} report=${report} busy=${busy} error=${error} onRename=${rename}
    onEdit=${(m) => onDone(m)} onCurate=${onCurate} onClose=${() => onDone()} />`;
  if (form.kind === "delete") return html`
    <form class="project-form danger" onSubmit=${submit}>
      <h4>Delete ${method.name.join(" | ")}</h4>
      <p>The method and its ${count(method.factors)} factors are deleted from the library. It cannot be undone (export it as CSV first to keep
        a copy). Databases built with it keep it until they are built again.</p>
      ${error && html`<div class="error">${error}</div>`}
      ${actions("Delete method", false, true)}
    </form>`;
  const head = { new: "New method", copy: "Copy methods of a project", import: "Import CSV files of factors", duplicate: `Duplicate ${method?.name.join(" | ")}` }[form.kind];
  return html`
    <form class="project-form" onSubmit=${submit}>
      <h4>${head}${form.kind === "import" ? html` <${InfoTip} label="About the CSV files" text="One factor per row: flow name, compartment, subcompartment, unit, factor (a header row with these names may give them in another order, and database and code columns, as the export writes them, find the flows by key). Comma, semicolon or tab separated; a decimal comma is read. A file of several methods names the method of each row: a 'method' column (its parts separated by '|', e.g. EF v3.1 | climate change | GWP100), or 'source', 'family', 'impact category' and 'indicator' columns, and a 'method unit' column its unit. Else a file is one method, named after the file. Each row is matched to an elementary flow of the project: by database and code, else by name and compartments (an 'unspecified' subcompartment is none), its unit checked. Rows of no flow are listed and left out. Curate names sets the names' parts afterwards." />`
        : form.kind === "copy" && html` <${InfoTip} label="About the copies" text="Each copy keeps the name of its method; an ecoinvent release first in the name (ecoinvent-3.10.1) becomes its source, so in the method lists a copy reads 'EF v3.1 · library/ecoinvent-3.10.1', apart from the project's. Curate names sets the names' parts afterwards, several at once. A method whose name the library has already is skipped, and can then be added with a suffix." />`}</h4>
      ${(form.kind === "copy" || form.kind === "import") && html`<${ProjectSelect} projects=${projects} value=${project} onChange=${setProject}
        label=${form.kind === "copy" ? "From the project" : "Match the flows of the project"} />`}
      ${form.kind === "copy" && (!project ? html`<p class="muted small">Choose a project (its list is still being read)</p>`
        : !source ? !error && html`<${Waiting} text="Reading the impact methods of the project…" />` : html`
        <div class="row">
          <button type="button" onClick=${() => setChosen(new Set(source.map((m) => methodKey(m.name))))}>All ${count(source.length)}</button>
          <button type="button" onClick=${() => setChosen(new Set())}>None</button>
          <span class="muted small">Tick a family to take all its methods</span>
        </div>
        <${MethodPicker} methods=${source} chosen=${chosen} setChosen=${setChosen} />`)}
      ${form.kind === "import" && html`<label>CSV files <${PathsInput} files=${files} setFiles=${setFiles} accept=".csv,.CSV,.txt" /></label>
        <label>Unit <input value=${unitText} onInput=${(e) => setUnit(e.target.value)} placeholder="Of the methods the files give no unit for (method unit column), e.g. kg CO2-eq" /></label>`}
      ${(form.kind === "new" || form.kind === "duplicate") && html`<${NameParts} parts=${parts} setParts=${setParts} />`}
      ${form.kind === "new" && html`
        <label>Unit <input value=${unitText} onInput=${(e) => setUnit(e.target.value)} placeholder="e.g. kg CO2-eq" /></label>
        <label>Description <textarea rows="2" value=${description} onInput=${(e) => setDescription(e.target.value)} /></label>`}
      ${error && html`<div class="error">${error}</div>`}
      ${actions(form.kind === "copy" ? `Copy ${plural(picked.length, "method")}` : { new: "Create method", import: files.length > 1 ? `Import ${count(files.length)} files` : "Import", duplicate: "Duplicate" }[form.kind],
        form.kind === "copy" ? !picked.length && "Tick the methods to copy" : form.kind === "import" ? !files.length && "Give the CSV files" : noName)}
    </form>`;
}

// What a copy or an import added: the methods added (renamed ones with the name they wanted), those skipped (a name
// the library has: added with a suffix, or left), the rows of no flow of the project
function AddReport({ report, busy, error, onRename, onEdit, onCurate, onClose }) {
  const [suffix, setSuffix] = useState(" (copy)");
  const taken = report.skipped.filter((s) => s.taken);
  const other = report.skipped.filter((s) => !s.taken);
  const named = (s) => [s.source, ...s.name].filter(Boolean).join(" | ");
  return html`
    <div class="project-form add-report">
      <h4>${report.added.length ? `${plural(report.added.length, "method")} added to the library` : "No method added"}
        ${report.kind === "import" && report.added.length > 0 && html` <span class="muted small">(factors matched to ${report.project})</span>`}</h4>
      ${report.added.length > 0 && html`<ul class="report-list">${report.added.map((m) => html`
        <li key=${m.id}>${[m.source, ...m.name].filter(Boolean).join(" | ")} <span class="muted small">${plural(m.factors, "factor")}${m.unit ? ` · ${unit(m.unit)}` : ""}${m.origin?.left_out ? ` · ${plural(m.origin.left_out, "factor")} on flows the project lacks, left out` : ""}</span>
          ${m.wanted && html` <span class="muted small">(renamed: the library has ${[m.source, ...m.wanted].filter(Boolean).join(" | ")})</span>`}</li>`)}</ul>`}
      ${taken.length > 0 && html`
        <div class="report-taken">
          <p>${plural(taken.length, "method")} skipped: the library has ${taken.length === 1 ? "a method of this name" : "methods of these names"} already.</p>
          <ul class="report-list">${taken.map((s) => html`<li key=${named(s)}>${named(s)}${s.file ? html` <span class="muted small">${s.file}</span>` : ""}</li>`)}</ul>
          <div class="form-actions">
            <label class="inline">Suffix after the last part <input class="suffix-input" value=${suffix} onInput=${(e) => setSuffix(e.target.value)} /></label>
            <button class="primary" disabled=${busy} onClick=${() => onRename(suffix)}
              title="Add them under their names with the suffix after the last part (numbered if that one is taken too)">${busy ? "…" : `Add ${taken.length === 1 ? "it" : "them"} renamed`}</button>
            <span class="muted small">or leave ${taken.length === 1 ? "it" : "them"} out (Close)</span>
          </div>
        </div>`}
      ${other.length > 0 && html`
        <p>${plural(other.length, "method")} not added:</p>
        <ul class="report-list">${other.map((s) => html`<li key=${named(s)}>${named(s)} <span class="muted small">${s.reason}${s.file ? ` · ${s.file}` : ""}</span></li>`)}</ul>`}
      ${report.unmatched.length > 0 && html`
        <p>${plural(report.unmatched.length, "row")} of the ${report.body.paths?.length > 1 ? "files" : "file"} left out (no flow of ${report.project}, or unreadable):</p>
        <${UnmatchedRows} rows=${report.unmatched} />`}
      ${report.kind === "import" && !report.unmatched.length && report.added.length > 0 && html`<p class="muted small">Every row matched a flow of ${report.project}.</p>`}
      ${error && html`<div class="error">${error}</div>`}
      <div class="form-actions">
        ${report.added.length === 1 && html`<button onClick=${() => onEdit(report.added[0])}>Edit the method</button>`}
        ${report.added.length > 0 && html`<button onClick=${onCurate} title="Set the source, family, category and indicator of the library's methods, several at once">Curate names…</button>`}
        <button class=${report.added.length ? "" : "primary"} onClick=${onClose}>Close</button>
      </div>
    </div>`;
}

function UnmatchedRows({ rows }) {
  const files = new Set(rows.map((r) => r.file).filter(Boolean)).size > 1;
  const methods = rows.some((r) => r.method);
  return html`
    <div class="unlinked-list"><table class="load-table">
      <tr>${files && html`<th>File</th>`}<th>Line</th>${methods && html`<th>Method</th>`}<th>Flow</th><th>Compartment</th><th>Unit</th><th class="num">Factor</th><th>Why</th></tr>
      ${rows.slice(0, 200).map((r) => html`<tr key=${`${r.file}:${r.line}`}>${files && html`<td class="muted small">${r.file}</td>`}<td class="num">${r.line}</td>
        ${methods && html`<td class="small">${r.method}</td>`}<td>${r.name || r.code}</td>
        <td>${[r.compartment, r.subcompartment].filter(Boolean).join(", ")}</td><td>${r.unit}</td><td class="num">${r.factor}</td>
        <td class="muted small">${r.reason}</td></tr>`)}
    </table>${rows.length > 200 && html`<p class="muted small">and ${count(rows.length - 200)} more</p>`}</div>`;
}

// ---- Curate names: every method of the library as a table of its name's parts, edited in place or several at once
// (set a part, find and replace, split a part in two, merge two parts), previewed as the method lists group them, then
// applied in one request (/api/library/methods/names). A method renamed keeps its former name: a database built with
// it finds it again and is shown as to rebuild.
const CURATE_LIMIT = 300;  // rows drawn before "Show all"
const draftOf = (m) => ({ id: m.id, source: m.source || "", parts: [...m.name], unit: m.unit || "" });
const tidy = (s) => (s || "").trim().replace(/\s+/g, " ");
const draftName = (d) => ({ source: tidy(d.source).replace(/^library(\/|$)/, "").trim(), name: d.parts.map(tidy).filter(Boolean), unit: tidy(d.unit) });
const draftKey = (d) => { const n = draftName(d); return libraryModelName(n.name, n.source).join("\u001f"); };
// A part of the name, as the bulk tools choose it: 0 the source, 1 the family, 2 the category…
const partLabel = (k) => ["Source", "Family", "Category", "Indicator"][k] || `Part ${k}`;
const partsOf = (d) => [d.source, ...d.parts];
const withParts = (d, all) => { const full = Array.from(all, (p) => p ?? ""); return { ...d, source: full[0] ?? "", parts: full.slice(1) }; };

function CurateNames({ methods, onClose, onApplied }) {
  const [drafts, setDrafts] = useState(() => methods.map(draftOf));
  const [selected, setSelected] = useState(() => new Set());
  const [text, setText] = useState("");
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  // The bulk tools: the part they act on, their values
  const [setPart, setSetPart] = useState(1);
  const [setValue, setSetValue] = useState("");
  const [findPart, setFindPart] = useState(-1);  // -1: every part
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [splitPart, setSplitPart] = useState(1);
  const [splitOn, setSplitOn] = useState("_");
  const [mergePart, setMergePart] = useState(1);
  const [mergeWith, setMergeWith] = useState(" ");
  const original = useMemo(() => new Map(methods.map((m) => [m.id, m])), [methods]);
  const changed = (d) => {
    const m = original.get(d.id), n = draftName(d);
    return n.source !== (m.source || "") || n.name.join("\u001f") !== m.name.join("\u001f") || n.unit !== (m.unit || "");
  };
  const width = Math.max(3, ...drafts.map((d) => d.parts.length));  // the name parts drawn: family, category, indicator, more
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = drafts.filter((d) => words.every((w) => `${d.source} ${d.parts.join(" ")} ${d.unit}`.toLowerCase().includes(w)));
  const drawn = all ? shown : shown.slice(0, CURATE_LIMIT);
  const edits = drafts.filter(changed);
  // What is wrong with the names as they will be: a method with no name, a name starting with "library", two of a name
  const problems = useMemo(() => {
    const out = new Map(), seen = new Map();
    drafts.forEach((d) => {
      const n = draftName(d);
      if (!n.name.length) out.set(d.id, "No name: give it a family at least");
      else if (/^library(\/|$)/.test(n.name[0])) out.set(d.id, "A name cannot start with 'library'");
      const key = draftKey(d);
      if (seen.has(key)) { out.set(d.id, "Two methods of this name"); out.set(seen.get(key), "Two methods of this name"); }
      seen.set(key, d.id);
    });
    return out;
  }, [drafts]);
  const update = (fn, ids = selected) => { setDrafts((now) => now.map((d) => (ids.has(d.id) ? fn(d) : d))); setMessage(null); };
  const setCell = (id, k, value) => update((d) => (k === "unit" ? { ...d, unit: value } : withParts(d, Object.assign([...partsOf(d)], { [k]: value }))), new Set([id]));
  const toggle = (id, on) => setSelected((s) => { const next = new Set(s); on ? next.add(id) : next.delete(id); return next; });
  const allShown = shown.length > 0 && shown.every((d) => selected.has(d.id));
  const tick = (on) => setSelected((s) => { const next = new Set(s); shown.forEach((d) => (on ? next.add(d.id) : next.delete(d.id))); return next; });
  // The bulk changes, on the rows ticked
  const bulkSet = () => update((d) => withParts(d, Object.assign([...partsOf(d)], { [setPart]: setValue })));
  const bulkReplace = () => find && update((d) => withParts(d, partsOf(d).map((p, k) => (findPart < 0 || k === findPart ? (p || "").split(find).join(replace) : p))));
  const bulkSplit = () => splitOn && update((d) => {
    const parts = partsOf(d), value = parts[splitPart] || "", at = value.indexOf(splitOn);
    if (at < 0) return d;
    return withParts(d, [...parts.slice(0, splitPart), value.slice(0, at), value.slice(at + splitOn.length), ...parts.slice(splitPart + 1)]);
  });
  const bulkMerge = () => update((d) => {
    const parts = partsOf(d);
    if (mergePart + 1 >= parts.length) return d;
    const joined = [parts[mergePart], parts[mergePart + 1]].map((p) => p || "").filter((p) => p.trim()).join(mergeWith);
    return withParts(d, [...parts.slice(0, mergePart), joined, ...parts.slice(mergePart + 2)]);
  });
  const apply = () => {
    setBusy(true); setError(null);
    serverRequest(`${LIBRARY_API}/names`, { changes: edits.map((d) => ({ id: d.id, ...draftName(d) })) })
      .then((r) => {
        onApplied(r.methods);
        setDrafts(r.methods.map(draftOf));
        setMessage(`${plural(r.changed.length, "method")} renamed. Databases built with ${r.changed.length === 1 ? "it" : "them"} find ${r.changed.length === 1 ? "it" : "them"} under the former names and are shown as to rebuild, to take the new names.`);
      })
      .catch((e) => setError(e.message)).finally(() => setBusy(false));
  };
  const partOptions = (from = 0, last = width) => Array.from({ length: last + 1 - from }, (_, k) => html`<option value=${k + from}>${partLabel(k + from)}</option>`);
  const none = !selected.size && "Tick the methods to change first";
  // The preview: the methods as the lists will group and name them, families sorted as the library lists them
  const preview = useMemo(() => {
    const groups = new Map();
    [...drafts].sort((a, b) => draftKey(a).toLowerCase().localeCompare(draftKey(b).toLowerCase())).forEach((d) => {
      const n = draftName(d);
      if (!n.name.length) return;
      const m = { name: libraryModelName(n.name, n.source), unit: n.unit };
      const key = familyKey(m);
      groups.set(key, [...(groups.get(key) || []), { d, m }]);
    });
    return [...groups];
  }, [drafts]);
  return html`
    <div class="project-form curate-names">
      <h4>Curate names <${InfoTip} label="About the names" text="A method is named by parts: its source (the release it came from, or none), its family (e.g. EF v3.1), its category (climate change), its indicator (GWP100), and more parts if need be. In a model and in the method lists, a library method's source is 'library', or 'library/<source>': the lists group the methods by family and source ('EF v3.1 · library/ecoinvent-3.10.1'). Edit a part in place, or tick rows and change them together; the preview shows the names as the lists will read them. Nothing is written until Apply. A method renamed keeps its former name: databases built with it find it and are shown as to rebuild." />
        <button class="link close" onClick=${onClose} aria-label="Close Curate names">×</button></h4>
      <div class="curate-tools">
        <div class="curate-tool"><span>Set</span><select value=${setPart} onChange=${(e) => setSetPart(Number(e.target.value))} aria-label="Part to set">${partOptions(0, 3)}</select>
          <span>to</span><input value=${setValue} onInput=${(e) => setSetValue(e.target.value)} aria-label="Value of the part" placeholder="(empty: none)" />
          <button disabled=${!!none} title=${none || "Set this part of the methods ticked"} onClick=${bulkSet}>Set</button></div>
        <div class="curate-tool"><span>Replace</span><input value=${find} onInput=${(e) => setFind(e.target.value)} aria-label="Text to find" placeholder="text" />
          <span>with</span><input value=${replace} onInput=${(e) => setReplace(e.target.value)} aria-label="Replacement" placeholder="(nothing)" />
          <span>in</span><select value=${findPart} onChange=${(e) => setFindPart(Number(e.target.value))} aria-label="Part to search"><option value="-1">every part</option>${partOptions()}</select>
          <button disabled=${!!none || !find} title=${none || "Replace the text in the methods ticked"} onClick=${bulkReplace}>Replace</button></div>
        <div class="curate-tool"><span>Split</span><select value=${splitPart} onChange=${(e) => setSplitPart(Number(e.target.value))} aria-label="Part to split">${partOptions()}</select>
          <span>in two at the first</span><input class="sep-input" value=${splitOn} onInput=${(e) => setSplitOn(e.target.value)} aria-label="Separator to split on" />
          <button disabled=${!!none || !splitOn} title=${none || "The text after the separator becomes the next part; the parts after move right"} onClick=${bulkSplit}>Split</button></div>
        <div class="curate-tool"><span>Merge</span><select value=${mergePart} onChange=${(e) => setMergePart(Number(e.target.value))} aria-label="Part to merge">${partOptions(0, width - 1)}</select>
          <span>with the next part, joined by</span><input class="sep-input" value=${mergeWith} onInput=${(e) => setMergeWith(e.target.value)} aria-label="Text joining the two parts" />
          <button disabled=${!!none} title=${none || "The two parts become one; the parts after move left"} onClick=${bulkMerge}>Merge</button></div>
      </div>
      <div class="row">
        <input class="method-filter" placeholder="Filter the methods" value=${text} onInput=${(e) => setText(e.target.value)} aria-label="Filter the methods to curate" />
        <span class="muted small">${selected.size ? `${count(selected.size)} ticked` : "Tick rows to change them together"}</span>
      </div>
      <div class="curate-list"><table class="load-table curate-table">
        <tr><th><input type="checkbox" checked=${allShown} ref=${(el) => el && (el.indeterminate = !allShown && shown.some((d) => selected.has(d.id)))}
            onChange=${(e) => tick(e.target.checked)} aria-label="Tick all the methods shown" /></th>
          ${Array.from({ length: width + 1 }, (_, k) => html`<th key=${k}>${partLabel(k)}</th>`)}<th>Unit</th><th></th></tr>
        ${drawn.map((d) => {
          const parts = partsOf(d), problem = problems.get(d.id), edited = changed(d);
          return html`<tr key=${d.id} class=${`${edited ? "edited" : ""}${problem ? " problem" : ""}`} title=${problem || ""}>
            <td><input type="checkbox" checked=${selected.has(d.id)} onChange=${(e) => toggle(d.id, e.target.checked)} aria-label=${`Tick ${d.parts.join(" | ")}`} /></td>
            ${Array.from({ length: width + 1 }, (_, k) => html`<td key=${k}><input value=${parts[k] || ""} onInput=${(e) => setCell(d.id, k, e.target.value)}
              aria-label=${`${partLabel(k)} of ${d.parts.join(" | ")}`} placeholder=${k === 0 ? "none" : ""} /></td>`)}
            <td><input class="unit-input" value=${d.unit} onInput=${(e) => setCell(d.id, "unit", e.target.value)} aria-label=${`Unit of ${d.parts.join(" | ")}`} /></td>
            <td>${edited && html`<button class="link" title="Undo the changes of this method" aria-label="Undo" onClick=${() => update(() => draftOf(original.get(d.id)), new Set([d.id]))}>↺</button>`}</td>
          </tr>`;
        })}
      </table>
      ${!all && shown.length > CURATE_LIMIT && html`<button class="link" onClick=${() => setAll(true)}>Show all ${count(shown.length)}</button>`}</div>
      <details class="curate-preview" open>
        <summary>Preview: the names as the method lists will read them <span class="muted small">(grouped by family and source)</span></summary>
        <div class="method-tree">
          ${preview.map(([family, list]) => html`
            <div class="method-family" key=${family}>
              <div class="method-head"><span class="name">${family}</span><span class="muted count">${list.length}</span></div>
              ${list.map(({ d, m }) => html`<div class=${`method-row${changed(d) ? " edited" : ""}`} key=${d.id}>
                <span class="name" title=${m.name.join(" | ")}>${methodParts(m).main}${methodParts(m).qual && html` <span class="muted">${methodParts(m).qual}</span>`}${m.unit && html` <span class="muted small">${unit(m.unit)}</span>`}</span></div>`)}
            </div>`)}
        </div>
      </details>
      ${error && html`<div class="error">${error}</div>`}
      ${message && html`<p class="muted small">${message}</p>`}
      <div class="form-actions">
        <button class="primary" disabled=${busy || !edits.length || problems.size > 0} onClick=${apply}>${busy ? "…" : edits.length ? `Apply ${plural(edits.length, "change")}` : "Apply"}</button>
        <button disabled=${!edits.length} onClick=${() => { setDrafts(methods.map(draftOf)); setMessage(null); }}>Discard</button>
        ${problems.size > 0 && html`<span class="error small">${plural(problems.size, "name")} to fix first (red rows)</span>`}
        ${edits.length > 0 && !problems.size && html`<span class="muted small">Databases built with the methods renamed will be shown as to rebuild</span>`}
        <span class="spacer"></span>
        <button onClick=${onClose}>Close</button>
      </div>
    </div>`;
}

// A factor's value, written when it is left (Enter or a click away)
function FactorInput({ value, onCommit, label }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const x = Number(text.trim().replace(",", "."));
    if (text.trim() === "" || !Number.isFinite(x)) { setText(String(value)); return; }
    if (x !== value) onCommit(x);
  };
  return html`<input class="factor-input" value=${text} aria-label=${label} onInput=${(e) => setText(e.target.value)} onBlur=${commit}
    onKeyDown=${(e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } else if (e.key === "Escape") setText(String(value)); }} />`;
}

const FACTOR_LIMIT = 300;  // rows drawn before "Show all"

// The editor of a method of the library: its name, unit and description, its factors (changed, removed, added from
// the flows of a project), a CSV file imported into it, and the other actions
function MethodEditor({ id, folder, projects, onClose, onChanged, onForm }) {
  const [method, setMethod] = useState(null);
  const [error, setError] = useState(null);
  const [parts, setParts] = useState(null);
  const [unitText, setUnit] = useState("");
  const [description, setDescription] = useState("");
  const [project, setProject] = useState(null);
  const [kind, setKind] = useState("emission");
  const [value, setValue] = useState("1");
  const [filter, setFilter] = useState("");
  const [all, setAll] = useState(false);
  const [csv, setCsv] = useState(null);  // the path of a CSV file to import into the method (null: closed)
  const [report, setReport] = useState(null);
  const [saving, setSaving] = useState(false);
  const read = (p = project) => serverRequest(`${LIBRARY_API}/${id}${p ? `?${new URLSearchParams({ folder, project: p })}` : ""}`)
    .then((m) => {
      setMethod(m); setError(null);
      if (parts === null) { setParts([...m.name]); setUnit(m.unit); setDescription(m.description || ""); }
      if (p === null) {  // the project the method came from, else the first with elementary flows
        const listed = flowProjects(projects);
        const first = listed.find((q) => q.name === m.origin?.project)?.name || listed[0]?.name;
        if (first) { setProject(first); read(first); }
      }
    }).catch((e) => setError(e.message));
  useEffect(() => { read(null); }, [id]);
  const change = (body) => serverRequest(`${LIBRARY_API}/${id}/factors`, body).then(() => { read(); onChanged(); }).catch((e) => setError(e.message));
  const save = (e) => {
    e.preventDefault();
    setSaving(true);
    serverRequest(`${LIBRARY_API}/${id}`, { name: cleanParts(parts), unit: unitText, description })
      .then((m) => { setParts([...m.name]); read(); onChanged(); }).catch((err) => setError(err.message)).finally(() => setSaving(false));
  };
  const importCsv = () => serverRequest(`${LIBRARY_API}/import`, { folder, project, path: csv, id })
    .then((r) => { setReport(r); setCsv(null); read(); onChanged(); }).catch((e) => setError(e.message));
  if (!method) return error ? html`<div class="error">${error}</div>` : html`<${Waiting} text="Reading the method…" />`;
  const edited = parts && (cleanParts(parts).join("\u001f") !== method.name.join("\u001f") || unitText !== method.unit || description !== (method.description || ""));
  const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = method.factors.filter((f) => words.every((w) => `${f.name} ${f.categories.join(" ")} ${f.database} ${f.unit}`.toLowerCase().includes(w)));
  const shownRows = all ? rows : rows.slice(0, FACTOR_LIMIT);
  const coverage = method.coverage;
  const added = (n) => change({ set: [{ database: n.database, code: n.code, name: n.name, categories: n.categories, unit: n.unit, cf: Number(value.replace(",", ".")) }] });
  return html`
    <div class="project-form method-editor">
      <h4>${method.name.join(" | ")} <span class="muted small">· library</span>
        <button class="link close" onClick=${onClose} aria-label="Close the editor">×</button></h4>
      <form class="source-form" onSubmit=${save}>
        <${NameParts} parts=${parts} setParts=${setParts} />
        <label>Unit <input value=${unitText} onInput=${(e) => setUnit(e.target.value)} placeholder="e.g. kg CO2-eq" /></label>
        <label>Description <textarea rows="2" value=${description} onInput=${(e) => setDescription(e.target.value)} /></label>
        ${edited && html`<div class="form-actions"><button type="submit" class="primary" disabled=${saving || !cleanParts(parts).length}>Save</button>
          <button type="button" onClick=${() => { setParts([...method.name]); setUnit(method.unit); setDescription(method.description || ""); }}>Undo</button>
          <span class="muted small">Databases built with the method: rebuild them to take the change</span></div>`}
      </form>
      ${error && html`<div class="error">${error}</div>`}
      <fieldset>
        <legend>Factors <${InfoTip} label="About the factors" text="One factor per elementary flow, kept with its database and code and shown with the flow's name, compartments and unit. A change is saved at once. In the project chosen here, a factor whose flow the project does not have (by key, code, or name and compartments) is greyed out: a build of that project leaves it out. Databases built with the method take the changes when they are built again (Projects shows them as to rebuild)." /></legend>
        <div class="row library-tools">
          <${ProjectSelect} projects=${projects} value=${project || ""} onChange=${(p) => { setProject(p); read(p); }} label="Flows of the project" />
          ${coverage && html`<span class="muted small">${count(coverage.matched)} of ${count(coverage.total)} factors on flows of ${coverage.project}</span>`}
        </div>
        ${project && html`
          <div class="row library-add">
            <select value=${kind} onChange=${(e) => setKind(e.target.value)} aria-label="Kind of flow">
              <option value="emission">Emission</option><option value="resource">Resource</option></select>
            <input class="factor-input" value=${value} onInput=${(e) => setValue(e.target.value)} aria-label="Factor of the flow to add" title="The factor of the flow picked" />
            <${NodePicker} key=${`${project}:${kind}`} folder=${folder} project=${project} kind=${kind}
              placeholder=${`Add a factor: search the ${kind === "resource" ? "resources" : "emissions"} of ${project}`} onPick=${added} />
          </div>`}
        ${method.factors.length > 10 && html`<input class="method-filter" placeholder="Filter the factors" value=${filter} onInput=${(e) => setFilter(e.target.value)} aria-label="Filter the factors" />`}
        ${!method.factors.length ? html`<p class="muted small">No factor yet: search a flow above to add one, or import a CSV file.</p>` : html`
          <div class="factor-list"><table class="load-table factor-table">
            <tr><th>Flow</th><th>Compartment</th><th>Unit</th><th>Database</th><th class="num">Factor</th><th></th></tr>
            ${shownRows.map((f) => html`
              <tr key=${`${f.database}/${f.code}`} class=${f.reason ? "muted" : ""} title=${f.reason ? `Not in ${project}: ${f.reason}` : ""}>
                <td>${f.name || f.code}</td><td>${f.categories.join(", ")}</td><td>${unit(f.unit)}</td><td class="muted small">${f.database}</td>
                <td class="num"><${FactorInput} value=${f.cf} label=${`Factor of ${f.name}`} onCommit=${(cf) => change({ set: [{ database: f.database, code: f.code, cf }] })} /></td>
                <td><button class="link" aria-label=${`Remove the factor of ${f.name}`} title="Remove this factor" onClick=${() => change({ remove: [[f.database, f.code]] })}>×</button></td>
              </tr>`)}
          </table>
          ${!all && rows.length > FACTOR_LIMIT && html`<button class="link" onClick=${() => setAll(true)}>Show all ${count(rows.length)}</button>`}</div>`}
      </fieldset>
      ${csv !== null && html`
        <div class="library-csv">
          <label>CSV file, matched to the flows of ${project}: its factors are set in this method
            <${PathInput} value=${csv} onChange=${setCsv} accept=".csv,.CSV,.txt" placeholder="Path of the CSV file" /></label>
          <div class="form-actions"><button class="primary" disabled=${!csv || !project} onClick=${importCsv}>Import</button>
            <button onClick=${() => setCsv(null)}>Cancel</button></div>
        </div>`}
      ${report && html`<div class="library-csv">
        <p>${count(report.matched)} ${report.matched === 1 ? "factor" : "factors"} set${report.unmatched.length ? `; ${count(report.unmatched.length)} rows left out:` : "."}
          <button class="link" onClick=${() => setReport(null)}>Dismiss</button></p>
        ${report.unmatched.length > 0 && html`<${UnmatchedRows} rows=${report.unmatched} />`}</div>`}
      <div class="form-actions">
        <button onClick=${() => { setCsv(""); setReport(null); }} disabled=${!project}>Import CSV…</button>
        <button onClick=${() => saveCsv(id)}>Export CSV</button>
        <button onClick=${() => onForm("duplicate")}>Duplicate…</button>
        <span class="spacer"></span>
        <button class="danger" onClick=${() => onForm("delete")}>Delete…</button>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- create dataset
// A new process, entered as in SimaPro, written into a Brightway project (lca_viewer.editing)
const DATASET_SECTIONS = [
  ["technosphere", "Inputs from technosphere", "process", "Search a process"],
  ["resource", "Inputs from nature", "resource", "Search a resource"],
  ["emission", "Emissions", "emission", "Search an emission and its compartment"],
  ["waste", "Waste to treatment", "process", "Search the process treating the waste"],
  ["avoided", "Avoided products", "process", "Search the process it replaces"],
];
const DOCUMENTATION_FIELDS = [
  ["technology", "Technology"], ["time period", "Time period"], ["geography", "Geography"],
  ["data sources", "Data sources and literature"], ["collection method", "Collection method"],
  ["data treatment", "Data treatment"], ["allocation rules", "Allocation rules"], ["verification", "Verification"],
  ["data entry by", "Data entry by"],
];
const BW_UNITS = ["kilogram", "cubic meter", "kilowatt hour", "megajoule", "unit", "ton kilometer", "square meter",
  "square meter-year", "hectare", "litre", "meter", "kilometer", "hour", "person kilometer", "kilogram day"];
const DRAFT_KEY = "lca_viewer.datasetDraft";
const emptyDraft = () => ({
  project: "", database: "", newDatabase: "",
  process: { name: "", location: "GLO", comment: "", documentation: {} },
  products: [{ name: "", amount: "1", unit: "kilogram", allocation: "100", comment: "" }],
  exchanges: [],
});
// A flow's compartment, a process's location (processes imported from SimaPro have categories too)
const FLOW_NODE_TYPES = ["emission", "natural resource", "economic", "inventory indicator", "social"];
const nodeLabel = (n) => (FLOW_NODE_TYPES.includes(n.type) ? (n.categories || []).join(", ") : n.location);

// Search box over the processes or flows of a project; picking one adds it as an exchange
// The fields the filters of the search of a project can use (what /api/brightway/search reads without opening the datasets)
const NODE_FILTER_FIELDS = { process: ["location", "reference_product", "name", "database"], flow: ["compartment", "subcompartment", "name", "unit", "database"] };

// The search of a process or a flow of the project, for the exchanges of a dataset: words and filters (always shown while
// it is open, as in the search of the top bar), the values of the filters read from the project
function NodePicker({ folder, project, kind, placeholder, onPick }) {
  const flows = kind !== "process";
  const [text, setText] = useState("");
  const [filters, setFilters] = useState(() => emptyQuery(flows ? "compartment" : "location", "eq"));
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState(null);
  const [active, setActive] = useState(0);
  const box = useRef(null);
  const predicate = useMemo(() => toPredicate(filters), [filters]);
  const activeFilters = filters.conditions.filter((c) => c.value.trim()).length;
  const searching = Boolean(text.trim() || predicate);  // the words alone search too
  const source = useMemo(() => ({
    options: (field, q, limit) => serverRequest(`/api/brightway/options?folder=${encodeURIComponent(folder)}&project=${encodeURIComponent(project)}`
      + `&kind=${kind}&field=${field}&q=${encodeURIComponent(q)}&limit=${limit}`),
  }), [folder, project, kind]);
  useEffect(() => {
    const away = (e) => box.current && !box.current.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);
  useEffect(() => {
    if (!open || !searching) { setResults(null); return; }
    let live = true;
    const timer = setTimeout(() => serverRequest(`/api/brightway/search?folder=${encodeURIComponent(folder)}&project=${encodeURIComponent(project)}&kind=${kind}&q=${encodeURIComponent(text)}`
        + (predicate ? `&filters=${encodeURIComponent(JSON.stringify(predicate))}` : ""))
      .then((r) => { if (live) { setResults(r); setActive(0); } }).catch(() => live && setResults([])), 200);
    return () => { live = false; clearTimeout(timer); };
  }, [text, predicate, open, folder, project, kind]);
  useEffect(() => {
    box.current?.querySelector(".search-results .active")?.scrollIntoView({ block: "nearest" });
  }, [active]);
  const pick = (n) => { onPick(n); setText(""); setResults(null); setOpen(false); };
  function onKey(e) {
    if (e.key === "Escape") { setOpen(false); return; }
    if (!results?.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(active + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(active - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); pick(results[active]); }
  }
  return html`
    <div class="node-picker" ref=${box}>
      <input value=${text} placeholder=${placeholder} onInput=${(e) => { setText(e.target.value); setOpen(true); }} onFocus=${() => setOpen(true)}
        onKeyDown=${onKey} disabled=${!project} />
      ${open && html`
        <div class="search-panel">
          <div class="search-filters">
            <${QueryBuilder} query=${filters} setQuery=${setFilters} fields=${NODE_FILTER_FIELDS[flows ? "flow" : "process"]} flows=${flows} source=${source} />
            ${activeFilters > 0 && html`<button onClick=${() => setFilters(emptyQuery(flows ? "compartment" : "location", "eq"))}>Clear filters</button>`}
          </div>
          <div class="search-meta">${!searching ? "Type words, or use the filters above."
            : !results ? "…" : results.length ? `${results.length}${results.length === 40 ? "+" : ""} found · ↑↓ Enter` : "Nothing found"}</div>
          <div class="search-results">
            ${(results || []).map((n, i) => html`
              <div key=${`${n.database}/${n.code}`} class=${i === active ? "active" : ""} onMouseEnter=${() => setActive(i)}
                   onMouseDown=${(e) => { e.preventDefault(); pick(n); }}>
                <div><b>${n.name}</b> <span class="loc-chip">${nodeLabel(n)}</span></div>
                <small>${kind === "process" && n.reference_product ? `${n.reference_product} · ` : ""}${unit(n.unit || "")} · ${n.database}</small>
              </div>`)}
          </div>
        </div>`}
    </div>`;
}

// ---------------------------------------------------------------- editing: shared pieces
// What the pop-ups, the tree and the edit panel need of edit mode: { editing, origin, pending, locked, stale,
// stage(change), openForm(what) }. Outside edit mode only ``editing`` (false) is there.
const EditorContext = createContext({ editing: false, pending: [], locked: [] });
// What a double-click (or Enter) on a row of a process, a flow or a model does in the tab it is in (docs/ui.md, *Row
// clicks*): ``process(index, inTab)``, ``flow(target, inTab)`` (``target``: { flow }), ``model(method, inTab)``. ``inTab``:
// Ctrl/⌘, in a new tab. Given by each tab (``FUView``, ``FlowTab``, ``ModelTab``) to its lists and pop-ups: a process
// becomes the process of a process tab, a flow the flow of a flow tab, a model the model of a model tab; anything else
// opens in a new tab.
const TabUseContext = createContext(null);
const activityOf = (index, method) => request(`/api/activity/${index}?method=${method}`);
// The gestures of a row (the same in the tree, its graph and the lists of the pop-ups): a click selects, a right-click
// opens its pop-up (as Space), a double-click uses it in the tab (as Enter), Ctrl/⌘ + double-click in a new tab (as
// Ctrl/⌘+Enter). ``use(inTab, e)``; the second click does not select the text.
const dblUse = (use) => (use ? {
  onDblClick: (e) => { e.stopPropagation(); use(e.ctrlKey || e.metaKey, e); },
  onMouseDown: (e) => { if (e.detail > 1) e.preventDefault(); },
} : {});
// The tooltip line of a row's name: what its gestures do
const rowHint = (open, use) => [open && "Right-click or Space: its pop-up", use && "Double-click or Enter: use it in this tab (Ctrl/⌘: in a new tab)"]
  .filter(Boolean).join(" · ");
const bwQuery = (o) => `folder=${encodeURIComponent(o.folder)}&project=${encodeURIComponent(o.project)}`;
const datasetUrl = (o, database, code) => `/api/brightway/dataset?${bwQuery(o)}&database=${encodeURIComponent(database)}&code=${encodeURIComponent(code)}`;

// A dataset as the form holds it: amounts are text. A copy has no identity: no ids, no product codes.
// ``id`` (of an exchange, of a product's exchange) lets the server change what is there instead of rewriting it.
function formOf(read, copy = false) {
  return {
    process: { name: copy ? `${read.process.name} (copy)` : read.process.name, location: read.process.location || "GLO",
      comment: read.process.comment || "", documentation: read.process.documentation || {} },
    products: read.products.map((p) => ({ ...(copy ? {} : { code: p.code, id: p.id, processCode: p.process_code }), name: p.name,
      amount: String(p.amount), unit: p.unit, allocation: String(p.allocation), comment: p.comment || "" })),
    exchanges: read.exchanges.map((e) => ({ ...(copy ? {} : { id: e.id }), section: e.section, node: e.node, amount: String(e.amount),
      comment: e.comment || "", sd2: e.sd2 ? String(e.sd2) : "" })),
  };
}

// What the server writes for a form (``code``: the dataset it replaces, if any)
function specOf(draft, target, code) {
  const several = draft.products.length > 1;
  return {
    database: target, new_database: draft.database === "__new__", ...(code ? { code } : {}),
    process: draft.process,
    products: draft.products.map((p) => ({ code: p.code, name: p.name, unit: p.unit, comment: p.comment,
      amount: Number(p.amount), allocation: several ? Number(p.allocation) : 100 })),
    exchanges: draft.exchanges.map((e) => ({ id: e.id, section: e.section, database: e.node.database, code: e.node.code,
      amount: Number(e.amount), comment: e.comment, sd2: e.sd2 ? Number(e.sd2) : null })),
  };
}

// Change one thing in a dataset (any process of it): what is staged for it, or else what the project holds, is
// changed by ``patch(draft, read)`` and staged again. Rejects when the database is locked.
async function quickEdit(ctx, database, code, patch) {
  const read = await serverRequest(datasetUrl(ctx.origin, database, code));
  if (read.locked) throw new Error(`${read.database} is locked: unlock it in Projects to change what it contains`);
  const staged = ctx.pending.find((c) => c.kind === "edit" && c.database === read.database && c.code === read.code);
  const draft = structuredClone(staged?.form || { ...emptyDraft(), project: ctx.origin.project, database: read.database, ...formOf(read) });
  patch(draft, read);
  await ctx.stage({ id: staged?.id, kind: "edit", title: `Edit ${draft.process.name}`, database: read.database, code: read.code,
    spec: specOf(draft, read.database, read.code), form: draft });
}

// The exchange of a dataset's form that a row of a pop-up stands for: the one linked to the same node
// (``input``: [database, code]). ``shown`` is the amount the pop-up shows, which for a process allocated from a
// multi-output one is its share of the dataset's: the ratio of the two is kept when the amount is changed.
function patchExchange(input, shown, text) {
  return (draft) => {
    const value = Number(text);
    if (!Number.isFinite(value)) throw new Error("Give a number");
    const found = draft.exchanges.filter((x) => x.node.database === input[0] && x.node.code === input[1]);
    if (!found.length) throw new Error("This exchange is not in the dataset any more: reopen it");
    const exchange = found.reduce((best, x) => (Math.abs(Math.abs(Number(x.amount)) - Math.abs(shown)) < Math.abs(Math.abs(Number(best.amount)) - Math.abs(shown)) ? x : best));
    const ratio = Math.abs(shown) / (Math.abs(Number(exchange.amount)) || Math.abs(shown) || 1);
    exchange.amount = String((Number(exchange.amount) < 0 ? -1 : 1) * Math.abs(value) / ratio);
  };
}
// The patch of a dataset's form that links the exchange of ``input`` ([database, code]) to another process or flow
function patchSupplier(input, node) {
  return (draft) => {
    const exchange = draft.exchanges.find((x) => x.node.database === input[0] && x.node.code === input[1]);
    if (!exchange) throw new Error("This exchange is not in the dataset any more: reopen it");
    exchange.node = node;
  };
}

// A value that can be changed by double-clicking it in edit mode: ``onCommit(text)`` returns a promise.
// ``edit``: what the input starts with when that differs from what is shown; ``load``: the same, fetched when needed;
// ``format``: how the value is shown (the input has it in full). ``start``: a token (any new object) that opens the
// input without a double-click, ``onStarted`` is then called, whether or not it could open.
function Editable({ value, display, edit, multiline, load, onCommit, disabled, title, format, start, onStarted, className = "" }) {
  const ctx = useContext(EditorContext);
  const [text, setText] = useState(null);  // null: not being edited
  const [shown, setShown] = useState(null);  // what was just staged, until the model is built again
  const input = useRef(null);
  const closing = useRef(false);
  const begin = async (e) => {
    e?.stopPropagation();
    closing.current = false;
    try { setText(load ? await load() : edit ?? String(value ?? "")); } catch (err) { window.alert(err.message); }
  };
  useEffect(() => setShown(null), [value]);
  useEffect(() => { if (text !== null) input.current?.focus(); }, [text === null]);
  useEffect(() => {
    if (!start) return;
    if (ctx.editing && !disabled) begin();
    onStarted?.();
  }, [start]);
  const looks = (v) => (format && Number.isFinite(Number(v)) ? format(Number(v)) : v);
  if (!ctx.editing) return html`<span class=${className}>${display ?? looks(value)}</span>`;
  if (disabled) return html`<span class=${`${className} locked`} title=${title || "Locked: unlock its database in Projects to change it"}>${display ?? looks(value)}</span>`;
  const finish = (commit) => {
    if (closing.current) return;  // Enter or Escape came first: the blur that follows is not a second answer
    closing.current = true;
    const typed = text;
    setText(null);
    if (!commit || typed === null || typed === (edit ?? String(value ?? ""))) return;
    onCommit(typed).then(() => setShown(typed)).catch((err) => window.alert(err.message));
  };
  if (text !== null) {
    const keys = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); finish(false); }
      else if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) { e.preventDefault(); e.stopPropagation(); finish(true); }
      e.stopPropagation();
    };
    const props = { ref: input, value: text, onInput: (e) => setText(e.target.value), onKeyDown: keys, onBlur: () => finish(true),
      onClick: (e) => e.stopPropagation(), onDblClick: (e) => e.stopPropagation() };
    return multiline
      ? html`<textarea class="editing" rows="4" ...${props}></textarea>`
      : html`<input class="editing" size=${Math.max(4, Math.min(40, text.length + 2))} ...${props} />`;
  }
  return html`<span class=${`${className} editable ${shown !== null ? "staged" : ""}`} title=${title || "Double-click to change it"}
    onClick=${(e) => e.detail > 1 && e.stopPropagation()} onDblClick=${begin}>${shown !== null ? looks(shown) : (display ?? looks(value))}</span>`;
}

// ---------------------------------------------------------------- the process form (staged, not written)
// The form of a process: a new one (empty, or a copy of any process of the project) or, with ``edit``
// { database, code }, any dataset of the project. Saving *stages* the change (``onStage``): nothing
// reaches Brightway until the staged changes are applied from the edit panel.
// ``current``: { folder, project, database } the open database was built from; ``initial``: a change staged
// before ({ id, form }) to open again; ``copyFrom``: { database, code } a process to start from;
// ``deleted``: Set of "database/code" of the datasets staged for deletion (they stop using what they use);
// ``staged``: the staged changes (an edit of the same dataset is opened again); ``locked``: locked databases.
function DatasetForm({ onClose, current, edit, initial, copyFrom: start, deleted, staged, locked = [], onStage }) {
  const folder = current.folder;
  const [projects, setProjects] = useState(null);
  const [error, setError] = useState(null);
  const fresh = !edit && !initial && !start;  // a new process from scratch: the form is a draft kept in the browser
  const [draft, setDraft] = useState(() => {
    const base = { ...emptyDraft(), project: current.project, ...(edit ? { database: edit.database } : {}) };
    if (initial?.form) return { ...base, ...initial.form, project: current.project };
    if (!fresh) return base;
    try { return { ...base, ...JSON.parse(localStorage.getItem(DRAFT_KEY) || "null"), project: current.project }; } catch { return base; }
  });
  const [original, setOriginal] = useState(null);  // edit: the dataset as read, with what uses it
  const [template, setTemplate] = useState(null);  // new: the process the form was filled from
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (fresh) try { localStorage.setItem(DRAFT_KEY, JSON.stringify(draft)); } catch { /* storage unavailable */ } }, [draft]);
  useEffect(() => {
    serverRequest(`/api/brightway?folder=${encodeURIComponent(folder)}`).then(setProjects).catch((e) => setError(e.message));
    const onKey = (e) => e.key === "Escape" && !e.target.closest?.(".node-picker, .editing") && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function copyFrom(node) {
    setError(null);
    serverRequest(datasetUrl(current, node.database, node.code)).then((read) => {
      setDraft((d) => ({ ...d, ...formOf(read, true) }));
      setTemplate({ name: read.process.name, database: read.database, warnings: read.warnings });
    }).catch((e) => setError(e.message));
  }
  useEffect(() => {
    if (edit) {
      serverRequest(datasetUrl(current, edit.database, edit.code)).then((read) => {
        if (read.locked) throw new Error(`${read.database} is locked: unlock it in Projects to change what it contains`);
        setOriginal(read);
        // a change staged before (this one, or an edit of the same dataset): its form, not what is in the project
        const before = initial?.form ? initial : (staged || []).find((c) => c.kind === "edit" && c.database === read.database && c.code === read.code);
        setDraft((d) => ({ ...d, ...(before?.form || formOf(read, false)), database: read.database, project: current.project }));
      }).catch((e) => setError(e.message));
    } else if (start) copyFrom(start);
  }, []);

  const usable = (projects?.projects || []).filter((p) => p.twofive);
  const project = usable.find((p) => p.name === current.project);
  const writable = (project?.databases || []).filter((d) => (d.processes || d.backend === "multifunctional") && !locked.includes(d.name));
  const set = (changes) => setDraft((d) => ({ ...d, ...changes }));
  const setProcess = (changes) => set({ process: { ...draft.process, ...changes } });
  const setProduct = (i, changes) => set({ products: draft.products.map((p, k) => (k === i ? { ...p, ...changes } : p)) });
  const [replacing, setReplacing] = useState(null);  // the exchange whose process or flow is being replaced
  const setExchange = (i, changes) => set({ exchanges: draft.exchanges.map((e, k) => (k === i ? { ...e, ...changes } : e)) });
  const several = draft.products.length > 1;
  const shares = draft.products.reduce((s, p) => s + (Number(p.allocation) || 0), 0);
  const target = draft.database === "__new__" ? draft.newDatabase.trim() : draft.database;
  const targetDb = (project?.databases || []).find((d) => d.name === draft.database);
  // What uses the dataset, apart from datasets staged for deletion; a product they use cannot be removed
  const users = (original?.used_by || []).filter((u) => !deleted?.has(`${u.database}/${u.code}`));
  const used = (p) => !!p.code && users.some((u) => u.uses[1] === p.code);
  const problems = [
    !project && "the project is not available",
    !target && "choose a database",
    locked.includes(target) && `${target} is locked`,
    edit && !original && "the dataset is not read yet",
    !draft.process.name.trim() && "name the process",
    draft.products.some((p) => !p.name.trim() || !Number(p.amount)) && "give each product a name and an amount",
    several && Math.abs(shares - 100) > 1e-6 && `the allocation percentages add up to ${shares}, not 100`,
    several && targetDb && targetDb.backend !== "multifunctional" && (edit
      ? `${targetDb.name} cannot hold co-products: copy this dataset into a new database instead`
      : `${targetDb.name} cannot hold co-products: write into a new database`),
    draft.exchanges.some((e) => e.amount === "" || Number.isNaN(Number(e.amount))) && "give each exchange an amount",
    draft.exchanges.some((e) => e.sd2 && !(Number(e.sd2) > 1)) && "SD² must be greater than 1",
  ].filter(Boolean);

  function stage() {
    setBusy(true);
    setError(null);
    onStage({ id: initial?.id, kind: edit ? "edit" : "create", title: `${edit ? "Edit" : "New"} ${draft.process.name.trim()}`,
      database: target, ...(edit && original ? { code: original.code } : {}),
      spec: specOf(draft, target, edit && original ? original.code : null), form: draft })
      .then(() => { if (fresh) try { localStorage.removeItem(DRAFT_KEY); } catch { /* storage unavailable */ } onClose(); })
      .catch((e) => { setBusy(false); setError(e.message); });
  }

  return html`
    <div class="modal-backdrop" onMouseDown=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="load-dialog dataset-dialog" role="dialog" aria-label=${edit ? "Edit a dataset" : "New process"}>
        <div class="info-head">
          <div class="info-title">${edit ? `Edit ${original ? original.process.name : "a dataset"}` : "New process"}</div>
          <button onClick=${onClose} aria-label="Close">×</button>
        </div>
        <div class="load-body">
          ${error && html`<div class="error">${error}</div>`}
          ${edit
            ? null
            : html`
              <section>
                <h3>Save into</h3>
                <div class="dataset-target">
                  <label>Database
                    <select value=${draft.database} onChange=${(e) => set({ database: e.target.value })} disabled=${!project}>
                      <option value="">Choose…</option>
                      ${writable.map((d) => html`<option value=${d.name}>${d.name}${d.backend === "multifunctional" ? "" : " (single-output only)"}</option>`)}
                      <option value="__new__">New database…</option>
                    </select></label>
                  ${draft.database === "__new__" && html`
                    <label>Name of the new database <input value=${draft.newDatabase} placeholder="Name"
                      onInput=${(e) => set({ newDatabase: e.target.value })} /></label>`}
                </div>
                <p class="muted small">Inputs and emissions are searched in project ${current.project}, in all its databases.</p>
              </section>
              <section>
                <h3>Start from <span class="muted">an empty process, or a copy of a process of the project</span></h3>
                <div class="dataset-template">
                  <${NodePicker} folder=${folder} project=${current.project} kind="process"
                    placeholder="Search a process to copy, ecoinvent included…" onPick=${copyFrom} />
                </div>
                ${template && html`
                  <p class="muted small">Filled from <b>${template.name}</b> (${template.database}); that process is not changed, a new one is written.</p>
                  ${template.warnings.length > 0 && html`<ul class="dataset-warnings">${template.warnings.map((w) => html`<li>${w}</li>`)}</ul>`}`}
              </section>`}
          <section>
            <h3>Process</h3>
            <div class="dataset-process">
              <label class="grow">Name <input value=${draft.process.name} onInput=${(e) => setProcess({ name: e.target.value })}
                placeholder="Name" /></label>
              <label>Location <input class="short" value=${draft.process.location} onInput=${(e) => setProcess({ location: e.target.value })}
                placeholder="GLO, FR, RER…" /></label>
            </div>
            <label>Comment <textarea rows="2" value=${draft.process.comment} onInput=${(e) => setProcess({ comment: e.target.value })}
              placeholder="What the process covers, main assumptions"></textarea></label>
            <details>
              <summary>Documentation (${DOCUMENTATION_FIELDS.filter(([k]) => draft.process.documentation[k]).length} of ${DOCUMENTATION_FIELDS.length} filled)</summary>
              <div class="dataset-doc">
                ${DOCUMENTATION_FIELDS.map(([k, label]) => html`
                  <label>${label} <textarea rows="2" value=${draft.process.documentation[k] || ""}
                    onInput=${(e) => setProcess({ documentation: { ...draft.process.documentation, [k]: e.target.value } })}></textarea></label>`)}
              </div>
            </details>
          </section>
          <section>
            <h3>Products <span class="muted">${several ? `co-products, allocated with these percentages (sum: ${shares} %)` : "the reference product"}</span>
              <${InfoTip} text=${edit ? "Applying replaces the process in place: its products keep their identity, so what uses them stays linked. A product another process uses cannot be removed. A co-product can be added to a process with one product." : "The reference product, or co-products with allocation percentages."} label="About products" /></h3>
            <table class="dataset-table">
              <tr><th>Name</th><th class="num">Amount</th><th>Unit</th>${several && html`<th class="num">Allocation %</th>`}<th>Comment</th><th></th></tr>
              ${draft.products.map((p, i) => html`
                <tr key=${p.code || `new${i}`}>
                  <td><input value=${p.name} onInput=${(e) => setProduct(i, { name: e.target.value })} placeholder=${i ? "Co-product" : "Product"} /></td>
                  <td class="num"><input class="num" value=${p.amount} onInput=${(e) => setProduct(i, { amount: e.target.value })} /></td>
                  <td><input list="bw-units" value=${p.unit} onInput=${(e) => setProduct(i, { unit: e.target.value })} /></td>
                  ${several && html`<td class="num"><input class="num" value=${p.allocation} onInput=${(e) => setProduct(i, { allocation: e.target.value })} /></td>`}
                  <td><input value=${p.comment} onInput=${(e) => setProduct(i, { comment: e.target.value })} /></td>
                  <td>${several && html`<button class="link" disabled=${used(p)} aria-label="Remove"
                    title=${used(p) ? "Another dataset uses this product: it cannot be removed" : "Remove this co-product"}
                    onClick=${() => set({ products: draft.products.filter((_, k) => k !== i) })}>×</button>`}</td>
                </tr>`)}
            </table>
            <datalist id="bw-units">${BW_UNITS.map((u) => html`<option value=${u} />`)}</datalist>
            <button class="link" onClick=${() => set({ products: [...draft.products,
              { name: "", amount: "1", unit: draft.products[0].unit, allocation: "0", comment: "" }] })}>+ Co-product</button>
          </section>
          ${DATASET_SECTIONS.map(([section, title, kind, placeholder]) => {
            const rows = draft.exchanges.map((e, i) => [e, i]).filter(([e]) => e.section === section);
            return html`
              <section key=${section}>
                <h3>${title} ${rows.length > 0 && html`<span class="muted">${rows.length}</span>`}</h3>
                ${rows.length > 0 && html`
                  <table class="dataset-table">
                    <tr><th>${kind === "process" ? "Process" : "Flow"}</th><th class="num">Amount</th><th>Unit</th>
                      <th class="num" title="Lognormal uncertainty: squared geometric standard deviation, as in SimaPro (empty: none)">SD²</th><th>Comment</th><th></th></tr>
                    ${rows.map(([e, i]) => html`
                      <tr key=${e.id || `n${i}`}>
                        <td class=${`node ${replacing === i ? "picking" : ""}`} title=${`${e.node.name} | ${nodeLabel(e.node)} | ${e.node.database}`}>
                          ${replacing === i
                            ? html`<${NodePicker} folder=${folder} project=${current.project} kind=${kind}
                                placeholder=${`Replace ${e.node.name} by…`}
                                onPick=${(node) => { setExchange(i, { node }); setReplacing(null); }} />
                              <button class="link" onClick=${() => setReplacing(null)}>cancel</button>`
                            : html`${e.node.name} <span class="loc-chip">${nodeLabel(e.node)}</span>
                              <button class="link replace" aria-label="Replace" onClick=${() => setReplacing(i)}
                                title=${`Link this exchange to another ${kind === "process" ? "process" : "flow"}: its amount, comment and uncertainty stay`}>replace</button>`}</td>
                        <td class="num"><input class="num" value=${e.amount} onInput=${(ev) => setExchange(i, { amount: ev.target.value })} /></td>
                        <td class="muted">${unit(e.node.unit || "")}</td>
                        <td class="num"><input class="num short" value=${e.sd2 || ""} placeholder="—" onInput=${(ev) => setExchange(i, { sd2: ev.target.value })} /></td>
                        <td><input value=${e.comment || ""} onInput=${(ev) => setExchange(i, { comment: ev.target.value })} placeholder="Source, assumption…" /></td>
                        <td><button class="link" aria-label="Remove" onClick=${() => set({ exchanges: draft.exchanges.filter((_, k) => k !== i) })}>×</button></td>
                      </tr>`)}
                  </table>`}
                <${NodePicker} folder=${folder} project=${current.project} kind=${kind} placeholder=${placeholder}
                  onPick=${(node) => set({ exchanges: [...draft.exchanges, { section, node, amount: "", comment: "", sd2: "" }] })} />
              </section>`;
          })}
        </div>
        <div class="dataset-footer">
          <div class="form-actions">
            <span class="muted small">${problems.length ? `To stage: ${problems.join(", ")}.` : ""}</span>
            <span class="spacer"></span>
            ${fresh && html`<button onClick=${() => { if (confirm("Clear this form?")) { setDraft({ ...emptyDraft(), project: current.project, database: draft.database }); setTemplate(null); } }}>Clear</button>`}
            <button class="primary" disabled=${problems.length > 0 || busy} onClick=${stage}
              title=${`Stage: nothing is written yet. Staged changes are made together by Apply, in project ${current.project}; the models of the databases they touch are then built again.`}>${edit || initial ? "Stage the change" : "Stage the new process"}</button>
          </div>
        </div>
      </div>
    </div>`;
}

// The right panel in edit mode: the focused process (edit, copy, delete), the staged changes, and
// "Apply", which makes them together (and builds the open database again if it is affected)
// A window with a panel on the right, as the buttons of VS Code and Obsidian: filled while the panel is shown
function SidePanelIcon({ open, left }) {
  return html`
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3">
      <rect x="1.65" y="2.65" width="12.7" height="10.7" rx="1.6" /><path d=${left ? "M6 2.9v10.2" : "M10 2.9v10.2"} />
      ${open && html`<rect x=${left ? 2.3 : 10} y="3.3" width="3.7" height="9.4" fill="currentColor" stroke="none" />`}
    </svg>`;
}
// The icon of the keyboard shortcuts in the status bar
function PaletteIcon() {
  return html`
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
      <path d="M8 1.8a6.2 6.2 0 1 0 0 12.4c.9 0 1.4-.6 1.4-1.3 0-.9-.8-1.2-.8-2s.6-1.3 1.5-1.3h1.6a2.5 2.5 0 0 0 2.5-2.5C14.2 4.1 11.4 1.8 8 1.8Z" />
      <path d="M4.6 7.4h.01M6.4 4.6h.01M9.6 4.4h.01" stroke-width="2" />
    </svg>`;
}
function KeyboardIcon() {
  return html`
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
      <rect x="1.65" y="3.65" width="12.7" height="8.7" rx="1.6" /><path d="M4.5 6.5h.01M7 6.5h.01M9.5 6.5h.01M12 6.5h.01M4.5 9.5h7" />
    </svg>`;
}

// The source code of the app (status bar): a code glyph
function SourceIcon() {
  return html`
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
      <path d="M5.2 4.4 1.8 8l3.4 3.6M10.8 4.4 14.2 8l-3.4 3.6M9.2 2.8 6.8 13.2" />
    </svg>`;
}
// The project caches and Brightway projects on disk (Projects dialog): a database cylinder
function StorageIcon() {
  return html`
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
      <ellipse cx="8" cy="3.6" rx="5.4" ry="1.9" /><path d="M2.6 3.6v8.8c0 1 2.4 1.9 5.4 1.9s5.4-.9 5.4-1.9V3.6M2.6 8c0 1 2.4 1.9 5.4 1.9s5.4-.9 5.4-1.9" />
    </svg>`;
}

// What the status bar says of the focused card, the object of the active tab of the focused window, from its title:
// a process (``index``), a flow (``flow``) or a model (``model``, the index of its method). Processes and flows are
// named as the tab is, by the *Name display* (``tabTitleNames``), the grey part in brackets
function focusedCardLabel(info, title) {
  if (!title) return null;
  if (title.model !== undefined) {
    const method = info.methods[title.model];
    return method ? `Model · ${methodTitle(method)}` : null;
  }
  const { main, qual } = tabTitleNames(title);
  return `${title.flow !== undefined ? "Flow" : "Process"} · ${main}${qual ? ` (${qual})` : ""}`;
}

// The focused card in the status bar: plain text, what the tab in focus shows (its pop-up opens from its row of the
// Processes panel)
function FocusedCard({ info, title }) {
  const label = focusedCardLabel(info, title);
  if (!label) return null;
  return html`<span class="status-text focused-card" title=${label}>${label}</span>`;
}

function EditPanel({ origin, dataset, pending, affected, locked, outdated, onForm, stage, unstage, unstageAll, onApplied, onRebuild, onCollapse }) {
  const [job, setJob] = useState(null);
  const [showLog, setShowLog] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!job || job.status !== "running") return;
    const timer = setTimeout(() => serverRequest(`/api/jobs/${job.id}`).then((j) => {
      setJob(j);
      if (j.status === "done" && j.key) { savePreference("mode", "view"); openDatabase(j.key); }  // back to the results
      else if (j.status !== "running") onApplied();  // made, not rebuilt: what is out of date now shows
    }).catch((e) => setJob({ ...job, status: "failed", error: e.message })), 700);
    return () => clearTimeout(timer);
  }, [job]);
  const id = (d) => `${d.database}/${d.code}`;
  const deleted = new Set(pending.filter((c) => c.kind === "delete").map(id));
  const staged = dataset && pending.find((c) => c.kind !== "create" && id(c) === id(dataset));
  const users = (dataset?.used_by || []).filter((u) => !deleted.has(id(u)));
  const isLocked = !!dataset && (dataset.locked || locked.includes(dataset.database));
  const attempt = (promise) => { setError(null); promise.catch((e) => setError(e.message)); };
  const apply = (rebuild) => {
    setError(null);
    setShowLog(false);
    serverRequest("/api/brightway/apply", { folder: origin.folder, project: origin.project, database: origin.database, rebuild })
      .then(setJob).catch((e) => setJob({ status: "failed", error: e.message, log: [] }));
  };
  const KIND = { create: "New", edit: "Edit", delete: "Delete" };
  const running = job?.status === "running";
  const hit = affected.includes(origin.database);

  return html`
    <div class="edit-panel">
      <h3 class="edit-title">Edit mode <span class="muted">· project ${origin.project}</span>
        <${InfoTip} text="Changes are staged here, then made together by Apply. Press e on a process to edit it. Impacts are those of the last build, greyed once stale." label="About edit mode" />
        <button class="collapse-side" onClick=${onCollapse} title="Hide the side panel (Ctrl/⌘ + →)" aria-label="Hide the side panel"><${SidePanelIcon} open /></button></h3>
      ${error && html`<div class="error">${error}</div>`}
      ${outdated && html`
        <div class="edit-warning">
          <b>Rebuild required.</b> ${outdated}: the results shown may be wrong.
          <button class="primary" onClick=${onRebuild}>Rebuild and reopen</button>
        </div>`}
      <section>
        <h4>Focused process</h4>
        ${!dataset
          ? html`<p class="muted small">Select a process in the view to edit, copy or delete it.</p>`
          : html`
            <div class="edit-card">
              <div><b>${dataset.process.name}</b> <span class="loc-chip">${dataset.process.location}</span></div>
              <div class="muted small">${dataset.database}${isLocked ? " · 🔒 locked: unlock it in Projects to change it" : ""}
</div>
              ${staged && html`<div class="small">${KIND[staged.kind]} staged</div>`}
              <div class="edit-actions">
                <button disabled=${isLocked || staged?.kind === "delete"}
                  onClick=${() => onForm({ edit: { database: dataset.database, code: dataset.code }, initial: staged?.kind === "edit" ? staged : undefined })}>Edit</button>
                <button onClick=${() => onForm({ copyFrom: { database: dataset.database, code: dataset.code } })}
                  title="A new process that starts as a copy of this one">Copy as new</button>
                ${staged?.kind === "delete"
                  ? html`<button onClick=${() => attempt(unstage(staged.id))}>Undo delete</button>`
                  : html`<button disabled=${isLocked || users.length > 0}
                      title=${isLocked ? "Its database is locked" : users.length ? "Other datasets use it" : "Stage the deletion of this process and its products"}
                      onClick=${() => attempt(stage({ kind: "delete", title: `Delete ${dataset.process.name}`, database: dataset.database, code: dataset.code }))}>Delete</button>`}
              </div>
            </div>`}
        <button onClick=${() => onForm({})}>+ New process</button>
      </section>
      <section>
        <h4>Staged changes <span class="muted">${pending.length}</span></h4>
        ${pending.length === 0 && html`<p class="muted small">Nothing staged.</p>`}
        <ul class="pending-list">
          ${pending.map((c) => html`
            <li key=${c.id}>
              <div class="pending-title"><span class=${`pending-kind ${c.kind}`}>${KIND[c.kind]}</span> ${c.title.replace(/^(New|Edit|Delete) /, "")}${" "}
                <span class="muted small">${c.database}</span></div>
              <div class="edit-actions">
                ${c.kind !== "delete" && html`<button class="link" onClick=${() => onForm({ ...(c.kind === "edit" ? { edit: { database: c.database, code: c.code } } : {}), initial: c })}>Open</button>`}
                <button class="link" onClick=${() => attempt(unstage(c.id))}>Remove</button>
              </div>
            </li>`)}
        </ul>
        ${affected.length > 0 && html`<p class="muted small">Models to build again once these are applied: ${affected.join(", ")}.${hit ? "" : ` ${origin.database} is not one of them.`}</p>`}
      </section>
      <div class="edit-apply">
        ${job && html`
          <div class=${`load-job ${job.status}`}>
            <div class="load-job-head">
              ${running && html`<span class="spinner" aria-hidden="true"></span>`}
              <span>${job.status === "failed" ? html`<b>It failed.</b> ${job.error}` : job.message}</span>
              ${job.log?.length > 0 && html`<button class="link" onClick=${() => setShowLog(!showLog)}>${showLog ? "Hide details" : "Details"}</button>`}
            </div>
            ${showLog && html`<pre class="load-log">${job.log.slice(-40).join("\n")}</pre>`}
          </div>`}
        <div class="form-actions">
          <button disabled=${!pending.length || running} onClick=${() => { if (confirm(`Discard the ${pending.length} staged change${pending.length > 1 ? "s" : ""}?`)) attempt(unstageAll()); }}>Discard all</button>
          <span class="spacer"></span>
          <button disabled=${!pending.length || running} onClick=${() => apply(false)}
            title="Make the staged changes in the project. The models of the databases they affect are then out of date: build them again when you want to see the new results">Apply</button>
          <button class="primary" disabled=${!pending.length || running} onClick=${() => apply(true)}
            title=${`Make the staged changes in the project in one go, then build ${origin.database} again if they affect it (about a minute for ecoinvent) and show it`}>
            Apply ${pending.length || ""} and rebuild</button>
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- tree
// Right-click on the headers of the list: choose its columns (the process is always there)
// ``options``: the columns to choose from (the tree's by default; the Exchanges panel has its own)
function ColumnMenu({ at, columns, onChange, onClose, options = COLUMNS }) {
  const box = useRef(null);
  useEffect(() => {
    const down = (e) => !box.current?.contains(e.target) && onClose();
    const key = (e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key, true);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key, true); };
  }, []);
  const toggle = (id) => onChange(options.map((c) => c.id).filter((x) => (x === id) !== columns.includes(x)));
  const left = Math.max(4, Math.min(at.x, window.innerWidth - 220)), top = Math.max(4, Math.min(at.y, window.innerHeight - 290));
  return html`
    <div class="column-menu" ref=${box} style=${{ left: `${left}px`, top: `${top}px` }} role="menu" aria-label="Columns">
      ${options.map((c, i) => html`
        <${Fragment} key=${c.id}>
          ${c.exchange && !options[i - 1]?.exchange && html`<div class="column-menu-head">Exchange data</div>`}
          <label title=${c.hint}><input type="checkbox" checked=${columns.includes(c.id)} onChange=${() => toggle(c.id)} /> ${c.label}</label>
        <//>`)}
    </div>`;
}

const GROUP_LOCKED = "Grouped by a rule (Rules panel): the group stays while the rule is active. Disable or remove the rule to ungroup.";

function Tree({ rows, rootImpact, shareBasis, selection, onToggle, onSelect, onUngroup, onRegroup, ungroupable, groupLocked, onMergeGroup, onUnpin, onPin, onMoveToOthers, onInfo, onUse, onUnmerge, onRevert, view, focus, renaming, onStartRename, onRename, onColumns }) {
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const [columnMenu, setColumnMenu] = useState(null);  // where the columns menu is open: { x, y }
  const shown = new Set(view.columns);
  const extra = COLUMNS.filter((c) => c.exchange && shown.has(c.id));
  const sections = new Map();  // row id -> the section of the last row seen below it
  const previous = { depth: 0 };  // the depth of the row before
  const span = 1 + COLUMNS.filter((c) => shown.has(c.id)).length;
  const colors = useMemo(() => groupColors(rows, view), [rows, view.groupColors, view.colorBy, paletteStamp]);
  const ctx = useContext(EditorContext);
  const editing = ctx.editing;
  const box = useRef(null);
  const scrolledTo = useRef(null);
  // Scroll once to a row found from elsewhere ("Go to in tree"), when it has been rendered
  useEffect(() => {
    const el = focus && focus !== scrolledTo.current && box.current?.querySelector(`[data-id="${CSS.escape(focus)}"]`);
    if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); scrolledTo.current = focus; }
  }, [focus, rows]);
  if (!rows.length) return html`<div class="empty">Choose a process to start.</div>`;
  return html`
    <div class="tree" ref=${box}>
      <table style=${{ minWidth: `${Math.max(460, 300 + COLUMNS.filter((c) => shown.has(c.id)).reduce((sum, c) => sum + c.width, 0))}px` }}>
        <colgroup><col />${COLUMNS.filter((c) => shown.has(c.id)).map((c) => html`<col key=${c.id} style=${{ width: `${c.width}px` }} />`)}</colgroup>
        <thead title="Right-click to choose the columns" onContextMenu=${(e) => { e.preventDefault(); setColumnMenu({ x: e.clientX, y: e.clientY }); }}><tr><th>Product</th>
          ${shown.has("amount") && html`<th class="num">Amount</th>`}${shown.has("impact") && html`<th class="num">Impact</th>`}
          ${shown.has("share") && html`<th title="Switched with the cut-off basis (total | parent)">Share of ${shareBasis === "parent" ? "parent" : "total"}</th>`}
          ${extra.map((c) => html`<th key=${c.id} title=${c.hint}>${c.label}</th>`)}</tr></thead>
        <tbody>
          ${rows.map((r) => {
            // Organise by: a heading where the section of the rows below a row changes
            // (again when the rows come back from the rows below a sibling)
            const section = r.depth > 0 ? sectionOf(r, view) : null;
            const returned = previous.depth > r.depth;
            previous.depth = r.depth;
            const heading = section && (sections.get(r.parent) !== section.key || returned) && (sections.set(r.parent, section.key), section.label);
            const share = shareOf(r, byId, rootImpact, shareBasis);
            const amount = rowAmount(r);
            const [product, name, location] = r.kind === "process" ? r.label.split(" | ") : [r.label];
            return html`
              <${Fragment} key=${r.id}>
              ${heading && html`<tr class="section"><td colspan=${span} style=${{ "--depth": r.depth }}><span style=${{ paddingLeft: `${r.depth * 16 + 18}px` }}>${heading}</span></td></tr>`}
              <tr data-id=${r.id} ...${dragProcess(r.kind === "process" || r.kind === "direct" ? r.meta?.index : null)} class="${r.kind} ${selection.has(r.id) ? "selected" : ""} ${focus === r.id ? "focus" : ""} ${colors.has(r.id) ? "in-group" : ""}"
                  style=${colors.has(r.id) ? { "--group-color": colors.get(r.id) } : null}
                  onClick=${(e) => onSelect(r, e.ctrlKey || e.metaKey || e.shiftKey)}
                  ...${dblUse(onUse && ((inTab) => onUse(r, inTab)))}
                  onContextMenu=${(e) => onInfo(r, e)}>
                <td style=${{ "--depth": r.depth }}>
                  ${colors.has(r.id) && colorModeOf(view) === "type" && flowTypeOf(r) && html`<span class="type-bar" title=${`${FLOW_TYPES[flowTypeOf(r)].label}: ${FLOW_TYPES[flowTypeOf(r)].hint}`}></span>`}
                  <div class="name" style=${{ paddingLeft: `${r.depth * 16}px` }}>
                    ${r.expandable
                      ? html`<button class="toggle" aria-label=${r.expanded ? "Collapse" : "Expand"}
                               onClick=${(e) => { e.stopPropagation(); onToggle(r); }}>${r.expanded ? "▾" : "▸"}</button>`
                      : html`<span class="toggle"></span>`}
                    ${(r.meta?.multifunctional || r.metas?.some((m) => m.multifunctional)) && html`
                      <span class="mf-badge" title="Co-product: allocated from a multi-output process (its inputs and emissions are a share of the dataset's)">M</span>`}
                    ${renaming === r.id
                      ? html`<${RenameInput} value=${rowNames(r, byId, view).main} renamed=${!!r.custom} onDone=${(name) => onRename(r, name)} />`
                      : html`<span class="label" title=${`${r.custom ? `${r.custom} — renamed from ${r.label}` : r.label}${r.kind === "process" || r.kind === "flow" ? `\n${rowHint(true, !!onUse)}` : ""}`}>
                          ${(() => {
                            const n = rowNames(r, byId, view);
                            return html`${n.main}${n.qual && html` <span class="loc">${n.qual}</span>`}`;
                          })()}
                        </span>`}
                    <span class=${`row-actions ${renaming === r.id ? "renaming" : ""}`}>
                      ${hasDetails(r) && html`
                        <button class="info-btn" title="Comment and uncertainty of the exchange; the activity: right-click" aria-label="Details"
                          onClick=${(e) => { e.stopPropagation(); onInfo(r, e, true); }}>i</button>`}
                      ${labelKey(r) && !editing && html`
                        <button class="rename-btn" title="Rename in this view — the database is not changed" aria-label="Rename"
                          onClick=${(e) => { e.stopPropagation(); onStartRename(r); }}>✎</button>`}
                      ${r.kind === "group" && ungroupable(r) && html`
                        <button class="chip" onClick=${(e) => { e.stopPropagation(); onUngroup(r); }}>ungroup</button>`}
                      ${groupLocked(r) && html`
                        <button class="chip locked" aria-disabled="true" title=${GROUP_LOCKED} onClick=${(e) => e.stopPropagation()}>ungroup</button>`}
                      ${r.regroup && html`
                        <button class="chip" title="Put back in the group by product it was ungrouped from"
                          onClick=${(e) => { e.stopPropagation(); onRegroup(r.regroup); }}>regroup</button>`}
                      ${r.kind === "group" && r.products && !editing && html`
                        <button class="chip merge" title="Merge into one process whose inventory is the union of the members'"
                          onClick=${(e) => { e.stopPropagation(); onMergeGroup(r); }}>merge</button>`}
                      ${r.kind === "merged" && !editing && html`<button class="chip merge" title="Split back into the original processes"
                          onClick=${(e) => { e.stopPropagation(); onUnmerge(r); }}>merged ×</button>`}
                      ${r.pinned && html`
                        <button class="chip pin" title="Restored from others, shown despite the cut-off — click to move it back to others"
                          onClick=${(e) => { e.stopPropagation(); onUnpin(r); }}>restored ×</button>`}
                      ${r.replaced && html`
                        <button class="chip merge" title="Supplied by another activity — back to the original"
                          onClick=${(e) => { e.stopPropagation(); onRevert(r); }}>replaced ×</button>`}
                      ${movable(r) && (inOthers(r)
                        ? html`<button class="chip pin" title="Restore: move it back from others to the main section, shown in the tree and the chart despite the cut-off"
                            onClick=${(e) => { e.stopPropagation(); onPin([r.id]); }}>restore</button>`
                        : html`<button class="to-others" title="Move to others (out of the tree and the chart)" aria-label="Move to others"
                            onClick=${(e) => { e.stopPropagation(); onMoveToOthers([r.id]); }}>×</button>`)}
                    </span>
                  </div>
                </td>
                ${shown.has("amount") && html`<td class="num" title=${amount}>${amount}</td>`}
                ${shown.has("impact") && html`<td class="num impact">${fmt(r.impacts[0])}</td>`}
                ${shown.has("share") && html`<td class="impact">
                  <div class="share">
                    <div class="track"><div class="fill ${share < 0 ? "neg" : ""}"
                      style=${{ width: `${Math.min(100, Math.abs(share) * 100)}%` }}></div></div>
                    <span>${pct(share)}</span>
                  </div>
                </td>`}
                ${extra.map((c) => html`<td key=${c.id} class="comment" title=${columnText(c, r)}>${columnText(c, r)}</td>`)}
              </tr>
              <//>`;
          })}
        </tbody>
      </table>
      ${columnMenu && html`<${ColumnMenu} at=${columnMenu} columns=${view.columns} onChange=${onColumns} onClose=${() => setColumnMenu(null)} />`}
    </div>`;
}

// ---------------------------------------------------------------- tree graph (nodes and edges)
const BOX = { w: 210, h: 58 };
const GAP = {
  horizontal: { depth: 70, slot: 14 }, // root on the left, inputs to the right
  vertical: { depth: 56, slot: 16 }, // root on top, inputs below
};

// Compact tidy tree: a parent is centred on its children, and a subtree is pushed along the
// slot axis only as far as needed not to meet a node of the subtrees before it *at the same
// depth* — so nodes at different depths share a slot (a row, in the horizontal display) and
// a depth holds one node per slot. Sets x, y and depth on each node reachable from ``root``
// through ``childrenOf``.
function tidyLayout(root, childrenOf, orientation) {
  const gap = GAP[orientation];
  const depthStep = (orientation === "horizontal" ? BOX.w : BOX.h) + gap.depth;
  const slotStep = (orientation === "horizontal" ? BOX.h : BOX.w) + gap.slot;
  // Slot of each node relative to its parent; returns the subtree's contour: the lowest and
  // highest slot at each depth below the node, relative to the node (index 0 is the node)
  function pack(node) {
    const kids = childrenOf(node);
    if (!kids.length) return { lo: [0], hi: [0] };
    const lo = [], hi = [], shifts = [];
    kids.forEach((kid, i) => {
      const c = pack(kid);
      let shift = 0;
      if (i) for (let d = 0; d < Math.min(c.lo.length, hi.length); d++) shift = Math.max(shift, hi[d] + 1 - c.lo[d]);
      shifts.push(shift);
      c.lo.forEach((v, d) => { if (d >= lo.length) lo.push(v + shift); });
      c.hi.forEach((v, d) => { if (d >= hi.length || v + shift > hi[d]) hi[d] = v + shift; });
    });
    const mid = shifts[shifts.length - 1] / 2;
    kids.forEach((kid, i) => { kid.rel = shifts[i] - mid; });
    return { lo: [0, ...lo.map((v) => v - mid)], hi: [0, ...hi.map((v) => v - mid)] };
  }
  pack(root);
  const placed = [];
  (function assign(node, depth, slot) {
    node.depth = depth;
    node.slot = slot;
    placed.push(node);
    for (const kid of childrenOf(node)) assign(kid, depth + 1, slot + kid.rel);
  })(root, 0, 0);
  const first = Math.min(...placed.map((n) => n.slot));
  for (const n of placed) {
    const slot = n.slot - first;
    [n.x, n.y] = orientation === "horizontal" ? [n.depth * depthStep, slot * slotStep] : [slot * slotStep, n.depth * depthStep];
  }
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

const SANKEY = { height: 480, bar: 12, column: 300, gap: 6, label: 30 };

// Sankey: a column per depth, each row a bar whose height is its |impact| (``height`` px
// for the root), joined to its parent by a band of the same thickness. A subtree takes
// the height of its bar, of its children stacked, or of two label lines, whichever is
// largest; the bar is centred on it.
function layoutSankey(rows, rootImpact, share, ofTotal) {
  const scale = rootImpact ? SANKEY.height / Math.abs(rootImpact) : 0;
  const nodes = rows.map((row) => ({ key: row.id, row, rows: [row], share: share(row), w: SANKEY.bar,
    t: Math.max(1.5, Math.abs(row.impacts[0]) * scale) }));
  const byId = new Map(nodes.map((n) => [n.key, n]));
  const children = new Map(nodes.map((n) => [n, []]));
  for (const n of nodes) byId.has(n.row.parent) && children.get(byId.get(n.row.parent)).push(n);
  const stacked = (kids, size) => kids.reduce((sum, k) => sum + size(k), 0) + SANKEY.gap * Math.max(0, kids.length - 1);
  function measure(n) {
    const kids = children.get(n);
    kids.forEach(measure);
    n.h = Math.max(n.t, kids.reduce((sum, k) => sum + k.t, 0));
    n.span = Math.max(n.h, SANKEY.label, stacked(kids, (k) => k.span));
  }
  function place(n, top, depth) {
    [n.x, n.y, n.depth] = [depth * SANKEY.column, top + (n.span - n.h) / 2, depth];
    const kids = children.get(n);
    let y = top + (n.span - stacked(kids, (k) => k.span)) / 2;
    for (const k of kids) { place(k, y, depth + 1); y += k.span + SANKEY.gap; }
  }
  measure(nodes[0]);
  place(nodes[0], 0, 0);
  // Bands leave the parent's bar stacked in order, centred on it, and enter the child's bar centred
  const edges = [];
  for (const [from, kids] of children) {
    let y1 = from.y + (from.h - kids.reduce((sum, k) => sum + k.t, 0)) / 2;
    for (const to of kids) {
      edges.push({ key: to.key, from, to, rows: [to.row], share: ofTotal(to.row), y1, y2: to.y + (to.h - to.t) / 2, t: to.t });
      y1 += to.t;
    }
  }
  const width = Math.max(...nodes.map((n) => n.x)) + SANKEY.column;
  return { nodes, edges, width, height: nodes[0].span };
}

function bandPath({ from, to, y1, y2, t }) {
  const x1 = from.x + SANKEY.bar, x2 = to.x, xm = (x1 + x2) / 2;
  return `M${x1},${y1} C${xm},${y1} ${xm},${y2} ${x2},${y2} L${x2},${y2 + t} C${xm},${y2 + t} ${xm},${y1 + t} ${x1},${y1 + t} Z`;
}

// Tooltip of a box or a Sankey bar
function nodeTitle(r, s, places) {
  return `${r.label}\n${fmt(r.impacts[0])} (${pct(s)})${places.length > 1 ? `\nIn ${places.length} places in the tree: amounts summed, loops counted once` : ""}`
    + (r.kind === "process" || r.kind === "flow" ? `\n${rowHint(true, true)}` : "");
}

// The colour of a row (``colors``: see groupColors) is --mark, which the boxes, edges, bars and bands are drawn with; a
// group's colour also overrides --group, which its name is drawn with
const markStyle = (colors, r) => (colors.has(r.id)
  ? { "--mark": colors.get(r.id), ...(r.kind === "group" && { "--group": colors.get(r.id) }) } : null);
const coloredClass = (colors, r) => (colors.has(r.id) ? "colored" : "");

function sankeyMarks({ graph, byId, view, colors, selection, focus, onSelect, onToggle, onInfo, onUse }) {
  return html`
    ${graph.edges.map((e) => html`
      <path key=${`b-${e.key}`} class="band ${e.share < 0 ? "neg" : ""} ${e.to.row.kind} ${coloredClass(colors, e.to.row)}" d=${bandPath(e)} style=${markStyle(colors, e.to.row)}>
        <title>${rowNames(e.to.row, byId, view).main} → ${rowNames(e.from.row, byId, view).main}\n${fmt(e.to.row.impacts[0])} (${pct(e.share)} of the total)</title>
      </path>`)}
    ${graph.nodes.map(({ key, row: r, share: s, x, y, h }) => {
      const names = rowNames(r, byId, view);
      const detail = [names.qual && truncate(names.qual, 24), rowAmount(r), pct(s)].filter(Boolean).join(" · ");
      const cy = y + h / 2, lx = x + SANKEY.bar + 6;
      return html`
        <g key=${key} data-id=${r.id} class="snode ${r.kind} ${selection.has(r.id) ? "selected" : ""} ${focus === r.id ? "focus" : ""} ${r.impacts[0] < 0 ? "neg" : ""} ${coloredClass(colors, r)}"
           style=${markStyle(colors, r)}
           onClick=${(e) => onSelect(r, e.ctrlKey || e.metaKey || e.shiftKey)}
           ...${dblUse(onUse && ((inTab) => onUse(r, inTab)))}
           onContextMenu=${(e) => onInfo(r, e)}>
          <title>${nodeTitle(r, s, [r])}</title>
          <rect x=${x} y=${y} width=${SANKEY.bar} height=${h} />
          ${r.expandable && html`
            <text x=${lx} y=${cy - 2} class="caret" onClick=${(e) => { e.stopPropagation(); onToggle(r); }}>${r.expanded ? "▾" : "▸"}</text>`}
          <text x=${lx + 13} y=${cy - 2} class="name">${truncate(names.main, 34)}</text>
          <text x=${lx + 13} y=${cy + 11} class="detail">${detail}</text>
        </g>`;
    })}`;
}

function TreeGraph({ rows, rootImpact, shareBasis, selection, onToggle, onSelect, onUngroup, onRegroup, ungroupable, groupLocked, onMergeGroup, onUnpin, onPin, onMoveToOthers, onInfo, onUse, onUnmerge, view, orientation: chosenOrientation, setOrientation, cycles: chosenCycles, setCycles, sankey, focus }) {
  const { editing } = useContext(EditorContext);
  // The Sankey is a tree drawn left to right
  const orientation = sankey ? "horizontal" : chosenOrientation;
  const cycles = chosenCycles && !sankey;
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const colors = useMemo(() => groupColors(rows, view), [rows, view.groupColors, view.colorBy, paletteStamp]);
  const container = useRef(null);
  const drag = useRef(null);
  const [transform, setTransform] = useState({ k: 1, x: 16, y: 16 });
  // Boxes show the share of the total or of the parent; edges always the share of the
  // total, so that the main supply paths stand out
  const graph = useMemo(() => {
    if (!rows.length) return { nodes: [], edges: [] };
    if (cycles) return layoutGraph(rows, orientation, rootImpact, shareBasis);
    const ofTotal = (r) => (rootImpact ? r.impacts[0] / rootImpact : 0);
    const share = (r) => shareOf(r, byId, rootImpact, shareBasis);
    return sankey ? layoutSankey(rows, rootImpact, share, ofTotal) : layoutTree(rows, orientation, share, ofTotal);
  }, [rows, orientation, cycles, sankey, rootImpact, shareBasis, byId]);
  const size = (n) => ({ w: n.w ?? BOX.w, h: n.h ?? BOX.h });
  const rootId = rows[0]?.id;

  // Whole tree in view; with ``readable``, never zoom out below 60 % and centre on the root instead
  function fit(readable = false) {
    const el = container.current;
    if (!el || !graph.nodes.length) return;
    const margin = 24;
    const whole = Math.min(1, (el.clientWidth - 2 * margin) / graph.width, (el.clientHeight - 2 * margin) / graph.height);
    const k = readable ? Math.max(whole, 0.6) : Math.max(whole, 0.1);
    const root = graph.nodes.find((n) => n.rows.some((r) => r.id === rootId));
    const { w, h } = size(root);
    setTransform(orientation === "horizontal"
      ? { k, x: margin, y: el.clientHeight / 2 - (root.y + h / 2) * k }
      : { k, x: el.clientWidth / 2 - (root.x + w / 2) * k, y: margin });
  }
  // Re-fit when the process, the orientation or the layout changes, not on every expand
  useEffect(() => fit(true), [rootId, orientation, cycles, sankey]);
  // Centre once on a node found from elsewhere ("Go to in tree"), when it has been laid out
  const centredOn = useRef(null);
  useEffect(() => {
    const node = focus && focus !== centredOn.current && graph.nodes.find((n) => n.rows.some((r) => r.id === focus));
    const el = container.current;
    if (!node || !el) return;
    centredOn.current = focus;
    const { w, h } = size(node);
    setTransform((t) => {
      const k = Math.max(t.k, 0.8);
      return { k, x: el.clientWidth / 2 - (node.x + w / 2) * k, y: el.clientHeight / 2 - (node.y + h / 2) * k };
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
    if (e.target.closest(".node, .snode, .graph-controls")) return;
    drag.current = { x: e.clientX, y: e.clientY, t: transform };
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function onPointerMove(e) {
    const d = drag.current;
    if (d) setTransform({ ...d.t, x: d.t.x + e.clientX - d.x, y: d.t.y + e.clientY - d.y });
  }
  const onPointerUp = () => { drag.current = null; };

  if (!rows.length) return html`<div class="empty">Choose a process to start.</div>`;
  const edgeTitle = ({ from, to, rows: links, share: s, back }) =>
    `${rowNames(to.row, byId, view).main} → ${rowNames(from.row, byId, view).main}${back ? " (loop)" : ""}\n`
    + `${pct(s)} of the total${links.length > 1 ? ` · ${links.length} places in the tree` : ""}`;
  return html`
    <div class="graph" ref=${container} onWheel=${onWheel} onPointerDown=${onPointerDown}
         onPointerMove=${onPointerMove} onPointerUp=${onPointerUp}>
      <div class="graph-controls">
        ${!sankey && html`
          <button class=${cycles ? "primary" : ""} onClick=${() => setCycles(!cycles)}
            title="Show each process once: a process used in several places has several edges into it, and loops become cycles">Cycles</button>
          <button class=${orientation === "horizontal" ? "primary" : ""} onClick=${() => setOrientation("horizontal")}
            title="Root on the left">→</button>
          <button class=${orientation === "vertical" ? "primary" : ""} onClick=${() => setOrientation("vertical")}
            title="Root on top">↓</button>`}
        <button onClick=${() => fit(false)}>Fit</button>
        <button onClick=${() => setTransform((t) => ({ ...t, k: 1 }))}>100 %</button>
      </div>
      <svg width="100%" height="100%">
        <g transform=${`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
          ${sankey ? sankeyMarks({ graph, byId, view, colors, selection, focus, onSelect, onToggle, onInfo, onUse }) : html`
          ${graph.edges.map((e) => html`
            <path key=${`e-${e.key}`} class="edge ${e.share < 0 ? "neg" : ""} ${e.back ? "back" : ""} ${coloredClass(colors, e.to.rows[0])}"
              style=${markStyle(colors, e.to.rows[0])}
              d=${edgePath(e.from, e.to, orientation, e.back)} stroke-width=${1 + 14 * Math.min(1, Math.abs(e.share))}>
              <title>${edgeTitle(e)}</title>
            </path>`)}
          ${graph.nodes.map(({ key, row: r, rows: places, share: s, x, y }) => {
            const location = r.kind === "process" ? r.label.split(" | ")[2] : null;
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
              <g key=${key} data-id=${first.id} class="node ${r.kind} ${places.some((p) => selection.has(p.id)) ? "selected" : ""} ${places.some((p) => focus === p.id) ? "focus" : ""} ${coloredClass(colors, first)}"
                 style=${markStyle(colors, first)}
                 onClick=${(e) => onSelect(first, e.ctrlKey || e.metaKey || e.shiftKey)}
                 ...${dblUse(onUse && ((inTab) => onUse(first, inTab)))}
                 onContextMenu=${(e) => onInfo(first, e)}>
                <title>${nodeTitle(r, s, places)}</title>
                <rect x=${x} y=${y} width=${BOX.w} height=${BOX.h} rx="6" />
                ${colors.has(first.id) && html`<rect x=${x} y=${y} width="5" height=${BOX.h} rx="2" class="mark" />`}
                <text x=${x + 10} y=${y + 17} class="title">${truncate(title, 30)}</text>
                <text x=${x + 10} y=${y + 32} class="subtitle">${truncate(subtitle, 34)}</text>
                <rect x=${x + 10} y=${y + 41} width=${barWidth} height="7" rx="3.5" class="track" />
                <rect x=${x + 10} y=${y + 41} width=${barWidth * Math.min(1, Math.abs(s))} height="7" rx="3.5"
                      class="fill ${s < 0 ? "neg" : ""}" />
                <text x=${x + BOX.w - 10} y=${y + 48} class="share" text-anchor="end">${pct(s)}</text>
                ${r.kind === "group" && ungroupable(r) && html`
                  <text x=${x + BOX.w - 10} y=${y + 17} class="ungroup" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onUngroup(r); }}>ungroup</text>`}
                ${groupLocked(r) && html`
                  <text x=${x + BOX.w - 10} y=${y + 17} class="ungroup locked" text-anchor="end"><title>${GROUP_LOCKED}</title>ungroup</text>`}
                ${r.regroup && html`
                  <text x=${x + BOX.w - 10} y=${y + 17} class="ungroup" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onRegroup(r.regroup); }}>regroup</text>`}
                ${r.kind === "group" && r.products && !editing && html`
                  <text x=${x + BOX.w - (ungroupable(r) ? 62 : 10)} y=${y + 17} class="unmerge" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onMergeGroup(r); }}>merge</text>`}
                ${r.kind === "merged" && !editing && html`
                  <text x=${x + BOX.w - 10} y=${y + 17} class="unmerge" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onUnmerge(r); }}>unmerge</text>`}
                ${r.pinned && html`
                  <text x=${x + BOX.w - 10} y=${y + 17} class="unpin" text-anchor="end"
                        onClick=${(e) => { e.stopPropagation(); onUnpin(r); }}>restored ×</text>`}
                ${movable(r) && (inOthers(r)
                  ? html`
                    <g class="to-others pin" onClick=${(e) => { e.stopPropagation(); onPin([r.id]); }}>
                      <title>Restore: move it back from others to the main section, shown in the tree and the chart despite the cut-off</title>
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
          })}`}
        </g>
      </svg>
      <div class="graph-hint">Drag to pan · wheel to zoom · ${sankey ? "▸" : "±"} to expand · Ctrl/⌘-click to select · right-click: its pop-up · double-click: use it (as Enter)${sankey ? " · band thickness ∝ impact" : ""}</div>
    </div>`;
}

// ---------------------------------------------------------------- bundle pop-ups (others, merged, group)
// Opened like a process's pop-up (right-click, (i), Space). Clicking a member opens its
// own pop-up (← comes back).

// What an "others" row contains (below the cut-off, or moved there by the user);
// ticking a member brings it back into the tree and the chart (pins it).
function OthersPopup({ target, win, rest, rows, rootImpact, cutoff, cutoffBasis, pinned, setPinned, others, setOthers, onMember, view, onClose, onBack }) {
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const parent = byId.get(rest.parent);
  const shownPins = rows.filter((r) => r.parent === rest.parent && r.pinned);
  const items = [...shownPins, ...rest.members].sort((a, b) => Math.abs(b.impacts[0]) - Math.abs(a.impacts[0]));
  const nMoved = rest.members.filter((m) => m.moved).length;
  const nZero = rest.members.filter((m) => !m.moved && m.impacts[0] === 0).length;
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
  const reasons = [nCut && `${nCut} ${cutoffReason(cutoff, cutoffBasis)}`,
    nZero && `${nZero} without impact for this method`, nMoved && `${nMoved} moved here by you`].filter(Boolean).join(", ");
  const things = rest.label.includes("flow") ? "flow" : "process";
  return html`
    <${PopupFrame} target=${target} win=${win} label="Others" className="others-popup" onClose=${onClose} onBack=${onBack}
      title=${html`Others${parent ? html` <span class="muted">under ${shortLabel(parent, byId)}</span>` : ""}`}
      subtitle=${`${reasons} · ${fmt(rest.impacts[0])} (${pct(share(rest))})`}
      footer=${html`
        <button onClick=${() => setShown(rest.members.map((m) => m.id), true)}>Show all</button>
        <button disabled=${!shownPins.length} onClick=${() => setShown(shownPins.map((m) => m.id), false)}>Hide restored</button>`}>
      <${InfoTip} text=${`Tick a ${things} to restore it in the tree and the chart; untick to put it back in others. Click one for its details.`} label="About others" />
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
                  <td><input type="checkbox" checked=${shown} aria-label="Restore: show in tree and chart" title="Restore: show in tree and chart"
                        onChange=${(e) => setShown([r.id], e.target.checked)} /></td>
                  <td class="name-cell" title=${r.label}>${names.main}${names.qual && html` <span class="loc">${names.qual}</span>`}</td>
                  <td class="reason">${r.moved ? "moved" : r.pinned ? "restored" : r.impacts[0] === 0 ? "no impact" : "cut-off"}</td>
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
function BundlePopup({ target, win, row, methodInfo, rootImpact, actions, onPart, onClose, onBack }) {
  const parts = [...row.parts].sort((a, b) => Math.abs(b.impacts[0]) - Math.abs(a.impacts[0]));
  // Amounts only add up (and have a share) when every member delivers the same product
  const sameUnit = !!row.unit;
  const total = row.impacts[0];
  const amountOf = (p) => `${fmtAmount(Math.abs(p.amount))} ${unit(p.unit)}${p.waste ? " treated" : ""}`;
  const things = parts.some((p) => p.index === undefined) ? "flows" : "processes";
  const kind = row.kind === "merged" ? "Merged" : "Group";
  return html`
    <${PopupFrame} target=${target} win=${win} label=${`${kind} row`} className="bundle-details" onClose=${onClose} onBack=${onBack}
      title=${html`${row.custom || row.label} <span class="loc-chip">${kind.toLowerCase()}</span>`}
      subtitle=${`${parts.length} ${things} · ${fmt(total)} ${methodInfo.unit} (${pct(rootImpact ? total / rootImpact : 0)} of the total)`}
      footer=${actions}>
      <${InfoTip} text=${`Each member's amount and impact, and its share of the ${kind.toLowerCase()} row.${sameUnit ? "" : ` The ${things} differ in kind: amounts do not add up.`} Click one for its details.`} label="About the members" />
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
function ResetIcon() {
  return html`
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
      <path d="M2.5 8a5.5 5.5 0 1 0 1.8-4.1" /><path d="M2.5 2.5v3h3" />
    </svg>`;
}
// A padlock, closed or ``open``
function LockIcon({ open }) {
  return html`
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="7" width="10" height="7" rx="1.5" /><path d=${open ? "M5.5 7V5a2.5 2.5 0 0 1 4.9-.7" : "M5.5 7V5a2.5 2.5 0 0 1 5 0v2"} />
    </svg>`;
}
function PlusIcon() {
  return html`
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
      <path d="M8 3v10M3 8h10" />
    </svg>`;
}
function RefreshIcon() {
  return html`
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
      <path d="M13.5 8a5.5 5.5 0 1 1-1.8-4.1" /><path d="M13.5 2.5v3h-3" />
    </svg>`;
}
function DownloadIcon() {
  return html`
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
      <path d="M8 2v8M4.8 7.2 8 10.4l3.2-3.2" /><path d="M2.5 11.5v2h11v-2" />
    </svg>`;
}
// The export of a panel, one icon: the chart as a PNG (legend included) and the table as a CSV file
function ExportMenu({ chart, csv, name = "chart" }) {
  const items = [
    ...(chart ? [{ key: "png", label: "Chart as PNG", title: "The chart as an image, with its legend", run: () => {
      if (!chart.current) return;
      const a = document.createElement("a");
      a.href = chart.current.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: cssVar("--panel") });
      a.download = `${name}.png`;
      a.click();
    } }] : []),
    ...(csv ? [{ key: "csv", label: "Table as CSV", title: "The table as a file", run: csv }] : []),
  ];
  return html`<${PopupMenu} label=${html`<${DownloadIcon} />`} className="icon-plain" title="Export" items=${items} />`;
}
function DiskIcon() {
  return html`
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round">
      <path d="M2.5 2.5h9l2 2v9h-11z" /><path d="M5 2.5v3.5h5V2.5" /><path d="M5 13.5v-4h6v4" />
    </svg>`;
}
function SaveMenu({ disabled, onSave, onLoad, onCopy, onDefaults, icon }) {
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
      <button disabled=${disabled} onClick=${onSave} aria-label="Save the view" title="Save the view: process, tree, settings, rules, methods">${icon ? html`<${DiskIcon} />` : "Save"}</button>
      <button class="split-arrow" onClick=${() => setOpen(!open)} aria-label="Load or copy a view" title="Load a view, copy it" aria-expanded=${open}>▾</button>
      ${open && html`
        <div class="menu" role="menu">
          <button role="menuitem" onClick=${() => { setOpen(false); file.current.click(); }}>Load view…</button>
          <button role="menuitem" disabled=${disabled} onClick=${() => { setOpen(false); onCopy(); }}>Copy view as JSON</button>
          ${onDefaults && html`<button role="menuitem" title="As at a first start: list, no cut-off, nothing grouped, direct emissions organised by compartment, full names, no colours, three columns; climate change as method, its family shown together, the default rules; the left panel closed, the panels' widths fitted to the window"
            onClick=${() => { setOpen(false); onDefaults(); }}>Default settings</button>`}
        </div>`}
      <input type="file" accept=".json,application/json" ref=${file} style="display:none" aria-label="View file"
             onChange=${(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) onLoad(f); }} />
    </span>`;
}

// ---------------------------------------------------------------- view bar (group by, organize, colours)
// The headers say what the buttons are for, so the buttons are one word
// The colours under Color by, at most this many (the Settings pop-up lists them all)
const LEGEND_MAX = 14;
// ``legend``: the colours in use in the tree (colorLegend); ``onPick(key, colour)``: a colour picked; ``onColours``: the Settings pop-up
function ViewBar({ view, setView, fields = [], legend = [], onPick = () => {}, onColours = () => {} }) {
  const set = (patch) => setView({ ...view, ...patch });
  const toggle = (key, label, title, on = !!view[key]) => html`
    <button class=${on ? "primary" : ""} onClick=${() => set({ [key]: !on })} aria-pressed=${on} title=${title}>${label}</button>`;
  // One grouping of the processes and one of the flows (the first would leave nothing to the next): choosing one replaces
  // the other, choosing it again switches it off
  const byProcesses = groupingOf(view).processes, byFlows = groupingOf(view).flows;
  const groupProcesses = (k) => set(processGrouping(byProcesses === k ? null : k));
  const groupFlows = (k) => set(flowGrouping(byFlows === k ? null : k));
  const organize = (key, k) => set({ [key]: view[key] === k ? "none" : k });
  const choice = (on, label, title, run) => html`
    <button class=${on ? "primary" : ""} onClick=${run} aria-pressed=${on} title=${title}>${label}</button>`;
  const item = (on, label, run, title) => ({ key: label, label: `${on ? "✓ " : "   "}${label}`, run, title });
  // The By… menus show what they apply
  const groupMore = [byProcesses && byProcesses !== "product" && fieldLabel(byProcesses, false), ["compartment", "subcompartment"].includes(byFlows) && FLOW_FIELD_LABELS[byFlows]].filter(Boolean);
  const organizeMore = [["location", "activity_type"].includes(view.organize) && ORGANIZE[view.organize].toLowerCase(),
    ["subcompartment", "type"].includes(view.organizeFlows) && `flows: ${ORGANIZE_FLOWS[view.organizeFlows].toLowerCase()}`].filter(Boolean);
  const moreLabel = (chosen) => (chosen.length ? `By ${chosen.join(", ")} ▾` : "By…");
  const colorMode = colorModeOf(view);
  const colorBy = (mode) => set(colorMode === mode ? { groupColors: false } : { colorBy: mode, groupColors: true });
  const colorMore = ["location", "activity_type", "compartment"].includes(colorMode) ? [COLOR_MODES[colorMode].toLowerCase()] : [];
  const shownLegend = legend.slice(0, LEGEND_MAX);
  return html`
    <div class="view-bar">
      <div class="view-group" role="group" aria-label="Organise by">
        <span class="view-group-label">Organise by</span>
        ${choice(view.organize === "type", ORGANIZE.type, ORGANIZE_HINTS.type, () => organize("organize", "type"))}
        ${choice(view.organizeFlows === "compartment", ORGANIZE_FLOWS.compartment, ORGANIZE_FLOWS_HINTS.compartment, () => organize("organizeFlows", "compartment"))}
        <${PopupMenu} label=${moreLabel(organizeMore)} className=${organizeMore.length ? "primary" : ""}
          title="Headings in the list, without grouping: one choice for the processes and one for the direct emissions"
          items=${[
            ...["location", "activity_type"].map((k) => item(view.organize === k, ORGANIZE[k], () => organize("organize", k), ORGANIZE_HINTS[k])),
            ...["subcompartment", "type"].map((k) => item(view.organizeFlows === k, `${ORGANIZE_FLOWS[k]} (flows)`, () => organize("organizeFlows", k), ORGANIZE_FLOWS_HINTS[k])),
          ]} />
      </div>
      <div class="view-group" role="group" aria-label="Group by">
        <span class="view-group-label">Group by</span>
        ${toggle("groupByRules", "Rules", "By your rules (the Rules panel)", view.groupByRules !== false)}
        ${choice(byProcesses === "product", "Product", "Suppliers of one product", () => groupProcesses("product"))}
        ${choice(byFlows === "name", "Substance", "Flows of a substance", () => groupFlows("name"))}
        <${PopupMenu} label=${moreLabel(groupMore)} className=${groupMore.length ? "primary" : ""}
          title="Group by another criterion: location, activity type, compartment… One for the processes and one for the flows: a second one would find nothing left to group"
          items=${[
            ...fields.map((f) => item(byProcesses === f, fieldLabel(f, false), () => groupProcesses(f))),
            ...["compartment", "subcompartment"].map((f) => item(byFlows === f, `${FLOW_FIELD_LABELS[f][0].toUpperCase()}${FLOW_FIELD_LABELS[f].slice(1)} (flows)`, () => groupFlows(f))),
          ]} />
      </div>
      <div class="view-group" role="group" aria-label="Color by">
        <span class="view-group-label">Color by</span>
        ${choice(colorMode === "group", COLOR_MODES.group, COLOR_HINTS.group, () => colorBy("group"))}
        ${choice(colorMode === "type", COLOR_MODES.type, COLOR_HINTS.type, () => colorBy("type"))}
        <${PopupMenu} label=${moreLabel(colorMore)} className=${colorMore.length ? "primary" : ""}
          title="Colour by another criterion: location, activity type, compartment. One at a time"
          items=${["location", "activity_type", "compartment"].map((k) => item(colorMode === k, `${COLOR_MODES[k]}${k === "compartment" ? " (flows)" : ""}`, () => colorBy(k), COLOR_HINTS[k]))} />
      </div>
      ${colorMode !== "none" && html`
        <div class="color-legend" aria-label="Colours in use">
          ${shownLegend.map((c) => html`
            <label key=${c.key} title=${`${c.name}: click to change its colour`}>
              <input type="color" class="swatch" value=${colorHex(c.color)} onInput=${(e) => onPick(c.key, e.target.value)} aria-label=${`Colour of ${c.name}`} />${c.name}</label>`)}
          ${legend.length > LEGEND_MAX && html`<span class="muted">+${legend.length - LEGEND_MAX} more</span>`}
          ${legend.length === 0 && html`<span class="muted">${colorMode === "group" ? "No rule matches these rows." : "Nothing to colour in these rows."}</span>`}
          <button class="link" onClick=${onColours} title="The palette, and every colour picked">Colours…</button>
        </div>`}
      <div class="view-group" role="group" aria-label="Rows">
        <span class="view-group-label">Rows</span>
        ${toggle("hideOthers", "Hide others", "No others rows in the tree")}
        ${toggle("collapseSingletons", "Collapse singletons", "A process made of only one process (a market of one supplier…) shows as that process, down the chain")}
      </div>
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

// A process in a list of a pop-up (used by, alternatives: ``PopupList``'s ``renderRow``): a row like those of the main
// list, which expands. ↑/↓ move between the rows (``popupKeys``), → expands it (or goes to its first child), ← collapses
// it (or goes to the row above it), Space, a right-click or its name opens its pop-up, i or its (i) the exchange it
// stands for, Enter makes it the process.
// ``item``: { index, name, reference_product, location, … }; ``above``: the item it hangs from (the pop-up's process
// for a top row); ``ancestors``: indexes above it (a process already there is a loop: shown, not expanded).
// ``panel``: { columns: the list's (the cells after the name: ``listCell(column, item, above, depth)``), label(item),
// chip?(item, depth), rowClass?(item, depth), load(item, above): Promise of { items, more } (its children, none: it does
// not expand), onOpen(item): right-click, Space, onExchange(item, above): the exchange it stands for (none: no (i)),
// onUse(item, inTab): double-click, Enter (it expands with its ▸ or →), empty }
function ProcessRow({ item, above, depth, ancestors, panel }) {
  const [open, setOpen] = useState(false);
  const [kids, setKids] = useState(null);
  const loop = ancestors.includes(item.index);
  const expandable = !loop && !!panel.load;
  useEffect(() => {
    if (!open || kids || !expandable) return;
    panel.load(item, above).then(setKids).catch(() => setKids({ items: [], more: 0 }));
  }, [open]);
  const exchange = panel.onExchange && !(depth === 0 && !above);
  const keys = (e) => {
    const inTab = (e.ctrlKey || e.metaKey) && !e.altKey && e.key === "Enter";  // Ctrl/⌘+Enter: in a new tab
    if ((e.ctrlKey || e.metaKey || e.altKey) && !inTab) return;
    const row = e.currentTarget;
    const used = () => { e.preventDefault(); e.stopPropagation(); };
    const rows = () => [...row.closest(".info-body").querySelectorAll("[data-nav]")];
    const go = (to) => { to?.focus({ preventScroll: true }); to?.scrollIntoView({ block: "nearest" }); };
    if (e.key === " ") { used(); panel.onOpen(item); }
    else if (e.key === "i") { used(); if (exchange) panel.onExchange(item, above); }
    else if (e.key === "Enter") { used(); panel.onUse(item, inTab); }
    else if (e.key === "ArrowRight" && expandable) {
      if (!open) { used(); setOpen(true); return; }
      const all = rows();
      const next = all[all.indexOf(row) + 1];
      if (next && Number(next.dataset.depth) > depth) { used(); go(next); }
    } else if (e.key === "ArrowLeft") {
      if (open) { used(); setOpen(false); }
      else if (depth > 0) {
        used();
        const all = rows();
        go(all.slice(0, all.indexOf(row)).reverse().find((r) => Number(r.dataset.depth) === depth - 1));
      }
    }
  };
  const span = panel.columns.length;
  return html`<${Fragment}>
    <tr class=${`exchange ${panel.rowClass?.(item, depth) || ""}`} data-nav data-depth=${depth} tabindex="-1" ...${dragProcess(item.index)} onKeyDown=${keys}
        ...${dblUse(panel.onUse && ((inTab) => panel.onUse(item, inTab)))}
        onContextMenu=${(e) => { e.preventDefault(); e.stopPropagation(); panel.onOpen(item, e); }}>
      <${NameCell} depth=${depth} title=${`${item.reference_product ?? ""} | ${item.name} | ${item.location}`} content=${panel.label(item)}
        hint=${rowHint(true, !!panel.onUse)}
        chip=${html`<span class="loc-chip">${item.location}</span>${panel.chip?.(item, depth) || ""}`}
        toggle=${loop
          ? html`<span class="toggle muted" title="Already above this process: a loop">↺</span>`
          : expandable
            ? html`<button class="toggle" aria-label=${open ? "Collapse" : "Expand"} onClick=${(e) => { e.stopPropagation(); setOpen(!open); }}>${open ? "▾" : "▸"}</button>`
            : html`<span class="toggle"></span>`}
        onInfo=${exchange ? (e) => panel.onExchange(item, above, e) : null} />
      ${panel.columns.slice(1).map((c) => listCell(c, item, above, depth))}
    </tr>
    ${open && !kids && html`<tr><td colspan=${span} class="muted">…</td></tr>`}
    ${open && kids && kids.items.length === 0 && html`<tr><td colspan=${span} class="muted">${panel.empty || "Nothing below it."}</td></tr>`}
    ${open && kids && kids.items.map((c, i) => html`
      <${ProcessRow} key=${`${c.index}-${i}`} item=${c} above=${item} depth=${depth + 1} ancestors=${[...ancestors, item.index]} panel=${panel} />`)}
    ${open && kids && kids.more > 0 && html`
      <tr class="more"><td colspan=${span} class="muted">+ ${kids.more} more</td></tr>`}
  <//>`;
}

// ---------------------------------------------------------------- activity info pop-up (right click)
const DETAIL_SECTIONS = [
  ["comment", "Description"], ["product_information", "Product"],
  ["included_activities_start", "Starts with"], ["included_activities_end", "Ends with"],
];

// Esc or a click outside closes a pop-up; Backspace (or ←) goes back to the previous one
// when a link inside it was followed. A pinned pop-up ignores clicks outside, and answers to
// the keys only while the pointer or the focus is on it (several can be open).
function usePopupKeys(box, onClose, onBack, pinned = false) {
  const back = useRef(onBack), pin = useRef(pinned);
  back.current = onBack;
  pin.current = pinned;
  useEffect(() => {
    const mine = () => !pin.current || box.current?.matches(":hover, :focus-within");
    const onKey = (e) => {
      if (!mine()) return;
      if (e.key === "Escape") onClose();
      else if (e.key === "Backspace" && back.current && !e.target.closest?.("input, textarea, select, [contenteditable]")) {
        e.preventDefault();
        back.current();
      }
    };
    const onDown = (e) => !pin.current && box.current && !box.current.contains(e.target) && onClose();
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onDown); };
  }, []);
}
const BackButton = ({ onBack }) => onBack && html`
  <button class="back" onClick=${onBack} title="Back" aria-label="Back">←</button>`;
// The pin of a pop-up's header: filled while it is pinned, an outline when not
const PinIcon = ({ filled = true }) => html`<svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"
  fill=${filled ? "currentColor" : "none"} stroke="currentColor" stroke-width=${filled ? 0 : 1.3} stroke-linejoin="round">
  <path d="M10 1.5L14.5 6l-1.3.6-2.6 2.6.3 3-1.1 1.1-2.9-2.9L3 14.5l-1-1 4.2-4.2-2.9-2.9 1.1-1.1 3 .3 2.6-2.6z" /></svg>`;
const PinButton = ({ pinned, onPin }) => html`
  <button class=${`pin ${pinned ? "active" : ""}`} onClick=${onPin} aria-pressed=${!!pinned} aria-label=${pinned ? "Unpin" : "Pin"}
    title=${pinned ? "Pinned: Esc or a click outside does not close it. Click to unpin" : "Pin: keep it open, to have several pop-ups at once"}><${PinIcon} filled=${!!pinned} /></button>`;

// Pop-up windows: dragged by the header, resized by the edges and corners, pinned to stay open
// beside others. Dropped on a pane's tab list, or on the edge of a pane, a pop-up that can
// (an activity's) opens as a tab there, or in a new pane on that side.
const POPUP_MIN = { width: 300, height: 140 };
const DOCK_EDGE = 56;  // px from the edge of a pane where a dragged pop-up docks
const RESIZE_EDGES = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
let popupZ = 30;  // raised on each click, so that the pop-up last used is on top

// The place a pop-up dropped at (x, y) would open its activity at: { pane, zone, left, top, width,
// height } (the last four: the part of the screen it would take, for the hint), or null
function dockAt(x, y, popup) {
  const under = document.elementsFromPoint(x, y).find((el) => !popup.contains(el));
  const tabs = under?.closest(".pane-tabs");
  const body = under?.closest(".pane-body");
  const r = (tabs || body)?.getBoundingClientRect();
  if (tabs) return { pane: tabs.dataset.pane, zone: "center", left: r.left, top: r.top, width: r.width, height: r.height };
  if (!body) return null;
  const zone = x - r.left < DOCK_EDGE ? "left" : r.right - x < DOCK_EDGE ? "right"
    : y - r.top < DOCK_EDGE ? "top" : r.bottom - y < DOCK_EDGE ? "bottom" : null;
  if (!zone) return null;
  const across = zone === "left" || zone === "right";
  return { pane: body.dataset.pane, zone,
    left: zone === "right" ? r.left + r.width / 2 : r.left, top: zone === "bottom" ? r.top + r.height / 2 : r.top,
    width: across ? r.width / 2 : r.width, height: across ? r.height : r.height / 2 };
}

// The ⋯ menu at the top right of a pop-up: ``items``: [{ label, run, title, disabled }]
// The icon of an options menu (three dots)
function GearIcon() {
  return html`
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
      <circle cx="3" cy="8" r="1.4" /><circle cx="8" cy="8" r="1.4" /><circle cx="13" cy="8" r="1.4" />
    </svg>`;
}
function PopupMenu({ items, label = "⋯", title = "Actions of this pop-up", className = "menu-btn" }) {
  const [at, setAt] = useState(null);
  const box = useRef(null);
  useEffect(() => {
    if (!at) return;
    const away = (e) => !box.current?.contains(e.target) && setAt(null);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [at]);
  // Below the button, or above it when the window has no room for the items below (a button at the foot of a panel)
  const open = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const up = window.innerHeight - r.bottom < items.length * 32 + 16 && r.top > window.innerHeight - r.bottom;
    setAt(at ? null : { ...(up ? { bottom: window.innerHeight - r.top + 4 } : { top: r.bottom + 4 }), left: Math.max(8, r.right - 190) });
  };
  return html`
    <span class="popup-menu" ref=${box}>
      <button class=${className} onClick=${open} aria-label=${title} aria-expanded=${!!at} title=${title}>${label}</button>
      ${at && html`<div class="menu" role="menu" style=${{ position: "fixed", ...(at.bottom !== undefined ? { top: "auto", bottom: `${at.bottom}px` } : { top: `${at.top}px` }),
        left: `${at.left}px`, right: "auto", minWidth: "190px" }}>
        ${items.map((it) => html`<button role="menuitem" key=${it.key ?? it.label} class=${it.danger ? "danger" : ""} disabled=${it.disabled} title=${it.title}
          onClick=${() => { setAt(null); it.run(); }}>${it.label}</button>`)}
      </div>`}
    </span>`;
}

// ``win``: { rect, pinned, onPin, onRect } (kept by the view, so that a pop-up keeps its place and
// size when a link inside it opens another kind of pop-up); ``head``: the title lines; the children
// follow the header. ``onDock(place)``: given when the pop-up can open as a tab.
function PopupWindow({ target, win, label, className = "", head, width = 560, menu = [], onClose, onBack, onDock, onKeyDown, children }) {
  const box = useRef(null);
  usePopupKeys(box, onClose, onBack, win.pinned);
  // A pop-up that opens takes the keyboard (arrows, Space, c: see popupKeys)
  useEffect(() => { if (!win.pinned) box.current?.focus({ preventScroll: true }); }, []);
  const [live, setLive] = useState(null);  // the geometry while it is dragged or resized
  const [dock, setDock] = useState(null);  // where it would dock, while it is dragged over a pane
  const w = Math.min(width, window.innerWidth - 24);
  const rect = live || win.rect || {
    left: Math.max(12, Math.min(target.x + 8, window.innerWidth - w - 12)),
    top: Math.max(12, Math.min(target.y + 8, window.innerHeight - 320)), width: w, height: null,
  };
  const left = Math.max(80 - rect.width, Math.min(rect.left, window.innerWidth - 80));
  const top = Math.max(0, Math.min(rect.top, window.innerHeight - 40));

  // mode: "move" (by the header) or the edges being dragged ("e", "nw", …)
  function begin(e, mode) {
    e.preventDefault();
    const at = box.current.getBoundingClientRect();
    const from = { x: e.clientX, y: e.clientY, left: at.left, top: at.top, width: at.width, height: at.height };
    let last = null;
    const move = (ev) => {
      const dx = ev.clientX - from.x, dy = ev.clientY - from.y;
      const next = { left: from.left, top: from.top, width: from.width, height: rect.height };
      if (mode === "move") {
        next.left += dx;
        next.top += dy;
      } else {
        if (mode.includes("e")) next.width = Math.max(POPUP_MIN.width, from.width + dx);
        if (mode.includes("w")) { next.width = Math.max(POPUP_MIN.width, from.width - dx); next.left = from.left + from.width - next.width; }
        if (mode.includes("s")) next.height = Math.max(POPUP_MIN.height, from.height + dy);
        if (mode.includes("n")) { next.height = Math.max(POPUP_MIN.height, from.height - dy); next.top = from.top + from.height - next.height; }
      }
      last = next;
      setLive(next);
      if (mode === "move" && onDock) setDock(dockAt(ev.clientX, ev.clientY, box.current));
    };
    const up = (ev) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      const place = last && mode === "move" && onDock ? dockAt(ev.clientX, ev.clientY, box.current) : null;
      setLive(null);
      setDock(null);
      if (place) onDock(place);
      else if (last) win.onRect(last);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return html`
    <${Fragment}>
      <div class=${`info-popup ${className} ${win.pinned ? "pinned" : ""} ${live ? "moving" : ""}`} ref=${box} role="dialog" aria-label=${label}
           tabindex="-1" onKeyDown=${onKeyDown}
           style=${{ left: `${left}px`, top: `${top}px`, width: `${rect.width}px`, height: rect.height ? `${rect.height}px` : undefined,
             maxHeight: `max(160px, calc(100vh - ${top + 12}px))` }}
           onPointerDownCapture=${() => { box.current.style.zIndex = ++popupZ; }}
           onPointerDown=${(e) => e.button === 0 && e.target.closest(".info-head") && !e.target.closest("button, a, input, select, textarea") && begin(e, "move")}>
        <div class="info-head" title="Drag to move">
          <div>${head}</div>
          <span class="info-head-actions">
            <${BackButton} onBack=${onBack} />
            ${menu.length > 0 && html`<${PopupMenu} items=${menu} />`}
            ${win.onPin && html`<${PinButton} pinned=${win.pinned} onPin=${win.onPin} />`}
            <button onClick=${onClose} aria-label="Close" title="Close (Esc)">×</button>
          </span>
        </div>
        ${children}
        ${RESIZE_EDGES.map((edge) => html`<div key=${edge} class=${`pop-resize ${edge}`} onPointerDown=${(e) => e.button === 0 && begin(e, edge)}></div>`)}
      </div>
      ${dock && html`<div class="popup-dock" style=${{ left: `${dock.left}px`, top: `${dock.top}px`, width: `${dock.width}px`, height: `${dock.height}px` }}>
        <span>${dock.zone === "center" ? "Open as a tab here" : "Open in a new pane here"}</span></div>`}
    <//>`;
}

// Keys inside a pop-up that has the focus (the main view's keys do not reach it): ←/→ switch
// panel, ↑/↓ move between the rows of the panel (``[data-nav]``, focusable; each row handles
// its own Space, c, Enter and ←/→ — see ``ProcessRow`` — and ←/→ reach here only when a row
// does not use them), or scroll a panel without rows; ↑ on the first row goes back to the
// panels' tabs (``toTabs``: a docked card, its active tab; else the pop-up itself); Space or c with no row closes it.
function popupKeys({ tabs, shown, setTab, body, onClose, exchange, explorerKeys, toTabs }) {
  return (e) => {
    const box = e.currentTarget;
    const inTab = (e.ctrlKey || e.metaKey) && !e.altKey && e.key === "Enter";  // for the explorer's selected process
    if (((e.ctrlKey || e.metaKey || e.altKey) && !inTab) || e.target.closest("input, textarea, select, [contenteditable]")) return;
    // A button clicked last (a panel) would also react to Space
    if (e.target !== box && e.target.matches("button")) box.focus();
    const used = () => { e.preventDefault(); e.stopPropagation(); };
    if (explorerKeys?.current?.(e)) { used(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const down = e.key === "ArrowDown";
      const rows = [...box.querySelectorAll(".info-body [data-nav]")];
      used();
      if (!rows.length) { body.current?.scrollBy(0, down ? 48 : -48); return; }
      const at = rows.indexOf(document.activeElement);
      if (at === 0 && !down) { if (toTabs) toTabs(); else box.focus({ preventScroll: true }); body.current?.scrollTo(0, 0); return; }
      const next = rows[at < 0 ? (down ? 0 : rows.length - 1) : at + (down ? 1 : -1)];
      next?.focus({ preventScroll: true });
      next?.scrollIntoView({ block: "nearest" });
    } else if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && tabs.length > 1) {
      used();
      const next = tabs[tabs.findIndex((t) => t.id === shown) + (e.key === "ArrowRight" ? 1 : -1)];
      if (next) { box.focus(); setTab(next.id); }
    } else if (e.key === " " || (exchange && e.key === "i")) {
      used();
      onClose();
    }
  };
}

// Pop-up chrome: header (title, pin, ←, ×), scrolling body, actions
function PopupFrame({ target, win, label, className = "", title, subtitle, footer, onClose, onBack, children, maxWidth = 620 }) {
  return html`
    <${PopupWindow} target=${target} win=${win} label=${label} className=${className} width=${maxWidth} onClose=${onClose} onBack=${onBack}
      head=${html`
        <div class="info-title">${title}</div>
        ${subtitle && html`<div class="muted">${subtitle}</div>`}`}>
      <div class="info-body">${children}</div>
      ${footer && html`<div class="info-actions">${footer}</div>`}
    <//>`;
}

// ---------------------------------------------------------------- flows
// Words (see docs/ui.md): a **flow** is the elementary flow itself, a node of the biosphere (no quantity: its factors,
// who emits it); an **exchange** is a flow in one dataset (an amount per unit of the process, comment, uncertainty);
// an **inventory flow** is a flow in a supply chain (B·s): a flow seen with a scope, the chip of the flow's panels.
// A flow target: { flow, flows?: several flows of one substance (compartment chips), labels?, activities?: the
// processes whose exchange it is, exchange?: i (the exchange only), tab?: the panel to open on, scope?: { index, name,
// location, amount }: the process (functional unit) of the tab it was opened from }

// The icon of a flow (a drop), in the colour of its type: resource (from nature) or emission (to nature)
function FlowIcon({ resource, size = 12 }) {
  const t = FLOW_TYPES[resource ? "resource" : "emission"];
  return html`<svg class="flow-icon" width=${size} height=${size} viewBox="0 0 12 12" aria-label=${t.label} role="img" style=${{ color: t.color }}>
    <title>${`Flow (${t.label.toLowerCase()})`}</title>
    <path d="M6 1 C6 1 2 5.6 2 7.8 A4 4 0 0 0 10 7.8 C10 5.6 6 1 6 1 Z" fill="currentColor" />
  </svg>`;
}

// The flow itself (``/api/flow``), asked again when the flow or the method changes
function useFlowInfo(index, method) {
  // The Brightway project the model was built from: the flow's metadata (CAS, synonyms…) of models built before
  // they were kept in the model
  const { origin } = useContext(EditorContext);
  const project = origin ? `&folder=${encodeURIComponent(origin.folder)}&project=${encodeURIComponent(origin.project)}` : "";
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (index == null) return;
    let live = true;
    setError(null);
    request(`/api/flow/${index}?method=${method}${project}`).then((i) => live && setInfo(i)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [index, method, project]);
  return { info: info?.index === index ? info : null, error };
}

const flowTabsOf = (info) => (info ? [
  { id: "factors", label: `Characterisation · ${info.factors.length}` },
  { id: "emitters", label: `${info.resource ? "Taken by" : "Emitted by"} · ${info.emitters}` },
  info.same_substance > 0 && { id: "same", label: `Same substance · ${info.same_substance}` },
  { id: "metadata", label: "Metadata" },
].filter(Boolean) : []);

// The title of a flow: its icon, name and compartment (what the flow is, as the name: plain text) and, if it carries
// one, its location (a chip, as a process's); under it, its kind and unit
function FlowHead({ info, kind = "Flow" }) {
  if (!info) return html`<div class="info-title">…</div>`;
  return html`
    <div class="info-title flow-title" style=${{ "--flow-color": FLOW_TYPES[info.resource ? "resource" : "emission"].color }}>
      <${FlowIcon} resource=${info.resource} size=${13} /> ${flowTitleNames(info).main}${flowTitleNames(info).qual && html`<span class="flow-compartment">, ${flowTitleNames(info).qual}</span>`}
      ${info.location && html` <span class="loc-chip" title="Location of the flow">${info.location}</span>`}</div>
    <div class="muted">${kind} · ${info.resource ? "resource" : "emission"} · ${unit(info.unit)}</div>`;
}

// ---------------------------------------------------------------- the lists of the pop-ups
// One list for every panel of the pop-ups (the process's Used by, Alternatives and Characterisation; the flow's
// Characterisation, Emitted by and Same substance; the model's flows), so that they look and work alike: the filter box
// above the table, at its left; a header that sorts (click; again: the other way) and filters by column (right-click: a
// row of boxes under the headers, "> 10" or "< 0.1" on numbers); rows of one height; numbers right-aligned with their
// unit in a column of its own; shares as bars; the empty state, how many rows are shown and *Show all* under it. The
// gestures of a row (``dblUse``): a click selects it; its right-click (or Space) opens the pop-up of what it is
// (``onOpen``); its double-click (or Enter) uses it in the tab, with Ctrl/⌘ in a new tab (``onUse(row, inTab)``); its (i)
// (or i) the exchange it stands for (``onInfo``), and only that.
// ``columns``: { key, label, title?, kind?: "num" | "unit" | "share" | "text" (the first column is the name: its link, its
//   (i), ``chip(row)`` after it), value(row, …), cell?(row, …): its content, format?: of a number, tip?(row): its title,
//   width?: px, sort?: false }. ``rows``, ``rowKey(row)``, ``rowClass?(row)``, ``pinned?(row)``: rows kept on top whatever
//   the order and filter (the current process of *Alternatives*), ``match?(row)``: the text the filter box searches (by
//   default the text columns), ``query`` / ``setQuery``: the filter box's text, when its owner needs it too. ``filter``:
//   show the filter box (default: more than 8 rows); ``empty``: the text of an empty list; ``limit``: rows drawn before
//   *Show all*; ``more``: a line under the list (e.g. *Show all n*: rows the server did not send); ``sections?(rows)``:
//   [{ key, label, items, cells? }]: headers that fold, ``noun``: what a section counts; ``renderRow?(row)``: a row of its
//   own (``ProcessRow``: rows that expand); ``tools``: beside the filter box; ``drag?(row)``: what a row drags;
//   ``onHeaderMenu?(event)``: what a right-click on the headers does instead of the row of filter boxes (the Exchanges
//   panel's columns menu). Enter: ``onUse(row)``, Ctrl/⌘+Enter: ``onUse(row, true)`` (in a new tab).
const LIST_WIDTHS = { num: 84, unit: 84, share: 104 };
function ShareBar({ value }) {
  const v = Math.max(-1, Math.min(1, value || 0));
  return html`<span class="share-cell"><span class="share-bar"><i class=${v < 0 ? "negative" : ""} style=${{ width: `${Math.abs(v) * 100}%` }}></i></span>
    <span class="share-pct">${pct(value || 0)}</span></span>`;
}
// A cell after the name, by the kind of its column (shared with ``ProcessRow``)
function listCell(c, r, ...context) {
  const v = c.value(r, ...context);
  const content = c.cell ? c.cell(r, ...context)
    : c.kind === "num" ? (v == null || Number.isNaN(v) ? html`<span class="muted">–</span>` : (c.format || fmt)(v))
    : c.kind === "share" ? (v == null ? "" : html`<${ShareBar} value=${v} />`)
    : v ?? "";
  const tip = c.tip ? c.tip(r, ...context) : !c.kind || c.kind === "unit" || c.kind === "text" ? (typeof v === "string" ? v : undefined) : undefined;
  return html`<td key=${c.key} class=${c.kind || "text"} title=${tip}>${content}</td>`;
}
// The name of a row: its (i) (the exchange it stands for), the name, a chip after it. The name is no link: a click
// selects the row, a right-click opens its pop-up, a double-click uses it (``dblUse``), which ``hint`` tells in the tooltip
function NameCell({ content, chip, title, hint, depth = 0, toggle, onInfo, infoTitle = "The exchange: amount, comment, uncertainty, pedigree (i)" }) {
  return html`
    <td class="name" title=${[title, hint].filter(Boolean).join("\n") || undefined}>
      <span class="name-cell" style=${depth ? { paddingLeft: `${depth * 16}px` } : null}>
        ${toggle}
        ${onInfo && html`<button class="info-btn" title=${infoTitle} aria-label="The exchange"
          onClick=${(e) => { e.stopPropagation(); onInfo(e); }}>i</button>`}
        <span class="name-text">${content}</span>
        ${chip}
      </span></td>`;
}
const passesFilter = (c, r, text) => {
  if (!text) return true;
  const v = c.value(r);
  const m = c.kind === "num" && text.match(/^([<>]=?)\s*(-?[\d.eE+-]+)$/);
  if (m) {
    const x = Number(m[2]);
    return { "<": v < x, "<=": v <= x, ">": v > x, ">=": v >= x }[m[1]];
  }
  return text.split(/\s+/).every((w) => String(v ?? "").toLowerCase().includes(w));
};
function PopupList({ columns, rows, rowKey, rowClass, pinned, match, query, setQuery, filter, empty = "Nothing to list.", limit = null,
  more, sections, noun = "row", renderRow, onOpen, onInfo, hasInfo = () => true, onUse, tools, drag, initial, className = "", onHeaderMenu }) {
  const [sort, setSort] = useState(initial || { key: null, desc: false });
  const [own, setOwn] = useState("");
  const [filters, setFilters] = useState(null);  // null: no row of filter boxes; else { key: text }
  const [all, setAll] = useState(false);
  const [folded, setFolded] = useState(new Set());
  const typed = query ?? own;
  const setText = setQuery ?? setOwn;
  const words = typed.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const textOf = match || ((r) => columns.filter((c) => !c.kind || c.kind === "text").map((c) => String(c.value(r) ?? "")).join(" "));
  const top = pinned ? rows.filter(pinned) : [];
  let shown = (pinned ? rows.filter((r) => !pinned(r)) : rows)
    .filter((r) => words.every((w) => textOf(r).toLowerCase().includes(w)))
    .filter((r) => columns.every((c) => passesFilter(c, r, (filters?.[c.key] || "").trim().toLowerCase())));
  const by = columns.find((c) => c.key === sort.key);
  if (by) {
    const cmp = (a, b) => {
      const x = by.value(a), y = by.value(b);
      return by.kind === "num" || by.kind === "share" ? (x ?? -Infinity) - (y ?? -Infinity) : String(x ?? "").localeCompare(String(y ?? ""));
    };
    shown = [...shown].sort((a, b) => (sort.desc ? -cmp(a, b) : cmp(a, b)));
  }
  const filtered = shown.length < rows.length - top.length;
  // the rows drawn: section headers and the rows of the unfolded sections, up to ``limit`` rows (*Show all*)
  const groups = sections ? sections(shown) : null;
  const drawn = [];
  let count = 0;
  const full = () => !all && limit !== null && count >= limit;
  for (const section of groups || [{ key: "", items: shown }]) {
    if (full()) break;
    if (groups) drawn.push({ section });
    if (groups && folded.has(section.key)) continue;
    for (const r of section.items) {
      if (full()) break;
      drawn.push({ r });
      count++;
    }
  }
  const hidden = full() && shown.length > count;
  const fold = (k) => { const next = new Set(folded); next.has(k) ? next.delete(k) : next.add(k); setFolded(next); };
  const click = (c) => c.sort !== false && setSort((s) => (s.key === c.key ? { key: c.key, desc: !s.desc } : { key: c.key, desc: c.kind === "num" || c.kind === "share" }));
  const [first, ...rest] = columns;
  const keys = (r) => (e) => {
    const inTab = (e.ctrlKey || e.metaKey) && !e.altKey && e.key === "Enter";  // Ctrl/⌘+Enter: in a new tab
    if ((e.ctrlKey || e.metaKey || e.altKey) && !inTab) return;
    const used = () => { e.preventDefault(); e.stopPropagation(); };
    if (e.key === " " && onOpen) { used(); onOpen(r, e); }
    else if (e.key === "i" && onInfo && hasInfo(r)) { used(); onInfo(r, e); }
    else if (e.key === "Enter" && onUse) { used(); onUse(r, inTab); }
  };
  const hint = rowHint(!!onOpen, !!onUse);
  const plainRow = (r) => html`
    <tr key=${rowKey(r)} data-nav tabindex="-1" class=${`exchange ${rowClass?.(r) || ""}`} ...${drag?.(r) || {}} onKeyDown=${keys(r)}
      onContextMenu=${onOpen ? (e) => { e.preventDefault(); e.stopPropagation(); onOpen(r, e); } : undefined}
      ...${dblUse(onUse && ((inTab) => onUse(r, inTab)))}>
      <${NameCell} content=${first.cell ? first.cell(r) : first.value(r)} chip=${first.chip?.(r)} title=${first.tip?.(r)} hint=${hint}
        onInfo=${onInfo && hasInfo(r) ? (e) => onInfo(r, e) : null} />
      ${rest.map((c) => listCell(c, r))}
    </tr>`;
  const row = (r) => (renderRow ? renderRow(r) : plainRow(r));
  const showFilter = filter ?? rows.length > 8;
  const clear = () => { setText(""); setFilters(null); };
  return html`
    <div class=${`popup-list ${className}`}>
      ${(showFilter || tools) && html`<div class="list-tools">
        ${showFilter && html`<input class="list-filter" type="search" placeholder="Filter…" value=${typed} aria-label="Filter the list"
          onInput=${(e) => setText(e.target.value)} onKeyDown=${(e) => { if (e.key === "Escape" && typed) { e.stopPropagation(); setText(""); } }} />`}
        ${tools}</div>`}
      ${rows.length === 0 ? html`<p class="list-empty">${empty}</p>` : html`
        <table class="plist" style=${{ minWidth: `${rest.reduce((n, c) => n + (c.width ?? LIST_WIDTHS[c.kind] ?? 110), 0) + 180}px` }}>
          <colgroup><col />${rest.map((c) => html`<col key=${c.key} style=${{ width: `${c.width ?? LIST_WIDTHS[c.kind] ?? 110}px` }} />`)}</colgroup>
          <thead>
            <tr>${columns.map((c) => html`
              <th key=${c.key} class=${`${c === first ? "name" : c.kind || "text"} ${sort.key === c.key ? "sorted" : ""}`}
                title=${`${c.title ? `${c.title}. ` : ""}${c.sort === false ? "" : "Click: sort. "}${onHeaderMenu ? "Right-click: choose the columns." : "Right-click: filter by column."}`}
                onClick=${() => click(c)} onContextMenu=${(e) => { e.preventDefault(); if (onHeaderMenu) onHeaderMenu(e); else setFilters((f) => (f ? null : {})); }}>
                ${c.label}${sort.key === c.key ? (sort.desc ? " ↓" : " ↑") : ""}</th>`)}</tr>
            ${filters && html`<tr class="filter-row">${columns.map((c, i) => html`
              <th key=${c.key}><input value=${filters[c.key] || ""} placeholder=${c.kind === "num" ? "> 0, < 10…" : "contains…"} aria-label=${`Filter by ${typeof c.label === "string" ? c.label : c.key}`}
                ref=${i === 0 ? (el) => el && !el.dataset.done && (el.dataset.done = "1", el.focus()) : undefined}
                onInput=${(e) => setFilters((f) => ({ ...f, [c.key]: e.target.value }))}
                onKeyDown=${(e) => { if (e.key === "Escape") { e.stopPropagation(); setFilters(null); } }} /></th>`)}</tr>`}
          </thead>
          <tbody>
            ${top.map(row)}
            ${drawn.map(({ section, r }) => (section ? html`
              <tr key=${`s:${section.key}`} class="list-section" onClick=${() => fold(section.key)} aria-expanded=${!folded.has(section.key)}>
                <td colspan=${section.cells ? 1 : columns.length}><span class="caret">${folded.has(section.key) ? "▸" : "▾"}</span> ${section.label}
                  <span class="muted"> · ${section.items.length} ${noun}${section.items.length === 1 ? "" : "s"}</span></td>
                ${section.cells}
              </tr>` : row(r)))}
          </tbody>
        </table>
        ${shown.length === 0 && html`<p class="list-empty">No ${noun} matching the filter · <button class="link" onClick=${clear}>clear it</button></p>`}
        ${shown.length > 0 && filtered && html`<p class="list-note">${shown.length} of ${rows.length - top.length} shown · <button class="link" onClick=${clear}>clear the filter</button></p>`}
        ${hidden && html`<p class="list-note"><button class="link" onClick=${() => setAll(true)}>Show all ${shown.length}</button></p>`}`}
      ${more && html`<p class="list-note">${more}</p>`}
    </div>`;
}

// The panels of a flow (pop-up and tab): ``shown`` is the panel; ``onActivity(index, e)``: a process opened (its pop-up),
// ``onExchange(flow, activity)``: its exchange of this flow (the (i) of an emitter); ``onFlow(index)``: another flow of
// the same substance; ``onCategory(method, e)``: the model pop-up of a method that characterises it (the cycle process →
// flow → model → flow…)
function FlowPanels({ info, shown, method, methods, scope, scoped, onActivity, onExchange, onFlow, onCategory, body }) {
  const use = useContext(TabUseContext);  // a double-click (Enter) on a row
  const [all, setAll] = useState(false);
  const root = scoped && scope ? `&root=${scope.index}&amount=${scope.amount ?? 1}` : "";
  const index = info.index;
  const emitters = useGet(`/api/flow/${index}/emitters?limit=${all ? 2000 : 50}${root}`, shown === "emitters");
  const same = useGet(`/api/flow/${index}/same-substance?method=${method}`, shown === "same");
  useEffect(() => setAll(false), [index]);
  const u = unit(info.unit);
  const error = emitters.error || same.error;
  const d = info.description || {};
  const parts = info.name_parts || { substance: info.name, qualifiers: [] };
  const inChain = scoped && scope;
  return html`
    <div class="info-body" ref=${body}>
      ${error && html`<div class="error">${error}</div>`}
      ${shown === "factors" && html`
        <section>
          <${PopupList} key=${`factors-${index}`} noun="model" empty="No method of this model characterises this flow."
            rows=${info.factors.map((f) => ({ ...f, label: methodTitle(methods[f.method]), unit: `${methods[f.method].unit} / ${u}` }))}
            rowKey=${(f) => f.method} rowClass=${(f) => (f.method === method ? "current" : "")}
            onOpen=${onCategory ? (f, e) => onCategory(f.method, e) : null} onUse=${use && ((f, inTab) => use.model(f.method, inTab))}
            columns=${[
              { key: "method", label: "Model", title: "The impact categories of the methods of this database that characterise the flow: open one for all its flows",
                value: (f) => f.label, tip: (f) => `${methods[f.method].name.join(" | ")} [${methods[f.method].unit}]: its model card` },
              { key: "cf", label: "Factor", kind: "num", value: (f) => f.cf },
              { key: "unit", label: "Unit", kind: "unit", width: 150, value: (f) => f.unit },
            ]} />
        </section>`}
      ${shown === "emitters" && html`
        <section>
          ${!emitters.data ? html`<${Waiting} text="Reading the exchanges…" />` : html`
            <${PopupList} key=${`emitters-${index}`} noun="process" empty="No process of the database has an exchange of this flow."
              rows=${emitters.data.emitters} rowKey=${(p) => p.index} drag=${(p) => dragProcess(p.index)}
              onOpen=${(p, e) => onActivity(p.index, e)} onInfo=${onExchange ? (p, e) => onExchange(index, p, e) : null}
              onUse=${use && ((p, inTab) => use.process(p.index, inTab))}
              columns=${[
                { key: "name", label: "Process", value: (p) => p.name, chip: (p) => html`<span class="loc-chip">${p.location}</span>`,
                  tip: (p) => `${p.reference_product} | ${p.name} | ${p.location}` },
                { key: "per_unit", label: "Per unit", kind: "num", format: fmtAmount, title: "Amount per unit of the process's product, as in its dataset", value: (p) => p.per_unit },
                { key: "unit", label: "Unit", kind: "unit", value: (p) => `${u}/${unit(p.unit)}` },
                ...(inChain ? [
                  { key: "amount", label: "In chain", kind: "num", format: fmtAmount, title: `In the supply chain of ${scope.name} (B·s)`, value: (p) => p.amount || null },
                  { key: "chain_unit", label: "Unit", kind: "unit", value: (p) => (p.amount ? u : "") },
                  { key: "share", label: "Share", kind: "share", title: "Share of the amount in the supply chain", value: (p) => p.share || null },
                ] : []),
              ]}
              more=${emitters.data.count > emitters.data.emitters.length && !all
                && html`<button class="link" onClick=${() => setAll(true)}>Show all ${emitters.data.count}</button>`} />`}
        </section>`}
      ${shown === "same" && html`
        <section>
          ${!same.data ? html`<${Waiting} text="…" />` : html`
            <p class="muted">${info.name} in each compartment of the model, with its factor in ${methodLabel(methods[method])}: a factor in one
              and none in another often means a method that does not cover a compartment.</p>
            <${PopupList} key=${`same-${index}`} noun="flow" rows=${same.data.flows} rowKey=${(f) => f.flow} rowClass=${(f) => (f.current ? "current" : "")}
              onOpen=${(f, e) => !f.current && onFlow(f.flow, e)} onUse=${use && ((f, inTab) => use.flow({ flow: f.flow }, inTab))}
              columns=${[
                { key: "compartment", label: "Compartment", value: (f) => f.compartment || "–",
                  chip: (f) => (f.current ? html`<span class="muted">this flow</span>` : "") },
                { key: "emitters", label: "Processes", kind: "num", format: String, title: "Processes with an exchange of it", value: (f) => f.emitters },
                { key: "cf", label: "Factor", kind: "num", value: (f) => (f.cfs[0] === 0 ? null : f.cfs[0]),
                  cell: (f) => (f.cfs[0] === 0 ? html`<span class="muted">none</span>` : fmt(f.cfs[0])) },
                { key: "unit", label: "Unit", kind: "unit", width: 150, value: (f) => (f.cfs[0] === 0 ? "" : `${methods[method].unit} / ${u}`) },
              ]} />`}
        </section>`}
      ${shown === "metadata" && html`
        <section>
          <table class="kv">
            <tr><th>Name</th><td>${info.name}</td></tr>
            <tr><th>Substance</th><td>${parts.substance}${parts.qualifiers.length > 0 && html` <span class="muted">· ${parts.qualifiers.join(", ")}</span>`}
              <span class="muted" title="Read from the structure of the name: the substance, then its qualifiers"> (from the name)</span></td></tr>
            ${d.cas && html`<tr><th>CAS number</th><td class="mono">${d.cas}</td></tr>`}
            ${d.formula && html`<tr><th>Formula</th><td class="mono">${d.formula}</td></tr>`}
            ${d.synonyms && html`<tr><th>Synonyms</th><td>${d.synonyms.join("; ")}</td></tr>`}
            <tr><th>Compartment</th><td>${info.compartment || "–"}</td></tr>
            <tr><th>Subcompartment</th><td>${info.subcompartment || "–"}</td></tr>
            <tr><th>Location</th><td>${info.location || html`<span class="muted">none (the flow carries no location)</span>`}</td></tr>
            <tr><th>Unit</th><td>${info.unit}</td></tr>
            <tr><th>Type</th><td>${info.resource ? "Resource (from nature)" : "Emission (to nature)"}</td></tr>
            ${d.comment && html`<tr><th>Comment</th><td>${d.comment}</td></tr>`}
            ${info.id && html`<tr><th>Database id</th><td class="mono">${info.id}</td></tr>`}
          </table>
          ${!d.cas && !d.synonyms && html`<p class="text muted">No CAS number or synonym: the biosphere record has none, or the model was
            built before they were kept and is not opened from its Brightway project (rebuild it, or open it from <i>Projects</i>).</p>`}
        </section>`}
    </div>`;
}

function FlowTabsBar({ tabs, shown, setTab }) {
  return html`<div class="tabs info-tabs" role="tablist" onKeyDown=${tabKeys(tabs.map((t) => t.id), shown, setTab)}>
    ${tabs.map((t) => html`
      <button role="tab" aria-selected=${t.id === shown} class=${t.id === shown ? "active" : ""} onClick=${() => setTab(t.id)}>${t.label}</button>`)}
  </div>`;
}

// Space on a flow: the flow pop-up (no exchange data: that is i). ``target.flows``: several flows of one substance
// (compartment chips). Drag it to a tab list (or *Open in new tab*) to make it a tab.
function FlowInfoPopup({ target, win, method, methods, chartMethods, scope, onClose, onOpen, onExchange, onCategory, onOpenTab, onBack }) {
  const [current, setCurrent] = useState(target.flow);
  const [tab, setTab] = useState(target.tab || null);
  const scoped = !!scope;  // opened from a process tab: its panels are in that supply chain
  useEffect(() => { setCurrent(target.flow); setTab(target.tab || null); }, [target.flow, target.x, target.y]);
  const { info, error } = useFlowInfo(current, method);
  const tabs = flowTabsOf(info);
  const shown = tabs.some((t) => t.id === tab) ? tab : tabs[0]?.id;
  const body = useRef(null);
  const asTab = (place) => { onOpenTab({ flow: current, name: info.name, place: info.place, unit: info.unit, resource: info.resource, scope: scoped ? scope : null }, place); onClose(); };
  return html`
    <${PopupWindow} target=${target} win=${win} label="Flow" className="flow-popup" width=${720} onClose=${onClose} onBack=${onBack}
      onDock=${info && onOpenTab ? asTab : null}
      menu=${info && onOpenTab ? [{ label: "Open in new tab", title: "Open the flow in a new tab (or drag the pop-up by its header to a tab list or the edge of a pane)", run: () => asTab() }] : []}
      onKeyDown=${(e) => {
        // Ctrl/⌘+Enter: the flow in a new tab
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && info && onOpenTab) { e.preventDefault(); e.stopPropagation(); asTab(); return; }
        popupKeys({ tabs, shown, setTab, body, onClose })(e);
      }}
      head=${html`
        <${FlowHead} info=${info} />
        ${target.flows?.length > 1 && html`
          <div class="flow-compartments" role="group" aria-label="Compartment">${target.flows.map((f, i) => html`
            <button class=${`chip ${f === current ? "active" : ""}`} onClick=${() => setCurrent(f)}>${target.labels?.[i] || `flow ${i + 1}`}</button>`)}</div>`}`}>
      ${error && html`<div class="error">${error}</div>`}
      ${info && html`
        <${FlowTabsBar} tabs=${tabs} shown=${shown} setTab=${setTab} />
        <${FlowPanels} info=${info} shown=${shown} method=${method} methods=${methods} chartMethods=${chartMethods} scope=${scope} scoped=${scoped}
          onActivity=${onOpen} onFlow=${setCurrent} onCategory=${onCategory} body=${body}
          onExchange=${(f, p) => onExchange({ flow: f, activities: [p.index], exchange: true })} />`}
    <//>`;
}

// ---------------------------------------------------------------- model pop-up (EF roadmap, phase 4)
// Words: an LCIA **method** (EF v3.1) groups **models**, its impact categories (climate change…); in the code a model is
// a ``method`` (an index of ``info.methods``) and its pop-up ``CategoryPopup``.
// The icon of a model seen from its flows (a ranked list): its pop-up, and the button that opens it
function CategoryIcon() {
  return html`<svg class="category-icon" width="13" height="13" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
    <rect x="1" y="2" width="14" height="2.4" rx="1" /><rect x="1" y="6.8" width="10" height="2.4" rx="1" /><rect x="1" y="11.6" width="5.5" height="2.4" rx="1" /></svg>`;
}
const CATEGORY_LIMIT = 300;  // rows drawn before "Show all"
// The other methods, for *Compare with*: those of the same category name first (the same impact in another family or
// version), then the others by family
function compareChoices(methods, method) {
  const category = methodPath(methods[method]).category.toLowerCase();
  const same = [], families = new Map();
  methods.forEach((m, i) => {
    if (i === method) return;
    if (category && methodPath(m).category.toLowerCase() === category) same.push(i);
    else families.set(familyKey(m), [...(families.get(familyKey(m)) || []), i]);
  });
  return { same, families: [...families] };
}

// How the rows of the model pop-up can be arranged: each flow keeps its row; sections gather the flows of a substance,
// a compartment or a location under a header, in the order of their first (largest) flow; *no location* last
const CATEGORY_ORGANIZE = [
  ["", "Factor", "One list, the largest relative factor first"],
  ["name", "Substance", "A section per substance: its flows in each compartment"],
  ["top", "Compartment", "A section per compartment (air, water, soil, natural resource…)"],
  ["sub", "Subcompartment", "A section per compartment and subcompartment"],
  ["location", "Location", "A section per location of the flows (regionalised flows, as LC-IMPACT or IMPACT World+ make); the flows without one under \"no location\", last"],
];
function organizeFlows(items, by) {
  if (!by) return null;
  const key = (it) => (by === "name" ? `${it.name}\u0000${it.unit}` : by === "top" ? it.top || "" : by === "location" ? it.location || ""
    : `${it.top || ""}\u0000${it.sub || ""}`);
  const label = (it) => (by === "name" ? it.name : by === "top" ? it.top || "(unspecified)" : by === "location" ? it.location || "no location"
    : [it.top, it.sub || "unspecified"].filter(Boolean).join(" · "));
  const sections = new Map();
  for (const it of items) {
    const k = key(it);
    if (!sections.has(k)) sections.set(k, { key: k, label: label(it), unit: it.unit, items: [] });
    sections.get(k).items.push(it);
  }
  const list = [...sections.values()];
  return by === "location" ? [...list.filter((s) => s.key), ...list.filter((s) => !s.key)] : list;
}

// The factors of the method(s) of the model pop-up on flows that are not in the model (dropped by the calculation,
// recorded when the model is built): folded under the table, each with what the model has under that name
function UnlinkedFactors({ unlinked, both, a, b, open, setOpen, words }) {
  const sides = [["a", a], ...(both && unlinked.b ? [["b", b]] : [])];
  const rows = sides.flatMap(([k, m]) => unlinked[k].items.map((it) => ({ ...it, side: k, m })))
    .filter((it) => words.every((w) => `${it.name} ${it.compartment}`.toLowerCase().includes(w)));
  const count = (k) => unlinked[k].count;
  const summary = sides.map(([k]) => `${both ? `${k === "a" ? "ref." : "comp."} ` : ""}${count(k)}`).join(" · ");
  const match = (it) => (it.match_kind === "same" ? html`<span title="A flow of the model has the same name and compartments (another biosphere version?)">same flow</span>`
    : it.match_kind === "name" ? html`<span title=${`A flow of the model has the same name: ${it.match.name} (${it.match.compartment})`}>same name · <span class="muted">${it.match.compartment}</span></span>`
    : html`<span class="muted">–</span>`);
  return html`
    <div class="category-unlinked">
      <button class="fold-toggle" onClick=${() => setOpen(!open)} aria-expanded=${open}
        title="Factors of the method on flows that no process of the model has: the calculation drops them">
        <span class="caret">${open ? "▾" : "▸"}</span> Factors on flows not in the model <span class="muted">· ${summary}</span></button>
      ${open && html`
        <${PopupList} noun="factor" filter=${false} rows=${rows} rowKey=${(it) => `${it.side}:${it.name}:${it.compartment}`}
          empty="No factor on a flow outside the model."
          columns=${[
            { key: "name", label: "Flow", value: (it) => `${it.name} ${it.compartment}`,
              cell: (it) => html`${both && html`<span class=${`role-tag ${it.side === "a" ? "ref" : "comp"}`}>${it.side === "a" ? "ref." : "comp."}</span> `}${it.name} <span class="muted">${it.compartment}</span>` },
            { key: "cf", label: "Factor", kind: "num", value: (it) => it.cf },
            { key: "unit", label: "Unit", kind: "unit", width: 150, value: (it) => `${it.m.unit} / ${unit(it.unit)}` },
            { key: "match", label: "In the model", width: 170, title: "What the model has under that name", value: (it) => it.match_kind || "", cell: match },
          ]} />
        ${sides.some(([k]) => count(k) > unlinked[k].items.length) && html`<p class="note">The largest ${unlinked.a.items.length} of each method.</p>`}`}
    </div>`;
}

// The **model card**: a model (an impact category of an LCIA method, e.g. the climate change of EF v3.1) seen from its
// elementary flows, in the whole database: the flows of the model that it characterises, their factor, the factor
// relative to the largest one of the same unit and how many processes emit them directly. *Organize by* arranges them in
// sections (substance, compartment, location). *Compare with* lines it up with another method (the factors of both,
// their ratio, the flows only one of them characterises). A flow's right-click (Space): its pop-up (``onFlow(target,
// event)``); its double-click (Enter): its flow tab (``TabUseContext``). Shown as a pop-up (``CategoryPopup``) or as a tab (``ModelTab``): ``frame({ head, menu,
// body, state, children })`` draws the chrome around it (``body``: the ref of the scrolling list, for the keys; ``state``:
// { method, compare, organize }, what a tab keeps). ``initial``: { compare, organize } it starts with; ``onState``: they
// changed. Not in the supply chain of the process of the tab, for now: a pinned pop-up would follow the focus of the tab.
function CategoryView({ info, method, initial = {}, onState, onFlow, frame }) {
  const use = useContext(TabUseContext);  // a double-click (Enter) on a flow
  const methods = info.methods;
  const [compare, setCompare] = useState(initial.compare ?? null);
  const [organize, setOrganize] = useState(initial.organize || "");
  const [filter, setFilter] = useState("");
  const [outside, setOutside] = useState(false);  // the factors on flows not in the model, unfolded
  const opened = useRef(method);
  useEffect(() => { if (opened.current !== method) { opened.current = method; setCompare(initial.compare ?? null); } }, [method]);
  useEffect(() => { onState?.({ compare, organize }); }, [compare, organize]);
  const { data, error } = useRequest("/api/category", { method, ...(compare !== null ? { compare } : {}) });
  const body = useRef(null);
  const both = !!data && data.compare !== null && data.compare !== undefined;
  const a = methods[method], b = both ? methods[data.compare] : null;
  const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
  const items = data ? data.items : [];
  // Organize by location: models built before the flows kept their location cannot (the option is hidden)
  const located = !!data?.locations;
  const by = organize === "location" && !located ? "" : organize;
  // its pop-up shows its factor in this model, not in the method of the tab
  const flowTarget = (it) => ({ flow: it.flows[0], flows: it.flows, labels: it.compartments, method });
  const choices = useMemo(() => compareChoices(methods, method), [methods, method]);
  const shownItems = items.filter((it) => words.every((w) => `${it.name} ${it.compartment} ${it.location || ""}`.toLowerCase().includes(w)));
  const csv = () => downloadCsv(`model-${methodLabel(a).replace(/[^\w-]+/g, "_")}.csv`,
    ["name", "compartment", ...(located ? ["location"] : []), "unit", both ? "factor_ref" : "factor", ...(both ? ["factor_comp", "ratio_comp_ref"] : ["relative"]), "emitters"],
    shownItems.map((it) => [it.name, it.compartment, ...(located ? [it.location ?? ""] : []), it.unit, it.a.max, ...(both ? [it.b.max, it.ratio ?? ""] : [it.a.rel]), it.emitters]),
    [`# ${both ? `ref.: ${methodTitle(a)} (${a.unit}); comp.: ${methodTitle(b)} (${b.unit})` : `${methodTitle(a)} (${a.unit})`}`, "# whole database"]);
  // in a section, the row says what the header does not
  const flowLabel = (it) => (by === "name" ? html`${it.compartment}`
    : by === "top" ? html`${it.name} <span class="muted">${it.sub || ""}</span>`
    : by === "sub" ? html`${it.name}`
    : html`${it.name} <span class="muted">${it.compartment}</span>`);
  const range = (list, k) => {
    const v = list.map((it) => it[k].max).filter((x) => x);
    if (!v.length) return "–";
    const lo = Math.min(...v), hi = Math.max(...v);
    return lo === hi ? fmt(hi) : `${fmt(lo)} … ${fmt(hi)}`;
  };
  // ref.: the method of the pop-up (its title); comp.: the one chosen in *Compare with*. Each name shows once, where it
  // was chosen; their units show beside them and under the factor columns, flagged when they differ
  const picked = compare !== null ? methods[compare] : null;
  const otherUnit = !!picked && picked.unit !== a.unit;
  const unitHead = (m, differs = false) => html`<span class=${`col-unit ${differs ? "differs" : ""}`}>${m.unit}</span>`;
  const columns = [
    { key: "flow", label: by === "name" ? "Substance / compartment" : by ? "Section / flow" : "Flow", value: (it) => `${it.name} ${it.compartment}`,
      cell: flowLabel, chip: (it) => (it.location && by !== "location" ? html`<span class="loc-chip" title="Location of the flow">${it.location}</span>` : "") },
    ...(both ? [
      { key: "ref", label: html`ref.${unitHead(a)}`, kind: "num", title: `${methodTitle(a)} (${a.unit})`, value: (it) => (it.only === "b" ? null : it.a.max) },
      { key: "comp", label: html`comp.${unitHead(b, b.unit !== a.unit)}`, kind: "num", title: `${methodTitle(b)} (${b.unit})`, value: (it) => (it.only === "a" ? null : it.b.max) },
      { key: "ratio", label: html`comp. / ref.${b.unit !== a.unit && html`<span class="col-unit differs">${b.unit} / ${a.unit}</span>`}`, kind: "num",
        title: "The factor of comp. over that of ref.", value: (it) => (it.only ? null : it.ratio),
        cell: (it) => (it.only ? html`<span class=${`chip only only-${it.only}`}>only ${it.only === "a" ? "ref." : "comp."}</span>` : it.ratio !== null ? fmt(it.ratio) : "") },
      { key: "unit", label: "Per", kind: "unit", width: 80, title: "The factors are per unit of the flow", value: (it) => `/ ${unit(it.unit)}` },
    ] : [
      { key: "factor", label: "Factor", kind: "num", title: `${methodTitle(a)} (${a.unit})`, value: (it) => it.a.max },
      { key: "unit", label: "Unit", kind: "unit", width: 150, value: (it) => `${a.unit} / ${unit(it.unit)}` },
      { key: "rel", label: "Relative", kind: "share", title: "Relative to the largest factor of the same unit", value: (it) => it.a.rel },
    ]),
    { key: "emitters", label: "Emitters", kind: "num", format: String, width: 76, title: "Processes emitting it directly, in the whole database", value: (it) => it.emitters },
  ];
  // a substance's header: the range of its factors under the factor columns
  const sectionCells = (s) => (by !== "name" ? null : both
    ? html`<td class="num">${range(s.items, "a")}</td><td class="num">${range(s.items, "b")}</td><td></td><td class="unit">/ ${unit(s.unit)}</td><td></td>`
    : html`<td class="num">${range(s.items, "a")}</td><td class="unit">${a.unit} / ${unit(s.unit)}</td><td></td><td></td>`);
  const head = html`
    <div class="info-title"><span class="kind-mark category" title="Model card: an impact category of a method, seen from its elementary flows"><${CategoryIcon} /></span>
      ${methodLabel(a)}${picked && html` <span class="role-tag ref" title="The reference: the method of this model card">ref.</span>`}</div>
    <div class="muted">Model · ${familyKey(a)} · ${a.unit}</div>`;
  return frame({ head, body, state: { method, compare, organize },
    menu: [{ label: "Export CSV", run: csv, title: "The rows of the table (all of them, filtered)" }],
    children: html`
      <div class="category-controls">
        <div class="view-group" role="group" aria-label="Organize by">
          <span class="view-group-label">Organize by</span>
          <span class="segmented">
            ${CATEGORY_ORGANIZE.filter(([k]) => k !== "location" || located).map(([k, label, hint]) => html`
              <button class=${by === k ? "primary" : ""} aria-pressed=${by === k} title=${hint} onClick=${() => setOrganize(k)}>${label}</button>`)}
          </span>
          ${data && !located && html`<span class="muted hint-rebuild" title="This model was built before the flows kept their location: build it again for Organize by location">Location: rebuild to see locations</span>`}
        </div>
        <label class="view-group compare-with">
          ${picked && html`<span class="role-tag comp" title="The method compared with the reference">comp.</span>`}
          <span class="view-group-label">Compare with</span>
          <select value=${compare ?? ""} onChange=${(e) => setCompare(e.target.value === "" ? null : Number(e.target.value))} aria-label="Compare with another method">
            <option value="">—</option>
            ${choices.same.length > 0 && html`<optgroup label=${`Same category: ${methodPath(a).category}`}>
              ${choices.same.map((i) => html`<option value=${i}>${methods[i].name.join(" | ")}</option>`)}</optgroup>`}
            ${choices.families.map(([family, list]) => html`<optgroup label=${family}>
              ${list.map((i) => html`<option value=${i}>${methodPath(methods[i]).rest.slice(1).join(" | ") || family}</option>`)}</optgroup>`)}
          </select>
          ${picked && html`<span class=${`category-unit ${otherUnit ? "differs" : ""}`}
            title=${otherUnit ? `Not the unit of the reference (${a.unit}): comp. / ref. mixes units` : "Its unit, the same as the reference's"}>${picked.unit}</span>`}
        </label>
        <${InfoTip} label="About this table" text=${`The flows of this model that the method characterises, in the whole database. Relative: the factor over the largest of the method among the flows of the same unit. Emitters: the processes emitting (or taking) the flow directly. ${both ? "ref.: the method of this model card; comp.: the one it is compared with; comp. / ref.: the ratio of their factors (in their units). " : ""}Factors of the method on flows that are not in the model are under the table${data && !data.unlinked ? " once the model is rebuilt (it did not record them)" : ""}. A right-click on a flow (or Space) opens its pop-up, a double-click (or Enter) its flow tab.`} />
      </div>
      ${error && html`<div class="error">${error}</div>`}
      <div class="info-body" ref=${body}>
        ${!data && !error && html`<${Waiting} text="Reading the factors…" />`}
        ${data && html`
          <${PopupList} key=${`${method}-${both ? data.compare : ""}-${by}`} noun="flow" filter rows=${items} columns=${columns} limit=${CATEGORY_LIMIT}
            rowKey=${(it) => it.flows[0]} rowClass=${(it) => `${by ? "in-section" : ""} ${it.only ? `only-${it.only}` : ""}`}
            match=${(it) => `${it.name} ${it.compartment} ${it.location || ""}`} query=${filter} setQuery=${setFilter}
            sections=${by ? (rows) => organizeFlows(rows, by).map((s) => ({ ...s, cells: sectionCells(s) })) : null}
            empty="No flow of the model has a factor in this method." onOpen=${(it, e) => onFlow(flowTarget(it), e)}
            onUse=${use && ((it, inTab) => use.flow({ flow: it.flows[0] }, inTab))} />
          ${data.unlinked && html`<${UnlinkedFactors} unlinked=${data.unlinked} both=${both} a=${a} b=${b} open=${outside} setOpen=${setOutside} words=${words} />`}`}
      </div>` });
}

// The **model pop-up**: the model card in a pop-up window (the Methods panel: the list icon of a method; a model of the
// flow pop-up's *Characterisation*). ``target.category``: the method (``compare``, ``organize``: how it starts).
// ``onOpenTab(root, place)``: it becomes a model tab (*Open in new tab*, Ctrl/⌘+Enter, or dragged by its header to a
// tab list or the edge of a pane), ``root``: { model: { method, compare, organize } }.
function CategoryPopup({ target, win, info, onClose, onBack, onFlow, onOpenTab }) {
  return html`<${CategoryView} info=${info} method=${target.category} initial=${{ compare: target.compare, organize: target.organize }}
    onFlow=${(next) => onFlow(next)}
    frame=${({ head, menu, body, state, children }) => {
      const asTab = onOpenTab ? (place) => { onOpenTab({ model: state }, place); onClose(); } : null;
      const keys = popupKeys({ tabs: [], shown: null, setTab: () => {}, body, onClose });
      return html`
        <${PopupWindow} target=${target} win=${win} label="Model card" className="category-popup" width=${780} onClose=${onClose} onBack=${onBack}
          onDock=${asTab}
          menu=${[...(asTab ? [{ label: "Open in new tab", title: "Open the model card in a new tab (or drag the pop-up by its header to a tab list or the edge of a pane)", run: () => asTab() }] : []), ...menu]}
          onKeyDown=${(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && asTab) { e.preventDefault(); e.stopPropagation(); asTab(); return; }
            keys(e);
          }}
          head=${head}>${children}<//>`;
    }} />`;
}

// i on a flow: its exchange in the dataset of the process emitting it (amount, comment, uncertainty, pedigree,
// formula), one per process (a merged or grouped emitter) and per flow (a substance over several compartments)
function FlowExchangePopup({ target, win, method, methods, onClose, onOpen, onFlow, onBack }) {
  const flowIds = target.flows?.length ? target.flows : [target.flow];
  const [found, setFound] = useState(null);
  const [error, setError] = useState(null);
  const body = useRef(null);
  const activities = (target.activities || []).join(",");
  useEffect(() => {
    let live = true;
    setFound(null);
    Promise.all(flowIds.map((f) => request(`/api/flow/${f}?method=${method}&activities=${activities}`)))
      .then((list) => live && setFound(list)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [flowIds.join(","), activities, method]);
  const first = found?.[0];
  const sections = (found || []).flatMap((f) => f.exchanges.filter((x) => x.amount !== 0 || x.exchanges.length).map((x) => ({ flow: f, ...x })));
  const single = sections.length === 1 ? sections[0] : null;
  return html`
    <${PopupWindow} target=${target} win=${win} label="Exchange information" onClose=${onClose} onBack=${onBack}
      onKeyDown=${popupKeys({ tabs: [], shown: null, setTab: () => {}, body, onClose, exchange: true })}
      head=${html`
        <div class="info-title flow-title">${first ? html`<${FlowIcon} resource=${first.resource} size=${13} />` : ""}
          ${single ? html`Exchange of <a href="#" title="The flow (Space)" onClick=${(e) => { e.preventDefault(); onFlow({ flow: single.flow.index }); }}>${single.flow.name}</a>
            in ${single.activity.name} <span class="loc-chip">${single.activity.location}</span>`
            : first ? html`Exchanges of <a href="#" title="The flow (Space)" onClick=${(e) => { e.preventDefault(); onFlow({ flow: first.index, flows: target.flows, labels: target.labels }); }}>${first.name}</a>` : "…"}</div>
        ${first && html`<div class="muted">Exchange · ${single ? single.flow.place || "no compartment" : `${sections.length} exchanges`} · ${unit(first.unit)}</div>`}`}>
      ${error && html`<div class="error">${error}</div>`}
      <div class="info-body" ref=${body}>
        ${found && sections.length === 0 && html`<p class="text muted">No exchange of this flow in these datasets.</p>`}
        ${sections.map(({ flow, activity, exchanges, amount }) => html`
          <section key=${`${flow.index}-${activity.index}`}>
            ${!single && html`<h4>${flow.place} in <a href="#" onClick=${(e) => { e.preventDefault(); onOpen(activity.index); }}>${activity.name}</a>
              <span class="loc-chip">${activity.location}</span></h4>`}
            <table class="kv"><tr><th>Amount</th><td>${fmtAmount(exchanges[0]?.amount ?? amount)} ${unit(exchanges[0]?.unit || flow.unit)}
              <span class="muted">${exchanges[0] ? "as recorded in the dataset" : "per operation of the process (matrix)"}</span></td></tr></table>
            ${exchanges.length === 0 ? html`<p class="text muted">${flow.exchanges_available
              ? "No comment or uncertainty recorded for this exchange."
              : "This model has no exchange metadata — rebuild it to see comments and uncertainty."}</p>`
              : exchanges.map((e) => (hasExchangeMeta(e) ? html`<${ExchangeMeta} e=${e} />` : html`<p class="text muted">No comment or uncertainty recorded for this exchange.</p>`))}
          </section>`)}
      </div>
    <//>`;
}

// The pop-ups of a tab (process or flow): { key, target, history, pinned, rect }. At most one is not pinned: opening a
// pop-up from outside replaces it, and a click outside or Esc closes it. Pinned ones stay open. ``history``: the targets
// visited by following links inside the pop-up (back button / Backspace); opening a pop-up from outside, or closing it,
// starts over. ``rect``: where it was dragged or resized to (null: at the click).
function usePopupStack() {
  const [popups, setPopups] = useState([]);
  const update = (key, change) => setPopups((ps) => ps.map((p) => (p.key === key ? { ...p, ...change(p) } : p)));
  const open = (target) => setPopups((ps) => [...ps.filter((p) => p.pinned), { key: newId("i"), target, history: [], pinned: false, rect: null }]);
  // Without a key: the pop-up that is not pinned
  const close = (key) => setPopups((ps) => ps.filter((p) => (key ? p.key !== key : p.pinned)));
  const pin = (key) => setPopups((ps) => (ps.find((p) => p.key === key)?.pinned
    ? ps.filter((p) => p.pinned || p.key === key).map((p) => (p.key === key ? { ...p, pinned: false } : p))
    : ps.map((p) => (p.key === key ? { ...p, pinned: true } : p))));
  const follow = (key, next) => update(key, (p) => ({ history: [...p.history, p.target], target: { ...next, x: p.target.x, y: p.target.y } }));
  const back = (p) => (p.history.length ? () => update(p.key, (q) => ({ target: q.history[q.history.length - 1], history: q.history.slice(0, -1) })) : null);
  const win = (p) => ({ rect: p.rect, pinned: p.pinned, onPin: () => pin(p.key), onRect: (rect) => update(p.key, () => ({ rect })) });
  // Only the pop-ups ``live(p)`` (the others are closed)
  const keep = (live) => setPopups((ps) => (ps.every(live) ? ps : ps.filter(live)));
  return { popups, update, open, close, pin, follow, back, win, keep };
}

// The pop-up of an object of a tab's pop-up stack: a model (``target.category``), an exchange of a flow, a flow, or a
// process. The same in a process tab (``FUView``) and a flow tab (``FlowTab``), so that the links inside a pop-up cycle
// process → flow → model → flow → process in one history (← comes back). ``follow(next)``: a link followed inside it;
// ``scope``: the process of the tab, for the flow pop-up's *Emitted by*; ``onOpenFlowTab``: a flow pop-up made a tab;
// ``onOpenModelTab``: a model pop-up made a tab ({ model: { method, compare, organize } }, place);
// ``process``: what the process pop-up needs of the tab ({ rowsById, rootImpact, root, amount, onUseAsRoot, onOpenTab, onGoTo })
function objectPopup(p, { stack, model, method, chartMethods, follow, scope, onOpenFlowTab, onOpenModelTab, process }) {
  const t = p.target;
  const common = { key: p.key, target: t, win: stack.win(p), onClose: () => stack.close(p.key), onBack: stack.back(p) };
  if (t.category !== undefined) return html`<${CategoryPopup} ...${common} info=${model} onFlow=${follow} onOpenTab=${onOpenModelTab} />`;
  if (t.flow !== undefined && t.exchange) return html`<${FlowExchangePopup} ...${common} method=${method} methods=${model.methods}
      onOpen=${(index) => follow({ index, row: null })} onFlow=${follow} />`;
  if (t.flow !== undefined) return html`<${FlowInfoPopup} ...${common} method=${t.method ?? method} methods=${model.methods} chartMethods=${chartMethods}
      scope=${scope} onOpen=${(index) => follow({ index, row: null })} onExchange=${follow} onCategory=${(m) => follow({ category: m })}
      onOpenTab=${onOpenFlowTab} />`;
  if (t.index === undefined) return null;
  return html`<${InfoPopup} ...${common} model=${model} method=${method} methodInfo=${model.methods[method]} rootImpact=${process.rootImpact}
      rowsById=${process.rowsById} onUseAsRoot=${process.onUseAsRoot} onOpenTab=${process.onOpenTab} root=${process.root} amount=${process.amount}
      onFollow=${follow} onGoTo=${process.onGoTo} />`;
}

// A flow as a tab: the flow's panels in the pane, no tree. Its state: { flow: { flow, name, place, unit, resource, scope } }.
// It has pop-ups of its own, as a process tab: a process, an exchange or a model opened from its panels (and the model
// pop-up of the Methods panel while it has the focus), followed from one to the next in one history.
function FlowTab({ id, info: model, initial, visible, onSave, onTitle, onOpenTab }) {
  const lens = useContext(LensContext);
  const start = initial?.state?.flow || initial?.flow;
  const [current, setCurrent] = useState(start.flow);
  const [scope] = useState(start.scope || null);
  const scoped = !!start.scope;
  const [tab, setTab] = useState(start.tab || null);
  const { info, error } = useFlowInfo(current, lens.method);
  const stack = usePopupStack();
  useEffect(() => {
    if (!info) return;
    const flow = { flow: current, name: info.name, place: info.place, unit: info.unit, resource: info.resource, scope: scoped ? scope : null, tab };
    onSave(id, { flow });
    onTitle(id, { name: info.name, location: info.place, flow: current, resource: info.resource });
  }, [info, scoped, tab]);
  // The Methods panel opens the model pop-up of the tab in focus, the Processes panel the pop-up of the tab's object:
  // a flow tab answers both (the model pop-up could not open while a flow tab had the focus)
  CATEGORY_OPENERS[id] = (category, x, y) => stack.open({ category, x, y });
  POPUP_OPENERS[id] = (x, y) => stack.open({ flow: current, x, y });
  const tabs = flowTabsOf(info);
  const shown = tabs.some((t) => t.id === tab) ? tab : tabs[0]?.id;
  const at = (e) => ({ x: e?.clientX ?? window.innerWidth / 3, y: e?.clientY ?? 120 });
  const process = {
    rowsById: new Map(), rootImpact: 0, root: null, amount: 1, onUseAsRoot: null, onGoTo: () => {},
    onOpenTab: (r, place) => onOpenTab(id, r, place),
  };
  // A double-click (Enter) on a row: a flow becomes the flow of this tab; a process or a model opens in a new tab
  const use = {
    process: (index) => activityOf(index, lens.method).then((found) => { stack.close(); onOpenTab(id, found); }).catch(() => {}),
    flow: (target, inTab) => {
      stack.close();
      if (inTab) onOpenTab(id, { ...target, scope: scoped ? scope : null });
      else if (target.flow !== current) setCurrent(target.flow);
    },
    model: (method) => { stack.close(); onOpenTab(id, { model: { method } }); },
  };
  return html`
    <${TabUseContext.Provider} value=${use}>
    <div class="flow-tab" style=${{ display: visible ? "" : "none" }}>
      <div class="flow-tab-head"><${FlowHead} info=${info} /></div>
      ${error && html`<div class="error">${error}</div>`}
      ${info && html`
        <${FlowTabsBar} tabs=${tabs} shown=${shown} setTab=${setTab} />
        <${FlowPanels} info=${info} shown=${shown} method=${lens.method} methods=${model.methods} chartMethods=${lens.chartMethods}
          scope=${scope} scoped=${scoped} onFlow=${(flow, e) => stack.open({ flow, ...at(e) })}
          onActivity=${(index, e) => stack.open({ index, row: null, ...at(e) })}
          onExchange=${(flow, p, e) => stack.open({ flow, activities: [p.index], exchange: true, ...at(e) })}
          onCategory=${(category, e) => stack.open({ category, ...at(e) })} />`}
      ${stack.popups.map((p) => objectPopup(p, { stack, model, method: lens.method, chartMethods: lens.chartMethods, scope,
        follow: (next) => stack.follow(p.key, next), process,
        onOpenFlowTab: (f, place) => onOpenTab(id, f, place), onOpenModelTab: (m, place) => onOpenTab(id, m, place) }))}
    </div>
    <//>`;
}

// The title of a model tab: { name, model } (a method no longer in the database: "Model")
const modelTitle = (info, method) => ({ name: info.methods[method] ? methodTitle(info.methods[method]) : "Model", model: method });

// A model as a tab: the model card in the pane, without the window chrome. Its state: { model: { method, compare,
// organize } } (``method``: an index of ``info.methods``). It has pop-ups of its own, as a flow tab: a flow opened from
// its rows opens its pop-up there, then the flow's processes, models… in one history (and the model pop-up of the
// Methods panel while it has the focus). Its title: { name, model: method } (the status bar shows "Model · ‹title›").
function ModelTab({ id, info: model, initial, visible, onSave, onTitle, onOpenTab }) {
  const lens = useContext(LensContext);
  const start = initial?.state?.model || initial?.model;
  const [method, setMethod] = useState(start.method);
  const m = model.methods[method];
  const kept = useRef({ compare: start.compare ?? null, organize: start.organize || "" });
  const stack = usePopupStack();
  const save = (shown = method) => onSave(id, { model: { method: shown, ...kept.current } });
  useEffect(() => { if (m) onTitle(id, modelTitle(model, method)); }, [m, lens.nameFormat]);
  // A double-click (Enter) on a row: a model becomes the model of this tab (its *Organize by* kept, no comparison);
  // a process or a flow opens in a new tab
  const use = {
    process: (index) => activityOf(index, lens.method).then((found) => { stack.close(); onOpenTab(id, found); }).catch(() => {}),
    flow: (target) => { stack.close(); onOpenTab(id, target); },
    model: (next, inTab) => {
      stack.close();
      if (inTab) onOpenTab(id, { model: { method: next } });
      else if (next !== method) { kept.current = { ...kept.current, compare: null }; setMethod(next); save(next); }
    },
  };
  CATEGORY_OPENERS[id] = (category, x, y) => stack.open({ category, x, y });
  const at = (e) => ({ x: e?.clientX ?? window.innerWidth / 3, y: e?.clientY ?? 120 });
  const process = {
    rowsById: new Map(), rootImpact: 0, root: null, amount: 1, onUseAsRoot: null, onGoTo: () => {},
    onOpenTab: (r, place) => onOpenTab(id, r, place),
  };
  return html`
    <${TabUseContext.Provider} value=${use}>
    <div class="flow-tab model-tab category-popup" style=${{ display: visible ? "" : "none" }}>
      ${!m ? html`<div class="empty">This model is not in this database.</div>` : html`
        <${CategoryView} info=${model} method=${method} initial=${kept.current}
          onState=${(state) => { kept.current = state; save(); }}
          onFlow=${(target, e) => stack.open({ ...target, ...at(e) })}
          frame=${({ head, menu, body, children }) => html`
            <div class="model-tab-card" tabindex="-1" onKeyDown=${popupKeys({ tabs: [], shown: null, setTab: () => {}, body, onClose: () => {} })}>
              <div class="flow-tab-head model-tab-head">
                <div class="model-tab-title">${head}</div>
                <${PopupMenu} items=${menu} title="Actions of this model card" />
              </div>
              ${children}
            </div>`} />`}
      ${stack.popups.map((p) => objectPopup(p, { stack, model, method: lens.method, chartMethods: lens.chartMethods, scope: null,
        follow: (next) => stack.follow(p.key, next), process,
        onOpenFlowTab: (f, place) => onOpenTab(id, f, place), onOpenModelTab: (mt, place) => onOpenTab(id, mt, place) }))}
    </div>
    <//>`;
}

// A GET of the server for a component, when ``enabled``: { data, error } (the last answer stays while the next comes)
function useGet(url, enabled = true) {
  const [state, setState] = useState({ data: null, error: null, url: null });
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    setState((s) => ({ ...s, error: null }));
    request(url).then((data) => live && setState({ data, error: null, url }), (e) => live && setState((s) => ({ ...s, error: e.message })));
    return () => { live = false; };
  }, [url, enabled]);
  return state.url === url ? state : { data: null, error: state.error };
}

// The processes in whose dataset a row (or a bundle's members) is an input: the nearest
// process or merged row above it, past group and others rows
function suppliedTo(row, rowsById) {
  let p = row && rowsById.get(row.parent);
  while (p && (p.kind === "group" || p.kind === "rest")) p = rowsById.get(p.parent);
  return p?.kind === "process" && p.meta ? [p.meta] : p?.kind === "merged" ? p.metas || [] : [];
}

// The Exchanges panel of a process card: its exchanges as its dataset records them, in the sections of the process form
// (its products, then DATASET_SECTIONS, in that order) and as compact as the form's tables. The type of an input is read
// as the tree reads it (``flowTypeOf``): from a waste treatment, waste; a substitution or a negative input, an avoided
// product; waste and avoided products are shown positive, as in the form.
const EXCHANGE_SECTIONS = [["product", "Products"], ...DATASET_SECTIONS.map(([key, label]) => [key, label])];
function inputSection(e) {
  if (e.waste) return "waste";
  if (e.type === "substitution") return "avoided";
  if (e.type === "production" || e.type === "generic production") return "product";  // a co-product
  return e.amount < 0 ? "avoided" : "technosphere";
}
// The columns of the Exchanges panel after the name (right-click their headers: ``ColumnMenu``; the choice is kept in the
// browser, ``exchangeColumns``). ``place`` is the chip after the name (a process's location, a flow's compartment).
const EXCHANGE_PANEL_COLUMNS = [
  { id: "place", label: "Location / compartment", hint: "After the name: the location of a process, the compartment of a flow" },
  { id: "amount", label: "Amount", hint: "As recorded in the dataset (waste and avoided products positive, as in the process form)" },
  { id: "unit", label: "Unit" },
  { id: "comment", label: "Comment", hint: "Comment on the exchange, on one line (hover it for all of it; i: the exchange pop-up)" },
  { id: "uncertainty", label: "Uncertainty", hint: "Uncertainty distribution recorded on the exchange" },
  { id: "pedigree", label: "Pedigree", hint: "Pedigree scores of the exchange: reliability, completeness, time, geography, technology" },
  { id: "formula", label: "Formula", hint: "Formula of the amount, as recorded" },
];
const DEFAULT_EXCHANGE_COLUMNS = ["place", "amount", "unit", "comment"];
function loadExchangeColumns() {
  try {
    const kept = JSON.parse(loadPreference("exchangeColumns", "null"));
    if (Array.isArray(kept)) return kept.filter((id) => EXCHANGE_PANEL_COLUMNS.some((c) => c.id === id));
  } catch { /* not kept, or not readable */ }
  return DEFAULT_EXCHANGE_COLUMNS;
}
const oneLine = (text) => (text || "").replace(/\s+/g, " ").trim();
const pedigreeText = (e) => (e?.pedigree ? PEDIGREE.map(([k]) => e.pedigree[k] ?? "–").join(",") : "");
const pedigreeTip = (e) => (e?.pedigree ? PEDIGREE.map(([k, label]) => `${label} ${e.pedigree[k] ?? "–"}`).join(" · ") : undefined);
// ``onOpen(row, e)``: the pop-up of its process or flow; ``onExchange(row, e)``: its exchange pop-up; ``onUse(row, inTab)``:
// Enter (double-click) on a process or a flow (Ctrl/⌘: in a new tab). Its keys are those of the lists of the pop-ups (``popupKeys``, and in a
// tab the docked card's). Nothing is edited here: Edit mode is the top bar's (the exchange pop-up edits an amount).
function ProcessExchanges({ index, info, exchanges, onOpen, onExchange, onUse }) {
  const [chosen, setChosen] = useState(loadExchangeColumns);
  const [menu, setMenu] = useState(null);  // where the columns menu is open: { x, y }
  const choose = (ids) => { setChosen(ids); savePreference("exchangeColumns", JSON.stringify(ids)); };
  const has = (id) => chosen.includes(id);
  const positive = (section, amount) => (section === "waste" || section === "avoided" ? Math.abs(amount) : amount);
  // A process named by the *Name display* (``processNames``: by default its product, the location as the chip); its
  // tooltip gives the activity and the product
  const named = (m) => {
    const n = processNames(m);
    return { name: n.main, place: n.qual, full: [m.name, m.reference_product && m.reference_product !== m.name && `product: ${m.reference_product}`, m.location].filter(Boolean).join(" · ") };
  };
  const p = exchanges.production;
  const rows = [
    ...(p ? [{ key: "production", section: "product", kind: "process", own: true, index, ...named(info),
      amount: p.amount, unit: p.unit ?? info.unit, comment: p.comment, ex: p }] : []),
    ...exchanges.inputs.map((e, i) => ({ key: `p${i}`, section: inputSection(e), kind: "process", index: e.index, ...named(e),
      amount: e.amount, unit: e.unit, comment: e.comment, ex: e })),
    ...exchanges.flows.map((f, i) => {
      const n = flowTitleNames({ name: f.name, place: (f.categories || []).join(", ") });
      return { key: `f${i}`, section: fromNature(f.categories?.[0]) ? "resource" : "emission", kind: "flow", index: f.index,
        name: n.main, place: n.qual, full: [f.name, ...(f.categories || [])].join(" · "), amount: f.amount, unit: f.unit, comment: f.comment, ex: f };
    }),
  ].map((r) => ({ ...r, shown: positive(r.section, r.amount ?? 0) }));
  const sections = (shown) => EXCHANGE_SECTIONS.map(([key, label]) => ({ key, label, items: shown.filter((r) => r.section === key) }))
    .filter((s) => s.items.length);
  if (!exchanges.available) return html`<p class="muted">This model has no exchanges recorded: build it again to see them.</p>`;
  const columns = [
    { key: "name", label: "Exchange", title: "Its process or flow: right-click for its pop-up, double-click to use it in the tab; (i): the exchange (comment, uncertainty, pedigree)",
      value: (r) => r.name, tip: (r) => r.full,
      chip: (r) => (!r.place || !has("place") ? "" : r.kind === "flow" ? html` <span class="muted small">${r.place}</span>` : html` <span class="loc-chip">${r.place}</span>`) },
    has("amount") && { key: "amount", label: "Amount", kind: "num", format: fmtAmount, title: EXCHANGE_PANEL_COLUMNS[1].hint, value: (r) => r.shown },
    has("unit") && { key: "unit", label: "Unit", kind: "unit", width: 60, value: (r) => unit(r.unit) },
    has("comment") && { key: "comment", label: "Comment", width: 220, title: "Comment on the exchange (hover it for all of it)",
      value: (r) => oneLine(r.comment), tip: (r) => r.comment?.trim() || undefined },
    has("uncertainty") && { key: "uncertainty", label: "Uncertainty", width: 200, value: (r) => uncertaintyText(r.ex) || "" },
    has("pedigree") && { key: "pedigree", label: "Pedigree", width: 90, title: EXCHANGE_PANEL_COLUMNS[5].hint, value: (r) => pedigreeText(r.ex), tip: (r) => pedigreeTip(r.ex) },
    has("formula") && { key: "formula", label: "Formula", width: 140, value: (r) => r.ex?.formula || "" },
  ].filter(Boolean);
  return html`
    <${PopupList} key=${`dataset-${index}`} noun="exchange" rows=${rows} rowKey=${(r) => r.key} sections=${sections}
      className="exchange-list" empty="The dataset records no exchange." match=${(r) => `${r.name} ${r.full} ${r.comment || ""}`}
      onOpen=${onOpen} onInfo=${onExchange} hasInfo=${(r) => !r.own} onUse=${onUse}
      onHeaderMenu=${(e) => setMenu({ x: e.clientX, y: e.clientY })} columns=${columns} />
    ${menu && html`<${PagePortal}>
      <${ColumnMenu} at=${menu} columns=${chosen} options=${EXCHANGE_PANEL_COLUMNS} onChange=${choose} onClose=${() => setMenu(null)} />
    <//>`}`;
}

// ``target.parents``: for a process opened from a bundle pop-up (no row of its own), the
// processes it is an input of, for its exchange tab

// Two pop-ups of a process: the activity's (Space, right-click: its exchanges, users, alternatives,
// description, metadata) and the exchange's (i, (i): the amount in this tree and the comment and
// uncertainty of the input, ``target.exchange``). ``onFollow(target)`` opens another in place.
// ``docked``: the same panels in a tab, under its first line: the tree (``treeSlot``) is the first panel, the pop-up's the
// others; ``active``: the tab is the one shown (a hidden tab loads nothing). ``card``: what a tab's card has more than a
// pop-up, from its tree: { characterisation: the multi indicator chart and table under the totals, contribution: the
// Contribution panel (its sub-panels) }
function DockedShell({ children, onKeyDown }) {
  return html`<div class="docked-panels" onKeyDown=${onKeyDown}>${children}</div>`;
}
function InfoPopup({ target, win, model, method, methodInfo, rootImpact, rowsById, onClose, onUseAsRoot, onOpenTab, root, amount, onFollow, onGoTo, onBack, docked, active, treeSlot, onDown, card }) {
  const idle = !!docked && !active;
  const exchange = !!target.exchange;
  const use = useContext(TabUseContext);  // a double-click (Enter) on a flow or a model of its lists
  const explorerKeys = useRef(null);  // set by the explorer of the Exchanges tab: asked first for the keys
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [users, setUsers] = useState(null);
  const [allUsers, setAllUsers] = useState(false);
  // Alternative suppliers: the other activities producing the same product, per unit
  const [alternatives, setAlternatives] = useState(null);
  // Open tab, back to the default on each right-click (following a link inside keeps it)
  const [tab, setTab] = useState(null);
  const body = useRef(null);
  useEffect(() => setTab(null), [target.x, target.y]);
  useEffect(() => {
    if (idle) return;
    setInfo(null);
    request(`/api/activity/${target.index}?method=${method}`).then(setInfo).catch((e) => setError(e.message));
  }, [target.index, method, idle]);
  useEffect(() => { setAllUsers(false); body.current?.scrollTo(0, 0); }, [target.index]);
  // Exchanges as in the dataset, and for a tree row, the parent's input of this product
  // (comment, uncertainty): ``link`` is [{ meta, inputs }], one per parent dataset using it
  // (several under a merged row), also when the row is in a group or an others row
  const [exchanges, setExchanges] = useState(null);
  const [link, setLink] = useState(null);
  const parentMetas = target.row ? (target.row.kind === "process" ? suppliedTo(target.row, rowsById) : []) : target.parents || [];
  const parentKey = parentMetas.map((m) => m.index).join(",");
  useEffect(() => {
    setExchanges(null);
    if (exchange || idle) return;
    request(`/api/activity/${target.index}/exchanges?method=${method}`).then(setExchanges).catch(() => setExchanges(null));
  }, [target.index, method, exchange, idle]);
  useEffect(() => {
    setLink(null);
    if (!exchange || !parentMetas.length) return;
    let live = true;
    Promise.all(parentMetas.map((m) => request(`/api/activity/${m.index}/exchanges?method=${method}&product=${target.index}`)
      .then((r) => ({ meta: m, inputs: r.inputs }))))
      .then((links) => live && setLink(links.filter((l) => l.inputs.length)))
      .catch(() => live && setLink(null));
    return () => { live = false; };
  }, [target.index, parentKey, method, exchange]);
  useEffect(() => {
    setUsers(null);
    if (exchange || idle) return;
    const fu = root ? `&root=${root.index}&amount=${amount}` : "";
    request(`/api/activity/${target.index}/users?method=${method}&limit=${allUsers ? 1000 : 15}${fu}`)
      .then(setUsers).catch((e) => setError(e.message));
  }, [target.index, method, root, amount, allUsers, exchange, idle]);
  useEffect(() => {
    setAlternatives(null);
    if (target.direct || exchange || idle) return;
    request(`/api/activity/${target.index}/alternatives?method=${method}`)
      .then(setAlternatives).catch(() => setAlternatives(null));
  }, [target.index, method, exchange, idle]);
  // Edit mode: the dataset as the project holds it (what the fields stand for), and what can be changed in place
  const ctx = useContext(EditorContext);
  const [ds, setDs] = useState(null);
  useEffect(() => {
    setDs(null);
    if (!ctx.editing || !info) return;
    let live = true;
    serverRequest(datasetUrl(ctx.origin, info.database, info.code)).then((r) => live && setDs(r)).catch(() => { /* not a Brightway process */ });
    return () => { live = false; };
  }, [ctx.editing, info?.database, info?.code, ctx.pending]);
  const locked = !!info && (ctx.locked.includes(info.database) || !!ds?.locked);
  const stagedHere = !!ds && ctx.pending.some((c) => c.kind === "edit" && c.database === ds.database && c.code === ds.code);
  const own = (patch) => quickEdit(ctx, info.database, info.code, patch);
  const inDataset = (meta) => ({ locked: ctx.locked.includes(meta.database), change: (patch) => quickEdit(ctx, meta.database, meta.code, patch) });
  const row = target.row;
  const path = [];
  for (let r = row; r; r = rowsById.get(r.parent)) if (r.meta && r.kind === "process") path.unshift(r.meta.name);
  const d = info?.details || {};
  const production = exchanges?.production;
  const asMeta = (a) => ({ index: a.index, name: a.name, location: a.location });
  // Following a row replaces the panel, and the row that had the focus: the pop-up takes it, to keep the keys
  // (``e``: the click, where a docked panel opens the pop-up; a pop-up keeps its place)
  const follow = (next, e) => {
    body.current?.closest(".info-popup")?.focus({ preventScroll: true });
    onFollow(e?.clientX !== undefined ? { ...next, x: e.clientX, y: e.clientY } : next);
  };
  const openActivity = (index, e) => follow({ index, row: null }, e);
  // The exchange of ``index``'s product in the dataset of ``user``
  const openExchange = (index, user, e) => follow({ index, row: null, parents: [asMeta(user)], exchange: true }, e);
  // Enter on a process: it becomes the process of the tab (Ctrl/⌘+Enter: of a new tab)
  const useAsRoot = (item, inTab) => request(`/api/activity/${item.index}?method=${method}`)
    .then((found) => { if (inTab || !onUseAsRoot) onOpenTab(found); else onUseAsRoot(found); onClose(); })
    .catch((e) => setError(e.message));
  // The panels of processes (exchanges, used by, alternatives) are lists like the main one: see ProcessRow
  const base = info && { index: target.index, name: info.name, location: info.location, unit: info.unit, database: info.database, code: info.code };
  const inputsOf = (item) => Promise.all([
    request(`/api/activity/${item.index}/exchanges?method=${method}`), request(`/api/activity/${item.index}?method=${method}`)])
    .then(([found, of]) => {
      const inputs = found.inputs.filter((e) => e.amount >= 0 && !["production", "substitution", "generic production"].includes(e.type));
      return { items: inputs.slice(0, 15).map((e) => ({ ...e, of: of.cumulative_per_unit })), more: Math.max(0, inputs.length - 15) };
    });
  const usersOf = (item) => request(`/api/activity/${item.index}/users?method=${method}&limit=15${root ? `&root=${root.index}&amount=${amount}` : ""}`)
    .then((found) => {
      // Below a process, only the users in its supply chain (flows above solver noise); the users are
      // listed largest flow first, so the ones beyond the limit are the rest of the chain
      const items = found.users.filter((c) => !root || Math.abs(c.flow) > 1e-12);
      return { items, more: root ? (found.users.length >= 15 ? Math.max(0, found.in_supply_chain - items.length) : 0) : found.count - found.users.length };
    });
  // The lists of processes (used by, alternatives) are PopupLists whose rows expand (ProcessRow), with the same columns
  // (the process of the tab itself has no users in its own supply chain, but for loops and solver noise)
  const inChain = !!root && root.index !== target.index;
  const usersPanel = {
    load: usersOf, empty: "No activity uses it.", label: (u) => u.name,
    onOpen: (u, e) => openActivity(u.index, e), onExchange: (u, above, e) => openExchange(above.index, u, e), onUse: useAsRoot,
    columns: [
      { key: "name", label: "Process", value: (u) => `${u.name} ${u.location}` },
      { key: "per_unit", label: "Per unit", kind: "num", format: fmtAmount, title: `${unit(info?.unit)} ${users?.waste ? "of waste " : ""}per unit of the activity's product`,
        value: (u) => Math.abs(u.per_unit) },
      { key: "unit", label: "Unit", kind: "unit", value: (u, above = base) => `${unit(above?.unit)}/${unit(u.unit)}` },
      ...(inChain ? [
        { key: "flow", label: "In chain", kind: "num", format: fmtAmount, title: `Amount it takes in the supply chain of ${root.name}`, value: (u) => (Math.abs(u.flow) > 1e-12 ? Math.abs(u.flow) : null) },
        { key: "chain_unit", label: "Unit", kind: "unit", width: 56, value: (u, above = base) => (Math.abs(u.flow) > 1e-12 ? unit(above?.unit) : "") },
        { key: "share", label: "Share", kind: "share", title: "Share of what the supply chain makes of it", value: (u) => u.share || null },
      ] : []),
    ],
  };
  // Edit mode, on the pop-up of an input of a process: "use instead" links that exchange, in the dataset(s) of the
  // process(es) above, to an alternative (same amount): staged like any other change
  const replaceable = ctx.editing && !exchange && !target.direct && parentMetas.length > 0;
  async function useInstead(a) {
    try {
      const alt = await request(`/api/activity/${a.index}?method=${method}`);
      const node = { database: alt.database, code: alt.code, name: alt.name, reference_product: alt.reference_product,
        location: alt.location, unit: alt.unit, categories: [], type: "process" };
      for (const m of parentMetas) {
        const found = await request(`/api/activity/${m.index}/exchanges?product=${target.index}`);
        const e = found.inputs[0];
        if (!e) throw new Error(`This input is not in the dataset of ${m.name}`);
        // (a model built before the build recorded what exchanges link to: the product itself is what the dataset links to)
        await quickEdit(ctx, m.database, m.code, patchSupplier(e.input || [info.database, info.code], node));
      }
      onClose();
    } catch (err) { setError(err.message); }
  }
  const alternativesPanel = {
    load: inputsOf, empty: "No input from the technosphere.", label: (a) => a.name,
    chip: (a, depth) => (a.current && depth === 0 ? html` <span class="muted">current</span>` : ""), rowClass: (a, depth) => (a.current && depth === 0 ? "current" : ""),
    onOpen: (a, e) => openActivity(a.index, e), onExchange: (a, above, e) => openExchange(a.index, above, e), onUse: useAsRoot,
    columns: [
      { key: "name", label: "Process", value: (a) => `${a.name} ${a.location}` },
      { key: "per_unit", label: "Impact", kind: "num", title: `Cumulative impact per unit of ${alternatives?.product ?? "the product"} (below an expanded one: of each input)`,
        value: (a, above, depth) => (depth ? a.impact || null : a.per_unit) },
      { key: "unit", label: "Unit", kind: "unit", width: 130, value: (a, above, depth) => `${methodInfo.unit} / ${unit(depth ? above?.unit : a.unit ?? alternatives?.unit)}` },
      { key: "relative", label: "vs. current", kind: "num", title: "Its impact over the current supplier's (below an expanded one: the share of each input in it)",
        value: (a, above, depth) => (depth ? (a.impact && a.of ? a.impact / a.of : null) : a.relative),
        cell: (a, above, depth) => (depth ? html`<span class="muted">${a.impact && a.of ? `${pct(a.impact / a.of)} of it` : ""}</span>`
          : a.relative === null || a.relative === undefined ? "" : `${a.relative > 0 ? "+" : ""}${pct(a.relative)}`) },
      ...(replaceable ? [{ key: "use", label: "", width: 96, sort: false, value: () => "",
        cell: (a, above, depth) => (depth === 0 && !a.current ? html`<button class="chip" title="Link this input to that activity instead (same amount): staged, applied with the other changes"
          onClick=${(e) => { e.stopPropagation(); useInstead(a); }}>use instead</button>` : "") }] : []),
    ],
  };
  // Characterisation: its total impacts per unit of its product (cumulative, G, and direct, U: nothing solved) in the
  // method of the tab and in the methods shown together (the Methods panel)
  const [characterisation, setCharacterisation] = useState(null);
  const chartMethods = useContext(LensContext)?.chartMethods || [];
  const totalMethods = [method, ...chartMethods.filter((k) => k !== method)].filter((k) => model.methods[k]);
  // The exchange pop-up is its one panel; the activity's: Explore first (the tree of a tab; the explorer of a pop-up: a
  // direct emissions row opens on its emissions), then its exchanges as the dataset records them, its impacts, what
  // makes its score (a tab's card only: the Contribution panel, of the tab's process and amount), users, alternatives,
  // information (what the process is, and its description)
  const tabs = (info || docked) && !exchange ? [
    docked ? { id: "explore", label: "Explore" } : exchanges?.available && { id: "explore", label: "Explore" },
    info && exchanges && { id: "exchanges", label: exchanges.available ? `Exchanges · ${(production ? 1 : 0) + exchanges.inputs.length + exchanges.flows.length}` : "Exchanges" },
    info && { id: "characterisation", label: `Characterisation · ${totalMethods.length}` },
    card?.contribution && { id: "contribution", label: "Contribution" },
    info && { id: "users", label: `${users?.waste ? "Treats" : "Used by"}${users ? ` · ${users.count}` : ""}` },
    alternatives && !target.direct && { id: "alternatives", label: `Alternatives · ${alternatives.count}` },
    info && { id: "information", label: "Information" },
  ].filter(Boolean) : [];
  const shown = exchange ? "tree" : tabs.some((t) => t.id === tab) ? tab : tabs[0]?.id;
  useEffect(() => { body.current?.scrollTo(0, 0); }, [shown]);
  // A docked card (a tab): the keys of the pop-ups in the rows of its panels (↑ on the first row: back to its active
  // panel tab); the tree has its own keys, and its tab bar ``tabKeys``
  const tabsBar = useRef(null);
  const dockedKeys = (e) => {
    if (!body.current?.contains(e.target)) return;
    popupKeys({ tabs, shown, setTab, body, onClose, toTabs: () => tabsBar.current?.querySelector("button.active")?.focus() })(e);
  };
  useEffect(() => {
    if (shown !== "characterisation" || idle) return;
    let live = true;
    request(`/api/activity/${target.index}/characterisation?method=${method}`)
      .then((c) => live && setCharacterisation({ ...c, index: target.index })).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [shown, target.index, method, idle]);
  const characterised = characterisation?.index === target.index && characterisation.method === method ? characterisation : null;
  const totals = characterised && totalMethods.filter((k) => k < characterised.totals.direct.length)
    .map((k) => ({ method: k, direct: characterised.totals.direct[k], cumulative: characterised.totals.cumulative[k] }));
  return html`
    <${docked ? DockedShell : PopupWindow} target=${target} win=${win} label=${exchange ? "Exchange information" : "Activity information"} onClose=${onClose} onBack=${onBack}
      onDock=${info && !exchange ? (place) => { onOpenTab(info, place); onClose(); } : null}
      menu=${info && !exchange ? [
        ...(onUseAsRoot ? [{ label: "Use in this tab", title: "Show this process in the tab in focus", run: () => { onUseAsRoot(info); onClose(); } }] : []),
        { label: "Open in new tab", title: "Open it in a new tab (or drag the pop-up by its header to a tab list or the edge of a pane)", run: () => { onOpenTab(info); onClose(); } },
        ...(ctx.editing && ctx.origin ? [{ label: "Edit…", disabled: locked,
          title: locked ? "Its database is locked: unlock it in the Projects dialog" : "Open the form of this process: the change is staged, then applied with the others",
          run: () => { ctx.openForm({ edit: { database: info.database, code: info.code } }); onClose(); } }] : []),
      ] : []}
      onKeyDown=${docked ? dockedKeys : popupKeys({ tabs, shown, setTab, body, onClose, exchange, explorerKeys })}
      head=${html`
        <div class="info-title">${target.direct ? "Direct emissions of " : ""}${(target.direct ? rowsById.get(row?.parent)?.custom : row?.custom)
          || (info ? processTitle(info) : "…")}${" "}
          ${info && html`<span class="loc-chip">${info.location}</span>`}
          ${stagedHere && html` <span class="chip staged" title="A change of this process is staged: the values below are those of the last build">changes staged</span>`}
          ${ctx.editing && locked && html` <span class="chip" title="Its database is locked">🔒 locked</span>`}</div>
        ${info && (target.direct ? rowsById.get(row?.parent)?.custom : row?.custom) && html`<div class="muted">renamed in this view · ${info.name}</div>`}
        ${info && (exchange ? html`<div class="muted">Exchange · ${info.reference_product} · ${unit(info.unit)}</div>`
          : html`<div class="muted">${info.activity_type} · ${info.reference_product} · ${unit(info.unit)}</div>`)}`}>
      ${error && html`<div class="error">${error}</div>`}
      ${(info || docked) && html`
        ${!exchange && html`<div class="tabs info-tabs" role="tablist" ref=${tabsBar} onKeyDown=${docked ? ((e) => {
          if (e.key !== "ArrowDown") { tabKeys(tabs.map((t) => t.id), shown, setTab)(e); return; }
          // ↓: into the panel, on its first row (the tree: ``onDown``)
          e.preventDefault(); e.stopPropagation();
          if (shown === "explore") { onDown?.(); return; }
          const first = body.current?.querySelector("[data-nav]");
          if (first) { first.focus({ preventScroll: true }); first.scrollIntoView({ block: "nearest" }); } else body.current?.scrollBy(0, 48);
        }) : undefined}>
          ${tabs.map((t) => html`
            <button role="tab" aria-selected=${t.id === shown} class=${t.id === shown ? "active" : ""}
              onClick=${() => setTab(t.id)}>${t.label}</button>`)}
        </div>`}
        ${docked && html`<div class="docked-tree" style=${{ display: shown === "explore" ? "" : "none" }}>${treeSlot}</div>`}
        <div class="info-body" ref=${body} style=${docked && shown === "explore" ? { display: "none" } : null}>
          ${shown === "tree" && html`
            <section>
              <h4>${row ? "In this tree" : "Exchange"}</h4>
              <table class="kv">
                ${row?.kind === "process" && html`<tr><th>Amount</th><td>${rowAmount(row)}</td></tr>`}
                ${row && html`<tr><th>Impact</th><td>${fmt(row.impacts[0])} ${methodInfo.unit} (${pct(rootImpact ? row.impacts[0] / rootImpact : 0)} of the total)</td></tr>`}
                ${row && html`<tr><th>Path</th><td>${path.join(" → ")}</td></tr>`}
                ${link?.map(({ meta, inputs }) => inputs.map((e) => html`
                  <tr><th>Input</th><td><${Editable} value=${Math.abs(e.amount)} format=${fmtAmount} disabled=${inDataset(meta).locked || !e.input}
                    title=${e.input ? undefined : "Build this database again once, to edit amounts here"}
                    onCommit=${(t) => inDataset(meta).change(patchExchange(e.input, e.amount, t))} /> ${unit(e.unit)} in the dataset of ${meta.name}
                    <span class="loc-chip">${meta.location}</span>${e.type !== "technosphere" ? html` <span class="muted">(${e.type})</span>` : ""}</td></tr>`))}
              </table>
              ${link?.map(({ meta, inputs }) => inputs.filter(hasExchangeMeta).map((e) => html`
                ${link.length > 1 && html`<p class="muted">In the dataset of ${meta.name} <span class="loc-chip">${meta.location}</span></p>`}
                <${ExchangeMeta} e=${e} />`))}
              ${link && !link.some((l) => l.inputs.some(hasExchangeMeta)) && html`
                <p class="text muted">No comment or uncertainty recorded for this exchange.</p>`}
            </section>`}
          ${exchanges?.available && !docked && html`
            <${FUView} key=${target.index} id=${`exchanges-${target.index}`} info=${model} visible=${shown === "explore"}
              initial=${{ root: base, inherit: { method, chartMethods: [], cutoff: 500, cutoffBasis: "top", rules: [] } }}
              embedded=${{ method, onInfo: follow, onUse: useAsRoot, keys: explorerKeys }} onSave=${() => {}} onTitle=${() => {}} />`}
          ${shown === "exchanges" && info && exchanges && html`
            <section>
              <${ProcessExchanges} index=${target.index} info=${info} exchanges=${exchanges}
                onOpen=${(r, e) => (r.kind === "flow" ? follow({ flow: r.index }, e) : openActivity(r.index, e))}
                onExchange=${(r, e) => (r.kind === "flow" ? follow({ flow: r.index, activities: [target.index], exchange: true }, e) : openExchange(r.index, base, e))}
                onUse=${(r, inTab) => (r.kind === "flow" ? use?.flow({ flow: r.index }, inTab) : useAsRoot(r, inTab))} />
            </section>`}
          ${shown === "characterisation" && info && html`
            <section>
              ${!characterised ? html`<${Waiting} text="Reading its impacts…" />` : html`
                <p class="muted list-summary">Per ${unit(info.unit)} of ${info.reference_product}, with its supply chain (<i>Total</i>) and
                  from its own exchanges with nature (<i>Direct</i>), in the main method and the methods shown together (ticked in the
                  Methods panel).</p>
                <${PopupList} key=${`totals-${target.index}`} noun="method" rows=${totals} rowKey=${(t) => t.method}
                  rowClass=${(t) => (t.method === method ? "current" : "")}
                  onOpen=${(t, e) => follow({ category: t.method }, e)} onUse=${use && ((t, inTab) => use.model(t.method, inTab))}
                  columns=${[
                    { key: "method", label: "Method", title: "The main method and the methods shown together: open one for its model card",
                      value: (t) => methodTitle(model.methods[t.method]), tip: (t) => `${model.methods[t.method].name.join(" | ")} [${model.methods[t.method].unit}]: its model card`,
                      chip: (t) => (t.method === method ? html`<span class="muted">main</span>` : "") },
                    { key: "cumulative", label: "Total", kind: "num", title: "With its supply chain (cumulative)", value: (t) => t.cumulative },
                    { key: "direct", label: "Direct", kind: "num", title: "Its own exchanges with nature", value: (t) => t.direct },
                    { key: "unit", label: "Unit", kind: "unit", width: 150, value: (t) => `${model.methods[t.method].unit} / ${unit(info.unit)}` },
                    { key: "share", label: "Direct share", kind: "share", title: "Share of the total that is direct", value: (t) => (t.cumulative ? t.direct / t.cumulative : null) },
                  ]} />`}
            </section>
            ${!idle && card?.characterisation}`}
          ${shown === "contribution" && !idle && card?.contribution}
          ${shown === "users" && users && info && html`
            <section>
              <${PopupList} key=${`users-${target.index}`} noun="process" rows=${users.users} rowKey=${(u) => u.index} columns=${usersPanel.columns}
                empty=${users.waste ? "No activity in the database generates this waste." : "No activity in the database uses this product."}
                renderRow=${(u) => html`<${ProcessRow} key=${u.index} item=${u} above=${base} depth=${0} ancestors=${[target.index]} panel=${usersPanel} />`}
                more=${users.count > users.users.length && html`<button class="link" onClick=${() => setAllUsers(true)}>Show all ${users.count}</button>`} />
            </section>`}
          ${shown === "alternatives" && alternatives && html`
            <section>
              <${PopupList} key=${`alternatives-${target.index}`} noun="process" columns=${alternativesPanel.columns}
                rows=${[{ ...alternatives.current, current: true, relative: null }, ...alternatives.alternatives]} rowKey=${(a) => a.index} pinned=${(a) => a.current}
                renderRow=${(a) => html`<${ProcessRow} key=${a.index} item=${a} above=${null} depth=${0} ancestors=${a.current ? [] : [target.index]} panel=${alternativesPanel} />`}
                more=${alternatives.count > alternatives.alternatives.length && `+ ${alternatives.count - alternatives.alternatives.length} more, not listed`} />
            </section>`}
          ${shown === "information" && info && html`
          <section>
            <table class="kv">
              <tr><th>Product</th><td>${info.reference_product ?? "–"}</td></tr>
              <tr><th>Unit</th><td title="The unit of its reference product and what it measures">${unitWithQuantity(info.unit)}</td></tr>
              <tr><th>Activity</th><td>${info.name}</td></tr>
              <tr><th>Location</th><td>${info.location ? `${info.location}${info.location_name ? ` · ${info.location_name}` : ""}` : "–"}</td></tr>
              ${info.activity_type && html`<tr><th>Activity type</th><td title="Market (a mix of the suppliers of a product), production, treatment…">${info.activity_type}</td></tr>`}
              ${info.multifunctional && html`<tr><th>Products</th><td>Multifunctional: it has more than one product</td></tr>`}
              ${(d.start_date || d.end_date) && html`<tr><th>Time period</th><td>${d.start_date ?? "?"} – ${d.end_date ?? "?"}</td></tr>`}
              ${info.isic && html`<tr><th>ISIC</th><td>${info.isic}</td></tr>`}
              ${info.cpc && html`<tr><th>CPC</th><td>${info.cpc}</td></tr>`}
              ${(d.classifications || []).filter(([system]) => !(info.isic && /isic/i.test(system)) && !(info.cpc && /cpc/i.test(system)))
                .map(([system, value]) => html`<tr><th>${system}</th><td>${value}</td></tr>`)}
              ${d.synonyms && html`<tr><th>Synonyms</th><td>${d.synonyms.join("; ")}</td></tr>`}
              ${d.authors && Object.entries(d.authors).map(([role, name]) => html`<tr><th>${role}</th><td>${name}</td></tr>`)}
              <tr><th>Database</th><td>${info.database}</td></tr>
              ${info.code && html`<tr><th>Code</th><td class="mono muted">${info.code}</td></tr>`}
              ${d.filename && html`<tr><th>File</th><td class="muted">${d.filename}</td></tr>`}
            </table>
          </section>
          ${ctx.editing && html`<section><h4>Description</h4><p class="text"><${Editable} multiline value=${d.comment || ""} display=${d.comment ? String(d.comment).trim() : "(none: double-click to add one)"}
            edit=${ds ? ds.process.comment : d.comment || ""} disabled=${locked} title="Double-click to change the description (Ctrl+Enter to stage it)"
            onCommit=${(t) => own((dr) => { dr.process.comment = t; })} /></p></section>`}
          ${DETAIL_SECTIONS.filter(([k]) => d[k] && !(ctx.editing && k === "comment")).map(([k, title]) => html`
            <section><h4>${title}</h4><p class="text">${String(d[k]).split("\n").filter((line) => line.trim() !== "None").join("\n").trim()}</p></section>`)}
          ${!ctx.editing && !DETAIL_SECTIONS.some(([k]) => d[k]) && html`
            <p class="muted">No description: the dataset has none, or the model is not opened from its Brightway project (open it from <i>Projects</i>).</p>`}
          ${production && (production.comment || production.properties || production["production volume"]) && html`
            <section>
              <h4>Reference product exchange</h4>
              <table class="kv">
                ${production["production volume"] !== undefined && html`
                  <tr><th>Production volume</th><td>${fmtAmount(production["production volume"])} ${unit(production.unit)} per year</td></tr>`}
                ${production.comment && html`<tr><th>Comment</th><td>${production.comment}</td></tr>`}
                ${Object.entries(production.properties || {}).map(([name, p]) => html`
                  <tr><th>${name}</th><td title=${p.comment || ""}>${typeof p.amount === "number" ? fmtAmount(p.amount) : p.amount} ${p.unit ?? ""}
                    ${p.comment && html`<span class="muted">· ${truncate(p.comment, 80)}</span>`}</td></tr>`)}
              </table>
            </section>`}`}
        </div>`}
    <//>`;
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

// ---------------------------------------------------------------- name display of processes and flows
// How processes and flows are named everywhere (the tree, the Sankey, the charts, the pop-ups, the tab titles, the
// Processes panel and Compare mode): the *Name display* of the Processes panel, one setting of the workspace kept in the
// lens, not per tab. A format for the processes and one for the flows, each a preset or the custom recipe (a main part,
// and a secondary part in grey, with the tokens of RECIPE_TOKENS and FLOW_RECIPE_TOKENS).
const PROCESS_NAMINGS = {
  product: { label: "Product", hint: "The reference product, the location in grey", main: "{product}", secondary: "{location}" },
  activity: { label: "Activity", hint: "The activity name, the location in grey (the full names of before)", main: "{name}", secondary: "{location}" },
  custom: { label: "Custom", hint: "Your format, with tokens" },
};
const FLOW_NAMINGS = {
  full: { label: "Full", hint: "The substance, its compartment in grey", flowMain: "{substance}", flowSecondary: "{compartment}, {subcompartment}" },
  short: { label: "Substance", hint: "The substance alone", flowMain: "{substance}", flowSecondary: "" },
  custom: { label: "Custom", hint: "Your format, with tokens" },
};
// The default: the product as the name, the location as the qualifier; flows with their compartment
const DEFAULT_NAMING = { process: "product", flow: "full", recipe: { ...DEFAULT_RECIPE } };
let nameDisplay = DEFAULT_NAMING;  // the lens's, set by App
// The recipe of a name display: the presets' parts, or the custom recipe's
function namingRecipe(n = nameDisplay) {
  const own = { ...DEFAULT_RECIPE, ...(n.recipe || {}) };
  const p = n.process !== "custom" && PROCESS_NAMINGS[n.process], f = n.flow !== "custom" && FLOW_NAMINGS[n.flow];
  return { main: p ? p.main : own.main, secondary: p ? p.secondary : own.secondary,
    flowMain: f ? f.flowMain : own.flowMain, flowSecondary: f ? f.flowSecondary : own.flowSecondary };
}
// ``view.naming`` and ``view.recipe`` as the readers of a view know them (the tree, the Sankey, the chart, the members
// pop-ups): the full names of before for activity names with full flows, else the recipe of the name display
const viewNaming = (n = nameDisplay) => ({ naming: n.process === "activity" && n.flow === "full" ? "full" : "custom", recipe: namingRecipe(n) });
// The name display kept in the lens. Before it, each tab had a *Names* option (a preference): Short named processes by
// their product and flows by their substance, Custom used its recipe; Full, the default then, takes the new default
function namingOf(stored) {
  if (stored && typeof stored === "object") {
    return { process: stored.process in PROCESS_NAMINGS ? stored.process : DEFAULT_NAMING.process,
      flow: stored.flow in FLOW_NAMINGS ? stored.flow : DEFAULT_NAMING.flow, recipe: { ...DEFAULT_RECIPE, ...(stored.recipe || {}) } };
  }
  const recipe = Object.fromEntries(Object.entries(RECIPE_PREFERENCES).map(([part, key]) => [part, loadPreference(key, DEFAULT_RECIPE[part])]));
  const old = loadPreference("naming", "full");
  if (old === "custom") return { process: "custom", flow: "custom", recipe };
  if (old === "short") return { process: "product", flow: "short", recipe };
  return { ...DEFAULT_NAMING, recipe };
}
// A process (``meta``: name, reference_product, location… as the API gives them) named by the name display:
// { main, qual }. A process with no product (a tab saved before titles kept it) goes by its activity name.
function processNames(meta, n = nameDisplay) {
  const recipe = namingRecipe(n);
  return { main: applyRecipe(recipe.main, [meta]) || meta.name || "", qual: applyRecipe(recipe.secondary, [meta], 6) };
}
// The title of a process card (its pop-up, its tab, its row of the Processes panel)
const processTitle = (info) => processNames(info).main;
// A flow ({ name, compartment, subcompartment } or { name, place: "air, urban air…" }) named by the name display: { main, qual }
function flowTitleNames(f, n = nameDisplay) {
  const split = f.subcompartment === undefined;
  const [compartment, ...sub] = String((split ? f.place ?? f.compartment : f.compartment) || "").split(", ");
  const flow = { name: f.name, compartment: split ? compartment : f.compartment, subcompartment: split ? sub.join(", ") : f.subcompartment };
  return flowNames(flow, "custom", namingRecipe(n));
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

function nameItem(item, naming, recipe, byId, withEmitter = true) {
  const names = baseNameItem(item, naming, recipe, byId, withEmitter);
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
    if (p?.custom) return { ...names, qual: [withEmitter && `direct: ${p.custom}`, row.kind === "flow" && flowNames(row.flow, naming, recipe).qual].filter(Boolean).join(" · ") };
  }
  return names;
}
// ``withEmitter``: a flow says whose direct emissions it is (the chart leaves it out unless its rows are split by type)
function baseNameItem(item, naming, recipe, byId, withEmitter = true) {
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
    return { main: flow ? flow.main : rows[0].label, qual: [withEmitter && `direct: ${emitter}`, flow && flow.qual].filter(Boolean).join(" · ") };
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
  // Collapse singletons: the processes made of this one alone, above it
  const via = r.chain?.length ? `via ${r.chain.map((m) => m.name).join(" › ")}` : "";
  return { ...names, ...(r.custom ? { main: r.custom } : {}), ...(via ? { qual: [via, names.qual].filter(Boolean).join(" · ") } : {}) };
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

// The colours of the bars of a chart, each with what it stands for: a rule of the Rules panel, or a type of exchange; the
// bars with no colour are "no rule"
function legendOf(items, colors, byId, view) {
  const mode = colorModeOf(view);
  if (mode === "none") return [];
  const keys = new Map();
  let plain = false;
  for (const p of items.slice().reverse()) {
    const [row] = p.rows;
    const colour = colors.get(row.id);
    if (!colour) { plain = true; continue; }
    let name;
    if (mode !== "group") name = colorKeyOf(row, mode)?.name;
    else { let r = row; while (r && !r.rule) r = byId.get(r.parent); name = r?.rule; }
    if (name && !keys.has(name)) keys.set(name, colour);
  }
  const out = [...keys].map(([name, color]) => ({ name, color }));
  if (out.length && plain) out.push({ name: mode === "group" ? "no rule" : "other", color: cssVar("--bar") });
  return out;
}

function nameItems(items, naming, recipe, byId, withEmitter = true) {
  const named = items.map((item) => ({ ...item, ...nameItem(item, naming, recipe, byId, withEmitter) }));
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

// ``kinds``: the recipes shown, of processes and of flows (the Name display shows those set to Custom)
function RecipeEditor({ recipe, setRecipe, example, kinds = ["process", "flow"] }) {
  const inputs = { main: useRef(null), secondary: useRef(null), flowMain: useRef(null), flowSecondary: useRef(null) };
  const [chosen, setTarget] = useState(kinds[0] === "flow" ? "flowMain" : "main");  // field receiving inserted tokens
  const target = kinds.includes(chosen.startsWith("flow") ? "flow" : "process") ? chosen : kinds[0] === "flow" ? "flowMain" : "main";
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
      ${kinds.includes("process") && html`<div class="row">
        <span class="recipe-kind">Processes</span>
        ${field("main", "Main")}
        ${field("secondary", "Secondary (grey)")}
      </div>`}
      ${kinds.includes("flow") && html`<div class="row">
        <span class="recipe-kind">Flows</span>
        ${field("flowMain", "Main")}
        ${field("flowSecondary", "Secondary (grey)")}
      </div>`}
      <div class="row tokens">Insert in ${target.startsWith("flow") ? "flow" : "process"} ${target.endsWith("ain") ? "main" : "secondary"}:
        ${Object.keys(target.startsWith("flow") ? FLOW_RECIPE_TOKENS : RECIPE_TOKENS).map((t) => html`<button class="token" onClick=${() => insert(t)}>{${t}}</button>`)}
        <button onClick=${() => setRecipe({ ...recipe, ...pick(DEFAULT_RECIPE, kinds.flatMap((k) => (k === "flow" ? ["flowMain", "flowSecondary"] : ["main", "secondary"]))) })}
          title="The default recipe">Default</button>
      </div>
      ${example && html`<p class="note">e.g. <span class="example-main">${example.main}</span>${example.qual && html` <span class="muted">${example.qual}</span>`}${" — when items are combined, their values are listed comma-separated"}</p>`}
    </div>`;
}

// ---------------------------------------------------------------- panels of a process card: charts and contributions
// What each panel shows, in a popup on its name (the Contribution panel's sub-panels, the left panel's tabs)
const PANEL_TIPS = {
  chart: "What each process of the supply chain adds by its own emissions, wherever it is in the tree.",
  flows: "Which substances drive the score, and which processes emit them.",
  missing: "The flows of the inventory that the active method has no factor for: what the score leaves out, and which another method characterises.",
  groups: "Group processes by your own criteria, beyond how individual flow or process contribute.",
  processes: "Your open tabs: compare, reorder, close. Drop a process here to open it.",
  methods: "The indicator of the tree and charts, and the ones to show together.",
  global: "The score split by your rules over the whole supply chain, to see what each rule weighs.",
};
// The sub-panels of the Contribution panel of a process tab's card, in the order of its tab bar (they were the side
// panel's, before the side panel left View mode)
const CONTRIBUTION_PANELS = [["chart", "Process"], ["flows", "Flow"], ["missing", "Uncharacterised"], ["global", "Rules"]];
// The Contribution panel: its sub-panels' tab bar (← → switch them, the focus staying on the bar) and the one chosen.
// ``panel``: the node of the sub-panel shown
function ContributionPanels({ tab, setTab, panel }) {
  return html`
    <section class="card-contribution">
      <div class="tabs contribution-tabs" role="tablist" onKeyDown=${tabKeys(CONTRIBUTION_PANELS.map(([key]) => key), tab, setTab)}>
        ${CONTRIBUTION_PANELS.map(([key, label]) => html`
          <${TabButton} key=${key} role="tab" class=${tab === key ? "active" : ""} aria-selected=${tab === key} onClick=${() => setTab(key)}
            tip=${PANEL_TIPS[key]}>${label}<//>`)}
      </div>
      ${panel}
    </section>`;
}

// The rows of the tree as its charts and exports read them: its frontier (chartItems), named by the name display
function useTreeParts(data, view) {
  const byId = useMemo(() => new Map(data.rows.map((r) => [r.id, r])), [data]);
  const parts = useMemo(
    () => nameItems(chartItems(data.rows, byId), view.naming, view.recipe, byId, view.organize === "type"),
    [data, byId, view.naming, view.recipe, view.organize, nameStamp],
  );
  return { byId, parts };
}
// The single indicator chart of the tree's rows (the main method, a bar per row, coloured by its rule): { option, height }
function singleChartOption({ parts, byId, data, info, method, view }) {
  const unit = info.methods[method].unit;
  const total = parts.reduce((s, p) => s + p.impacts[0], 0);
  const colors = groupColors(data.rows, view);  // bars of rows in a group (or of the group) take the colour of its rule
  const o = baseOption();
  const items = [...parts].reverse();
  const shown = items.map((p) => fitLabel(p.main, p.qual, LABEL_WIDTH));
  o.grid = { ...o.grid, containLabel: false, top: 36, bottom: 28,
    left: Math.max(...shown.map(([m, q]) => textWidth(q ? `${m} ${q}` : m))) + 16 };
  o.title = { text: methodTitle(info.methods[method]), textStyle: { fontSize: 12, color: cssVar("--muted") } };
  // The legend of the colours: the rules (or the flow types) of the bars, wrapped on as many lines as it needs, in the image too
  const legendKeys = legendOf(items, colors, byId, view);
  let lines = 0;
  if (legendKeys.length) {
    lines = Math.max(1, Math.ceil(legendKeys.reduce((w, k) => w + textWidth(k.name) + 34, 0) / 460));
    o.legend = { type: "plain", bottom: 4, left: "center", width: "92%", selectedMode: false, itemWidth: 12, itemHeight: 12, itemGap: 14,
      icon: "roundRect", textStyle: { color: cssVar("--text"), fontSize: 11 }, data: legendKeys.map((k) => ({ name: k.name, itemStyle: { color: k.color } })) };
    o.grid = { ...o.grid, bottom: 28 + lines * 20 };
  }
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
  o.series = [{
    type: "bar", data: items.map((p) => ({
      value: p.impacts[0],
      itemStyle: { color: colors.get(p.rows[0].id) || (p.impacts[0] < 0 ? cssVar("--bar-negative") : cssVar("--bar")) },
    })),
    label: { show: true, position: "right", formatter: (d) => fmt(d.value), color: cssVar("--muted") },
  }, ...legendKeys.map((k) => ({ type: "scatter", name: k.name, data: [], silent: true, itemStyle: { color: k.color } }))];  // the keys of the legend, with no bar to share the width
  return { option: o, height: Math.max(260, parts.length * 26 + 60) + 24 * lines };
}
// A chart drawn off the page, for its image only (``opts``: those of getDataURL)
function offscreenImage({ option, height }, opts, width = 800) {
  const box = document.createElement("div");
  box.style.cssText = `position: fixed; left: -10000px; top: 0; width: ${width}px; height: ${height}px`;
  document.body.appendChild(box);
  const chart = echarts.init(box);
  try {
    chart.setOption({ ...option, animation: false });
    return chart.getDataURL(opts);
  } finally { chart.dispose(); box.remove(); }
}
// The export of the Explore panel (at the right of the tree's foot, by its total): the tree's rows as a CSV file
// (every method of the rows), and the single indicator chart of them as an image, drawn for it (the chart is not on the
// page any more: it was the side panel's)
function TreeExport({ data, info, method, view }) {
  const { byId, parts } = useTreeParts(data, view);
  const image = { get current() { return { getDataURL: (opts) => offscreenImage(singleChartOption({ parts, byId, data, info, method, view }), opts) }; } };
  const csv = () => downloadCsv("contributions.csv", ["row", "kind", "merged rows", ...data.methods.map((m) => methodTitle(info.methods[m]))],
    parts.map((p) => [p.label, p.kind, p.rows.length, ...p.impacts]));
  return html`<span class="card-tools"><${ExportMenu} chart=${image} csv=${csv} name="contributions" /></span>`;
}
// The tree's rows in the methods shown together (the Methods panel), each method as 100 %: the chart, the table of the
// same shares (the total of each method in its unit at the foot) and their export. In the Characterisation panel of a
// process tab's card. The largest 11 rows, the rest in one.
function MultiIndicator({ data, info, chartMethods, view, onPickMethods }) {
  const chartRef = useRef(null);
  const { parts } = useTreeParts(data, view);
  const positions = chartMethods.map((m) => data.methods.indexOf(m)).filter((i) => i >= 0);
  const { segments, totals } = useMemo(() => {
    const top = parts.slice(0, 11);
    const rest = parts.slice(11);
    const list = top.map((p) => ({ name: truncate(p.label, 60), label: p.label, impacts: p.impacts }));
    if (rest.length) list.push({ name: `${rest.length} more rows`, label: `${rest.length} more rows`, impacts: data.methods.map((_, k) => rest.reduce((s, p) => s + p.impacts[k], 0)) });
    return { segments: list, totals: data.methods.map((_, k) => parts.reduce((s, p) => s + p.impacts[k], 0)) };
  }, [parts, data]);
  const share = (s, k) => (totals[k] ? s.impacts[k] / totals[k] : 0);
  const option = useMemo(() => {
    const o = baseOption();
    const categories = positions.map((k) => truncate(methodRow(info.methods[data.methods[k]]), 50));
    const lines = Math.max(1, Math.ceil(segments.reduce((w, k) => w + textWidth(k.name) + 34, 0) / 460));
    o.legend = { type: "plain", bottom: 4, left: "center", width: "92%", selectedMode: false, itemWidth: 12, itemHeight: 12, itemGap: 14,
      icon: "roundRect", textStyle: { color: cssVar("--text"), fontSize: 11 } };
    o.grid.bottom = 28 + lines * 20;
    o.yAxis = { ...o.yAxis, type: "category", data: categories, inverse: true };
    o.xAxis = { ...o.xAxis, type: "value", axisLabel: { color: cssVar("--muted"), formatter: (v) => `${v}%` } };
    o.tooltip.valueFormatter = (v) => `${v.toFixed(1)}%`;
    o.series = segments.map((s) => ({ type: "bar", stack: "total", name: s.name, data: positions.map((k) => 100 * share(s, k)) }));
    return o;
  }, [segments, totals, chartMethods, data, nameStamp]);
  const title = (k) => methodTitle(info.methods[data.methods[k]]);
  const unitOf = (k) => info.methods[data.methods[k]].unit;
  function exportCsv() {
    downloadCsv("multi-indicator.csv", ["row", ...positions.flatMap((k) => [`${title(k)} [${unitOf(k)}]`, `${title(k)} share`])],
      [...segments.map((s) => [s.label, ...positions.flatMap((k) => [s.impacts[k], share(s, k)])]),
        ["total", ...positions.flatMap((k) => [totals[k], 1])]]);
  }
  return html`
    <section class="card-multi">
      <h4>Multi indicator <span class="muted">· the tree's rows in the methods shown together</span></h4>
      <div class="row">
        <button onClick=${onPickMethods} title="Tick the methods in the Methods panel">${chartMethods.length} ${chartMethods.length === 1 ? "method" : "methods"} shown together</button>
        <span class="spacer" style="flex:1"></span>
        ${positions.length > 0 && segments.length > 0 && html`<${ExportMenu} chart=${chartRef} csv=${exportCsv} name="multi-indicator" />`}
      </div>
      ${!positions.length ? html`<div class="empty">Select methods to compare (tick them in the Methods panel).</div>`
        : !segments.length ? html`<div class="empty">The chart of the tree's rows, once a process is shown.</div>`
        : html`
          <${Chart} exportRef=${chartRef} option=${option} height=${Math.max(300, chartMethods.length * 28 + 120) + Math.max(0, option.grid.bottom - 40)} />
          <div class="multi-table-box">
            <table class="results multi-table">
              <thead><tr><th>Row</th>${positions.map((k) => html`<th class="num" key=${k} title=${`${methodLabel(info.methods[data.methods[k]])}: each row's share of the total`}>${truncate(title(k), 28)}</th>`)}</tr></thead>
              <tbody>${segments.map((s) => html`
                <tr key=${s.label}><td title=${s.label}>${s.name}</td>${positions.map((k) => html`<td class="num" key=${k} title=${`${fmt(s.impacts[k])} ${unitOf(k)}`}>${pct(share(s, k))}</td>`)}</tr>`)}
              </tbody>
              <tfoot><tr><th>Total</th>${positions.map((k) => html`<th class="num" key=${k}>${fmt(totals[k])} <span class="muted">${unitOf(k)}</span></th>`)}</tr></tfoot>
            </table>
          </div>`}
    </section>`;
}

// The filters of a flat list of rows, the cut-offs of the view options that make sense for it (the server cuts the list as
// it cuts the tree): the largest ``value`` rows, the rows under ``value`` % of the total (to others), the largest rows until
// ``value`` % of all the rows
const LIST_CUTS = {
  top: { label: "Top", unit: "", hint: "The largest rows", default: 20, step: 5 },
  total: { label: "Cut-off", unit: "%", hint: "Rows under this share of the total go to others", default: 1, step: 0.5, max: 100 },
  cumulative: { label: "Cumulative", unit: "%", hint: "The largest rows until this share of the total", default: 90, step: 5, max: 100 },
};
// A filter as the server takes it (a share as a fraction, as the cut-off of a tree); ``ALL_ROWS``: no filter
const apiCut = ({ mode, value }) => ({ mode, value: cutoffValue(value, mode) });
const ALL_ROWS = { mode: "top", value: 0 };
// The control: the type of the filter (a click on its name) and its value, in little room
function CutField({ cut, setCut, what }) {
  const m = LIST_CUTS[cut.mode];
  return html`<span class="cut-field" title=${`${m.hint} (${what})`}>
    <${PopupMenu} label=${`${m.label} ▾`} className="cut-mode" title="Type of filter"
      items=${Object.entries(LIST_CUTS).map(([k, q]) => ({ key: k, label: `${cut.mode === k ? "✓ " : "   "}${q.label}`, title: q.hint, run: () => setCut({ mode: k, value: q.default }) }))} />
    <${NumberInput} class="cutoff" value=${cut.value} min=${1} max=${m.max} step=${m.step} onValue=${(v) => setCut({ ...cut, value: v })} aria-label=${`${m.label} ${what}`} />${m.unit && html`<span class="muted">${m.unit}</span>`}
  </span>`;
}

// A list of contributions, largest first, the rest in one *others* row, as a chart and as the table of the same rows, with
// its filter (``cut``) and its export: a click on a bar or a row chooses it in both (the others fade). ``items``: the rows
// the server listed, ``others`` ({ count, impact }) the rest, ``total`` the score the shares are of, ``what`` the rows in
// words; a row: ``name`` its first cell, ``columns`` ({ head, cell, csv }) more before the impact, ``below`` the rows opened
// under it when chosen; ``controls``: more of the panel's buttons, before the filter
function ContributionList({ items, others, total, count, what, unitLabel, cut, setCut, keyOf, label, name, chosen, onChoose,
                            head, columns = [], rowProps = () => ({}), below, csv, exportName, tableClass = "", controls }) {
  const chartRef = useRef(null);
  const chosenRow = useRef(null);
  useEffect(() => { chosenRow.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [chosen]);
  const share = (v) => pct(total ? v / total : 0);
  const bars = [...items.map((it) => [label(it), it.impact, it]),
    ...(others.count > 0 ? [[`others (${others.count} ${what})`, others.impact, null]] : [])];
  const option = useMemo(() => {
    const o = baseOption();
    o.yAxis = { ...o.yAxis, type: "category", data: bars.map(([n]) => n), inverse: true, triggerEvent: true,
      axisLabel: { ...o.yAxis.axisLabel, width: 240, overflow: "truncate" } };
    o.xAxis = { ...o.xAxis, type: "value", name: unitLabel };
    o.tooltip.valueFormatter = (v) => `${fmt(v)} ${unitLabel} (${share(v)})`;
    o.series = [{ type: "bar", cursor: "pointer", data: bars.map(([, v, it]) => ({ value: v,
        itemStyle: { color: cssVar(!it ? "--muted" : v < 0 ? "--bar-negative" : "--bar"), opacity: chosen != null && !(it && keyOf(it) === chosen) ? 0.35 : 1 } })),
      label: { show: true, position: "right", formatter: (d) => share(d.value), color: cssVar("--muted") } }];
    return o;
  }, [items, others, total, unitLabel, chosen]);
  const exportCsv = () => downloadCsv(`${exportName}.csv`, [...csv.header, ...columns.filter((c) => c.csv).map((c) => c.head), "impact", "share"], [
    ...items.map((it) => [...csv.cells(it), ...columns.filter((c) => c.csv).map((c) => c.csv(it)), it.impact, total ? it.impact / total : 0]),
    ...(others.count > 0 ? [[`${others.count} more`, ...csv.header.slice(1).map(() => ""), ...columns.filter((c) => c.csv).map(() => ""),
      others.impact, total ? others.impact / total : 0]] : [])], [unitLabel]);
  const blank = columns.map((c) => html`<td key=${c.head}></td>`);
  return html`
    <div class="row">${controls}<${CutField} cut=${cut} setCut=${setCut} what=${what} /><${ExportMenu} chart=${chartRef} csv=${exportCsv} name=${exportName} /></div>
    <${Chart} exportRef=${chartRef} option=${option} height=${Math.max(200, bars.length * 26 + 60)}
      onClick=${(e) => { const i = e.componentType === "yAxis" ? bars.findIndex(([n]) => n === e.value) : e.dataIndex; const it = bars[i]?.[2]; if (it) onChoose(it); }} />
    <table class=${`results chart-results ${tableClass}`}>
      <thead><tr><th>${head}</th>${columns.map((c) => html`<th class="num" key=${c.head}>${c.head}</th>`)}<th class="num">${unitLabel}</th><th class="num">Share</th></tr></thead>
      <tbody>${items.map((it) => {
        const on = keyOf(it) === chosen;
        return html`<${Fragment} key=${keyOf(it)}>
          <tr class=${`clickable ${on ? "selected" : ""}`} ref=${on ? chosenRow : null} ...${rowProps(it)} onClick=${() => onChoose(it)}>
            <td>${on ? "▾" : "▸"} ${name(it)}</td>
            ${columns.map((c) => html`<td class="num" key=${c.head}>${c.cell(it)}</td>`)}
            <td class="num">${fmt(it.impact)}</td><td class="num">${share(it.impact)}</td>
          </tr>
          ${on && below?.(it)}
        <//>`;
      })}
        ${others.count > 0 && html`<tr class="muted"><td>+ ${others.count} more ${what}</td>${blank}<td class="num">${fmt(others.impact)}</td><td class="num">${share(others.impact)}</td></tr>`}
      </tbody>
      <tfoot><tr><th>Total · ${count} ${what}</th>${blank}<th class="num">${fmt(total)}</th><th class="num">100%</th></tr></tfoot>
    </table>`;
}

// What each process of the supply chain contributes by its own emissions (the direct contributions, wherever it is in the
// tree): /api/global/contributors with no group (everything is "other"). A click on a process opens the elementary flows
// its contribution is made of.
function ProcessContributions({ info, root, amount, method, onInfo }) {
  const [cut, setCut] = useState({ mode: "top", value: 20 });
  const [chosen, setChosen] = useState(null);  // the activity chosen
  useEffect(() => setChosen(null), [root.index, method]);
  const { data: found, error } = useRequest("/api/global/contributors",
    { root: root.index, amount, groups: {}, flow_groups: {}, order: [], rule: "direct", group: "other", method, cut: apiCut(cut) });
  // the flows of the process chosen: its contribution, reversed
  const inner = useRequest("/api/flows/of-process", chosen === null ? null : { root: root.index, amount, method, activity: chosen, cut: { mode: "top", value: 12 } });
  if (error) return html`<div class="error">${error}</div>`;
  if (!found) return html`<${Waiting} text="Computing…" />`;
  const flowsOf = (c) => (inner.error ? html`<tr class="sub"><td colspan="3" class="error">${inner.error}</td></tr>`
    : !inner.data ? html`<tr class="sub"><td colspan="3"><${Waiting} text="Computing…" /></td></tr>`
    : html`${inner.data.flows.map((f) => html`
        <tr class="sub" key=${f.flow}><td>${f.name} <span class="muted">${f.compartment}</span></td>
          <td class="num">${fmt(f.impact)}</td><td class="num muted" title="Of this process's contribution">${pct(inner.data.total ? f.impact / inner.data.total : 0)}</td></tr>`)}
        ${inner.data.others.count > 0 && html`<tr class="sub muted"><td>+ ${inner.data.others.count} more flows</td><td class="num">${fmt(inner.data.others.impact)}</td><td></td></tr>`}`);
  return html`
    <div class="panel">
      <${ContributionList} items=${found.contributors} others=${found.others} total=${found.total} count=${found.count} what="processes"
        unitLabel=${info.methods[method].unit} cut=${cut} setCut=${setCut} exportName="process-contributions" tableClass="direct-contributions"
        keyOf=${(c) => c.index} label=${(c) => `${c.name} (${c.location})`} chosen=${chosen}
        onChoose=${(c) => setChosen(chosen === c.index ? null : c.index)}
        head=${html`Process <span class="muted">· direct contributions</span>`}
        name=${(c) => html`${c.name} <span class="loc-chip">${c.location}</span>`}
        rowProps=${(c) => ({ ...dragProcess(c.index), title: `${c.reference_product ?? ""} | ${c.name} | ${c.location}: click for the flows it is made of, right-click for its details`,
          onContextMenu: (e) => { e.preventDefault(); onInfo?.(c.index, e.clientX, e.clientY); } })}
        below=${flowsOf} csv=${{ header: ["process", "location"], cells: (c) => [c.name, c.location] }} />
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
  { label: "Fertilisers", rule: { name: "fertilisers", predicate: { any: [
    { field: "cpc", op: "regex", value: "^346[1-5]" },
    { field: "reference_product", op: "startswith", value: "packaging, for fertiliser" },
  ] } } },
  // The machines, and the field operations that use them (tillage, sowing, harvesting, …)
  { label: "Agricultural machinery", rule: { name: "agricultural machinery", predicate: { field: "reference_product", op: "regex",
    value: "^(tractor, 4-wheel, agricultural|harvester|agricultural machinery|agricultural trailer|liquid manure tank trailer"
      + "|tillage|sowing|planting|potato planting|harvesting|combine harvesting|fertilising, by|application of plant protection product"
      + "|mowing|mulching|hoeing|haying|swath|baling|bale loading|chopping|fodder loading|potato haulm cutting|furrow"
      + "|field leveling|limestone and gypsum application|liquid manure spreading|solid manure loading and spreading"
      + "|sugarcane (loading|transfer)|transport, tractor and trailer|diesel, burned in agricultural machinery"
      + "|petrol, unleaded, burned in machinery)" } } },
];

const RULES_TIP = "Rules bundle rows into groups, in list order: the first rule that matches takes a process. Shared by all tabs. Numbers never change.";
const RULE_SET_TIP = "A named list of rules. The one in use is shared by all tabs.";

// A rule's query as the builder's conditions (null: it cannot be shown there, as a group made from selected rows)
function queryOf(predicate) {
  const condition = (c) => ({ id: ++conditionIds, field: c.field, op: c.op, value: Array.isArray(c.value) ? c.value.join(", ") : String(c.value ?? "") });
  if (predicate?.field) return { combinator: "all", conditions: [condition(predicate)] };
  const key = predicate?.all ? "all" : predicate?.any ? "any" : null;
  return key && predicate[key].length && predicate[key].every((c) => c.field) ? { combinator: key, conditions: predicate[key].map(condition) } : null;
}

// An eye, crossed out while the rule is switched off
function EyeIcon({ off }) {
  return html`
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" aria-hidden="true">
      <path d="M1.5 8s2.3-4.5 6.5-4.5S14.5 8 14.5 8 12.2 12.5 8 12.5 1.5 8 1.5 8z" /><circle cx="8" cy="8" r="1.9" />
      ${off && html`<path d="M2.5 13.5l11-11" />`}
    </svg>`;
}

// A rule in words, whole (the list shows one line of it, this is its tooltip)
const ruleText = (r) => `${r.target === "flows" ? "flows: " : ""}${r.by ? `group by ${r.by === "name" && r.target === "flows" ? "substance" : r.by}` : `${r.name} ← ${describePredicate(r.predicate)}`}${r.owner ? `, emitted by processes ${describePredicate(r.owner)}` : ""}`
  + ` — ${r.scope === "*" ? "everywhere in the tree" : `under ${r.scope} (this tab only)`}${r.disabled ? ", disabled" : ""}`;
// A section of a panel that folds: its header is the button, ``force`` keeps it open (a rule being edited), ``actions``
// stay at the right of the header. Open or folded is kept per ``id``.
function Fold({ id, title, open = true, force = false, actions, innerRef, children }) {
  const [on, setOn] = useState(() => { const v = loadPreference(`fold.${id}`, ""); return v === "" ? open : v === "1"; });
  const shown = on || force;
  const toggle = () => { setOn(!shown); savePreference(`fold.${id}`, !shown ? "1" : "0"); };
  return html`
    <section class="fold" ref=${innerRef}>
      <h3 class="fold-head">
        <button class="fold-toggle" onClick=${toggle} aria-expanded=${shown} title=${shown ? "Fold" : "Unfold"}><span class="caret">${shown ? "▾" : "▸"}</span> ${title}</button>
        ${actions}
      </h3>
      ${shown && children}
    </section>`;
}

// ``shared``: { set } when the rules are those of a rule set of the workspace
function GroupPanel({ info, rules, setRules, selectedRow, view, setView, shared, extras }) {
  const { editing } = useContext(EditorContext);
  const [editIndex, setEditIndex] = useState(null);  // the rule being changed in the form below, else null
  const [queryLocked, setQueryLocked] = useState(false);  // the rule edited has a query the builder cannot show
  const form = useRef(null);
  const [kind, setKind] = useState("named");
  const [target, setTarget] = useState("processes");  // what the new rule groups: processes or elementary flows
  const flows = target === "flows";
  const [name, setName] = useState("");
  const [by, setBy] = useState("location");
  const [query, setQuery] = useState(() => emptyQuery("name", "contains"));
  const [ownerQuery, setOwnerQuery] = useState(null);  // a flow rule on the emissions of some processes only: their query
  const [ownerLocked, setOwnerLocked] = useState(false);  // the rule edited has an owner query the builder cannot show: kept
  const [preview, setPreview] = useState(null);
  const predicate = useMemo(() => toPredicate(query), [query]);
  const ownerPredicate = useMemo(() => (ownerQuery ? toPredicate(ownerQuery) : null), [ownerQuery]);
  useEffect(() => {
    if (kind !== "named" || !predicate) { setPreview(null); return; }
    const timer = setTimeout(() => request("/api/search", { predicate, limit: 5, target: flows ? "flows" : undefined }).then(setPreview).catch(() => setPreview(null)), 250);
    return () => clearTimeout(timer);
  }, [predicate, kind, flows]);
  const chooseTarget = (next) => {
    setTarget(next);
    setQuery(emptyQuery("name", "contains"));
    setOwnerQuery(null);
    setBy(next === "flows" ? "compartment" : "location");
    setPreview(null);
  };
  // A rule is not in the set twice: a named rule that exists comes as a copy (a number after the name, so a colour of its
  // own), a group-by rule that exists is not added again
  const nameTaken = (n, except = null) => rules.some((r, j) => j !== except && (r.name === n || (r.by && r.by === n)));
  const uniqueName = (base, except = null) => { let n = base, k = 1; while (nameTaken(n, except)) n = `${base} ${++k}`; return n; };
  const add = (rule) => {
    if (rule.by && rules.some((r) => r.by === rule.by && (r.target || "processes") === (rule.target || "processes"))) return;
    setRules([...rules, { scope: "*", ...rule, ...(rule.by ? {} : { name: uniqueName(rule.name) }) }]);
  };
  function addCustom() {
    if (!predicate) return;
    add({ name: name.trim() || describePredicate(predicate), predicate, ...(flows ? { target: "flows" } : {}), ...(flows && ownerPredicate ? { owner: ownerPredicate } : {}) });
    setName("");
    setOwnerQuery(null);
  }
  // Change a rule: its form is the one of a new rule, filled in; the target, the kind and the scope stay as they were
  function startEdit(i) {
    const r = rules[i];
    setEditIndex(i);
    setTarget(r.target === "flows" ? "flows" : "processes");
    if (r.by) {
      setKind("by");
      setBy(r.by);
      setQueryLocked(false);
    } else {
      setKind("named");
      setName(r.name);
      const q = queryOf(r.predicate);
      setQueryLocked(!q);
      setQuery(q || emptyQuery("name", "contains"));
      const owner = r.owner ? queryOf(r.owner) : null;
      setOwnerLocked(!!r.owner && !owner);
      setOwnerQuery(owner);
    }
    setTimeout(() => form.current?.scrollIntoView({ block: "start", behavior: "smooth" }));
  }
  function stopEdit() {
    setEditIndex(null);
    setQueryLocked(false);
    setOwnerQuery(null);
    setOwnerLocked(false);
    setName("");
    setQuery(emptyQuery("name", "contains"));
  }
  function saveEdit() {
    const r = rules[editIndex];
    const { owner: before, ...rest } = r;
    const owner = r.target === "flows" && !ownerLocked ? (ownerPredicate ?? undefined) : before;
    const next = r.by ? { ...r, by } : { ...rest, name: uniqueName(name.trim() || r.name, editIndex), ...(queryLocked || !predicate ? {} : { predicate }), ...(owner ? { owner } : {}) };
    setRules(rules.map((x, j) => (j === editIndex ? next : x)));
    stopEdit();
  }
  // Esc leaves the change of a rule
  useEffect(() => {
    if (editIndex === null) return;
    const key = (e) => { if (e.key === "Escape" && !e.defaultPrevented && !document.querySelector(".modal-backdrop")) stopEdit(); };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [editIndex]);
  // A rule's colour: picked here or in the Settings pop-up (kept by the workspace), or the palette's
  const pickColor = (key, color) => extras?.pickColor?.(key, color);
  // Disabling keeps a rule (to switch it back on)
  const toggle = (i, key) => setRules(rules.map((r, j) => {
    if (j !== i) return r;
    const { [key]: on, ...rest } = r;
    return on ? rest : { ...rest, [key]: true };
  }));
  // Drag and drop reordering: the rule dragged and where it would land (before rule `at`)
  const [dragging, setDragging] = useState(null);
  const [dropAt, setDropAt] = useState(null);
  function onDragOver(e, i) {
    if (dragging === null) return;
    e.preventDefault();
    const box = e.currentTarget.getBoundingClientRect();
    setDropAt(e.clientY < box.top + box.height / 2 ? i : i + 1);
  }
  function onDrop(e) {
    e.preventDefault();
    if (dragging !== null && dropAt !== null && dropAt !== dragging && dropAt !== dragging + 1) {
      const next = rules.filter((_, j) => j !== dragging);
      next.splice(dropAt > dragging ? dropAt - 1 : dropAt, 0, rules[dragging]);
      setRules(next);
    }
    setDragging(null);
    setDropAt(null);
  }

  return html`
    <div class="panel">
      ${extras?.attribution}
      <${Fold} id="active" title="Active rules" actions=${html`<${InfoTip} text=${RULES_TIP} label="How rules work" />
        ${rules.length > 1 && html`<button class="link" onClick=${() => { setRules([]); stopEdit(); }}>remove all</button>`}`}>
      ${rules.length === 0 ? null : html`
        <ul class="rules">
          ${rules.map((r, i) => html`
            <li class=${[r.disabled && "disabled", dragging === i && "dragging",
                dropAt === i && "drop-before", dropAt === i + 1 && i === rules.length - 1 && "drop-after"].filter(Boolean).join(" ")}
              draggable=${rules.length > 1}
              onDragStart=${(e) => { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); setDragging(i); }}
              onDragOver=${(e) => onDragOver(e, i)} onDrop=${onDrop}
              onDragEnd=${() => { setDragging(null); setDropAt(null); }}
              onContextMenu=${(e) => { e.preventDefault(); startEdit(i); }}>
              ${rules.length > 1 && html`<span class="grip" title="Drag to reorder (rules apply in list order)" aria-hidden="true">⠿</span>`}
              ${!(r.target === "flows" && r.by) && colorModeOf(view) === "group" && html`
                <input type="color" class="swatch" value=${colorHex(colorOf(ruleKey(r)))}
                  onInput=${(e) => pickColor(ruleKey(r), e.target.value)}
                  onContextMenu=${(e) => { e.preventDefault(); e.stopPropagation(); pickColor(ruleKey(r), null); }}
                  title="Colour of this rule's groups in the tree and the charts: click to change, right-click to reset" />`}
              <span class="desc" title=${ruleText(r)}>
                <span class="line">${r.target === "flows" && "flows: "}${r.by ? html`<b>group by ${r.by === "name" && r.target === "flows" ? "substance" : r.by}</b>` : html`<b>${r.name}</b> <small>← ${describePredicate(r.predicate)}</small>`}</span>
                <span class="line"><small>${r.owner ? `of processes ${describePredicate(r.owner)} · ` : ""}${r.scope === "*" ? "everywhere in the tree" : `under ${r.scope} · this tab only`}${r.disabled ? " · disabled" : ""}</small></span>
              </span>
              <button class=${editIndex === i ? "primary" : ""} onClick=${() => (editIndex === i ? stopEdit() : startEdit(i))}
                aria-label="Edit rule" title="Edit this rule (or right-click it); Esc leaves it">✎</button>
              <button class="icon-btn" onClick=${() => toggle(i, "disabled")} aria-pressed=${!!r.disabled}
                aria-label=${r.disabled ? "Enable rule" : "Disable rule"}
                title=${r.disabled ? "Disabled: apply this rule again" : "Stop applying this rule, keeping it in the list"}><${EyeIcon} off=${!!r.disabled} /></button>
              <button onClick=${() => { setRules(rules.filter((_, j) => j !== i)); if (editIndex !== null) stopEdit(); }} aria-label="Remove rule">×</button>
            </li>`)}
        </ul>`}
      <//>
      ${extras?.ruleSet}

      <${Fold} id="quick" title="Quick groups" open=${false}>
        <div class="row">${PRESETS.map((p) => {
          const key = ruleKey(p.rule);
          const used = colorModeOf(view) === "group" && rules.some((r) => !r.disabled && r.target !== "flows" && ruleKey(r) === key);
          const fill = used ? colorOf(key) : null;
          return html`<button style=${used ? { background: fill, borderColor: fill, color: textOn(fill) } : null}
            title=${used ? "In the active rules, in this colour" : undefined} onClick=${() => add(p.rule)}>${p.label}</button>`;
        })}</div>
      <//>

      <${Fold} id="new" title=${editIndex !== null ? "Edit rule" : "New rule"} open=${false} force=${editIndex !== null} innerRef=${form}>
      <div class="row">
        <span class="segmented" role="group" aria-label="What the rule groups">
          <button class=${!flows ? "primary" : ""} aria-pressed=${!flows} disabled=${editIndex !== null} title="Rule on processes" onClick=${() => chooseTarget("processes")}>Processes</button>
          <button class=${flows ? "primary" : ""} aria-pressed=${flows} disabled=${editIndex !== null} title="Rule on elementary flows" onClick=${() => chooseTarget("flows")}>Elementary flows</button>
        </span>
      </div>
      ${kind === "named" ? html`
        <div class="row"><input placeholder="Group name" value=${name} onInput=${(e) => setName(e.target.value)} aria-label="Group name" /></div>
        ${queryLocked
          ? html`<p class="note">This group was made from selected rows or a nested query: only its name can be changed here.</p>`
          : html`<${QueryBuilder} query=${query} setQuery=${setQuery} fields=${(flows ? info.rule_flow_query_fields || info.flow_query_fields : info.rule_query_fields || info.query_fields)} flows=${flows} />`}
        ${flows && ownerLocked && html`<p class="note">Its owning process query cannot be shown here: it is kept as it is.</p>`}
        ${flows && !queryLocked && !ownerLocked && (ownerQuery
          ? html`
            <div class="owner-query" role="group" aria-label="Owning process">
              <div class="row"><span class="view-group-label" title="Only the emissions of the processes that match">Of processes</span>
                <button onClick=${() => setOwnerQuery(null)} aria-label="Any process" title="Any process">×</button></div>
              <${QueryBuilder} query=${ownerQuery} setQuery=${setOwnerQuery} fields=${info.query_fields} flows=${false} />
            </div>`
          : html`<div class="row"><button onClick=${() => setOwnerQuery(emptyQuery("name", "contains"))} title="Only the emissions of some processes">+ Owning process</button></div>`)}
        ${preview && !queryLocked && html`<p class="note" title=${preview.results.slice(0, 3).map((r) => `${r.name} (${r.location})`).join("; ") + (preview.total > 3 ? "…" : "")}>${usesExchange(predicate)
          ? `Matches ${preview.total} ${flows ? "flows" : "processes"}, where their exchange matches. In the Rules contribution (a tab's Contribution panel) it takes what goes through the exchanges it matches.`
          : `Matches ${preview.total} ${flows ? "flows" : "processes"}`}</p>`}
      ` : html`
        <div class="row"><select value=${by} onChange=${(e) => setBy(e.target.value)} aria-label="Group by field">
          ${(flows ? info.flow_query_fields : info.group_by_fields).map((f) => html`<option value=${f}>${fieldLabel(f, flows)}</option>`)}
        </select></div>`}
      <div class="row">
        ${editIndex !== null
          ? html`<button class="primary" onClick=${saveEdit}>Save rule</button><button onClick=${stopEdit}>Cancel</button>`
          : html`<button class="primary" onClick=${addCustom}>Add rule</button>`}
      </div>
      <//>
    </div>`;
}

// ---------------------------------------------------------------- global grouping panel
// Right-click menu of a process listed in a contribution panel: go to it in the tree, or its details
// A small right-click menu: ``open(e, items)``, items { label, run, title? }; ``element`` to render
function useContextMenu() {
  const [menu, setMenu] = useState(null);
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (e) => e.key === "Escape" && close();
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", onKey); };
  }, [menu]);
  const open = (e, items) => { e.preventDefault(); e.stopPropagation?.(); setMenu({ x: e.clientX, y: e.clientY, items }); };
  const element = menu && html`
    <div class="menu context" style=${{ left: `${Math.min(menu.x, window.innerWidth - 200)}px`, top: `${Math.min(menu.y, window.innerHeight - 90)}px` }}
         onMouseDown=${(e) => e.stopPropagation()}>
      ${menu.items.map((it) => html`<button title=${it.title || ""} onClick=${() => { setMenu(null); it.run(menu); }}>${it.label}</button>`)}
    </div>`;
  return { open, element };
}

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

// The key of a rule: its name, or the field of a group-by rule (its colour goes by it)
const ruleKey = (r) => r.by || r.name;
// The named rules as the analyses take them: groups of processes, groups of flows (renamed when the name is taken by a
// group of processes), the names of all of them in the order of the rules, and the rule of each group (``ruleOf``: the
// colour of a group is its rule's, whatever its name)
function namedGroups(rules) {
  // a rule on the exchange that brings a row in (avoided products, amounts…) takes the impacts that go through the
  // exchanges it matches
  const active = rules.filter((r) => r.predicate && !r.disabled);
  const groups = Object.fromEntries(active.filter((r) => r.target !== "flows").map((r) => [r.name, r.predicate]));
  const nameOf = (r) => (r.target === "flows" && r.name in groups ? `${r.name} (flows)` : r.name);
  const flowGroups = Object.fromEntries(active.filter((r) => r.target === "flows").map((r) => [nameOf(r), r.predicate]));
  // A flow rule can be on the emissions of some processes only: its predicate on the processes that emit
  const flowOwners = Object.fromEntries(active.filter((r) => r.target === "flows" && r.owner).map((r) => [nameOf(r), r.owner]));
  return { groups, flowGroups, flowOwners, order: active.map(nameOf), ruleOf: Object.fromEntries(active.map((r) => [nameOf(r), ruleKey(r)])) };
}

// The palette of the colours of the rules: a menu, with the first colours of the one chosen. A colour picked on a rule makes
// the palette "Custom" (choosing a palette again clears the colours picked); "Add palette…" makes one of the user's own.
function PaletteSwitch({ palette, setPalette, palettes, addPalette, customized }) {
  const all = { ...RULE_PALETTES, ...Object.fromEntries(Object.entries(palettes).map(([n, colors]) => [n, { label: n, hint: "Your palette", colors }])) };
  const current = all[palette] || RULE_PALETTES.auto;
  const [adding, setAdding] = useState(null);  // the palette being made: { name, colors }
  const choose = (value) => {
    if (value === "__add__") { setAdding({ name: "", colors: PALETTE.slice(0, 5) }); return; }
    if (value !== "__custom__") setPalette(value);
  };
  const strip = (colors) => (colors || [0, 60, 120, 180, 240, 300].map((h) => `hsl(${h} 55% 52%)`)).slice(0, 8);
  return html`
    <div class="view-group" role="group" aria-label="Palette of the rules">
      <span class="view-group-label">Colours</span>
      <select value=${customized ? "__custom__" : palette} onChange=${(e) => choose(e.target.value)} aria-label="Palette of the rules" title=${customized ? "Colours picked on rules" : current.hint}>
        ${customized && html`<option value="__custom__">Custom</option>`}
        ${Object.entries(all).map(([key, q]) => html`<option value=${key} title=${q.hint}>${q.label}</option>`)}
        <option value="__add__">Add palette…</option>
      </select>
      <span class="palette-strip" aria-hidden="true">${strip(current.colors).map((c) => html`<i style=${{ background: c }}></i>`)}</span>
    </div>
    ${adding && html`
      <div class="view-group palette-form" role="group" aria-label="New palette">
        <input placeholder="Name" value=${adding.name} onInput=${(e) => setAdding({ ...adding, name: e.target.value })} aria-label="Name of the palette" />
        ${adding.colors.map((c, i) => html`<input type="color" class="swatch" key=${i} value=${colorHex(c)} aria-label=${`Colour ${i + 1}`}
          onInput=${(e) => setAdding({ ...adding, colors: adding.colors.map((x, j) => (j === i ? e.target.value : x)) })} />`)}
        <button onClick=${() => setAdding({ ...adding, colors: [...adding.colors, PALETTE[adding.colors.length % PALETTE.length]] })} title="One more colour" aria-label="One more colour">+</button>
        <button class="primary" disabled=${!adding.name.trim()} onClick=${() => { addPalette(adding.name.trim(), adding.colors); setAdding(null); }}>Add</button>
        <button onClick=${() => setAdding(null)}>Cancel</button>
      </div>`}`;
}

// The Rules panel keeps these next to its rules (the attribution, the rule set, the colours picked), made where the lens is known
const ruleExtras = (lens) => ({
  pickColor: lens.pickColor,
  attribution: html`<${AttributionSwitch} rule=${lens.attribution} setRule=${lens.setAttribution} />`,
  ruleSet: html`<${Fold} id="ruleset" title=${`Rule set · ${lens.set}`} open=${false} actions=${html`<${InfoTip} text=${RULE_SET_TIP} label="What a rule set is" />`}>
    <${SetMenu} lens=${lens} />
  <//>`,
});
// A small supply chain, attributed to the rules both ways, side by side: direct (each process's own emissions go to its own
// rule) and first match (the first process of a rule on a path takes its whole supply chain)
function AttributionPicture() {
  const colors = { other: "#8a8a82", fert: "hsl(270 45% 58%)", elec: "hsl(178 50% 38%)", mach: "hsl(32 85% 50%)" };
  const labels = { other: "other", fert: "fertilisers", elec: "electricity", mach: "machinery" };
  const nodes = [
    { id: "wheat", name: "Wheat", own: 4, rule: "other", x: 170, y: 0 },
    { id: "fert", name: "Fertiliser", own: 2, rule: "fert", x: 50, y: 110, parent: "wheat" },
    { id: "tractor", name: "Tractor", own: 2, rule: "mach", x: 290, y: 110, parent: "wheat" },
    { id: "elec", name: "Electricity", own: 3, rule: "elec", x: 0, y: 220, parent: "fert" },
    { id: "gas", name: "Natural gas", own: 1, rule: "other", x: 130, y: 220, parent: "fert" },
  ];
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const chain = (n) => (n.parent ? [...chain(byId[n.parent]), n] : [n]);
  const first = (n) => chain(n).find((m) => m.rule !== "other")?.rule || "other";
  const W = 120, H = 46;
  const panel = (x0, title, hint, owner) => {
    const totals = {};
    nodes.forEach((n) => { totals[owner(n)] = (totals[owner(n)] || 0) + n.own; });
    const order = ["other", "fert", "elec", "mach"].filter((k) => totals[k]);
    const sum = nodes.reduce((t, n) => t + n.own, 0);
    let at = 0;
    return html`
      <g transform=${`translate(${x0} 0)`}>
        <text x="0" y="22" font-size="19" font-weight="700" style=${{ fill: "var(--text)" }}>${title}</text>
        <text x="0" y="44" font-size="13" style=${{ fill: "var(--muted)" }}>${hint}</text>
        <g transform="translate(10 66)">
          ${nodes.filter((n) => n.parent).map((n) => html`<line key=${`e${n.id}`} x1=${byId[n.parent].x + W / 2} y1=${byId[n.parent].y + H} x2=${n.x + W / 2} y2=${n.y}
            style=${{ stroke: "var(--guide, #bbb)", strokeWidth: 2 }} />`)}
          ${nodes.map((n) => {
            const rule = owner(n), inherited = rule !== n.rule;
            return html`<g key=${n.id}>
              <rect x=${n.x} y=${n.y} width=${W} height=${H} rx="7" fill=${colors[rule]} fill-opacity=${inherited ? 0.55 : 1}
                stroke=${colors[rule]} stroke-width="2" stroke-dasharray=${inherited ? "5 3" : undefined} />
              <text x=${n.x + W / 2} y=${n.y + 19} text-anchor="middle" font-size="14" font-weight="700" fill="#fff">${n.name}</text>
              <text x=${n.x + W / 2} y=${n.y + 37} text-anchor="middle" font-size="12" fill="#fff">own ${n.own}</text>
            </g>`;
          })}
        </g>
        <g transform="translate(10 380)">
          <text x="0" y="0" font-size="13" font-weight="600" style=${{ fill: "var(--text)" }}>Score ${sum}, by rule</text>
          ${order.map((k) => { const w = (400 * totals[k]) / sum, x = at; at += w; return html`<g key=${k}>
            <rect x=${x} y="12" width=${w} height="26" fill=${colors[k]} />
            <text x=${x + w / 2} y="30" text-anchor="middle" font-size="13" font-weight="700" fill="#fff">${totals[k]}</text>
          </g>`; })}
          ${(() => { let lx = 0; return order.map((k) => { const x = lx; lx += 110; return html`<g key=${`l${k}`}>
            <rect x=${x} y="48" width="11" height="11" fill=${colors[k]} /><text x=${x + 16} y="58" font-size="12" style=${{ fill: "var(--text)" }}>${labels[k]}</text></g>`; }); })()}
        </g>
      </g>`;
  };
  return html`
    <svg viewBox="0 0 960 480" role="img" aria-label="Direct and first match attribution on a small supply chain" class="attribution-picture">
      ${panel(0, "Direct", "Each process's own emissions go to its own rule.", (n) => n.rule)}
      <line x1="480" y1="10" x2="480" y2="470" style=${{ stroke: "var(--border)", strokeWidth: 2 }} />
      ${panel(500, "First match", "The first process of a rule on a path takes its whole supply chain.", first)}
    </svg>`;
}

// The (i) that opens the picture, big, in a dialog
function AttributionHelp() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const esc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open]);
  return html`
    <button class="help-btn action" aria-label="How attribution works" title="Click for a picture" onClick=${() => setOpen(true)}>?</button>
    ${open && html`
      <div class="modal-backdrop" onMouseDown=${(e) => e.target === e.currentTarget && setOpen(false)}>
        <div class="load-dialog attribution-dialog" role="dialog" aria-label="Attribution of impacts to the rules">
          <div class="info-head"><div class="info-title">Attribution</div><button onClick=${() => setOpen(false)} aria-label="Close">×</button></div>
          <div class="load-body"><${AttributionPicture} /></div>
        </div>
      </div>`}`;
}

// How the impacts go to the rules, one choice for the Rules contribution (a tab's Contribution panel) and for Compare (``rule``: "first_match" or "direct")
function AttributionSwitch({ rule, setRule }) {
  return html`
    <div class="view-group" role="group" aria-label="Attribution">
      <span class="view-group-label">Attribution rule</span>
      <span class="segmented">
        <button class=${rule === "first_match" ? "primary" : ""} onClick=${() => setRule("first_match")}
          title="The first process of a rule takes its supply chain">First match</button>
        <button class=${rule === "direct" ? "primary" : ""} onClick=${() => setRule("direct")}
          title="Own emissions only">Direct</button>
      </span>
      <${AttributionHelp} />
    </div>`;
}

function GlobalPanel({ info, root, amount, method, rules, view, rule, setRule, onLocate, onInfo }) {
  // Named rules on activities are activity groups, named rules on elementary flows flow groups; they go by the order of the rules
  const { groups, flowGroups, flowOwners, order, ruleOf } = useMemo(() => namedGroups(rules), [rules]);
  const k = method;
  const [group, setGroup] = useState(null);
  const [exportError, setError] = useState(null);
  const contributorsBox = useRef(null);
  const chartRef = useRef(null);
  const asked = root && { root: root.index, amount, groups, flow_groups: flowGroups, flow_owners: flowOwners, order, rule, method: k };
  const { data: result, error: resultError } = useRequest("/api/global", asked);
  useEffect(() => { if (group && result && !(group in result)) setGroup(null); }, [result]);
  // Processes making up the chosen group
  const { data: contributors, error: contributorsError } = useRequest("/api/global/contributors", asked && group && { ...asked, group, cut: { mode: "top", value: 50 } });
  const error = resultError || contributorsError || exportError;
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
  const methodName = methodTitle(info.methods[k]);
  const fileName = (what) => `group-contributions-${what}-${(root?.name || "").replace(/[^A-Za-z0-9]+/g, "-").slice(0, 40)}.csv`;
  function exportGroups() {
    downloadCsv(fileName("groups"), ["group", "kind", methodName, "share of total"],
      entries.map(([n, v]) => [n, n in flowGroups ? "flow group" : n === "other" ? "other" : "process group", v, total ? v / total : 0]),
      [`${rule === "first_match" ? "First match" : "Direct"} attribution`]);
  }
  function exportContributors() {
    // Every process of the group, not only those listed
    request("/api/global/contributors", { ...asked, group, cut: ALL_ROWS })
      .then((d) => downloadCsv(fileName(`group-${group.replace(/[^A-Za-z0-9]+/g, "-")}`),
        ["process", "reference product", "location", "database", methodName, "share of group", "share of total"],
        d.contributors.map((c) => [c.name, c.reference_product, c.location, c.database, c.impact, d.total ? c.impact / d.total : 0,
          total ? c.impact / total : 0])))
      .catch((e) => setError(e.message));
  }
  // A group has the colour of its rule in the Rules panel (and of its rows in the tree)
  const colorOfGroup = (n) => n === "other" ? cssVar("--muted") : colorModeOf(view) === "group" ? colorOf(ruleOf[n] ?? n) : undefined;
  const option = useMemo(() => {
    const o = baseOption();
    o.yAxis = { ...o.yAxis, type: "category", data: entries.map(([n]) => n), inverse: true, triggerEvent: true };
    o.xAxis = { ...o.xAxis, type: "value", name: unit };
    o.tooltip.valueFormatter = (v) => `${fmt(v)} ${unit} (${pct(v / total)})`;
    o.series = [{ type: "bar", colorBy: "data", cursor: "pointer",
      data: entries.map(([n, v]) => ({
        value: v,
        itemStyle: { color: colorOfGroup(n), opacity: group && n !== group ? 0.35 : 1 },
      })),
      label: { show: true, position: "right", formatter: (d) => pct(d.value / total), color: cssVar("--muted") } }];
    return o;
  }, [result, k, group, view.groupColors, view.colorBy, paletteStamp]);

  return html`
    <div class="panel">
      <div class="view-group" role="group" aria-label="Attribution">
        <span class="view-group-label">Attribution rule</span>
        <b>${rule === "first_match" ? "First match" : "Direct"}</b>
        <${AttributionHelp} />
      </div>
      ${error && html`<div class="error">${error}</div>`}
      ${result && html`
        <${Chart} exportRef=${chartRef} option=${option} height=${Math.max(200, entries.length * 34 + 60)}
          onClick=${(e) => choose(e.componentType === "yAxis" ? e.value : e.name)} />
        <table class="results">
          <thead><tr><th>Group <span class="muted">· click to see its processes</span>
            <${ExportMenu} chart=${chartRef} csv=${exportGroups} name="rules-contributions" /></th>
            <th class="num">${unit}</th><th class="num">Share</th></tr></thead>
          <tbody>${entries.map(([n, v]) => html`
            <tr class="clickable ${n === group ? "selected" : ""}" onClick=${() => choose(n)}>
              <td>${n === group ? "▾" : "▸"} ${colorModeOf(view) === "group" && html`<span class="swatch" style=${{ background: colorOfGroup(n) }}></span>`}${n}${n in flowGroups && html` <span class="loc-chip">flows</span>`}</td>
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
            ${contributors && html`<${ExportMenu} csv=${exportContributors} />`}
            <button onClick=${() => setGroup(null)} aria-label="Close">×</button>
          </div>
          ${contributors && html`
            <${InfoTip} label="About the contributors" text=${`${contributors.flow_group
              ? `Processes emitting these flows outside the process groups${rule === "first_match" ? " and their supply chains" : ""} (their own emissions).`
              : contributors.cumulative
              ? "Each process with its supply chain (the first process of the group on each supply path)."
              : `Each process's own emissions${group === "other" && Object.keys(flowGroups).length ? ", without the flows of the flow groups" : ""}.`} Click for its pop-up; right-click or double-click to go to it in the tree.`} />
            <div class="contributors-list">
              <table>
                <thead><tr><th>Process</th><th class="num">${unit}</th><th class="pct">of group</th><th class="pct">of total</th></tr></thead>
                <tbody>
                  ${contributors.contributors.map((c) => html`
                    <tr key=${c.index} ...${dragProcess(c.index)} title=${`${c.reference_product} | ${c.name} | ${c.location}`}
                        onClick=${(e) => e.detail < 2 && onInfo(c.index, e.clientX, e.clientY)}
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

// ---------------------------------------------------------------- coverage of the flows by a method
// What a unit of elementary flows measures: the headings of the lists by unit
const QUANTITIES = {
  kilogram: "Mass", "kilo becquerel": "Radioactivity", "square meter": "Area", "square meter-year": "Land occupation",
  "cubic meter": "Volume", "standard cubic meter": "Volume", "cubic meter-year": "Volume × time", litre: "Volume",
  megajoule: "Energy", "kilowatt hour": "Energy", hectare: "Area", meter: "Length", "meter-year": "Length × time",
  "kilogram day": "Mass × time", hour: "Time", unit: "Items", kilometer: "Length", "ton kilometer": "Transport",
  "metric ton*km": "Transport", "person kilometer": "Transport", "vehicle kilometer": "Transport",
};
// A unit with what it measures, for the Information panel ("kg · mass", "m2·a · land occupation"): by its Brightway name
// (kilogram) or its short form (kg)
function unitWithQuantity(u) {
  if (!u) return "–";
  const full = QUANTITIES[u.toLowerCase()] ? u.toLowerCase() : Object.keys(UNITS).find((k) => UNITS[k] === u);
  const q = full && QUANTITIES[full];
  return q ? `${unit(u)} · ${q.toLowerCase()}` : unit(u);
}
const quantityLabel = (u) => {
  if (!u) return "No unit";
  const q = QUANTITIES[u.toLowerCase()];
  return q ? `${q} (${unit(u)})` : unit(u);
};

// A value that follows ``value`` once it has stopped changing for ``ms`` (what is typed in a search)
function useSettled(value, ms = 250) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value]);
  return settled;
}

// A method to read the factors of another against (e.g. the one a new method replaces), among ``choices`` (the methods
// ticked in the Methods panel): kept by name, since positions move when a model is rebuilt, and shared by the
// Uncharacterised panels
function useReferenceMethod(info, choices) {
  const [name, setName] = useState(() => { try { return JSON.parse(loadPreference("coverageReference", "null")); } catch { return null; } });
  const k = name ? methodNamed(info, name) : -1;
  const choose = (i) => {
    const n = i >= 0 ? info.methods[i].name : null;
    setName(n);
    savePreference("coverageReference", JSON.stringify(n));
  };
  return [k >= 0 && choices.includes(k) ? k : null, choose];
}

// The methods ticked in the Methods panel (but the active one), by family
function MethodSelect({ info, value, onChange, choices, exclude, label = "Compare with" }) {
  const families = useMemo(() => {
    const map = new Map();
    [...choices].sort((a, b) => a - b).filter((i) => i !== exclude)
      .forEach((i) => map.set(familyKey(info.methods[i]), [...(map.get(familyKey(info.methods[i])) || []), i]));
    return [...map];
  }, [info, choices, exclude]);
  const none = families.length === 0;
  return html`
    <label class="method-select" title=${none ? "Tick methods in the Methods panel to compare with them"
      : "Its factors are shown next to the flows: a flow it characterises and the active method does not is likely a gap. The methods ticked in the Methods panel."}>
      <span class="view-group-label">${label}</span>
      <select value=${value ?? ""} disabled=${none} onChange=${(e) => onChange(e.target.value === "" ? -1 : Number(e.target.value))} aria-label=${label}>
        <option value="">${none ? "no other method ticked" : "no other method"}</option>
        ${families.map(([family, indices]) => html`
          <optgroup key=${family} label=${family}>
            ${indices.map((i) => html`<option key=${i} value=${i}>${methodPath(info.methods[i]).rest.slice(1).join(" | ") || family}</option>`)}
          </optgroup>`)}
      </select>
    </label>`;
}

// A search of the flows by name and compartment, for the lists of the coverage
function FlowSearch({ value, onChange }) {
  return html`
    <input type="search" class="flow-search" placeholder="Search the flows" value=${value} onInput=${(e) => onChange(e.target.value)}
      aria-label="Search the flows by name and compartment" title="Only the flows whose name or compartment has all these words" />`;
}

// The units of a list that are folded: kept between processes; while searching, every unit is open but those folded
// during this search
function useFoldedUnits(query) {
  const [folded, setFolded] = useState(() => { try { return new Set(JSON.parse(loadPreference("foldedUnits", "[]"))); } catch { return new Set(); } });
  const [foldedInSearch, setFoldedInSearch] = useState(new Set());
  useEffect(() => setFoldedInSearch(new Set()), [query]);
  const isOpen = (u) => !(query ? foldedInSearch : folded).has(u);
  const toggle = (u) => {
    if (query) { setFoldedInSearch((s) => { const next = new Set(s); next.has(u) ? next.delete(u) : next.add(u); return next; }); return; }
    const next = new Set(folded);
    next.has(u) ? next.delete(u) : next.add(u);
    setFolded(next);
    savePreference("foldedUnits", JSON.stringify([...next]));
  };
  return [isOpen, toggle];
}

// The flows of a list as a table like the tree's: one foldable row per unit (amounts in different units do not add up),
// the flows under it, with the factor of the reference method
function CoverageTable({ groups, reference, info, amount = false, onFlowInfo, emittersHead, isOpen, toggle }) {
  const refUnit = reference != null ? info.methods[reference].unit : "";
  const columns = 3 + (amount ? 1 : 0) + (reference != null ? 1 : 0);
  return html`
    <div class="tree coverage-tree">
      <table>
        <colgroup><col />${amount && html`<col style="width: 100px" />`}<col style="width: 76px" /><col style="width: 66px" />${reference != null && html`<col style="width: 84px" />`}</colgroup>
        <thead><tr>
          <th>Flow</th>
          ${amount && html`<th class="num" title="Amount in the inventory of the process, its supply chain included">Amount</th>`}
          <th class="num" title=${emittersHead}>Processes</th>
          <th class="num" title="Methods of the model with a factor on it">Methods</th>
          ${reference != null && html`<th class="num" title=${`Factor in ${methodLabel(info.methods[reference])}, ${refUnit} per unit of the flow`}>Reference</th>`}
        </tr></thead>
        ${groups.map((g) => {
          const open = isOpen(g.unit);
          return html`
            <tbody key=${g.unit}>
              <tr class="unit-row" onClick=${() => toggle(g.unit)}>
                <td style="--depth: 0"><div class="name">
                  <button class="toggle" aria-expanded=${open} aria-label=${`${open ? "Fold" : "Unfold"} ${quantityLabel(g.unit)}`}
                    onClick=${(e) => { e.stopPropagation(); toggle(g.unit); }}>${open ? "▾" : "▸"}</button>
                  <span class="label">${quantityLabel(g.unit)}</span>
                  <span class="loc">${g.count} ${g.count === 1 ? "flow" : "flows"}</span>
                </div></td>
                ${amount && html`<td class="num" title="Their total in the inventory">${g.total != null ? `${fmtAmount(g.total)} ${unit(g.unit)}` : ""}</td>`}
                <td></td><td></td>
                ${reference != null && html`<td class=${`num ${g.gaps ? "gap-count" : "muted"}`} title="Flows of this unit with a factor in the reference: likely gaps of the active method">${g.gaps ? `${g.gaps} ${g.gaps === 1 ? "gap" : "gaps"}` : "–"}</td>`}
              </tr>
              ${open && g.items.map((it) => html`
                <tr key=${it.flow} class=${reference != null && it.reference_cf ? "gap" : ""}
                    title=${onFlowInfo ? "Right-click for its details: its factors in every method, its metadata" : ""}
                    onContextMenu=${onFlowInfo && ((e) => onFlowInfo({ flow: it.flow, flows: [it.flow], labels: [it.compartment] }, e))}>
                  <td style="--depth: 1"><div class="name" style="padding-left: 16px">
                    <span class="toggle"></span>
                    <span class="label" title=${`${it.name} · ${it.compartment}`}>${it.name} <span class="loc">${it.compartment}</span></span>
                  </div></td>
                  ${amount && html`<td class="num" title=${`${it.amount} ${it.unit}`}>${fmtAmount(it.amount)} ${unit(it.unit)}</td>`}
                  <td class="num">${it.emitters}</td>
                  <td class="num">${it.methods}</td>
                  ${reference != null && html`<td class="num" title=${it.reference_cf ? `${it.reference_cf} ${refUnit}/${unit(it.unit)}` : "No factor in the reference"}>${it.reference_cf ? fmt(it.reference_cf) : html`<span class="muted">–</span>`}</td>`}
                </tr>`)}
              ${open && g.count > g.items.length && html`<tr class="more"><td colspan=${columns}>+ ${g.count - g.items.length} more: search to find them</td></tr>`}
            </tbody>`;
        })}
      </table>
    </div>`;
}

// The right pane's *Uncharacterised* tab: the flows of the inventory that the active method has no factor for
function UncharacterisedPanel({ info, root, amount, method, chartMethods, onFlowInfo }) {
  const [reference, setReference] = useReferenceMethod(info, chartMethods);
  const ref = reference === method ? null : reference;
  const [text, setText] = useState("");
  const query = useSettled(text.trim());
  const [isOpen, toggle] = useFoldedUnits(query);
  const { data: answer, error } = useRequest("/api/flows/uncharacterised",
    { root: root.index, amount, method, ...(ref != null ? { reference: ref } : {}), ...(query ? { query } : {}) });
  // The last answer stays while the next one comes: not one for another method or reference, read as this one's
  const data = answer && answer.method === method && answer.reference === ref ? answer : null;
  const gaps = data?.gaps || 0;
  const exportCsv = () => downloadCsv("uncharacterised-flows.csv",
    ["name", "compartment", "unit", "amount", "processes", "methods", ...(ref != null ? [`factor in ${methodLabel(info.methods[ref])}`] : [])],
    data.units.flatMap((g) => g.items.map((it) => [it.name, it.compartment, it.unit, it.amount, it.emitters, it.methods,
      ...(ref != null ? [it.reference_cf] : [])])),
    [`Flows of the inventory of ${root.name ?? `process ${root.index}`} with no factor in ${methodLabel(info.methods[method])}${data.query ? `, matching "${data.query}"` : ""}`]);
  return html`
    <div class="panel coverage">
      <div class="row">
        <${MethodSelect} info=${info} value=${ref} exclude=${method} choices=${chartMethods} onChange=${setReference} />
        <${FlowSearch} value=${text} onChange=${setText} />
        <span style="flex:1"></span>
        ${data?.matched > 0 && html`<button onClick=${exportCsv} title="The flows listed as a CSV file">CSV</button>`}
      </div>
      ${error && html`<div class="error">${error}</div>`}
      ${!data && !error && html`<${Waiting} text="Reading the inventory…" />`}
      ${data && html`
        <p class="coverage-summary">
          <b>${data.count}</b> of the ${data.inventory} flows of the inventory have no factor in <i>${methodLabel(info.methods[method])}</i>
          ${ref != null && data.count > 0 && html`; <b class=${gaps ? "gap-count" : ""}>${gaps}</b> of them have one in the reference`}
          ${data.query && html`; <b>${data.matched}</b> match the search`}
          <${InfoTip} label="About the uncharacterised flows" text="The elementary flows of this process's whole inventory (its supply chain included) that the active method gives no factor to: the score leaves them out. Amounts in different units do not add up, so there is one foldable list per unit, the largest amount first. Processes: how many processes of the supply chain emit it; methods: how many methods of the model characterise it. Compare with: one of the methods ticked in the Methods panel; a flow it characterises (highlighted, listed first) is likely a gap of the active method. Right-click a flow for its details." />
        </p>
        ${data.count === 0 ? html`<div class="empty">Every flow of the inventory has a factor in this method.</div>`
          : data.matched === 0 ? html`<div class="empty">No uncharacterised flow matches the search.</div>`
          : html`<${CoverageTable} groups=${data.units} reference=${ref} info=${info} amount=${true} onFlowInfo=${onFlowInfo}
              emittersHead="Processes of the supply chain emitting it" isOpen=${isOpen} toggle=${toggle} />`}`}
    </div>`;
}

// ---------------------------------------------------------------- elementary flow contribution
// The whole inventory of the process, characterised: which substances make the
// score. Clicking a flow lists the processes emitting it.
function FlowContribution({ info, root, amount, method, onLocate, onInfo, onFlowInfo, rules, setRules }) {
  // The rows: by substance, by compartment, by subcompartment, alone or together; none: one row per flow
  const [grouped, setGrouped] = useState({ name: true, compartment: false, subcompartment: false });
  const keys = ["name", "compartment", "subcompartment"].filter((k) => grouped[k]);
  const by = keys.join("+") || "flow";
  const withName = keys.length === 0 || keys.includes("name");
  const things = !keys.length ? "flows" : withName ? "substances" : "groups";
  const [cut, setCut] = useState({ mode: "top", value: 25 });
  const [flow, setFlow] = useState(null);
  const contributorsBox = useRef(null);
  const { data: result, error } = useRequest("/api/flows", { root: root.index, amount, method, by: "flow", ...(keys.length ? { keys } : {}), cut: apiCut(cut) });
  useEffect(() => setFlow(null), [root, by]);
  const label = (it) => (withName && (!keys.length || keys.some((k) => k !== "name")) && it.compartment ? `${it.name} (${it.compartment})` : it.name);
  // Processes emitting the chosen flow (all its compartments, by substance)
  const { data: contributors, error: contributorsError } = useRequest("/api/flows/contributors",
    flow && { root: root.index, amount, method, flows: flow.flows, cut: { mode: "top", value: 50 } });
  useEffect(() => { contributorsBox.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [flow, !!contributors]);

  const goTo = (c) => onLocate(c.index);
  // Right-click a substance or flow: its pop-up (factors, metadata); a process emitting
  // it: also its exchange of that flow (comment, uncertainty)
  const flowTarget = (it) => ({ flow: it.flows[0], flows: it.flows, labels: it.compartments || [it.compartment] });
  const menu = useProcessMenu(goTo, onInfo, (c, at) =>
    onFlowInfo({ flow: c.flow, activities: [c.index], exchange: true }, { preventDefault() {}, clientX: at.x, clientY: at.y }));
  const rowMenu = useContextMenu();
  const methodUnit = info.methods[method].unit;
  const total = result?.total || 0;
  const ofTotal = (v) => pct(total ? v / total : 0);
  const ofFlow = (v) => pct(contributors?.total ? v / contributors.total : 0);
  const keyOf = (it) => it.flows.join(",");
  const choose = (it) => setFlow(flow && keyOf(flow) === keyOf(it) ? null : it);

  return html`
    <div class="panel">
      ${error && html`<div class="error">${error}</div>`}
      ${result && html`
        <${ContributionList} items=${result.items} others=${result.others} total=${total} count=${result.count} what=${things}
          unitLabel=${methodUnit} cut=${cut} setCut=${setCut} exportName="flow-contributions" tableClass="flow-results"
          controls=${html`<div class="view-group" role="group" aria-label="Group by">
            <span class="view-group-label">Group by</span>
            ${[["name", "Substance", "Flows of a substance together"], ["compartment", "Compartment", "By compartment"], ["subcompartment", "Subcompartment", "By subcompartment"]].map(([k, label, hint]) => html`
              <button class=${grouped[k] ? "primary" : ""} aria-pressed=${grouped[k]} title=${hint} onClick=${() => setGrouped({ ...grouped, [k]: !grouped[k] })}>${label}</button>`)}
          </div>`}
          keyOf=${keyOf} label=${label} chosen=${flow && keyOf(flow)} onChoose=${choose}
          head=${html`${!keys.length ? "Flow" : withName ? "Substance" : keys.join(", ")} <span class="muted">· click for the processes emitting it</span>`}
          name=${(it) => html`${it.name} <span class="muted">${it.compartment}</span>`}
          columns=${[{ head: "amount", cell: (it) => (it.unit ? `${fmtAmount(it.amount)} ${unit(it.unit)}` : ""), csv: (it) => it.amount }]}
          rowProps=${(it) => ({ title: "Click for the processes emitting it, right-click: the flow, or who emits it in this supply chain",
            onContextMenu: (e) => rowMenu.open(e, [
              { label: "Flow…", title: "The flow: its factors, who emits it, its intensity (Space)", run: (m) => onFlowInfo(flowTarget(it), { preventDefault() {}, clientX: m.x, clientY: m.y }) },
              { label: "Emitted by…", title: "The processes with an exchange of it, in the supply chain of this process (i)",
                run: (m) => onFlowInfo({ ...flowTarget(it), tab: "emitters" }, { preventDefault() {}, clientX: m.x, clientY: m.y }) },
            ]) })}
          csv=${{ header: ["name", "compartment", "unit"], cells: (it) => [it.name, it.compartment, it.unit] }} />`}
      ${flow && html`
        <div class="contributors" ref=${contributorsBox}>
          <div class="details-head">
            <span class="details-title"><b>${flow.name}</b> <span class="muted">${flow.compartment}</span>${contributors
              ? `: ${contributors.count} ${contributors.count === 1 ? "process" : "processes"} · ${fmt(contributors.total)} ${methodUnit} (${ofTotal(contributors.total)})`
              : " …"}</span>
            ${(() => {
              // A substance: all its compartments, by name; a flow: that flow only
              const rule = by === "name"
                ? { name: flow.name, predicate: { field: "name", op: "eq", value: flow.name }, target: "flows", scope: "*" }
                : { name: `${flow.name} (${flow.compartment})`, predicate: { flows: flow.flows }, target: "flows", scope: "*" };
              const exists = rules.some((r) => r.name === rule.name && r.target === "flows");
              return html`<button disabled=${exists} onClick=${() => setRules([...rules, rule])}
                title="A flow group: in the Rules contribution (a tab's Contribution panel) it takes these flows from what the process groups leave; in the tree it bundles them under direct emissions">
                ${exists ? "In groups" : "Add as a group"}</button>`;
            })()}
            <button onClick=${() => setFlow(null)} aria-label="Close">×</button>
          </div>
          ${contributorsError && html`<div class="error">${contributorsError}</div>`}
          ${contributors && html`
            <${InfoTip} text="Processes of the supply chain emitting it, by their own emissions. Right-click one: go to it in the tree, its details, or its exchange of this flow." label="About the emitters" />
            <div class="contributors-list">
              <table>
                <thead><tr><th>Process</th><th class="num">${unit(flow.unit)}</th><th class="num">${methodUnit}</th><th class="pct">of flow</th><th class="pct">of total</th></tr></thead>
                <tbody>
                  ${contributors.contributors.map((c) => html`
                    <tr key=${c.index} ...${dragProcess(c.index)} title=${`${c.reference_product} | ${c.name} | ${c.location}`}
                        onContextMenu=${(e) => menu.open(e, c)} onDblClick=${() => goTo(c)}>
                      <td>${c.name}<span class="loc">${c.location}</span></td>
                      <td class="num">${fmtAmount(c.amount)}</td><td class="num">${fmt(c.impact)}</td>
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
      ${rowMenu.element}
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

// Over the Rules panel while a flow or model tab is in focus: its rules are those of a process tab, the one last in
// focus (``title``: its title, null for a tab with no process yet), or, with no process tab open (``title``
// undefined), what it is for
function PanelsFollow({ title }) {
  if (title === undefined) return html`
    <div class="panels-follow-empty" role="note">
      <p>This panel follows a process tab: the rules of its tree. A flow or model tab has none.</p>
      <p>Open a process in a tab (the search of the top bar, or <em>Open in new tab</em> on a process) to see them here.</p>
    </div>`;
  const name = title ? `${title.name}${title.location ? ` (${title.location})` : ""}` : "an empty tab";
  return html`<p class="panels-follow" role="note" title="A flow or model tab is in focus: this panel is that of a process tab">
    Showing <strong>${name}</strong>, the last process in focus</p>`;
}

// ---------------------------------------------------------------- lens: impact methods and group rules
// The impact methods and the group rules are chosen once for the workspace and shared by all tabs (the Methods and
// Rules panel on the left). The options of the view (display, cut-off, colours, columns…) belong to each tab
// (the view options pop-up, from the tab's row in the Processes panel or key v). Methods: the *active* method (tree, charts, totals) and the ones *shown together* (the several
// methods chart, the heat map). Rules: named sets, one of them active; rules scoped to a row of one tree stay in
// their tab. Kept per database in ``localStorage``, by method name (positions change when a model is rebuilt).
const LENS_KEY = `${STORAGE_KEY}.lens`;
// The options of the view as kept in the preferences; a pop-up's explorer lists its exchanges by section, in full names
// The view of the explorer in a pop-up (the exchanges of a process): the plain tree, in the sections of the form of a new
// process, whatever the options of the tabs are
const PLAIN_VIEW = { ...DEFAULT_VIEW, organize: "type", organizeFlows: "type", groupByRules: true, columns: DEFAULT_COLUMNS };
const initialView = (embedded) => (embedded ? { ...PLAIN_VIEW, recipe: { ...DEFAULT_RECIPE } } : preferredView());
// An option as the preferences keep it (see DEFAULT_VIEW), back to its value (the default when it does not read)
function loadOption(key, fallback) {
  if (typeof fallback === "boolean") return loadPreference(key, fallback ? "1" : "0") === "1";
  if (typeof fallback === "string") return loadPreference(key, fallback);
  try {
    const value = JSON.parse(loadPreference(key, "null"));
    return Array.isArray(value) === Array.isArray(fallback) && value !== null ? value : fallback;
  } catch { return fallback; }
}
const saveOption = (key, value) => savePreference(key, typeof value === "boolean" ? (value ? "1" : "0") : typeof value === "string" ? value : JSON.stringify(value));
const RECIPE_PREFERENCES = { main: "recipeMain", secondary: "recipeSecondary", flowMain: "recipeFlowMain", flowSecondary: "recipeFlowSecondary" };
const preferredView = () => oneGrouping(withOrganize({
  ...Object.fromEntries(Object.entries(DEFAULT_VIEW).map(([key, fallback]) => [key, loadOption(key, fallback)])),
  recipe: Object.fromEntries(Object.entries(RECIPE_PREFERENCES).map(([part, key]) => [part, loadPreference(key, DEFAULT_RECIPE[part])])),
}, { splitByType: loadPreference("splitByType", "0") === "1", ...(loadPreference("organize", "") ? { organize: true } : {}) }));
// The names are not a preference of the tabs any more (the Name display of the lens): ``naming`` and the recipe
// preferences are only read once, to start the lens (namingOf)
function saveViewPreferences({ view, display, orientation, cycles }) {
  for (const key of Object.keys(DEFAULT_VIEW)) if (key !== "naming") saveOption(key, view[key] ?? DEFAULT_VIEW[key]);
  savePreference("display", display);
  savePreference("orientation", orientation);
  savePreference("cycles", cycles ? "on" : "off");
}
const LensContext = createContext(null);
const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const methodNamed = (info, name) => info.methods.findIndex((m) => m.name.join("|") === (name || []).join("|"));
const familyOf = (info, k) => info.methods.flatMap((m, i) => (familyKey(m) === familyKey(info.methods[k]) ? [i] : []));

// The lens of the workspace: what was kept, else that of ``seed`` (the first tab of a workspace saved before the lens)
function initLens(info, seed) {
  let stored = null;
  try { stored = JSON.parse(localStorage.getItem(LENS_KEY) || "null"); } catch { /* none */ }
  const sameLayout = seed?.layout === info.layout;
  const seeded = (name, k) => { const i = name ? methodNamed(info, name) : -1; return i >= 0 ? i : sameLayout && k < info.methods.length ? k : -1; };
  let method = stored ? methodNamed(info, stored.method) : seed ? seeded(seed.methodName, seed.method) : -1;
  if (method < 0) method = info.default_method;
  const chart = stored ? (stored.chartMethods || []).map((n) => methodNamed(info, n))
    : seed?.chartMethods ? seed.chartMethods.map((k, n) => seeded(seed.chartMethodNames?.[n], k)) : null;
  const seeded_rules = (seed?.rules || []).filter((r) => r.scope === "*");
  const sets = stored?.sets && Object.keys(stored.sets).length ? stored.sets : { Default: seeded_rules.length ? seeded_rules : defaultRules() };
  return {
    method, chartMethods: chart ? chart.filter((k) => k >= 0) : familyOf(info, method), sets, set: stored?.set in sets ? stored.set : Object.keys(sets)[0],
    attribution: stored?.attribution === "direct" ? "direct" : "first_match",  // of the impacts to the rules: the Rules contribution and Compare
    nameFormat: { ...DEFAULT_NAME_FORMAT, ...(stored?.nameFormat || {}) },
    naming: namingOf(stored?.naming),  // the Name display of processes and flows (the Processes panel)
    palettes: stored?.palettes && typeof stored.palettes === "object" ? stored.palettes : {},  // the user's own: name -> colours
    palette: stored?.palette in RULE_PALETTES || stored?.palette in (stored?.palettes || {}) ? stored.palette : "auto",
    // colours picked for rules, by rule name (or field, for a group-by rule); before, a preference of the tabs' views
    ruleColors: stored?.ruleColors || loadOption("ruleColors", {}),
  };
}
function saveLens(info, lens) {
  try {
    localStorage.setItem(LENS_KEY, JSON.stringify({ method: info.methods[lens.method]?.name,
      chartMethods: lens.chartMethods.map((k) => info.methods[k]?.name).filter(Boolean), sets: lens.sets, set: lens.set,
      attribution: lens.attribution, nameFormat: lens.nameFormat, naming: lens.naming, palette: lens.palette, palettes: lens.palettes, ruleColors: lens.ruleColors }));
  } catch { /* storage unavailable */ }
}

// A method as the Brightway name (family, category, indicator…) shown in parts: the category as the main text, the indicator
// and what follows in grey, as the products of the explorer
const methodParts = (m) => { const p = methodPath(m); return { family: familyKey(m), main: p.rest[1] ?? p.family, qual: p.indicator }; };
// ``Methods`` summary: the active method, and how many more are shown together
const methodSummary = (info, method, chartMethods) => {
  const m = info.methods[method];
  const more = chartMethods.filter((k) => k !== method).length;
  const p = methodPath(m);
  return `${p.family} · ${p.rest.slice(1).join(" | ") || p.family}${more ? ` (+${more})` : ""}`;
};

// The impact methods in one list nested by family: one mark per method, ✓ shown together, ★ the main (active)
// method. ``note``: what a change here applies to.
function MethodMenu({ info, method, setMethod, chartMethods, setChartMethods, note, format, setFormat, adder, onCategory }) {
  return html`
    <div class="lens-menu">
      ${note && html`<p class="note">${note}</p>`}
      ${format && html`<${Fold} id="nameformat" title="Name display" open=${false}
        actions=${html`<${InfoTip} text="How a method is named in titles, headers, files and the several-methods charts" label="About the name" />`}>
        <div class="view-group" role="group" aria-label="Name format">
          <span class="segmented">
            ${[["parts", "Parts", "Pick the parts"], ["custom", "Custom", "Your format"]].map(([k, label, hint]) => html`
              <button class=${format.mode === k ? "primary" : ""} title=${hint} onClick=${() => setFormat({ ...format, mode: k })}>${label}</button>`)}
          </span>
        </div>
        ${format.mode === "parts" ? html`
          <div class="row">
            ${[...(info.methods.some((m) => methodPath(m).source) ? [["source", "Source", "ecoinvent-3.10.1: the release the method came with"]] : []),
              ["family", "Family", "EF v3.1"], ["category", "Category", "Climate change"], ["indicator", "Indicator", "GWP100"], ["unit", "Unit", "kg CO₂ eq"]].map(([k, label, hint]) => html`
              <label title=${hint}><input type="checkbox" checked=${format[k]} onChange=${(e) => setFormat({ ...format, [k]: e.target.checked })} /> ${label}</label>`)}
          </div>` : html`
          <div class="row">
            <input class="recipe-input" value=${format.recipe} onInput=${(e) => setFormat({ ...format, recipe: e.target.value })} aria-label="Format of the method names" />
          </div>
          <div class="row tokens">
            ${METHOD_TOKENS.map((t) => html`<button class="token" onClick=${() => setFormat({ ...format, recipe: `${format.recipe} {${t}}`.trim() })}>{${t}}</button>`)}
            <button onClick=${() => setFormat({ ...format, recipe: DEFAULT_NAME_FORMAT.recipe })} title="The default format">Default</button>
          </div>`}
        <p class="note">${methodTitle(info.methods[method])}</p>
      <//>`}
      <h3 class="fold-head method-selection">Method selection
        <${InfoTip} text="Click a method's box in turn: ✓ shown together, in the multi indicator charts and the matrix; ★ the main method, for the single indicator, the tree and the charts (the one before stays ticked); again to untick it (the first other ticked method becomes the main one). The family box ticks or unticks the whole family." label="Legend of the method selection" /></h3>
      <${MethodTree} info=${info} main=${method} setMain=${setMethod} selected=${chartMethods} setSelected=${setChartMethods} onCategory=${onCategory} />
      ${adder && html`<${AddMethods} info=${info} ...${adder} />`}
    </div>`;
}

// More impact methods for the open database, from its Brightway project: their factors are read and only their impacts
// computed (the model is not built again). ``onAdded``: the server's model has them, the page reads its info again.
function AddMethods({ info, origin, onAdded }) {
  const [open, setOpen] = useState(false);
  const [listing, setListing] = useState(null);
  const [error, setError] = useState(null);
  const [chosen, setChosen] = useState(() => new Set());
  const [job, setJob] = useState(null);
  useEffect(() => {
    if (!open) return;
    const q = new URLSearchParams({ folder: origin.folder, project: origin.project });
    serverRequest(`/api/brightway/methods?${q}`).then((r) => setListing([...r.methods, ...(r.library || [])].filter((m) => methodNamed(info, m.name) < 0)))
      .catch((e) => setError(e.message));
  }, [open, info]);
  useEffect(() => {
    if (job?.status !== "running" || !job.id) return;
    const timer = setTimeout(() => serverRequest(`/api/jobs/${job.id}`).then((j) => {
      setJob(j);
      if (j.status === "done") { setChosen(new Set()); onAdded(); }
    }).catch((e) => setJob({ status: "failed", error: e.message })), 700);
    return () => clearTimeout(timer);
  }, [job]);
  const names = (listing || []).filter((m) => chosen.has(methodKey(m.name))).map((m) => m.name);
  const running = job?.status === "running";
  const add = () => {
    setJob({ status: "running", message: "Starting…" });
    serverRequest("/api/methods/add", { key: DATABASE, methods: names }).then(setJob).catch((e) => setJob({ status: "failed", error: e.message }));
  };
  if (!open) return html`
    <p class="note"><button class="link" onClick=${() => setOpen(true)}
      title=${`Methods of ${origin.project} and of the methods library that this database was not built with`}>Add impact methods…</button></p>`;
  return html`
    <h3 class="fold-head">Add impact methods
      <${InfoTip} label="About adding methods" text=${`The methods of the project ${origin.project} and of the Methods library (Projects) that this database was not built with. Only their impacts are computed (the model is not built again): seconds, plus a few for each method on a large database. They are added for all the databases of the project.`} />
      <button class="link" onClick=${() => setOpen(false)} aria-label="Close">×</button></h3>
    ${error && html`<div class="error">${error}</div>`}
    ${!listing ? !error && html`<${Waiting} text="Reading the impact methods of the project…" />`
      : !listing.length ? html`<p class="muted small">The database has all the impact methods of ${origin.project} and of the methods library.</p>` : html`
        <${MethodPicker} methods=${listing} chosen=${chosen} setChosen=${setChosen} />
        <div class="row">
          <button class="primary" disabled=${!names.length || running} onClick=${add}>
            ${running ? "Adding…" : `Add ${names.length || ""} ${names.length === 1 ? "method" : "methods"}`}</button>
          ${running && html`<span class="spinner" aria-hidden="true"></span><span class="muted small">${job.message}</span>`}
          ${job?.status === "done" && html`<span class="muted small">${job.message}</span>`}
        </div>`}
    ${job?.status === "failed" && html`<div class="error">${job.error}</div>`}`;
}

// The rule sets of the workspace: the one in use, new / copy / rename / delete. ``note``: what a change here applies to.
function SetMenu({ lens, onEdit }) {
  const names = Object.keys(lens.sets);
  const ask = (text, value = "") => { const name = window.prompt(text, value); return name && name.trim() ? name.trim() : null; };
  return html`
    <div class="lens-menu">
      <ul class="rule-sets">
        ${names.map((name) => html`
          <li key=${name}><label><input type="radio" name="rule-set" checked=${name === lens.set} onChange=${() => lens.chooseSet(name)} />
            <span class="name">${name}</span> <span class="muted">${lens.sets[name].length} ${lens.sets[name].length === 1 ? "rule" : "rules"}</span></label></li>`)}
      </ul>
      <div class="row">
        <button onClick=${() => { const n = ask("Name of the new rule set"); if (n) lens.addSet(n, false); }}>New</button>
        <button onClick=${() => { const n = ask("Name of the copy", `${lens.set} copy`); if (n) lens.addSet(n, true); }}>Copy</button>
        <button onClick=${() => { const n = ask("New name of the rule set", lens.set); if (n && n !== lens.set) lens.renameSet(n); }}>Rename</button>
        <button disabled=${names.length < 2} onClick=${() => window.confirm(`Delete the rule set ${lens.set}? The tabs that follow it move to another set.`) && lens.deleteSet()}>Delete</button>
      </div>
      ${onEdit && html`<p class="note"><button class="link" data-close onClick=${onEdit}>Edit the rules of ${lens.set}…</button></p>`}
    </div>`;
}

// A label with the letter of its shortcut ``k`` underlined while the pointer is on its button (``.has-key``)
const KeyedLabel = ({ label, k }) => {
  const i = label.toLowerCase().indexOf(k);
  return i < 0 ? label : html`${label.slice(0, i)}<u class="key-hint">${label[i]}</u>${label.slice(i + 1)}`;
};
// ←/→ on the tab bar of a side pane: the previous or the next panel, the focus staying on the bar
const tabKeys = (ids, current, setCurrent) => (e) => {
  if ((e.key !== "ArrowLeft" && e.key !== "ArrowRight") || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
  e.preventDefault();
  e.stopPropagation();
  const next = ids[(ids.indexOf(current) + (e.key === "ArrowRight" ? 1 : -1) + ids.length) % ids.length];
  const bar = e.currentTarget;
  setCurrent(next);
  setTimeout(() => bar.querySelector("button.active")?.focus(), 0);
};
// The left panel, where the processes (the processes of the tabs, their amounts, what is compared), the impact
// methods and the group rules are chosen: *Processes*, *Methods* and *Rules* (the rule sets and the rules). The rules
// shown are those of the tab in focus, which its own component puts in its slot (``groups-<tab id>``); in Compare mode
// they are those of the workspace's set (``view``: a tab's view, where the colours picked for rules are kept).
// ``focusedTab``: the process tab whose rules are shown, ``follow``: the line that says so when it is not the tab in focus.
function LensPanel({ info, lens, tab, setTab, tabs, focusedTab, follow, comparing, view, compareTabs, flowTabs = [], compare, setCompare, windows, onCollapse, adder, onCategory }) {
  const methods = lens.method < info.methods.length;
  return html`
    <div class="tabs" role="tablist" onKeyDown=${tabKeys(["processes", "methods", "groups"], tab, setTab)}>
      ${[["processes", "Processes"], ["methods", "Methods"], ["groups", "Rules"]].map(([key, label]) => html`
        <${TabButton} role="tab" class=${tab === key ? "active" : ""} onClick=${() => setTab(key)} tip=${PANEL_TIPS[key]}>${label}<//>`)}
      <span class="spacer"></span>
      <button class="collapse-side" onClick=${onCollapse} title="Hide the panel (Ctrl/⌘ + ←)" aria-label="Hide the panel"><${SidePanelIcon} open left /></button>
    </div>
    <div class="panel" style=${{ display: tab === "processes" ? "" : "none" }}>
      <${ProcessesPanel} tabs=${compareTabs} flowTabs=${flowTabs} compare=${compare} setCompare=${setCompare} windows=${windows}
        naming=${lens.naming} setNaming=${lens.setNaming} />
    </div>
    <div class="panel methods-panel" style=${{ display: tab === "methods" ? "" : "none" }}>
      ${methods && html`<${MethodMenu} info=${info} method=${lens.method} setMethod=${lens.setMethod} chartMethods=${lens.chartMethods} setChartMethods=${lens.setChartMethods}
        format=${lens.nameFormat} setFormat=${lens.setNameFormat} adder=${adder} onCategory=${comparing ? null : onCategory} />`}
    </div>
    <div class="groups-panel" style=${{ display: tab === "groups" ? "" : "none" }}>
      ${comparing && view && html`<${GroupPanel} info=${info} rules=${lens.rules} setRules=${lens.setRules} selectedRow=${null} view=${view.view} setView=${view.setView}
        shared=${{ set: lens.set }} extras=${ruleExtras(lens)} />`}
      ${!comparing && follow}
      ${tabs.map((id) => html`<div key=${id} id=${`groups-${id}`} class="groups-slot" style=${{ display: id === focusedTab && !comparing ? "" : "none" }}></div>`)}
    </div>`;
}

// ---------------------------------------------------------------- view options (a pop-up per process tab)
// Children drawn in a box of the page itself, as BodyPortal, but presses in it reach the page: the menus inside (Save ▾,
// By…) close on a press elsewhere in it. The pop-up of a tab's view options shows even while its tab is hidden.
function PagePortal({ children }) {
  const host = useRef(null);
  if (!host.current) host.current = document.createElement("div");
  useEffect(() => {
    document.body.appendChild(host.current);
    return () => { render(null, host.current); host.current.remove(); };
  }, []);
  useEffect(() => { render(children, host.current); });
  return null;
}
// The icon of the view options (sliders), on each process of the Processes panel
function SlidersIcon() {
  return html`
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
      <path d="M2.5 4H4M7 4h6.5M2.5 8H9M12 8h1.5M2.5 12h3M8.5 12h5" />
      <circle cx="5.5" cy="4" r="1.5" /><circle cx="10.5" cy="8" r="1.5" /><circle cx="7" cy="12" r="1.5" />
    </svg>`;
}
// Where the view options of a tab open when asked by the keyboard (v): beside its button in the Processes panel when
// it is in sight, else near the top left of the workspace
function viewOptionsPlace(id) {
  const button = document.querySelector(`[data-view-options="${CSS.escape(id)}"]`);
  const r = button?.getBoundingClientRect();
  return r && r.width ? [r.right, r.top - 8] : [Math.round(window.innerWidth / 4), 96];
}
// The view options of a process tab: Display, Cut-off, Organise by, Group by, Color by, Rows, with Reset and
// Save ▾. Drawn by the tab (``FUView``), which gives the options (``children``) and the buttons (``actions``).
function ViewOptionsPopup({ target, win, title, actions, onClose, children }) {
  return html`
    <${PopupWindow} target=${target} win=${win} label="View options" className="view-options-popup" width=${430} onClose=${onClose} onBack=${null}
      head=${html`<div class="info-title">View options</div><div class="muted">${title}</div>`}>
      <div class="view-options-actions">${actions}</div>
      <div class="info-body">${children}</div>
    <//>`;
}

// ---------------------------------------------------------------- process view (one tab)
// The tree that takes keyboard navigation: the one clicked last (several can be visible in split panes)
let keyboardOwner = null;
const TAB_API = {};  // tab id -> { view, setView } of the tab: the comparison's group rules take their colours from a view
const POPUP_OPENERS = {};  // tab id -> the function that opens the pop-up of the process of the tab at (x, y): the Processes panel
const CATEGORY_OPENERS = {};  // tab id (process, flow or model tab) -> the function that opens the model pop-up of a method (the Methods panel)
const ROOT_SETTERS = {};  // tab id -> the function that makes a process the process of the tab (the search in the top bar)
const VIEW_OPTIONS_OPENERS = {};  // process tab id -> the function that opens the pop-up of the tab's view options at (x, y): the Processes panel, key v
// ``initial``: { state } a saved view, { inherit, root? } settings taken from another tab,
// or { defaultRoot: true } (first start: the model's default process)
// ``embedded``: the left panel alone (header, list and bars, without the side panel), inside the Exchanges tab of a
// process pop-up, rooted at that process and with a state of its own: { onInfo(target): a process or flow opened from
// a row replaces the content of the pop-up, onUse(meta): Enter on a process, keys: ref the pop-up asks first for its keys }
function FUView({ id, info, initial, visible, onSave, onTitle, onOpenTab, onNewTab, onCloseTab, embedded }) {
  // Edit mode: merges, custom names and replaced suppliers only change how the view looks, not the data: they are
  // left out (and kept in the tab, for the view mode); pins, cut-off and groups stay
  const editor = useContext(EditorContext);
  const editing = editor.editing;
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
  // The method(s) and the group rules are those of the workspace (the lens), shared by all tabs. The tab keeps the rules
  // scoped to a row of its tree, which mean nothing to the others. The explorer of a pop-up takes the method of the tab
  // and has rules of its own.
  const lens = useContext(LensContext);
  const [ownRules, setOwnRules] = useState([]);
  const method = embedded ? (embedded.method ?? lens.method) : lens.method;
  const chartMethods = embedded ? [] : lens.chartMethods;
  const rules = useMemo(() => (embedded ? ownRules : [...lens.rules, ...ownRules]), [lens.rules, ownRules]);
  function setRules(next) {
    const list = typeof next === "function" ? next(rules) : next;
    if (!embedded && list.length > rules.length && view.groupByRules === false) {
      setView({ ...view, groupByRules: true });
      setUndo({ message: "Group by rules is on again: the rule you added groups the tree." });
    }
    if (embedded) { setOwnRules(list); return; }
    const shared = list.filter((r) => r.scope === "*");
    if (!sameJson(shared, lens.rules)) lens.setRules(shared);
    setOwnRules(list.filter((r) => r.scope !== "*"));
  }
  // The options of the view (cut-off, display, colours, columns…; not the names: see viewNaming) are the tab's own; the ones of the last tab
  // changed are the preferences that a new tab starts with
  const [cutoff, setCutoffState] = useState(DEFAULT_CUTOFF);
  // Never under 0 (nor not a number, from an old or hand-edited save): 0 is the least, all rows with an impact shown
  const setCutoff = (v) => setCutoffState(Number.isFinite(+v) && +v > 0 ? +v : 0);
  const [cutoffBasis, setCutoffBasis] = useState("parent");
  const [ownView, setView] = useState(() => initialView(embedded));
  // The names follow the Name display of the workspace (the Processes panel), not the tab: ``view.naming`` and
  // ``view.recipe`` come from the lens, for every reader of the view
  const view = useMemo(() => ({ ...ownView, ...viewNaming(lens?.naming || DEFAULT_NAMING) }), [ownView, lens?.naming]);
  const [display, setDisplay] = useState(() => (embedded ? "list" : loadPreference("display", "list")));
  const [orientation, setOrientation] = useState(() => loadPreference("orientation", "horizontal"));
  const [cycles, setCycles] = useState(() => loadPreference("cycles", "off") === "on");
  // kept once changed, not at the first render (the defaults of a first start are not written: see DEFAULT_VIEW_COLUMNS)
  const viewChanged = useRef(false);
  useEffect(() => {
    if (!viewChanged.current) { viewChanged.current = true; return; }
    if (!embedded) saveViewPreferences({ view, display, orientation, cycles });
  }, [view, display, orientation, cycles]);
  const [undo, setUndo] = useState(null);
  // Pop-ups: see usePopupStack (the same in a flow tab)
  const stack = usePopupStack();
  const popups = stack.popups;
  const infoTarget = popups.find((p) => !p.pinned)?.target;
  // An embedded explorer hands processes and flows to the pop-up it is in; only bundles (others, merged, group) open here
  const openInfo = (target) => (embedded && !target.bundle ? handOver(target) : stack.open(target));
  // The pop-up of a row of this tree has no row of its own in the pop-up's view: what it needs of it (the process
  // it is an input of) goes along
  function handOver(target) {
    const { row, x, y, ...rest } = target;
    embedded.onInfo({ ...rest, row: null, ...(row?.kind === "process" ? { parents: suppliedTo(row, rowsById) } : {}) });
  }
  const closeInfo = stack.close;
  const outerUse = useContext(TabUseContext);  // embedded: the tab's (a flow of its tree opens in a flow tab)
  const [focus, setFocus] = useState(null);
  const focusTimer = useRef(null);
  const [expanded, setExpanded] = useState(new Set());
  const [pinned, setPinned] = useState(new Set());
  const [others, setOthers] = useState(new Set());
  const [merges, setMerges] = useState([]);
  const [ungrouped, setUngrouped] = useState(new Set());  // "group by product" rows the user ungrouped
  const [undoSteps, setUndoSteps] = useState([]);  // states before the changes, the last one undone first
  // Inputs replaced by another supplier: {row id of the input: activity index}
  const [substitutions, setSubstitutions] = useState({});
  const [selection, setSelection] = useState(new Set());
  const [rawData, setData] = useState({ methods: [], rows: [] });
  // Custom row names (labelKey → name), part of the tab's view; applied to the rows here
  // so that the tree, the pop-ups and the chart all read them
  const [labels, setLabels] = useState({});
  const [renaming, setRenaming] = useState(null);
  const allData = useMemo(() => {
    const rows = withLabels(rawData.rows, editing ? {} : labels);
    return { ...rawData, rows: organizing(view) ? splitRows(rows, view) : rows };
  }, [rawData, labels, editing, view.organize, view.organizeFlows]);
  // Hide others: the others rows are left out of the list, the tree and the Sankey (the chart keeps them)
  const data = useMemo(() => (view.hideOthers ? { ...allData, rows: withoutOthers(allData.rows) } : allData),
    [allData, view.hideOthers]);
  const [error, setError] = useState(null);
  // The sub-panel of the card's Contribution panel (Process, Flow, Uncharacterised, Rules), kept by the tab
  const [contributionTab, setContributionTab] = useState("chart");
  // The pop-up of the view options (the Processes panel's button, key v): { x, y, rect, pinned }, or null
  const [optionsPop, setOptionsPop] = useState(null);
  const openViewOptions = (x, y) => setOptionsPop((p) => ({ x, y, rect: p?.rect ?? null, pinned: p?.pinned ?? false }));
  useEffect(() => {
    if (embedded) return;
    // key v: the tab in focus (``detail.id``), else the tree clicked last
    const show = (e) => (e.detail?.id ?? keyboardOwner) === id && openViewOptions(...viewOptionsPlace(id));
    document.addEventListener("lca:custom-view", show);
    return () => document.removeEventListener("lca:custom-view", show);
  }, [id]);
  const [groupName, setGroupName] = useState("");

  useEffect(() => {
    if (initial?.state?.root) restoreTab(initial.state);
    else startTab();
  }, []);
  useEffect(() => onTitle(id, root), [root]);

  // Where a saved process is in this database: { target, same }, same when it is still
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
  // process and methods are found again by name, and what refers to other positions
  // (expanded rows, pins, moved rows, merges, names, groups of this tree) is not restored.
  // Tabs saved before layouts were recorded: as long as the process did not move.
  async function restoreTab(saved) {
    const sameLayout = saved.layout === info.layout;
    const found = sameLayout ? { target: saved.root, same: true } : await locateRoot(saved.root).catch(() => null);
    if (!found) {
      startTab();
      setError(`${saved.root.name} (${saved.root.location}), the process of this tab, is not in this database any more.`);
      return;
    }
    const { target } = found;
    const same = found.same && (sameLayout || !saved.layout);
    setRoot(target); setAmount(saved.amount ?? 1);
    setCutoff(saved.cutoff ?? DEFAULT_CUTOFF); setCutoffBasis(saved.cutoffBasis || "parent");
    setOwnRules(embedded ? [] : same ? (saved.rules || []).filter((r) => r.scope !== "*") : []);
    setExpanded(new Set(same ? saved.expanded : [String(target.index)]));
    setPinned(new Set(same ? saved.pinned || [] : []));
    setOthers(new Set(same ? saved.others || [] : []));
    setMerges(same ? saved.merges || [] : []);
    setUngrouped(new Set(same ? saved.ungrouped || [] : []));
    setSubstitutions(same ? saved.substitutions || {} : {});
    setLabels(same ? saved.labels || {} : {});
    if (!same) setUndo({ message: "This database was rebuilt since the tab was saved: expansions, pins, moved rows, merges and names were not restored." });
  }

  function startTab() {
    const i = info;
    // A new tab keeps the cut-off of the tab it was opened from (the method and the rules are the workspace's); the
    // explorer of a pop-up starts with the rules it is given
    const base = initial?.inherit;
    if (base) {
      setCutoff(base.cutoff ?? DEFAULT_CUTOFF);
      setCutoffBasis(base.cutoffBasis || "parent");
      if (embedded) setOwnRules((base.rules || []).filter((rule) => rule.scope === "*"));
    }
    if (initial?.root) selectRoot(initial.root);
    else if (initial?.defaultRoot && i.default_root) selectRoot(i.default_root);
    else document.querySelector(".top-bar .fu-input input")?.focus({ preventScroll: true });
  }

  if (!embedded) {
    ROOT_SETTERS[id] = (r) => { keyboardOwner = id; selectRoot(r); };
    POPUP_OPENERS[id] = (x, y) => root && openInfo({ index: root.index, row: null, x, y });
    CATEGORY_OPENERS[id] = (category, x, y) => openInfo({ category, x, y });
    VIEW_OPTIONS_OPENERS[id] = openViewOptions;
    TAB_API[id] = { view, setView };
  }
  function selectRoot(r) {
    setRoot(r);
    setExpanded(new Set([String(r.index)]));
    setPinned(new Set());
    setOthers(new Set());
    setMerges([]);
    setUngrouped(new Set());
    setSubstitutions({});
    setOwnRules((current) => (embedded ? current.filter((rule) => rule.scope === "*") : []));
    setSelection(new Set());
    setUndo(null);
  }

  // Pins are exceptions to one cut-off, and moved rows mix with the cut-off rows in
  // "others": a new cut-off starts from a clean slate, with an undo.
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const describeCustom = (p, o) => [p.size && `${plural(p.size, "pinned row")}`, o.size && `${plural(o.size, "row")} moved to others`].filter(Boolean).join(" and ");
  function changeCutoff(next, basis = cutoffBasis) {
    if (pinned.size || others.size) {
      setUndo({ message: `Cut-off changed: ${describeCustom(pinned, others)} reset.` });
      setPinned(new Set());
      setOthers(new Set());
    }
    setCutoff(next);
    setCutoffBasis(basis);
  }
  // Switching the mode keeps the number between the two shares (total, parent); else the
  // default of the new mode (a count of rows for "top")
  function changeCutoffMode(mode) {
    const shares = ["total", "parent"];
    const keep = shares.includes(mode) && shares.includes(cutoffBasis);
    changeCutoff(keep ? cutoff : CUTOFF_DEFAULTS[mode], mode);
  }
  // Back to the plain tree: collapsed, no pins or moved rows, no groups (rules, quick
  // groups, group by product), no merges and no custom names; the notice can undo it all
  function resetView() {
    const removed = [describeCustom(pinned, others), ownRules.length && plural(ownRules.length, "grouping rule"),
      merges.length && plural(merges.length, "merge"), view.groupProducts && "group by product",
      Object.keys(substitutions).length && plural(Object.keys(substitutions).length, "replaced input"),
      Object.keys(labels).length && plural(Object.keys(labels).length, "custom name")].filter(Boolean).join(", ");
    setUndo({ message: `View reset${removed ? ` (${removed} removed)` : ""}${!embedded && lens.rules.length ? `; the rule set ${lens.set} of the workspace stays (Rules panel)` : ""}.` });
    setExpanded(new Set([String(root.index)]));
    setPinned(new Set());
    setOthers(new Set());
    setOwnRules([]);
    setMerges([]);
    setUngrouped(new Set());
    setSubstitutions({});
    setLabels({});
    if (view.groupProducts) setView({ ...view, groupProducts: false });
    setSelection(new Set());
    closeInfo();
  }
  // Undo: each change of the tree, groups, pins, names and settings is a step. Expanding and
  // collapsing are no steps of their own: they are kept with the step they fall in. A functional
  // unit's first change has nothing to go back to (no step), so a new tab starts without steps.
  const edits = { root, amount, method, cutoff, cutoffBasis, chartMethods, rules, pinned: [...pinned], others: [...others],
    merges, substitutions, labels, ungrouped: [...ungrouped], view };
  const editKey = JSON.stringify(edits);
  const expandedKey = JSON.stringify([...expanded]);
  const undoBase = useRef(null);  // the state seen last: { key, edits, expanded }
  useEffect(() => {
    const before = undoBase.current;
    undoBase.current = { key: editKey, edits, expanded: [...expanded] };
    if (before && before.key !== editKey && before.edits.root) setUndoSteps((steps) => [...steps.slice(-99), before]);
  }, [editKey, expandedKey]);
  // Ctrl+Z and the Undo buttons: back to the state before the last change
  function undoLast() {
    const step = undoSteps[undoSteps.length - 1];
    if (!step) return false;
    setUndoSteps(undoSteps.slice(0, -1));
    undoBase.current = step;
    const e = step.edits;
    setRoot(e.root); setAmount(e.amount);
    if (!embedded) {
      lens.setMethod(e.method); lens.setChartMethods(e.chartMethods);
      const shared = e.rules.filter((r) => r.scope === "*");
      if (!sameJson(shared, lens.rules)) lens.setRules(shared);
    }
    setOwnRules(embedded ? e.rules : e.rules.filter((r) => r.scope !== "*"));
    setCutoff(e.cutoff); setCutoffBasis(e.cutoffBasis); setView(e.view);
    setPinned(new Set(e.pinned)); setOthers(new Set(e.others)); setMerges(e.merges); setSubstitutions(e.substitutions); setLabels(e.labels);
    setUngrouped(new Set(e.ungrouped)); setExpanded(new Set(step.expanded)); setSelection(new Set());
    return true;
  }

  const viewState = (exp = expanded) => ({
    root: [root.index], amount, expanded: [...exp], rules: [...(view.groupByRules === false ? [] : rules), ...flowRulesOf(view)], cutoff: cutoffValue(cutoff, cutoffBasis), cutoff_basis: cutoffBasis, method, methods: chartMethods,
    pinned: [...pinned], others: [...others], merges: editing ? [] : merges, ungrouped: [...ungrouped],
    substitutions: editing ? {} : substitutions, group_products: view.groupProducts, collapse_singletons: view.collapseSingletons,
    colour_rules: rules,
    attribution: lens.attribution,
  });

  useEffect(() => {
    if (!root || !info) return;
    let cancelled = false;
    request("/api/rows", viewState())
      .then((d) => { if (!cancelled) { setData(d); setError(null); } })
      .catch((e) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [root, amount, method, lens.attribution, cutoff, cutoffBasis, expanded, rules, chartMethods, pinned, others, merges, ungrouped, substitutions, ...TREE_OPTIONS.map((k) => view[k]), info, editing]);
  // The tab's state is kept for the next visit (renaming does not reload the rows)
  useEffect(() => {
    if (!root || !info) return;
    onSave(id, { root, amount, method, cutoff, cutoffBasis, expanded: [...expanded], rules, chartMethods, ...pick(view, TREE_OPTIONS), pinned: [...pinned], others: [...others], merges, ungrouped: [...ungrouped], substitutions, labels,
      layout: info.layout, methodName: info.methods[method]?.name, chartMethodNames: chartMethods.map((k) => info.methods[k]?.name) });
  }, [root, amount, method, cutoff, cutoffBasis, expanded, rules, chartMethods, ...TREE_OPTIONS.map((k) => view[k]), pinned, others, merges, ungrouped, substitutions, labels, info]);
  function startRename(row) {
    if (editing || !labelKey(row)) return;
    // The tree and Sankey views have no inline editor: ask
    if (display !== "list") {
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
  useEffect(() => {
    setSelection((sel) => {
      const shown = [...sel].filter((id) => rowsById.has(id));
      return shown.length === sel.size ? sel : new Set(shown);
    });
  }, [rowsById]);
  const selectedRows = [...selection].map((id) => rowsById.get(id)).filter(Boolean);
  const selectedTotals = selectedProcessTotals(rowsById, selection);
  const siblings =selectedRows.length >= 2 && selectedRows.every((r) => ownerOf(r) === ownerOf(selectedRows[0]))
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
  // An input supplied by another activity (a saved view). Keyed by the input's row id without
  // the replacement.
  function revertInput(row) {
    const rest = { ...substitutions };
    delete rest[row.replaced];
    setUndo({ message: `Supplier of ${rowNames(row, rowsById, view).main} back to the original.` });
    setSubstitutions(rest);
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
  const showFlowInfo = (target, e) => { e.preventDefault(); openInfo({ ...target, x: e.clientX, y: e.clientY }); };
  // The pop-up of a row: a process (or direct emissions: its emitter), a flow, or the
  // members of a bundle (others, merged, group); null when there is nothing to show
  // ``exchange``: a process's exchange (comment, uncertainty) rather than its activity
  function infoTargetOf(row, exchange = false) {
    if (row.kind === "rest" || row.kind === "merged" || row.kind === "group") return { bundle: row.id };
    if (row.kind === "flow") return { flow: row.flow.index, activities: (row.emitters || []).map((m) => m.index), exchange };
    return row.meta ? { index: row.meta.index, row, direct: row.kind === "direct", exchange } : null;
  }
  function showInfo(row, e, exchange = false) {
    e.preventDefault();
    // A right-click opens the pop-up of the row's process or flow, as everywhere; its exchange is its (i) (or i)
    const target = infoTargetOf(row, exchange);
    if (target) openInfo({ ...target, x: e.clientX, y: e.clientY });
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
  function groupSelected() { return groupRows(selectedRows); }
  // A group of sibling rows. Its name: the typed one; else the product name when the processes all
  // deliver one product; else "group N". A default name already in use gets a number.
  function groupRows(members) {
    const parent = ownerOf(members[0]);
    const sameProduct = members.every((r) => r.kind === "process") && members.every((r) => productNameOf(r) === productNameOf(members[0]));
    let name = groupName.trim();
    if (!name) {
      const base = sameProduct ? productNameOf(members[0]) : `group ${rules.filter((r) => r.predicate?.products || r.predicate?.flows).length + 1}`;
      name = base;
      for (let n = 2; rules.some((r) => r.scope === parent && r.name === name); n++) name = `${base} ${n}`;
    }
    const rule = members[0].kind === "flow"
      ? { predicate: { flows: members.map((r) => r.flow.index) }, target: "flows" }
      : { predicate: { products: members.map((r) => productOf(r.id)) } };
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
  // The × (move to others) or pin of one of several selected rows is the one of all of them
  const ofSelection = (ids, keep) => (ids.length === 1 && selection.size > 1 && selection.has(ids[0])
    ? movableSelection.filter(keep).map((r) => r.id) : ids);
  const moveSelectedToOthers = (ids) => moveToOthers(ofSelection(ids, (r) => !inOthers(r)));
  const pinSelected = (ids) => pin(ofSelection(ids, inOthers));
  // A bundle that disappeared (all its members shown, unmerged, ungrouped) closes its pop-up
  useEffect(() => {
    const live = (p) => !p.target.bundle || findRow(p.target.bundle);
    stack.keep(live);
  }, [popups, data]);
  const manualRuleFor = (groupRow) => rules.findIndex((r) => r.predicate && !r.disabled && r.scope === ownerOf(groupRow) && `${ownerOf(groupRow)}/g:${r.name}` === groupRow.id);
  // A group that can be ungrouped: one of a rule, or one of "group by product"
  const ungroupable = (groupRow) => !!groupRow.by_product || manualRuleFor(groupRow) >= 0;
  // A group made by a rule that cannot be undone from the row ("group by" rules): it stays while the rule is active
  const groupLocked = (groupRow) => groupRow.kind === "group" && !ungroupable(groupRow);
  function ungroup(groupRow) {
    if (groupRow.by_product) { setUngrouped(new Set([...ungrouped, groupRow.id])); return; }
    const i = manualRuleFor(groupRow);
    if (i >= 0) setRules(rules.filter((_, j) => j !== i));
  }
  // Put an ungrouped "group by product" back: its members are bundled again
  const regroup = (groupId) => setUngrouped(new Set([...ungrouped].filter((id) => id !== groupId)));
  // Turning "group by product" on starts from a clean slate: the groups ungrouped before are grouped again
  const setDisplayView = (next) => { if (next.groupProducts && !view.groupProducts) setUngrouped(new Set()); setView(next); };
  // Saved views store names (method, process) as well as positions: node ids are
  // positions in the model, valid only for the model the view was saved from.
  // Version 2: ``cutoff`` is a fraction, as in the API (the page shows a percentage; version 1 stored it)
  function viewFile() {
    return {
      format: "lca_viewer view", version: 2,
      model: { title: info.title, activities: info.activities },
      root, amount, method: info.methods[method].name, chartMethods: chartMethods.map((k) => info.methods[k].name),
      cutoff: +cutoffValue(cutoff, cutoffBasis).toFixed(8), cutoffBasis, expanded: [...expanded], rules, pinned: [...pinned], others: [...others], merges, ungrouped: [...ungrouped], substitutions, labels,
      display: { ...pick(view, Object.keys(DEFAULT_VIEW)), recipe: view.recipe },
    };
  }
  function exportView() {
    const name = root ? `${root.name}-${root.location}`.replace(/[^\w.-]+/g, "_").slice(0, 60) : "view";
    download(`lca-view-${name}.json`, JSON.stringify(viewFile(), null, 2), "application/json");
  }
  async function copyView() {
    try { await navigator.clipboard.writeText(JSON.stringify(viewFile(), null, 2)); setUndo({ message: "View copied to the clipboard as JSON." }); }
    catch { setError("The browser did not allow copying to the clipboard."); }
  }
  // The settings of a first start (the view of this tab, and the method, the rules and the names of the workspace)
  function loadDefaults() {
    setView({ ...DEFAULT_VIEW, recipe: { ...DEFAULT_RECIPE } });
    setDisplay("list");
    setCutoff(DEFAULT_CUTOFF);
    setCutoffBasis("parent");
    if (!embedded) lens.resetDefaults();
    setUndo({ message: "Default settings loaded." });
  }
  async function loadView(file) {
    try {
      const saved = JSON.parse(await file.text());
      if (!saved.root || !Array.isArray(saved.expanded)) throw new Error("This file is not a saved view.");
      // Same process at the same position: everything applies; otherwise find it by name
      const found = await locateRoot(saved.root);
      if (!found) throw new Error(`${saved.root.name} (${saved.root.location}) is not in this database.`);
      const { target, same: sameModel } = found;
      // The method, the methods shown together and the rules of the file are the workspace's now (the rules as a set of their own)
      if (!embedded) {
        const named = (names) => (names || []).map((n) => methodNamed(info, n)).filter((i) => i >= 0);
        if (saved.method && methodNamed(info, saved.method) >= 0) lens.setMethod(methodNamed(info, saved.method));
        if (saved.chartMethods && named(saved.chartMethods).length) lens.setChartMethods(named(saved.chartMethods));
        const shared = (saved.rules || []).filter((r) => r.scope === "*");
        if (shared.length && !sameJson(shared, lens.rules)) lens.loadSet(file.name.replace(/\.json$/i, "").replace(/^lca-view-/, ""), shared);
      }
      setUndo({
        message: `View loaded from ${file.name}: its settings, rules and methods${sameModel ? "" : " — saved from another model: expansions, pins, moved rows, merges and names were not restored"}.`,
      });
      setRoot(target);
      setAmount(saved.amount ?? 1);
      // A share is saved as a fraction (version 2), "top n" as its count
      setCutoff(saved.cutoff === undefined ? DEFAULT_CUTOFF : saved.cutoffBasis === "top" || saved.version < 2 ? saved.cutoff : +(saved.cutoff * 100).toFixed(6));
      setCutoffBasis(saved.cutoffBasis || "parent");
      setOwnRules(sameModel ? (saved.rules || []).filter((r) => r.scope !== "*") : []);
      setExpanded(new Set(sameModel ? saved.expanded : [String(target.index)]));
      setPinned(new Set(sameModel ? saved.pinned || [] : []));
      setOthers(new Set(sameModel ? saved.others || [] : []));
      setMerges(sameModel ? saved.merges || [] : []);
      setUngrouped(new Set(sameModel ? saved.ungrouped || [] : []));
      setSubstitutions(sameModel ? saved.substitutions || {} : {});
      setLabels(sameModel ? saved.labels || {} : {});
      if (saved.display) {
        // Views saved before the columns menu have a Comments switch
        const { comments, ...display } = saved.display;
        setView(oneGrouping(withOrganize({ ...DEFAULT_VIEW, recipe: display.recipe || view.recipe, ...pick(display, Object.keys(DEFAULT_VIEW)),
          ...(!display.columns && comments ? { columns: [...DEFAULT_COLUMNS, "comment"] } : {}) }, display)));
      }
      setSelection(new Set());
      setError(null);
    } catch (e) {
      setError(`Could not load the view: ${e.message}`);
    }
  }

  // Enter or a double-click on a row of the tree (Ctrl/⌘: in a new tab): a process becomes the process of the tab, a flow
  // opens in a flow tab (a process tab cannot show it); a bundle has nothing to use. True when it was used
  function useRow(row, inTab = false) {
    if (row.kind === "process" && row.meta) {
      if (inTab) { if (embedded) embedded.onUse(row.meta, true); else onOpenTab(id, row.meta); }
      else if (embedded) embedded.onUse(row.meta);
      else if (row.meta.index !== root?.index) { closeInfo(); selectRoot(row.meta); }
      return true;
    }
    if (row.kind === "flow" && row.flow && tabUse) { tabUse.flow({ flow: row.flow.index }, inTab); return true; }
    return false;
  }
  // Keys on the selected row (see docs/ui.md); returns true when the key was used
  function onTreeKey(e) {
    const rows = data.rows;
    if (visible && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "z") return undoLast();
    const inTab = (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key === "Enter";  // the process in a new tab
    if (!visible || !rows.length || ((e.ctrlKey || e.metaKey) && !inTab) || e.altKey) return false;
    const typed = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    // The tree view with the root on top: ←/→ go across the processes (the siblings), and ↑/↓ collapse/expand (↓ goes
    // on to the first child, ↑ to the parent). They are handled as the keys of the list, which expands with → and
    // collapses with ←, and moves with ↓/↑.
    const vertical = display === "graph" && orientation === "vertical";
    const across = vertical && (typed === "ArrowLeft" || typed === "ArrowRight");
    const key = !vertical ? typed
      : across ? (typed === "ArrowRight" ? "ArrowDown" : "ArrowUp")
        : typed === "ArrowDown" ? "ArrowRight" : typed === "ArrowUp" ? "ArrowLeft" : typed;
    // t: a new tab like this one; w: close this tab (the tabs of the workspace, not the pop-ups')
    if ((key === "t" || key === "w") && !e.shiftKey) {
      if (embedded) return false;
      if (key === "t") onNewTab(id); else onCloseTab(id);
      return true;
    }
    const found = rows.findIndex((r) => r.id === cursor.current.id);
    const row = rows[found >= 0 ? found : Math.min(cursor.current.index, rows.length - 1)];
    const moveTo = (r) => {
      cursor.current = { id: r.id, index: rows.indexOf(r) };
      anchor.current = r.id;
      setSelection(new Set([r.id]));
      closeInfo();
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
      case "ArrowDown":
      case "ArrowUp": {
        const next = across
          ? (() => { const siblings = rows.filter((r) => r.parent === row.parent); return siblings[siblings.indexOf(row) + (key === "ArrowDown" ? 1 : -1)]; })()
          : rows[found + (key === "ArrowDown" ? 1 : -1)];
        if (next) moveTo(next);
        else if (key === "ArrowUp" && !embedded) {  // above the top row: the panel selection (Process, Used by…)
          setSelection(new Set());
          treeSection.current?.closest(".docked-panels")?.querySelector(".info-tabs button.active")?.focus();
        }
        return true;
      }
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
      case " ":
      case "i": {
        // Space: the row's pop-up (the activity); i: its exchange (info: comment, uncertainty)
        if (infoTarget) { closeInfo(); return true; }
        const el = treeSection.current?.querySelector(`[data-id="${CSS.escape(row.id)}"]`)?.getBoundingClientRect();
        showInfo(row, { preventDefault() {}, clientX: el ? el.left + 40 : 200, clientY: el ? el.bottom : 200 }, key === "i");
        return true;
      }
      case "o":
        // Inside an expanded others row, o pins (takes out of others) instead
        if (movableSelection.length && movableSelection.every(inOthers)) pin(movableSelection.map((r) => r.id));
        else if (movableSelection.length) { followCursor.current = true; moveToOthers(movableSelection.filter((r) => !inOthers(r)).map((r) => r.id)); }
        return true;
      case "m":
        if (editing) return true;
        followCursor.current = true;
        if (selectedRows.length === 1 && row.kind === "merged") unmerge(row);
        else if (canMerge) selectNew(mergeSelected());
        else followCursor.current = false;
        return true;
      case "r":
        if (!editing && selectedRows.length === 1) startRename(row);
        return true;
      case "e":
        // Edit mode: the form of the process (its exchanges, products, documentation)
        if (!editing || selectedRows.length !== 1 || row.kind !== "process" || !row.meta) return false;
        editor.openForm({ edit: { database: row.meta.database, code: row.meta.code } });
        return true;
      case "Enter":
        // The process becomes the process of the tab (a flow opens in a flow tab); with Ctrl/⌘ in a new tab instead
        if (selectedRows.length !== 1) return false;
        return useRow(row, inTab);
      case "g":
        followCursor.current = true;
        if (selectedRows.length === 1 && row.kind === "group" && ungroupable(row)) ungroup(row);
        else if (selectedRows.length === 1 && row.kind === "process") {
          // One process: its product group back if it was ungrouped; else its same-named siblings grouped
          const same = rows.filter((r) => r.kind === "process" && r.parent === row.parent && productNameOf(r) === productNameOf(row));
          if (row.regroup) regroup(row.regroup);
          else if (same.length > 1) selectNew(groupRows(same));
          else followCursor.current = false;
        }
        else if (canGroup) selectNew(groupSelected());
        else followCursor.current = false;
        return true;
      default:
        return false;
    }
  }
  const treeKey = useRef(onTreeKey);
  treeKey.current = onTreeKey;
  // Embedded: the pop-up asks this panel first for its keys. ↑ on the top row unselects it, and with no row selected ←/→
  // (and ↑) are the pop-up's: they switch its panel
  if (embedded) {
    embedded.keys.current = (e) => {
      if (e.key === "ArrowUp" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (!selection.size) return false;
        if (cursor.current.id === data.rows[0]?.id) { setSelection(new Set()); return true; }
      }
      return (e.key === "ArrowLeft" || e.key === "ArrowRight") && !selection.size ? false : treeKey.current(e);
    };
  }
  useEffect(() => {
    if (embedded) return;
    const onKey = (e) => {
      // A pop-up that has the focus has its own keys
      // (and a row of a panel of the tab's card: the card's keys, ``dockedKeys``)
      if (keyboardOwner !== id || e.target.closest?.("input, textarea, select, [contenteditable], .info-popup, .docked-panels > .info-tabs, .docked-panels > .info-body")) return;
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
  // A double-click (Enter) on a row of its lists and pop-ups: a process becomes the process of the tab; a flow or a
  // model opens in a new tab (a process tab cannot show it), with this tab's process as the flow's scope
  const tabUse = embedded ? outerUse : {
    process: (index, inTab) => activityOf(index, method).then((found) => {
      closeInfo();
      if (inTab) onOpenTab(id, found);
      else if (found.index !== root?.index) selectRoot(found);
    }).catch((e) => setError(e.message)),
    flow: (target) => {
      closeInfo();
      onOpenTab(id, { ...target, scope: root ? { index: root.index, name: root.name, location: root.location, amount } : null });
    },
    model: (m) => { closeInfo(); onOpenTab(id, { model: { method: m } }); },
  };
  // One pop-up: of a bundle (others, merged, group), an elementary flow, or a process
  function renderPopup(p) {
    const t = p.target;
    const bundle = t.bundle ? findRow(t.bundle) : null;
    const follow = (next) => (embedded ? handOver(next) : stack.follow(p.key, next));
    const common = { key: p.key, target: t, win: stack.win(p), onClose: () => closeInfo(p.key), onBack: stack.back(p) };
    if (bundle?.kind === "rest") return html`<${OthersPopup} ...${common} rest=${bundle} rows=${data.rows} rootImpact=${rootImpact}
        cutoff=${cutoff} cutoffBasis=${cutoffBasis} pinned=${pinned} setPinned=${setPinned} others=${others} setOthers=${setOthers} view=${view}
        onMember=${(r) => { const next = infoTargetOf(r); if (next) follow(next); }} />`;
    if (bundle?.parts) return html`<${BundlePopup} ...${common} row=${bundle} methodInfo=${methodInfo} rootImpact=${rootImpact}
        actions=${bundle.kind === "merged"
          ? !editing && html`<button onClick=${() => unmerge(bundle)} title="Split back into the original processes">Unmerge</button>`
          : html`
            ${bundle.products && !editing && html`<button onClick=${() => mergeRows([bundle])} title="Merge into one process whose inventory is the union of the members'">Merge</button>`}
            ${ungroupable(bundle) && html`<button onClick=${() => ungroup(bundle)}>Ungroup</button>`}
            ${groupLocked(bundle) && html`<button disabled title=${GROUP_LOCKED}>Ungroup</button>`}`}
        onPart=${(part) => follow(part.flow !== undefined
          ? { flow: part.flow, activities: (bundle.emitters || []).map((m) => m.index) }
          : { index: part.index, row: null, parents: suppliedTo(bundle, rowsById) })} />`;
    if (t.bundle) return null;
    return objectPopup(p, { stack, model: info, method, chartMethods, follow,
      scope: root && !embedded ? { index: root.index, name: root.name, location: root.location, amount } : t.scope || null,
      onOpenFlowTab: embedded ? null : (f, place) => onOpenTab(id, f, place),
      onOpenModelTab: embedded ? null : (m, place) => onOpenTab(id, m, place),
      process: { rowsById, rootImpact, root, amount, onUseAsRoot: selectRoot, onOpenTab: (r, place) => onOpenTab(id, r, place), onGoTo: (index) => goTo(index) } });
  }
  // The options of the view: in their pop-up (View)
  const cutoffBare = html`
    <label><${NumberInput} class="cutoff" value=${cutoff} min=${cutoffBasis === "top" ? 1 : 0} max=${cutoffBasis === "top" ? undefined : 100}
      step=${cutoffBasis === "top" ? 1 : 0.5} onValue=${(v) => changeCutoff(v)} aria-label="Cut-off"
      title="Changing the cut-off resets pinned rows and rows moved to others" /> ${CUTOFF_MODES[cutoffBasis].unit}</label>
    <select class="cutoff-mode" aria-label="Cut-off mode" value=${cutoffBasis} title=${CUTOFF_MODES[cutoffBasis].hint}
      onChange=${(e) => changeCutoffMode(e.target.value)}>
      ${Object.entries(CUTOFF_MODES).map(([key, mode]) => html`<option value=${key} title=${mode.hint}>${mode.label}</option>`)}
    </select>`;
  const viewBar = html`<${ViewBar} view=${view} setView=${setDisplayView} fields=${info.group_by_fields}
    legend=${colorLegend(allData.rows, view)} onPick=${embedded ? undefined : lens.pickColor} onColours=${embedded ? undefined : lens.showColours} />`;
  // The options of the view, in a pop-up of their own (the button of the tab's row in the Processes panel, key v)
  const viewOptionsActions = html`
    <button class="icon-plain" disabled=${!root} onClick=${resetView} aria-label="Reset the view" title="Reset: the process alone, undoable"><${ResetIcon} /></button>
    <${SaveMenu} icon disabled=${!root} onSave=${exportView} onLoad=${loadView} onCopy=${copyView} onDefaults=${loadDefaults} />`;
  const viewOptions = html`
    <div class="view-options">
      <div class="view-group" role="group" aria-label="Display">
        <span class="view-group-label">Display</span>
        <span class="segmented">
          ${[["list", "List", "Table"], ["graph", "Tree", "Boxes"], ["sankey", "Sankey", "Bands"]].map(([k, label, hint]) => html`
            <button class=${display === k ? "primary" : ""} title=${hint} onClick=${() => setDisplay(k)}>${label}</button>`)}
        </span>
      </div>
      <div class="view-group" role="group" aria-label="Cut-off">
        <span class="view-group-label">Cut-off</span>
        ${cutoffBare}
      </div>
      ${viewBar}
    </div>`;
  // The tree, the first panel of the process of the tab (its pop-up's panels are the others)
  const treeNode = html`
    <section class="tree-section" ref=${treeSection} onMouseDown=${() => { if (!embedded) keyboardOwner = id; }}>
          ${undo && html`
            <div class="notice" role="status">
              <span>${undo.message}</span>
              <button onClick=${() => setUndo(null)} aria-label="Dismiss">×</button>
            </div>`}
          ${error && html`<div class="error">${error}</div>`}
          ${display === "list"
            ? html`<${Tree} rows=${data.rows} rootImpact=${rootImpact} shareBasis=${shareBasisOf(cutoffBasis)} selection=${selection} onToggle=${toggle} onSelect=${select}
                renaming=${renaming} onStartRename=${startRename} onRename=${rename}
                onUngroup=${ungroup} onRegroup=${regroup} ungroupable=${ungroupable} groupLocked=${groupLocked} onMergeGroup=${(r) => mergeRows([r])} onUnpin=${unpin} onPin=${pinSelected} onMoveToOthers=${moveSelectedToOthers} onInfo=${showInfo} onUse=${useRow} onUnmerge=${unmerge} onRevert=${revertInput} view=${view} focus=${focus}
                onColumns=${(columns) => setView({ ...view, columns })} />`
            : html`<${TreeGraph} rows=${data.rows} rootImpact=${rootImpact} shareBasis=${shareBasisOf(cutoffBasis)} selection=${selection} onToggle=${toggle} onSelect=${select}
                onUngroup=${ungroup} onRegroup=${regroup} ungroupable=${ungroupable} groupLocked=${groupLocked} onMergeGroup=${(r) => mergeRows([r])} onUnpin=${unpin} onPin=${pinSelected} onMoveToOthers=${moveSelectedToOthers} onInfo=${showInfo} onUse=${useRow} onUnmerge=${unmerge} view=${view}
                orientation=${orientation} setOrientation=${setOrientation} cycles=${cycles} setCycles=${setCycles} sankey=${display === "sankey"} focus=${focus} />`}
          ${selection.size > 1 && html`
            <div class="selection-bar">
              <span>${selection.size} selected · o or × moves them to others</span>
              <input placeholder="group name" value=${groupName} onInput=${(e) => setGroupName(e.target.value)} aria-label="New group name" />
              <button disabled=${!canGroup} onClick=${groupSelected}
                title="Bundle the selected sibling rows under one row; expanding it shows them">Group</button>
              ${!editing && html`<button disabled=${!canMerge} onClick=${mergeSelected}
                title="Combine the selected sibling processes (or the processes of a selected group) into one process: inventories added, common inputs summed">Merge</button>`}
              <button onClick=${() => setSelection(new Set())} aria-label="Clear selection">×</button>
            </div>`}
          <div class="toolbar"><span class="hint">
            ${root ? `Total: ${fmt(rootImpact)} ${methodInfo.unit}` : ""}
          </span>
            ${selectedTotals.count > 0 && html`
              <span class="spacer"></span>
              <span class="hint selected-total" title="Selected processes, merged processes and groups of processes: a row inside another selected one is not counted again">
                Selected (${selectedTotals.count} ${selectedTotals.count === 1 ? "process" : "processes"}): ${selectedTotals.amounts} · impact ${fmt(selectedTotals.impact)} ${methodInfo.unit}
              </span>`}
            ${!embedded && allData.rows.length > 0 && html`
              <span class="spacer"></span>
              <${TreeExport} data=${allData} info=${info} method=${method} view=${view} />`}
          </div>
        </section>`;
  // What the card of the tab has more than a pop-up (see InfoPopup's ``card``; the tree's export is at the foot of its
  // Explore panel, above): the multi indicator chart and table in Characterisation, and the Contribution panel (what
  // the side panel of View mode held: the process, flow, uncharacterised and rules contributions of the tab's process
  // and amount)
  const openProcessInfo = (index, x, y) => openInfo({ index, row: null, x, y });
  const contributionPanel = root && {
    chart: () => html`<${ProcessContributions} info=${info} root=${root} amount=${amount} method=${method} onInfo=${openProcessInfo} />`,
    flows: () => html`<${FlowContribution} info=${info} root=${root} amount=${amount} method=${method}
      onLocate=${goTo} onInfo=${openProcessInfo} onFlowInfo=${showFlowInfo} rules=${rules} setRules=${setRules} />`,
    missing: () => html`<${UncharacterisedPanel} info=${info} root=${root} amount=${amount} method=${method}
      chartMethods=${chartMethods} onFlowInfo=${showFlowInfo} />`,
    global: () => html`<${GlobalPanel} info=${info} root=${root} amount=${amount} method=${method} rules=${rules} view=${view}
      rule=${lens.attribution} setRule=${lens.setAttribution} onLocate=${goTo} onInfo=${openProcessInfo} />`,
  }[contributionTab];
  const cardPanels = !embedded && {
    characterisation: html`<${MultiIndicator} key=${editing ? "edit" : "view"} data=${allData} info=${info} chartMethods=${chartMethods}
      view=${view} onPickMethods=${() => lens.show("methods")} />`,
    contribution: contributionPanel && html`<${ContributionPanels} tab=${contributionTab} setTab=${setContributionTab} panel=${contributionPanel()} />`,
  };
  return html`
    <${TabUseContext.Provider} value=${tabUse}>
    <div class=${`fu-view ${embedded ? "embedded" : ""}`} ref=${box} style=${{ display: visible ? "" : "none" }}>
      ${!embedded && root
        ? html`<${InfoPopup} docked active=${visible} treeSlot=${treeNode} target=${{ index: root.index, row: null }} win=${{}} model=${info}
            method=${method} methodInfo=${methodInfo} rootImpact=${rootImpact} rowsById=${rowsById} onClose=${() => {}} onBack=${null}
            onUseAsRoot=${selectRoot} onOpenTab=${(r, place) => onOpenTab(id, r, place)} root=${root} amount=${amount}
            onFollow=${(next) => openInfo(next)} onGoTo=${(index) => goTo(index)}
            onDown=${() => {  // ↓ on the panel selection: back to the tree, on its top row
              keyboardOwner = id;
              document.activeElement?.blur();
              const top = data.rows[0];
              if (top) { cursor.current = { id: top.id, index: 0 }; anchor.current = top.id; setSelection(new Set([top.id])); }
            }} card=${cardPanels} />`
        : treeNode}
      ${popups.map(renderPopup)}
      ${!embedded && optionsPop && html`<${PagePortal}>
        <${ViewOptionsPopup} target=${optionsPop} title=${root ? `${root.name} (${root.location})` : "No process yet"}
          win=${{ rect: optionsPop.rect, pinned: optionsPop.pinned,
            onPin: () => setOptionsPop((p) => p && { ...p, pinned: !p.pinned }), onRect: (rect) => setOptionsPop((p) => p && { ...p, rect }) }}
          actions=${viewOptionsActions} onClose=${() => setOptionsPop(null)}>${viewOptions}<//>
      <//>`}
      ${!embedded && html`<${Portal} slot=${`groups-${id}`}>
        <${GroupPanel} info=${info} rules=${rules} setRules=${setRules} view=${view} setView=${setView}
          selectedRow=${selectedRows.length === 1 ? selectedRows[0] : null}
          shared=${{ set: lens.set }} extras=${ruleExtras(lens)} />
      <//>`}
    </div>
    <//>`;
}

// ---------------------------------------------------------------- comparison mode
// The processes of several tabs side by side: their contribution by group (bars), what
// makes the difference between two of them (waterfall) and every impact category at once (heat
// map). All three come from /api/compare, computed on the matrices: the cut-off, expansion,
// merges and substitutions of a tab do not enter into it, only its process and amount.
const COMPARE_PANELS = [["groups", "Compare contributions"], ["difference", "Cascade"], ["methods", "Multi indicator matrix"]];
// What the bars of the first two panels are split into: the groups (rules and quick groups), the rows of each
// tab's displayed tree (View mode), or the elementary flows
const compareColor = (i) => PALETTE[i % PALETTE.length];
const OTHER_GROUP = "other";
const TOTAL_BAR = "Total";  // the one bar of a tab with no breakdown
const relative = (value, base) => (base ? (value - base) / Math.abs(base) : value ? null : 0);
const signed = (x) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${fmt(Math.abs(x))}`;
const signedPct = (r) => (r == null ? "n/a" : `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r * 100).toFixed(Math.abs(r) < 0.1 ? 1 : 0)} %`);

// Names of the compared tabs: what tells them apart (the place when they share the process, the
// process when they share the place), with the amount when it is not 1, and a number when two tabs
// would still read the same. ``full`` always has the process and the place; ``common`` is the name
// every tab shares, if there is one.
// Each tab named by the process format of the Name display (``processNames``): the main part, the qualifier in brackets
function compareLabels(tabs) {
  const names = tabs.map((t) => processNames(t.state.root));
  const both = (n) => (n.qual ? `${n.main} (${n.qual})` : n.main);
  const sameName = names.length > 1 && names.every((n) => n.main === names[0].main);
  const sameLocation = names.length > 1 && names.every((n) => n.qual === names[0].qual);
  const amountOf = (t) => (t.state.amount !== 1 ? ` × ${fmtAmount(t.state.amount)}` : "");
  const base = tabs.map((t, i) => (sameName && names[i].qual ? names[i].qual : sameLocation && !sameName ? names[i].main : both(names[i])) + amountOf(t));
  const seen = new Map();
  const numbered = (list, i) => {
    const n = (seen.get(list[i]) || 0) + 1;
    seen.set(list[i], n);
    return list.filter((b) => b === list[i]).length > 1 ? `${list[i]} #${n}` : list[i];
  };
  const short = Object.fromEntries(tabs.map((t, i) => [t.id, numbered(base, i)]));
  const roots = tabs.map((t) => t.state.root);
  const full = Object.fromEntries(tabs.map((t, i) => [t.id, `${both(names[i])}${amountOf(t)}${names[i].main !== roots[i].name ? ` · ${roots[i].name}` : ""}`]));
  return { short, full, common: sameName ? names[0].main : null };
}

// The tree a tab displays, as /api/rows is asked for it (the method is the comparison's)
const treeBody = (s, method, attribution) => ({
  root: [s.root.index], amount: s.amount, expanded: s.expanded || [], rules: [...(s.groupByRules === false ? [] : s.rules || []), ...flowRulesOf(s)], cutoff: cutoffValue(s.cutoff, s.cutoffBasis),
  cutoff_basis: s.cutoffBasis || "parent", method, methods: s.chartMethods || [], pinned: s.pinned || [], others: s.others || [], merges: s.merges || [],
  ungrouped: s.ungrouped || [], substitutions: s.substitutions || {}, group_products: !!s.groupProducts, collapse_singletons: !!s.collapseSingletons,
  colour_rules: s.rules || [],
  attribution,
});

// A bar of a tab's tree: what it stands for (the same in every tab, so that bars line up) and its name. A process counts
// wherever it is in the tree; its direct emissions, flows and others row go with it. The process of each tab is
// one and the same thing, whatever process it is: that is what is compared.
function segmentOf(r, byId, rootIndex) {
  const parent = byId.get(r.parent);
  const ref = (meta) => (meta?.index === rootIndex ? "root" : `p${meta?.index}`);
  const named = (meta) => (meta ? (meta.index === rootIndex ? "the process" : `${meta.name} (${meta.location})`) : "?");
  const [parentKey, parentName] = parent ? segmentOf(parent, byId, rootIndex) : ["", ""];
  switch (r.kind) {
    case "process": return [ref(r.meta), r.custom || named(r.meta)];
    case "direct": return [r.meta ? `d${ref(r.meta)}` : `d@${parentKey}`, `direct: ${r.meta ? named(r.meta) : parentName}`];
    case "flow": return [`f${r.flow.index}@${parentKey}`, `${r.custom || r.label} · ${parentName}`];
    case "rest": return [`r@${parentKey}`, `others under ${parentName}`];
    default: return [`${r.kind}:${r.label}`, r.custom || r.label];  // group, merged
  }
}

// The bars (frontier) of each tab's displayed tree, aligned by what they stand for: the entries of /api/compare with
// ``groups`` and ``total`` replaced
function treeEntries(entries, rowsOf, roots) {
  const perTab = rowsOf.map((rows, i) => {
    const byId = new Map(rows.map((r) => [r.id, r]));
    const parts = new Map();
    for (const r of frontier(rows)) {
      const [key, label] = segmentOf(r, byId, roots[i]);
      let ruled = r;
      while (ruled && !ruled.rule) ruled = byId.get(ruled.parent);
      parts.set(key, { label, impact: (parts.get(key)?.impact || 0) + r.impacts[0], rule: parts.get(key)?.rule ?? ruled?.rule });
    }
    return parts;
  });
  const names = new Map(), taken = new Set();
  for (const parts of perTab) {
    for (const [key, { label }] of parts) {
      if (names.has(key)) continue;
      let name = label, n = 2;
      while (taken.has(name)) name = `${label} #${n++}`;
      names.set(key, name);
      taken.add(name);
    }
  }
  return entries.map((e, i) => {
    const groups = {}, rules = {};
    for (const [key, { impact, rule }] of perTab[i]) {
      groups[names.get(key)] = impact;
      if (rule) rules[names.get(key)] = rule;
    }
    return { ...e, groups, rules, total: Object.values(groups).reduce((s, v) => s + v, 0) };
  });
}

// The breakdown of the bars: by rules, by flows, or by processes (the rows of each tab's view, or what each process
// contributes by its own emissions). One at a time.
function CompareBasis({ basis, setBasis, cut, setCut, none }) {
  return html`
    <div class="view-group" role="group" aria-label="Breakdown">
      <span class="view-group-label">Breakdown</span>
      <span class="segmented">
        ${[...(none ? [["none", "None", "Total only"]] : []), ["groups", "Rules", "By rule"], ["flows", "Flows", "By elementary flow"],
          ["tree", "Explore", "Rows of each tab's view"], ["activities", "Each content", "Own emissions and each input, first tier only"]].map(([k, label, hint]) => html`
          <button class=${basis === k ? "primary" : ""} onClick=${() => setBasis(k)} title=${hint}>${label}</button>`)}
      </span>
      ${(basis === "flows" || basis === "activities") && html`<${CutField} cut=${cut} setCut=${setCut} what="bars" />`}
    </div>`;
}

// The steps from one total to another: the first total, what each group adds or removes (largest
// first), the second total. A step floats between the running total before and after it.
function waterfallRows(from, to, names) {
  const scale = Math.max(Math.abs(from.total), Math.abs(to.total));
  const steps = names.map((name) => ({ name, delta: (to.groups[name] || 0) - (from.groups[name] || 0) }))
    .filter((s) => Math.abs(s.delta) > scale * 1e-9)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  const total = (r) => ({ name: r.label, lo: Math.min(0, r.total), hi: Math.max(0, r.total), total: r.total, color: r.color });
  const rows = [total(from)];
  let running = from.total;
  for (const s of steps) {
    rows.push({ name: s.name, lo: Math.min(running, running + s.delta), hi: Math.max(running, running + s.delta), delta: s.delta });
    running += s.delta;
  }
  rows.push(total(to));
  return rows;
}

function CompareBars({ results, base, unitLabel, names, groupColor, mode, setMode, basis, setBasis, cut, setCut }) {
  const noun = { none: "Total", groups: "Group", tree: "Row", flows: "Flow", activities: "Content" }[basis];
  const baseTotal = Math.abs(results[base].total);
  const chartRef = useRef(null);
  const value = (r, g) => {
    const v = r.groups[g] || 0;
    if (mode === "baseline") return baseTotal ? (100 * v) / baseTotal : 0;
    if (mode === "own") return r.total ? (100 * v) / Math.abs(r.total) : 0;
    return v;
  };
  const option = useMemo(() => {
    const o = baseOption();
    o.grid = { ...o.grid, top: 12, bottom: 56, right: 64 };
    o.legend = { type: "scroll", bottom: 0, textStyle: { color: cssVar("--text") } };
    o.yAxis = { ...o.yAxis, type: "category", inverse: true, data: results.map((r) => r.label),
      axisLabel: { color: cssVar("--text"), width: 240, overflow: "truncate" } };
    o.xAxis = { ...o.xAxis, type: "value", name: mode === "absolute" ? unitLabel : "%",
      axisLabel: { color: cssVar("--muted"), formatter: mode === "absolute" ? fmtAxis : (v) => `${v}%` } };
    o.tooltip.formatter = (d) => {
      const r = results[d.dataIndex], v = r.groups[d.seriesName] || 0;
      return `${r.label}<br/>${d.marker}${d.seriesName}: <b>${fmt(v)} ${unitLabel}</b> (${pct(r.total ? v / r.total : 0)} of ${fmt(r.total)})`;
    };
    o.series = names.map((g) => ({ type: "bar", stack: "total", name: g, itemStyle: { color: groupColor(g) }, data: results.map((r) => value(r, g)) }));
    return o;
  }, [results, names, mode, unitLabel]);
  const exportCsv = () => downloadCsv(`comparison-${basis}.csv`, [noun.toLowerCase(), ...results.flatMap((r) => [r.label, `${r.label}: share`])],
    [...names, "total"].map((g) => [g, ...results.flatMap((r) => {
      const v = g === "total" ? r.total : r.groups[g] || 0;
      return [v, r.total ? v / r.total : 0];
    })]), [unitLabel]);
  return html`
    <div class="row">
      <${CompareBasis} none basis=${basis} setBasis=${setBasis} cut=${cut} setCut=${setCut} />
      <span style="flex:1"></span>
      <${ExportMenu} chart=${chartRef} csv=${exportCsv} name="compare-contributions" />
    </div>
    <div class="view-group" role="group" aria-label="Scale">
      <span class="view-group-label">Scale</span>
      <span class="segmented">
        ${[["absolute", unitLabel, "Impact"], ["baseline", "% of baseline", "Share of the baseline"], ["own", "Each 100 %", "Own composition"]].map(([key, label, hint]) => html`
          <button class=${mode === key ? "primary" : ""} title=${hint} onClick=${() => setMode(key)}>${label}</button>`)}
      </span>
    </div>
    <${Chart} exportRef=${chartRef} option=${option} height=${Math.max(200, results.length * 54 + 90)} />
    <table class="results compare-table">
      <thead>
        <tr><th rowspan="2">${noun} <span class="muted">· ${unitLabel}</span></th>
          ${results.map((r) => html`<th class="tab-head" colspan="2" title=${r.label}><span class="dot" style=${{ background: r.color }}></span>${truncate(r.label, 28)}</th>`)}</tr>
        <tr>${results.map(() => html`<th class="num">${unitLabel}</th><th class="num">share</th>`)}</tr>
      </thead>
      <tbody>${names.map((g) => html`
        <tr key=${g}><td><span class="dot" style=${{ background: groupColor(g) }}></span>${g}</td>
          ${results.map((r) => g in r.groups
            ? html`<td class="num">${fmt(r.groups[g])}</td><td class="num muted">${pct(r.total ? r.groups[g] / r.total : 0)}</td>`
            : html`<td class="num muted">–</td><td></td>`)}</tr>`)}
      </tbody>
      <tfoot>
        <tr class="total"><th>Total</th>${results.map((r) => html`<td class="num">${fmt(r.total)}</td><td class="num muted">${r.total ? "100.0%" : ""}</td>`)}</tr>
        <tr><td class="muted">vs baseline</td>${results.map((r, i) => html`<td class="num muted" colspan="2">${i === base ? "baseline" : signedPct(relative(r.total, results[base].total))}</td>`)}</tr>
      </tfoot>
    </table>`;
}

function CompareDifference({ results, base, target, setTarget, unitLabel, names, basis, setBasis, cut, setCut }) {
  const noun = { groups: "Group", tree: "Row", flows: "Flow", activities: "Content" }[basis];
  const from = results[base], to = results[target];
  const chartRef = useRef(null);
  const rows = useMemo(() => waterfallRows(from, to, names), [from, to, names]);
  // A small difference is invisible against two totals drawn from zero: the axis zooms on the steps (the totals are cut)
  const steps = rows.slice(1, -1);
  const zoomed = steps.length > 0 && Math.abs(to.total - from.total) < 0.25 * Math.max(Math.abs(from.total), Math.abs(to.total));
  const option = useMemo(() => {
    const o = baseOption();
    const muted = cssVar("--muted"), up = cssVar("--bar-negative"), down = cssVar("--bar");
    o.grid = { ...o.grid, top: 12, bottom: 28, right: 90 };
    o.yAxis = { ...o.yAxis, type: "category", inverse: true, data: rows.map((r) => r.name),
      axisLabel: { color: cssVar("--text"), width: 240, overflow: "truncate" } };
    o.xAxis = { ...o.xAxis, type: "value", name: unitLabel };
    if (zoomed) {
      const lo = Math.min(...steps.map((s) => s.lo)), hi = Math.max(...steps.map((s) => s.hi)), pad = (hi - lo) * 0.3 || Math.abs(hi) * 0.1;
      o.xAxis = { ...o.xAxis, min: lo - pad, max: hi + pad,
        axisLabel: { color: muted, formatter: fmtAxis } };
    }
    o.tooltip.formatter = (d) => {
      const r = rows[d.dataIndex];
      if (r.total !== undefined) return `${r.name}<br/><b>${fmt(r.total)} ${unitLabel}</b>`;
      const change = to.total - from.total;
      return `${r.name}<br/><b>${signed(r.delta)} ${unitLabel}</b>${change ? ` (${pct(r.delta / change)} of the difference)` : ""}`;
    };
    o.series = [{
      type: "custom", encode: { x: [1, 2], y: 0 },
      data: rows.map((r, i) => ({ value: [i, r.lo, r.hi], itemStyle: { color: r.total !== undefined ? muted : r.delta > 0 ? up : down } })),
      renderItem: (params, api) => {
        const r = rows[api.value(0)], area = params.coordSys;
        const a = api.coord([api.value(1), api.value(0)]), b = api.coord([api.value(2), api.value(0)]);
        const height = api.size([0, 1])[1] * 0.6;
        const x = Math.max(Math.min(a[0], b[0]), area.x), end = Math.min(Math.max(a[0], b[0]), area.x + area.width);
        return { type: "group", children: [
          { type: "rect", shape: { x, y: a[1] - height / 2, width: Math.max(end - x, 1), height }, style: api.style() },
          { type: "text", style: { text: r.total !== undefined ? fmt(r.total) : signed(r.delta), x: end + 6, y: a[1], textVerticalAlign: "middle", fill: muted, fontSize: 12 } },
        ] };
      },
    }];
    return o;
  }, [rows, unitLabel, zoomed]);
  const change = to.total - from.total;
  const lead = steps[0];
  const exportCsv = () => downloadCsv("comparison-difference.csv", [noun.toLowerCase(), from.label, to.label, "difference"],
    [...steps.map((s) => [s.name, from.groups[s.name] || 0, to.groups[s.name] || 0, s.delta]), ["total", from.total, to.total, change]], [unitLabel]);
  return html`
    <div class="row">
      <${CompareBasis} basis=${basis} setBasis=${setBasis} cut=${cut} setCut=${setCut} />
      <span style="flex:1"></span>
      <${ExportMenu} chart=${chartRef} csv=${exportCsv} name="cascade" />
    </div>
    ${results.length > 2 && html`<div class="row">
      <select value=${target} onChange=${(e) => setTarget(Number(e.target.value))} aria-label="Compared with the baseline" title="The tab read against the baseline">
        ${results.map((r, i) => i !== base && html`<option value=${i}>${r.label}</option>`)}
      </select>
    </div>`}
    <p class="note">
      <b>${truncate(to.label, 50)}</b> is <b>${signedPct(relative(to.total, from.total))}</b> (${signed(change)} ${unitLabel}) against the baseline${lead
        ? html`; the largest step is <b>${lead.name}</b> (${signed(lead.delta)} ${unitLabel})` : ""}.
      Orange steps add to the score, green ones take away from it.
      ${basis === "groups" && names.length < 2 && " Add groups (left) to see which parts of the supply chain make the difference."}
    </p>
    <${Chart} exportRef=${chartRef} option=${option} height=${Math.max(200, rows.length * 34 + 60)} />
    <table class="results compare-table">
      <thead><tr><th>${noun}</th><th class="num" title=${from.label}>${truncate(from.label, 24)}</th><th class="num" title=${to.label}>${truncate(to.label, 24)}</th>
        <th class="num">Difference</th><th class="num">Of the total difference</th></tr></thead>
      <tbody>${steps.map((s) => html`
        <tr key=${s.name}><td>${s.name}</td><td class="num">${fmt(from.groups[s.name] || 0)}</td><td class="num">${fmt(to.groups[s.name] || 0)}</td>
          <td class="num">${signed(s.delta)}</td><td class="num muted">${change ? pct(s.delta / change) : ""}</td></tr>`)}
      </tbody>
      <tfoot><tr class="total"><th>Total</th><td class="num">${fmt(from.total)}</td><td class="num">${fmt(to.total)}</td><td class="num">${signed(change)}</td>
        <td class="num muted">${change ? "100.0%" : ""}</td></tr></tfoot>
    </table>`;
}

// The impact methods in one list nested by family: one box per method, ✓ shown together (the several methods chart, the
// *Impact categories* heat map), ★ the active method (tree, charts, comparison); a family box ticks the whole family
function MethodTree({ info, main, setMain, selected, setSelected, onCategory }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(() => new Set([familyKey(info.methods[main])]));
  const families = useMemo(() => {
    const map = new Map();
    info.methods.forEach((m, i) => map.set(familyKey(m), [...(map.get(familyKey(m)) || []), i]));
    return [...map];
  }, [info]);
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  const matches = (i) => !words.length || words.every((w) => info.methods[i].name.join(" ").toLowerCase().includes(w));
  const ticked = new Set(selected);
  const toggleOpen = (family) => setOpen((o) => { const next = new Set(o); next.has(family) ? next.delete(family) : next.add(family); return next; });
  // The family box ticks or unticks its methods but the main one, which stays ticked
  const tick = (indices, on) => setSelected(on ? [...new Set([...selected, ...indices])] : selected.filter((i) => i === main || !indices.includes(i)));
  // One mark per method, clicked in turn: not shown → ✓ shown together → ★ main → not shown. The main method is
  // always ticked; unticking it makes the first other ticked method the main one, so it needs another ticked
  const stateOf = (i) => (i === main ? "main" : ticked.has(i) ? "ticked" : "off");
  const others = selected.filter((i) => i !== main);
  const step = (i) => {
    const state = stateOf(i);
    if (state === "off") setSelected([...selected, i]);
    else if (state === "ticked") { setMain(i); if (!ticked.has(main)) setSelected([...selected, main]); }
    else if (others.length) {
      setMain([...others].sort((a, b) => a - b)[0]);
      setSelected(others);
    }
  };
  const MARKS = {
    off: ["", "Not shown: click to show it together with the others"],
    ticked: ["✓", "Shown together with the others: click to make it the main method"],
    main: ["★", "The main method (single indicator, tree, charts, comparison): click to untick it"],
  };
  const markTitle = (i) => (stateOf(i) === "main" && !others.length
    ? "The main method (single indicator, tree, charts, comparison): tick another method to untick this one" : MARKS[stateOf(i)][1]);
  return html`
    <input class="method-filter" placeholder="Filter the methods" value=${text} onInput=${(e) => setText(e.target.value)} aria-label="Filter the methods" />
    <div class="method-tree">
      ${families.map(([family, indices]) => {
        const shown = indices.filter(matches);
        if (!shown.length) return null;
        const expanded = open.has(family) || words.length > 0;
        const n = shown.filter((i) => ticked.has(i) || i === main).length;  // the main method counts as ticked
        return html`
          <div class="method-family" key=${family}>
            <div class="method-head">
              <button class="fold-caret" onClick=${() => toggleOpen(family)} aria-expanded=${expanded} aria-label=${`${expanded ? "Fold" : "Unfold"} ${family}`}>${expanded ? "▾" : "▸"}</button>
              <input type="checkbox" checked=${n === shown.length} ref=${(el) => el && (el.indeterminate = n > 0 && n < shown.length)}
                onChange=${(e) => tick(shown, e.target.checked)} aria-label=${`All of ${family}`} title="The whole family, shown together" />
              <span class="name" onClick=${() => toggleOpen(family)}>${family}</span>
              <span class="muted count">${n}/${shown.length}</span>
            </div>
            ${expanded && shown.map((i) => html`
              <div class=${`method-row ${stateOf(i)}`} key=${i}
                onContextMenu=${onCategory ? (e) => { e.preventDefault(); onCategory(i, e.clientX, e.clientY); } : undefined}>
                <button class=${`method-mark ${stateOf(i)}`} onClick=${() => step(i)} title=${markTitle(i)}
                  aria-label=${`${methodLabel(info.methods[i])}: ${{ off: "not shown", ticked: "shown together", main: "the main method" }[stateOf(i)]}`}>${MARKS[stateOf(i)][0]}</button>
                <span class="name" title=${`${methodLabel(info.methods[i])}
${markTitle(i)}`} onClick=${() => step(i)}>${methodParts(info.methods[i]).main}${methodParts(info.methods[i]).qual && html` <span class="muted">${methodParts(info.methods[i]).qual}</span>`}</span>
                ${onCategory && html`<button class="icon-plain category-open" onClick=${(e) => onCategory(i, e.clientX, e.clientY)}
                  aria-label=${`The model card of ${methodLabel(info.methods[i])}`} title="Its model card (or right-click the method): its elementary flows, their factors and emitters; compare with another method"><${CategoryIcon} /></button>`}
              </div>`)}
          </div>`;
      })}
    </div>`;
}

function CompareMethods({ results, base, info, methods, sorted, setSorted }) {
  const chartRef = useRef(null);
  const rows = useMemo(() => {
    const list = methods.map((k, j) => {
      const changes = results.map((r) => relative(r.totals[j], results[base].totals[j]));
      return { k, j, changes, spread: Math.max(0, ...changes.map((c, i) => (i === base || c == null ? 0 : Math.abs(c)))) };
    });
    return sorted ? [...list].sort((a, b) => b.spread - a.spread) : list;
  }, [results, base, methods, sorted]);
  const option = useMemo(() => {
    const o = baseOption();
    const clip = (c) => (c == null ? 0 : Math.max(-1, Math.min(1, c)));
    o.grid = { left: 8, right: 16, top: 70, bottom: 44, containLabel: true };
    o.xAxis = { type: "category", position: "top", data: results.map((r) => r.label), splitArea: { show: false },
      axisLabel: { color: cssVar("--text"), interval: 0, width: 150, overflow: "truncate" } };
    o.yAxis = { type: "category", inverse: true, data: rows.map((r) => methodRow(info.methods[r.k])),
      axisLabel: { color: cssVar("--text"), width: 320, overflow: "truncate" } };
    o.visualMap = { min: -1, max: 1, calculable: false, orient: "horizontal", left: "center", bottom: 0, itemWidth: 12, itemHeight: 160,
      textStyle: { color: cssVar("--muted") }, formatter: (v) => `${v > 0 ? "+" : ""}${Math.round(v * 100)} %`,
      inRange: { color: [cssVar("--bar"), cssVar("--panel"), cssVar("--bar-negative")] } };
    o.tooltip.formatter = (d) => {
      const [c, y] = d.value, row = rows[y], m = info.methods[row.k];
      const cell = (r, i) => `${i === c ? "<b>" : ""}${truncate(r.label, 40)}: ${fmt(r.totals[row.j])} ${m.unit}${i === base ? " (baseline)" : ` (${signedPct(row.changes[i])})`}${i === c ? "</b>" : ""}`;
      return `${methodLabel(m)}<br/>${results.map(cell).join("<br/>")}`;
    };
    o.series = [{
      type: "heatmap", itemStyle: { borderColor: cssVar("--panel"), borderWidth: 2 },
      data: rows.flatMap((row, y) => results.map((_, c) => [c, y, clip(row.changes[c])])),
      label: { show: true, color: cssVar("--text"), formatter: (d) => (d.value[0] === base ? "baseline" : signedPct(rows[d.value[1]].changes[d.value[0]])) },
    }];
    return o;
  }, [rows, results, base, info, nameStamp]);
  const exportCsv = () => downloadCsv("comparison-methods.csv", ["method", "unit", ...results.flatMap((r) => [r.label, `${r.label}: vs baseline`])],
    rows.map((row) => [methodLabel(info.methods[row.k]), info.methods[row.k].unit,
      ...results.flatMap((r, i) => [r.totals[row.j], i === base ? "" : row.changes[i] ?? ""])]));
  return html`
    <div class="row">
      <label><input type="checkbox" checked=${sorted} onChange=${(e) => setSorted(e.target.checked)} /> Largest difference first</label>
      <span style="flex:1"></span>
      <${ExportMenu} chart=${chartRef} csv=${exportCsv} name="multi-indicator-matrix" />
    </div>
    ${methods.length === 0
      ? html`<div class="empty">Tick the methods to show in the <i>Methods</i> panel, by family or one by one.</div>`
      : html`
        <${InfoTip} text="Each process's score in the chosen impact categories, as a change against the baseline: orange higher, green lower, to ±100 %. Values in the tooltip and the CSV." label="About the heat map" />
        <${Chart} exportRef=${chartRef} option=${option} height=${Math.max(240, rows.length * 28 + 130)} />`}`;
}

// The tabs of the comparison (``compare.chosen``, all by default) and the baseline (``compare.baseline``, the first by default)
function compareSelection(tabs, compare) {
  const { short: labels, full: fullLabels, common } = compareLabels(tabs);
  const chosen = (compare.chosen || tabs.map((t) => t.id)).map((id) => tabs.find((t) => t.id === id)).filter(Boolean);
  return { labels, fullLabels, common, chosen, baseline: chosen.find((t) => t.id === compare.baseline) || chosen[0] };
}

// The *Name display* of processes and flows (the top of the Processes panel), as the Methods panel has for the
// methods: a format for the processes and one for the flows, each a preset or Custom (the recipe, with tokens). One
// setting of the workspace (the lens), which the tree, the Sankey, the charts, the pop-ups and the titles follow.
// ``example``: a process to show it on (the first tab's)
const SAMPLE_FLOW = { name: "Carbon dioxide, fossil", compartment: "air", subcompartment: "urban air close to ground" };
function NameDisplay({ naming, setNaming, example }) {
  const choice = (key, presets, label) => html`
    <div class="view-group" role="group" aria-label=${`${label} names`}>
      <span class="view-group-label">${label}</span>
      <span class="segmented">
        ${Object.entries(presets).map(([k, preset]) => html`
          <button class=${naming[key] === k ? "primary" : ""} aria-pressed=${naming[key] === k} title=${preset.hint}
            onClick=${() => setNaming({ ...naming, [key]: k })}>${preset.label}</button>`)}
      </span>
    </div>`;
  const custom = [naming.process === "custom" && "process", naming.flow === "custom" && "flow"].filter(Boolean);
  const shown = (names) => html`<span class="example-main">${names.main}</span>${names.qual && html` <span class="muted">${names.qual}</span>`}`;
  return html`
    <${Fold} id="namedisplay" title="Name display" open=${false}
      actions=${html`<${InfoTip} text="How processes and flows are named in every tab: the tree, the Sankey, the charts, the pop-ups, the tab titles and this panel. A name you give a row (✎) still wins." label="About the names" />`}>
      <div class="name-display">
        ${choice("process", PROCESS_NAMINGS, "Processes")}
        ${choice("flow", FLOW_NAMINGS, "Flows")}
        ${custom.length > 0 && html`<${RecipeEditor} recipe=${naming.recipe} setRecipe=${(recipe) => setNaming({ ...naming, recipe })} kinds=${custom} />`}
        <p class="note">e.g. ${example && html`${shown(processNames(example, naming))} · `}${shown(flowTitleNames(SAMPLE_FLOW, naming))}</p>
      </div>
    <//>`;
}

// The compare selection's next step for tab ``id``, as the method boxes of the Methods panel: not compared → ✓ compared
// → ★ the baseline (the one before stays compared) → not compared, the first other compared tab becoming the baseline.
// The baseline alone cannot be unticked. Returns the patch of ``compare`` ({ chosen, baseline }).
function compareStep(tabs, compare, id) {
  const { chosen, baseline } = compareSelection(tabs, compare);
  const ids = chosen.map((t) => t.id);
  if (!ids.includes(id)) return { chosen: [...ids, id], baseline: baseline?.id ?? id };
  if (baseline?.id !== id) return { chosen: ids, baseline: id };
  const others = tabs.filter((t) => t.id !== id && ids.includes(t.id));
  return others.length ? { chosen: others.map((t) => t.id), baseline: others[0].id } : {};
}
// The one box of a tab in the Processes panel, drawn as a method's box (``.method-mark``): "off", "ticked" (✓ compared)
// or "main" (★ the baseline); ``alone``: the baseline is the only tab compared, so it cannot be unticked
function CompareMark({ state, alone, label, onStep }) {
  const TITLES = {
    off: "Not compared: click to compare it in Compare mode",
    ticked: "Compared: click to make it the baseline",
    main: alone ? "The baseline, the others are read against it: tick another tab to untick this one"
      : "The baseline, the others are read against it: click to untick it (the first other compared tab becomes the baseline)",
  };
  return html`<button class=${`method-mark compare-mark ${state}`} onClick=${onStep} title=${TITLES[state]}
    aria-label=${`${label}: ${{ off: "not compared", ticked: "compared", main: "the baseline" }[state]}`}>${{ off: "", ticked: "✓", main: "★" }[state]}</button>`;
}

// The tabs open (*Processes* of the left panel), grouped by window (the panes of the split): the ones to compare in
// Compare mode and the baseline (one box: ✓ and ★, see CompareMark) and a button to close the tab; a right-click opens the pop-up of the
// process. Drag a process to another window, before another one, or to the zone for a new window; drop a process from a
// row of the explorer or a panel to open it in a new tab. What each does is in its tooltip.
// ``windows``: { panes, current: the pane used last, select(pane, tab), focus(pane), close(tab), closePane(pane), drag(tab),
// move(tab, pane, zone, before), openProcess(index, pane, zone), reopen(at), history: [{ at, label }] of what was closed, dragging: tab }
function ProcessesPanel({ tabs, flowTabs = [], compare, setCompare, windows, naming, setNaming }) {
  const { labels, fullLabels, common, chosen, baseline } = compareSelection(tabs, compare);
  const [over, setOver] = useState(null);  // where a drop would go: { pane, before } or { fresh: true }
  const set = (patch) => setCompare((c) => ({ ...c, ...patch }));
  // The tab shown in its window first (a hidden tab has no pop-up to see), then its pop-up
  const showTab = (pane, t) => { windows.select(pane.id, t.id); windows.focus(pane.id); };
  const openPopup = (e, pane, t) => {
    e.preventDefault();
    const { clientX: x, clientY: y } = e;
    showTab(pane, t);
    setTimeout(() => POPUP_OPENERS[t.id]?.(x, y), 60);
  };
  // The view options of the tab: the tab shown first, as a click on its name, then its pop-up beside the button
  const openViewOptions = (e, pane, t) => {
    const r = e.currentTarget.getBoundingClientRect();
    showTab(pane, t);
    VIEW_OPTIONS_OPENERS[t.id]?.(r.right, r.top - 8);
  };
  const lastPane = windows.panes[windows.panes.length - 1];
  const dragging = windows.dragging || processDrag;
  // Drop on a window (``before``: ahead of a tab of it): a tab moves there, a process opens there
  const target = (pane, before = null) => ({
    onDragOver: (e) => { if (!dragging) return; e.preventDefault(); e.stopPropagation(); if (over?.pane !== pane.id || over?.before !== before) setOver({ pane: pane.id, before }); },
    onDrop: (e) => {
      if (!dragging) return;
      e.preventDefault(); e.stopPropagation(); setOver(null);
      if (processDrag) { const { index } = processDrag; processDrag = null; windows.openProcess(index, pane.id, "center"); }
      else windows.move(windows.dragging, pane.id, "center", before);
    },
  });
  const fresh = {
    onDragOver: (e) => { if (!dragging) return; e.preventDefault(); e.stopPropagation(); setOver({ fresh: true }); },
    onDragLeave: () => setOver(null),
    onDrop: (e) => {
      if (!dragging) return;
      e.preventDefault(); e.stopPropagation(); setOver(null);
      if (processDrag) { const { index } = processDrag; processDrag = null; windows.openProcess(index, lastPane.id, "right"); }
      else windows.move(windows.dragging, lastPane.id, "right", null);
    },
  };
  const row = (t, pane) => {
    const i = tabs.indexOf(t);
    const on = chosen.includes(t);
    return html`<div key=${t.id} class="process-row ${on ? "" : "off"} ${pane.active === t.id ? "shown" : ""} ${over?.pane === pane.id && over.before === t.id ? "drop-before" : ""}"
      draggable="true" onDragStart=${(e) => { e.dataTransfer.setData("text/plain", t.id); e.dataTransfer.effectAllowed = "move"; windows.drag(t.id); }}
      onDragEnd=${() => { windows.drag(null); setOver(null); }} ...${target(pane, t.id)}
      onContextMenu=${(e) => openPopup(e, pane, t)}>
      <div class="compare-tab ${on ? "" : "off"}">
        <${CompareMark} state=${t === baseline ? "main" : on ? "ticked" : "off"} alone=${chosen.length < 2} label=${labels[t.id]}
          onStep=${() => set(compareStep(tabs, compare, t.id))} />
        <span class="dot" style=${{ background: compareColor(i) }}></span>
        <span class="name" title=${`${fullLabels[t.id]}. Click: show it. Right-click: its details. Drag: move it to another window.`} onClick=${() => showTab(pane, t)}>${labels[t.id]}</span>
        <button class="view-options-btn" data-view-options=${t.id} onClick=${(e) => openViewOptions(e, pane, t)} aria-label=${`View options of ${labels[t.id]}`}
          title="View options: display, cut-off, organise, group, colours, rows (v for the tab in focus)"><${SlidersIcon} /></button>
        <button class="close-tab" onClick=${() => windows.close(t.id)} aria-label=${`Close ${labels[t.id]}`} title="Close it (w); Undo brings it back">×</button>
      </div>
    </div>`;
  };
  // A flow tab: its icon and name; no tick or baseline (flows are not compared)
  const flowRow = (t, pane) => html`<div key=${t.id} class="process-row flow-row ${pane.active === t.id ? "shown" : ""} ${over?.pane === pane.id && over.before === t.id ? "drop-before" : ""}"
      draggable="true" onDragStart=${(e) => { e.dataTransfer.setData("text/plain", t.id); e.dataTransfer.effectAllowed = "move"; windows.drag(t.id); }}
      onDragEnd=${() => { windows.drag(null); setOver(null); }} ...${target(pane, t.id)}>
      <div class="compare-tab">
        <${FlowIcon} resource=${t.flow.resource} />
        <span class="name" title=${`Flow: ${t.flow.name} (${t.flow.place}). Click: show it. Drag: move it to another window.`} onClick=${() => showTab(pane, t)}>${flowTitleNames(t.flow).main}
          ${flowTitleNames(t.flow).qual && html`<span class="loc">${flowTitleNames(t.flow).qual}</span>`}</span>
        <button class="close-tab" onClick=${() => windows.close(t.id)} aria-label=${`Close ${t.flow.name}`} title="Close it (w); Undo brings it back">×</button>
      </div>
    </div>`;
  // A model tab: the model icon and its name, as a flow tab (``flowTabs`` has the model tabs too: ``t.model``)
  const modelRow = (t, pane) => {
    const name = t.title?.name || "Model";
    return html`<div key=${t.id} class="process-row flow-row model-row ${pane.active === t.id ? "shown" : ""} ${over?.pane === pane.id && over.before === t.id ? "drop-before" : ""}"
      draggable="true" onDragStart=${(e) => { e.dataTransfer.setData("text/plain", t.id); e.dataTransfer.effectAllowed = "move"; windows.drag(t.id); }}
      onDragEnd=${() => { windows.drag(null); setOver(null); }} ...${target(pane, t.id)}>
      <div class="compare-tab">
        <span class="model-mark"><${CategoryIcon} /></span>
        <span class="name" title=${`Model card: ${name}. Click: show it. Drag: move it to another window.`} onClick=${() => showTab(pane, t)}>${name}</span>
        <button class="close-tab" onClick=${() => windows.close(t.id)} aria-label=${`Close ${name}`} title="Close it (w); Undo brings it back">×</button>
      </div>
    </div>`;
  };
  const groups = windows.panes.map((pane) => ({ pane, items: pane.tabs.map((id) => tabs.find((t) => t.id === id) || flowTabs.find((t) => t.id === id)).filter(Boolean) })).filter((g) => g.items.length);
  return html`
    <div class="lens-menu" ...${target(windows.panes.find((q) => q.id === windows.current) || lastPane)}>
      ${naming && html`<${NameDisplay} naming=${naming} setNaming=${setNaming} example=${tabs[0]?.state.root} />`}
      ${common && html`<div class="muted compare-common" title=${common}>${common}</div>`}
      <div class="row history-row">
        <${PopupMenu} label="Open history ▾" title="Tabs and windows closed, the last first: bring one back" className=""
          items=${windows.history.length ? windows.history.map((h) => ({ key: `h${h.at}`, label: truncate(h.label, 48), title: h.label, run: () => windows.reopen(h.at) }))
            : [{ label: "Nothing closed yet", disabled: true, run: () => {} }]} />
      </div>
      <div class="compare-tabs">
        ${tabs.length === 0 && flowTabs.length === 0 && html`<div class="muted">No process yet.</div>`}
        ${groups.map(({ pane, items }, n) => html`
          <div key=${pane.id} class="window-group ${windows.panes.length > 1 && pane.id === windows.current ? "focused" : ""} ${over?.pane === pane.id && !over.before ? "drop-here" : ""}"
            ...${target(pane)}>
            <div class="window-head">
              <span title="A window of the workspace: a split pane">Window ${n + 1}</span>
              <button class="close-tab" onClick=${() => windows.closePane(pane.id)} aria-label=${`Close window ${n + 1}`} title="Close this window and its tabs; Undo brings them back">×</button>
            </div>
            ${items.map((t) => (t.flow ? flowRow(t, pane) : t.model ? modelRow(t, pane) : row(t, pane)))}
          </div>`)}
        ${dragging && html`<div class="window-new ${over?.fresh ? "drop-here" : ""}" ...${fresh}>Drop here: a new window</div>`}
      </div>
    </div>`;
}

function ComparisonView({ info, tabs, compare, setCompare }) {
  keyboardOwner = null;  // the trees are out of sight: their keys stay off
  const set = (patch) => setCompare((c) => ({ ...c, ...patch }));
  const { labels, chosen, baseline } = compareSelection(tabs, compare);
  const base = Math.max(0, chosen.indexOf(baseline));
  // The method(s) and the group rules are those of the workspace (the Methods and Rules panel)
  const lens = useContext(LensContext);
  const method = lens.method;
  const rule = lens.attribution;
  const panel = compare.panel || "groups";

  // The groups of the comparison: the rule set of the workspace (named rules on processes, and on elementary flows
  // as flow groups, as in the Rules contribution); they are changed in the Rules panel
  const rules = lens.rules;
  const { groups, flowGroups, flowOwners, order, ruleOf } = useMemo(() => namedGroups(rules), [rules]);
  // The methods of the heat map: those shown together
  const heatMethods = lens.chartMethods;
  // What the bars are split into: groups, the tabs' displayed trees (asked of /api/rows tab by tab; /api/compare then
  // only gives the totals) or elementary flows
  const basis = panel !== "groups" && compare.basis === "none" ? "groups" : compare.basis || "groups";
  const cut = compare.cut?.mode in LIST_CUTS ? compare.cut : { mode: "top", value: compare.limit || 15 };
  const noBreakdown = basis === "none";
  const body = { entries: chosen.map((t) => ({ root: t.state.root.index, amount: t.state.amount })), method, methods: heatMethods, rule,
    basis: basis === "flows" ? "flows" : basis === "activities" ? "activities" : "groups", cut: apiCut(cut), groups: basis === "groups" ? groups : {}, flow_groups: basis === "groups" ? flowGroups : {}, flow_owners: basis === "groups" ? flowOwners : {},
    order: basis === "groups" ? order : [] };
  const trees = basis === "tree" ? chosen.map((t) => treeBody(t.state, method, rule)) : null;
  const key = JSON.stringify([body, trees]);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (body.entries.length < 2) return;
    let cancelled = false;
    setError(null);
    Promise.all([request("/api/compare", body), trees && Promise.all(trees.map((b, i) => request("/api/rows", b).then((d) => withLabels(d.rows, chosen[i].state.labels || {}))))])
      .then(([d, rows]) => !cancelled && setData({ key, entries: rows ? treeEntries(d.entries, rows, chosen.map((t) => t.state.root.index)) : d.entries }))
      .catch((e) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [key]);
  const ready = data?.key === key ? (noBreakdown ? data.entries.map((e) => ({ ...e, groups: { [TOTAL_BAR]: e.total } })) : data.entries) : null;
  const results = ready ? chosen.map((t, i) => ({ id: t.id, label: labels[t.id], color: compareColor(tabs.indexOf(t)), ...ready[i] })) : null;
  const names = useMemo(() => {
    if (!results) return [];
    const all = [...new Set(results.flatMap((r) => Object.keys(r.groups)))].filter((g) => g !== OTHER_GROUP);
    const size = (g) => results.reduce((s, r) => s + Math.abs(r.groups[g] || 0), 0);
    const present = results.some((r) => OTHER_GROUP in r.groups);
    return [...all.sort((a, b) => size(b) - size(a)), ...(present ? [OTHER_GROUP] : [])];
  }, [results]);

  // A bar has the colour of its rule: a group of the rules, a row of a tab's tree (or of the row above it) that a rule
  // matches; else one of its own
  const barRules = basis === "groups" ? ruleOf : basis === "tree" ? Object.assign({}, ...(ready || []).map((e) => e.rules || {})) : {};
  const groupColor = (g) => (g === OTHER_GROUP ? cssVar("--muted") : g === TOTAL_BAR && noBreakdown ? cssVar("--bar") : colorOf(barRules[g] ?? g));
  const target = chosen.length > 1 && compare.target !== base && compare.target < chosen.length ? compare.target : base === 0 ? 1 : 0;
  const unitLabel = info.methods[method].unit;
  const units = [...new Set(chosen.map((t) => unit(t.state.root.unit)).filter(Boolean))];
  const substituted = chosen.filter((t) => Object.keys(t.state.substitutions || {}).length);

  if (tabs.length < 2) {
    return html`<div class="compare"><div class="empty">
      Comparison needs two tabs with a process. Open another process in a new tab (Ctrl/⌘ + Enter in the search), then come back.
    </div></div>`;
  }
  return html`
    <div class="compare">
      <div class="compare-main">
        <div class="tabs" role="tablist">
          ${COMPARE_PANELS.map(([k, label]) => html`<button role="tab" class=${panel === k ? "active" : ""} onClick=${() => set({ panel: k })}>${label}</button>`)}
        </div>
        ${(units.length > 1 || (substituted.length > 0 && basis !== "tree")) && html`<div class="compare-info">
          ${units.length > 1 && html`<p class="warning">The processes are in ${units.join(", ")}: compare them only if they deliver an equivalent function.</p>`}
          ${substituted.length > 0 && basis !== "tree" && html`<p class="warning">Substitutions made in ${substituted.map((t) => labels[t.id]).join(", ")} are not applied here, only in the Explore view.</p>`}
        </div>`}
        <div class="panel">
          ${chosen.length < 2 ? html`<div class="empty">Tick at least two tabs to compare.</div>`
            : error ? html`<div class="error">${error}</div>`
            : !results ? html`<${Waiting} text="Computing…" />`
            : panel === "groups" ? html`<${CompareBars} results=${results} base=${base} unitLabel=${unitLabel} names=${names} groupColor=${groupColor}
                mode=${compare.mode || "absolute"} setMode=${(mode) => set({ mode })} basis=${basis} setBasis=${(b) => set({ basis: b })}
                cut=${cut} setCut=${(c) => set({ cut: c })} />`
            : panel === "difference" ? html`<${CompareDifference} results=${results} base=${base} target=${target} setTarget=${(t) => set({ target: t })}
                unitLabel=${unitLabel} names=${names}
                basis=${basis} setBasis=${(b) => set({ basis: b })} cut=${cut} setCut=${(c) => set({ cut: c })} />`
            : html`<${CompareMethods} results=${results} base=${base} info=${info} methods=${heatMethods}
                sorted=${!!compare.sorted} setSorted=${(sorted) => set({ sorted })} />`}
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- dragging a process
// The process being dragged from a row (an activity index), for the windows and the Processes panel to take
let processDrag = null;
// Props that make a row draggable as a process (none without an index)
const dragProcess = (index) => (index == null ? {} : {
  draggable: true,
  onDragStart: (e) => { processDrag = { index }; e.dataTransfer.setData("text/plain", String(index)); e.dataTransfer.effectAllowed = "copy"; },
  onDragEnd: () => { processDrag = null; document.dispatchEvent(new CustomEvent("lca-dragend")); },
});

// ---------------------------------------------------------------- workspace: tabs and split panes
// Like a notes app: each tab is one process; panes hold tabs and can be split
// left/right or top/bottom by dragging a tab onto a pane's edge. The Rules panel (left) shows the rules of the active
// tab of the pane used last; a process tab's charts and contributions are panels of its own card.
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

// The title of a process tab: its name, location and index, and what the Name display may ask for (the product…)
const processTitleOf = (root) => ({ ...pick(root, ["reference_product", "unit", "activity_type", "isic", "cpc", "database"]),
  name: root.name, location: root.location, index: root.index });
// A tab's title as the Name display has it: { main, qual } (process or flow tabs), the model tabs by their name
function tabTitleNames(title) {
  if (!title) return { main: "New tab", qual: "" };
  if (title.model !== undefined) return { main: title.name, qual: "" };
  if (title.flow !== undefined) return flowTitleNames({ name: title.name, place: title.location });
  return processNames(title);
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
    if (!dragging && !processDrag) return;
    e.preventDefault();
    e.stopPropagation();
    const z = typeof zone === "function" ? zone(e) : zone;
    if (drop?.pane !== pane.id || drop.zone !== z || drop.before !== before) setDrop({ pane: pane.id, zone: z, before });
  };
  const dropHere = (e) => {
    if ((!dragging && !processDrag) || !drop) return;
    e.preventDefault();
    if (processDrag) { const { index } = processDrag; processDrag = null; setDrop(null); actions.openProcess(index, pane.id, drop.zone); return; }
    actions.move(dragging, pane.id, drop.zone, drop.before);
  };
  const title = (id) => tabTitleNames(titles[id]).main;
  return html`
    <div class="pane ${focused && multiple ? "focused" : ""}" onMouseDownCapture=${() => actions.focus(pane.id)}>
      <div class="pane-tabs" role="tablist" data-pane=${pane.id} onDragOver=${over("center")} onDrop=${dropHere}>
        ${pane.tabs.map((id) => html`
          <div key=${id} role="tab" aria-selected=${id === pane.active} draggable="true"
               class="pane-tab ${id === pane.active ? "active" : ""} ${titles[id]?.flow !== undefined ? "flow" : ""} ${titles[id]?.model !== undefined ? "model" : ""} ${drop?.pane === pane.id && drop.before === id ? "drop-before" : ""}"
               style=${titles[id]?.flow !== undefined ? { "--flow-color": FLOW_TYPES[titles[id].resource ? "resource" : "emission"].color } : null}
               title=${titles[id]?.model !== undefined ? `Model card: ${titles[id].name}` : titles[id] ? `${titles[id].reference_product ? `${titles[id].reference_product} | ` : ""}${titles[id].name} (${titles[id].location})` : "New tab: choose a process"}
               onClick=${() => actions.select(pane.id, id)}
               onAuxClick=${(e) => e.button === 1 && actions.close(id)}
               onDragStart=${(e) => { e.dataTransfer.setData("text/plain", id); e.dataTransfer.effectAllowed = "move"; actions.drag(id); }}
               onDragEnd=${() => actions.drag(null)}
               onDragOver=${over("center", id)}>
            ${titles[id]?.flow !== undefined && html`<${FlowIcon} resource=${titles[id].resource} />`}
            ${titles[id]?.model !== undefined && html`<span class="model-mark"><${CategoryIcon} /></span>`}
            <span class="pane-tab-title">${title(id)}</span>
            ${tabTitleNames(titles[id]).qual && html`<span class="loc">${tabTitleNames(titles[id]).qual}</span>`}
            <button class="close" aria-label="Close tab" title="Close (w, or middle-click)"
              onClick=${(e) => { e.stopPropagation(); actions.close(id); }}>×</button>
          </div>`)}
        <button class="new-tab" onClick=${() => actions.add(pane.id)} title="New tab (t)" aria-label="New tab">+</button>
        <span class="spacer"></span>
        <button class="icon" onClick=${() => actions.split(pane.id, "right")} title="Split right: open a copy of this tab on the right"
          aria-label="Split right"><${SplitIcon} dir="row" /></button>
        <button class="icon" onClick=${() => actions.split(pane.id, "bottom")} title="Split down: open a copy of this tab below"
          aria-label="Split down"><${SplitIcon} dir="column" /></button>
      </div>
      <div class="pane-body" ref=${body} data-pane=${pane.id} onDragOver=${over(zoneAt)} onDrop=${dropHere}
           onDragLeave=${(e) => !body.current.contains(e.relatedTarget) && drop?.pane === pane.id && setDrop(null)}>
        ${children}
        ${(dragging || processDrag) && drop?.pane === pane.id && !drop.before && html`<div class="drop-zone ${drop.zone}"></div>`}
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

// Keys of the lists and pop-ups, for the Shortcuts dialog of the top bar: [keys, what they do]
const SHORTCUTS = [
  ["Anywhere", [
    ["Ctrl/⌘ + K", "Search a process in the top bar: Enter shows it in the tab, Ctrl/⌘ + Enter opens it in a new tab"],
    ["v", "View mode; when already in View mode, the view options of the tab in focus (as its sliders button in the Processes panel)"],
    ["e", "Edit mode (a Brightway-built database); when already in Edit mode, open the form of the selected process, as below"],
    ["c", "Compare mode"],
    ["s", "Search a process in the top bar (as Ctrl/⌘ + K)"],
    ["Ctrl/⌘ + ←  →", "Hide or show the left panel (processes, methods, rules) / in Edit mode, the Edit panel on the right"],
  ]],
  ["Lists and trees (the one clicked last)", [
    ["↓ ↑", "Select the row below / above"],
    ["Shift + ↓ ↑", "Extend the selection over siblings (what Group and Merge act on)"],
    ["→", "Expand the row; if already expanded, go to its first child"],
    ["←", "Collapse the row; if already collapsed, go to its parent"],
    ["Tree view, root on top", "← → go across the processes (siblings), ↓ expands (then goes to the first child), ↑ collapses (then goes to the parent)"],
    ["Space", "Open (or close) the pop-up of the process or flow (also a right-click)"],
    ["i", "Open (or close) the pop-up of its exchange (info): comment and uncertainty"],
    ["Enter", "Show the process in the tab; a flow opens in a flow tab (also a double-click)"],
    ["e", "In Edit mode: open the form of the process, to change it"],
    ["t / w", "A new tab like this one / close this tab"],
    ["Ctrl/⌘ + Enter", "Open the process in a new tab (also Ctrl/⌘ + double-click)"],
    ["r", "Rename the row in this view (the database is not changed)"],
    ["g", "Group the selected rows, or ungroup a group"],
    ["m", "Merge the selected rows, or unmerge a merged row"],
    ["o", "Move the selection to others (inside an expanded others row: pin it out); × on one of several selected rows does the same"],
    ["Ctrl/⌘ + Z", "Undo the last change to the tree"],
    ["Esc", "Clear the selection"],
  ]],
  ["Pop-ups", [
    ["← →", "Switch panel"],
    ["↓", "Enter the panel; then ↓ ↑ move between its rows, ↑ on the first goes back to the panels"],
    ["→ ←", "On a row of a list: expand, collapse (or go to the first child, the parent)"],
    ["Space  i  Enter  Ctrl/⌘ + Enter", "On a row of a list: as in the main list; Enter uses a process, flow or model in the tab where the tab can show it (a flow in a flow tab, a model in a model tab), else in a new tab"],
    ["Backspace", "Back to the previous pop-up"],
    ["Esc", "Close the pop-up (a pinned one only while the pointer is on it)"],
  ]],
  ["Mouse, on a row of a process, flow or model", [
    ["Click", "Select the row"],
    ["Right-click", "Its pop-up (as Space)"],
    ["Double-click", "Use it in the tab (as Enter)"],
    ["Ctrl/⌘ + double-click", "Open it in a new tab (as Ctrl/⌘ + Enter)"],
    ["Right-click a method", "In the Methods panel: its model card (as the list icon at its right)"],
  ]],
  ["Search fields", [
    ["↓ ↑  Enter", "Move through the results, choose one"],
    ["Ctrl/⌘ + Enter", "Choose the result in a new tab (also Ctrl/⌘-click)"],
    ["Esc", "Close the results"],
  ]],
];

// A pop-up over the page, closed with Esc, ×, or a click beside it
function Modal({ title, className = "", onClose, children }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return html`
    <div class="modal-backdrop" onMouseDown=${(e) => e.target === e.currentTarget && onClose()}>
      <div class=${`load-dialog ${className}`} role="dialog" aria-label=${title}>
        <div class="info-head">
          <div class="info-title">${title}</div>
          <button onClick=${onClose} aria-label="Close">×</button>
        </div>
        <div class="load-body">${children}</div>
      </div>
    </div>`;
}

function ShortcutsDialog({ onClose }) {
  return html`
    <${Modal} title="Keyboard shortcuts" className="shortcuts-dialog" onClose=${onClose}>
      ${SHORTCUTS.map(([title, keys]) => html`
        <section key=${title}>
          <h3>${title}</h3>
          <table class="shortcuts">
            ${keys.map(([k, what]) => html`<tr key=${k + what}><td class="keys">${k}</td><td>${what}</td></tr>`)}
          </table>
        </section>`)}
      <p class="muted small">Keys do nothing while you type in a field.</p>
    <//>`;
}

// The settings of the workspace, from the status bar (and Colours… under Color by): the colours of every Color by mode, the
// same in every tab, chart and comparison. One palette for all (Automatic: a hue from each rule's or value's name, and the
// exchange types' own colours); a colour picked wins over it.
const VALUE_PREFIXES = ["location", "activity_type", "compartment"];
function SettingsDialog({ lens, onClose }) {
  const picked = lens.ruleColors || {};
  const keys = [...new Set(lens.rules.filter((r) => !(r.target === "flows" && r.by)).map(ruleKey))];
  const values = Object.keys(picked).filter((k) => VALUE_PREFIXES.some((p) => k.startsWith(`${p}:`))).sort();
  const swatch = (key, name, title) => html`
    <li key=${key} title=${title}>
      <input type="color" class="swatch" value=${colorHex(colorFor(key))} onInput=${(e) => lens.pickColor(key, e.target.value)}
        aria-label=${`Colour of ${name}`} />
      <span>${name}</span>
      ${picked[key] && html`<button class="link" onClick=${() => lens.pickColor(key, null)} title="Back to the palette's colour">reset</button>`}
    </li>`;
  return html`
    <${Modal} title="Settings" className="settings-dialog" onClose=${onClose}>
      <section>
        <h3>Colours <${InfoTip} text="Rows, bars and groups take these colours with Color by in the view options: by rules, by type, or by location, activity type or compartment. The palette serves every mode; a colour picked (here, or on the swatches under Color by) wins over it; choosing a palette clears them." label="About the colours" /></h3>
        <${PaletteSwitch} palette=${lens.palette} setPalette=${lens.setPalette} palettes=${lens.palettes} addPalette=${lens.addPalette}
          customized=${Object.keys(picked).length > 0} />
        <h4>Rules</h4>
        <ul class="rule-colours">${keys.map((key) => swatch(key, key))}</ul>
        ${keys.length === 0 && html`<p class="muted small">The rule set in use has no rules yet.</p>`}
        <h4>Types</h4>
        <ul class="rule-colours">${Object.entries(FLOW_TYPES).map(([type, t]) => swatch(`type:${type}`, t.label, t.hint))}</ul>
        <h4>Locations, activity types, compartments</h4>
        <ul class="rule-colours">${values.map((key) => {
          const [mode, ...rest] = key.split(":");
          return swatch(key, `${rest.join(":")} (${COLOR_MODES[mode].toLowerCase()})`);
        })}</ul>
        <p class="muted small">${values.length ? "Others follow the palette." : "From the palette; click a swatch under Color by to pick one."}</p>
      </section>
    <//>`;
}

// The panels at a first start (see DEFAULT_VIEW_COLUMNS): the tabs alone. The left panel (processes, methods, rules)
// opens with its icon, the method or rule set of the status bar, or Ctrl/⌘ + ←. The side panel on the right is Edit
// mode's (the Edit panel), open when Edit mode starts; Ctrl/⌘ + → hides or shows it there. Until a panel is resized by
// hand its width follows the window: the side panel 42 % of it, leaving the tree at least 720 px (its three columns and
// names); a width dragged is kept, and fitted to a smaller window.
const FIRST_PANELS = { leftOpen: false, sideOpen: true, leftWidth: 340 };
const PANEL_MIN = { left: 260, side: 280 };
// The panels never push the page wider than the window: the workspace between them keeps WORKSPACE_MIN, the rest
// (less the resizers, or the buttons that reopen a closed panel) is what the two panels can share. The left panel is
// fitted first (leaving the side panel its minimum), the side panel takes at most what is left; a window too small
// for both minimums squeezes the workspace, and below that ``.body`` clips rather than scrolls.
const WORKSPACE_MIN = 360;
const PANEL_EDGE = { open: 4, closed: 30 };  // a resizer, or the button that reopens the panel
const viewportWidth = () => document.documentElement.clientWidth || window.innerWidth;
const panelRoom = (leftOpen, sideOpen) => viewportWidth() - WORKSPACE_MIN
  - PANEL_EDGE[leftOpen ? "open" : "closed"] - PANEL_EDGE[sideOpen ? "open" : "closed"];
const clampWidth = (width, min, max) => Math.max(min, Math.min(width, max));
// The left panel's width shown (``width``: as dragged, or null), the side panel open or not
const leftWidthFor = (width, sideOpen) => clampWidth(width ?? FIRST_PANELS.leftWidth, PANEL_MIN.left,
  panelRoom(true, sideOpen) - (sideOpen ? PANEL_MIN.side : 0));
// The side panel's width shown (``width``: as dragged, or null: 42 % of the window, the tree keeping 720 px), next to
// the left panel's ``left`` px (0: closed)
const sideWidthFor = (width, left) => {
  const wanted = width || Math.min(Math.round(viewportWidth() * 0.42), viewportWidth() - 720);
  return clampWidth(wanted, PANEL_MIN.side, panelRoom(left > 0, true) - left);
};
const keptWidth = (name) => Number(loadPreference(name, "")) || null;
// Keeps ``value`` as the preference ``name`` when it changes (not at the first render: a default that was never chosen is
// not written, so that a later version's defaults still apply)
function useChangeSaved(name, value) {
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    savePreference(name, value);
  }, [value]);
}

function App() {
  const [info, setInfo] = useState(null);
  // The impact methods and group rules of the workspace, which the tabs follow (see the lens above FUView)
  const [lens, setLens] = useState(null);
  nameFormat = lens?.nameFormat || DEFAULT_NAME_FORMAT;
  nameStamp = JSON.stringify(nameFormat);
  nameDisplay = lens?.naming || DEFAULT_NAMING;
  rulePalette = { name: lens?.palette || "auto", keys: (lens?.sets[lens.set] || []).map((r) => r.by || r.name), own: lens?.palettes || {},
    picked: lens?.ruleColors || {} };
  paletteStamp = `${rulePalette.name}|${rulePalette.keys.join("|")}|${JSON.stringify(rulePalette.own[rulePalette.name] || "")}|${JSON.stringify(rulePalette.picked)}`;
  useEffect(() => { if (lens && info) saveLens(info, lens); }, [lens]);
  const [error, setError] = useState(null);
  // Databases loaded in the server; null: a server without the Projects dialog (one model, plain URLs)
  const [databases, setDatabases] = useState(undefined);
  const [loadOpen, setLoadOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Ctrl/⌘ + K: the search of the top bar
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        const input = document.querySelector(".top-bar .fu-input input");
        if (!input) return;
        e.preventDefault();
        input.focus();
        input.select();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  // The process form: null (closed), or what to open it on: {} (a new process), { edit } (a dataset written
  // by the app), { copyFrom } (a process to start from), { initial } (a change staged before)
  const [datasetOpen, setDatasetOpen] = useState(null);
  // "view": the model built from the database; "edit": change processes, staged then applied together
  const [mode, setMode] = useState(() => loadPreference("mode", "view"));
  useEffect(() => savePreference("mode", mode === "compare" ? "view" : mode), [mode]);  // the page opens on the explorer
  const comparing = mode === "compare";
  const comparingRef = useRef(false);
  comparingRef.current = comparing;
  const watchStates = useRef(false);
  const [, setTick] = useState(0);
  const [compare, setCompare] = useState({});  // the comparison's choices, kept while the mode is left
  const [layout, setLayout] = useState(null);
  const [focus, setFocus] = useState(null);
  const [titles, setTitles] = useState({});
  const [dragging, setDragging] = useState(null);
  const [drop, setDrop] = useState(null);
  // The panels as the user left them, else those of a first start (FIRST_PANELS); a width is null until dragged
  const [sideOpen, setSideOpen] = useState(() => loadPreference("sideOpen", FIRST_PANELS.sideOpen ? "1" : "0") === "1");
  const [sideWidth, setSideWidth] = useState(() => keptWidth("sideWidth"));
  useChangeSaved("sideOpen", sideOpen ? "1" : "0");
  useChangeSaved("sideWidth", sideWidth ? String(sideWidth) : "");
  // The left panel: the impact methods and the group rules (the lens of the workspace)
  const [leftOpen, setLeftOpen] = useState(() => loadPreference("leftOpen", FIRST_PANELS.leftOpen ? "1" : "0") === "1");
  const [leftWidth, setLeftWidth] = useState(() => keptWidth("leftWidth"));
  const [leftTab, setLeftTab] = useState(() => { const t = loadPreference("leftTab", "processes"); return t === "compare" || t === "view" ? "processes" : t; });
  useChangeSaved("leftOpen", leftOpen ? "1" : "0");
  useChangeSaved("leftWidth", leftWidth ? String(leftWidth) : "");
  useEffect(() => savePreference("leftTab", leftTab), [leftTab]);
  // The panels' widths follow the window until dragged
  useEffect(() => {
    const onResize = () => setTick((t) => t + 1);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  watchStates.current = comparing || (leftOpen && leftTab === "processes");
  // A side pane that is opened takes the focus (its panel's tab: ← and → switch panel)
  const opened = useRef({ left: leftOpen, side: sideOpen, mounted: false });
  useEffect(() => {
    const was = opened.current;
    opened.current = { left: leftOpen, side: sideOpen, mounted: true };
    if (!was.mounted) return;
    const focusOn = (selector) => setTimeout(() => document.querySelector(selector)?.focus({ preventScroll: true }), 40);
    if (leftOpen && !was.left) focusOn(".leftbar .tabs button.active");
    if (sideOpen && !was.side) focusOn(".sidebar .edit-title button");
  }, [leftOpen, sideOpen]);
  const processFocus = useRef([]);  // the process tabs, last in focus first (what the side and Rules panels show for a flow or model tab)
  const states = useRef({});  // tab id → its saved view (what a remounted tab starts from)
  const starts = useRef({});  // tab id → how a tab without a saved view starts
  const current = useRef({});
  current.current = { layout, focus };
  // Where the open database was built from (Brightway): the project that is edited
  const origin = databases?.find((d) => d.key === DATABASE)?.brightway || null;
  const editing = mode === "edit" && !!origin;
  // v: View mode, or in View mode the view options of the tab in focus (their pop-up); e: Edit mode (in Edit mode the tree's e opens the form of the
  // process); c: Compare mode; s: the search of the top bar. Not while typing, in a pop-up or in a dialog.
  useEffect(() => {
    const onKey = (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.defaultPrevented) return;
      if (e.target.closest?.("input, textarea, select, [contenteditable], .info-popup") || document.querySelector(".modal-backdrop")) return;
      if (e.key === "v") {
        // the pop-up of the view options of the tab in focus (View mode)
        if (mode !== "view") setMode("view");
        else {
          const { layout: shown, focus: pane } = current.current;
          const id = shown ? panesOf(shown).find((p) => p.id === pane)?.active : null;
          document.dispatchEvent(new CustomEvent("lca:custom-view", { detail: { id } }));
        }
      } else if (e.key === "e") {
        if (editing || !origin) return;
        setMode("edit");
        setSideOpen(true);
      } else if (e.key === "c") setMode("compare");
      else if (e.key === "s") {
        const input = document.querySelector(".top-bar .fu-input input");
        if (!input) return;
        input.focus();
        input.select();
      } else return;
      e.preventDefault();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mode, editing, !!origin]);
  // Ctrl/⌘ + ← and →: hide or show the left panel and, in Edit mode, the right one (the Edit panel; View mode has none)
  // (not while typing: there they move by word)
  const editingRef = useRef(false);
  editingRef.current = editing;
  useEffect(() => {
    const onKey = (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.defaultPrevented || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
      if (e.target.closest?.("input, textarea, select, [contenteditable]") || document.querySelector(".modal-backdrop")) return;
      if (e.key === "ArrowRight" && !editingRef.current) return;
      e.preventDefault();
      if (e.key === "ArrowLeft") setLeftOpen((open) => !open); else setSideOpen((open) => !open);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  // The changes staged for the project (kept by the server) with the databases they affect and the locked ones
  const [pendingView, setPendingView] = useState({ changes: [], affected: [], locked: [] });
  const pending = pendingView.changes;
  const refreshEditor = () => origin && serverRequest(`/api/brightway/pending?${bwQuery(origin)}`).then(setPendingView).catch(() => { /* none */ });
  const refreshDatabases = () => serverRequest("/api/databases").then(setDatabases).catch(() => { /* kept as it was */ });
  useEffect(() => {
    setPendingView({ changes: [], affected: [], locked: [] });
    refreshEditor();
  }, [origin?.folder, origin?.project]);
  const stage = (change) => serverRequest("/api/brightway/pending", { folder: origin.folder, project: origin.project, change }).then(setPendingView);
  const unstage = (id) => serverRequest("/api/brightway/pending/remove", { folder: origin.folder, project: origin.project, id }).then(setPendingView);
  const unstageAll = () => serverRequest("/api/brightway/pending/remove", { folder: origin.folder, project: origin.project }).then(setPendingView);
  // What the open model shows may be wrong: the project changed since it was built (a rebuild is required), or, in
  // edit mode, staged changes affect it. Its impacts are greyed out.
  const outdated = databases?.find((d) => d.key === DATABASE)?.outdated || null;
  const stale = !!outdated || (editing && !!origin && pendingView.affected.includes(origin.database));
  const [rebuilding, setRebuilding] = useState(null);
  const rebuild = () => serverRequest("/api/load", { brightway: origin.folder, project: origin.project, database: origin.database, rebuild: true, reopen: true })
    .then(setRebuilding).catch((e) => setRebuilding({ status: "failed", error: e.message }));
  useEffect(() => {
    if (rebuilding?.status !== "running") return;
    const timer = setTimeout(() => serverRequest(`/api/jobs/${rebuilding.id}`).then((j) => {
      setRebuilding(j);
      if (j.status === "done" && j.key) openDatabase(j.key);
    }).catch((e) => setRebuilding({ status: "failed", error: e.message })), 700);
    return () => clearTimeout(timer);
  }, [rebuilding]);
  const focusedRoot = layout && focus ? titles[panesOf(layout).find((p) => p.id === focus)?.active] : null;
  const [dataset, setDataset] = useState(null);
  useEffect(() => {
    setDataset(null);
    if (!editing || focusedRoot?.index == null) return;
    let live = true;
    request(`/api/activity/${focusedRoot.index}`)
      .then((a) => serverRequest(datasetUrl(origin, a.database, a.code)))
      .then((d) => live && setDataset(d))
      .catch(() => { /* not a Brightway process: nothing to edit */ });
    return () => { live = false; };
  }, [editing, origin?.folder, origin?.project, focusedRoot?.index, pendingView.locked.join("|")]);
  const editor = { editing, origin, pending, locked: pendingView.locked, stale, stage, openForm: setDatasetOpen };
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
    clearAnswers();
    request("/api/info").then((i) => {
      const saved = loadWorkspace();
      let seed = null;  // a workspace saved before the lens: its first tab gives the method and the rules
      if (saved?.layout && panesOf(saved.layout).some((p) => p.tabs.length)) {
        states.current = saved.tabs || {};
        const first = panesOf(saved.layout)[0];
        seed = states.current[first.active] || states.current[first.tabs[0]] || null;
        setTitles(Object.fromEntries(Object.entries(states.current).map(([id, s]) => [id, s?.root ? processTitleOf(s.root)
          : s?.flow ? { name: s.flow.name, location: s.flow.place, flow: s.flow.flow, resource: s.flow.resource }
          : s?.model ? modelTitle(i, s.model.method) : null])));
        setLayout(saved.layout);
        setFocus(panesOf(saved.layout).some((p) => p.id === saved.focus) ? saved.focus : panesOf(saved.layout)[0].id);
      } else {
        // First start, or a view saved before tabs existed
        const id = newId("t"), old = loadSaved();
        seed = old;
        starts.current[id] = old ? { state: old } : { defaultRoot: true };
        const pane = { type: "pane", id: newId("p"), tabs: [id], active: id };
        setLayout(pane);
        setFocus(pane.id);
      }
      setLens(initLens(i, seed));
      setInfo(i);
    }).catch((e) => setError(e.message));
  };

  const initialOf = (id) => (states.current[id] ? { state: states.current[id] } : starts.current[id]);
  const activeTabOf = (paneId) => (layout ? panesOf(layout).find((p) => p.id === paneId)?.active : null);
  // The comparison, and the list of tabs of the left panel, read the states of the tabs: they are drawn again when one changes
  const onSave = (id, state) => { states.current[id] = state; persist(); if (watchStates.current) setTick((t) => t + 1); };
  const onTitle = (id, root) => setTitles((t) => ({ ...t, [id]: root ? (root.flow !== undefined ? { name: root.name, location: root.location, flow: root.flow, resource: root.resource }
    : root.model !== undefined ? { name: root.name, model: root.model }
    : processTitleOf(root)) : null }));
  // A flow tab (its panels, no tree): its state, or how it starts, has ``flow``
  const flowOf = (id) => states.current[id]?.flow || starts.current[id]?.state?.flow || starts.current[id]?.flow || null;
  // A model tab (the model card): its state, or how it starts, has ``model`` ({ method, compare, organize })
  const modelOf = (id) => states.current[id]?.model || starts.current[id]?.state?.model || starts.current[id]?.model || null;
  // Tabs with no tree, whose settings a new process tab does not take
  const treeless = (id) => !!(flowOf(id) || modelOf(id));
  // What a new tab opened from tab ``from`` starts with: a flow, a model, or a process with the settings of ``from``
  const startFrom = (from, root) => (root?.flow !== undefined ? { flow: root }
    : root?.model?.method !== undefined ? { model: root.model }
    : { inherit: treeless(from) ? null : states.current[from], root });
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
  // What was closed, the last first: a notice offers to bring it back, and the Processes panel while any is kept
  const closedStack = useRef([]);
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);
  const [closedCount, setClosedCount] = useState(0);
  function remember(list, message) {
    closedStack.current = [...closedStack.current.slice(-19), list];
    setClosedCount(closedStack.current.length);
    setToast({ message });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 12000);
  }
  // ``at``: which of the closed ones (the last by default)
  function reopenClosed(at = closedStack.current.length - 1) {
    const [list] = closedStack.current.splice(at, 1);
    setClosedCount(closedStack.current.length);
    setToast(null);
    if (!list) return;
    setLayout((l) => {
      let next = l, made = null;
      for (const snap of list) {
        if (snap.state) states.current[snap.id] = snap.state; else starts.current[snap.id] = snap.start || { inherit: null };
        if (panesOf(next).some((q) => q.id === snap.pane)) next = insertTab(next, snap.pane, snap.id, "center", snap.before);
        else if (made) next = insertTab(next, made, snap.id, "center", null);
        else { next = insertTab(next, panesOf(next)[0].id, snap.id, "right"); made = paneOfTab(next, snap.id).id; }
      }
      setFocus(paneOfTab(next, list[list.length - 1].id).id);
      return next;
    });
  }
  useEffect(() => {
    const clear = () => setDrop(null);
    document.addEventListener("lca-dragend", clear);
    return () => document.removeEventListener("lca-dragend", clear);
  }, []);
  const actions = {
    focus: (paneId) => paneId !== focus && setFocus(paneId),
    select: (paneId, id) => setLayout((l) => mapPanes(l, (p) => (p.id === paneId ? { ...p, active: id } : p))),
    add: (paneId) => openTab(paneId, { inherit: treeless(activeTabOf(paneId)) ? null : states.current[activeTabOf(paneId)] }),
    // Split = a copy of the active tab in a new pane (same process, to compare settings)
    split: (paneId, zone) => {
      const source = activeTabOf(paneId);
      openTab(paneId, states.current[source] ? { state: structuredClone(states.current[source]) } : { inherit: null }, zone);
    },
    close: (id) => {
      const pane = paneOfTab(current.current.layout, id);
      remember([{ id, state: states.current[id], start: starts.current[id], pane: pane.id, before: pane.tabs[pane.tabs.indexOf(id) + 1] ?? null }],
        `Closed ${titles[id] ? titles[id].name : "a tab"}`);
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
    // A window (a pane) with its tabs
    closePane: (paneId) => {
      const pane = panesOf(current.current.layout).find((q) => q.id === paneId);
      if (!pane) return;
      remember(pane.tabs.map((id) => ({ id, state: states.current[id], start: starts.current[id], pane: paneId, before: null })),
        `Closed a window of ${pane.tabs.length} ${pane.tabs.length === 1 ? "tab" : "tabs"}`);
      pane.tabs.forEach((id) => { delete states.current[id]; delete starts.current[id]; });
      setLayout((l) => {
        const next = mapPanes(l, (q) => (q.id === paneId ? null : q));
        if (next) {
          setFocus((f) => (panesOf(next).some((q) => q.id === f) ? f : panesOf(next)[0].id));
          return next;
        }
        const fresh = newId("t");
        starts.current[fresh] = { inherit: null };
        const empty = { type: "pane", id: newId("p"), tabs: [fresh], active: fresh };
        setFocus(empty.id);
        return empty;
      });
    },
    // A process opened in a new tab of a window (``zone``: "center", or an edge: a new window there)
    openProcess: async (index, paneId, zone = "center") => {
      try {
        const found = await request(`/api/activity/${index}?method=${lens.method}`);
        openTab(paneId, startFrom(activeTabOf(paneId), found), zone);
      } catch { /* not a process of this database */ }
    },
    reopen: (at) => reopenClosed(at),
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

  // The lens the tabs follow, with the changes the menus make (a name taken by another set gets a number)
  const freshName = (name, sets) => { let n = name, k = 1; while (n in sets) n = `${name} ${++k}`; return n; };
  const lensApi = lens && {
    ...lens, rules: lens.sets[lens.set],
    setMethod: (method) => setLens((l) => ({ ...l, method })),
    setChartMethods: (chartMethods) => setLens((l) => ({ ...l, chartMethods })),
    setAttribution: (attribution) => setLens((l) => ({ ...l, attribution })),
    setNameFormat: (format) => setLens((l) => ({ ...l, nameFormat: format })),
    setNaming: (naming) => setLens((l) => ({ ...l, naming })),
    // choosing a palette, or making one, clears the colours picked for rules
    setPalette: (palette) => setLens((l) => ({ ...l, palette, ruleColors: {} })),
    addPalette: (name, colors) => setLens((l) => { const n = freshName(name, { ...RULE_PALETTES, ...l.palettes }); return { ...l, palettes: { ...l.palettes, [n]: colors }, palette: n, ruleColors: {} }; }),
    pickColor: (key, color) => setLens((l) => { const { [key]: old, ...rest } = l.ruleColors || {}; return { ...l, ruleColors: color ? { ...rest, [key]: color } : rest }; }),
    setRules: (rules) => setLens((l) => ({ ...l, sets: { ...l.sets, [l.set]: rules } })),
    chooseSet: (set) => setLens((l) => ({ ...l, set })),
    // The rules of a view file as a set of their own, now in use
    loadSet: (name, rules) => setLens((l) => { const n = freshName(name, l.sets); return { ...l, set: n, sets: { ...l.sets, [n]: rules } }; }),
    // The settings of a first start: the method, the methods shown together, the rules, the names and the palette, and the panels
    resetDefaults: () => {
      setLens((l) => ({ ...l, method: info.default_method, chartMethods: familyOf(info, info.default_method),
        sets: { ...l.sets, Default: defaultRules() }, set: "Default", palette: "auto", ruleColors: {}, nameFormat: DEFAULT_NAME_FORMAT, naming: DEFAULT_NAMING, attribution: "first_match" }));
      setLeftOpen(FIRST_PANELS.leftOpen); setSideOpen(FIRST_PANELS.sideOpen); setLeftWidth(null); setSideWidth(null);
    },
    addSet: (name, copy) => setLens((l) => { const n = freshName(name, l.sets); return { ...l, set: n, sets: { ...l.sets, [n]: copy ? l.sets[l.set] : [] } }; }),
    renameSet: (name) => setLens((l) => {
      const n = freshName(name, l.sets);
      return { ...l, set: n, sets: Object.fromEntries(Object.entries(l.sets).map(([k, v]) => [k === l.set ? n : k, v])) };
    }),
    deleteSet: () => setLens((l) => { const { [l.set]: gone, ...sets } = l.sets; return { ...l, sets, set: Object.keys(sets)[0] }; }),
    // Show the Methods or Rules panel
    show: (what) => { setLeftTab(what); setLeftOpen(true); },
    // The colours (Settings pop-up)
    showColours: () => setSettingsOpen(true),
  };
  // ``centre``: the search of a process (a model is open), in the middle of the bar
  const topBar = (centre) => html`
    <header class="top-bar">
      <div class="top-left">
      <h1>LCA Explorer</h1>
      <${DatabaseSelect} databases=${databases} />
      ${databases && html`<button onClick=${() => setLoadOpen(true)}
        title="Projects: open, add, rename, lock or remove a database">Projects</button>`}
      ${editing && html`<span class="mode-badge" title="Edit mode: changes are staged, then made together in the project">Edit mode</span>`}
      ${outdated && html`<button class="primary" disabled=${rebuilding?.status === "running"} onClick=${rebuild}
        title=${`${outdated}: the model has to be built again, and its impacts are greyed out until then`}>
        ${rebuilding?.status === "running" ? "Rebuilding…" : "Rebuild required"}</button>`}
      ${rebuilding?.status === "failed" && html`<span class="error-inline" title=${rebuilding.error}>Rebuild failed</span>`}
      </div>
      <div class="top-centre">${centre}</div>
      <div class="top-right">
      ${databases && html`
        <span class="mode-switch" role="group" aria-label="Mode">
          <button class=${`has-key ${!editing && !comparing ? "primary" : ""}`} onClick=${() => setMode("view")}
            title="View (v): explore the results of the model built from the database"><${KeyedLabel} label="View" k="v" /></button>
          <button class=${`has-key ${comparing ? "primary" : ""}`} onClick=${() => setMode("compare")}
            title="Compare (c): the processes of several tabs: contribution by group, difference, impact categories"><${KeyedLabel} label="Compare" k="c" /></button>
          <button class=${`has-key ${editing ? "primary" : ""}`} disabled=${!origin} onClick=${() => { setMode("edit"); setSideOpen(true); }}
            title=${origin ? "Edit (e): change processes, staged then made together and the model built again" : "Open a database of a Brightway project to edit it"}>
            <${KeyedLabel} label="Edit" k="e" />${pending.length ? ` (${pending.length} staged)` : ""}</button>
        </span>`}
      </div>
    </header>
    ${shortcutsOpen && html`<${ShortcutsDialog} onClose=${() => setShortcutsOpen(false)} />`}
    ${settingsOpen && html`<${SettingsDialog} lens=${lensApi} onClose=${() => setSettingsOpen(false)} />`}
    ${loadOpen &&html`<${LoadDatabase} onClose=${() => setLoadOpen(false)} onDatabases=${setDatabases} onLocks=${refreshEditor} />`}
    ${datasetOpen && origin && html`<${DatasetForm} current=${origin} edit=${datasetOpen.edit} initial=${datasetOpen.initial}
      copyFrom=${datasetOpen.copyFrom} deleted=${new Set(pending.filter((c) => c.kind === "delete").map((c) => `${c.database}/${c.code}`))}
      staged=${pending} locked=${pendingView.locked} onStage=${stage} onClose=${() => setDatasetOpen(null)} />`}`;
  if (databases && !databases.some((d) => d.key === DATABASE)) {
    // Nothing loaded yet, or the database of this address is not loaded any more
    return html`
      <div class="app">
        ${topBar(null)}
        <div class="welcome"><${LoadDatabase} missing=${DATABASE && decodeURIComponent(DATABASE)} /></div>
      </div>`;
  }
  if (!info || !layout || !lens) return error ? html`<div class="empty">${error}</div>` : html`<${Waiting} text="Loading model…" />`;
  const allTabs = panesOf(layout).flatMap((p) => p.tabs);
  const showStaged = !editing && !!origin && pending.length > 0;
  const focusedTab = activeTabOf(focus);
  const compareTabs = allTabs.filter((id) => states.current[id]?.root).map((id) => ({ id, state: states.current[id], title: titles[id] }));
  // The right panel is Edit mode's alone (the Edit panel): in View mode a process tab's card has what it held
  const sideVisible = sideOpen && editing;
  const leftShown = leftWidthFor(leftWidth, sideVisible), sideShown = sideWidthFor(sideWidth, leftOpen ? leftShown : 0);
  // The Processes panel lists the flow and model tabs too (with their icon; they are not compared)
  const flowTabs = allTabs.filter(treeless).map((id) => ({ id, flow: flowOf(id), model: modelOf(id), title: titles[id] }));
  // The Rules panel is filled by process tabs (their slots groups-<id>): with a flow or model tab in focus it shows the
  // process tab last in focus (else the first open one), and says so (PanelsFollow)
  const panelsFollow = !!focusedTab && treeless(focusedTab);
  if (focusedTab && !panelsFollow) processFocus.current = [focusedTab, ...processFocus.current.filter((id) => id !== focusedTab)];
  processFocus.current = processFocus.current.filter((id) => allTabs.includes(id));
  const panelTab = !panelsFollow ? focusedTab : processFocus.current[0] ?? allTabs.find((id) => !treeless(id)) ?? null;
  const followNote = panelsFollow && html`<${PanelsFollow} title=${panelTab ? titles[panelTab] ?? null : undefined} />`;
  function startSideResize(e) {
    e.preventDefault();
    const start = e.clientX, width = sideShown;
    const move = (ev) => setSideWidth(sideWidthFor(width + start - ev.clientX, leftOpen ? leftShown : 0));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }
  function startLeftResize(e) {
    e.preventDefault();
    const start = e.clientX, width = leftShown;
    // Not wider than what the side panel leaves, at the width it has (dragging one panel does not shrink the other)
    const move = (ev) => setLeftWidth(clampWidth(width + ev.clientX - start, PANEL_MIN.left,
      panelRoom(true, sideVisible) - (sideVisible ? sideShown : 0)));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }
  // The search of the top bar: the process becomes the process of the tab in focus, or opens in a new tab
  const pickRoot = (r, inTab) => {
    if (inTab || !ROOT_SETTERS[focusedTab] || treeless(focusedTab)) openTab(focus, startFrom(focusedTab, r));
    else ROOT_SETTERS[focusedTab](r);
  };
  const renderPane = (pane) => html`
    <${PaneView} key=${pane.id} pane=${pane} focused=${pane.id === focus} multiple=${allTabs.length > 1 && panesOf(layout).length > 1}
      titles=${titles} dragging=${dragging} drop=${drop} setDrop=${setDrop} actions=${actions}>
      ${pane.tabs.map((id) => flowOf(id) ? html`
        <${FlowTab} key=${id} id=${id} info=${info} initial=${initialOf(id)} visible=${id === pane.active} onSave=${onSave} onTitle=${onTitle}
          onOpenTab=${(from, root, place) => openTab(place?.pane ?? paneOfTab(layout, from).id, startFrom(from, root), place?.zone)} />` : modelOf(id) ? html`
        <${ModelTab} key=${id} id=${id} info=${info} initial=${initialOf(id)} visible=${id === pane.active} onSave=${onSave} onTitle=${onTitle}
          onOpenTab=${(from, root, place) => openTab(place?.pane ?? paneOfTab(layout, from).id, startFrom(from, root), place?.zone)} />` : html`
        <${FUView} key=${id} id=${id} info=${info} initial=${initialOf(id)} visible=${id === pane.active}
          onSave=${onSave} onTitle=${onTitle}
          onNewTab=${(from) => actions.add(paneOfTab(layout, from).id)} onCloseTab=${(from) => actions.close(from)}
          onOpenTab=${(from, root, place) => openTab(place?.pane ?? paneOfTab(layout, from).id, startFrom(from, root), place?.zone)} />`)}
    <//>`;
  return html`
    <${EditorContext.Provider} value=${editor}>
    <${LensContext.Provider} value=${lensApi}>
    <div class=${`app ${stale ? "stale" : ""} ${editing ? "editing" : ""} ${comparing ? "comparing" : ""} ${showStaged ? "has-staged" : ""}`}>
      ${topBar(comparing ? null : html`<${FunctionalUnit} root=${focusedRoot?.index === undefined ? null : focusedRoot} fields=${info.query_fields} onSelect=${pickRoot} />`)}
      ${showStaged && html`
        <div class="staged-warning" role="status">
          <span>⚠ ${pending.length} staged change${pending.length === 1 ? "" : "s"} not applied to the project yet.</span>
          <button onClick=${() => { setMode("edit"); setSideOpen(true); }}>Review and apply in Edit mode</button>
        </div>`}
      <div class="body">
        ${!leftOpen && html`<button class="side-reopen left" onClick=${() => setLeftOpen(true)} title="Show the Processes, Methods and Rules panel (Ctrl/⌘ + ←)" aria-label="Show the Processes, Methods and Rules panel"><${SidePanelIcon} left /></button>`}
        <aside class="leftbar" style=${{ width: `${leftShown}px`, display: leftOpen ? "" : "none" }}>
          <${LensPanel} info=${info} lens=${lensApi} tab=${leftTab} setTab=${setLeftTab} tabs=${allTabs} focusedTab=${panelTab} follow=${followNote} comparing=${comparing}
            view=${TAB_API[focusedTab] || Object.values(TAB_API)[0]} compareTabs=${compareTabs} flowTabs=${flowTabs} compare=${compare} setCompare=${setCompare} onCollapse=${() => setLeftOpen(false)}
            adder=${origin && DATABASE ? { origin, onAdded: () => request("/api/info").then((i) => { clearAnswers(); setInfo(i); }).catch((e) => setError(e.message)) } : null}
            onCategory=${(category, x, y) => CATEGORY_OPENERS[focusedTab]?.(category, x, y)}
            windows=${{ panes: panesOf(layout), current: focus, select: actions.select, focus: actions.focus, close: actions.close, drag: actions.drag, move: actions.move,
              closePane: actions.closePane, openProcess: actions.openProcess, reopen: actions.reopen, dragging,
              history: closedStack.current.map((list, at) => {
                const roots = list.map((snap) => snap.state?.root || (snap.state?.flow && { name: snap.state.flow.name, location: snap.state.flow.place })
                  || (snap.state?.model && { name: modelTitle(info, snap.state.model.method).name, location: "model" })).filter(Boolean);
                return { at, label: list.length > 1 ? `${list.length} tabs: ${roots.map((r) => r.name).join(", ")}` : roots[0] ? `${roots[0].name} (${roots[0].location})` : "An empty tab" };
              }).reverse() }} />
        </aside>
        ${leftOpen && html`<div class="side-resizer" onPointerDown=${startLeftResize} title="Drag to resize"></div>`}
        <div class="workspace" style=${{ display: comparing ? "none" : "" }}><${LayoutView} node=${layout} renderPane=${renderPane}
          onResize=${(id, sizes) => setLayout((l) => resizeSplit(l, id, sizes))} /></div>
        ${comparing && html`<${ComparisonView} info=${info} compare=${compare} setCompare=${setCompare}
          tabs=${compareTabs} />`}
        ${editing && (sideOpen
          ? html`<div class="side-resizer" onPointerDown=${startSideResize} title="Drag to resize"></div>`
          : html`<button class="side-reopen" onClick=${() => setSideOpen(true)} title="Show the Edit panel (Ctrl/⌘ + →)" aria-label="Show the Edit panel"><${SidePanelIcon} /></button>`)}
        ${editing && html`<aside class="sidebar" style=${{ width: `${sideShown}px`, display: sideOpen ? "" : "none" }}>
          <${EditPanel} origin=${origin} dataset=${dataset} pending=${pending} affected=${pendingView.affected}
            locked=${pendingView.locked} outdated=${outdated} onForm=${setDatasetOpen} stage=${stage} unstage=${unstage} unstageAll=${unstageAll}
            onApplied=${() => { refreshEditor(); refreshDatabases(); }} onRebuild=${rebuild} onCollapse=${() => setSideOpen(false)} />
        </aside>`}
      </div>
      <div class="bottom">
      ${toast && html`<div class="toast" role="status">
        <span>${toast.message}</span>
        <button onClick=${reopenClosed}>Undo</button>
        <button class="link" onClick=${() => setToast(null)} aria-label="Dismiss">×</button>
      </div>`}
      <footer class="status-bar">
        <${FocusedCard} info=${info} title=${titles[focusedTab]} />
        <button class="status-text" onClick=${() => lensApi.show("methods")} title=${methodLabel(info.methods[lens.method])}>
          ${methodSummary(info, lens.method, lens.chartMethods)}</button>
        <button class="status-text" onClick=${() => lensApi.show("groups")} title="The rule set shared by all tabs">Rules: ${lens.set} · ${lensApi.rules.filter((r) => !r.disabled).length}</button>
        <span class="spacer"></span>
        <button class="status-button" onClick=${() => setSettingsOpen(true)} aria-label="Settings" title="Settings: the colours (rules, types, locations…)"><${PaletteIcon} /></button>
        <button class="status-button" onClick=${() => setShortcutsOpen(true)} aria-label="Shortcuts" title="Keyboard shortcuts"><${KeyboardIcon} /></button>
        <a class="status-button" href=${SOURCE_URL} target="_blank" rel="noopener noreferrer" aria-label="Source code"
          title="Source code of LCA Explorer, free software under the GNU AGPL 3.0 or later"><${SourceIcon} /></a>
      </footer>
      </div>
    </div>
    <//>
    <//>`;
}

render(html`<${App} />`, document.getElementById("app"));
