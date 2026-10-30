"""Comparison mode: several functional units under the same groups."""

import pytest
from fastapi.testclient import TestClient

from lca_viewer.server import create_app

WHEAT = {"wheat": {"field": "name", "op": "eq", "value": "wheat"}}


@pytest.fixture
def client(linear_chain_model):
    return TestClient(create_app(linear_chain_model))


def post(client, **body):
    return client.post("/api/compare", json={"method": 0, **body})


def test_groups_add_up_to_the_total_of_each_entry(client):
    # 1 kg bread: wheat 0.4 kg x 2 CO2, flour 0.8 kg x 0.5 CO2, bread 0.3 CO2
    entries = [{"root": 2}, {"root": 1, "amount": 2.0}]
    first, second = post(client, entries=entries, groups=WHEAT, rule="direct").json()["entries"]
    assert first["groups"] == pytest.approx({"wheat": 0.8, "other": 0.7})
    assert second["groups"] == pytest.approx({"wheat": 2.0, "other": 1.0})
    for entry in (first, second):
        assert entry["total"] == pytest.approx(sum(entry["groups"].values()))
    assert first["total"] == pytest.approx(1.5) and second["total"] == pytest.approx(3.0)


def test_first_match_attributes_the_whole_supply_chain(client):
    flour = {"flour": {"field": "name", "op": "eq", "value": "flour"}}
    [entry] = post(client, entries=[{"root": 2}], groups=flour).json()["entries"]
    # flour and the wheat behind it: 0.8 x (0.5 + 0.5 x 2); bread keeps its own emissions
    assert entry["groups"] == pytest.approx({"flour": 1.2, "other": 0.3})


def test_totals_of_the_requested_methods(client):
    [entry] = post(client, entries=[{"root": 2}], methods=[1, 0]).json()["entries"]
    # water: 0.4 x 10 + 0.8 x 2 + 1 L; then climate change
    assert entry["totals"] == pytest.approx([6.6, 1.5])
    assert entry["groups"] == pytest.approx({"other": 1.5})
    assert post(client, entries=[{"root": 2}]).json()["entries"][0]["totals"] == []


def test_flows_basis_splits_by_substance(client):
    entries = [{"root": 2}, {"root": 1, "amount": 2.0}]
    first, second = post(client, entries=entries, basis="flows").json()["entries"]
    # climate change only counts CO2; water has no factor
    assert first["groups"] == pytest.approx({"CO2 to air": 1.5}) and first["total"] == pytest.approx(1.5)
    assert second["groups"] == pytest.approx({"CO2 to air": 3.0})
    [water] = post(client, entries=[{"root": 2}], basis="flows", method=1).json()["entries"]
    assert water["groups"] == pytest.approx({"Water consumed": 6.6})


def test_flows_basis_folds_the_rest_into_other(client):
    [entry] = post(client, entries=[{"root": 2}], basis="flows", limit=0).json()["entries"]
    assert entry["groups"] == pytest.approx({"other": 1.5})


def test_bad_requests(client):
    assert post(client, entries=[{"root": 0}], basis="nope").status_code == 400
    assert post(client, entries=[]).status_code == 400
    assert post(client, entries=[{"root": 99}]).status_code == 404
    assert post(client, entries=[{"root": 0}], methods=[7]).status_code == 404
    assert post(client, entries=[{"root": 0}], rule="nope").status_code == 400
