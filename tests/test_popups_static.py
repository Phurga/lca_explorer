"""Checks of the pop-ups in the page's script (no browser here): what an API test cannot see."""

import shutil
import subprocess

import pytest

from lca_viewer.server import STATIC

APP = (STATIC / "app.js").read_text(encoding="utf-8")


def body(name: str) -> str:
    """The source of a top-level function of app.js, up to the next one."""
    start = APP.index(f"\nfunction {name}(")
    end = APP.find("\nfunction ", start + 1)
    return APP[start:end]


def test_a_flow_tab_opens_the_model_popup_of_the_methods_panel():
    # The Methods panel opens the model pop-up through the opener of the tab in focus: a flow tab had none, so the
    # pop-up did not open while a flow had the focus
    assert "CATEGORY_OPENERS[focusedTab]" in APP
    assert "CATEGORY_OPENERS[id] =" in body("FlowTab")
    assert "CATEGORY_OPENERS[id] =" in body("FUView")


def test_flow_popup_opens_the_model_popup_of_its_characterisation():
    assert "onCategory" in body("FlowPanels")
    assert "onCategory=${(m) => follow({ category: m })}" in body("objectPopup")


def test_the_lists_of_the_popups_are_one_component():
    for name in ["FlowPanels", "CategoryView", "InfoPopup", "UnlinkedFactors"]:
        assert "<${PopupList}" in body(name), name
        assert 'class="flows' not in body(name), name  # the tables each panel drew itself
    assert "SortableTable" not in APP


def test_the_model_card_is_one_component_for_the_popup_and_the_tab():
    # The pop-up and the tab draw the same card; each gives its own chrome (a window, a pane)
    assert "<${CategoryView}" in body("CategoryPopup")
    assert "<${CategoryView}" in body("ModelTab")
    assert "<${PopupWindow}" not in body("ModelTab")
    assert "<${PopupWindow}" not in body("CategoryView")


def test_a_model_popup_docks_as_a_tab():
    popup = body("CategoryPopup")
    assert "onDock=${asTab}" in popup
    assert '"Open in new tab"' in popup
    assert "{ model: state }" in popup
    # every place a model pop-up opens from (process tabs, flow tabs, model tabs) can make it a tab
    assert "onOpenTab=${onOpenModelTab}" in body("objectPopup")
    for name in ["FUView", "FlowTab", "ModelTab"]:
        assert "onOpenModelTab:" in body(name), name


def test_a_model_tab_has_its_own_popups_and_answers_the_methods_panel():
    tab = body("ModelTab")
    assert "usePopupStack()" in tab
    assert "objectPopup(p," in tab
    assert "CATEGORY_OPENERS[id] =" in tab


def test_the_app_tells_model_tabs_apart():
    app = body("App")
    assert "const modelOf = (id)" in app
    assert "<${ModelTab}" in app
    assert "root?.model?.method !== undefined ? { model: root.model }" in app  # startFrom
    assert "s?.model ? modelTitle(i, s.model.method)" in app  # the titles of a saved workspace
    assert "snap.state?.model" in app  # the open history
    assert "treeless(focusedTab)" in app  # the search of the top bar opens a process in a new tab
    # titles of model tabs: { name, model } (the status bar shows "Model · ‹title›")
    assert "name: info.methods[method] ? methodTitle(info.methods[method]) : \"Model\", model: method" in APP
    # model tabs are not compared: only tabs with a process (``root``)
    assert "allTabs.filter((id) => states.current[id]?.root)" in app


def test_model_cards_are_called_model_cards_in_the_ui():
    assert "Its model pop-up" not in APP
    assert "its model pop-up`" not in APP
    assert "method card" not in APP.lower()
    assert 'label="Model card"' in body("CategoryPopup")


def test_right_click_on_a_tree_flow_row_opens_its_popup_not_a_menu():
    assert "flowMenu" not in body("FUView")


def test_view_options_open_from_each_process_of_the_processes_panel():
    # The view options left the side panel: each process row opens those of its tab, drawn by the tab on the page
    assert "VIEW_OPTIONS_OPENERS[t.id]" in body("ProcessesPanel")
    assert "VIEW_OPTIONS_OPENERS[id] =" in body("FUView")
    assert "<${ViewOptionsPopup}" in body("FUView") and "<${PagePortal}>" in body("FUView")
    assert "VIEW_OPTIONS_OPENERS" not in body("FlowTab")  # flow tabs have no view options
    assert '["view", "Explore"]' in APP and "<${SidePanelTabs}" in body("FUView") and "header=${viewOptions}" not in body("FUView")
    # key v: the view options of the tab in focus
    assert 'new CustomEvent("lca:custom-view", { detail: { id } })' in body("App")


def test_a_process_card_has_explore_then_exchanges():
    # The tree (a tab) and the explorer (a pop-up) are the Explore panel; the dataset's exchanges come second
    info = body("InfoPopup")
    assert 'label: "Process" }' not in info and "`Exchanges · ${" in info
    assert info.index('{ id: "explore", label: "Explore" }') < info.index('{ id: "exchanges", label:')
    assert "<${ProcessExchanges}" in info


def test_the_exchanges_panel_follows_the_sections_of_the_process_form():
    panel = body("ProcessExchanges")
    assert "<${PopupList}" in panel and "sections=${sections}" in panel
    assert 'const EXCHANGE_SECTIONS = [["product", "Products"], ...DATASET_SECTIONS' in APP


def test_the_exchanges_panel_edits_nothing():
    # Edit mode stays in the top bar: no Edit / Done editing / Edit process… here, no amount edited in place
    panel = body("ProcessExchanges")
    for gone in ("Done editing", "Edit process…", "enterEdit", "Editable", "patchExchange", "quickEdit", "EditorContext"):
        assert gone not in panel


def test_edit_mode_is_entered_from_the_top_bar():
    app = body("App")
    switch = app[app.index('<span class="mode-switch"'):]
    switch = switch[:switch.index("</span>")]
    assert 'label="View"' in switch and 'label="Compare"' in switch and 'label="Edit"' in switch
    assert '`has-key ${editing ? "primary" : ""}`' in switch and "disabled=${!origin}" in switch
    assert "(${pending.length} staged)" in switch  # the count of staged changes, on the button
    assert "const editor = { editing, origin, pending, locked: pendingView.locked, stale, stage, openForm: setDatasetOpen };" in app
    assert "enterEdit" not in APP and "ctx.setMode" not in APP
    assert "Review and apply in Edit mode" in app  # the staged-changes warning still enters it


def test_the_exchanges_panel_shows_comments_in_a_column():
    panel = body("ProcessExchanges")
    assert '{ key: "comment", label: "Comment"' in panel and "value: (r) => oneLine(r.comment)" in panel
    assert "tip: (r) => r.comment?.trim()" in panel  # all of it in the tooltip
    assert "comment-mark" not in panel
    assert 'const DEFAULT_EXCHANGE_COLUMNS = ["place", "amount", "unit", "comment"];' in APP


def test_the_exchanges_panel_has_a_columns_menu():
    panel = body("ProcessExchanges")
    assert "onHeaderMenu=${" in panel and "<${ColumnMenu}" in panel and "options=${EXCHANGE_PANEL_COLUMNS}" in panel
    assert 'savePreference("exchangeColumns"' in panel and 'loadPreference("exchangeColumns"' in body("loadExchangeColumns")
    columns = APP[APP.index("const EXCHANGE_PANEL_COLUMNS = ["):]
    columns = columns[:columns.index("];")]
    for id_ in ("place", "amount", "unit", "comment", "uncertainty", "pedigree", "formula"):
        assert f'{{ id: "{id_}"' in columns
    # the menu takes other columns than the tree's; the tree's stays the default
    assert "function ColumnMenu({ at, columns, onChange, onClose, options = COLUMNS })" in APP


def test_popup_list_header_menu_replaces_the_filter_row_only_when_asked():
    plist = body("PopupList")
    assert "if (onHeaderMenu) onHeaderMenu(e); else setFilters((f) => (f ? null : {}));" in plist
    assert '"Right-click: filter by column."' in plist  # the default, elsewhere


def test_the_exchanges_panel_has_the_keys_of_the_popup_lists():
    plist = body("PopupList")
    # Enter: the process of the tab; Ctrl/⌘+Enter: of a new tab
    assert "onUse(r, inTab)" in plist and 'const inTab = (e.ctrlKey || e.metaKey) && !e.altKey && e.key === "Enter";' in plist
    assert "onUse=${(r, inTab) => r.kind === \"process\" && onUse(r, inTab)}" in body("ProcessExchanges")
    # in a tab (a docked card): the card has the keys of the pop-ups on its panels, ↑ on the first row goes back to the
    # active panel tab, ↓ on the tabs enters the panel, and the tree leaves those keys to the card
    info = body("InfoPopup")
    assert "onKeyDown=${docked ? dockedKeys : popupKeys(" in info and "toTabs: () => tabsBar.current" in info
    assert 'const first = body.current?.querySelector("[data-nav]");' in info
    assert "if (toTabs) toTabs(); else box.focus(" in body("popupKeys")
    assert '<div class="docked-panels" onKeyDown=${onKeyDown}>' in body("DockedShell")
    assert ".docked-panels > .info-body" in body("FUView")


def test_information_names_the_unit_and_the_location():
    info = body("InfoPopup")
    assert "unitWithQuantity(info.unit)" in info and "info.location_name" in info
    assert '<tr><th>Location</th><td>${info.location ? html`<span class="loc-chip">' not in info


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_side_panel_tabs_have_short_names_and_icons_when_narrow():
    # Explore, Process, Flow, Uncharacterised, Rules: the side panel says they are contributions
    assert ('const SIDE_PANELS = [["view", "Explore"], ["chart", "Process"], ["flows", "Flow"], ["missing", "Uncharacterised"], '
            '["global", "Rules"]];') in APP
    for old in ("Explore view", "Process contribution", "Flow contribution", "Rules contributions"):
        assert f'"{old}"' not in APP
    tabs = body("SidePanelTabs")
    assert "new ResizeObserver(fit)" in tabs and "el.scrollWidth > el.clientWidth" in tabs
    assert "aria-label=${compact ? label : undefined}" in tabs and "<${SidePanelTabIcon} panel=${key} />" in tabs
    assert all(f"  {key}: \"M" in APP for key in ("view", "chart", "flows", "missing", "global"))


def test_the_panels_are_fitted_to_the_window():
    # The workspace keeps its minimum; the left panel leaves the side panel its own, the side panel takes what is left
    assert "const WORKSPACE_MIN = 360;" in APP
    assert "panelRoom(true, sideOpen) - (sideOpen ? PANEL_MIN.side : 0)" in APP
    assert "panelRoom(left > 0, true) - left" in APP
    app = body("App")
    assert "sideWidthFor(sideWidth, leftOpen ? leftShown : 0)" in app
    assert "sideWidthFor(width + start - ev.clientX, leftOpen ? leftShown : 0)" in app
    assert "panelRoom(true, sideVisible) - (sideVisible ? sideShown : 0)" in app


def test_a_flow_or_model_tab_shows_the_panels_of_the_last_process_tab():
    app = body("App")
    assert "processFocus.current[0] ?? allTabs.find((id) => !treeless(id)) ?? null" in app
    assert 'id=${`side-${id}`} class="side-slot" style=${{ display: id === panelTab && !editing' in app
    assert "focusedTab=${panelTab} follow=${followNote(null)}" in app
    assert "${!comparing && follow}" in body("LensPanel")
    follow = body("PanelsFollow")
    assert "the last process in focus" in follow and "These panels follow a process tab" in follow


def test_app_js_parses(tmp_path):
    copy = tmp_path / "app.mjs"  # an ES module (its imports)
    copy.write_text(APP, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_processes_panel_marks_compare_and_baseline_in_one_box():
    # One box per tab, as the methods of the Methods panel: ✓ compared, ★ the baseline (no tick and radio any more)
    panel = body("ProcessesPanel")
    assert "<${CompareMark}" in panel and "compareStep(tabs, compare, t.id)" in panel
    assert 'type="radio"' not in panel and 'type="checkbox"' not in panel
    assert "method-mark compare-mark" in body("CompareMark")


def test_name_display_is_a_setting_of_the_lens_not_of_the_tabs():
    assert "naming: namingOf(stored?.naming)" in body("initLens")
    assert "naming: lens.naming" in body("saveLens")
    assert "<${NameDisplay}" in body("ProcessesPanel")
    assert 'aria-label="Names"' not in body("ViewBar") and "RecipeEditor" not in body("ViewBar")
    # the readers of a tab's view get the names of the lens
    assert "viewNaming(lens?.naming" in body("FUView")
    # the titles of the cards follow it
    assert "processTitle(info)" in body("InfoPopup")
    assert "tabTitleNames(titles[id])" in body("PaneView")
    assert "processNames(t.state.root)" in body("compareLabels")
    assert "flowTitleNames(info)" in body("FlowHead")


def run_node(script: str) -> str:
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_compare_mark_steps_like_the_method_boxes():
    script = body("compareSelection") + body("compareStep") + """
const compareLabels = () => ({ short: {}, full: {}, common: null });
const tabs = [{ id: "a" }, { id: "b" }, { id: "c" }];
const out = [
  compareStep(tabs, {}, "b"),                                  // ticked -> baseline (a stays compared)
  compareStep(tabs, { chosen: ["a", "b"], baseline: "a" }, "c"), // off -> compared
  compareStep(tabs, { chosen: ["a", "c"], baseline: "c" }, "c"), // baseline -> off: the first other becomes the baseline
  compareStep(tabs, { chosen: ["b"], baseline: "b" }, "b"),      // the baseline alone stays
];
console.log(JSON.stringify(out));
"""
    import json

    assert json.loads(run_node(script)) == [
        {"chosen": ["a", "b", "c"], "baseline": "b"},
        {"chosen": ["a", "b", "c"], "baseline": "a"},
        {"chosen": ["a"], "baseline": "a"},
        {},
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_names_default_to_the_product_and_migrate_the_old_preference():
    start = APP.index("const RECIPE_TOKENS")
    end = APP.index("\nfunction chartItems(")
    script = APP[start:end] + """
const compartmentOf = (flow) => [flow.compartment, flow.subcompartment].filter(Boolean).join(", ");
const RECIPE_PREFERENCES = { main: "recipeMain", secondary: "recipeSecondary", flowMain: "recipeFlowMain", flowSecondary: "recipeFlowSecondary" };
let prefs = {};
const loadPreference = (k, fallback) => (k in prefs ? prefs[k] : fallback);
const market = { name: "market for electricity, high voltage", reference_product: "electricity, high voltage", location: "CH" };
const out = {
  product: processNames(market),
  activity: processNames(market, { ...DEFAULT_NAMING, process: "activity" }),
  view: viewNaming(DEFAULT_NAMING).naming,
  full: viewNaming({ ...DEFAULT_NAMING, process: "activity" }).naming,
  flow: flowTitleNames({ name: "Carbon dioxide", place: "air, urban air" }),
  short: flowTitleNames({ name: "Carbon dioxide", compartment: "air", subcompartment: "" }, { ...DEFAULT_NAMING, flow: "short" }),
  fresh: namingOf(undefined),
};
prefs = { naming: "short" }; out.wasShort = namingOf(undefined);
prefs = { naming: "custom", recipeMain: "{name}" }; out.wasCustom = namingOf(undefined);
out.stored = namingOf({ process: "activity", flow: "nonsense" });
console.log(JSON.stringify(out));
"""
    import json

    out = json.loads(run_node(script))
    assert out["product"] == {"main": "electricity, high voltage", "qual": "CH"}
    assert out["activity"] == {"main": "market for electricity, high voltage", "qual": "CH"}
    assert out["view"] == "custom" and out["full"] == "full"
    assert out["flow"] == {"main": "Carbon dioxide", "qual": "air, urban air"}
    assert out["short"] == {"main": "Carbon dioxide", "qual": ""}
    assert (out["fresh"]["process"], out["fresh"]["flow"]) == ("product", "full")
    assert (out["wasShort"]["process"], out["wasShort"]["flow"]) == ("product", "short")
    assert out["wasCustom"]["process"] == "custom" and out["wasCustom"]["recipe"]["main"] == "{name}"
    assert (out["stored"]["process"], out["stored"]["flow"]) == ("activity", "full")
