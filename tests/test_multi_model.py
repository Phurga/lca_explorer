"""Several models in one server, each under /db/<key>/."""

import pytest
from fastapi.testclient import TestClient

from lca_viewer.cli import _per_model
from lca_viewer.server import app_for, model_key


@pytest.fixture
def two_models(tmp_path, linear_chain_model, loop_model):
    linear_chain_model.save(tmp_path / "bread")
    loop_model.save(tmp_path / "energy system")
    return [
        {"path": str(tmp_path / "bread"), "title": "Bread"},
        {"path": str(tmp_path / "energy system"), "title": "Energy", "default_root": "coal|world"},
    ]


def test_each_model_has_its_own_api(two_models):
    client = TestClient(app_for(two_models))
    assert client.get("/api/databases").json() == [
        {"key": "bread", "title": "Bread", "activities": 3},
        {"key": "energy-system", "title": "Energy", "activities": 2},
    ]
    root = client.get("/", follow_redirects=False)
    assert root.status_code in (302, 307) and root.headers["location"] == "/db/bread/"

    bread = client.get("/db/bread/api/info").json()
    energy = client.get("/db/energy-system/api/info").json()
    assert (bread["title"], bread["activities"], bread["default_root"]) == ("Bread", 3, None)
    assert (energy["title"], energy["activities"], energy["default_root"]["name"]) == ("Energy", 2, "coal")
    # The page and the shared static files are served for every model
    assert client.get("/db/energy-system/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    found = client.post("/db/bread/api/search", json={"text": "flour"}).json()
    assert [r["name"] for r in found["results"]] == ["flour"]


def test_single_model_keeps_the_plain_urls(two_models):
    client = TestClient(app_for(two_models[:1]))
    assert client.get("/api/info").json()["title"] == "Bread"
    assert client.get("/api/databases").status_code == 404  # no picker


def test_model_keys():
    assert model_key("data/cache/agb32") == "agb32"
    assert model_key("C:/x/ei 3.9.1 (cut-off)/") == "ei-3.9.1-cut-off"


def test_per_model_options():
    assert _per_model(None, 2, "--default-root") == [None, None]
    assert _per_model(["a|GLO"], 2, "--default-root") == ["a|GLO", "a|GLO"]
    assert _per_model(["a|GLO", "b|FR"], 2, "--default-root") == ["a|GLO", "b|FR"]
    with pytest.raises(SystemExit):
        _per_model(["a", "b", "c"], 2, "--default-root")
