# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The top bar, the status bar and the Projects dialog in the page's script (no browser here)."""

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


def status_bar() -> str:
    start = APP.index('<footer class="status-bar">')
    return APP[start:APP.index("</footer>", start)]


def top_bar() -> str:
    start = APP.index('<header class="top-bar">')
    return APP[start:APP.index("</header>", start)]


def test_the_source_link_is_an_icon_of_the_status_bar():
    assert "SOURCE_URL" in status_bar() and "<${SourceIcon}" in status_bar()
    assert "SOURCE_URL" not in top_bar() and "source-link" not in APP


def test_no_shut_down_button_closing_the_last_tab_stops_the_app():
    assert "ShutDownButton" not in APP and "/api/shutdown" not in APP
    assert "/api/tab/bye" in APP and "/api/tab/alive" in APP


def test_storage_opens_from_the_projects_dialog():
    assert "Storage" not in top_bar()
    assert "<${StorageButton}" in body("LoadDatabase") and "<${StorageDialog}" in body("LoadDatabase")
    assert "storageOpen" not in body("App")


def test_the_status_bar_names_the_focused_card_and_has_no_left_panel_button():
    # the focused card is plain text: it no longer opens the pop-up of the tab (its row of the Processes panel does)
    assert "<${FocusedCard}" in status_bar() and "POPUP_OPENERS[focusedTab]" not in status_bar()
    assert "SidePanelIcon" not in status_bar()
    label = body("focusedCardLabel")
    for kind in ["Model ·", "methodTitle(method)", '"Flow"', '"Process"', "tabTitleNames(title)"]:  # named as the tab is
        assert kind in label, kind


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_focused_card_labels(tmp_path):
    script = tmp_path / "label.mjs"
    script.write_text(body("focusedCardLabel") + """
const methodTitle = (m) => m.name.join(" | ");
// the Name display's default: a process by its product, a flow by its substance, the location or compartment in grey
const tabTitleNames = (t) => ({ main: t.flow === undefined ? t.reference_product || t.name : t.name, qual: t.location || "" });
const info = { methods: [{ name: ["EF v3.1", "climate change"] }] };
console.log(JSON.stringify([
  focusedCardLabel(info, { name: "steel production, converter", reference_product: "steel", location: "GLO", index: 3 }),
  focusedCardLabel(info, { name: "Carbon dioxide", location: "air", flow: 7 }),
  focusedCardLabel(info, { model: 0 }),
  focusedCardLabel(info, null),
]));
""", encoding="utf-8")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ('["Process · steel (GLO)","Flow · Carbon dioxide (air)",'
                                     '"Model · EF v3.1 | climate change",null]')
