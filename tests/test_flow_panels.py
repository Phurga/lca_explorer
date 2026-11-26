"""The panels of a flow (the abstract biosphere node): characterisation with ranks, emitters, same substance,
name parts and metadata."""

import pytest
from fastapi.testclient import TestClient

from lca_viewer.server import create_app
from test_flows import flows_model  # noqa: F401  (fixture)


@pytest.fixture
def client(flows_model):  # noqa: F811
    return TestClient(create_app(flows_model))


def test_characterisation_ranks_the_flow_in_each_method(client):
    # climate change characterises flows 0, 1 (1.0) and 2 (273): N2O is first, CO2 second (tied) of 3
    n2o = client.get("/api/flow/2?method=0").json()
    assert n2o["factors"] == [{"method": 0, "cf": 273.0, "rank": 1, "of": 3}]
    co2 = client.get("/api/flow/0?method=0").json()
    assert co2["factors"][0]["rank"] == 2 and co2["factors"][0]["of"] == 3
    assert co2["same_substance"] == 1 and co2["emitters"] == 2
    assert co2["resource"] is False
    # nitrate: no method characterises it
    assert client.get("/api/flow/4").json()["factors"] == []


def test_exchange_carries_the_matrix_amount(client):
    found = client.get("/api/flow/1?activities=1").json()
    assert found["exchanges"][0]["amount"] == 2.0


def test_emitters_per_unit_and_in_a_supply_chain(client, flows_model):  # noqa: F811
    database = client.get("/api/flow/3/emitters").json()
    assert database["count"] == 2
    assert [e["index"] for e in database["emitters"]] == [2, 1]  # 4 > 3 per unit
    assert database["total"] is None
    scoped = client.get("/api/flow/3/emitters?root=0").json()
    s = flows_model.supply(0)
    expected = {1: 3.0 * s[1], 2: 4.0 * s[2]}
    assert {e["index"]: e["amount"] for e in scoped["emitters"]} == pytest.approx(expected)
    assert scoped["total"] == pytest.approx(sum(expected.values()))
    assert [e["index"] for e in scoped["emitters"]] == [1, 2]  # 1.8 > 1.6 in the market's chain


def test_same_substance_lists_the_compartments_with_the_active_factor(client):
    found = client.get("/api/flow/0/same-substance?method=0").json()
    assert [f["flow"] for f in found["flows"]] == [0, 1]
    assert found["flows"][0]["current"] and not found["flows"][1]["current"]
    assert found["methods"] == [0] and found["flows"][1]["cfs"] == [1.0]


def test_metadata_from_the_model_and_the_name(flows_model):  # noqa: F811
    flows_model.flows["cas"] = ["000124-38-9", "000124-38-9", None, None, None]
    flows_model.flows["synonyms"] = [("Carbon dioxide",), (), (), (), ()]
    flows_model.flows["location"] = [None, None, None, "RE", None]
    client = TestClient(create_app(flows_model))
    co2 = client.get("/api/flow/0").json()
    assert co2["description"] == {"cas": "000124-38-9", "synonyms": ["Carbon dioxide"]}
    assert co2["name_parts"] == {"substance": "Carbon dioxide", "qualifiers": ["fossil"]}
    assert co2["location"] is None
    assert client.get("/api/flow/3").json()["location"] == "RE"


def test_unknown_flow_is_404(client):
    for url in ("/api/flow/99", "/api/flow/99/emitters", "/api/flow/-1/same-substance"):
        assert client.get(url).status_code == 404
