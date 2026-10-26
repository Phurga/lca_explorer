"""Queries on activity metadata, as JSON-serialisable predicates.

A predicate is a dict, evaluated vectorised over the activities dataframe:

- ``{"field": "location", "op": "in", "value": ["FR", "DE"]}``
- ``{"field": "name", "op": "contains", "value": "electricity"}``
- ``{"all": [p1, p2]}``, ``{"any": [p1, p2]}``, ``{"not": p}``
- ``{"products": [12, 345]}`` — explicit selection (manual groups); ``{"flows": [...]}``
  for elementary flows (predicates evaluated over :func:`derived_flows`)

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
    missing = pd.Series("", index=df.index, dtype=object)  # importers may not classify activities
    isic = df["isic"].fillna("") if "isic" in df else missing
    df["isic_division"] = isic.str.split(":").str[0].str[:2]
    cpc = df["cpc"].fillna("") if "cpc" in df else missing
    df["cpc_section"] = cpc.str.split(":").str[0].str[:1]
    return df


def derived_activities(model) -> pd.DataFrame:
    """``with_derived_fields`` of the model's activities, computed once per model."""
    if getattr(model, "_derived_activities", None) is None:
        df = with_derived_fields(model.activities)
        # waste: produced with a negative amount (treatments and markets of wastes)
        df["flow_type"] = np.where(model.diagonal < 0, "waste", "product")
        model._derived_activities = df
    return model._derived_activities


def derived_flows(model) -> pd.DataFrame:
    """Elementary flow metadata with ``compartment`` and ``subcompartment`` split out of
    ``categories`` (e.g. ``("air", "urban air close to ground")``), computed once per model."""
    if getattr(model, "_derived_flows", None) is None:
        df = model.flows.copy()
        categories = df["categories"] if "categories" in df else pd.Series([()] * len(df), index=df.index)
        categories = categories.map(lambda c: tuple(c) if isinstance(c, (tuple, list)) else ())
        df["compartment"] = categories.map(lambda c: c[0] if c else "")
        df["subcompartment"] = categories.map(lambda c: ", ".join(c[1:]))
        if "unit" not in df:
            df["unit"] = ""
        model._derived_flows = df
    return model._derived_flows


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
    if "products" in predicate or "flows" in predicate:  # explicit rows of the table
        mask = np.zeros(len(activities), dtype=bool)
        mask[list(predicate.get("products", predicate.get("flows")))] = True
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


def search(activities: pd.DataFrame, text: str = "", predicate: dict | None = None) -> np.ndarray:
    """Indices of activities matching free ``text`` and ``predicate``, best first.

    Every word must appear in the name or reference product, or equal the
    location. Ranking (punctuation ignored): exact name, exact product,
    markets, name starting with the text, product starting with it, text
    contained as a phrase, then shorter names.
    """
    mask = np.ones(len(activities), dtype=bool)
    if predicate:
        mask &= evaluate(predicate, activities)
    words = text.lower().split()
    name = activities["name"].str.lower()
    product = activities["reference_product"].fillna("").str.lower()
    location = activities["location"].fillna("").str.lower()
    for word in words:
        mask &= np.asarray(
            name.str.contains(word, regex=False) | product.str.contains(word, regex=False) | (location == word)
        )
    found = np.flatnonzero(mask)
    if not words:
        return found[np.argsort(name.iloc[found].to_numpy(), kind="stable")]
    # Rank on the text without words that only matched the location, ignoring punctuation
    phrase = _normalise(pd.Series([
        " ".join(w for w in words if not (location.iloc[found] == w).all()) or text
    ])).iat[0]
    n, p = _normalise(name.iloc[found]), _normalise(product.iloc[found])
    keys = [  # lexsort: last key is the primary one
        n.to_numpy(),
        n.str.len().to_numpy(),
        ~(n.str.contains(phrase, regex=False) | p.str.contains(phrase, regex=False)).to_numpy(),
        ~p.str.startswith(phrase).to_numpy(),
        ~n.str.startswith(phrase).to_numpy(),
        ~n.str.startswith("market").to_numpy(),  # markets are what one usually links to
        ~(p == phrase).to_numpy(),
        ~(n == phrase).to_numpy(),
    ]
    return found[np.lexsort(keys)]


def _normalise(text: pd.Series) -> pd.Series:
    return text.str.lower().str.replace(r"[,;:()\[\]/]", " ", regex=True).str.split().str.join(" ")


def suggest_values(activities: pd.DataFrame, field: str, text: str = "", limit: int = 100) -> list[str]:
    """Distinct values of ``field`` containing ``text``, prefix matches first."""
    values = pd.Series(activities[field].dropna().astype(str).unique())
    if text:
        lower = values.str.lower()
        t = text.lower()
        values = values[lower.str.contains(t, regex=False)]
        order = np.lexsort([values.str.lower().to_numpy(), values.str.len().to_numpy(),
                            ~values.str.lower().str.startswith(t).to_numpy()])
        values = values.iloc[order]
    else:
        values = values.sort_values()
    return values.head(limit).tolist()
