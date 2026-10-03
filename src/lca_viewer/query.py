"""Queries on activity metadata, as JSON-serialisable predicates.

A predicate is a dict, evaluated vectorised over the activities dataframe:

- ``{"field": "location", "op": "in", "value": ["FR", "DE"]}``
- ``{"field": "name", "op": "contains", "value": "electricity"}``
- ``{"all": [p1, p2]}``, ``{"any": [p1, p2]}``, ``{"not": p}``
- ``{"products": [12, 345]}`` — explicit selection (manual groups)

Operators: ``eq``, ``ne``, ``in``, ``contains``, ``startswith``, ``regex``.
Text operators are case-insensitive.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

OPERATORS = ("eq", "ne", "in", "contains", "startswith", "regex")


def activity_type(name: str) -> str:
    """Coarse ecoinvent activity type, derived from the activity name."""
    if name.startswith("market group for"):
        return "market group"
    if name.startswith("market for"):
        return "market"
    if name.startswith("treatment of") or name.startswith("treatment, "):
        return "treatment"
    return "production"


def with_derived_fields(activities: pd.DataFrame) -> pd.DataFrame:
    """Add fields useful for thematic grouping, computed from metadata."""
    df = activities.copy()
    df["activity_type"] = df["name"].map(activity_type)
    isic = df["isic"].fillna("")
    df["isic_division"] = isic.str.split(":").str[0].str[:2]
    cpc = df["cpc"].fillna("")
    df["cpc_section"] = cpc.str.split(":").str[0].str[:1]
    return df


def derived_activities(model) -> pd.DataFrame:
    """``with_derived_fields`` of the model's activities, computed once per model."""
    if getattr(model, "_derived_activities", None) is None:
        model._derived_activities = with_derived_fields(model.activities)
    return model._derived_activities


def evaluate(predicate: dict, activities: pd.DataFrame) -> np.ndarray:
    """Boolean mask over activities (= products) matching ``predicate``."""
    if "all" in predicate:
        mask = np.ones(len(activities), dtype=bool)
        for p in predicate["all"]:
            mask &= evaluate(p, activities)
        return mask
    if "any" in predicate:
        mask = np.zeros(len(activities), dtype=bool)
        for p in predicate["any"]:
            mask |= evaluate(p, activities)
        return mask
    if "not" in predicate:
        return ~evaluate(predicate["not"], activities)
    if "products" in predicate:
        mask = np.zeros(len(activities), dtype=bool)
        mask[list(predicate["products"])] = True
        return mask

    column = activities[predicate["field"]]
    op, value = predicate["op"], predicate["value"]
    if op == "eq":
        result = column == value
    elif op == "ne":
        result = column != value
    elif op == "in":
        result = column.isin(value)
    else:
        text = column.fillna("").astype(str)
        if op == "contains":
            result = text.str.contains(value, case=False, regex=False)
        elif op == "startswith":
            result = text.str.lower().str.startswith(str(value).lower())
        elif op == "regex":
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", "This pattern is interpreted as a regular expression")
                result = text.str.contains(value, case=False, regex=True)
        else:
            raise ValueError(f"Unknown operator {op!r}, expected one of {OPERATORS}")
    return np.asarray(result, dtype=bool)
