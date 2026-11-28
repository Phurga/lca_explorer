"""Exchange metadata (comment, uncertainty, pedigree): cache round trip and API."""

import pytest
from fastapi.testclient import TestClient

from lca_viewer.model import LCAModel
from lca_viewer.server import create_app

PEDIGREE = {"reliability": 2, "completeness": 3, "temporal correlation": 5,
            "geographical correlation": 5, "further technological correlation": 1}

EXCHANGES = [
    # bread (2): its production, 0.8 kg flour, 0.3 kg CO2
    (2, "production", 2, {"amount": 1.0, "unit": "kilogram", "comment": "EcoSpold01Location=CH",
                          "production volume": 1000.0, "properties": {"price": {"amount": 0.4, "unit": "EUR2005"}}}),
    (2, "technosphere", 1, {"amount": 0.8, "unit": "kilogram", "comment": "literature", "uncertainty type": 2,
                            "loc": -0.22, "scale": 0.1, "scale without pedigree": 0.05, "pedigree": PEDIGREE}),
    (2, "biosphere", 0, {"amount": 0.3, "unit": "kilogram", "comment": "literature", "formula": "a * 2"}),
    # flour (1): an unusual pedigree is kept as is
    (1, "technosphere", 0, {"amount": 0.5, "unit": "kilogram", "pedigree": {"reliability": 1}}),
]


@pytest.fixture
def model(linear_chain_model, tmp_path):
    linear_chain_model.exchanges = EXCHANGES
    linear_chain_model.save(tmp_path)
    return LCAModel.load(tmp_path)


def test_round_trip(model):
    production, flour, co2 = model.exchange_details(2)
    assert production == {"type": "production", "row": 2, **EXCHANGES[0][3]}
    assert flour == {"type": "technosphere", "row": 1, **EXCHANGES[1][3]}
    assert co2 == {"type": "biosphere", "row": 0, **EXCHANGES[2][3]}
    assert model.exchange_details(1)[0]["pedigree"] == {"reliability": 1}
    assert model.exchange_details(0) == []


def test_no_exchanges(linear_chain_model, tmp_path):
    linear_chain_model.save(tmp_path)
    loaded = LCAModel.load(tmp_path)
    assert not loaded.has_exchanges and loaded.exchange_details(2) == []


def test_api(model):
    client = TestClient(create_app(model))
    body = client.get("/api/activity/2/exchanges?method=0").json()
    assert body["available"]
    assert body["production"]["production volume"] == 1000.0
    [flour] = body["inputs"]
    assert flour["reference_product"] == "flour" and flour["comment"] == "literature"
    assert flour["pedigree"] == PEDIGREE
    # 0.8 kg flour x 1.5 kg CO2-eq/kg (0.5 direct + 0.5 kg wheat x 2)
    assert flour["impact"] == pytest.approx(1.2)
    [co2] = body["flows"]
    assert co2["impact"] == pytest.approx(0.3) and co2["formula"] == "a * 2"
    # The input of one product, for a tree row's link to its parent
    assert client.get("/api/activity/2/exchanges?product=1").json()["inputs"] == [flour]
    assert client.get("/api/activity/2/exchanges?product=0").json()["inputs"] == []


def test_flow_info_carries_the_exchange_comment(model):
    client = TestClient(create_app(model))
    info = client.get("/api/flow/0?method=0&activities=2,1").json()
    assert info["exchanges_available"]
    bread, flour = info["exchanges"]
    assert bread["exchanges"] == [EXCHANGES[2][3]]  # amount, comment, formula as recorded
    assert flour["exchanges"] == []  # activity 1 has no CO2 exchange recorded


def test_rows_carry_the_comments_of_flow_exchanges(model):
    """The *Comment* column of the list: a flow row has the comment of its exchange, in the dataset of the process
    emitting it, as a process row has that of the exchange supplying it (it used to be technosphere only)."""
    client = TestClient(create_app(model))
    state = {"root": [2], "expanded": ["2", "2/-1"], "method": 0}
    rows = {r["id"]: r for r in client.post("/api/rows", json=state).json()["rows"]}
    assert rows["2/1"]["comment"] == "literature"  # flour, supplied to bread
    co2 = next(r for r in rows.values() if r["kind"] == "flow" and r["flow"]["index"] == 0)
    assert co2["comment"] == "literature" and co2["exchanges"] == [{"formula": "a * 2"}]
    assert all("comment" not in r for r in rows.values() if r["kind"] == "flow" and r is not co2)  # none recorded


def test_inputs_say_whether_they_are_waste(model):
    # an input of a waste treatment (negative production in A) is waste to treatment: the Exchanges panel's section
    client = TestClient(create_app(model))
    [flour] = client.get("/api/activity/2/exchanges").json()["inputs"]
    assert flour["waste"] is False
    model.diagonal[1] = -model.diagonal[1]
    [flour] = TestClient(create_app(model)).get("/api/activity/2/exchanges").json()["inputs"]
    assert flour["waste"] is True


def test_activity_names_its_location(grouping_model):
    # the Information panel reads "FR · France": the name of the location code, from codes.py
    client = TestClient(create_app(grouping_model))
    names = [client.get(f"/api/activity/{i}").json()["location_name"] for i in (0, 1, 4)]
    assert names == ["Global", "France", "Europe"]


def test_a_model_open_while_its_exchanges_are_read(linear_chain_model):
    """A database is opened once its results are solved, before its build has read the exchanges: the tree, the
    impacts and the pop-ups answer, and say the exchanges are coming rather than missing."""
    linear_chain_model.exchanges_pending = True
    client = TestClient(create_app(linear_chain_model))
    info = client.get("/api/info").json()
    assert info["pending"] == ["exchanges"] and info["pending_error"] is None
    rows = client.post("/api/rows", json={"root": [2], "expanded": ["2"]}).json()["rows"]
    assert len(rows) > 1 and all("comment" not in r for r in rows)
    body = client.get("/api/activity/2/exchanges?method=0").json()
    assert body["pending"] and not body["available"] and body["inputs"] == []
    assert client.get("/api/activity/2").status_code == 200
    flow = client.get("/api/flow/0", params={"activities": "2"}).json()
    assert flow["exchanges_pending"] and flow["exchanges"][0]["exchanges"] == []
    # Once read (``project_cache.attach_exchanges``), nothing is pending
    linear_chain_model.exchanges_pending = False
    assert client.get("/api/info").json()["pending"] == []
