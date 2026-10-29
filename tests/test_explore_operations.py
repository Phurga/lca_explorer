"""Cut-off modes, replaced inputs (substitutions) and alternative suppliers."""

import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp
from fastapi.testclient import TestClient

from lca_viewer.model import LCAModel
from lca_viewer.server import create_app
from lca_viewer.tree import Tree
from lca_viewer.view import View


def with_spanish_bread(model):
    """The grouping model plus one more kg bread production (index 6) with no inputs."""
    A = sp.coo_matrix(model.A)
    A = sp.csc_matrix((np.append(A.data, 1.0), (np.append(A.row, 6), np.append(A.col, 6))), shape=(7, 7))
    B = sp.coo_matrix(model.B)
    B = sp.csc_matrix((np.append(B.data, [1.0, 1.0]), (np.append(B.row, [0, 1]), np.append(B.col, [6, 6]))), shape=(2, 7))
    activities = pd.concat([model.activities, pd.DataFrame([{
        "name": "bread production", "reference_product": "bread", "location": "ES", "unit": "kg",
        "isic": "10.61", "cpc": "1071"}])], ignore_index=True)
    return LCAModel(A=A, B=B, activities=activities, flows=model.flows, methods=model.methods)


def children_of_root(view):
    return [r for r in view.rows() if r.parent == "0"]


class TestCutoffModes:
    def magnitudes(self, model):
        """|impact| of the root's children, largest first, with no cut-off."""
        rows = children_of_root(View(Tree(model, 0), expanded={"0"}))
        return sorted((abs(r.impacts[0]) for r in rows), reverse=True)

    def test_top_keeps_the_largest_rows(self, grouping_model):
        expected = self.magnitudes(grouping_model)
        view = View(Tree(grouping_model, 0), expanded={"0"}, cutoff=2, cutoff_basis="top")
        rows = children_of_root(view)
        others = [r for r in rows if r.kind == "rest"]
        kept = [abs(r.impacts[0]) for r in rows if r.kind != "rest"]
        assert len(others) == 1 and len(others[0].members) == len(expected) - 2
        assert sorted(kept, reverse=True) == pytest.approx(expected[:2])

    def test_top_zero_means_no_limit(self, grouping_model):
        view = View(Tree(grouping_model, 0), expanded={"0"}, cutoff=0, cutoff_basis="top")
        assert all(r.kind != "rest" for r in children_of_root(view))

    def test_cumulative_keeps_rows_until_they_reach_the_share(self, grouping_model):
        magnitudes = self.magnitudes(grouping_model)
        share = 0.6  # two rows are needed; the other two are below (one alone would be shown)
        target = share * sum(magnitudes)
        # The smallest number of largest rows whose sum reaches the target
        needed = next(n for n in range(1, len(magnitudes) + 1) if sum(magnitudes[:n]) >= target)
        view = View(Tree(grouping_model, 0), expanded={"0"}, cutoff=share, cutoff_basis="cumulative")
        rows = children_of_root(view)
        kept = [r for r in rows if r.kind != "rest"]
        others = [r for r in rows if r.kind == "rest"]
        assert len(kept) == needed
        assert len(others) == 1 and len(others[0].members) == len(magnitudes) - needed
        assert sum(abs(r.impacts[0]) for r in kept) >= target

    def test_cumulative_zero_keeps_everything(self, grouping_model):
        view = View(Tree(grouping_model, 0), expanded={"0"}, cutoff=0, cutoff_basis="cumulative")
        assert all(r.kind != "rest" for r in children_of_root(view))

    def test_mode_round_trips(self, grouping_model):
        view = View(Tree(grouping_model, 0), cutoff=3, cutoff_basis="top")
        again = View.from_dict(grouping_model, view.to_dict())
        assert (again.cutoff, again.cutoff_basis) == (3, "top")


class TestSubstitutedInputs:
    def test_replaced_input_takes_the_new_supplier(self, grouping_model):
        model = with_spanish_bread(grouping_model)
        view = View(Tree(model, 0), expanded={"0"}, substitutions={"0/3": 6})
        rows = {r.id: r for r in view.rows()}
        assert "0/3" not in rows
        new = rows["0/6"]
        assert new.replaced == "0/3" and new.label.endswith("| ES")
        assert new.amount == pytest.approx(0.25)
        assert np.allclose(new.impacts, 0.25 * model.G[6])

    def test_replacement_in_another_unit_is_ignored(self, grouping_model):
        view = View(Tree(grouping_model, 0), expanded={"0"}, substitutions={"0/3": 5})
        assert "0/3" in {r.id for r in view.rows()}

    def test_replacement_already_an_input_is_ignored(self, grouping_model):
        view = View(Tree(grouping_model, 0), expanded={"0"}, substitutions={"0/3": 2})
        rows = {r.id: r for r in view.rows()}
        assert "0/3" in rows and rows["0/3"].replaced is None

    def test_substitutions_round_trip(self, grouping_model):
        model = with_spanish_bread(grouping_model)
        view = View(Tree(model, 0), expanded={"0"}, substitutions={"0/3": 6})
        again = View.from_dict(model, view.to_dict())
        assert again.substitutions == {"0/3": 6}


class TestAlternativeSuppliers:
    def test_lists_the_activities_of_the_same_product_lowest_first(self, grouping_model):
        client = TestClient(create_app(grouping_model))
        data = client.get("/api/activity/3/alternatives").json()
        assert data["product"] == "bread" and data["unit"] == "kg"
        # The market for bread, and the two other bread productions
        assert data["count"] == 3
        per_unit = [a["per_unit"] for a in data["alternatives"]]
        assert per_unit == sorted(per_unit)
        assert {a["index"] for a in data["alternatives"]} == {0, 1, 2}
        assert data["current"]["per_unit"] == pytest.approx(grouping_model.G[3, 0])
