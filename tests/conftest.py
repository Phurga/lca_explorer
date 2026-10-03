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
