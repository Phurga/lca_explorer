"""An impact category seen from its elementary flows, alone or against another method."""

import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp
from fastapi.testclient import TestClient

from lca_viewer.analysis import category_flows, compare_methods
from lca_viewer.model import LCAModel, Method
from lca_viewer.server import create_app


@pytest.fixture
def category_model():
    """A market (0) of two farms (1, 2). Flows: 0 CO2 urban air, 1 CO2 rural air, 2 N2O air, 3 methane air (emitted by
    nobody), 4 radon (another unit).

    Climate change A characterises CO2 (both), N2O and methane; climate change B characterises CO2 (urban only, 1),
    N2O (265) and the radon (nonsense, but another unit), not methane.
    """
    A = sp.csc_matrix(np.array([
        [1.0, 0.0, 0.0],
        [-0.6, 1.0, 0.0],
        [-0.4, 0.0, 1.0],
    ]))
    B = sp.csc_matrix(np.array([
        [0.0, 1.0, 0.5],
        [0.0, 2.0, 0.0],
        [0.0, 0.01, 0.02],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 3.0],
    ]))
    activities = pd.DataFrame({
        "name": ["market for wheat", "wheat production", "wheat production"],
        "reference_product": ["wheat"] * 3, "location": ["GLO", "FR", "DE"], "unit": ["kg"] * 3,
    })
    flows = pd.DataFrame({
        "name": ["Carbon dioxide, fossil", "Carbon dioxide, fossil", "Dinitrogen monoxide", "Methane", "Radon-222"],
        "categories": [("air", "urban air close to ground"), ("air", "non-urban air or from high stacks"),
                       ("air",), ("air",), ("air",)],
        "unit": ["kilogram"] * 4 + ["kilo Becquerel"],
    })
    methods = [
        Method(("A", "climate change", "GWP100"), "kg CO2-eq", np.array([1.0, 1.0, 273.0, 29.7, 0.0])),
        Method(("B", "climate change", "GWP100"), "kg CO2-eq", np.array([1.0, 0.0, 265.0, 0.0, 0.5])),
    ]
    return LCAModel(A, B, activities, flows, methods)


def test_category_lists_the_characterised_flows_largest_factor_first(category_model):
    table = category_flows(category_model, 0)
    assert list(table.index) == [2, 3, 0, 1]  # N2O, methane, CO2 (stable for equal factors); radon has no factor
    assert table.loc[2, "cf_a"] == 273.0
    assert table.loc[2, "rel_a"] == 1.0
    assert table.loc[3, "rel_a"] == pytest.approx(29.7 / 273.0)
    # emitters: activities with the flow in their column of B
    assert list(table["emitters"]) == [2, 0, 2, 1]
    assert "amount" not in table and "cf_b" not in table


def test_relative_factor_is_within_the_unit(category_model):
    table = category_flows(category_model, 1)
    assert table.loc[4, "rel_a"] == 1.0  # radon is the only kBq flow: the largest of its unit
    assert table.loc[2, "rel_a"] == 1.0


def test_category_in_a_supply_chain_adds_amounts_and_contributions_summing_to_the_score(category_model):
    s = category_model.supply(0, 1.0)
    table = category_flows(category_model, 0, s)
    assert table.loc[2, "amount"] == pytest.approx(0.6 * 0.01 + 0.4 * 0.02)
    assert table.loc[3, "amount"] == 0.0
    assert table["impact_a"].sum() == pytest.approx(category_model.G[0, 0])
    # the largest contribution first, then the flows not in the inventory (methane) by factor
    assert list(table.index) == [2, 1, 0, 3]


def test_compare_methods_lines_up_the_factors(category_model):
    table = compare_methods(category_model, 0, 1)
    assert set(table.index) == {0, 1, 2, 3, 4}
    assert table.loc[2, "ratio"] == pytest.approx(265 / 273)
    assert table.loc[0, "ratio"] == 1.0
    assert table.loc[1, "only"] == "a" and np.isnan(table.loc[1, "ratio"])
    assert table.loc[3, "only"] == "a"
    assert table.loc[4, "only"] == "b"
    assert table.loc[2, "only"] == ""


def test_compare_methods_in_a_supply_chain_has_both_scores(category_model):
    s = category_model.supply(0, 1.0)
    table = compare_methods(category_model, 0, 1, s)
    assert table["impact_a"].sum() == pytest.approx(category_model.G[0, 0])
    assert table["impact_b"].sum() == pytest.approx(category_model.G[0, 1])


@pytest.fixture
def client(category_model):
    return TestClient(create_app(category_model))


def test_api_category_database_wide(client):
    r = client.post("/api/category", json={"method": 0})
    assert r.status_code == 200
    data = r.json()
    assert data["flows"] == 4 and data["in_inventory"] is None and data["unlinked"] is None
    first = data["items"][0]
    assert first["name"] == "Dinitrogen monoxide" and first["a"]["max"] == 273.0 and first["amount"] is None
    assert first["b"] is None and first["emitters"] == 2


def test_api_category_in_a_process_and_compared(client):
    data = client.post("/api/category", json={"method": 0, "compare": 1, "root": 0}).json()
    assert data["in_inventory"] == 4  # CO2 (2 flows), N2O, radon
    assert data["totals"]["a"] == pytest.approx(0.6 * 1 + 0.4 * 0.5 + 0.6 * 2 + 273 * 0.014)
    by_flow = {it["flows"][0]: it for it in data["items"]}
    assert by_flow[1]["only"] == "a" and by_flow[1]["ratio"] is None
    assert by_flow[4]["only"] == "b" and by_flow[4]["b"]["impact"] == pytest.approx(0.4 * 3 * 0.5)
    assert by_flow[2]["ratio"] == pytest.approx(265 / 273)


def test_api_category_grouped_by_substance(client):
    data = client.post("/api/category", json={"method": 0, "compare": 1, "root": 0, "keys": ["name"]}).json()
    co2 = next(it for it in data["items"] if it["name"] == "Carbon dioxide, fossil")
    assert sorted(co2["flows"]) == [0, 1]
    assert co2["compartment"] == "air · 2 subcompartments"
    assert co2["emitters"] == 2  # both farms, counted once
    assert co2["amount"] == pytest.approx(0.6 * 3 + 0.4 * 0.5)
    assert co2["b"] == {"min": 0.0, "max": 1.0, "rel": pytest.approx(1 / 265), "impact": pytest.approx(0.6 + 0.2)}
    assert co2["ratio"] is None and co2["only"] == ""  # factors differ within the group: no single ratio


def test_api_category_rejects_unknown_input(client):
    assert client.post("/api/category", json={"method": 5}).status_code == 404
    assert client.post("/api/category", json={"method": 0, "compare": 9}).status_code == 404
    assert client.post("/api/category", json={"method": 0, "keys": ["colour"]}).status_code == 400
