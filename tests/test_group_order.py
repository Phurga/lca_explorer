"""Group contributions go by the order of the rules: a flow group listed before the group of an activity takes its flows."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lca_viewer.analysis import group_contributors, supply_chain_contribution
from lca_viewer.server import create_app

FLOUR = {"flour": {"field": "name", "op": "eq", "value": "flour"}}
WATER = {"water": {"field": "name", "op": "eq", "value": "Water consumed"}}
WS = 1  # the water method of the chain model: 1 per litre
# 1 kg bread: 0.4 kg wheat (10 L/kg), 0.8 kg flour (2 L/kg), bread itself 1 L: 4 + 1.6 + 1 = 6.6 L


def split(model, rule, order, method=WS):
    result = supply_chain_contribution(model, 2, FLOUR, rule, 1.0, WATER, [method], order)
    return {name: float(v[0]) for name, v in result.items()}


@pytest.mark.parametrize("order", [None, ["flour", "water"]])
def test_activity_group_first_keeps_what_it_reaches(linear_chain_model, order):
    # direct: the flour process's own water, the flow group takes the water of the others
    assert split(linear_chain_model, "direct", order) == pytest.approx({"flour": 1.6, "water": 5.0, "other": 0.0})
    # first match: flour with the wheat behind it (0.8 x (2 + 0.5 x 10)), the flow group the bread's own water
    assert split(linear_chain_model, "first_match", order) == pytest.approx({"flour": 5.6, "water": 1.0, "other": 0.0})


def test_flow_group_first_takes_its_flows_everywhere(linear_chain_model):
    for rule in ("direct", "first_match"):
        assert split(linear_chain_model, rule, ["water", "flour"]) == pytest.approx({"flour": 0.0, "water": 6.6, "other": 0.0})


def test_the_parts_always_add_up_to_the_score(linear_chain_model):
    for rule in ("direct", "first_match"):
        for order in (None, ["flour", "water"], ["water", "flour"]):
            assert sum(split(linear_chain_model, rule, order).values()) == pytest.approx(6.6)
            assert sum(split(linear_chain_model, rule, order, method=0).values()) == pytest.approx(1.5)  # climate change


def test_a_flow_group_takes_only_part_of_a_group(linear_chain_model):
    # climate change counts CO2 only: a flow group on it listed first takes the CO2 of the flour group as well
    co2 = {"co2": {"field": "name", "op": "eq", "value": "CO2 to air"}}
    for order, flour, other in ((["flour", "co2"], 0.4, 0.0), (["co2", "flour"], 0.0, 0.0)):
        got = supply_chain_contribution(linear_chain_model, 2, FLOUR, "direct", 1.0, co2, [0], order)
        assert float(got["flour"][0]) == pytest.approx(flour) and float(got["other"][0]) == pytest.approx(other)
        assert float(got["co2"][0]) == pytest.approx(1.5 - flour)


def test_contributors_follow_the_same_order(linear_chain_model):
    for rule in ("direct", "first_match"):
        for order in (["flour", "water"], ["water", "flour"]):
            totals = split(linear_chain_model, rule, order)
            for group, total in totals.items():
                members, values = group_contributors(linear_chain_model, 2, FLOUR, group, rule, 1.0, WATER, [WS], order)
                assert float(values.sum()) == pytest.approx(total), (rule, order, group)
                assert np.all(members >= 0)


def test_through_the_api(linear_chain_model):
    client = TestClient(create_app(linear_chain_model))
    body = {"root": 2, "groups": FLOUR, "flow_groups": WATER, "rule": "first_match", "method": WS}
    first = client.post("/api/global", json={**body, "order": ["flour", "water"]}).json()
    assert first["flour"][0] == pytest.approx(5.6) and first["water"][0] == pytest.approx(1.0)
    second = client.post("/api/global", json={**body, "order": ["water", "flour"]}).json()
    assert second["water"][0] == pytest.approx(6.6) and second["flour"][0] == pytest.approx(0.0)
    assert client.post("/api/global", json=body).json() == first  # without an order, activity groups first
    contributors = client.post("/api/global/contributors", json={**body, "order": ["water", "flour"], "group": "water"}).json()
    assert contributors["total"] == pytest.approx(6.6) and contributors["flow_group"]
    compared = client.post("/api/compare", json={"entries": [{"root": 2}], "groups": FLOUR, "flow_groups": WATER,
                                                  "rule": "first_match", "method": WS, "order": ["water", "flour"]}).json()
    assert compared["entries"][0]["groups"]["water"] == pytest.approx(6.6)
