"""Unit tests for query, view, grouping, and contribution analysis."""

import numpy as np
import pandas as pd
import pytest

from lca_viewer.query import evaluate, with_derived_fields
from lca_viewer.tree import Tree, DIRECT
from lca_viewer.view import View, GroupRule
from lca_viewer.analysis import direct_contribution, first_match_contribution


class TestQueryEvaluate:
    """Test query.evaluate predicates."""

    def test_evaluate_eq_field(self, grouping_model):
        """eq operator should match exact values."""
        activities = grouping_model.activities
        mask = evaluate({"field": "location", "op": "eq", "value": "GLO"}, activities)
        assert mask[0] and not mask[1]
        assert np.sum(mask) == 1

    def test_evaluate_ne_field(self, grouping_model):
        """ne operator should match non-equal values."""
        activities = grouping_model.activities
        mask = evaluate({"field": "location", "op": "ne", "value": "GLO"}, activities)
        assert not mask[0] and mask[1]
        assert np.sum(mask) == 5

    def test_evaluate_in_field(self, grouping_model):
        """in operator should match values in list."""
        activities = grouping_model.activities
        mask = evaluate({"field": "location", "op": "in", "value": ["FR", "DE"]}, activities)
        assert not mask[0]  # GLO
        assert mask[1]  # FR
        assert mask[2]  # DE
        assert not mask[3]  # US
        assert np.sum(mask) == 3

    def test_evaluate_contains_case_insensitive(self, grouping_model):
        """contains operator should be case-insensitive."""
        activities = grouping_model.activities
        mask = evaluate({"field": "name", "op": "contains", "value": "bread"}, activities)
        assert mask[0]  # "market for bread"
        assert mask[1]  # "bread production"
        assert np.sum(mask) == 4  # market + 3 productions

        # Case insensitive
        mask = evaluate({"field": "name", "op": "contains", "value": "BREAD"}, activities)
        assert mask[0]

    def test_evaluate_startswith(self, grouping_model):
        """startswith operator should match prefix (case-insensitive)."""
        activities = grouping_model.activities
        mask = evaluate({"field": "name", "op": "startswith", "value": "market"}, activities)
        assert mask[0] and mask[5]
        assert np.sum(mask) == 2

    def test_evaluate_regex(self, grouping_model):
        """regex operator should match pattern."""
        activities = grouping_model.activities
        mask = evaluate({"field": "name", "op": "regex", "value": "production|freight"}, activities)
        assert not mask[0]
        assert mask[1]  # bread production
        assert mask[4]  # transport, freight
        assert np.sum(mask) == 4

    def test_evaluate_all(self, grouping_model):
        """all combinator should require all predicates to match."""
        activities = grouping_model.activities
        mask = evaluate({
            "all": [
                {"field": "name", "op": "contains", "value": "bread"},
                {"field": "location", "op": "ne", "value": "GLO"},
            ]
        }, activities)
        assert not mask[0]  # "market for bread" is GLO
        assert mask[1]  # "bread production" FR
        assert np.sum(mask) == 3

    def test_evaluate_any(self, grouping_model):
        """any combinator should require at least one predicate to match."""
        activities = grouping_model.activities
        mask = evaluate({
            "any": [
                {"field": "location", "op": "eq", "value": "GLO"},
                {"field": "location", "op": "eq", "value": "RER"},
            ]
        }, activities)
        assert mask[0] and mask[4]
        assert np.sum(mask) == 2

    def test_evaluate_not(self, grouping_model):
        """not combinator should negate predicate."""
        activities = grouping_model.activities
        mask = evaluate({
            "not": {"field": "location", "op": "eq", "value": "FR"}
        }, activities)
        assert not mask[1]  # FR bread production is negated
        assert not mask[5]  # FR electricity is negated
        assert mask[0] and mask[2]
        assert np.sum(mask) == 4  # GLO, DE, US, RER

    def test_evaluate_products(self, grouping_model):
        """products selector should match explicit indices."""
        activities = grouping_model.activities
        mask = evaluate({"products": [1, 3, 5]}, activities)
        assert mask[1] and mask[3] and mask[5]
        assert not mask[0]
        assert np.sum(mask) == 3

    def test_evaluate_unknown_op_raises_valueerror(self, grouping_model):
        """Unknown operator should raise ValueError."""
        activities = grouping_model.activities
        with pytest.raises(ValueError, match="Unknown operator"):
            evaluate({"field": "location", "op": "unknown", "value": "GLO"}, activities)


class TestWithDerivedFields:
    """Test query.with_derived_fields activity type derivation."""

    def test_activity_type_market_for(self, grouping_model):
        """activity_type should be 'market' for 'market for' names."""
        activities = with_derived_fields(grouping_model.activities)
        assert activities.iloc[0]["activity_type"] == "market"
        assert activities.iloc[5]["activity_type"] == "market"

    def test_activity_type_market_group(self, grouping_model):
        """activity_type should be 'market group' for 'market group for' names."""
        # Create a test dataframe with market group
        df = pd.DataFrame({
            "name": ["market group for something", "other"],
            "reference_product": ["prod", "prod"],
            "location": ["GLO", "GLO"],
            "unit": ["kg", "kg"],
            "isic": [None, None],
            "cpc": [None, None],
        })
        result = with_derived_fields(df)
        assert result.iloc[0]["activity_type"] == "market group"

    def test_activity_type_treatment(self, grouping_model):
        """activity_type should be 'treatment' for 'treatment of' and 'treatment,' names."""
        df = pd.DataFrame({
            "name": ["treatment of waste", "treatment, landfill", "other"],
            "reference_product": ["waste", "waste", "prod"],
            "location": ["GLO", "GLO", "GLO"],
            "unit": ["kg", "kg", "kg"],
            "isic": [None, None, None],
            "cpc": [None, None, None],
        })
        result = with_derived_fields(df)
        assert result.iloc[0]["activity_type"] == "treatment"
        assert result.iloc[1]["activity_type"] == "treatment"
        assert result.iloc[2]["activity_type"] == "production"

    def test_activity_type_default_production(self, grouping_model):
        """activity_type should be 'production' for other names."""
        activities = with_derived_fields(grouping_model.activities)
        assert activities.iloc[1]["activity_type"] == "production"  # bread production
        assert activities.iloc[4]["activity_type"] == "production"  # transport


class TestViewBasic:
    """Test View basic rendering."""

    def test_view_root_only_when_not_expanded(self, grouping_model):
        """View should show only root row when nothing is expanded."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        view = View(tree)
        rows = view.rows()

        assert len(rows) == 1
        assert rows[0].id == "0"
        assert rows[0].kind == "process"

    def test_view_expand_root_shows_children(self, grouping_model):
        """Expanding root should show its children sorted by |impact| desc."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        view = View(tree)
        view.expand("0")
        rows = view.rows()

        # Root + children (3 breads + electricity + transport + direct)
        assert rows[0].id == "0"
        assert rows[0].expanded
        assert len(rows) > 1

        # Check children are sorted by |impact| descending for method 0 (climate change)
        children = [r for r in rows[1:] if r.parent == "0" and not r.kind == "direct"]
        for i in range(len(children) - 1):
            curr_impact = abs(children[i].impacts[view.method])
            next_impact = abs(children[i + 1].impacts[view.method])
            assert curr_impact >= next_impact

    def test_view_collapse_hides_children(self, grouping_model):
        """Collapsing a node should hide its children."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        view = View(tree)
        view.expand("0")
        rows_expanded = view.rows()
        assert len(rows_expanded) > 1

        view.collapse("0")
        rows_collapsed = view.rows()
        assert len(rows_collapsed) == 1


class TestViewFrontier:
    """Test frontier sum-to-root property."""

    def test_frontier_equals_root_no_expansion(self, grouping_model):
        """With no expansion, frontier sum should equal root impacts."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        view = View(tree)
        rows = view.frontier()

        assert len(rows) == 1
        np.testing.assert_allclose(rows[0].impacts, tree.root.impacts)

    def test_frontier_sum_with_single_expansion(self, grouping_model):
        """Frontier sum should equal root after expanding one node."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        view = View(tree)
        view.expand("0")

        frontier = view.frontier()
        frontier_sum = sum(r.impacts for r in frontier)
        np.testing.assert_allclose(frontier_sum, tree.root.impacts, rtol=1e-10)

    def test_frontier_sum_with_multiple_expansions(self, grouping_model):
        """Frontier sum should equal root after expanding multiple nodes."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        view = View(tree)

        # Expand root and one child
        view.expand("0")
        rows = view.rows()
        first_child = [r for r in rows[1:] if r.parent == "0" and r.expandable][0]
        view.expand(first_child.id)

        frontier = view.frontier()
        frontier_sum = sum(r.impacts for r in frontier)
        np.testing.assert_allclose(frontier_sum, tree.root.impacts, rtol=1e-10)


class TestGroupRuleBasic:
    """Test GroupRule functionality."""

    def test_group_by_location_partitions_children(self, grouping_model):
        """GroupRule with by='location' should partition children by location."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        # Root's children: bread from FR (1), DE (2), US (3)
        # by="location" will try to group by location, but each location has only 1 child
        # So no groups are created (per rule: "a predicate matching a single child creates no group")
        view = View(tree, rules=[GroupRule(by="location")])
        view.expand("0")
        rows = view.rows()

        # Verify that root is expanded and children are visible (not grouped)
        assert rows[0].id == "0" and rows[0].expanded
        children = [r for r in rows if r.parent == "0" and r.kind != "direct"]
        assert len(children) == 3

    def test_group_by_name_with_predicate(self, grouping_model):
        """GroupRule with predicate should bundle matching children."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        # Root's children: 3 bread productions (all match "bread production")
        # This should create 1 group containing all 3
        rule = GroupRule(
            name="all_breads",
            predicate={"field": "name", "op": "startswith", "value": "bread production"},
            scope="*"
        )
        view = View(tree, rules=[rule])
        view.expand("0")
        rows = view.rows()

        # Should have a group for "all_breads"
        groups = [r for r in rows if r.kind == "group" and r.label == "all_breads"]
        assert len(groups) == 1

    def test_single_child_predicate_creates_no_group(self, grouping_model):
        """A predicate matching a single child should not create a group."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        # Root's children: 3 breads + transport + electricity + direct
        # Predicate matches only electricity (product 5), which appears 0 times in direct children
        # So no group is created
        rule = GroupRule(
            name="single",
            predicate={"products": [5]},  # electricity is not a direct child of root
            scope="*"
        )
        view = View(tree, rules=[rule])
        view.expand("0")
        rows = view.rows()

        # No group should be created for electricity since it's not a child
        groups = [r for r in rows if r.kind == "group" and r.label == "single"]
        assert len(groups) == 0

    def test_group_scope_limited_to_node(self, grouping_model):
        """GroupRule scope limited to a node ID should apply only there."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        rule = GroupRule(
            name="breads",
            predicate={"field": "name", "op": "startswith", "value": "bread production"},
            scope="0"  # Only apply to root
        )
        view = View(tree, rules=[rule])
        view.expand("0")
        rows = view.rows()

        # Check that breads group appears under node "0"
        groups = [r for r in rows if r.kind == "group" and r.label == "breads"]
        assert len(groups) == 1
        assert groups[0].parent == "0"


class TestGroupExpansion:
    """Test expanding group rows."""

    def test_expand_group_shows_members(self, grouping_model):
        """Expanding a group should show its member rows."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        rule = GroupRule(
            name="breads",
            predicate={"field": "name", "op": "startswith", "value": "bread production"},
            scope="*"
        )
        view = View(tree, rules=[rule])
        view.expand("0")
        rows = view.rows()

        # Find the breads group
        breads_group = [r for r in rows if r.kind == "group" and r.label == "breads"]
        if breads_group:
            group = breads_group[0]
            # Expand it
            view.expand(group.id)
            rows = view.rows()

            # Should now show members
            members = [r for r in rows if r.parent == group.id]
            assert len(members) > 0

    def test_group_impacts_equal_sum_of_members(self, grouping_model):
        """Group impacts should equal sum of member impacts."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        rule = GroupRule(
            name="all_breads",
            predicate={"field": "name", "op": "startswith", "value": "bread production"},
            scope="*"
        )
        view = View(tree, rules=[rule])
        view.expand("0")
        rows = view.rows()

        # Find the group
        groups = [r for r in rows if r.kind == "group" and r.label == "all_breads"]
        if groups:
            group = groups[0]
            # Expand to get members
            view.expand(group.id)
            rows = view.rows()
            members = [r for r in rows if r.parent == group.id and r.kind != "rest"]
            member_sum = sum(r.impacts for r in members)
            np.testing.assert_allclose(member_sum, group.impacts, rtol=1e-10)


def _cutoff_between(view, parent_id, n):
    """A cut-off that bundles exactly the ``n`` smallest children of ``parent_id``."""
    magnitudes = sorted(abs(r.impacts[view.method]) for r in view.rows() if r.parent == parent_id)
    total = sum(magnitudes)
    return (sum(magnitudes[:n]) + magnitudes[n] / 2) / total


class TestCutoff:
    """Cut-off: under each parent, the smallest children weighing together at most
    ``cutoff`` of the siblings' total go to "others"."""

    def test_bundles_the_bottom_share_of_each_parent(self, grouping_model):
        view = View(Tree(grouping_model, 0))
        view.expand("0")
        smallest = sorted((r for r in view.rows() if r.parent == "0"), key=lambda r: abs(r.impacts[0]))
        view.cutoff = _cutoff_between(view, "0", 2)
        rest = next(r for r in view.rows() if r.kind == "rest")
        assert {m.id for m in rest.members} == {smallest[0].id, smallest[1].id}

    def test_is_relative_to_each_parent_not_the_root(self, grouping_model):
        view = View(Tree(grouping_model, 0))
        view.expand("0")
        for r in [r for r in view.rows() if r.depth == 1 and r.kind == "process"]:
            view.expand(r.id)
        full = view.rows()
        view.cutoff = 0.2
        rest_by_parent = {r.parent: {m.id for m in r.members} for r in view.rows() if r.kind == "rest"}
        for parent in {r.parent for r in full if r.parent is not None}:
            children = sorted((r for r in full if r.parent == parent), key=lambda r: abs(r.impacts[0]))
            budget, cumulated, expected = 0.2 * sum(abs(c.impacts[0]) for c in children), 0.0, set()
            for c in children:
                cumulated += abs(c.impacts[0])
                if cumulated > budget:
                    break
                expected.add(c.id)
            if len(expected) > 1:
                assert rest_by_parent.get(parent) == expected
            else:
                assert parent not in rest_by_parent

    def test_frontier_still_sums_to_root(self, grouping_model):
        tree = Tree(grouping_model, product=0, amount=1.0)
        view = View(tree, cutoff=0.3)
        view.expand("0")
        for r in [r for r in view.rows() if r.depth == 1 and r.kind == "process"]:
            view.expand(r.id)
        np.testing.assert_allclose(sum(r.impacts for r in view.frontier()), tree.root.impacts, rtol=1e-10)

    def test_a_single_bottom_row_is_shown_as_itself(self, grouping_model):
        view = View(Tree(grouping_model, 0))
        view.expand("0")
        view.cutoff = _cutoff_between(view, "0", 1)
        assert not [r for r in view.rows() if r.kind == "rest"]


class TestViewRoundTrip:
    """Test View.to_dict / from_dict round-trip."""

    def test_to_dict_from_dict_preserves_state(self, grouping_model):
        """Round-trip through dict should preserve view state."""
        tree = Tree(grouping_model, product=0, amount=2.5)
        view1 = View(tree, cutoff=0.05, method=1)
        view1.expand("0")
        view1.rules = [
            GroupRule(name="test", predicate={"field": "location", "op": "eq", "value": "FR"}),
        ]

        # Convert to dict and back
        data = view1.to_dict()
        view2 = View.from_dict(grouping_model, data)

        # Check state is preserved
        assert view2.expanded == view1.expanded
        assert view2.cutoff == view1.cutoff
        assert view2.method == view1.method
        assert len(view2.rules) == len(view1.rules)

    def test_rows_identical_after_roundtrip(self, grouping_model):
        """Rows from original and round-tripped views should be identical."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        view1 = View(tree)
        view1.expand("0")
        rows1 = view1.rows()

        # Round-trip
        data = view1.to_dict()
        view2 = View.from_dict(grouping_model, data)
        rows2 = view2.rows()

        # Check row counts match
        assert len(rows1) == len(rows2)

        # Check row IDs and impacts match
        for r1, r2 in zip(rows1, rows2):
            assert r1.id == r2.id
            np.testing.assert_allclose(r1.impacts, r2.impacts, rtol=1e-10)
            assert r1.kind == r2.kind


class TestDirectContribution:
    """Test direct_contribution analysis."""

    def test_direct_contribution_sums_to_root(self, grouping_model):
        """Sum of all groups should equal root score."""
        groups = {}
        result = direct_contribution(grouping_model, product=0, groups=groups, amount=1.0)

        # With empty groups, everything should be in "other"
        total = sum(result.values())
        root_score = grouping_model.score(0, 1.0)
        np.testing.assert_allclose(total, root_score, rtol=1e-10)

    def test_direct_contribution_empty_groups_uses_other(self, grouping_model):
        """With empty groups dict, all impacts should be in 'other'."""
        result = direct_contribution(grouping_model, product=0, groups={}, amount=1.0)

        assert "other" in result
        assert len(result) == 1
        np.testing.assert_allclose(
            result["other"],
            grouping_model.score(0, 1.0),
            rtol=1e-10
        )

    def test_direct_contribution_grouped(self, grouping_model):
        """With groups, impacts should be attributed by first match."""
        groups = {
            "electricity": {"field": "name", "op": "contains", "value": "electricity"},
            "bread": {"field": "name", "op": "contains", "value": "bread"},
        }
        result = direct_contribution(grouping_model, product=0, groups=groups, amount=1.0)

        # Should have electricity, bread, and other groups
        assert "electricity" in result
        assert "bread" in result
        assert "other" in result

        # Sum should still equal root
        total = sum(result.values())
        root_score = grouping_model.score(0, 1.0)
        np.testing.assert_allclose(total, root_score, rtol=1e-10)

    def test_direct_contribution_equals_dense_computation(self, grouping_model):
        """direct_contribution should equal dense U @ supply computation."""
        groups = {
            "electricity": {"field": "name", "op": "contains", "value": "electricity"},
        }
        result = direct_contribution(grouping_model, product=0, groups=groups, amount=1.0)

        # Hand compute: supply * U for each activity
        m = grouping_model
        supply = m.supply(0, 1.0)
        direct_impacts = supply[:, None] * m.U.T

        # Assignment: electricity group
        from lca_viewer.query import with_derived_fields
        activities = with_derived_fields(m.activities)
        assignment = np.full(m.size, 1)  # 1 = "other"
        for idx in range(m.size):
            if "electricity" in activities.iloc[idx]["name"].lower():
                assignment[idx] = 0  # 0 = "electricity"

        # Sum by group
        expected_electricity = np.sum(direct_impacts[assignment == 0], axis=0)
        expected_other = np.sum(direct_impacts[assignment == 1], axis=0)

        np.testing.assert_allclose(result["electricity"], expected_electricity, rtol=1e-10)
        np.testing.assert_allclose(result["other"], expected_other, rtol=1e-10)


class TestFirstMatchContribution:
    """Test first_match_contribution analysis."""

    def test_first_match_sums_to_root(self, grouping_model):
        """Sum of all groups should equal root score."""
        groups = {}
        result = first_match_contribution(grouping_model, product=0, groups=groups, amount=1.0)

        total = sum(result.values())
        root_score = grouping_model.score(0, 1.0)
        np.testing.assert_allclose(total, root_score, rtol=1e-10)

    def test_first_match_empty_groups_uses_other(self, grouping_model):
        """With empty groups, all impacts should be in 'other'."""
        result = first_match_contribution(grouping_model, product=0, groups={}, amount=1.0)

        assert "other" in result
        assert len(result) == 1
        np.testing.assert_allclose(
            result["other"],
            grouping_model.score(0, 1.0),
            rtol=1e-10
        )

    def test_first_match_group_matching_root_direct_supplier(self, grouping_model):
        """Group matching root's direct supplier should capture its cumulative impact."""
        # The root (market for bread) directly uses bread production activities (1, 2, 3)
        groups = {
            "bread_production": {
                "field": "name",
                "op": "startswith",
                "value": "bread production"
            }
        }
        result = first_match_contribution(grouping_model, product=0, groups=groups, amount=1.0)

        # bread production activities should account for their cumulative impacts
        # This includes their own emissions and their supply chain
        assert "bread_production" in result
        assert "other" in result

        # Sum should equal root
        total = sum(result.values())
        root_score = grouping_model.score(0, 1.0)
        np.testing.assert_allclose(total, root_score, rtol=1e-10)

    def test_first_match_group_matching_root_itself(self, grouping_model):
        """Group matching root itself should get 100% of impact."""
        groups = {
            "everything": {
                "field": "name",
                "op": "contains",
                "value": "market for bread"
            }
        }
        result = first_match_contribution(grouping_model, product=0, groups=groups, amount=1.0)

        # The root itself should account for all impacts
        root_score = grouping_model.score(0, 1.0)
        np.testing.assert_allclose(result["everything"], root_score, rtol=1e-10)
        # Other should be zero or near-zero
        np.testing.assert_allclose(result["other"], np.zeros(len(result["everything"])), atol=1e-10)

    def test_first_match_grouped_sum_invariant(self, grouping_model):
        """first_match with various groups should always sum to root."""
        # Test with multiple different grouping schemes
        test_cases = [
            {},
            {"electricity": {"field": "location", "op": "eq", "value": "FR"}},
            {
                "electricity": {"field": "name", "op": "contains", "value": "electricity"},
                "transport": {"field": "name", "op": "contains", "value": "transport"},
            },
            {
                "g1": {"field": "location", "op": "in", "value": ["FR", "DE"]},
                "g2": {"field": "location", "op": "eq", "value": "US"},
            },
        ]

        root_score = grouping_model.score(0, 1.0)

        for groups in test_cases:
            result = first_match_contribution(grouping_model, product=0, groups=groups, amount=1.0)
            total = sum(result.values())
            np.testing.assert_allclose(
                total, root_score, rtol=1e-10,
                err_msg=f"Failed for groups={groups}"
            )


class TestComplexViewScenarios:
    """Test complex combinations of expansion and rules."""

    def test_multiple_rules_applied_in_order(self, grouping_model):
        """Multiple rules should be applied in order; first match wins."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        rules = [
            GroupRule(
                name="breads",
                predicate={"field": "name", "op": "startswith", "value": "bread production"},
                scope="*"
            ),
            GroupRule(by="location", scope="*"),
        ]
        view = View(tree, rules=rules)
        view.expand("0")
        rows = view.rows()

        # First rule should create a "breads" group for the 3 bread productions
        # Second rule won't apply to already-grouped children
        groups = [r for r in rows if r.kind == "group"]
        bread_groups = [g for g in groups if g.label == "breads"]

        # Should have at least the breads group
        assert len(bread_groups) == 1

    def test_frontier_invariant_multiple_combinations(self, grouping_model):
        """Frontier sum invariant should hold for multiple combinations."""
        tree = Tree(grouping_model, product=0, amount=1.0)
        view = View(tree, cutoff=0.1)

        # Test various expansion patterns
        test_patterns = [
            set(),  # No expansion
            {"0"},  # Root expanded
            {"0", "0/1"},  # Root and first child
        ]

        root_impacts = tree.root.impacts

        for pattern in test_patterns:
            view.expanded = pattern.copy()
            frontier = view.frontier()
            frontier_sum = sum(r.impacts for r in frontier)
            np.testing.assert_allclose(
                frontier_sum, root_impacts, rtol=1e-10,
                err_msg=f"Failed for expansion pattern {pattern}"
            )


def test_named_groups_take_precedence_over_group_by(grouping_model):
    """A group-by listed first must not swallow children claimed by a named group."""
    view = View(Tree(grouping_model, 0))
    view.expand("0")
    view.rules = [
        GroupRule(by="name"),  # would bundle the three "bread production" suppliers
        GroupRule(name="bread EU", predicate={"field": "location", "op": "in", "value": ["FR", "DE"]}),
    ]
    groups = [r for r in view.rows() if r.kind == "group"]
    assert [g.label for g in groups] == ["bread EU"]
    assert np.allclose(sum(r.impacts for r in view.frontier()), view.tree.root.impacts)


def test_zero_direct_rows_are_hidden():
    """A market without emissions gets no empty "direct emissions" row."""
    import scipy.sparse as sp
    from lca_viewer.model import LCAModel, Method

    A = sp.csc_matrix(np.array([[1.0, 0.0], [-1.0, 1.0]]))
    B = sp.csc_matrix(np.array([[0.0, 2.0]]))  # the market (column 0) emits nothing
    activities = pd.DataFrame({
        "name": ["market for x", "x production"], "reference_product": ["x", "x"],
        "location": ["GLO", "FR"], "unit": ["kg", "kg"], "isic": [None, None], "cpc": [None, None],
    })
    model = LCAModel(A, B, activities, pd.DataFrame({"name": ["CO2"]}), [Method(("gwp",), "kg", np.array([1.0]))])
    view = View(Tree(model, 0))
    view.expand("0")
    assert [r.kind for r in view.rows()] == ["process", "process"]
    view.expand("0/1")
    assert [r.kind for r in view.rows()] == ["process", "process", "direct"]


def test_group_hiding_an_expanded_member_opens_until_collapsed(grouping_model):
    view = View(Tree(grouping_model, 0))
    view.expand("0")
    french = next(r for r in view.rows() if r.kind == "process" and r.depth == 1)
    view.expand(french.id)
    view.rules = [GroupRule(name="all bread", predicate={"field": "name", "op": "contains", "value": "bread production"})]
    group = next(r for r in view.rows() if r.kind == "group")
    assert group.expanded
    assert french.id in [r.id for r in view.rows()]
    view.collapse(group.id)
    assert not next(r for r in view.rows() if r.kind == "group").expanded
    assert french.id not in [r.id for r in view.rows()]
    view.expand(group.id)
    assert next(r for r in view.rows() if r.kind == "group").expanded


def test_rest_row_lists_hidden_members_and_pins_bring_them_back(grouping_model):
    view = View(Tree(grouping_model, 0))
    view.expand("0")
    view.cutoff = _cutoff_between(view, "0", 2)  # the two smallest children go to others
    rest = next(r for r in view.rows() if r.kind == "rest")
    assert len(rest.members) == 2
    assert np.allclose(rest.impacts, sum(m.impacts for m in rest.members))

    pinned = rest.members[0]
    view.pinned = {pinned.id}
    rows = view.rows()
    shown = next(r for r in rows if r.id == pinned.id)
    assert shown.pinned
    # only one row left below the cut-off: it is shown as itself, no rest row
    assert not [r for r in rows if r.kind == "rest"]
    assert np.allclose(sum(r.impacts for r in view.frontier()), view.tree.root.impacts)
    assert View.from_dict(grouping_model, view.to_dict()).pinned == {pinned.id}


def test_pin_above_cutoff_is_not_flagged(grouping_model):
    view = View(Tree(grouping_model, 0), cutoff=1e-9)
    view.expand("0")
    child = next(r for r in view.rows() if r.depth == 1)
    view.pinned = {child.id}
    assert not next(r for r in view.rows() if r.id == child.id).pinned


def test_rows_api_with_cutoff_and_pins_is_json_serialisable(grouping_model):
    """Cut-off flags must be plain bools in the API response (regression: numpy.bool)."""
    from fastapi.testclient import TestClient
    from lca_viewer.server import create_app

    client = TestClient(create_app(grouping_model))
    view = View(Tree(grouping_model, 0))
    view.expand("0")
    state = {"root": [0], "expanded": ["0"], "cutoff": _cutoff_between(view, "0", 2), "method": 0, "pinned": []}
    response = client.post("/api/rows", json=state)
    assert response.status_code == 200
    rest = [r for r in response.json()["rows"] if r["kind"] == "rest"]
    assert rest and len(rest[0]["members"]) > 1

    state["pinned"] = [rest[0]["members"][0]["id"]]
    rows = client.post("/api/rows", json=state).json()["rows"]
    assert any(r["pinned"] for r in rows)


def test_moving_rows_to_others(grouping_model):
    view = View(Tree(grouping_model, 0))
    view.expand("0")
    children = [r for r in view.rows() if r.depth == 1]
    biggest = children[0]

    # a single moved row creates an "others" row, even without cut-off
    view.others = {biggest.id}
    rows = view.rows()
    assert biggest.id not in [r.id for r in rows]
    rest = next(r for r in rows if r.kind == "rest")
    assert [m.id for m in rest.members] == [biggest.id] and rest.members[0].moved
    assert np.allclose(rest.impacts, biggest.impacts)
    assert np.allclose(sum(r.impacts for r in view.frontier()), view.tree.root.impacts)

    # moved wins over pinned; round trip keeps it
    view.pinned = {biggest.id}
    assert biggest.id not in [r.id for r in view.rows()]
    assert View.from_dict(grouping_model, view.to_dict()).others == {biggest.id}


def test_moved_and_cut_rows_share_one_others_row(grouping_model):
    view = View(Tree(grouping_model, 0))
    view.expand("0")
    children = [r for r in view.rows() if r.depth == 1]
    view.cutoff = _cutoff_between(view, "0", 1)  # only the smallest child is below the cut-off
    view.others = {children[0].id}  # and the biggest is moved
    rest = [r for r in view.rows() if r.kind == "rest"]
    assert len(rest) == 1 and len(rest[0].members) == 2
    assert np.allclose(sum(r.impacts for r in view.frontier()), view.tree.root.impacts)


def test_rows_carry_metadata_for_chart_names(grouping_model):
    from fastapi.testclient import TestClient
    from lca_viewer.server import create_app

    client = TestClient(create_app(grouping_model))
    rows = client.post("/api/rows", json={"root": [0], "expanded": ["0", "0/1"], "method": 0}).json()["rows"]
    process = next(r for r in rows if r["id"] == "0/1")
    assert process["meta"]["name"] == grouping_model.activities["name"].iat[1]
    assert process["meta"]["index"] == 1
    direct = next(r for r in rows if r["kind"] == "direct" and r["parent"] == "0/1")
    assert direct["meta"]["index"] == 1  # direct emissions are named after the emitting process


def test_activity_endpoint_and_lazy_details(grouping_model, tmp_path):
    from fastapi.testclient import TestClient
    from lca_viewer.model import LCAModel
    from lca_viewer.server import create_app

    grouping_model.details = [{"comment": f"about {i}"} for i in range(grouping_model.size)]
    grouping_model.save(tmp_path)
    loaded = LCAModel.load(tmp_path)
    assert loaded.details is None  # not read until needed
    client = TestClient(create_app(loaded))
    info = client.get("/api/activity/1?method=0").json()
    assert info["details"] == {"comment": "about 1"}
    assert info["cumulative_per_unit"] == pytest.approx(loaded.G[1, 0])
    assert info["direct_per_unit"] == pytest.approx(loaded.U[0, 1] / loaded.diagonal[1])
    assert sum(f["impact"] for f in info["direct_flows"]) == pytest.approx(info["direct_per_unit"])
    assert client.get(f"/api/activity/{loaded.size}").status_code == 404


def test_cutoff_of_total_uses_the_root_score_as_budget(grouping_model):
    view = View(Tree(grouping_model, 0), cutoff_basis="total")
    view.expand("0")
    for r in [r for r in view.rows() if r.depth == 1 and r.kind == "process"]:
        view.expand(r.id)
    full = view.rows()
    view.cutoff = 0.05
    root = abs(view.tree.root.impacts[0])
    rest_by_parent = {r.parent: {m.id for m in r.members} for r in view.rows() if r.kind == "rest"}
    assert rest_by_parent  # the model has small enough grandchildren
    for parent in {r.parent for r in full if r.parent is not None}:
        children = sorted((r for r in full if r.parent == parent), key=lambda r: abs(r.impacts[0]))
        cumulated, expected = 0.0, set()
        for c in children:
            cumulated += abs(c.impacts[0])
            if cumulated > 0.05 * root:
                break
            expected.add(c.id)
        if len(expected) > 1:
            assert rest_by_parent.get(parent) == expected
        else:
            assert parent not in rest_by_parent
    # every others row stays within the budget, and totals are exact
    assert all(abs(r.impacts[0]) <= 0.05 * root + 1e-12 for r in view.rows() if r.kind == "rest")
    assert np.allclose(sum(r.impacts for r in view.frontier()), view.tree.root.impacts)
    assert View.from_dict(grouping_model, view.to_dict()).cutoff_basis == "total"


def test_unknown_cutoff_basis_is_rejected(grouping_model):
    view = View(Tree(grouping_model, 0), cutoff=0.1, cutoff_basis="sideways")
    view.expand("0")
    with pytest.raises(ValueError):
        view.rows()
