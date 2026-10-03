"""Grouping demo on the reference example (1 kg market for wheat grain, feed | GLO)."""

from pathlib import Path

from lca_viewer.analysis import direct_contribution, first_match_contribution
from lca_viewer.model import LCAModel
from lca_viewer.tree import Tree
from lca_viewer.view import GroupRule, View

model = LCAModel.load(Path("data/cache/ei391_cutoff"))
acts = model.activities
wheat = int(acts.index[(acts.name == "market for wheat grain, feed") & (acts.location == "GLO")][0])
k = model.method_index(("EF v3.1", "climate change", "global warming potential (GWP100)"))
root = Tree(model, wheat).root.impacts[k]


def show(view):
    for r in view.rows():
        mark = "-" if r.expanded else ("+" if r.expandable else " ")
        print(f"{'  ' * r.depth}{mark} {r.impacts[k] / root:6.1%}  {r.label}")
    total = sum(r.impacts[k] for r in view.frontier())
    print(f"  frontier: {len(view.frontier())} rows, sum = {total / root:.6%} of root\n")


# 1. Tree with thematic + query grouping
view = View(Tree(model, wheat), method=k, cutoff=0.01)
view.expand(f"{wheat}")
feed = next(r for r in view.rows() if r.depth == 1 and r.kind == "process")
view.expand(feed.id)
market_rw = next(r for r in view.rows() if r.depth == 2 and r.kind == "process")
view.expand(market_rw.id)
view.rules = [
    GroupRule(name="transport", predicate={"field": "name", "op": "contains", "value": "transport"}),
    GroupRule(name="Europe", predicate={"field": "location", "op": "in", "value": ["FR", "DE", "PL", "UA", "RO", "GB", "ES", "IT", "HU"]},
              scope=market_rw.id),
]
print("=== Tree, transport grouped everywhere, European suppliers grouped under the RoW market ===")
show(view)

view.rules = [GroupRule(by="activity_type")]
print("=== Same tree, children grouped by activity type ===")
show(view)

# 2. Global grouping
groups = {
    "electricity": {"field": "reference_product", "op": "startswith", "value": "electricity"},
    "fertilisers": {"any": [
        {"field": "reference_product", "op": "contains", "value": "fertiliser"},
        {"field": "reference_product", "op": "regex", "value": r"^(urea|ammonium nitrate|ammonia|diammonium|monoammonium)"},
    ]},
    "transport": {"field": "name", "op": "contains", "value": "transport"},
    "machinery & diesel": {"any": [
        {"field": "reference_product", "op": "contains", "value": "diesel"},
        {"field": "name", "op": "contains", "value": "tillage"},
        {"field": "name", "op": "contains", "value": "combine harvesting"},
        {"field": "name", "op": "contains", "value": "sowing"},
        {"field": "name", "op": "contains", "value": "fertilising, by broadcaster"},
        {"field": "name", "op": "contains", "value": "application of plant protection"},
    ]},
}
for title, fn in [("direct attribution", direct_contribution), ("first-match cut", first_match_contribution)]:
    result = fn(model, wheat, groups)
    print(f"=== Global grouping, {title} ===")
    for name, v in result.items():
        print(f"  {name:20s} {v[k] / root:6.1%}")
    print(f"  {'sum':20s} {sum(v[k] for v in result.values()) / root:.6%}\n")
