import argparse
import json
from pathlib import Path

from lca_viewer.quickstart import find_datasets, run
from lca_viewer.sources import catalog, register


def test_find_datasets(tmp_path):
    assert find_datasets(tmp_path) is None
    datasets = tmp_path / "release" / "datasets"
    datasets.mkdir(parents=True)
    (datasets / "a.spold").write_text("")
    assert find_datasets(tmp_path) == datasets  # one level down
    assert find_datasets(tmp_path / "release") == datasets
    assert find_datasets(datasets) == datasets


def test_register_and_catalog(tmp_path):
    register(tmp_path, tmp_path / "cache" / "a", "A", "x|GLO")
    register(tmp_path, tmp_path / "cache" / "b", "B", None)
    register(tmp_path, tmp_path / "cache" / "a", "A2", "x|GLO")  # updated in place, order kept
    assert [m["title"] for m in json.loads((tmp_path / "models.json").read_text())] == ["A2", "B"]
    models = catalog(tmp_path)
    assert models[0] == {"path": str((tmp_path / "cache" / "a").resolve()), "title": "A2", "default_root": "x|GLO"}
    assert models[1]["default_root"] is None
    assert catalog(tmp_path / "nothing") == []


def test_setup_only_records_the_python(tmp_path):
    run(argparse.Namespace(data=str(tmp_path / "data")))
    assert (tmp_path / "data" / "python.txt").exists()
    assert not (tmp_path / "data" / "models.json").exists()


def test_launcher_sets_up_only_when_needed(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import bootstrap
    import launch

    calls = []
    python = tmp_path / "python"
    python.write_text("")
    monkeypatch.setattr(bootstrap, "LAST", tmp_path / "python.txt")

    def fake_setup(args):
        calls.append(("setup", args))
        bootstrap.LAST.write_text(str(python), encoding="utf-8")
        return 0

    monkeypatch.setattr(bootstrap, "main", fake_setup)
    monkeypatch.setattr(bootstrap, "probe", lambda p: ((3, 12), True))
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda cmd, **kw: calls.append(("serve", cmd[3:])) or argparse.Namespace(returncode=0))

    assert launch.main(["--port", "8800"]) == 0  # not set up: setup, then serve with the arguments
    assert calls == [("setup", []), ("serve", ["serve", "--open", "--port", "8800"])]
    calls.clear()
    assert launch.main([]) == 0  # set up: serve only
    assert calls == [("serve", ["serve", "--open"])]
    calls.clear()
    assert launch.main(["setup", "--yes"]) == 0  # again on request
    assert calls == [("setup", ["--yes"]), ("serve", ["serve", "--open"])]


def test_one_brightway_folder(tmp_path, monkeypatch):
    from lca_viewer.sources import brightway_dir, brightway_dirs, default_brightway_dir

    monkeypatch.setenv("BRIGHTWAY2_DIR", str(tmp_path / "bw"))
    assert brightway_dir() == (tmp_path / "bw").resolve()
    assert brightway_dirs() == [{"label": "BRIGHTWAY2_DIR", "path": str((tmp_path / "bw").resolve())}]
    monkeypatch.delenv("BRIGHTWAY2_DIR")
    assert brightway_dir() == default_brightway_dir().expanduser().resolve()  # as bw2data picks it
    assert brightway_dirs()[0]["label"] == "Brightway default folder"


def test_first_launch_creates_the_brightway_folder(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("bw2data")
    from lca_viewer.cli import init_brightway
    from lca_viewer.sources import brightway_projects

    folder = tmp_path / "new" / "bw"  # does not exist yet: bw2data would refuse it
    monkeypatch.setenv("BRIGHTWAY2_DIR", str(folder))
    init_brightway()
    assert [(p["name"], p["twofive"]) for p in brightway_projects(folder)] == [("default", True)]
    stamp = (folder / "projects.db").stat().st_mtime_ns
    init_brightway()  # done once
    assert (folder / "projects.db").stat().st_mtime_ns == stamp
