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


def test_bad_requests(client):
    assert post(client, entries=[{"root": -1}]).status_code == 404  # not the last activity
    assert post(client, entries=[{"root": 0}], basis="flows", cut={"mode": "nope", "value": 1}).status_code == 400
    assert post(client, entries=[{"root": 0}], basis="nope").status_code == 400
    assert post(client, entries=[]).status_code == 400
    assert post(client, entries=[{"root": 99}]).status_code == 404
    assert post(client, entries=[{"root": 0}], methods=[7]).status_code == 404
    assert post(client, entries=[{"root": 0}], rule="nope").status_code == 400


def test_activities_basis_is_the_first_tier_only(client):
    # 1 kg bread: wheat 0.4 kg x 2 CO2, flour 0.8 kg x 0.5 CO2, bread 0.3 CO2. Inside the bread: its own emissions and
    # the flour with the wheat in it; the wheat is not a content of the bread, only of the flour
    entries = [{"root": 2}, {"root": 1, "amount": 2.0}]
    first, second = post(client, entries=entries, basis="activities").json()["entries"]
    assert first["groups"] == pytest.approx({"direct emissions": 0.3, "flour (world)": 1.2})
    assert second["groups"] == pytest.approx({"direct emissions": 1.0, "wheat (world)": 2.0})
    assert first["total"] == pytest.approx(1.5) and second["total"] == pytest.approx(3.0)


def test_activities_basis_folds_the_rest_into_other(client):
    # the cut is of the bars of all the entries together: the wheat (2.0) is the largest, the direct emissions (1.3) and
    # the flour (1.2) go to other
    entries = [{"root": 2}, {"root": 1, "amount": 2.0}]
    first, second = post(client, entries=entries, basis="activities", cut={"mode": "top", "value": 1}).json()["entries"]
    assert first["groups"] == pytest.approx({"other": 1.5}) and second["groups"] == pytest.approx({"wheat (world)": 2.0, "other": 1.0})
    # as in the tree, a single row under the cut-off is shown as itself
    [entry] = post(client, entries=[{"root": 2}], basis="activities", cut={"mode": "top", "value": 1}).json()["entries"]
    assert entry["groups"] == pytest.approx({"flour (world)": 1.2, "direct emissions": 0.3})
    # a share of the total: the direct emissions (0.3 of 1.5) are under 25 %, but alone
    [entry] = post(client, entries=[{"root": 2}], basis="activities", cut={"mode": "total", "value": 0.25}).json()["entries"]
    assert entry["groups"] == pytest.approx({"flour (world)": 1.2, "direct emissions": 0.3})


def test_flows_of_a_process_of_the_supply_chain(client):
    # the wheat of 1 kg bread emits 0.4 kg x (CO2 factor 2.0): its own emissions are its contribution
    body = {"root": 2, "method": 0, "activity": 0}
    found = client.post("/api/flows/of-process", json=body).json()
    assert found["total"] == pytest.approx(0.8) and found["count"] >= 1
    assert sum(f["impact"] for f in found["flows"]) + found["others"]["impact"] == pytest.approx(found["total"])
    assert client.post("/api/flows/of-process", json={**body, "activity": 99}).status_code == 404


def test_every_analysis_checks_what_it_is_asked_for(client):
    # a negative index would else be an activity from the end, an unknown rule computed as first match
    for url, body in (("/api/global", {"groups": {}, "method": 0}), ("/api/global/contributors", {"groups": {}, "group": "other"}),
                      ("/api/flows", {}), ("/api/flows/contributors", {"flows": [0]}), ("/api/flows/of-process", {"activity": 0})):
        assert client.post(url, json={"root": -1, **body}).status_code == 404, url
        assert client.post(url, json={"root": 0, **body, "method": 9}).status_code == 404, url
    assert client.post("/api/global", json={"root": 0, "groups": {}, "rule": "firstmatch"}).status_code == 400
    assert client.post("/api/rows", json={"root": [0], "attribution": "nope"}).status_code == 400
