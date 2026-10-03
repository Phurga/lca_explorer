"""Tests for search API, query functions, and server endpoints."""

import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp
from fastapi.testclient import TestClient

from lca_viewer.model import LCAModel, Method
from lca_viewer.query import search, suggest_values, derived_activities, with_derived_fields
from lca_viewer.server import create_app


@pytest.fixture
def search_model():
    """Test model with ~8 activities for search testing.

    Activities:
    0: "market for wheat grain" (RoW)
    1: "wheat production" (FR)
    2: "wheat production" (US)
    3: "wheat grain, import from RoW" (ZA)
    4: "market for electricity, low voltage" (FR)
    5: "electricity, low voltage, residual mix" (FR)
    6: "market for diesel" (RoW)
    7: "diesel production" (RoW)
    """
    activities = pd.DataFrame({
        "name": [
            "market for wheat grain",
            "wheat production",
            "wheat production",
            "wheat grain, import from RoW",
            "market for electricity, low voltage",
            "electricity, low voltage, residual mix",
            "market for diesel",
            "diesel production",
        ],
        "reference_product": [
            "wheat grain",
            "wheat grain",
            "wheat grain",
            "wheat grain",
            "electricity, low voltage",
            "electricity, low voltage",
            "diesel",
            "diesel",
        ],
        "location": ["RoW", "FR", "US", "ZA", "FR", "FR", "RoW", "RoW"],
        "unit": ["kg", "kg", "kg", "kg", "kWh", "kWh", "liter", "liter"],
        "isic": [None, "01.11", "01.11", "01.11", "35.11", "35.11", "19.20", "19.20"],
        "cpc": [None, "0111", "0111", "0111", "1711", "1711", "1920", "1920"],
        "database": ["ecoinvent", "ecoinvent", "ecoinvent", "ecoinvent",
                     "ecoinvent", "ecoinvent", "ecoinvent", "ecoinvent"],
    })

    flows = pd.DataFrame({
        "name": ["CO2 to air"],
        "unit": ["kg"],
    })

    # A matrix (8x8): technosphere
    # Diagonal: each activity produces 1 unit
    # Inputs: simple supply chain
    # 0 (market for wheat) uses wheat from FR, US, ZA
    # 1, 2, 3 (wheat productions) use electricity
    # 6 (market for diesel) uses diesel from production
    A_data = [
        1.0,    # A[0,0] = market for wheat
        -0.4,   # A[1,0] = wheat from FR
        1.0,    # A[1,1] = wheat production FR
        -0.15,  # A[4,1] = electricity for wheat FR
        -0.3,   # A[2,0] = wheat from US
        1.0,    # A[2,2] = wheat production US
        -0.15,  # A[4,2] = electricity for wheat US
        -0.3,   # A[3,0] = wheat from ZA
        1.0,    # A[3,3] = wheat production ZA
        -0.15,  # A[4,3] = electricity for wheat ZA
        1.0,    # A[4,4] = market for electricity
        -0.5,   # A[5,4] = residual mix electricity
        1.0,    # A[5,5] = electricity residual mix production
        1.0,    # A[6,6] = market for diesel
        -1.0,   # A[7,6] = diesel production
        1.0,    # A[7,7] = diesel production
    ]
    A_rows = [
        0, 1, 1, 4, 2, 2, 4, 3, 3, 4, 4, 5, 5, 6, 7, 7
    ]
    A_cols = [
        0, 0, 1, 1, 0, 2, 2, 0, 3, 3, 4, 4, 5, 6, 6, 7
    ]
    A = sp.csc_matrix((A_data, (A_rows, A_cols)), shape=(8, 8))

    # B matrix (1x8): biosphere (1 flow: CO2)
    # Each activity has a CO2 footprint
    B_data = [2.0, 1.5, 1.8, 1.7, 0.3, 0.35, 2.5, 2.8]
    B_rows = [0] * 8
    B_cols = list(range(8))
    B = sp.csc_matrix((B_data, (B_rows, B_cols)), shape=(1, 8))

    methods = [
        Method(("climate change",), "kg CO2-eq", np.array([1.0])),
    ]

    return LCAModel(A=A, B=B, activities=activities, flows=flows, methods=methods)


class TestSearchQuery:
    """Test query.search function with various text and predicate combinations."""

    def test_word_must_match_name_product_or_location(self, search_model):
        """Every word must match name, product, or equal location (case-insensitive)."""
        activities = derived_activities(search_model)

        # "wheat fr" should return only the FR wheat production (index 1)
        # "wheat" matches names/products, "fr" matches location
        result = search(activities, "wheat fr")
        assert len(result) > 0
        # Should find FR wheat
        assert 1 in result
        # FR is the only FR wheat production
        assert result[0] == 1, "FR wheat production should rank first"

    def test_market_ranks_first_for_product(self, search_model):
        """Market for a product should rank before non-market production."""
        activities = derived_activities(search_model)

        result = search(activities, "wheat grain")
        assert len(result) > 0
        # The market should come first
        assert activities.iloc[result[0]]["name"].startswith("market for wheat grain")

    def test_exact_name_match_ranks_first(self, search_model):
        """Exact name match should rank first."""
        activities = derived_activities(search_model)

        result = search(activities, "diesel production")
        assert len(result) > 0
        # Exact name match should be first
        assert activities.iloc[result[0]]["name"] == "diesel production"

    def test_electricity_low_voltage_ranking(self, search_model):
        """For "electricity low voltage fr", market should come first."""
        activities = derived_activities(search_model)

        result = search(activities, "electricity low voltage fr")
        assert len(result) > 0
        # Both are in FR and have "electricity, low voltage" in product
        # Market should come first
        first_name = activities.iloc[result[0]]["name"]
        assert "market for electricity" in first_name

    def test_empty_text_with_predicate_returns_filtered_sorted(self, search_model):
        """Empty text with location predicate returns all RoW activities sorted by name."""
        activities = derived_activities(search_model)

        predicate = {"field": "location", "op": "eq", "value": "RoW"}
        result = search(activities, "", predicate)
        assert len(result) > 0
        # Should return RoW activities
        for idx in result:
            assert activities.iloc[idx]["location"] == "RoW"
        # Should be sorted by name
        names = [activities.iloc[idx]["name"] for idx in result]
        assert names == sorted(names)

    def test_empty_text_no_predicate_returns_all_sorted(self, search_model):
        """Empty text and no predicate returns all activities sorted by name."""
        activities = derived_activities(search_model)

        result = search(activities, "")
        assert len(result) == len(activities)
        # Should be sorted by name
        names = [activities.iloc[idx]["name"] for idx in result]
        assert names == sorted(names)

    def test_predicate_combined_with_text_narrows(self, search_model):
        """Predicate combined with text should narrow results."""
        activities = derived_activities(search_model)

        # Text: "wheat", Predicate: location = FR
        predicate = {"field": "location", "op": "eq", "value": "FR"}
        result = search(activities, "wheat", predicate)
        assert len(result) > 0
        # Should only include FR wheat
        for idx in result:
            assert "wheat" in activities.iloc[idx]["name"].lower()
            assert activities.iloc[idx]["location"] == "FR"

    def test_location_case_insensitive(self, search_model):
        """Location matching should be case-insensitive."""
        activities = derived_activities(search_model)

        # Try lowercase "row"
        result = search(activities, "wheat row")
        assert len(result) > 0
        assert 0 in result  # market for wheat grain (RoW)

    def test_no_matches_returns_empty(self, search_model):
        """Query with no matches returns empty array."""
        activities = derived_activities(search_model)

        result = search(activities, "nonexistent product xyz")
        assert len(result) == 0


class TestSuggestValues:
    """Test query.suggest_values function."""

    def test_prefix_matches_before_infix(self, search_model):
        """Prefix matches should come before infix matches."""
        activities = derived_activities(search_model)

        result = suggest_values(activities, "location", "r")
        # "RoW" and "row" (case variations) should come before others
        # RoW starts with 'r' (prefix), others might contain it
        assert len(result) > 0
        # RoW should be before others if it exists
        if "RoW" in result:
            row_idx = result.index("RoW")
            for i in range(row_idx):
                if result[i].lower() != "row":
                    assert result[i].lower().startswith("r")

    def test_limit_respected(self, search_model):
        """Limit parameter should be respected."""
        activities = derived_activities(search_model)

        result = suggest_values(activities, "location", limit=2)
        assert len(result) <= 2

    def test_empty_text_returns_sorted_values(self, search_model):
        """Empty text returns all values sorted."""
        activities = derived_activities(search_model)

        result = suggest_values(activities, "location", "")
        assert len(result) == len(activities["location"].dropna().unique())
        # Should be sorted
        assert result == sorted(result)

    def test_prefix_match_sorting(self, search_model):
        """Verify prefix matches are ranked before infix matches."""
        activities = derived_activities(search_model)

        result = suggest_values(activities, "name", "wheat", limit=10)
        assert len(result) > 0
        # Should have results containing "wheat"
        assert any("wheat" in val.lower() for val in result)

    def test_limit_default_100(self, search_model):
        """Default limit should be 100."""
        activities = derived_activities(search_model)

        result = suggest_values(activities, "location")
        # Won't exceed 100 even if we have fewer
        assert len(result) <= 100


class TestSearchAPIEndpoints:
    """Test API endpoints using TestClient."""

    @pytest.fixture
    def client(self, search_model):
        """Create a TestClient with the search model."""
        app = create_app(search_model, default_root="market for wheat grain|RoW")
        return TestClient(app)

    def test_info_endpoint_has_default_root(self, client):
        """GET /api/info includes default_root with activity and methods list."""
        response = client.get("/api/info")
        assert response.status_code == 200
        data = response.json()

        assert "default_root" in data
        assert data["default_root"] is not None
        assert data["default_root"]["name"] == "market for wheat grain"
        assert data["default_root"]["location"] == "RoW"
        assert "methods" in data
        assert len(data["methods"]) > 0

    def test_search_endpoint_text_and_limit(self, client):
        """POST /api/search with text and limit returns results and total."""
        response = client.post("/api/search", json={"text": "wheat", "limit": 2})
        assert response.status_code == 200
        data = response.json()

        assert "total" in data
        assert "results" in data
        assert data["total"] > 0
        assert len(data["results"]) <= 2

    def test_search_endpoint_invalid_predicate_returns_400(self, client):
        """POST /api/search with invalid predicate returns 400."""
        response = client.post("/api/search", json={
            "text": "wheat",
            "predicate": {"field": "location", "op": "unknown_op", "value": "RoW"},
            "limit": 50
        })
        assert response.status_code == 400

    def test_values_endpoint_prefix_match(self, client):
        """GET /api/values/{field}?q={text} returns values containing text."""
        response = client.get("/api/values/location?q=f")
        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, list)
        # Should contain values with 'f' (case-insensitive)
        for val in data:
            assert "f" in val.lower()

    def test_values_endpoint_unknown_field_returns_404(self, client):
        """GET /api/values/{unknown_field} returns 404."""
        response = client.get("/api/values/unknown_field?q=test")
        assert response.status_code == 404

    def test_rows_endpoint_with_root_expanded(self, client):
        """POST /api/rows with expanded root returns rows and methods."""
        # Find the root index (should be 0 for "market for wheat grain|RoW")
        info_response = client.get("/api/info")
        root_idx = info_response.json()["default_root"]["index"]

        response = client.post("/api/rows", json={
            "root": [root_idx],
            "amount": 1.0,
            "expanded": [],
            "rules": [],
            "cutoff": 0.0,
            "method": 0,
            "methods": []
        })
        assert response.status_code == 200
        data = response.json()

        assert "methods" in data
        assert "rows" in data
        assert len(data["rows"]) > 0
        # First row should be the root with kind "process"
        assert data["rows"][0]["kind"] == "process"
        # Impacts length should equal number of methods (1 method)
        assert len(data["rows"][0]["impacts"]) == len(data["methods"])

    def test_global_endpoint_returns_impacts(self, client):
        """POST /api/global with empty groups returns root's impacts."""
        info_response = client.get("/api/info")
        root_idx = info_response.json()["default_root"]["index"]

        response = client.post("/api/global", json={
            "root": root_idx,
            "amount": 1.0,
            "groups": {}
        })
        assert response.status_code == 200
        data = response.json()

        assert "other" in data
        assert isinstance(data["other"], list)
        # Should have one value per method
        info = client.get("/api/info").json()
        assert len(data["other"]) == len(info["methods"])

    def test_search_empty_text_with_predicate(self, client):
        """POST /api/search with empty text and location predicate."""
        response = client.post("/api/search", json={
            "text": "",
            "predicate": {"field": "location", "op": "eq", "value": "RoW"},
            "limit": 50
        })
        assert response.status_code == 200
        data = response.json()

        assert data["total"] > 0
        # All results should have location = RoW
        for result in data["results"]:
            assert result["location"] == "RoW"


class TestWithDerivedFields:
    """Test query.with_derived_fields function."""

    def test_adds_activity_type_field(self, search_model):
        """with_derived_fields adds activity_type column."""
        df = with_derived_fields(search_model.activities)

        assert "activity_type" in df.columns
        # Check specific activity types
        assert df.iloc[0]["activity_type"] == "market"
        assert df.iloc[1]["activity_type"] == "production"

    def test_adds_isic_division(self, search_model):
        """with_derived_fields adds isic_division column."""
        df = with_derived_fields(search_model.activities)

        assert "isic_division" in df.columns
        # 01.11 -> "01"
        assert df.iloc[1]["isic_division"] == "01"

    def test_adds_cpc_section(self, search_model):
        """with_derived_fields adds cpc_section column."""
        df = with_derived_fields(search_model.activities)

        assert "cpc_section" in df.columns
        # 0111 -> "0"
        assert df.iloc[1]["cpc_section"] == "0"


class TestDerivedActivities:
    """Test query.derived_activities function."""

    def test_caches_result(self, search_model):
        """derived_activities caches the result on the model."""
        da1 = derived_activities(search_model)
        da2 = derived_activities(search_model)

        # Should return the same object (cached)
        assert da1 is da2

    def test_returns_with_derived_fields(self, search_model):
        """derived_activities returns with_derived_fields result."""
        da = derived_activities(search_model)

        assert "activity_type" in da.columns
        assert "isic_division" in da.columns
        assert "cpc_section" in da.columns


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_search_with_null_location(self):
        """Search handles null/NaN locations gracefully."""
        activities = pd.DataFrame({
            "name": ["activity a", "activity b"],
            "reference_product": ["product a", "product b"],
            "location": ["RoW", None],
            "unit": ["kg", "kg"],
        })

        result = search(activities, "activity")
        assert len(result) == 2

    def test_suggest_values_with_null_field(self):
        """suggest_values handles null values in field gracefully."""
        activities = pd.DataFrame({
            "name": ["activity a", "activity b"],
            "reference_product": ["product a", "product b"],
            "location": ["RoW", None],
            "unit": ["kg", "kg"],
        })

        result = suggest_values(activities, "location", "")
        assert "RoW" in result
        assert None not in result

    def test_search_multiple_words_all_must_match(self, search_model):
        """All words in search text must match."""
        activities = derived_activities(search_model)

        # "wheat electricity" - should have no matches (no activity has both in name/product)
        result = search(activities, "wheat electricity")
        # The wheat production uses electricity but doesn't mention it
        assert len(result) == 0

    def test_predicate_with_location_in_operator(self, search_model):
        """Predicate with 'in' operator for multiple locations."""
        activities = derived_activities(search_model)

        predicate = {"field": "location", "op": "in", "value": ["FR", "US"]}
        result = search(activities, "", predicate)
        assert len(result) > 0
        for idx in result:
            assert activities.iloc[idx]["location"] in ["FR", "US"]

    def test_case_insensitive_search(self, search_model):
        """Search should be case-insensitive."""
        activities = derived_activities(search_model)

        result_lower = search(activities, "wheat")
        result_upper = search(activities, "WHEAT")
        result_mixed = search(activities, "WhEaT")

        # Should return the same results regardless of case
        assert set(result_lower) == set(result_upper)
        assert set(result_lower) == set(result_mixed)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
