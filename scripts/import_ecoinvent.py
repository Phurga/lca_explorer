"""ecoinvent 3.9.1 cut-off into project lca_viewer_ei391 (set BRIGHTWAY2_DIR=data/brightway first).

Same as: lca-viewer import ecospold2 data/ei391_cutoff/datasets --project lca_viewer_ei391 --database ecoinvent-3.9.1-cutoff
"""
import bw2data as bd

from lca_viewer.importing import import_ecospold2, setup_project

setup_project("lca_viewer_ei391")
if "ecoinvent-3.9.1-cutoff" not in bd.databases:
    import_ecospold2("lca_viewer_ei391", "data/ei391_cutoff/datasets", "ecoinvent-3.9.1-cutoff")
print(bd.databases, len(bd.methods))
