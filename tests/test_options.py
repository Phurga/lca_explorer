"""The values of the filter fields, with names for codes: the helper of the filter builder."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from lca_viewer.codes import ISIC_DIVISIONS, location_name
from lca_viewer.query import field_options, with_derived_fields
from lca_viewer.server import create_app

ACTIVITIES = with_derived_fields(pd.DataFrame({
    "name": ["market for a", "a production", "a production", "treatment of b", "market for c"],
    "location": ["FR", "FR", "BR-GO", "RoW", "Europe without Switzerland"],
    "unit": ["kg", "kg", "kWh", "kg", "kg"],
    "isic": ["0111:Growing of cereals (except rice), leguminous crops and oil seeds", "0111:Growing of cereals (except rice), leguminous crops and oil seeds",
             "3510:Electric power generation, transmission and distribution", None, "4322:Plumbing, heat and air-conditioning installation"],
    "cpc": ["0111: Wheat", "0111: Wheat", "17100: Electrical energy", "7: Financial and related services; real estate services", None],
}))


def test_location_names():
    assert location_name("FR") == "France" and location_name("RoW") == "Rest of the world"
    assert location_name("BR-GO") == "Brazil – Goiás" and location_name("CA-QC") == "Canada – Québec"
    assert location_name("US-NPCC") == "United States – NPCC"
    assert location_name("UN-OCEANIA") == "United Nations region OCEANIA"
    assert location_name("Europe without Switzerland") == ""


def test_locations_come_with_names_and_counts_by_name():
    found = field_options(ACTIVITIES, "location")
    assert found["named"] and found["total"] == 4
    by = {i["value"]: i for i in found["items"]}
    assert by["FR"] == {"value": "FR", "name": "France", "count": 2}
    assert [i["name"] or i["value"] for i in found["items"]] == sorted(
        [i["name"] or i["value"] for i in found["items"]], key=str.lower)


def test_search_matches_the_code_or_the_name():
    assert [i["value"] for i in field_options(ACTIVITIES, "location", "france")["items"]] == ["FR"]
    assert [i["value"] for i in field_options(ACTIVITIES, "location", "fr")["items"]][0] == "FR"  # the code first
    assert field_options(ACTIVITIES, "location", "brazil")["items"][0]["value"] == "BR-GO"
    assert field_options(ACTIVITIES, "location", "zzz") == {"field": "location", "named": True, "total": 0, "items": []}


def test_isic_and_cpc_split_the_code_from_the_name():
    isic = {i["value"]: i for i in field_options(ACTIVITIES, "isic")["items"]}
    assert isic["0111"]["name"].startswith("Growing of cereals") and isic["0111"]["count"] == 2
    assert isic["0111"]["full"] == "0111:Growing of cereals (except rice), leguminous crops and oil seeds"
    assert list(isic) == ["0111", "3510", "4322"]  # by code, empty values left out
    cpc = {i["value"]: i for i in field_options(ACTIVITIES, "cpc")["items"]}
    assert cpc["17100"]["name"] == "Electrical energy" and cpc["7"]["name"].startswith("Financial")
    assert [i["value"] for i in field_options(ACTIVITIES, "isic", "plumbing")["items"]] == ["4322"]


def test_divisions_and_sections_are_named():
    divisions = {i["value"]: i["name"] for i in field_options(ACTIVITIES, "isic_division")["items"]}
    assert divisions["01"] == ISIC_DIVISIONS["01"] and divisions["35"].startswith("Electricity")
    assert "" not in divisions  # the activity without an ISIC class is not a division
    sections = {i["value"]: i["name"] for i in field_options(ACTIVITIES, "cpc_section")["items"]}
    assert sections["1"].startswith("Ores") and sections["7"].startswith("Financial")


def test_other_fields_list_values_by_count():
    found = field_options(ACTIVITIES, "unit")
    assert not found["named"]
    assert found["items"] == [{"value": "kg", "count": 4}, {"value": "kWh", "count": 1}]
    assert field_options(ACTIVITIES, "activity_type")["items"] == [
        {"value": "market", "count": 2}, {"value": "production", "count": 2}, {"value": "treatment", "count": 1}]  # ties by value
    assert len(field_options(ACTIVITIES, "name", limit=2)["items"]) == 2 and field_options(ACTIVITIES, "name", limit=2)["total"] == 4


def test_api(linear_chain_model):
    client = TestClient(create_app(linear_chain_model))
    found = client.get("/api/options/location").json()
    assert found["items"] == [{"value": "world", "name": "", "count": 3}]
    assert client.get("/api/options/unit?q=k").json()["items"] == [{"value": "kg", "count": 3}]
    flows = client.get("/api/options/name?target=flows").json()
    assert {i["value"] for i in flows["items"]} == {"CO2 to air", "Water consumed"}
    assert client.get("/api/options/nope").status_code == 404
