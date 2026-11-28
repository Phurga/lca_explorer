"""The project cache (segments per database): a database opened from segments is the model a build of its own gives
(needs Brightway)."""

import numpy as np
import pytest

from lca_viewer import project_cache

GWP, CH4 = ("toy", "gwp"), ("toy", "ch4")
METHODS = [GWP, CH4]


@pytest.fixture
def project(tmp_path):
    """bio; bg (electricity and steel use each other, feed); fg (a farm on bg, a dairy on the farm); two methods."""
    pytest.importorskip("bw2calc")
    import bw2data as bd
    from bw2data.tests import _close_sqlite_handles

    bd.config.dont_warn = True
    bd.config.is_test = True
    _close_sqlite_handles()
    bd.projects.change_base_directories(base_dir=tmp_path, base_logs_dir=tmp_path, project_name="toy",
                                        update=False)
    bd.projects._is_temp_dir = True
    bd.Database("bio").write({
        ("bio", "co2"): {"name": "Carbon dioxide", "categories": ("air",), "unit": "kilogram", "type": "emission"},
        ("bio", "ch4"): {"name": "Methane", "categories": ("air",), "unit": "kilogram", "type": "emission"},
        ("bio", "n2o"): {"name": "Dinitrogen monoxide", "categories": ("air",), "unit": "kilogram", "type": "emission"},
    })
    write("bg", {"elec": [("bio", "co2", 0.1), ("bg", "steel", 0.01)],
                 "steel": [("bio", "co2", 2.0), ("bg", "elec", 5.0)],
                 "feed": [("bio", "co2", 0.5), ("bg", "elec", 0.2)]})
    write("fg", {"farm": [("bg", "feed", 4.0), ("bg", "elec", 1.0), ("bio", "co2", 3.0), ("bio", "ch4", 0.1)],
                 "dairy": [("fg", "farm", 2.0), ("bg", "elec", 0.5)]})
    bd.Method(GWP).register(unit="kg CO2-eq")
    bd.Method(GWP).write([(("bio", "co2"), 1.0), (("bio", "ch4"), 28.0), (("bio", "n2o"), 265.0)])
    bd.Method(CH4).register(unit="kg CH4")
    bd.Method(CH4).write([(("bio", "ch4"), 1.0)])
    yield tmp_path
    _close_sqlite_handles()


def write(database, processes):
    import bw2data as bd

    data = {}
    for code, inputs in processes.items():
        exchanges = [{"input": (database, code), "amount": 1.0, "type": "production"}]
        exchanges += [{"input": (db, c), "amount": a, "type": "biosphere" if db == "bio" else "technosphere"}
                      for db, c, a in inputs]
        data[(database, code)] = {"name": code, "reference product": code, "location": "FR", "unit": "kilogram",
                                  "type": "process", "exchanges": exchanges}
    bd.Database(database).write(data)


def assert_same(model, database, methods=METHODS):
    """``model`` (from segments) has the activities, ``G`` and exchanges of the model a build of its own gives."""
    from lca_viewer.brightway import from_brightway

    whole = from_brightway("toy", database, methods)
    ids = whole.activities["id"].tolist()
    assert sorted(model.activities["id"]) == sorted(ids)
    at = {i: k for k, i in enumerate(model.activities["id"])}
    order = [at[i] for i in ids]
    assert [m.name for m in model.methods] == [tuple(m) for m in methods]
    np.testing.assert_allclose(model.G[order], whole.G, rtol=1e-12, atol=1e-14)
    np.testing.assert_allclose(model.direct_impacts()[:, order], whole.direct_impacts(), rtol=1e-12, atol=1e-14)
    flow_id, product_id = model.flows["id"].to_numpy(), model.activities["id"].to_numpy()
    whole_flow, whole_product = whole.flows["id"].to_numpy(), whole.activities["id"].to_numpy()

    def linked(exchanges, flows, products):
        return sorted((e["type"], int((flows if e["type"] == "biosphere" else products)[e["row"]]), e["amount"])
                      for e in exchanges)

    for k, i in enumerate(ids):
        assert linked(model.exchange_details(at[i]), flow_id, product_id) == linked(whole.exchange_details(k), whole_flow, whole_product)
        assert model.activity_details(at[i]) == whole.activity_details(k)


def versions(cache):
    info = project_cache.read_project(cache)
    return {tuple(r["databases"]): (r["version"], r.get("basis")) for r in info["segments"]}


def test_a_foreground_on_a_background(project):
    cache = project / "cache"
    project_cache.build("toy", "fg", METHODS, cache)
    assert set(versions(cache)) == {("bg",), ("fg",)}  # bio holds no processes: rows of B
    assert_same(project_cache.load(cache, "fg"), "fg")
    assert_same(project_cache.load(cache, "bg"), "bg")  # built with it, as the segment fg uses
    assert project_cache.status(cache, "bg")["built"] and not project_cache.status(cache, "bio")["built"]

    before = versions(cache)
    project_cache.build("toy", "fg", None, cache)  # nothing changed: nothing read or solved again
    assert versions(cache) == before


def test_a_rebuild_of_the_background(project):
    import bw2data as bd

    from lca_viewer import sources

    cache = project / "cache"
    project_cache.build("toy", "fg", METHODS, cache)
    before = versions(cache)
    feed = bd.get_activity(("bg", "feed"))
    exchange = next(iter(feed.biosphere()))
    exchange["amount"] = 0.7
    exchange.save()

    p = next(p for p in sources.brightway_projects(project) if p["name"] == "toy")
    assert project_cache.status(cache, "fg", p)["outdated"] == "bg (linked) changed since the model was built"
    project_cache.build("toy", "bg", None, cache)  # the background alone: the foreground above it is solved again
    after = versions(cache)
    assert after[("bg",)][0] != before[("bg",)][0]
    assert after[("fg",)][0] == before[("fg",)][0] and after[("fg",)][1] != before[("fg",)][1]  # not read again
    assert project_cache.status(cache, "fg", p)["outdated"] is None
    assert_same(project_cache.load(cache, "fg"), "fg")


def test_a_loop_between_two_databases(project):
    import bw2data as bd

    cache = project / "cache"
    project_cache.build("toy", "fg", METHODS, cache)
    model = project_cache.load(cache, "fg")
    # The background now takes some of the farm's product: bg and fg use each other
    steel = bd.get_activity(("bg", "steel"))
    steel.new_exchange(input=("fg", "farm"), amount=0.05, type="technosphere").save()
    messages = []
    project_cache.build("toy", "fg", None, cache, log=messages.append)
    # bw2data leaves ``depends`` as it was: the link is found in the exchanges as they are read
    assert "bg: its exchanges link to other databases than its metadata lists: built again" in messages
    assert "bg, fg use products of each other: they are built together" in messages
    assert set(versions(cache)) == {("bg", "fg")}
    assert_same(project_cache.load(cache, "fg"), "fg")
    assert_same(project_cache.load(cache, "bg"), "bg")
    # The folders of the segments replaced wait for the models that read them
    retired = project_cache.read_project(cache)["retired"]
    assert set(model.project_cache["folders"]) <= {str(cache / "segments" / name) for name in retired}
    project_cache.remove_retired(cache, model.project_cache["folders"])
    assert len(project_cache.read_project(cache)["retired"]) == 2
    messages.clear()
    project_cache.build("toy", "bg", None, cache, log=messages.append)  # the links are known now
    assert not any("built again" in m or m.startswith("Reading") for m in messages)
    project_cache.remove_retired(cache, [])
    assert project_cache.read_project(cache)["retired"] == []
    assert sorted(p.name for p in (cache / "segments").iterdir()) == [project_cache.read_project(cache)["segments"][0]["folder"]]


def test_methods_added_and_removed(project):
    cache = project / "cache"
    project_cache.build("toy", "fg", [GWP], cache)
    model = project_cache.load(cache, "fg")
    solved = (project_cache.folder(cache, project_cache.read_project(cache)["segments"][0]) / "G.npy").stat().st_mtime_ns
    project_cache.add_methods("toy", "fg", cache, [list(CH4)])
    assert project_cache.method_names(cache) == [list(GWP), list(CH4)]
    methods, columns, unlinked = project_cache.method_columns(model, [list(CH4)])
    assert model.add_methods(methods, unlinked=unlinked, columns=columns) == 1
    assert_same(model, "fg")
    assert_same(project_cache.load(cache, "fg"), "fg")
    # The factor of n2o, a flow of no process: outside the model
    assert list(model.unlinked["name"]) == ["Dinitrogen monoxide"]

    project_cache.build("toy", "fg", [CH4], cache)  # GWP removed: its columns dropped, nothing solved
    assert_same(project_cache.load(cache, "fg"), "fg", [CH4])
    assert solved  # (the time of the first solve, for the reader)


def test_results_dropped_are_solved_again(project):
    cache = project / "cache"
    project_cache.build("toy", "fg", METHODS, cache)
    info = project_cache.read_project(cache)
    project_cache.drop_results(cache, [r["id"] for r in info["segments"] if r["databases"] == ["bg"]])
    assert project_cache.status(cache, "fg")["solve"]
    with pytest.raises(ValueError, match="compute again"):
        project_cache.load(cache, "fg")
    before = versions(cache)
    project_cache.build("toy", "fg", None, cache)
    assert versions(cache) == before  # the same numbers: the basis of fg holds
    assert_same(project_cache.load(cache, "fg"), "fg")


def snapshot(model):
    """What a model is made of, to compare two builds: its matrices, results, tables and exchanges, by node id."""
    order = np.argsort(model.activities["id"].to_numpy())
    flows = model.flows["id"].to_numpy()
    products = model.activities["id"].to_numpy()

    def exchanges(j):
        return [{**e, "row": int((flows if e["type"] == "biosphere" else products)[e["row"]])}
                for e in model.exchange_details(int(j))]

    return {"A": model.A[order][:, order].toarray(), "B": model.B[:, order].toarray(), "G": model.G[order],
            "activities": model.activities.iloc[order].reset_index(drop=True),
            "details": [model.activity_details(int(j)) for j in order], "exchanges": [exchanges(j) for j in order]}


def test_exchanges_read_in_parallel_give_the_same_model(project, monkeypatch):
    serial, parallel = project / "serial", project / "parallel"
    monkeypatch.setattr(project_cache, "EXCHANGE_WORKERS", 0)
    project_cache.build("toy", "fg", METHODS, serial)
    monkeypatch.setattr(project_cache, "EXCHANGE_WORKERS", 2)
    monkeypatch.setattr(project_cache, "PARALLEL_EXCHANGES", 0)  # the toy's few exchanges, in two processes
    messages = []
    project_cache.build("toy", "fg", METHODS, parallel, log=messages.append)
    assert any("in 2 processes" in m for m in messages)
    # The results first, the exchanges after
    ready = next(k for k, m in enumerate(messages) if m.startswith(project_cache.READY))
    assert ready < messages.index("Exchanges read")
    info = project_cache.read_project(parallel)
    record = next(r for r in info["segments"] if r["databases"] == ["bg"])
    assert len(project_cache.exchange_parts(project_cache.folder(parallel, record), 3)) == 2
    for database in ("fg", "bg"):
        a, b = snapshot(project_cache.load(serial, database)), snapshot(project_cache.load(parallel, database))
        for key in ("A", "B", "G"):
            np.testing.assert_array_equal(a[key], b[key])  # the same numbers, not close ones
        assert a["activities"].equals(b["activities"])
        assert a["details"] == b["details"] and a["exchanges"] == b["exchanges"]
    assert_same(project_cache.load(parallel, "fg"), "fg")


def test_a_database_opened_before_its_exchanges_are_read(project):
    cache = project / "cache"
    project_cache.build("toy", "fg", METHODS, cache)
    whole = snapshot(project_cache.load(cache, "fg"))
    info = project_cache.read_project(cache)
    for r in info["segments"]:  # as a build stopped after the results: the exchanges not read
        (project_cache.folder(cache, r) / project_cache.EXCHANGES).unlink()
    assert project_cache.status(cache, "fg")["exchanges"] is False
    model = project_cache.load(cache, "fg")  # it opens: the results are there
    assert not model.has_exchanges and model.exchange_details(0) == []
    np.testing.assert_array_equal(snapshot(model)["G"], whole["G"])
    before, messages = versions(cache), []
    project_cache.build("toy", "fg", None, cache, log=messages.append)  # the exchanges alone are read
    assert versions(cache) == before and not any(m.startswith(("Reading the matrices", "Solving")) for m in messages)
    assert project_cache.status(cache, "fg")["exchanges"] is True
    assert project_cache.attach_exchanges(model) and model.has_exchanges
    assert snapshot(model)["exchanges"] == whole["exchanges"]


def test_a_segment_with_its_exchanges_in_one_file(project):
    """Segments built before the exchanges were read by runs of activities have one exchanges.sqlite."""
    cache = project / "cache"
    project_cache.build("toy", "fg", METHODS, cache)
    whole = snapshot(project_cache.load(cache, "fg"))
    for r in project_cache.read_project(cache)["segments"]:
        path = project_cache.folder(cache, r)
        assert [p.name for p in path.glob("exchanges-*.sqlite")] == ["exchanges-0.sqlite"]  # small: one run
        (path / "exchanges-0.sqlite").rename(path / "exchanges.sqlite")
        (path / project_cache.EXCHANGES).unlink()
    assert snapshot(project_cache.load(cache, "fg"))["exchanges"] == whole["exchanges"]
