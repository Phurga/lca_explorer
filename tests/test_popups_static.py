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
    assert '["view", "Explore view"]' in body("FUView") and "header=${viewOptions}" not in body("FUView")
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
    # amounts editable in place in Edit mode, as in the exchange pop-up; Edit mode entered from here
    assert "patchExchange(r.input, r.amount, t)" in panel and "ctx.enterEdit()" in panel
    assert "Done editing" in panel and "Edit process…" in panel


def test_edit_mode_is_entered_from_the_card_not_the_top_bar():
    app = body("App")
    switch = app[app.index('<span class="mode-switch"'):]
    switch = switch[:switch.index("</span>")]
    assert 'label="View"' in switch and 'label="Compare"' in switch and 'label="Edit"' not in switch
    assert "openForm: setDatasetOpen, setMode," in app and "enterEdit: () =>" in app  # the card's buttons (EditorContext)
    assert "Review and apply in Edit mode" in app  # the staged-changes warning still enters it


def test_information_names_the_unit_and_the_location():
    info = body("InfoPopup")
    assert "unitWithQuantity(info.unit)" in info and "info.location_name" in info
    assert '<tr><th>Location</th><td>${info.location ? html`<span class="loc-chip">' not in info


def test_pin_is_an_icon_of_the_header_not_a_menu_item():
    window = body("PopupWindow")
    assert "<${PinButton} pinned=${win.pinned} onPin=${win.onPin} />" in window
    assert 'label: win.pinned ? "Unpin" : "Pin"' not in APP and "pin-mark" not in APP
    assert "menu.length > 0" in window  # no empty ⋯ menu once Pin left it
    assert "aria-pressed=${!!pinned}" in APP and "<${PinIcon} filled=${!!pinned} />" in APP


def test_row_gestures_are_the_same_everywhere():
    # A click selects, a right-click is Space (its pop-up), a double-click is Enter (used in the tab), Ctrl/⌘ +
    # double-click is Ctrl/⌘+Enter (a new tab): on the rows of the pop-up lists, of ProcessRow, and of the tree's list,
    # graph and Sankey
    for name in ["PopupList", "ProcessRow", "Tree", "TreeGraph", "sankeyMarks"]:
        assert "dblUse(" in body(name), name
    assert "onDblClick=${() => r.expandable" not in APP  # the graph's double-click expanded; ± does
    assert "onDblClick=${() => expandable && setOpen(!open)}" not in body("ProcessRow")
    assert 'href="#"' not in body("NameCell")  # the name is no link: a click selects the row
    assert "onUse(r, inTab)" in body("PopupList")  # Enter and Ctrl/⌘+Enter on a row of a list
    assert "return useRow(row, inTab);" in body("FUView")  # Enter and the double-click of the tree are one
    assert 'row.kind === "flow" && row.flow && tabUse' in body("FUView")  # a flow of the tree: a flow tab


def test_each_tab_says_what_a_double_click_does_there():
    for name in ["FUView", "FlowTab", "ModelTab"]:
        assert "<${TabUseContext.Provider} value=" in body(name), name
    assert "const [method, setMethod] = useState(start.method);" in body("ModelTab")  # a model replaces the tab's
    assert "else if (target.flow !== current) setCurrent(target.flow);" in body("FlowTab")  # a flow replaces the tab's
    for name in ["FlowPanels", "CategoryView", "InfoPopup"]:
        assert "useContext(TabUseContext)" in body(name), name
    assert "use.model(f.method, inTab)" in body("FlowPanels") and "use.process(p.index, inTab)" in body("FlowPanels")
    assert "use.model(t.method, inTab)" in body("InfoPopup")
    assert "use.flow({ flow: it.flows[0] }, inTab)" in body("CategoryView")


def test_a_right_click_on_a_method_opens_its_model_card():
    assert "onContextMenu=${onCategory ? (e) => { e.preventDefault(); onCategory(i, e.clientX, e.clientY); }" in body("MethodTree")


def test_the_flow_characterisation_has_no_rank_column():
    assert 'key: "rank"' not in body("FlowPanels") and "ordinal(" not in APP


def test_the_focused_card_of_the_status_bar_is_plain_text():
    card = body("FocusedCard")
    assert "<button" not in card and "onClick" not in card and '<span class="status-text focused-card"' in card
    assert "<${FocusedCard} info=${info} title=${titles[focusedTab]} />" in body("App")


def test_the_shortcuts_list_the_mouse_gestures():
    shortcuts = APP[APP.index("const SHORTCUTS = ["):APP.index("\nfunction Modal(")]
    assert '["Double-click", "Use it in the tab (as Enter)"]' in shortcuts
    assert '["Ctrl/⌘ + double-click", "Open it in a new tab (as Ctrl/⌘ + Enter)"]' in shortcuts
    assert '["Right-click", "Its pop-up (as Space)"]' in shortcuts


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_app_js_parses(tmp_path):
    copy = tmp_path / "app.mjs"  # an ES module (its imports)
    copy.write_text(APP, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
