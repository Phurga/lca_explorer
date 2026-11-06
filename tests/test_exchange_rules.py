"""Rules on the exchange that brings a row in: its type (avoided product, waste…) and its amount."""

import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp

from lca_viewer.analysis import group_contributors, supply_chain_contribution
from lca_viewer.model import LCAModel, Method
from lca_viewer.query import evaluate, uses_exchange, without_exchange
from lca_viewer.tree import Tree
from lca_viewer.view import GroupRule, View


@pytest.fixture
def consequential_model():
    """cheese (0) takes 1 kg milk (1) and 0.1 kWh electricity (4), avoids 0.5 kg feed (2) and 0.1 kg fertiliser (5)
    (negative inputs), and sends 0.2 kg whey to treatment (3: negative production, as in ecoinvent)."""
    names = ["cheese", "milk", "feed", "whey treatment", "electricity", "fertiliser"]
    activities = pd.DataFrame({"name": names, "reference_product": names, "location": ["GLO"] * 6,
                               "unit": ["kg", "kg", "kg", "kg", "kWh", "kg"]})
    flows = pd.DataFrame({"name": ["CO2"], "unit": ["kg"], "categories": [("air",)]})
    # inputs negative; an avoided product is a negative input (positive here), a waste input too (with -1 production)
    entries = {(0, 0): 1.0, (1, 0): -1.0, (2, 0): 0.5, (3, 0): 0.2, (4, 0): -0.1, (5, 0): 0.1,
               (1, 1): 1.0, (2, 2): 1.0, (3, 3): -1.0, (4, 4): 1.0, (5, 5): 1.0}
    rows, cols = zip(*entries)
    A = sp.csc_matrix((list(entries.values()), (rows, cols)), shape=(6, 6))
    B = sp.csc_matrix(np.array([[0.1, 1.0, 0.4, 0.3, 0.5, 2.0]]))
    return LCAModel(A=A, B=B, activities=activities, flows=flows,
                    methods=[Method(("climate change",), "kg CO2-eq", np.array([1.0]))])


def _rows(model, rules):
    view = View(Tree(model, 0), expanded={"0"}, rules=[GroupRule(**r) for r in rules])
    return view, view.rows()


def _members(rows, label):
    group = next(r for r in rows if r.kind == "group" and r.label == label)
    return sorted(int(m.product) for m in group.node_members)


def test_rule_on_exchange_type_groups_avoided_products(consequential_model):
    _, rows = _rows(consequential_model, [{"name": "avoided", "predicate": {"field": "exchange_type", "op": "eq", "value": "avoided"}}])
    assert _members(rows, "avoided") == [2, 5]  # not the waste, though its input is negative too


def test_rule_on_exchange_amount(consequential_model):
    _, rows = _rows(consequential_model, [{"name": "credits", "predicate": {"all": [
        {"field": "exchange_amount", "op": "lt", "value": 0}, {"field": "unit", "op": "eq", "value": "kg"}]}}])
    assert _members(rows, "credits") == [2, 3, 5]
    _, rows = _rows(consequential_model, [{"name": "big", "predicate": {"field": "exchange_amount", "op": "ge", "value": "0.1"}}])
    assert _members(rows, "big") == [1, 4]


def test_group_by_exchange_type(consequential_model):
    _, rows = _rows(consequential_model, [{"by": "exchange_type"}])
    assert _members(rows, "exchange_type: avoided") == [2, 5]
    assert _members(rows, "exchange_type: technosphere") == [1, 4]


def test_colour_rule_on_exchange(consequential_model):
    view = View(Tree(consequential_model, 0), expanded={"0"},
                colour_rules=[GroupRule(name="waste", predicate={"field": "exchange_type", "op": "eq", "value": "waste"})])
    matched = {r.node.product: view.matching_rule(r) for r in view.rows() if r.kind == "process" and r.depth == 1}
    assert matched[3] == "waste" and matched[2] is None and matched[1] is None


def test_exchange_amount_below_an_avoided_product(consequential_model):
    """Below an avoided product, the amounts are negative; the exchanges are still read as recorded."""
    model = consequential_model
    A = model.A.tolil()
    A[1, 2] = -2.0  # feed takes 2 kg milk
    model = LCAModel(A=A.tocsc(), B=model.B, activities=model.activities, flows=model.flows, methods=model.methods)
    view, rows = _rows(model, [])
    view.expanded = {"0", "0/2"}
    view.rows()
    assert view._exchanges["0/2/1"] == ("technosphere", pytest.approx(2.0))
    assert view._exchanges["0/2"] == ("avoided", pytest.approx(-0.5))


def test_exchange_conditions_elsewhere(consequential_model):
    predicate = {"all": [{"field": "exchange_type", "op": "eq", "value": "avoided"}, {"field": "unit", "op": "eq", "value": "kg"}]}
    assert uses_exchange(predicate) and not uses_exchange({"field": "unit", "op": "eq", "value": "kg"})
    assert without_exchange(predicate) == {"all": [{"field": "unit", "op": "eq", "value": "kg"}]}
    assert without_exchange({"field": "exchange_amount", "op": "lt", "value": 0}) is None


@pytest.fixture
def deeper_model(consequential_model):
    """As ``consequential_model``, with feed taking 2 kg milk: the avoided feed avoids milk too. Score -0.19."""
    m = consequential_model
    A = m.A.tolil()
    A[1, 2] = -2.0
    return LCAModel(A=A.tocsc(), B=m.B, activities=m.activities, flows=m.flows, methods=m.methods)


AVOIDED = {"field": "exchange_type", "op": "eq", "value": "avoided"}


def _split(model, groups, rule, **kw):
    result = {k: float(v[0]) for k, v in supply_chain_contribution(model, 0, groups, rule, **kw).items()}
    assert np.isclose(sum(result.values()), float(model.score(0)[0]))  # the parts add up to the total
    return result


def test_rules_contributions_of_exchange_groups(deeper_model):
    assert np.isclose(float(deeper_model.score(0)[0]), -0.19)
    # first match: the avoided products with their supply chains (feed and the milk it takes, fertiliser)
    first = _split(deeper_model, {"avoided": AVOIDED}, "first_match")
    assert np.isclose(first["avoided"], -0.5 * (0.4 + 2 * 1.0) - 0.1 * 2.0)
    # own emissions: those of feed and fertiliser only, for the share the avoided exchanges bring in
    direct = _split(deeper_model, {"avoided": AVOIDED}, "direct")
    assert np.isclose(direct["avoided"], -0.5 * 0.4 - 0.1 * 2.0)
    credits = _split(deeper_model, {"credits": {"field": "exchange_amount", "op": "lt", "value": 0}}, "direct")
    assert np.isclose(credits["credits"], -0.2 - 0.2 + 0.2 * 0.3)  # the waste too


def test_exchange_groups_go_by_the_order_of_the_rules(deeper_model):
    groups = {"feed": {"field": "name", "op": "eq", "value": "feed"}, "avoided": AVOIDED}
    feed_first = _split(deeper_model, groups, "first_match", order=["feed", "avoided"])
    assert np.isclose(feed_first["feed"], -0.5 * 2.4) and np.isclose(feed_first["avoided"], -0.2)
    avoided_first = _split(deeper_model, groups, "first_match", order=["avoided", "feed"])
    assert np.isclose(avoided_first["avoided"], -1.4) and np.isclose(avoided_first["feed"], 0)
    # a flow group listed first takes its flows from the exchange group
    flows = _split(deeper_model, {"avoided": AVOIDED}, "first_match",
                   flow_groups={"CO2": {"field": "name", "op": "eq", "value": "CO2"}}, order=["CO2", "avoided"])
    assert np.isclose(flows["avoided"], 0) and np.isclose(flows["CO2"], -0.19)


def test_contributors_of_an_exchange_group(deeper_model):
    members, values = group_contributors(deeper_model, 0, {"avoided": AVOIDED}, "avoided", "first_match")
    assert list(members) == [2, 5] and np.isclose(values.sum(), -1.4)


def test_flow_group_on_exchanges(deeper_model):
    big = {"field": "exchange_amount", "op": "ge", "value": 1}  # milk's 1 kg CO2 per kg, fertiliser's 2 kg
    result = _split(deeper_model, {}, "direct", flow_groups={"big": big})
    # milk: 1 kg for the cheese, -1 kg through the avoided feed; fertiliser: -0.1 kg avoided
    assert np.isclose(result["big"], 1.0 * (1 - 0.5 * 2) - 0.1 * 2.0)
    small = _split(deeper_model, {}, "direct", flow_groups={"small": {"field": "exchange_amount", "op": "lt", "value": 1}})
    assert np.isclose(small["small"], 0.1 + 0.5 * 0.1 - 0.5 * 0.4 + 0.2 * 0.3)


def test_numeric_operators():
    frame = pd.DataFrame({"x": [-1.0, 0.0, 2.5]})
    assert list(evaluate({"field": "x", "op": "lt", "value": 0}, frame)) == [True, False, False]
    assert list(evaluate({"field": "x", "op": "ge", "value": "0"}, frame)) == [False, True, True]
    assert list(evaluate({"field": "x", "op": "eq", "value": "2.5"}, frame)) == [False, False, True]
    with pytest.raises(ValueError):
        evaluate({"field": "x", "op": "gt", "value": "a"}, frame)
