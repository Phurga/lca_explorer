"""Direct emissions split into elementary flows, and grouping of those flows."""

import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp

from lca_viewer.model import LCAModel, Method
from lca_viewer.tree import Tree
from lca_viewer.view import GroupRule, View


@pytest.fixture
def flows_model():
    """A market (0) of two farms (1, 2), each emitting CO2 to two compartments, N2O and water.

    Water has a factor only in the second method, so it is zero for climate change.
    """
    A = sp.csc_matrix(np.array([
        [1.0, 0.0, 0.0],
        [-0.6, 1.0, 0.0],
        [-0.4, 0.0, 1.0],
    ]))
    # flows: 0 CO2 urban air, 1 CO2 rural air, 2 N2O air, 3 water, 4 nitrate to water (no factor at all)
    B = sp.csc_matrix(np.array([
        [0.0, 1.0, 0.5],
        [0.0, 2.0, 0.0],
        [0.0, 0.01, 0.02],
        [0.0, 3.0, 4.0],
        [0.0, 0.1, 0.1],
    ]))
    activities = pd.DataFrame({
        "name": ["market for wheat", "wheat production", "wheat production"],
        "reference_product": ["wheat"] * 3, "location": ["GLO", "FR", "DE"], "unit": ["kg"] * 3,
        "isic": [None] * 3, "cpc": [None] * 3,
    })
    flows = pd.DataFrame({
        "name": ["Carbon dioxide, fossil", "Carbon dioxide, fossil", "Dinitrogen monoxide", "Water", "Nitrate"],
        "categories": [("air", "urban air close to ground"), ("air", "non-urban air or from high stacks"),
                       ("air",), ("water", "surface water"), ("water", "ground-")],
        "unit": ["kilogram"] * 4 + ["kilogram"],
    })
    methods = [
        Method(("climate change",), "kg CO2-eq", np.array([1.0, 1.0, 273.0, 0.0, 0.0])),
        Method(("water use",), "m3", np.array([0.0, 0.0, 0.0, 1.0, 0.0])),
    ]
    return LCAModel(A, B, activities, flows, methods)


def _flow_view(model, **kwargs):
    view = View(Tree(model, 0), **kwargs)
    view.expand("0")
    view.expand("0/1")
    view.expand("0/1/-1")
    return view


def test_direct_rows_expand_into_flows_that_sum_to_them(flows_model):
    view = _flow_view(flows_model)
    rows = view.rows()
    direct = next(r for r in rows if r.id == "0/1/-1")
    assert direct.expandable and direct.expanded
    flows = [r for r in rows if r.parent == direct.id]
    assert {r.kind for r in flows} == {"flow", "rest"}
    # nitrate has no factor in any method: left out
    assert all("Nitrate" not in r.label for r in flows)
    assert np.allclose(sum(r.impacts for r in flows), direct.impacts)  # every method
    assert np.allclose(sum(r.impacts for r in view.frontier()), view.tree.root.impacts)
    n2o = next(r for r in flows if r.label.startswith("Dinitrogen"))
    assert n2o.amount == pytest.approx(0.6 * 0.01) and n2o.unit == "kilogram"
    assert n2o.label == "Dinitrogen monoxide | air"
    assert n2o.sources == [(1, pytest.approx(0.6))]


def test_flows_without_impact_for_the_method_go_to_others(flows_model):
    view = _flow_view(flows_model)
    rest = next(r for r in view.rows() if r.id == "0/1/-1/rest")
    assert [m.label for m in rest.members] == ["Water | water, surface water"]
    assert rest.label == "others (1 flow)"
    view.method = 1  # water use: water is the only flow with impact
    shown = [r for r in view.rows() if r.parent == "0/1/-1"]
    assert [r.label.split(" | ")[0] for r in shown] == ["Water", "others (3 flows)"]


def test_flow_rows_follow_cutoff_pins_and_moves(flows_model):
    view = _flow_view(flows_model)
    co2 = [r for r in view.rows() if r.kind == "flow" and r.label.startswith("Carbon")]
    view.others = {co2[0].id}
    rest = next(r for r in view.rows() if r.id == "0/1/-1/rest")
    assert co2[0].id in {m.id for m in rest.members}
    assert np.allclose(sum(r.impacts for r in view.frontier()), view.tree.root.impacts)


def test_flows_of_a_merged_process_combine_both_inventories(flows_model):
    view = View(Tree(flows_model, 0), merges=[{"scope": "0", "products": [1, 2]}])
    view.expand("0")
    merged = next(r for r in view.rows() if r.kind == "merged")
    view.expand(merged.id)
    direct = next(r for r in view.rows() if r.parent == merged.id and r.kind == "direct")
    view.expand(direct.id)
    flows = [r for r in view.rows() if r.parent == direct.id]
    urban = next(r for r in flows if "urban air close" in r.label)
    assert urban.amount == pytest.approx(0.6 * 1.0 + 0.4 * 0.5)
    assert np.allclose(sum(r.impacts for r in flows), direct.impacts)


def test_flow_group_by_substance_sums_amounts(flows_model):
    view = _flow_view(flows_model, rules=[GroupRule(by="name", target="flows")])
    rows = view.rows()
    group = next(r for r in rows if r.kind == "group")
    assert group.label == "Carbon dioxide, fossil"
    assert group.amount == pytest.approx(0.6 * 3.0) and group.unit == "kilogram"
    assert group.parent == "0/1/-1"
    # flow rules leave processes alone, and process rules leave flows alone
    view.rules.append(GroupRule(by="location"))
    assert [r.label for r in view.rows() if r.kind == "group"] == ["Carbon dioxide, fossil"]
    view.expand(group.id)
    members = [r for r in view.rows() if r.parent == group.id]
    assert len(members) == 2 and all(r.kind == "flow" for r in members)
    assert np.allclose(sum(r.impacts for r in view.frontier()), view.tree.root.impacts)


def test_flow_group_by_compartment_and_manual_group(flows_model):
    view = _flow_view(flows_model, rules=[GroupRule(by="compartment", target="flows")])
    assert [r.label for r in view.rows() if r.kind == "group"] == ["air"]
    co2 = [0, 1]
    view.rules = [GroupRule(name="CO2", predicate={"flows": co2}, scope="0/1/-1", target="flows")]
    group = next(r for r in view.rows() if r.kind == "group")
    assert group.id == "0/1/-1/g:CO2" and group.amount == pytest.approx(1.8)
    restored = View.from_dict(flows_model, view.to_dict())
    assert restored.rules[0].target == "flows"


def test_api_rows_describe_flows(flows_model):
    from fastapi.testclient import TestClient
    from lca_viewer.server import create_app

    client = TestClient(create_app(flows_model))
    state = {"root": [0], "expanded": ["0", "0/1", "0/1/-1"], "method": 0, "methods": [1]}
    rows = client.post("/api/rows", json=state).json()["rows"]
    direct = next(r for r in rows if r["id"] == "0/1/-1")
    assert direct["expandable"] and direct["expanded"]
    flow = next(r for r in rows if r["kind"] == "flow")
    assert flow["flow"]["compartment"] == "air"
    assert flow["emitters"][0]["index"] == 1
    assert len(flow["impacts"]) == 2


def test_flow_contribution_sums_to_the_score_and_merges_compartments(flows_model):
    from fastapi.testclient import TestClient
    from lca_viewer.server import create_app

    client = TestClient(create_app(flows_model))
    score = flows_model.score(0)[0]
    by_flow = client.post("/api/flows", json={"root": 0, "method": 0, "by": "flow"}).json()
    # water and nitrate have no climate change factor: left out
    assert by_flow["count"] == 3
    assert by_flow["total"] == pytest.approx(score)
    assert sum(it["impact"] for it in by_flow["items"]) == pytest.approx(score)
    # CO2 to urban air: farm FR emits 1 per kg (0.6 kg), farm DE 0.5 (0.4 kg)
    urban = next(it for it in by_flow["items"] if it["flows"] == [0])
    assert urban["amount"] == pytest.approx(0.6 * 1.0 + 0.4 * 0.5)

    by_substance = client.post("/api/flows", json={"root": 0, "method": 0}).json()
    co2 = next(it for it in by_substance["items"] if it["name"] == "Carbon dioxide, fossil")
    assert sorted(co2["flows"]) == [0, 1] and co2["compartment"] == "2 compartments"
    # compartment of each flow, to switch between them in the flow pop-up
    assert dict(zip(co2["flows"], co2["compartments"])) == {0: "air, urban air close to ground", 1: "air, non-urban air or from high stacks"}
    assert co2["amount"] == pytest.approx(0.6 * 3.0 + 0.4 * 0.5)
    assert by_substance["count"] == 2
    assert by_substance["items"][0]["impact"] >= by_substance["items"][1]["impact"]


def test_flow_contributors_are_the_emitting_processes(flows_model):
    from fastapi.testclient import TestClient
    from lca_viewer.server import create_app

    client = TestClient(create_app(flows_model))
    result = client.post("/api/flows/contributors", json={"root": 0, "method": 0, "flows": [0, 1]}).json()
    assert result["count"] == 2  # the two farms; the market emits nothing
    by_location = {c["location"]: c for c in result["contributors"]}
    assert by_location["FR"]["amount"] == pytest.approx(0.6 * 3.0)
    assert by_location["DE"]["impact"] == pytest.approx(0.4 * 0.5)
    # the compartment each farm mostly emits to (FR: rural air, DE: only urban air)
    assert by_location["FR"]["flow"] == 1 and by_location["DE"]["flow"] == 0
    assert result["total"] == pytest.approx(0.6 * 3.0 + 0.4 * 0.5)


def test_flow_info_gives_factors_and_the_exchange_of_each_emitter(flows_model):
    from fastapi.testclient import TestClient
    from lca_viewer.server import create_app

    client = TestClient(create_app(flows_model))
    info = client.get("/api/flow/2?method=0&activities=1,2").json()
    assert info["name"] == "Dinitrogen monoxide" and info["compartment"] == "air"
    assert info["cf"] == 273.0
    assert info["factors"] == [{"method": 0, "cf": 273.0}]  # no factor for water use
    assert [e["activity"]["location"] for e in info["exchanges"]] == ["FR", "DE"]
    # water: a factor only in the second method
    assert client.get("/api/flow/3?method=0").json()["factors"] == [{"method": 1, "cf": 1.0}]
    assert client.get("/api/flow/99").status_code == 404


@pytest.mark.parametrize("rule", ["direct", "first_match"])
def test_flow_groups_split_what_the_activity_groups_leave(flows_model, rule):
    from lca_viewer.analysis import group_contributors, supply_chain_contribution

    groups = {"farm FR": {"field": "location", "op": "eq", "value": "FR"}}
    flow_groups = {"N2O": {"field": "name", "op": "eq", "value": "Dinitrogen monoxide"}}
    result = supply_chain_contribution(flows_model, 0, groups, rule, flow_groups=flow_groups)
    assert list(result) == ["farm FR", "N2O", "other"]
    # the parts add up to the score, for every method
    np.testing.assert_allclose(sum(result.values()), flows_model.score(0))
    # the French farm is reached first: it keeps all its emissions, N2O included
    assert result["farm FR"][0] == pytest.approx(0.6 * (3.0 + 0.01 * 273))
    # the N2O group only has the N2O of the other farm (0.4 kg x 0.02 kg x 273)
    assert result["N2O"][0] == pytest.approx(0.4 * 0.02 * 273)
    assert result["other"][0] == pytest.approx(0.4 * 0.5)  # the German farm's CO2
    # one method only gives the same numbers
    single = supply_chain_contribution(flows_model, 0, groups, rule, flow_groups=flow_groups, methods=[0])
    assert {k: v[0] for k, v in single.items()} == pytest.approx({k: v[0] for k, v in result.items()})

    for group in result:
        members, values = group_contributors(flows_model, 0, groups, group, rule, flow_groups=flow_groups)
        np.testing.assert_allclose(values.sum(axis=0), result[group])
    members, _ = group_contributors(flows_model, 0, groups, "N2O", rule, flow_groups=flow_groups)
    assert list(members) == [2]  # the German farm


def test_first_match_flows_upstream_of_a_matched_activity_stay_with_it(flows_model):
    from lca_viewer.analysis import supply_chain_contribution

    # The market reaches both farms first: all their N2O is the market's
    groups = {"market": {"field": "name", "op": "eq", "value": "market for wheat"}}
    flow_groups = {"N2O": {"field": "name", "op": "eq", "value": "Dinitrogen monoxide"}}
    result = supply_chain_contribution(flows_model, 0, groups, "first_match", flow_groups=flow_groups)
    assert result["N2O"][0] == pytest.approx(0)
    assert result["market"][0] == pytest.approx(flows_model.score(0)[0])


def test_global_api_with_flow_groups(flows_model):
    from fastapi.testclient import TestClient
    from lca_viewer.server import create_app

    client = TestClient(create_app(flows_model))
    body = {"root": 0, "groups": {}, "flow_groups": {"N2O": {"field": "name", "op": "eq", "value": "Dinitrogen monoxide"}},
            "rule": "direct", "method": 0}
    result = client.post("/api/global", json=body).json()
    assert list(result) == ["N2O", "other"] and len(result["N2O"]) == 1
    assert result["N2O"][0] + result["other"][0] == pytest.approx(flows_model.score(0)[0])  # no process group: all N2O
    listed = client.post("/api/global/contributors", json={**body, "group": "N2O"}).json()
    assert listed["flow_group"] and not listed["cumulative"] and listed["count"] == 2
    assert listed["total"] == pytest.approx(result["N2O"][0])
