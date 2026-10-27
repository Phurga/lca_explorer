import json

from lca_viewer.quickstart import clean_path, find_datasets, parse_release
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
