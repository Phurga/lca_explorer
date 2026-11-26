"""Coverage of the elementary flows by an impact method: flows it leaves out, factors it has on flows outside the model."""

import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp
from fastapi.testclient import TestClient

from lca_viewer.analysis import method_coverage, uncharacterised
from lca_viewer.model import LCAModel, Method
from lca_viewer.server import create_app


def _model(unlinked=True):
    """A market (0) of two farms (1, 2), and a digester (3) outside its supply chain.

    Flows: 0 CO2 urban air, 1 CO2 rural air, 2 N2O, 3 water, 4 nitrate (no factor anywhere), 5 methane (only the
    digester emits it), 6 lead (no activity emits it). Methods: 0 climate change (a factor on lead), 1 water use,
    2 IPCC, the reference.
    """
    A = sp.csc_matrix(np.array([
        [1.0, 0.0, 0.0, 0.0],
        [-0.6, 1.0, 0.0, 0.0],
        [-0.4, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]))
    B = sp.csc_matrix(np.array([
        [0.0, 1.0, 0.5, 0.0],
        [0.0, 2.0, 0.0, 0.0],
        [0.0, 0.01, 0.02, 0.0],
        [0.0, 3.0, 4.0, 0.0],
        [0.0, 0.1, 0.1, 0.0],
        [0.0, 0.0, 0.0, 1.0],
        [0.0, 0.0, 0.0, 0.0],
    ]))
    activities = pd.DataFrame({
        "name": ["market for wheat", "wheat production", "wheat production", "anaerobic digestion"],
        "reference_product": ["wheat", "wheat", "wheat", "biogas"], "location": ["GLO", "FR", "DE", "CH"],
        "unit": ["kg", "kg", "kg", "m3"], "isic": [None] * 4, "cpc": [None] * 4,
    })
    flows = pd.DataFrame({
        "id": [10, 11, 12, 13, 14, 15, 16],
        "name": ["Carbon dioxide, fossil", "Carbon dioxide, fossil", "Dinitrogen monoxide", "Water", "Nitrate",
                 "Methane, fossil", "Lead"],
        "categories": [("air", "urban air close to ground"), ("air", "non-urban air or from high stacks"), ("air",),
                       ("water", "surface water"), ("water", "ground-"), ("air",), ("air",)],
        "unit": ["kilogram", "kilogram", "kilogram", "cubic meter", "kilogram", "kilogram", "kilogram"],
    })
    methods = [
        Method(("climate change",), "kg CO2-eq", np.array([1.0, 1.0, 273.0, 0.0, 0.0, 28.0, 5.0])),
        Method(("water use",), "m3", np.array([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])),
        Method(("IPCC 2013",), "kg CO2-eq", np.array([1.0, 1.0, 265.0, 0.0, 0.0, 27.0, 0.0])),
    ]
    table = pd.DataFrame({
        "method": [0, 0, 0],
        "id": [99, 77, 78],
        "name": ["Sulfur hexafluoride", "Carbon dioxide, fossil", "Dinitrogen monoxide"],
        "categories": [("air",), ("air", "urban air close to ground"), ("air", "lower stratosphere")],
        "unit": ["kilogram"] * 3,
        "cf": [23500.0, 1.0, 273.0],
    })
    return LCAModel(A, B, activities, flows, methods, unlinked=table if unlinked else None)


@pytest.fixture
def model():
    return _model()


def test_uncharacterised_lists_the_inventory_flows_without_a_factor(model):
    found = uncharacterised(model, model.supply(0), 0, reference=2)
    assert list(found["flows"]) == [3, 4]  # water and nitrate; methane is outside this supply chain
    assert found["amounts"] == pytest.approx([0.6 * 3 + 0.4 * 4, 0.1])
    assert list(found["emitters"]) == [2, 2]
    assert list(found["methods"]) == [1, 0]  # water use characterises water; nothing nitrate
    assert list(found["reference_cf"]) == [0.0, 0.0]
    assert found["inventory"] == 5 and found["characterised"] == 3


def test_uncharacterised_gives_the_factor_of_the_reference(model):
    found = uncharacterised(model, model.supply(0), 1, reference=0)
    assert list(found["flows"]) == [0, 1, 2, 4]
    assert list(found["reference_cf"]) == [1.0, 1.0, 273.0, 0.0]
    assert list(found["emitters"]) == [2, 1, 2, 2]
    assert "reference_cf" not in uncharacterised(model, model.supply(0), 1)


def test_coverage_of_the_whole_model(model):
    found = method_coverage(model, 0, reference=2)
    assert found["linked"] == 5  # CO2 ×2, N2O, methane, lead
    assert found["characterised_unused"] == 1  # lead: no activity emits it
    assert found["inventory"] == 6
    missing = found["uncharacterised"]
    assert list(missing["flows"]) == [3, 4]
    assert list(missing["emitters"]) == [2, 2]
    assert list(missing["reference_cf"]) == [0.0, 0.0]


def test_uncharacterised_flows_with_most_emitters_first(model):
    missing = method_coverage(model, 1)["uncharacterised"]
    assert list(missing["flows"]) == [0, 2, 4, 1, 5]  # 2 emitters each, then CO2 rural and methane (1 each)
    assert list(missing["emitters"]) == [2, 2, 2, 1, 1]


def test_unlinked_factors_are_matched_to_flows_of_the_model(model):
    table = method_coverage(model, 0)["unlinked"]
    assert list(table["name"]) == ["Sulfur hexafluoride", "Carbon dioxide, fossil", "Dinitrogen monoxide"]
    assert list(table["match_kind"]) == ["", "same", "name"]  # CO2 of another biosphere: same name and compartments
    assert list(table["match"]) == [-1, 0, 2]
    assert method_coverage(model, 1)["unlinked"].empty


def test_coverage_without_recorded_factors():
    assert method_coverage(_model(unlinked=False), 0)["unlinked"] is None


def test_unlinked_factors_saved_with_the_model(model, tmp_path):
    model.save(tmp_path / "m")
    loaded = LCAModel.load(tmp_path / "m")
    pd.testing.assert_frame_equal(loaded.unlinked, model.unlinked)
    _model(unlinked=False).save(tmp_path / "m")  # a rebuild without them: none left from before
    assert LCAModel.load(tmp_path / "m").unlinked is None


def test_api_uncharacterised(model):
    client = TestClient(create_app(model))
    r = client.post("/api/flows/uncharacterised", json={"root": 0, "method": 1, "reference": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["inventory"] == 5 and body["characterised"] == 1 and body["count"] == 4 and body["gaps"] == 3
    kg = body["units"][0]
    assert kg["unit"] == "kilogram" and kg["count"] == 4
    # those the reference characterises first, then the largest amount first
    assert [(it["name"], it["amount"]) for it in kg["items"]] == [
        ("Carbon dioxide, fossil", pytest.approx(1.2)), ("Carbon dioxide, fossil", pytest.approx(0.8)),
        ("Dinitrogen monoxide", pytest.approx(0.014)), ("Nitrate", pytest.approx(0.1))]
    n2o = kg["items"][2]
    assert n2o["reference_cf"] == 273.0 and n2o["compartment"] == "air" and n2o["emitters"] == 2
    plain = client.post("/api/flows/uncharacterised", json={"root": 0, "method": 1}).json()
    assert [it["name"] for it in plain["units"][0]["items"]][2] == "Nitrate" and plain["gaps"] == 0
    assert client.post("/api/flows/uncharacterised", json={"root": 0, "method": 1, "reference": 9}).status_code == 404


def test_api_coverage(model):
    client = TestClient(create_app(model))
    body = client.get("/api/methods/0/coverage?reference=2").json()
    assert body["linked"] == 5 and body["inventory"] == 6 and body["characterised_unused"] == 1
    assert [it["name"] for it in body["uncharacterised"]["items"]] == ["Water", "Nitrate"]
    assert body["uncharacterised"]["gaps"] == 0
    water = client.get("/api/methods/1/coverage?reference=0").json()["uncharacterised"]
    assert water["gaps"] == 4  # CO2 ×2, N2O, methane: characterised by climate change; nitrate is not
    assert [it["name"] for it in water["items"]][-1] == "Nitrate"
    assert body["unlinked"]["count"] == 3 and body["unlinked"]["same"] == 1
    first = body["unlinked"]["items"][0]
    assert first["name"] == "Sulfur hexafluoride" and first["match"] is None and first["compartment"] == "air"
    co2 = next(it for it in body["unlinked"]["items"] if it["match_kind"] == "same")
    assert co2["match"]["flow"] == 0
    assert TestClient(create_app(_model(unlinked=False))).get("/api/methods/0/coverage").json()["unlinked"] is None
    assert client.get("/api/methods/7/coverage").status_code == 404
