import json, zipfile
from pathlib import Path
import bw2data as bd, bw2io as bi
from bw2io.importers.base_lcia import LCIAImporter

bd.projects.set_current("lca_viewer_ei391")
if "biosphere3" not in bd.databases:
    bi.create_default_biosphere3()
if not bd.methods:
    fp = Path(bi.__file__).parent / "data" / "lcia" / "lcia_39_ecoinvent.zip"
    with zipfile.ZipFile(fp) as archive:
        data = json.load(archive.open("data.json"))
    for m in data:
        m["name"] = tuple(m["name"])
        for exc in m["exchanges"]:
            if isinstance(exc.get("input"), list):
                exc["input"] = tuple(exc["input"])
    ei = LCIAImporter("lcia_39_ecoinvent.zip")
    ei.data = data
    ei.write_methods()
    bi.create_core_migrations()
if "ecoinvent-3.9.1-cutoff" not in bd.databases:
    imp = bi.SingleOutputEcospold2Importer("data/ei391_cutoff/datasets", "ecoinvent-3.9.1-cutoff", use_mp=True)
    imp.apply_strategies()
    imp.statistics()
    imp.write_database()
print(bd.databases, len(bd.methods))
