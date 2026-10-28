"""Importing a SimaPro CSV linked to ecoinvent, on toy data (tests/fixtures/simapro_toy.csv).

The fixture mimics an Agribalyse export: a crop, a multi-output dairy farm (economic allocation),
a waste treatment, and inputs from ecoinvent under their SimaPro names. The Brightway tests need
bw2data, bw2io, bw2calc and bw_simapro_csv (``pip install -e ".[brightway]"``) and are skipped otherwise.
"""

from pathlib import Path

import numpy as np
import pytest

from lca_viewer import importing

FIXTURE = Path(__file__).parent / "fixtures" / "simapro_toy.csv"
EI = "ecoinvent-3.9.1-cutoff"
METHOD = ("toy", "climate change")


def test_default_migrations():
    assert importing.default_migrations([EI]) == ["simapro-ecoinvent-3.9.1-cutoff"]
    assert importing.default_migrations(["ecoinvent-3.10-cutoff", "biosphere3"]) == ["simapro-ecoinvent-3.10-cutoff"]
    assert importing.default_migrations(["my-database"]) == []


def test_split_name_geography():
    data = [
        {"name": "Soft wheat grain, at farm gate {FR} U", "location": "France",
         "reference product": "Soft wheat grain, at farm gate {FR} U"},
        {"name": "Electricity, low voltage {FR}| market for electricity, low voltage | Cut-off, U"},
        {"name": "Dairy farm", "reference product": "Cow milk {FR} U"},
    ]
    importing.split_name_geography(data)
    assert data[0]["name"] == "Soft wheat grain, at farm gate"
    assert data[0]["location"] == "FR"
    assert data[0]["reference product"] == "Soft wheat grain, at farm gate"
    assert data[0]["simapro name"] == "Soft wheat grain, at farm gate {FR} U"
    assert "location" not in data[1]  # ecoinvent-style names are left alone
    assert data[2]["reference product"] == "Cow milk"


def test_link_internal_by_product_name():
    data = [
        {"type": "product", "name": "Wheat {FR} U", "unit": "kg", "location": "France", "database": "d", "code": "p"},
        {"type": "process", "name": "Bread", "exchanges": [
            {"type": "technosphere", "name": "Wheat {FR} U", "unit": "kg", "amount": 1},
            {"type": "technosphere", "name": "Wheat {FR} U", "unit": "t", "amount": 1},
        ]},
    ]
    importing.link_internal_by_product_name(data)
    assert data[1]["exchanges"][0]["input"] == ("d", "p")
    assert "input" not in data[1]["exchanges"][1]  # other unit: left for the report


def test_unlinked_report_skips_placeholders():
    data = [{"name": "Treatment", "exchanges": [
        {"type": "production", "name": "Waste", "amount": 0},
        {"type": "technosphere", "name": "Diesel", "unit": "kg", "amount": 1},
        {"type": "technosphere", "name": "Diesel", "unit": "kg", "amount": 2},
        {"type": "biosphere", "name": "CO2", "categories": ("air",), "unit": "kg", "amount": 1, "input": ("b", "c")},
    ]}]
    rows = importing.unlinked_exchanges(data)
    assert [(r["name"], r["exchanges"]) for r in rows] == [("Diesel", 2)]
    importing.drop_unlinked(data)
    assert [e["name"] for e in data[0]["exchanges"]] == ["Waste", "CO2"]


# ---------------------------------------------------------------- with Brightway


@pytest.fixture
def toy_project(tmp_path):
    """Temporary Brightway project with a toy biosphere, a toy "ecoinvent" and one method."""
    pytest.importorskip("bw2io")
    pytest.importorskip("bw_simapro_csv")
    import bw2data as bd
    from bw2data.tests import _close_sqlite_handles

    bd.config.dont_warn = True
    bd.config.is_test = True
    _close_sqlite_handles()
    bd.projects.change_base_directories(base_dir=tmp_path, base_logs_dir=tmp_path, project_name="toy", update=False)
    bd.projects._is_temp_dir = True

    flows = {
        "co2": ("Carbon dioxide, fossil", ("air",)),
        "n2o": ("Dinitrogen monoxide", ("air", "non-urban air or from high stacks")),
        "ch4": ("Methane, non-fossil", ("air",)),
        "nh3": ("Ammonia", ("air",)),
    }
    bd.Database("biosphere3").write({
        ("biosphere3", code): {"name": name, "categories": categories, "unit": "kilogram", "type": "emission"}
        for code, (name, categories) in flows.items()
    })

    def market(code, name, product, location, unit, co2):
        return (EI, code), {
            "name": name, "reference product": product, "location": location, "unit": unit, "type": "process",
            "exchanges": [
                {"input": (EI, code), "amount": 1, "type": "production"},
                {"input": ("biosphere3", "co2"), "amount": co2, "type": "biosphere"},
            ],
        }

    bd.Database(EI).write(dict([
        market("diesel", "market for diesel", "diesel", "Europe without Switzerland", "kilogram", 3.0),
        market("elec", "market for electricity, low voltage", "electricity, low voltage", "FR", "kilowatt hour", 0.08),
        market("water", "market for tap water", "tap water", "Europe without Switzerland", "kilogram", 0.0004),
    ]))
    method = bd.Method(METHOD)
    method.register(unit="kg CO2-eq")
    method.write([(("biosphere3", "co2"), 1), (("biosphere3", "ch4"), 30), (("biosphere3", "n2o"), 270)])
    yield bd.projects.current
    _close_sqlite_handles()


def test_import_links_to_ecoinvent_and_builds_square_model(toy_project, tmp_path):
    import bw2calc as bc
    import bw2data as bd

    from lca_viewer.brightway import from_brightway

    result = importing.import_simapro_csv(
        toy_project, FIXTURE, "toy-agb", link_to=[EI], report=tmp_path / "unlinked.csv"
    )
    assert result["unlinked"] == 0
    assert not (tmp_path / "unlinked.csv").exists()

    model = from_brightway(toy_project, "toy-agb", [METHOD])
    acts = model.activities
    # 3 ecoinvent markets + wheat + dairy farm allocated to milk and to cull cow + manure treatment
    assert model.size == 7
    assert sorted(set(acts["database"])) == sorted(["toy-agb", EI])
    wheat = acts.index[acts["name"] == "Soft wheat grain, conventional, at farm gate"]
    assert list(acts.loc[wheat, "location"]) == ["FR"]
    assert not acts["name"].str.contains("{FR}", regex=False).any()  # allocated processes too
    manure = acts.index[acts["name"] == "Manure, treatment"][0]
    assert model.diagonal[manure] < 0  # waste treatment: negative production, as in ecoinvent
    # The wheat and the dairy farm both send manure to treatment ("Waste to treatment", written by
    # bw_simapro_csv as non-functional production exchanges): each keeps its own product
    assert list(acts.loc[wheat, "reference_product"]) == ["Soft wheat grain, conventional, at farm gate"]
    assert model.diagonal[wheat[0]] > 0

    # Same scores as Brightway, for the milk produced by the allocated dairy farm
    milk_product = bd.get_node(database="toy-agb", name="Cow milk, at farm gate", type="product")
    lca = bc.LCA({milk_product: 1}, method=METHOD)
    lca.lci()
    lca.lcia()
    milk = int(np.flatnonzero(acts["reference_product"] == "Cow milk, at farm gate")[0])
    assert model.score(milk)[0] == pytest.approx(lca.score)
    # 0.8 of the farm (economic allocation): 1.6 kg wheat, 40 kg water, 0.016 kg biogenic CH4,
    # 2.4 kg manure treated (0.01 kg NH3 each, no CF)
    wheat_score = 0.05 * 3.0 + 0.1 * 0.08 + 0.2 + 0.001 * 270
    assert lca.score == pytest.approx(1.6 * wheat_score + 40 * 0.0004 + 0.016 * 30)


def test_import_stops_on_unlinked_inputs(toy_project, tmp_path):
    import bw2data as bd

    report = tmp_path / "unlinked.csv"
    with pytest.raises(SystemExit):
        importing.import_simapro_csv(toy_project, FIXTURE, "toy-agb", link_to=[], report=report)
    assert "toy-agb" not in bd.databases
    text = report.read_text(encoding="utf-8")
    assert "Diesel {Europe without Switzerland}| market for diesel | Cut-off, U" in text

    result = importing.import_simapro_csv(toy_project, FIXTURE, "toy-agb", link_to=[], unlinked="drop", report=report)
    assert result["unlinked"] == 3  # diesel, electricity, tap water
    assert "toy-agb" in bd.databases


def test_internal_links_use_simapro_names():
    # bw2io later shortens both names to "Yeast, at plant" (location FR / RER): link before that
    data = [
        {"type": "product", "name": "Yeast, at plant/FR U", "unit": "kg", "database": "d", "code": "fr"},
        {"type": "product", "name": "Yeast, at plant/RER U", "unit": "kg", "database": "d", "code": "rer"},
        {"type": "process", "name": "Yeast, at plant/FR U", "exchanges": [
            {"type": "technosphere", "name": "Yeast, at plant/RER U", "unit": "kg", "amount": 1}]},
    ]
    importing.link_internal_by_product_name(data)
    assert data[2]["exchanges"][0]["input"] == ("d", "rer")


def test_aggregated_ecoinvent_copies_are_replaced(toy_project):
    copied = "Diesel {Europe without Switzerland}| market for diesel | Cut-off, S - Copied from Ecoinvent"
    changed = "Tap water {Europe without Switzerland}| market for tap water | Cut-off, S - Copied from Ecoinvent"
    unknown = "Unobtainium {GLO}| market for | Cut-off, S - Copied from Ecoinvent"

    def process(code, product, inputs=()):
        return {"type": "process", "database": "agb", "code": code, "name": product, "exchanges": [
            {"type": "production", "functional": True, "name": product, "amount": 1, "input": ("agb", f"p{code}")},
            {"type": "biosphere", "name": "Carbon dioxide, fossil", "amount": 1},
            *({"type": "technosphere", "name": n, "amount": 1} for n in inputs)]}

    data = [
        process("1", copied),  # aggregated: only emissions
        {"type": "product", "database": "agb", "code": "p1", "name": copied, "reference process": ("agb", "1")},
        process("2", changed, inputs=["Electricity"]),  # has inputs: changed by the authors, kept
        process("3", unknown),  # not in ecoinvent: kept
        process("4", "Wheat {FR} U", inputs=[copied, changed, unknown]),
    ]
    importing.replace_aggregated_copies(data, [EI], ["simapro-ecoinvent-3.9.1-cutoff"], log=lambda m: None)
    assert [ds["code"] for ds in data] == ["2", "3", "4"]  # the copy and its product are gone
    wheat = data[-1]["exchanges"]
    assert [e["name"] for e in wheat if e["type"] == "technosphere"] == [
        "Diesel {Europe without Switzerland}| market for diesel | Cut-off, U", changed, unknown]


def test_ecoinvent_release_into_an_existing_project(toy_project, monkeypatch):
    """With the release's own biosphere and methods, or linked to a biosphere of the project
    without methods (no download: ecoinvent_interface and bw2io's importer are stand-ins)."""
    import sys
    import types

    import bw2io as bi

    class Release:
        storage = types.SimpleNamespace(dir="cache")

        def __init__(self, settings):
            pass

        def list_versions(self):
            return ["3.10"]

        def list_system_models(self, version):
            return ["cutoff"]

        def get_release(self, **kwargs):
            pass

    fake = types.SimpleNamespace(Settings=lambda **kw: kw, EcoinventRelease=Release,
                                 ReleaseType=types.SimpleNamespace(ecospold="ecospold"))
    monkeypatch.setitem(sys.modules, "ecoinvent_interface", fake)
    calls = []
    monkeypatch.setattr(bi, "import_ecoinvent_release", lambda **kw: calls.append(kw))

    assert importing.import_ecoinvent_release(toy_project, "3.10", "cutoff", "me", "pw") == "ecoinvent-3.10-cutoff"
    assert calls[-1]["biosphere_name"] is None and calls[-1]["lcia"]
    importing.import_ecoinvent_release(toy_project, "3.10", "cutoff", "me", "pw", biosphere="biosphere3")
    assert calls[-1]["biosphere_name"] == "biosphere3" and not calls[-1]["lcia"]
    with pytest.raises(SystemExit, match="not in project"):
        importing.import_ecoinvent_release(toy_project, "3.10", "cutoff", "me", "pw", biosphere="nothing")
    with pytest.raises(SystemExit, match="already exists"):
        importing.import_ecoinvent_release(toy_project, "3.9.1", "cutoff", "me", "pw")
