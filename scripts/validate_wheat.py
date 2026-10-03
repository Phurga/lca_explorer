"""Phase 0 validation on the reference example: 1 kg market for wheat grain, feed | GLO.

Checks, against bw2calc:
1. root scores equal ``bw2calc.LCA`` scores for every method;
2. cumulative per-unit impacts ``G`` match bw2calc for random activities;
3. at every expanded node, children + direct = parent.
Prints timings and the first levels of the tree for climate change.

Run with BRIGHTWAY2_DIR pointing to data/brightway.
"""

import random
import time
from pathlib import Path

import bw2calc as bc
import bw2data as bd
import numpy as np

from lca_viewer.brightway import from_brightway
from lca_viewer.model import LCAModel
from lca_viewer.tree import Tree

PROJECT = "lca_viewer_ei391"
DATABASE = "ecoinvent-3.9.1-cutoff"
CACHE = Path("data/cache/ei391_cutoff")
CLIMATE = ("EF v3.1", "climate change", "global warming potential (GWP100)")

bd.projects.set_current(PROJECT)
methods = [m for m in bd.methods if m[0] == "EF v3.1"]
methods.append(("IPCC 2021", "climate change", "global warming potential (GWP100)"))

t = time.perf_counter()
if CACHE.exists():
    model = LCAModel.load(CACHE)
    print(f"loaded cached model in {time.perf_counter() - t:.1f}s")
else:
    model = from_brightway(PROJECT, DATABASE, methods)
    model.save(CACHE)
    print(f"built model from Brightway in {time.perf_counter() - t:.1f}s")
print(f"{model.size} activities, {model.B.shape[0]} flows, {len(model.methods)} methods, A nnz={model.A.nnz}")

t = time.perf_counter()
G = model.G
print(f"factorised + solved G for {len(model.methods)} methods in {time.perf_counter() - t:.2f}s")

acts = model.activities
wheat = acts.index[
    (acts.name == "market for wheat grain, feed") & (acts.location == "GLO")
][0]
k = model.method_index(CLIMATE)

# 1. Root scores vs bw2calc
node = bd.get_node(id=int(acts.id[wheat]))
lca = bc.LCA({node: 1}, method=methods[0])
lca.lci()
worst = 0.0
for i, m in enumerate(model.methods):
    lca.switch_method(m.name)
    lca.lcia()
    ours = model.score(wheat)[i]
    rel = abs(ours - lca.score) / max(abs(lca.score), 1e-30)
    worst = max(worst, rel)
print(f"[1] root scores vs bw2calc, worst relative difference over {len(model.methods)} methods: {worst:.2e}")

# 2. Random activities vs bw2calc (climate change)
random.seed(0)
worst = 0.0
for j in random.sample(range(model.size), 20):
    lca = bc.LCA({bd.get_node(id=int(acts.id[j])): 1}, method=CLIMATE)
    lca.lci()
    lca.lcia()
    rel = abs(G[j, k] - lca.score) / max(abs(lca.score), 1e-30)
    worst = max(worst, rel)
print(f"[2] G vs bw2calc on 20 random activities, worst relative difference: {worst:.2e}")

# 3. Closure, expanding everything above 0.1% of the root, up to depth 6
tree = Tree(model, wheat)
root_score = tree.root.impacts[k]
stack, expanded, worst = [tree.root], 0, 0.0
t = time.perf_counter()
while stack:
    n = stack.pop()
    children = tree.children(n)
    expanded += 1
    total = sum(c.impacts for c in children)
    worst = max(worst, float(np.max(np.abs(total - n.impacts) / np.maximum(np.abs(n.impacts), 1e-30))))
    stack.extend(
        c for c in children
        if not c.is_direct and c.depth < 6 and abs(c.impacts[k]) > 1e-3 * abs(root_score)
    )
print(
    f"[3] expanded {expanded} nodes in {time.perf_counter() - t:.2f}s, "
    f"worst children+direct vs parent relative difference: {worst:.2e}"
)


def label(n):
    if n.is_direct:
        return "(direct emissions)"
    a = acts.loc[n.product]
    return f"{n.amount:.3g} {a.unit} {a.reference_product} | {a['name']} | {a.location}"


def show(n, depth):
    share = n.impacts[k] / root_score
    print(f"{'  ' * n.depth}{share:7.1%}  {n.impacts[k]:.3g}  {label(n)}")
    if depth == 0 or n.is_direct:
        return
    children = sorted(tree.children(n), key=lambda c: -abs(c.impacts[k]))
    for c in children:
        if abs(c.impacts[k]) > 0.02 * abs(root_score):
            show(c, depth - 1)


print(f"\n{model.methods[k].name} [{model.methods[k].unit}], children > 2% shown")
show(tree.root, 3)
