"""The cumulative impacts G are computed once and cached next to the model files."""

import numpy as np

from lca_viewer import model as model_module
from lca_viewer.model import LCAModel


def test_g_is_read_from_its_cache_the_second_time(linear_chain_model, tmp_path, monkeypatch):
    linear_chain_model.save(tmp_path)
    first = LCAModel.load(tmp_path)
    G = first.G.copy()
    assert (tmp_path / "G.npy").exists() and (tmp_path / "G.json").exists()

    def no_solve(*args, **kwargs):
        raise AssertionError("G was computed again")

    monkeypatch.setattr(model_module, "_factorized_solver", no_solve)
    second = LCAModel.load(tmp_path)
    np.testing.assert_allclose(second.G, G)


def test_g_is_computed_again_when_the_model_files_change(linear_chain_model, tmp_path):
    linear_chain_model.save(tmp_path)
    LCAModel.load(tmp_path).G
    # the model is built again with other amounts: the cached G is not its G
    changed = LCAModel(A=linear_chain_model.A * 2, B=linear_chain_model.B, activities=linear_chain_model.activities,
                       flows=linear_chain_model.flows, methods=linear_chain_model.methods)
    changed.save(tmp_path)
    reloaded = LCAModel.load(tmp_path)
    np.testing.assert_allclose(reloaded.G, changed.G)


def test_a_model_without_a_folder_has_no_cache(linear_chain_model):
    assert linear_chain_model._directory is None
    assert linear_chain_model.G.shape[0] == linear_chain_model.size
