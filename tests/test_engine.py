"""Comprehensive unit tests for LCA model and tree engine."""

import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp
from pathlib import Path
from tempfile import TemporaryDirectory

from lca_viewer.model import LCAModel, Method
from lca_viewer.tree import Tree, DIRECT


class TestLinearChainGComputation:
    """Test 1: Linear chain with G computation verification."""

    def test_G_matches_hand_computed(self, linear_chain_model):
        """G should match hand-computed impacts for linear chain."""
        m = linear_chain_model
        G = m.G

        # For wheat (product 0):
        # GWP: 2 kg CO2/kg (direct)
        # WS: 10 L/kg (direct)
        assert G[0, 0] == pytest.approx(2.0), "Wheat GWP"
        assert G[0, 1] == pytest.approx(10.0), "Wheat WS"

        # For flour (product 1):
        # GWP: 0.5 kg wheat (2 kg CO2 each) + 0.5 kg CO2 processing = 1.5 kg CO2
        # WS: 0.5 kg wheat (10 L each) + 2 L processing = 7 L
        assert G[1, 0] == pytest.approx(1.5), "Flour GWP"
        assert G[1, 1] == pytest.approx(7.0), "Flour WS"

        # For bread (product 2):
        # GWP: 0.8 kg flour (1.5 kg CO2 each) + 0.3 kg CO2 processing = 1.5 kg CO2
        # WS: 0.8 kg flour (7 L each) + 1 L processing = 6.6 L
        assert G[2, 0] == pytest.approx(1.5), "Bread GWP"
        assert G[2, 1] == pytest.approx(6.6), "Bread WS"

    def test_G_equals_dense_solve(self, linear_chain_model):
        """G = solve(A.T, U.T) should match dense computation."""
        m = linear_chain_model
        G_computed = m.G

        # Hand-compute using dense solve
        A_dense = m.A.toarray()
        U_dense = m.U
        G_expected = np.linalg.solve(A_dense.T, U_dense.T)

        np.testing.assert_allclose(G_computed, G_expected, rtol=1e-10)

    def test_score_is_G_row(self, linear_chain_model):
        """score(product, amount) should equal amount * G[product]."""
        m = linear_chain_model

        # Wheat
        score = m.score(0, 2.5)
        expected = 2.5 * m.G[0]
        np.testing.assert_allclose(score, expected)

        # Bread
        score = m.score(2, 1.0)
        expected = m.G[2]
        np.testing.assert_allclose(score, expected)


class TestLoopRecursion:
    """Test 2: System with loop - tree expansion and closure."""

    def test_tree_expands_through_loop_5_levels(self, loop_model):
        """Tree should expand through the loop at least 5 levels."""
        tree = Tree(loop_model, product=1, amount=1.0)  # electricity

        # Manually expand through the loop
        node = tree.root
        depth = 0
        max_depth = 0

        # Follow coal -> electricity -> coal -> ... loop
        for _ in range(6):  # Try to go 6 levels deep
            children = tree.children(node)
            if children:
                # Find non-direct child
                non_direct = [c for c in children if not c.is_direct]
                if non_direct:
                    node = non_direct[0]
                    max_depth = node.depth
                else:
                    break
            else:
                break

        assert max_depth >= 5, f"Should expand at least 5 levels, got {max_depth}"

    def test_children_sum_to_parent_closure(self, loop_model):
        """Sum of child impacts (including direct) should equal parent impacts."""
        tree = Tree(loop_model, product=0, amount=1.0)  # coal
        node = tree.root

        # Expand root to children
        children = tree.children(node)
        child_impacts_sum = np.sum([c.impacts for c in children], axis=0)

        np.testing.assert_allclose(child_impacts_sum, node.impacts, rtol=1e-10)

    def test_closure_at_multiple_depths(self, loop_model):
        """Closure should hold at multiple expansion depths in loop."""
        tree = Tree(loop_model, product=1, amount=1.0)

        # Expand root
        root_children = tree.children(tree.root)
        root_sum = np.sum([c.impacts for c in root_children], axis=0)
        np.testing.assert_allclose(root_sum, tree.root.impacts, rtol=1e-10)

        # Expand a child
        child_candidates = [c for c in root_children if not c.is_direct]
        if child_candidates:
            child = child_candidates[0]
            grandchildren = tree.children(child)
            child_sum = np.sum([g.impacts for g in grandchildren], axis=0)
            np.testing.assert_allclose(child_sum, child.impacts, rtol=1e-10)

    def test_root_score_equals_dense_formula(self, loop_model):
        """Root score c·B·A^-1·f should equal computed G."""
        tree = Tree(loop_model, product=0, amount=1.0)
        m = loop_model

        # Compute densely
        A_dense = m.A.toarray()
        B_dense = m.B.toarray()
        A_inv = np.linalg.inv(A_dense)

        # demand for 1 unit of product 0
        demand = np.zeros(2)
        demand[0] = 1.0

        # supply = A^-1 @ demand
        supply = A_inv @ demand

        # Direct impacts of supply
        for k, method in enumerate(m.methods):
            cfs = method.cfs
            score = cfs @ B_dense @ supply
            expected = tree.root.impacts[k]
            assert score == pytest.approx(expected, rel=1e-10)


class TestWasteTreatment:
    """Test 3: Waste treatment with negative diagonal."""

    def test_waste_diagonal_is_negative(self, waste_treatment_model):
        """Waste treatment activity should have negative diagonal."""
        m = waste_treatment_model
        assert m.diagonal[1] < 0, "Waste treatment diagonal should be negative"

    def test_waste_child_amount_negative(self, waste_treatment_model):
        """Waste is a negative amount of the treatment's (negative) reference product."""
        tree = Tree(waste_treatment_model, product=0, amount=1.0)
        node = tree.root

        # Expand to children
        children = tree.children(node)

        # Should have waste treatment child (product 1)
        waste_children = [c for c in children if c.product == 1]
        assert waste_children, "Should have waste treatment child"

        # -0.2 kg of "waste" product, i.e. 0.2 kg of waste treated
        waste_child = waste_children[0]
        assert waste_child.amount == pytest.approx(-0.2)

    def test_waste_treatment_burdens_are_positive(self, waste_treatment_model):
        """Treating waste adds the treatment's burdens to the generating activity."""
        m = waste_treatment_model
        tree = Tree(m, product=0, amount=1.0)
        waste_child = next(c for c in tree.children(tree.root) if c.product == 1)
        treatment_direct = next(c for c in tree.children(waste_child) if c.is_direct)
        np.testing.assert_allclose(treatment_direct.impacts, 0.2 * m.U[:, 1])
        assert np.all(waste_child.impacts >= 0)

    def test_waste_child_impact_equals_amount_times_G(self, waste_treatment_model):
        """Child impact should equal amount * G for waste treatment."""
        tree = Tree(waste_treatment_model, product=0, amount=1.0)
        children = tree.children(tree.root)

        waste_children = [c for c in children if c.product == 1]
        assert waste_children

        waste_child = waste_children[0]
        expected_impact = waste_child.amount * waste_treatment_model.G[1]
        np.testing.assert_allclose(waste_child.impacts, expected_impact, rtol=1e-10)

    def test_waste_closure_holds(self, waste_treatment_model):
        """Closure should still hold with waste treatment."""
        tree = Tree(waste_treatment_model, product=0, amount=1.0)

        # Expand root
        children = tree.children(tree.root)
        sum_impacts = np.sum([c.impacts for c in children], axis=0)

        np.testing.assert_allclose(sum_impacts, tree.root.impacts, rtol=1e-10)


class TestNonUnitProduction:
    """Test 4: Non-unit production amount (diagonal != 1)."""

    def test_non_unit_diagonal(self, non_unit_production_model):
        """Model should have non-unit diagonal."""
        m = non_unit_production_model
        assert m.diagonal[1] == 2.0, "Product diagonal should be 2.0"

    def test_child_amounts_scale_by_inverse_diagonal(self, non_unit_production_model):
        """Child amounts should scale by 1/diagonal of parent."""
        tree = Tree(non_unit_production_model, product=1, amount=2.0)
        m = non_unit_production_model

        # Request 2 kg product (which has diagonal = 2)
        # So operation level = 2.0 / 2.0 = 1.0
        # Raw material input = -1.5 * 1.0 = 1.5 kg (absolute value)

        children = tree.children(tree.root)
        raw_children = [c for c in children if c.product == 0]

        assert raw_children, "Should have raw material child"
        raw_child = raw_children[0]

        # Amount should be 1.5 kg
        assert raw_child.amount == pytest.approx(1.5)

    def test_impact_scales_correctly(self, non_unit_production_model):
        """Impacts should scale correctly with non-unit production."""
        m = non_unit_production_model

        # G for product (1): operation_cost * U[:,1] / diagonal[1]
        # With 1 unit of product:
        score_1 = m.score(1, 1.0)

        # With 2 units of product:
        score_2 = m.score(1, 2.0)

        # Should be 2x
        np.testing.assert_allclose(score_2, 2.0 * score_1)


class TestTreeNodePath:
    """Test 5: Tree.node(path) rebuilds nodes correctly."""

    def test_node_path_rebuilds_same_node(self, linear_chain_model):
        """node(path) should rebuild same node as walking children."""
        tree = Tree(linear_chain_model, product=2, amount=1.0)  # bread

        # Manually walk to flour child, then wheat child
        root_children = tree.children(tree.root)
        flour_child = [c for c in root_children if c.product == 1][0]
        flour_children = tree.children(flour_child)
        wheat_child = [c for c in flour_children if c.product == 0][0]

        # Now rebuild using node(path)
        path = (2, 1, 0)  # bread -> flour -> wheat
        rebuilt = tree.node(path)

        assert rebuilt.amount == wheat_child.amount
        np.testing.assert_allclose(rebuilt.impacts, wheat_child.impacts)

    def test_node_path_invalid_raises_keyerror(self, linear_chain_model):
        """node(path) should raise KeyError for invalid path."""
        tree = Tree(linear_chain_model, product=2, amount=1.0)

        # Invalid: starts with wrong product
        with pytest.raises(KeyError):
            tree.node((0, 1, 2))

        # Invalid: intermediate step doesn't exist
        with pytest.raises(KeyError):
            tree.node((2, 0))  # bread -> wheat (no direct edge)

    def test_node_root_path(self, linear_chain_model):
        """node(path) with single-element path should return root."""
        tree = Tree(linear_chain_model, product=1, amount=3.5)

        rebuilt = tree.node((1,))
        assert rebuilt.product == tree.root.product
        assert rebuilt.amount == tree.root.amount
        np.testing.assert_allclose(rebuilt.impacts, tree.root.impacts)


class TestSaveLoadRoundTrip:
    """Test 6: Save/load round trip preserves model."""

    def test_save_load_preserves_G(self, linear_chain_model):
        """G matrix should be identical after save/load round trip."""
        with TemporaryDirectory() as tmpdir:
            # Compute G before save
            G_before = linear_chain_model.G.copy()

            # Save
            linear_chain_model.save(tmpdir)

            # Load
            loaded_model = LCAModel.load(tmpdir)
            G_after = loaded_model.G

            np.testing.assert_allclose(G_before, G_after, rtol=1e-10)

    def test_save_load_preserves_activities(self, loop_model):
        """Activities DataFrame should be preserved."""
        with TemporaryDirectory() as tmpdir:
            loop_model.save(tmpdir)
            loaded = LCAModel.load(tmpdir)

            pd.testing.assert_frame_equal(loop_model.activities, loaded.activities)

    def test_save_load_preserves_methods(self, linear_chain_model):
        """Methods should be preserved with correct cfs."""
        with TemporaryDirectory() as tmpdir:
            linear_chain_model.save(tmpdir)
            loaded = LCAModel.load(tmpdir)

            assert len(loaded.methods) == len(linear_chain_model.methods)
            for orig, loaded_m in zip(linear_chain_model.methods, loaded.methods):
                assert orig.name == loaded_m.name
                assert orig.unit == loaded_m.unit
                np.testing.assert_array_equal(orig.cfs, loaded_m.cfs)


class TestModelValidation:
    """Test 7: ValueError for invalid models."""

    def test_non_square_A_raises_valueerror(self, linear_chain_model):
        """Non-square A should raise ValueError."""
        m = linear_chain_model
        # Create non-square A
        A_rect = m.A[:2, :]  # 2 x 3

        with pytest.raises(ValueError, match="square"):
            LCAModel(
                A=A_rect,
                B=m.B,
                activities=m.activities,
                flows=m.flows,
                methods=m.methods,
            )

    def test_zero_diagonal_raises_valueerror(self, linear_chain_model):
        """Zero on diagonal of A should raise ValueError."""
        m = linear_chain_model

        # Create A with zero on diagonal
        A_copy = m.A.copy()
        A_copy[0, 0] = 0  # Zero diagonal
        A_copy = A_copy.tocsr()
        A_copy.eliminate_zeros()
        A_copy = A_copy.tocsc()

        with pytest.raises(ValueError, match="zero"):
            LCAModel(
                A=A_copy,
                B=m.B,
                activities=m.activities,
                flows=m.flows,
                methods=m.methods,
            )

    def test_mismatched_B_raises_valueerror(self, linear_chain_model):
        """B with wrong number of columns should raise ValueError."""
        m = linear_chain_model
        # Create B with 4 columns instead of 3
        B_wrong = sp.csc_matrix((2, 4))

        with pytest.raises(ValueError):
            LCAModel(
                A=m.A,
                B=B_wrong,
                activities=m.activities,
                flows=m.flows,
                methods=m.methods,
            )


class TestSupply:
    """Test 8: supply(p) method and satisfaction of A @ s == demand."""

    def test_supply_satisfies_demand_linear_chain(self, linear_chain_model):
        """A @ supply should equal demand vector."""
        m = linear_chain_model

        # Demand for 1 kg bread (product 2)
        supply = m.supply(product=2, amount=1.0)
        demand = np.zeros(3)
        demand[2] = 1.0

        # A @ supply should equal demand
        result = m.A @ supply
        np.testing.assert_allclose(result, demand, atol=1e-10)

    def test_supply_satisfies_demand_loop(self, loop_model):
        """A @ supply should satisfy demand in loop system."""
        m = loop_model

        # Demand for 5 kWh electricity (product 1)
        supply = m.supply(product=1, amount=5.0)
        demand = np.zeros(2)
        demand[1] = 5.0

        result = m.A @ supply
        np.testing.assert_allclose(result, demand, atol=1e-10)

    def test_supply_different_amounts(self, linear_chain_model):
        """supply(p, amount) should scale linearly."""
        m = linear_chain_model

        supply_1 = m.supply(product=0, amount=1.0)
        supply_5 = m.supply(product=0, amount=5.0)

        np.testing.assert_allclose(supply_5, 5.0 * supply_1, rtol=1e-10)

    def test_supply_product_on_diagonal(self, non_unit_production_model):
        """supply should work with non-unit diagonal."""
        m = non_unit_production_model

        # Request 4 kg of product 1 (which has diagonal = 2)
        supply = m.supply(product=1, amount=4.0)
        demand = np.zeros(2)
        demand[1] = 4.0

        result = m.A @ supply
        np.testing.assert_allclose(result, demand, atol=1e-10)


class TestTreeDirectNode:
    """Additional tests for direct emissions nodes."""

    def test_direct_node_properties(self, linear_chain_model):
        """Direct node should have DIRECT product and zero amount."""
        tree = Tree(linear_chain_model, product=0, amount=1.0)
        children = tree.children(tree.root)

        direct = [c for c in children if c.is_direct]
        assert direct, "Should have direct node"

        node = direct[0]
        assert node.product == DIRECT
        assert node.amount == 0.0
        assert node.is_direct

    def test_direct_node_impacts(self, linear_chain_model):
        """Direct node impacts should equal operation level * U."""
        tree = Tree(linear_chain_model, product=1, amount=1.0)
        m = linear_chain_model

        children = tree.children(tree.root)
        direct = [c for c in children if c.is_direct]

        operation = 1.0 / m.diagonal[1]
        expected_impacts = operation * m.U[:, 1]

        np.testing.assert_allclose(direct[0].impacts, expected_impacts)


class TestEdgeCases:
    """Test edge cases and robustness."""

    def test_zero_amount_request(self, linear_chain_model):
        """Requesting zero amount should give zero impacts."""
        m = linear_chain_model
        score = m.score(0, 0.0)
        np.testing.assert_array_equal(score, [0.0, 0.0])

    def test_negative_amount_request(self, linear_chain_model):
        """Negative amount should give negative impacts (for sensitivity)."""
        m = linear_chain_model
        score_pos = m.score(0, 1.0)
        score_neg = m.score(0, -1.0)

        np.testing.assert_allclose(score_neg, -score_pos)

    def test_very_large_amount(self, linear_chain_model):
        """Very large amounts should still compute correctly."""
        m = linear_chain_model

        supply_small = m.supply(0, 1.0)
        supply_large = m.supply(0, 1e6)

        np.testing.assert_allclose(supply_large, 1e6 * supply_small, rtol=1e-8)
