"""Storage dialog (sizes, deletions) and the links between the databases of a project."""

import json
import sqlite3
from contextlib import closing

from pathlib import Path

import numpy as np
import pytest
from conftest import write_project_cache

from lca_viewer import sources


def test_folder_size_and_unregister(tmp_path, linear_chain_model):
    assert sources.folder_size(tmp_path / "nothing") == 0
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "x").write_bytes(b"12345")
    (tmp_path / "a" / "b" / "y").write_bytes(b"123")
    assert sources.folder_size(tmp_path / "a") == 8

    data = tmp_path / "data"
    for name in ("one", "two"):
        linear_chain_model.save(data / "cache" / name)
        sources.register(data, data / "cache" / name, name, None)
    sources.unregister(data, data / "cache" / "one")
    assert [m["title"] for m in sources.catalog(data)] == ["two"]


def test_database_links_counted_and_kept(tmp_path):
    (tmp_path / "lci").mkdir()
    path = tmp_path / "lci" / "databases.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE exchangedataset (output_database TEXT, input_database TEXT)")
        db.executemany("INSERT INTO exchangedataset VALUES (?, ?)",
                       [("fg", "ei")] * 3 + [("fg", "bio"), ("ei", "bio"), ("ei", "ei"), ("ei", "fg")])
        db.commit()
    cache = {}
    links = sources.database_links(tmp_path, cache)
    # inside a database: not a link; ei -> fg: a link the metadata may not list (a loop between them)
    assert sorted(map(tuple, links)) == [("ei", "bio", 1), ("ei", "fg", 1), ("fg", "bio", 1), ("fg", "ei", 3)]
    assert sources.database_links(tmp_path, cache) is links  # kept while the file is not written
    assert sources.database_links(tmp_path / "missing") == []


@pytest.fixture
def library(tmp_path, linear_chain_model, monkeypatch):
    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    monkeypatch.setenv("BRIGHTWAY2_DIR", str(tmp_path / "bw"))  # no projects there
    data = tmp_path / "data"
    # A model of one database and all it uses, before the project cache: obsolete
    old = data / "cache" / "bw-0123456789" / "p-db"
    linear_chain_model.save(old)
    sources.register(data, old, "db (p)", None, {"brightway": str(tmp_path / "bw"), "project": "p", "database": "db"})
    model = data / "cache" / "imported"  # made from files
    linear_chain_model.save(model)
    np.save(model / "G.npy", np.zeros((3, 2)))
    (model / "G.json").write_text("{}")
    sources.register(data, model, "imported", None)
    (data / "cache" / "bw-0123456789" / "p-db.part").mkdir()
    (data / "cache" / "bw-0123456789" / "p-db.part" / "A.npz").write_bytes(b"x" * 10)
    (data / "cache" / "bw-empty").mkdir()
    linear_chain_model.save(data / "cache" / "unlisted")  # e.g. served with --model
    # A project cache: ei, and fg on it
    cache = write_project_cache(data, tmp_path / "bw", "p", linear_chain_model, [(["ei"], [0]), (["fg"], [1, 2])])
    (cache / "segments" / "fg-unfinished").mkdir()
    return TestClient(create_library(data)), data, model, cache


def test_storage_lists_and_deletes(library):
    client, data, model, cache = library
    listing = client.get("/api/storage").json()
    items = {i["title"]: i for i in listing["items"] if i["kind"] != "download"}
    assert set(items) == {"db (p)", "imported", "p-db.part", "bw-empty", "unlisted", "p", "ei", "fg", "p: fg-unfinished"}
    assert items["db (p)"]["kind"] == "obsolete" and items["imported"]["kind"] == "model"
    assert items["imported"]["results"] > 0 and not items["imported"]["open"]
    assert items["p-db.part"]["bulk"] and items["bw-empty"]["bulk"] and not items["unlisted"]["bulk"]
    assert items["p"]["kind"] == "project" and items["p"]["orphan"] == "project p no longer exists"
    assert items["ei"]["kind"] == "segment" and items["ei"]["users"] == ["fg"] and items["fg"]["users"] == []
    assert items["p: fg-unfinished"]["reason"] == "build left unfinished" and items["p: fg-unfinished"]["bulk"]
    assert listing["total"] == sum(i["size"] for i in listing["items"] if i["kind"] != "segment")  # in their project

    assert client.post("/api/storage/delete", json={"kind": "results", "path": str(data / "cache" / "unlisted")}).status_code == 400
    assert client.post("/api/storage/delete", json={"kind": "model", "path": str(data / "elsewhere")}).status_code == 404

    assert client.post("/api/storage/delete", json={"kind": "results", "path": str(model)}).status_code == 200
    assert not (model / "G.npy").exists() and (model / "A.npz").exists()
    fg = items["fg"]["path"]
    assert client.post("/api/storage/delete", json={"kind": "results", "path": fg}).status_code == 200
    assert not (Path(fg) / "G.npy").exists() and (Path(fg) / "A.npz").exists()

    assert client.post("/api/storage/delete", json={"kind": "leftovers"}).status_code == 200
    assert not (data / "cache" / "bw-0123456789" / "p-db.part").exists() and not (data / "cache" / "bw-empty").exists()
    assert not (cache / "segments" / "fg-unfinished").exists()
    assert (data / "cache" / "unlisted").exists()  # only deleted on its own

    # A segment goes with those that use it
    after = client.post("/api/storage/delete", json={"kind": "segment", "path": items["ei"]["path"]}).json()
    assert [i["title"] for i in after["items"] if i["kind"] == "segment"] == []
    assert list((cache / "segments").iterdir()) == []  # no open database reads them

    assert client.post("/api/storage/delete", json={"kind": "obsolete"}).status_code == 200
    assert not (data / "cache" / "bw-0123456789" / "p-db").exists()
    assert [m["title"] for m in sources.catalog(data)] == ["imported"]
    assert client.post("/api/storage/delete", json={"kind": "all"}).status_code == 200
    assert not cache.exists() and not model.exists() and sources.catalog(data) == []
    assert json.loads((data / "models.json").read_text()) == []


def test_an_open_segment_is_closed_first(library):
    client, data, model, cache = library
    job = client.post("/api/load", json={"path": str(sources.cached_database(cache, "p", "fg"))}).json()
    for _ in range(100):
        if job["status"] != "running":
            break
        job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done", job
    items = {i["title"]: i for i in client.get("/api/storage").json()["items"]}
    assert items["ei"]["open"] and items["fg"]["open"] and items["p"]["open"]
    response = client.post("/api/storage/delete", json={"kind": "segment", "path": items["ei"]["path"]})
    assert response.status_code == 409 and "Close" in response.json()["detail"]
    assert client.post("/api/storage/delete", json={"kind": "project", "path": str(cache)}).status_code == 409
