"""The methods library (data/methods): methods made, copied from a project or imported from a CSV file, edited, and
built into models like the methods of a project (needs Brightway for the projects)."""

import time

import numpy as np
import pytest

from lca_viewer import methods_library
from lca_viewer.server import _method_family

GWP = ("toy", "gwp")


@pytest.fixture
def project(tmp_path):
    """A Brightway folder with one project: a biosphere of three flows, a background database and a method."""
    pytest.importorskip("bw2calc")
    import bw2data as bd
    from bw2data.tests import _close_sqlite_handles

    bd.config.dont_warn = True
    bd.config.is_test = True
    _close_sqlite_handles()
    bd.projects.change_base_directories(base_dir=tmp_path, base_logs_dir=tmp_path, project_name="toy", update=False)
    bd.projects._is_temp_dir = True
    bd.Database("bio").write({
        ("bio", "co2"): {"name": "Carbon dioxide, fossil", "categories": ("air",), "unit": "kilogram", "type": "emission"},
        ("bio", "ch4"): {"name": "Methane", "categories": ("air", "urban air close to ground"), "unit": "kilogram",
                         "type": "emission"},
        ("bio", "water"): {"name": "Water, river", "categories": ("natural resource", "in water"),
                           "unit": "cubic meter", "type": "natural resource"},
    })

    def process(code, co2, ch4):
        return ("bg", code), {"name": code, "reference product": code, "location": "FR", "unit": "kilogram",
                              "type": "process", "exchanges": [
                                  {"input": ("bg", code), "amount": 1.0, "type": "production"},
                                  {"input": ("bio", "co2"), "amount": co2, "type": "biosphere"},
                                  {"input": ("bio", "ch4"), "amount": ch4, "type": "biosphere"}]}

    bd.Database("bg").write(dict([process("elec", 0.1, 0.0), process("feed", 0.5, 0.01)]))
    bd.Method(GWP).register(unit="kg CO2-eq", description="Toy GWP")
    bd.Method(GWP).write([(("bio", "co2"), 1.0), (("bio", "ch4"), 28.0)])
    yield tmp_path
    _close_sqlite_handles()


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    return TestClient(create_library(tmp_path / "data"))


def wait(client, job):
    for _ in range(600):
        if job["status"] != "running":
            return job
        time.sleep(0.1)
        job = client.get(f"/api/jobs/{job['id']}").json()
    raise AssertionError("job still running")


def test_names_and_families():
    assert methods_library.model_name(["My EF", "climate change", "GWP100"]) == ["library", "My EF", "climate change", "GWP100"]
    # The library is the source of its methods, as a release is that of ecoinvent's
    assert _method_family(["library", "My EF", "climate change", "GWP100"]) == ("library", "My EF")
    assert _method_family(["library", "Mine"]) == ("library", "Mine")
    assert _method_family(["ecoinvent-3.10.1", "EF v3.1", "climate change", "GWP100"]) == ("ecoinvent-3.10.1", "EF v3.1")
    assert _method_family(["EF v3.1", "climate change", "GWP100"]) == ("", "EF v3.1")
    assert methods_library.clean_name(["  My  EF ", "", "gwp"]) == ["My EF", "gwp"]
    with pytest.raises(ValueError):
        methods_library.clean_name(["", " "])
    with pytest.raises(ValueError):
        methods_library.clean_name(["library", "x"])


def test_create_edit_duplicate_and_delete(project, client):
    folder = str(project)
    made = client.post("/api/library/methods", json={"name": ["Mine", "climate"], "unit": "kg CO2-eq"}).json()
    assert made["factors"] == 0 and made["model_name"] == ["library", "Mine", "climate"]
    assert client.post("/api/library/methods", json={"name": ["Mine", "climate"]}).status_code == 400  # taken
    assert (project / "data" / "methods" / f"{made['id']}.json").exists()
    id = made["id"]

    # A factor added from the project's flows (as the search gives them), changed, another added, one removed
    co2 = next(n for n in client.get("/api/brightway/search", params={"folder": folder, "project": "toy", "kind": "emission",
                                                                    "q": "carbon"}).json())
    out = client.post(f"/api/library/methods/{id}/factors", json={"set": [{**co2, "cf": 1.0}]}).json()
    assert [(f["database"], f["code"], f["cf"], f["categories"]) for f in out["factors"]] == [("bio", "co2", 1.0, ["air"])]
    out = client.post(f"/api/library/methods/{id}/factors", json={"set": [
        {"database": "bio", "code": "co2", "cf": "2,5"}, {"database": "bio", "code": "ch4", "name": "Methane", "cf": 30}]}).json()
    assert [(f["code"], f["cf"], f["name"]) for f in out["factors"]] == [("co2", 2.5, "Carbon dioxide, fossil"), ("ch4", 30.0, "Methane")]
    out = client.post(f"/api/library/methods/{id}/factors", json={"remove": [["bio", "ch4"]]}).json()
    assert [f["code"] for f in out["factors"]] == ["co2"]
    assert client.post(f"/api/library/methods/{id}/factors", json={"set": [{"database": "bio", "code": "co2", "cf": "x"}]}).status_code == 400

    # Renamed: the former name is kept, for the builds made with it
    renamed = client.post(f"/api/library/methods/{id}", json={"name": ["Mine", "climate change"], "unit": "kg"}).json()
    assert renamed["name"] == ["Mine", "climate change"] and renamed["unit"] == "kg"
    library = methods_library.Library(project / "data" / "methods")
    assert library.find(["library", "Mine", "climate"])["id"] == id

    # The factors in a project: each one's flow there
    read = client.get(f"/api/library/methods/{id}", params={"folder": folder, "project": "toy"}).json()
    assert read["coverage"] == {"matched": 1, "total": 1, "project": "toy"} and read["factors"][0]["flow"] > 0

    copy = client.post(f"/api/library/methods/{id}/duplicate", json={"name": ["Mine", "climate change", "copy"]}).json()
    assert copy["factors"] == 1 and copy["id"] != id
    assert [m["name"] for m in client.get("/api/library/methods").json()] == [["Mine", "climate change"], ["Mine", "climate change", "copy"]]
    left = client.post(f"/api/library/methods/{copy['id']}/delete").json()
    assert [m["id"] for m in left] == [id]
    assert client.get(f"/api/library/methods/{copy['id']}").status_code == 404
    assert client.get("/api/library/methods/../../x").status_code == 404


def test_copy_from_a_project(project, client):
    folder = str(project)
    copy = client.post("/api/library/methods/copy", json={"folder": folder, "project": "toy", "method": list(GWP)}).json()
    assert copy["name"] == ["toy (copy)", "gwp"] and copy["unit"] == "kg CO2-eq" and copy["description"] == "Toy GWP"
    assert copy["origin"] == {"project": "toy", "method": ["toy", "gwp"]}
    factors = client.get(f"/api/library/methods/{copy['id']}").json()["factors"]
    assert sorted((f["code"], f["cf"], f["name"], f["unit"]) for f in factors) == [
        ("ch4", 28.0, "Methane", "kilogram"), ("co2", 1.0, "Carbon dioxide, fossil", "kilogram")]
    # Again: the name is taken, another one is given
    assert client.post("/api/library/methods/copy", json={"folder": folder, "project": "toy", "method": list(GWP)}).status_code == 400
    named = client.post("/api/library/methods/copy", json={"folder": folder, "project": "toy", "method": list(GWP),
                                                          "name": ["Other", "gwp"]}).json()
    assert named["factors"] == 2
    assert client.post("/api/library/methods/copy", json={"folder": folder, "project": "toy", "method": ["no", "such"]}).status_code == 400


CSV = """Flow name;Compartment;Subcompartment;Unit;Factor
Carbon dioxide, fossil;air;unspecified;kg;1
methane;Air;urban air close to ground;kg;29,7
Water, river;natural resource;in water;kg;3
Dinitrogen monoxide;air;;kg;273
Carbon dioxide, fossil;air;;kg;not a number
"""


def test_csv_import_matches_the_flows_of_a_project(project, client, tmp_path):
    folder = str(project)
    path = tmp_path / "factors.csv"
    path.write_text(CSV, encoding="utf-8")
    out = client.post("/api/library/methods/import", json={"folder": folder, "project": "toy", "path": str(path),
                                                          "name": ["Imported", "gwp"], "unit": "kg CO2-eq"}).json()
    assert out["matched"] == 2 and out["method"]["factors"] == 2
    reasons = {(r["line"], r["name"]): r["reason"] for r in out["unmatched"]}
    assert reasons == {(6, "Carbon dioxide, fossil"): "the factor 'not a number' is not a number",
                       (4, "Water, river"): "the flow is in cubic meter, not kg",
                       (5, "Dinitrogen monoxide"): "no flow of this name in these compartments"}
    method = client.get(f"/api/library/methods/{out['method']['id']}").json()
    assert sorted((f["database"], f["code"], f["cf"]) for f in method["factors"]) == [("bio", "ch4", 29.7), ("bio", "co2", 1.0)]

    # Exported, and imported again into the method: the database and code columns find the flows
    text = client.get(f"/api/library/methods/{out['method']['id']}/csv").text
    assert text.splitlines()[0] == "name,compartment,subcompartment,unit,factor,database,code"
    assert "Methane,air,urban air close to ground,kilogram,29.7,bio,ch4" in text
    again = tmp_path / "again.csv"
    again.write_text(text.replace("29.7", "30.0"), encoding="utf-8")
    out = client.post("/api/library/methods/import", json={"folder": folder, "project": "toy", "path": str(again),
                                                          "id": out["method"]["id"]}).json()
    assert out["matched"] == 2 and out["unmatched"] == [] and out["method"]["factors"] == 2
    method = client.get(f"/api/library/methods/{out['method']['id']}").json()
    assert sorted(f["cf"] for f in method["factors"]) == [1.0, 30.0]

    # A file without a header row, in this order; nothing matching: refused
    plain = tmp_path / "plain.csv"
    plain.write_text("Nothing at all,air,,kg,1\n", encoding="utf-8")
    refused = client.post("/api/library/methods/import", json={"folder": folder, "project": "toy", "path": str(plain),
                                                              "name": ["None"]})
    assert refused.status_code == 400 and "No row" in refused.json()["detail"]


def test_flows_found_by_code_in_another_biosphere():
    flows = [{"id": 7, "database": "ecoinvent-3.10-biosphere", "code": "349b", "name": "Carbon dioxide, fossil",
              "categories": ["air"], "unit": "kilogram", "type": "emission"}]
    matcher = methods_library.Matcher(flows)
    assert matcher.find("biosphere3", "349b")[0]["id"] == 7
    assert matcher.find("biosphere3", "zzz", "carbon dioxide, FOSSIL", ["Air", "unspecified"], "kg") == (flows[0], "name")
    assert matcher.find("biosphere3", "zzz", "Carbon dioxide, fossil", ["water"], "kg")[0] is None


def test_a_build_and_an_addition_with_library_methods(project, client):
    """Library methods are chosen at build like the project's, enter the model's characterisation, and an edit of
    one makes the build outdated; one is added to the open model too."""
    folder = str(project)
    triple = client.post("/api/library/methods", json={"name": ["Mine", "co2 three times"], "unit": "kg"}).json()
    client.post(f"/api/library/methods/{triple['id']}/factors", json={"set": [{"database": "bio", "code": "co2", "cf": 3}]})
    ch4 = client.post("/api/library/methods", json={"name": ["Mine", "methane"], "unit": "kg CH4"}).json()
    client.post(f"/api/library/methods/{ch4['id']}/factors", json={"set": [{"database": "bio", "code": "ch4", "cf": 1}]})

    params = {"folder": folder, "project": "toy", "database": "bg"}
    listed = client.get("/api/brightway/methods", params=params).json()
    assert [m["name"] for m in listed["library"]] == [["library", "Mine", "co2 three times"], ["library", "Mine", "methane"]]
    assert listed["library"][0]["matched"] == 1 and listed["library"][0]["factors"] == 1

    body = {"brightway": folder, "project": "toy", "database": "bg"}
    job = wait(client, client.post("/api/load", json={**body, "methods": [list(GWP), ["library", "Mine", "co2 three times"]]}).json())
    assert job["status"] == "done", job
    key = job["key"]
    info = client.get(f"/db/{key}/api/info").json()
    assert [m["name"] for m in info["methods"]] == [["toy", "gwp"], ["library", "Mine", "co2 three times"]]
    assert info["methods"][1]["unit"] == "kg"

    def impacts(key, index_of="feed", n=2):
        found = client.post(f"/db/{key}/api/search", json={"text": index_of}).json()
        index = next(a["index"] for a in (found if isinstance(found, list) else found["results"]) if a["name"] == index_of)
        return [client.get(f"/db/{key}/api/activity/{index}", params={"method": k}).json()["cumulative_per_unit"] for k in range(n)]

    gwp, mine = impacts(key)
    assert np.isclose(gwp, 0.5 + 0.28) and np.isclose(mine, 1.5)
    assert client.get("/api/brightway/methods", params=params).json()["selected"] == [["toy", "gwp"], ["library", "Mine", "co2 three times"]]

    # Added to the open model: only its impacts are computed
    job = wait(client, client.post("/api/methods/add", json={"key": key, "methods": [["library", "Mine", "methane"]]}).json())
    assert job["status"] == "done" and job["added"] == 1, job
    info = client.get(f"/db/{key}/api/info").json()
    assert [m["name"] for m in info["methods"]][-1] == ["library", "Mine", "methane"]
    assert np.isclose(impacts(key, n=3)[2], 0.01)

    # Edited and renamed: the build is outdated, and the rebuild takes the new factor under the new name
    def state():
        projects = client.get("/api/brightway", params={"folder": folder, "refresh": True}).json()["projects"]
        return next(d for d in next(p for p in projects if p["name"] == "toy")["databases"] if d["name"] == "bg")["model"]

    assert not state()["outdated"]
    client.post(f"/api/library/methods/{triple['id']}/factors", json={"set": [{"database": "bio", "code": "co2", "cf": 4}]})
    client.post(f"/api/library/methods/{triple['id']}", json={"name": ["Mine", "co2 four times"]})
    assert state()["outdated"] == "methods of the library changed since the model was built"
    selected = client.get("/api/brightway/methods", params=params).json()["selected"]
    assert ["library", "Mine", "co2 four times"] in selected and ["library", "Mine", "co2 three times"] not in selected
    client.post("/api/unload", json={"key": key})
    job = wait(client, client.post("/api/load", json={**body, "rebuild": True}).json())
    assert job["status"] == "done", job
    info = client.get(f"/db/{job['key']}/api/info").json()
    # The method renamed is a new one for the project cache: its column comes after those kept
    assert [m["name"] for m in info["methods"]] == [["toy", "gwp"], ["library", "Mine", "methane"], ["library", "Mine", "co2 four times"]]
    assert np.isclose(impacts(job["key"], n=3)[2], 2.0)
    assert not state()["outdated"]

    # A build of library methods only (no method of the project to read the matrices with)
    client.post("/api/unload", json={"key": job["key"]})
    job = wait(client, client.post("/api/load", json={**body, "rebuild": True, "methods": [["library", "Mine", "methane"]]}).json())
    assert job["status"] == "done", job
    assert [m["name"] for m in client.get(f"/db/{job['key']}/api/info").json()["methods"]] == [["library", "Mine", "methane"]]
