# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Queries on activity metadata, as JSON-serialisable predicates.

A predicate is a dict, evaluated vectorised over the activities dataframe:

- ``{"field": "location", "op": "in", "value": ["FR", "DE"]}``
- ``{"field": "name", "op": "contains", "value": "electricity"}``
- ``{"all": [p1, p2]}``, ``{"any": [p1, p2]}``, ``{"not": p}``
- ``{"products": [12, 345]}`` — explicit selection (manual groups); ``{"flows": [...]}``
  for elementary flows (predicates evaluated over :func:`derived_flows`)

Operators: ``eq``, ``ne``, ``in``, ``contains``, ``startswith``, ``regex``, and for numbers ``gt``, ``ge``,
``lt``, ``le``. Text operators are case-insensitive.

The rules of the tree can also test the exchange that brings a row in (``EXCHANGE_FIELDS``):
``exchange_type`` (``technosphere``, ``avoided``, ``waste``; ``resource``, ``emission`` for elementary flows) and
``exchange_amount`` (as recorded in the dataset of the process above, per unit of its reference product: avoided
products and wastes negative). They belong to an edge, not to an activity: the tree evaluates them over a frame of
the siblings (:func:`exchange_frame`), the global analyses over the entries of the matrices (``analysis``); the
preview of a rule (:func:`without_exchange`) leaves them out.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

OPERATORS = ("eq", "ne", "in", "contains", "startswith", "regex", "gt", "ge", "lt", "le")
NUMERIC_OPERATORS = {"gt": "__gt__", "ge": "__ge__", "lt": "__lt__", "le": "__le__"}
EXCHANGE_FIELDS = ("exchange_type", "exchange_amount")
EXCHANGE_TYPES = ("technosphere", "avoided", "waste")
FLOW_EXCHANGE_TYPES = ("resource", "emission")


def uses_exchange(predicate: dict | None) -> bool:
    """Whether ``predicate`` tests the exchange of a row (``EXCHANGE_FIELDS``), not only the activity or flow."""
    if not predicate:
        return False
    if "all" in predicate or "any" in predicate:
        return any(uses_exchange(p) for p in predicate.get("all", predicate.get("any")))
    if "not" in predicate:
        return uses_exchange(predicate["not"])
    return predicate.get("field") in EXCHANGE_FIELDS


def predicate_fields(predicate: dict | None) -> set[str]:
    """The fields ``predicate`` tests."""
    if not predicate:
        return set()
    for key in ("all", "any"):
        if key in predicate:
            return set().union(*(predicate_fields(p) for p in predicate[key]))
    if "not" in predicate:
        return predicate_fields(predicate["not"])
    return {predicate["field"]} if "field" in predicate else set()


def without_exchange(predicate: dict | None) -> dict | None:
    """``predicate`` with its conditions on exchanges left out (None: nothing is left), for what tests activities alone:
    the processes that may match, whatever their exchange."""
    if not predicate:
        return None
    for key in ("all", "any"):
        if key in predicate:
            parts = [q for q in (without_exchange(p) for p in predicate[key]) if q is not None]
            return {key: parts} if parts else None
    if "not" in predicate:
        inner = without_exchange(predicate["not"])
        return {"not": inner} if inner is not None else None
    return None if predicate.get("field") in EXCHANGE_FIELDS else predicate


def exchange_frame(table: pd.DataFrame, indices: list[int], exchanges: list[tuple[str, float]]) -> pd.DataFrame:
    """The rows ``indices`` of ``table`` (activities or flows) with the type and amount of the exchange of each: what a
    predicate using ``EXCHANGE_FIELDS`` is evaluated over (``_row``: the position in ``table``, for explicit selections)."""
    frame = table.iloc[indices].reset_index(drop=True)
    return frame.assign(_row=np.asarray(indices, dtype=int),
                        exchange_type=[t for t, _ in exchanges], exchange_amount=[float(a) for _, a in exchanges])


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
        df["place"] = categories.map(lambda c: ", ".join(x for x in c if x))  # what the lists write: "air, urban air…"
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
        chosen = list(predicate.get("products", predicate.get("flows")))
        if "_row" in activities:  # a frame of some rows (``exchange_frame``)
            return np.isin(activities["_row"].to_numpy(), chosen)
        mask = np.zeros(len(activities), dtype=bool)
        mask[chosen] = True
        return mask

    column = activities[predicate["field"]]
    op, value = predicate["op"], predicate["value"]
    numeric = pd.api.types.is_numeric_dtype(column)
    if op in NUMERIC_OPERATORS or (numeric and op in ("eq", "ne")):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{predicate['field']}: {value!r} is not a number")
        result = getattr(pd.to_numeric(column, errors="coerce"), NUMERIC_OPERATORS.get(op, f"__{op}__"))(number)
    elif op == "eq":
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


CODE_FIELDS = ("location", "isic", "isic_division", "cpc", "cpc_section")


def field_options(frame: pd.DataFrame, field: str, text: str = "", limit: int = 200) -> dict:
    """The distinct values of ``field`` with how many rows have each, for the helper of the filter
    builder. Code fields carry what the code stands for (``name``): a location's country, the name of an
    ISIC or CPC class (written in the data after the code, which is the ``value``; ``full`` is the
    whole text) or division or section. ``total`` counts all the matches of ``text`` (in the value or
    the name), ``items`` the first ``limit``."""
    from .codes import CPC_SECTIONS, ISIC_DIVISIONS, location_name

    counts = frame[field].dropna().astype(str).value_counts()
    counts = counts[counts.index.str.strip() != ""]  # activities without a classification have a blank division
    items = []
    for value, n in counts.items():
        if field in ("isic", "cpc"):
            code, _, name = value.partition(":")
            items.append({"value": code.strip(), "name": name.strip(), "count": int(n), "full": value})
        elif field in CODE_FIELDS:
            name = {"location": location_name, "isic_division": ISIC_DIVISIONS.get, "cpc_section": CPC_SECTIONS.get}[field](value)
            items.append({"value": value, "name": name or "", "count": int(n)})
        else:
            items.append({"value": value, "count": int(n)})
    if text:
        t = text.lower()
        items = [it for it in items if t in it["value"].lower() or t in it.get("name", "").lower()]
        items.sort(key=lambda it: (not it["value"].lower().startswith(t), it["value"].lower() != t, it.get("name", "") or it["value"]))
    elif field == "location":
        items.sort(key=lambda it: (it["name"] or it["value"]).lower())
    elif field in CODE_FIELDS:
        items.sort(key=lambda it: it["value"])
    else:
        items.sort(key=lambda it: (-it["count"], it["value"]))
    return {"field": field, "named": field in CODE_FIELDS, "total": len(items), "items": items[:limit]}


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
