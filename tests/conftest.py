"""Test fixtures for LCA model testing."""

import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp

from lca_viewer.model import LCAModel, Method


@pytest.fixture
def linear_chain_model():
    """Simple linear chain: wheat -> flour -> bread.

    3 activities (indices 0, 1, 2)
    2 flows: CO2, water
    2 methods: Global Warming (GWP), Water Stress (WS)
    """
    # Activities: 0=wheat, 1=flour, 2=bread
    activities = pd.DataFrame({
        "name": ["wheat", "flour", "bread"],
        "reference_product": ["wheat", "flour", "bread"],
        "location": ["world", "world", "world"],
        "unit": ["kg", "kg", "kg"],
    })

    # Flows: 0=CO2, 1=water
    flows = pd.DataFrame({
        "name": ["CO2 to air", "Water consumed"],
        "unit": ["kg", "L"],
    })

    # A matrix (3x3): technosphere
    # A[j,j] = production amount (positive)
    # A[i,j] = input amount (negative) to produce 1 unit of j
    # 0.5 kg wheat -> 1 kg flour
    # 0.8 kg flour -> 1 kg bread
    A_data = [
        1.0,   # A[0,0] = wheat production (1 kg)
        -0.5,  # A[0,1] = 0.5 kg wheat input to flour
        1.0,   # A[1,1] = flour production (1 kg)
        -0.8,  # A[1,2] = 0.8 kg flour input to bread
        1.0,   # A[2,2] = bread production (1 kg)
    ]
    A_rows = [0, 0, 1, 1, 2]
    A_cols = [0, 1, 1, 2, 2]
    A = sp.csc_matrix((A_data, (A_rows, A_cols)), shape=(3, 3))

    # B matrix (2x3): biosphere (flows x activities)
    # Wheat: 2 kg CO2, 10 L water per kg
    # Flour: 0.5 kg CO2, 2 L water per kg (processing)
    # Bread: 0.3 kg CO2, 1 L water per kg (processing)
    B_data = [2.0, 10.0, 0.5, 2.0, 0.3, 1.0]
    B_rows = [0, 1, 0, 1, 0, 1]
    B_cols = [0, 0, 1, 1, 2, 2]
    B = sp.csc_matrix((B_data, (B_rows, B_cols)), shape=(2, 3))

    # Methods
    # GWP: 1 kg CO2-eq per kg CO2, 0 per L water
    # WS: 0 per kg CO2, 1 L-eq per L water
    methods = [
        Method(("climate change",), "kg CO2-eq", np.array([1.0, 0.0])),
        Method(("water", "stress"), "L", np.array([0.0, 1.0])),
    ]

    return LCAModel(A=A, B=B, activities=activities, flows=flows, methods=methods)


@pytest.fixture
def loop_model():
    """System with a loop: electricity uses coal, coal mining uses electricity.

    2 activities: 0=coal, 1=electricity
    2 flows: 0=CO2, 1=other
    2 methods: GWP, Other Impact
    """
    activities = pd.DataFrame({
        "name": ["coal", "electricity"],
        "reference_product": ["coal", "electricity"],
        "location": ["world", "world"],
        "unit": ["kg", "kWh"],
    })

    flows = pd.DataFrame({
        "name": ["CO2 to air", "Particulates"],
        "unit": ["kg", "kg"],
    })

    # A matrix (2x2):
    # 1 kg coal production from 1 kg coal
    # Coal: 0.1 kWh electricity per kg coal (to run mining)
    # 1 kWh electricity production from 1 kWh electricity
    # Electricity: 0.5 kg coal per kWh (thermal power plant)
    A_data = [1.0, -0.1, 1.0, -0.5]
    A_rows = [0, 1, 1, 0]
    A_cols = [0, 0, 1, 1]
    A = sp.csc_matrix((A_data, (A_rows, A_cols)), shape=(2, 2))

    # B matrix (2x2):
    # Coal: 1.5 kg CO2, 0.1 kg particulates per kg
    # Electricity: 0.8 kg CO2, 0.05 kg particulates per kWh
    B_data = [1.5, 0.1, 0.8, 0.05]
    B_rows = [0, 1, 0, 1]
    B_cols = [0, 0, 1, 1]
    B = sp.csc_matrix((B_data, (B_rows, B_cols)), shape=(2, 2))

    methods = [
        Method(("climate change",), "kg CO2-eq", np.array([1.0, 0.0])),
        Method(("particulates",), "kg", np.array([0.0, 1.0])),
    ]

    return LCAModel(A=A, B=B, activities=activities, flows=flows, methods=methods)


@pytest.fixture
def waste_treatment_model():
    """System with waste treatment (negative diagonal).

    3 activities: 0=product, 1=waste treatment, 2=something else
    As in Brightway / ecoinvent: the waste treatment has a negative diagonal
    (-1 kg of "waste" produced) and the waste-generating activity has a
    POSITIVE entry for the waste it generates.
    """
    activities = pd.DataFrame({
        "name": ["product", "waste treatment", "virgin material"],
        "reference_product": ["product", "waste", "material"],
        "location": ["world", "world", "world"],
        "unit": ["kg", "kg", "kg"],
    })

    flows = pd.DataFrame({
        "name": ["CO2 to air", "waste to landfill"],
        "unit": ["kg", "kg"],
    })

    # A matrix (3x3):
    # A[0,0] = 1.0: produce 1 kg product
    # A[1,0] = +0.2: 0.2 kg waste generated when producing 1 kg product
    # A[1,1] = -1.0: treat 1 kg waste (negative, waste treatment)
    # A[2,1] = -0.15: recover 0.15 kg virgin material from 1 kg waste treated
    # A[2,2] = 1.0: produce 1 kg material
    A_data = [1.0, 0.2, -1.0, -0.15, 1.0]
    A_rows = [0, 1, 1, 2, 2]
    A_cols = [0, 0, 1, 1, 2]
    A = sp.csc_matrix((A_data, (A_rows, A_cols)), shape=(3, 3))

    # B matrix (2x3):
    # Product: 2 kg CO2, 0 kg waste per kg
    # Waste treatment: 0.5 kg CO2, 0.9 kg waste (landfilled) per kg treated
    # Material: 3 kg CO2, 0 kg waste per kg
    B_data = [2.0, 0.0, 0.5, 0.9, 3.0, 0.0]
    B_rows = [0, 1, 0, 1, 0, 1]
    B_cols = [0, 0, 1, 1, 2, 2]
    B = sp.csc_matrix((B_data, (B_rows, B_cols)), shape=(2, 3))

    methods = [
        Method(("climate change",), "kg CO2-eq", np.array([1.0, 0.0])),
        Method(("waste",), "kg", np.array([0.0, 1.0])),
    ]

    return LCAModel(A=A, B=B, activities=activities, flows=flows, methods=methods)


@pytest.fixture
def non_unit_production_model():
    """System with non-unit production amount.

    2 activities: 0=raw material, 1=product
    Diagonal of A[1,1] = 2.0 (produces 2 kg per operation)
    """
    activities = pd.DataFrame({
        "name": ["raw material", "product"],
        "reference_product": ["material", "product"],
        "location": ["world", "world"],
        "unit": ["kg", "kg"],
    })

    flows = pd.DataFrame({
        "name": ["CO2 to air"],
        "unit": ["kg"],
    })

    # A matrix (2x2):
    # A[0,0] = 1.0: produce 1 kg material
    # A[0,1] = -1.5: use 1.5 kg material to produce (2 kg product, see diagonal)
    # A[1,1] = 2.0: produce 2 kg product per operation
    A_data = [1.0, -1.5, 2.0]
    A_rows = [0, 0, 1]
    A_cols = [0, 1, 1]
    A = sp.csc_matrix((A_data, (A_rows, A_cols)), shape=(2, 2))

    # B matrix (1x2):
    # Material: 5 kg CO2 per kg
    # Product: 2 kg CO2 per 2 kg (1 kg CO2-eq per kg product when normalized)
    B_data = [5.0, 2.0]
    B_rows = [0, 0]
    B_cols = [0, 1]
    B = sp.csc_matrix((B_data, (B_rows, B_cols)), shape=(1, 2))

    methods = [
        Method(("climate change",), "kg CO2-eq", np.array([1.0])),
    ]

    return LCAModel(A=A, B=B, activities=activities, flows=flows, methods=methods)


@pytest.fixture
def grouping_model():
    """System for testing grouping and view operations.

    6 activities forming a supply chain with multiple locations and common intermediates:
    0: "market for bread" (GLO)
    1: "bread production" (FR)
    2: "bread production" (DE)
    3: "bread production" (US)
    4: "transport, freight, lorry" (RER)
    5: "market for electricity" (FR)

    Market for bread (0) consumes bread from 3 locations (1, 2, 3).
    Each bread production uses transport (4) and electricity (5).
    Transport also uses electricity (5), creating shared upstream.
    """
    activities = pd.DataFrame({
        "name": [
            "market for bread",
            "bread production",
            "bread production",
            "bread production",
            "transport, freight, lorry",
            "market for electricity",
        ],
        "reference_product": ["bread", "bread", "bread", "bread", "transport", "electricity"],
        "location": ["GLO", "FR", "DE", "US", "RER", "FR"],
        "unit": ["kg", "kg", "kg", "kg", "tkm", "kWh"],
        "isic": [None, "10.61", "10.61", "10.61", "49.40", "35.11"],
        "cpc": [None, "1071", "1071", "1071", "7121", "1711"],
    })

    flows = pd.DataFrame({
        "name": ["CO2 to air", "Energy consumed"],
        "unit": ["kg", "MJ"],
    })

    # A matrix (6x6):
    # Market for bread (0) produces 1 kg bread, uses 0.4 kg from FR, 0.35 kg from DE, 0.25 kg from US
    # Each bread production (1,2,3) produces 1 kg bread, uses 0.1 tkm transport + 0.5 kWh electricity
    # Transport (4) produces 1 tkm, uses 0.3 kWh electricity
    # Electricity (5) produces 1 kWh (no inputs)
    A_data = [
        1.0,      # A[0,0] = market for bread production
        -0.4,     # A[1,0] = bread from FR to market
        1.0,      # A[1,1] = bread production (FR)
        -0.1,     # A[4,1] = transport used by FR bread
        -0.5,     # A[5,1] = electricity used by FR bread
        -0.35,    # A[2,0] = bread from DE to market
        1.0,      # A[2,2] = bread production (DE)
        -0.1,     # A[4,2] = transport used by DE bread
        -0.5,     # A[5,2] = electricity used by DE bread
        -0.25,    # A[3,0] = bread from US to market
        1.0,      # A[3,3] = bread production (US)
        -0.1,     # A[4,3] = transport used by US bread
        -0.5,     # A[5,3] = electricity used by US bread
        1.0,      # A[4,4] = transport production
        -0.3,     # A[5,4] = electricity used by transport
        1.0,      # A[5,5] = electricity production
    ]
    A_rows = [0, 1, 1, 4, 5, 2, 2, 4, 5, 3, 3, 4, 5, 4, 5, 5]
    A_cols = [0, 0, 1, 1, 1, 0, 2, 2, 2, 0, 3, 3, 3, 4, 4, 5]
    A = sp.csc_matrix((A_data, (A_rows, A_cols)), shape=(6, 6))

    # B matrix (2x6):
    # Market for bread: 0.1 kg CO2, 0 MJ
    # Bread FR: 2.0 kg CO2, 5 MJ per kg
    # Bread DE: 1.8 kg CO2, 4.8 MJ per kg
    # Bread US: 3.0 kg CO2, 8 MJ per kg
    # Transport: 0.15 kg CO2, 2 MJ per tkm
    # Electricity: 0.5 kg CO2, 1 MJ per kWh
    B_data = [
        0.1, 0.0,       # market for bread
        2.0, 5.0,       # bread FR
        1.8, 4.8,       # bread DE
        3.0, 8.0,       # bread US
        0.15, 2.0,      # transport
        0.5, 1.0,       # electricity
    ]
    B_rows = [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
    B_cols = [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    B = sp.csc_matrix((B_data, (B_rows, B_cols)), shape=(2, 6))

    methods = [
        Method(("climate change",), "kg CO2-eq", np.array([1.0, 0.0])),
        Method(("cumulative energy",), "MJ", np.array([0.0, 1.0])),
    ]

    return LCAModel(A=A, B=B, activities=activities, flows=flows, methods=methods)
