import argparse
import json

from lca_viewer.quickstart import Prompt, ask_source, clean_path, find_datasets, parse_release, run
from lca_viewer.sources import catalog, register


def test_parse_release():
    assert parse_release("ecoinvent 3.9.1_cutoff_ecoSpold02.7z") == ("3.9.1", "cutoff")
    assert parse_release("ecoinvent 3.10_apos_ecoSpold02.7z") == ("3.10", "apos")
    assert parse_release("ecoinvent 3.9.1_consequential_ecoSpold02") == ("3.9.1", "consequential")
    assert parse_release("Cut-off ecoinvent 3.8") == ("3.8", "cutoff")
    assert parse_release("ei391_cutoff datasets") == (None, "cutoff")


def test_clean_path():
    assert clean_path('"C:/x y/a.7z"').name == "a.7z"
    assert clean_path("& 'C:/x y/a.7z'").name == "a.7z"  # dragged onto PowerShell


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


def test_ecoinvent_is_optional(tmp_path, monkeypatch):
    assert ask_source(Prompt(yes=True), None) is None  # --yes without an archive: no ecoinvent
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert ask_source(Prompt(), None) is None  # Enter skips
    archive = tmp_path / "ecoinvent 3.9.1_cutoff_ecoSpold02.7z"
    archive.write_text("")
    assert ask_source(Prompt(yes=True), str(archive)) == (archive, None)

    # The setup then only records the Python for the start scripts, and offers to start the app
    started = []
    monkeypatch.setattr("lca_viewer.cli.main", lambda argv: started.append(argv))
    run(argparse.Namespace(data=str(tmp_path / "data"), yes=True, source=None))
    assert (tmp_path / "data" / "python.txt").exists()
    assert not (tmp_path / "data" / "models.json").exists()
    assert started == [["serve", "--data", str((tmp_path / "data").resolve()), "--open"]]


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
