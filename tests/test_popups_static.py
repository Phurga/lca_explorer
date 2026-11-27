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
    for name in ["FlowPanels", "CategoryPopup", "InfoPopup", "UnlinkedFactors"]:
        assert "<${PopupList}" in body(name), name
        assert 'class="flows' not in body(name), name  # the tables each panel drew itself
    assert "SortableTable" not in APP


def test_right_click_on_a_tree_flow_row_opens_its_popup_not_a_menu():
    assert "flowMenu" not in body("FUView")


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


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_app_js_parses(tmp_path):
    copy = tmp_path / "app.mjs"  # an ES module (its imports)
    copy.write_text(APP, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
