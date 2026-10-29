"""Datasets written from the Create dataset panel, and project management (needs Brightway)."""

import math

import numpy as np
import pytest

from lca_viewer import editing, sources

GWP = ("toy", "gwp")


@pytest.fixture
def project(tmp_path):
    """A Brightway folder with one project: a biosphere, a background database and a method."""
    pytest.importorskip("multifunctional")
    import bw2data as bd
    from bw2data.tests import _close_sqlite_handles

    bd.config.dont_warn = True
    bd.config.is_test = True
    _close_sqlite_handles()
    bd.projects.change_base_directories(base_dir=tmp_path, base_logs_dir=tmp_path, project_name="toy", update=False)
    bd.projects._is_temp_dir = True
    bd.Database("bio").write({
        ("bio", "co2"): {"name": "Carbon dioxide, fossil", "categories": ("air",), "unit": "kilogram", "type": "emission"},
        ("bio", "water"): {"name": "Water, river", "categories": ("natural resource", "in water"),
                           "unit": "cubic meter", "type": "natural resource"},
    })

    def process(code, name, co2, production=1.0):
        return ("bg", code), {"name": name, "reference product": name, "location": "FR", "unit": "kilogram",
                              "type": "process", "exchanges": [
                                  {"input": ("bg", code), "amount": production, "type": "production"},
                                  {"input": ("bio", "co2"), "amount": co2, "type": "biosphere"}]}

    bd.Database("bg").write(dict([process("elec", "electricity", 0.1), process("feed", "feed", 0.5),
                                  process("manure", "manure treatment", 0.2, production=-1.0)]))
    bd.Method(GWP).register(unit="kg CO2-eq")
    bd.Method(GWP).write([(("bio", "co2"), 1.0)])
    yield tmp_path
    _close_sqlite_handles()


def spec(**changes):
    out = {
        "database": "my datasets", "new_database": True,
        "process": {"name": "dairy farm", "location": "FR", "comment": "Toy farm",
                    "documentation": {"technology": "Grass-based", "time period": "2020-2024"}},
        "products": [{"name": "milk", "amount": 10, "unit": "kilogram", "allocation": 80, "comment": "raw milk"},
                     {"name": "meat", "amount": 2, "unit": "kilogram", "allocation": 20}],
        "exchanges": [
            {"section": "technosphere", "database": "bg", "code": "elec", "amount": 5, "comment": "milking", "sd2": 1.21},
            {"section": "technosphere", "database": "bg", "code": "feed", "amount": 4},
            {"section": "waste", "database": "bg", "code": "manure", "amount": 3, "comment": "slurry"},
            {"section": "emission", "database": "bio", "code": "co2", "amount": 3},
            {"section": "resource", "database": "bio", "code": "water", "amount": 0.2},
        ],
    }
    out.update(changes)
    return out


def test_dataset_with_coproducts_is_allocated_and_builds(project):
    import bw2data as bd

    from lca_viewer.brightway import from_brightway

    result = editing.write_dataset("toy", spec())
    assert result["database"] == "my datasets" and len(result["codes"]) == 2
    db = bd.Database("my datasets")
    assert db.backend == "multifunctional"
    parent = next(n for n in db if n["type"] == "multifunctional")
    assert "Technology: Grass-based" in parent["comment"] and parent["documentation"]["time period"] == "2020-2024"
    elec = next(e for e in parent.technosphere() if e.input["name"] == "electricity")
    assert elec["comment"] == "milking" and elec["uncertainty type"] == 2
    assert math.isclose(elec["scale"], 0.5 * math.log(1.21))
    manure = next(e for e in parent.technosphere() if e.input["name"] == "manure treatment")
    assert manure["amount"] == -3  # waste to treatment: negative, as in ecoinvent

    # Each allocated process carries its share of every input and emission
    model = from_brightway("toy", "my datasets", [GWP])
    acts = model.activities
    shared = 5 * 0.1 + 4 * 0.5 + 3 * 0.2 + 3  # electricity, feed, manure treatment, direct CO2
    milk = acts.index[(acts["database"] == "my datasets") & (acts["reference_product"] == "milk")][0]
    meat = acts.index[(acts["database"] == "my datasets") & (acts["reference_product"] == "meat")][0]
    assert np.isclose(model.score(milk)[0], 0.8 * shared / 10)
    assert np.isclose(model.score(meat)[0], 0.2 * shared / 2)


def test_single_product_dataset_in_an_existing_database(project):
    from lca_viewer.brightway import from_brightway

    editing.write_dataset("toy", spec(products=[{"name": "milk", "amount": 1, "unit": "kilogram"}]))
    second = spec(new_database=False, process={"name": "cheese making", "location": "FR"},
                  products=[{"name": "cheese", "amount": 1, "unit": "kilogram"}],
                  exchanges=[{"section": "technosphere", "database": "bg", "code": "elec", "amount": 2},
                             {"section": "avoided", "database": "bg", "code": "feed", "amount": 1}])
    editing.write_dataset("toy", second)
    model = from_brightway("toy", "my datasets", [GWP])
    cheese = model.activities.index[model.activities["name"] == "cheese making"][0]
    assert np.isclose(model.score(cheese)[0], 2 * 0.1 - 1 * 0.5)  # avoided feed is credited


def test_dataset_errors(project):
    with pytest.raises(ValueError, match="add up to 100"):
        editing.write_dataset("toy", spec(products=[{"name": "a", "amount": 1, "allocation": 50},
                                                    {"name": "b", "amount": 1, "allocation": 40}]))
    with pytest.raises(ValueError, match="multifunctional backend"):
        editing.write_dataset("toy", spec(database="bg", new_database=False))
    with pytest.raises(ValueError, match="SD²"):
        editing.write_dataset("toy", spec(exchanges=[{"section": "technosphere", "database": "bg", "code": "elec",
                                                      "amount": 1, "sd2": 0.5}]))


def as_spec(read: dict, **changes) -> dict:
    """What the page sends back for a dataset it read (:func:`sources.read_dataset`), with ``changes``."""
    out = {
        "database": read["database"], "new_database": False, "code": read["code"], "process": read["process"],
        "products": [{k: v for k, v in p.items() if k != "used"} for p in read["products"]],
        "exchanges": [{"section": e["section"], "database": e["node"]["database"], "code": e["node"]["code"],
                       "amount": e["amount"], "comment": e["comment"], "sd2": e["sd2"], "id": e["id"]} for e in read["exchanges"]],
    }
    out.update(changes)
    return out


def as_new(read: dict, **changes) -> dict:
    """The same, as a new dataset (a copy): no code, no product codes."""
    out = as_spec(read, code=None, **changes)
    out["products"] = [{k: v for k, v in p.items() if k != "code"} for p in out["products"]]
    return out


def without_ids(read: dict) -> dict:
    """A dataset as read, without the ids of its exchanges: they change when a multi-output process is allocated again."""
    return {k: ([{i: x for i, x in e.items() if i != "id"} for e in v] if k in ("products", "exchanges") else v)
            for k, v in read.items()}


def scores(database: str) -> dict:
    """Impact (GWP) per unit of product of each process of a database, from a model built the way the app builds it."""
    from lca_viewer.brightway import from_brightway

    model = from_brightway("toy", database, [GWP])
    acts = model.activities
    return {acts["reference_product"][i]: float(model.score(i)[0]) for i in acts.index[acts["database"] == database]}


def products_of(read: dict, *names) -> list[dict]:
    return [p for n in names for p in as_spec(read)["products"] if p["name"] == n]


@pytest.fixture
def farm(project):
    """Two linked datasets: a dairy farm (milk, meat) and a cheese dairy that takes 2 kg of its milk."""
    first = editing.write_dataset("toy", spec())
    milk = next(c for c in first["codes"]
                if sources.read_dataset(project, "toy", "my datasets", c)["products"][0]["name"] == "milk")
    cheese = editing.write_dataset("toy", {
        "database": "downstream", "new_database": True, "process": {"name": "cheese dairy", "location": "FR"},
        "products": [{"name": "cheese", "amount": 1, "unit": "kilogram"}],
        "exchanges": [{"section": "technosphere", "database": "my datasets", "code": milk, "amount": 2},
                      {"section": "technosphere", "database": "bg", "code": "elec", "amount": 1}]})
    return {"farm": first["code"], "milk": milk, "cheese": cheese["code"]}


FARM_SHARED = 5 * 0.1 + 4 * 0.5 + 3 * 0.2 + 3  # electricity, feed, manure treatment, direct CO2 of spec()
CHEESE = 2 * 0.8 * FARM_SHARED / 10 + 0.1  # 2 kg of milk and 1 kg of electricity


def test_a_dataset_is_read_back_as_the_form_takes_it(project, farm):
    read = sources.read_dataset(project, "toy", "my datasets", farm["milk"])  # an allocated process: the dataset is its parent's
    assert read["code"] == farm["farm"] and read["editable"] and read["multifunctional"]
    assert read["process"]["comment"] == "Toy farm" and read["process"]["documentation"]["technology"] == "Grass-based"
    assert [(p["name"], p["amount"], p["allocation"], p["used"]) for p in read["products"]] == [
        ("milk", 10, 80, True), ("meat", 2, 20, False)]
    sections = {(e["section"], e["node"]["name"]): e for e in read["exchanges"]}
    assert set(sections) == {("technosphere", "electricity"), ("technosphere", "feed"), ("waste", "manure treatment"),
                             ("emission", "Carbon dioxide, fossil"), ("resource", "Water, river")}
    assert sections[("waste", "manure treatment")]["amount"] == 3 and sections[("technosphere", "electricity")]["sd2"] == 1.21
    assert [u["name"] for u in read["used_by"]] == ["cheese dairy"]
    background = sources.read_dataset(project, "toy", "bg", "elec")  # not written by the app: it can be edited too
    assert background["editable"] and sorted(u["name"] for u in background["used_by"]) == ["cheese dairy", "dairy farm"]
    assert [(p["name"], p["amount"]) for p in background["products"]] == [("electricity", 1)]
    assert background["exchanges"][0]["section"] == "emission"
    assert sources.read_dataset(project, "toy", "bg", "manure")["products"][0]["amount"] == -1
    with pytest.raises(ValueError, match="not a process"):
        sources.read_dataset(project, "toy", "bio", "co2")


def test_a_copy_is_a_new_dataset_with_the_same_results(project, farm):
    read = sources.read_dataset(project, "toy", "my datasets", farm["farm"])
    editing.write_dataset("toy", as_new(read, new_database=True, database="copies",
                                        process={**read["process"], "name": "dairy farm (copy)"}))
    assert scores("copies") == pytest.approx({"milk": 0.8 * FARM_SHARED / 10, "meat": 0.2 * FARM_SHARED / 2})
    assert scores("my datasets") == pytest.approx(scores("copies"))  # the original is as it was
    # a process of a background database as a template: its emissions come along
    template = sources.read_dataset(project, "toy", "bg", "feed")
    editing.write_dataset("toy", as_new(template, database="copies", process={**template["process"], "name": "my feed"}))
    assert scores("copies")["feed"] == pytest.approx(0.5)


def test_editing_a_dataset_changes_the_results_of_what_uses_it(project, farm):
    assert scores("downstream")["cheese"] == pytest.approx(CHEESE)
    read = sources.read_dataset(project, "toy", "my datasets", farm["farm"])
    spec_ = as_spec(read)
    for e in spec_["exchanges"]:
        if e["code"] == "feed":
            e["amount"] = 6
    for p in spec_["products"]:
        if p["name"] == "milk":
            p["name"] = "whole milk"
    spec_["process"] = {**read["process"], "name": "dairy farm, grass"}
    result = editing.write_dataset("toy", spec_)
    assert result["code"] == farm["farm"] and len(result["codes"]) == 2
    shared = 5 * 0.1 + 6 * 0.5 + 3 * 0.2 + 3
    assert scores("my datasets") == pytest.approx({"whole milk": 0.8 * shared / 10, "meat": 0.2 * shared / 2})
    # the cheese dairy is still linked to the milk, and takes the new results
    assert scores("downstream")["cheese"] == pytest.approx(2 * 0.8 * shared / 10 + 0.1)
    again = sources.read_dataset(project, "toy", "my datasets", farm["farm"])
    assert again["process"]["name"] == "dairy farm, grass" and again["process"]["comment"] == "Toy farm"
    assert [u["database"] for u in again["used_by"]] == ["downstream"]
    assert {p["code"] for p in again["products"]} == {p["code"] for p in read["products"]}


def test_a_failed_edit_leaves_the_dataset_as_it_was(project, farm):
    before = sources.read_dataset(project, "toy", "my datasets", farm["farm"])
    exchanges = as_spec(before)["exchanges"] + [{"section": "technosphere", "database": "bg", "code": "nothing", "amount": 1}]
    with pytest.raises(Exception):
        editing.write_dataset("toy", as_spec(before, exchanges=exchanges, process={**before["process"], "name": "changed"}))
    assert without_ids(sources.read_dataset(project, "toy", "my datasets", farm["farm"])) == without_ids(before)
    assert scores("downstream")["cheese"] == pytest.approx(CHEESE)
    with pytest.raises(ValueError, match="No process"):
        editing.write_dataset("toy", spec(database="bg", new_database=False, code="nothing", products=[{"name": "x", "amount": 1}]))


def test_co_products_can_be_added_to_a_process_that_is_used(project, farm):
    """A third dataset uses the cheese dairy, which has one product; then the cheese dairy gets a co-product."""
    cheese = sources.read_dataset(project, "toy", "downstream", farm["cheese"])
    assert not cheese["multifunctional"] and cheese["products"][0]["code"] == farm["cheese"]
    editing.write_dataset("toy", {
        "database": "downstream", "process": {"name": "pasta bake", "location": "FR"},
        "products": [{"name": "pasta bake", "amount": 1, "unit": "kilogram"}],
        "exchanges": [{"section": "technosphere", "database": "downstream", "code": farm["cheese"], "amount": 0.5}]})
    before = scores("downstream")
    assert before["pasta bake"] == pytest.approx(0.5 * CHEESE)

    whey = {"name": "whey", "amount": 4, "unit": "kilogram", "allocation": 10, "comment": "liquid"}
    result = editing.write_dataset("toy", as_spec(cheese, products=[{**products_of(cheese, "cheese")[0], "allocation": 90}, whey]))
    assert len(result["codes"]) == 2  # the process becomes a multi-output one
    after = scores("downstream")
    assert after["cheese"] == pytest.approx(0.9 * CHEESE) and after["whey"] == pytest.approx(0.1 * CHEESE / 4)
    assert after["pasta bake"] == pytest.approx(0.5 * after["cheese"])  # still linked to the cheese

    read = sources.read_dataset(project, "toy", "downstream", result["code"])
    assert read["multifunctional"] and [(p["name"], p["used"]) for p in read["products"]] == [("cheese", True), ("whey", False)]
    assert [u["name"] for u in read["used_by"]] == ["pasta bake"]
    # the old code of the process is now the cheese product's: it still leads to the dataset
    assert sources.read_dataset(project, "toy", "downstream", farm["cheese"])["code"] == result["code"]

    # co-products taken away: one that is used stays, one that nobody uses goes, down to a single product
    with pytest.raises(ValueError, match=r"'cheese'.* is used by pasta bake"):
        editing.write_dataset("toy", as_spec(read, products=[{**p, "allocation": 100} for p in products_of(read, "whey")]))
    assert scores("downstream") == pytest.approx(after)  # nothing changed
    alone = editing.write_dataset("toy", as_spec(read, products=[{**p, "allocation": 100} for p in products_of(read, "cheese")]))
    assert len(alone["codes"]) == 1
    final = scores("downstream")
    assert "whey" not in final and final["cheese"] == pytest.approx(CHEESE) and final["pasta bake"] == pytest.approx(0.5 * CHEESE)
    # a process with one product (that is not its own) can get co-products again, and be edited like the others
    assert sources.read_dataset(project, "toy", "downstream", alone["codes"][0])["products"][0]["name"] == "cheese"
    more = sources.read_dataset(project, "toy", "downstream", alone["code"])
    editing.write_dataset("toy", as_spec(more, products=[{**products_of(more, "cheese")[0], "allocation": 50},
                                                         {"name": "curd", "amount": 1, "unit": "kilogram", "allocation": 50}]))
    assert scores("downstream")["pasta bake"] == pytest.approx(0.5 * 0.5 * CHEESE)


def test_products_nothing_uses_can_be_taken_away_and_added(project, farm):
    read = sources.read_dataset(project, "toy", "my datasets", farm["farm"])
    editing.write_dataset("toy", as_spec(read, products=[{**products_of(read, "milk")[0], "allocation": 100}]))
    assert scores("my datasets") == pytest.approx({"milk": FARM_SHARED / 10})
    assert scores("downstream")["cheese"] == pytest.approx(2 * FARM_SHARED / 10 + 0.1)
    again = sources.read_dataset(project, "toy", "my datasets", farm["farm"])
    editing.write_dataset("toy", as_spec(again, products=[{**products_of(again, "milk")[0], "allocation": 50},
                                                          {"name": "manure", "amount": 1, "unit": "kilogram", "allocation": 50}]))
    assert scores("my datasets") == pytest.approx({"milk": 0.5 * FARM_SHARED / 10, "manure": 0.5 * FARM_SHARED})
    assert scores("downstream")["cheese"] == pytest.approx(2 * 0.5 * FARM_SHARED / 10 + 0.1)


def test_deleting_a_dataset(project, farm):
    import bw2data as bd

    with pytest.raises(ValueError, match="dairy farm is used by cheese dairy"):
        editing.delete_dataset("toy", "my datasets", farm["farm"])  # the cheese dairy uses its milk
    assert scores("downstream")["cheese"] == pytest.approx(CHEESE)
    with pytest.raises(ValueError, match="electricity is used by"):  # any dataset can be deleted, if nothing uses it
        editing.delete_dataset("toy", "bg", "elec")

    pasta = editing.write_dataset("toy", {
        "database": "downstream", "process": {"name": "pasta bake", "location": "FR"},
        "products": [{"name": "pasta bake", "amount": 1, "unit": "kilogram"}],
        "exchanges": [{"section": "technosphere", "database": "downstream", "code": farm["cheese"], "amount": 0.5}]})
    with pytest.raises(ValueError, match="cheese dairy is used by pasta bake"):
        editing.delete_dataset("toy", "downstream", farm["cheese"])
    done = editing.delete_dataset("toy", "downstream", pasta["code"])  # nothing uses it
    assert done["deleted"] == "pasta bake" and scores("downstream") == pytest.approx({"cheese": CHEESE})
    assert scores("my datasets")["milk"] == pytest.approx(0.8 * FARM_SHARED / 10)

    editing.delete_dataset("toy", "downstream", farm["cheese"])
    done = editing.delete_dataset("toy", "my datasets", farm["milk"])  # now the farm can go, asked from one of its processes
    assert done["deleted"] == "dairy farm"
    assert not [n for n in bd.Database("my datasets")] and not [n for n in bd.Database("downstream")]  # products and allocated processes too


def test_staged_changes_are_applied_together_or_not_at_all(project, farm):
    import bw2data as bd

    read = sources.read_dataset(project, "toy", "my datasets", farm["farm"])
    edit = as_spec(read)
    next(e for e in edit["exchanges"] if e["code"] == "feed")["amount"] = 6
    created = {"database": "scratch", "new_database": True, "process": {"name": "compost", "location": "FR"},
               "products": [{"name": "compost", "amount": 1, "unit": "kilogram"}],
               "exchanges": [{"section": "emission", "database": "bio", "code": "co2", "amount": 0.2}]}
    more = {**created, "process": {"name": "compost bis"}}  # the same new database, made by the change before
    changes = [{"kind": "edit", "title": "edit farm", "spec": edit},
               {"kind": "create", "title": "new compost", "spec": created},
               {"kind": "create", "title": "another compost", "spec": more},
               {"kind": "delete", "title": "delete cheese dairy", "database": "downstream", "code": farm["cheese"]},
               {"kind": "delete", "title": "delete the farm", "database": "my datasets", "code": farm["farm"]}]
    # The cheese dairy goes before the farm it uses: in this order all of it works; in the other, nothing is done
    wrong = [changes[0], changes[4], changes[3]]
    with pytest.raises(ValueError, match="delete the farm: dairy farm is used by cheese dairy"):
        editing.apply_changes("toy", wrong)
    assert without_ids(sources.read_dataset(project, "toy", "my datasets", farm["farm"])) == without_ids(read)
    assert scores("downstream")["cheese"] == pytest.approx(CHEESE)

    with pytest.raises(ValueError, match="another compost"):  # fails last: the database made for the first one goes too
        editing.apply_changes("toy", [changes[0], changes[1], {**changes[2], "spec": {**more, "products": []}}])
    assert "scratch" not in bd.databases
    assert without_ids(sources.read_dataset(project, "toy", "my datasets", farm["farm"])) == without_ids(read)

    result = editing.apply_changes("toy", changes[:3])
    assert result["applied"] == 3 and result["databases"] == ["my datasets", "scratch"]
    shared = 5 * 0.1 + 6 * 0.5 + 3 * 0.2 + 3
    assert scores("my datasets")["milk"] == pytest.approx(0.8 * shared / 10)
    assert scores("downstream")["cheese"] == pytest.approx(2 * 0.8 * shared / 10 + 0.1)  # one build sees the whole batch
    assert scores("scratch") == pytest.approx({"compost": 0.2})  # both composts are in the one new database
    assert len([n for n in bd.Database("scratch")]) == 2

    editing.apply_changes("toy", changes[3:])
    assert not [n for n in bd.Database("my datasets")] and not [n for n in bd.Database("downstream")]


def test_any_process_can_be_edited_and_what_the_form_cannot_show_stays(project, farm):
    """An imported process (no mark of the app): its exchanges are changed one by one, by id."""
    import bw2data as bd

    pedigree = {"reliability": 1, "completeness": 2, "temporal correlation": 3, "geographical correlation": 1, "further technological correlation": 4}
    co2 = next(iter(bd.get_node(database="bg", code="elec").biosphere()))
    for k, v in {"uncertainty type": 2, "loc": math.log(0.1), "scale": 0.2, "pedigree": pedigree, "formula": "0.1 * x"}.items():
        co2[k] = v
    co2.save()

    read = sources.read_dataset(project, "toy", "bg", "elec")
    assert read["editable"] and sorted(u["name"] for u in read["used_by"]) == ["cheese dairy", "dairy farm"]
    assert read["exchanges"][0]["sd2"] == pytest.approx(math.exp(0.4), abs=1e-6)
    edited = as_spec(read, process={**read["process"], "name": "electricity, edited"})
    edited["exchanges"][0]["amount"] = 0.3
    edited["exchanges"].append({"section": "emission", "database": "bio", "code": "co2", "amount": 0.05, "comment": "new"})
    result = editing.write_dataset("toy", edited)
    assert result["code"] == "elec" and result["codes"] == ["elec"]

    node = bd.get_node(database="bg", code="elec")
    assert node["name"] == "electricity, edited" and node["lca_viewer"]["modified"]
    first = next(e for e in node.biosphere() if e["amount"] == 0.3)
    assert first["scale"] == 0.2 and first["pedigree"] == pedigree and first["formula"] == "0.1 * x"  # not shown by the form: kept
    assert first["loc"] == pytest.approx(math.log(0.3)) and first["uncertainty type"] == 2  # follows the amount
    assert sorted(e["amount"] for e in node.biosphere()) == [0.05, 0.3]
    # What uses it is still linked and follows: the farm takes 5 kWh of it
    assert scores("bg")["electricity"] == pytest.approx(0.35)
    shared = 5 * 0.35 + 4 * 0.5 + 3 * 0.2 + 3
    assert scores("my datasets")["milk"] == pytest.approx(0.8 * shared / 10)
    assert scores("downstream")["cheese"] == pytest.approx(2 * 0.8 * shared / 10 + 0.35)

    # An exchange left out of the form is deleted; a lognormal uncertainty can be taken away
    again = sources.read_dataset(project, "toy", "bg", "elec")
    spec_ = as_spec(again)
    spec_["exchanges"] = [dict(e, sd2=None) for e in spec_["exchanges"] if e["amount"] == 0.3]
    editing.write_dataset("toy", spec_)
    left = list(bd.get_node(database="bg", code="elec").biosphere())
    assert [e["amount"] for e in left] == [0.3] and left[0].get("uncertainty type", 0) == 0 and "pedigree" not in left[0]._data
    # Used by others: it cannot be deleted
    with pytest.raises(ValueError, match="electricity, edited is used by"):
        editing.delete_dataset("toy", "bg", "elec")


def test_a_form_without_ids_rewrites_the_exchanges(project, farm):
    """A change staged by an older version has no exchange ids: its exchanges are written again from the form."""
    read = sources.read_dataset(project, "toy", "my datasets", farm["farm"])
    spec_ = as_spec(read)
    for e in spec_["exchanges"]:
        del e["id"]
        if e["code"] == "feed":
            e["amount"] = 6
    editing.write_dataset("toy", spec_)
    shared = 5 * 0.1 + 6 * 0.5 + 3 * 0.2 + 3
    assert scores("my datasets")["milk"] == pytest.approx(0.8 * shared / 10)
    assert len(sources.read_dataset(project, "toy", "my datasets", farm["farm"])["exchanges"]) == len(read["exchanges"])


def test_databases_that_use_an_edited_one_are_affected(project, farm):
    projects = sources.brightway_projects(project)
    toy = next(p for p in projects if p["name"] == "toy")
    assert sources.affected_databases(toy, ["bg"]) == ["bg", "downstream", "my datasets"]
    assert sources.affected_databases(toy, ["downstream"]) == ["downstream"]


def test_search_reads_the_project_files(project):
    found = sources.search_nodes(project, "toy", "electricity fr")
    assert [(n["name"], n["unit"]) for n in found] == [("electricity", "kilogram")]
    assert [n["name"] for n in sources.search_nodes(project, "toy", "carbon air", "emission")] == ["Carbon dioxide, fossil"]
    assert [n["name"] for n in sources.search_nodes(project, "toy", "water", "resource")] == ["Water, river"]
    assert sources.search_nodes(project, "toy", "water", "emission") == []


def test_create_and_delete_projects(project):
    import bw2data as bd

    editing.create_project("second")
    assert "second" in bd.projects
    with pytest.raises(SystemExit):
        editing.create_project("second")
    editing.delete_project("second")
    assert "second" not in bd.projects and [p["name"] for p in sources.brightway_projects(project)] == ["toy"]
    with pytest.raises(SystemExit, match="at least one project"):
        editing.delete_project("toy")


def test_through_the_api(project, tmp_path):
    """The page's requests: changes run as ``lca-viewer`` in a separate process on the folder."""
    import time

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    def wait(job):
        for _ in range(600):
            if job["status"] != "running":
                return job
            time.sleep(0.1)
            job = client.get(f"/api/jobs/{job['id']}").json()
        raise AssertionError("job still running")

    client = TestClient(create_library(tmp_path / "data"))
    folder = str(project)
    found = client.get("/api/brightway/search", params={"folder": folder, "project": "toy", "q": "feed"}).json()
    assert [n["code"] for n in found] == ["feed"]

    job = wait(client.post("/api/brightway/dataset", json={"folder": folder, "project": "toy", "spec": spec()}).json())
    assert job["status"] == "done", job
    assert job["result"]["database"] == "my datasets" and len(job["result"]["codes"]) == 2
    # bw2data adds a "default" project to a folder that has none, when imported
    projects = client.get("/api/brightway", params={"folder": folder}).json()["projects"]
    listed = next(p for p in projects if p["name"] == "toy")["databases"]
    assert ("my datasets", 2) in [(d["name"], d["processes"]) for d in listed]  # the two allocated processes
    failed = wait(client.post("/api/brightway/dataset", json={"folder": folder, "project": "toy",
                                                             "spec": spec(products=[])}).json())
    assert failed["status"] == "failed" and "name and an amount" in failed["error"]

    assert wait(client.post("/api/brightway/projects", json={"folder": folder, "name": "second"}).json())["status"] == "done"
    assert client.post("/api/brightway/projects", json={"folder": folder, "name": "second"}).status_code == 400
    assert client.post("/api/brightway/projects/delete",
                       json={"folder": folder, "project": "second", "confirm": "sec"}).status_code == 400
    job = wait(client.post("/api/brightway/projects/delete",
                           json={"folder": folder, "project": "second", "confirm": "second"}).json())
    assert job["status"] == "done" and "second" not in [p["name"] for p in sources.brightway_projects(project)]

    uploaded = client.post("/api/upload", params={"name": "my export.CSV"}, content=b"{CSV}").json()
    assert uploaded["size"] == 5 and uploaded["path"].endswith("my-export.CSV")
    assert client.post("/api/brightway/import", json={"folder": folder, "project": "toy", "format": "simapro-csv",
                                                      "path": str(tmp_path / "nothing.csv"), "database": "x"}).status_code == 404
    adapters = client.get("/api/adapters", params={"link_to": ["ecoinvent-3.9.1-cutoff"]}).json()
    assert adapters["default"] == ["simapro-ecoinvent-3.9.1-cutoff"]


def test_edit_copy_and_delete_through_the_api(project, tmp_path):
    """The page's flow: read a dataset, write it again in place, rebuild the open model, delete what nothing uses."""
    import time

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    def wait(job):
        for _ in range(900):
            if job["status"] != "running":
                return job
            time.sleep(0.1)
            job = client.get(f"/api/jobs/{job['id']}").json()
        raise AssertionError("job still running")

    client = TestClient(create_library(tmp_path / "data"))
    folder, where = str(project), {"folder": str(project), "project": "toy"}
    first = wait(client.post("/api/brightway/dataset", json={**where, "spec": spec(products=[{"name": "milk", "amount": 1, "unit": "kilogram"}])}).json())
    assert first["status"] == "done", first
    code = first["result"]["code"]

    read = client.get("/api/brightway/dataset", params={**where, "database": "my datasets", "code": code}).json()
    assert read["editable"] and read["products"][0]["name"] == "milk" and read["used_by"] == []
    assert client.get("/api/brightway/dataset", params={**where, "database": "my datasets", "code": "nothing"}).status_code == 404
    assert client.get("/api/brightway/dataset", params={**where, "database": "bg", "code": "elec"}).json()["editable"] is True

    opened = wait(client.post("/api/load", json={"brightway": folder, "project": "toy", "database": "my datasets"}).json())
    assert opened["status"] == "done", opened
    key = opened["key"]
    listed = client.get("/api/databases").json()
    assert listed[0]["brightway"] == {"folder": folder, "project": "toy", "database": "my datasets"}
    def impact(product):
        """GWP per unit of a product of "my datasets", from the model served under ``key``."""
        found = client.post(f"/db/{key}/api/search", json={"predicate": {"all": [
            {"field": "reference_product", "op": "eq", "value": product}, {"field": "database", "op": "eq", "value": "my datasets"}]}}).json()
        assert found["total"] == 1, found
        index = found["results"][0]["index"]
        row = client.post(f"/db/{key}/api/rows", json={"root": [index], "method": 0}).json()["rows"][0]
        assert row["meta"]["code"] in (code, *(c for c in saved_codes))
        return row["impacts"][0]

    saved_codes = []
    assert impact("milk") == pytest.approx(5 * 0.1 + 4 * 0.5 + 3 * 0.2 + 3)

    # Edit: one more input, a co-product; the model that is open is not replaced unless asked
    edited = as_spec(read)
    next(e for e in edited["exchanges"] if e["code"] == "feed")["amount"] = 6
    edited["products"] = [{**edited["products"][0], "allocation": 60},
                          {"name": "buttermilk", "amount": 2, "unit": "kilogram", "allocation": 40}]
    saved = wait(client.post("/api/brightway/dataset", json={**where, "spec": edited}).json())
    assert saved["status"] == "done", saved
    assert len(saved["result"]["codes"]) == 2
    refused = wait(client.post("/api/load", json={"brightway": folder, "project": "toy", "database": "my datasets", "rebuild": True}).json())
    assert refused["status"] == "failed" and "is open" in refused["error"]
    again = wait(client.post("/api/load", json={"brightway": folder, "project": "toy", "database": "my datasets",
                                                "rebuild": True, "reopen": True}).json())
    assert again["status"] == "done" and again["key"] == key, again
    saved_codes.extend(saved["result"]["codes"])
    shared = 5 * 0.1 + 6 * 0.5 + 3 * 0.2 + 3
    assert impact("milk") == pytest.approx(0.6 * shared) and impact("buttermilk") == pytest.approx(0.4 * shared / 2)

    # Delete: what is used is refused (and said so), the rest goes
    user = wait(client.post("/api/brightway/dataset", json={**where, "spec": {
        "database": "downstream", "new_database": True, "process": {"name": "cheese dairy"},
        "products": [{"name": "cheese", "amount": 1, "unit": "kilogram"}],
        "exchanges": [{"section": "technosphere", "database": "my datasets", "code": saved["result"]["codes"][0], "amount": 1}]}}).json())
    assert user["status"] == "done", user
    blocked = wait(client.post("/api/brightway/dataset/delete", json={**where, "database": "my datasets", "code": code}).json())
    assert blocked["status"] == "failed" and "is used by cheese dairy" in blocked["error"]
    assert client.post("/api/brightway/dataset/delete", json={**where, "database": "my datasets", "code": "nothing"}).status_code == 404
    gone = wait(client.post("/api/brightway/dataset/delete", json={**where, "database": "downstream", "code": user["result"]["code"]}).json())
    assert gone["status"] == "done" and gone["result"]["deleted"] == "cheese dairy"
    gone = wait(client.post("/api/brightway/dataset/delete", json={**where, "database": "my datasets", "code": code}).json())
    assert gone["status"] == "done", gone


def test_staged_changes_through_the_api(project, tmp_path):
    """Changes are staged (nothing is written), then made together and the open model replaced by a rebuilt one."""
    import time

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    def wait(job):
        for _ in range(900):
            if job["status"] != "running":
                return job
            time.sleep(0.1)
            job = client.get(f"/api/jobs/{job['id']}").json()
        raise AssertionError("job still running")

    client = TestClient(create_library(tmp_path / "data"))
    folder, where = str(project), {"folder": str(project), "project": "toy"}
    first = wait(client.post("/api/brightway/dataset", json={**where, "spec": spec()}).json())
    assert first["status"] == "done", first
    key = wait(client.post("/api/load", json={"brightway": folder, "project": "toy", "database": "my datasets"}).json())["key"]

    read = client.get("/api/brightway/dataset", params={**where, "database": "my datasets", "code": first["result"]["code"]}).json()
    edited = as_spec(read)
    next(e for e in edited["exchanges"] if e["code"] == "feed")["amount"] = 6
    change = {"kind": "edit", "title": "Edit dairy farm", "database": "my datasets", "code": read["code"], "spec": edited,
              "form": {"anything": "the page needs"}}
    assert client.get("/api/brightway/pending", params=where).json()["changes"] == []
    staged = client.post("/api/brightway/pending", json={**where, "change": change}).json()["changes"]
    assert len(staged) == 1 and staged[0]["id"] and staged[0]["form"] == {"anything": "the page needs"}
    # Nothing is written: the open model and the dataset are as they were
    unchanged = sources.read_dataset(project, "toy", "my datasets", read["code"])
    assert next(e for e in unchanged["exchanges"] if e["node"]["code"] == "feed")["amount"] == 4
    # The same dataset staged again takes the place of the first change; a wrong one is refused
    edited["process"] = {**edited["process"], "name": "dairy farm v2"}
    again = client.post("/api/brightway/pending", json={**where, "change": {**change, "spec": edited}}).json()["changes"]
    assert [c["spec"]["process"]["name"] for c in again] == ["dairy farm v2"]
    bad = client.post("/api/brightway/pending", json={**where, "change": {**change, "spec": {**edited, "products": []}}})
    assert bad.status_code == 400 and "name and an amount" in bad.json()["detail"]
    assert client.post("/api/brightway/pending", json={**where, "change": {"kind": "other"}}).status_code == 400

    new = {"kind": "create", "title": "Create compost", "spec": {
        "database": "my datasets", "process": {"name": "compost"}, "products": [{"name": "compost", "amount": 1, "unit": "kilogram"}],
        "exchanges": [{"section": "emission", "database": "bio", "code": "co2", "amount": 0.2}]}}
    ids = [c["id"] for c in client.post("/api/brightway/pending", json={**where, "change": new}).json()["changes"]]
    assert len(ids) == 2
    left = client.post("/api/brightway/pending/remove", json={**where, "id": ids[1]}).json()["changes"]
    assert [c["id"] for c in left] == ids[:1]
    client.post("/api/brightway/pending", json={**where, "change": new})

    job = wait(client.post("/api/brightway/apply", json={**where, "database": "my datasets"}).json())
    assert job["status"] == "done" and job["applied"] == 2 and job["key"] == key, job
    assert client.get("/api/brightway/pending", params=where).json()["changes"] == []
    assert client.post("/api/brightway/apply", json=where).status_code == 400  # nothing staged any more
    shared = 5 * 0.1 + 6 * 0.5 + 3 * 0.2 + 3
    found = client.post(f"/db/{key}/api/search", json={"predicate": {"all": [
        {"field": "reference_product", "op": "eq", "value": "milk"}, {"field": "database", "op": "eq", "value": "my datasets"}]}}).json()
    row = client.post(f"/db/{key}/api/rows", json={"root": [found["results"][0]["index"]], "method": 0}).json()["rows"][0]
    assert row["impacts"][0] == pytest.approx(0.8 * shared / 10)
    assert client.get(f"/db/{key}/api/lookup", params={"name": "compost"}).json()["matches"]

    # A batch that fails is not made, and stays staged to be corrected
    client.post("/api/brightway/pending", json={**where, "change": {"kind": "delete", "title": "Delete dairy farm", "database": "my datasets", "code": read["code"]}})
    client.post("/api/brightway/pending", json={**where, "change": {**new, "title": "Create compost 2", "spec": {**new["spec"], "database": "nowhere"}}})
    failed = wait(client.post("/api/brightway/apply", json=where).json())
    assert failed["status"] == "failed" and "Create compost 2" in failed["error"]
    assert len(client.get("/api/brightway/pending", params=where).json()["changes"]) == 2
    assert sources.read_dataset(project, "toy", "my datasets", read["code"])["editable"]


def test_locked_databases_and_the_rebuild_they_require(project, tmp_path):
    """A locked database cannot be changed, from any database; a change made without rebuilding says which models are out of date."""
    import time

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    def wait(job):
        for _ in range(900):
            if job["status"] != "running":
                return job
            time.sleep(0.1)
            job = client.get(f"/api/jobs/{job['id']}").json()
        raise AssertionError("job still running")

    client = TestClient(create_library(tmp_path / "data"))
    folder, where = str(project), {"folder": str(project), "project": "toy"}
    first = wait(client.post("/api/brightway/dataset", json={**where, "spec": spec(products=[{"name": "milk", "amount": 1, "unit": "kilogram"}])}).json())
    key = wait(client.post("/api/load", json={"brightway": folder, "project": "toy", "database": "my datasets"}).json())["key"]
    assert client.get("/api/databases").json()[0]["outdated"] is None

    def listed():
        databases = client.get("/api/brightway", params={"folder": folder}).json()["projects"]
        return {d["name"]: d["locked"] for d in next(p for p in databases if p["name"] == "toy")["databases"]}

    assert listed() == {"bg": False, "bio": False, "my datasets": False}
    assert client.post("/api/brightway/lock", json={**where, "database": "bg", "locked": True}).json() == {"locked": ["bg"]}
    assert listed()["bg"] is True
    # bg is locked: its processes are read as locked, and no change to them is staged, applied or written
    read = client.get("/api/brightway/dataset", params={**where, "database": "bg", "code": "elec"}).json()
    assert read["locked"] is True and read["editable"] is True
    assert client.get("/api/brightway/dataset", params={**where, "database": "my datasets", "code": first["result"]["code"]}).json()["locked"] is False
    edit_bg = {"kind": "edit", "title": "Edit electricity", "database": "bg", "code": "elec", "spec": as_spec(read)}
    refused = client.post("/api/brightway/pending", json={**where, "change": edit_bg})
    assert refused.status_code == 400 and "bg is locked" in refused.json()["detail"]
    assert client.post("/api/brightway/dataset/delete", json={**where, "database": "bg", "code": "elec"}).status_code == 400
    into_bg = client.post("/api/brightway/dataset", json={**where, "spec": spec(database="bg", new_database=False)})
    assert into_bg.status_code == 400

    # Unlocked, it can be edited; locked again before the changes are applied, they are refused
    client.post("/api/brightway/lock", json={**where, "database": "bg", "locked": False})
    staged = client.post("/api/brightway/pending", json={**where, "change": edit_bg}).json()
    assert staged["affected"] == ["bg", "my datasets"] and staged["locked"] == []
    client.post("/api/brightway/lock", json={**where, "database": "bg", "locked": True})
    assert client.get("/api/brightway/pending", params=where).json()["locked"] == ["bg"]
    assert client.post("/api/brightway/apply", json=where).status_code == 400
    client.post("/api/brightway/lock", json={**where, "database": "bg", "locked": False})

    # Applied without rebuilding: the open model is out of date, and the apply says so
    done = wait(client.post("/api/brightway/apply", json={**where, "database": "my datasets", "rebuild": False}).json())
    assert done["status"] == "done" and done["affected"] == ["bg", "my datasets"] and "has to be built again" in done["message"], done
    assert "key" not in done
    assert "changed since the model was built" in client.get("/api/databases").json()[0]["outdated"]
    rebuilt = wait(client.post("/api/load", json={"brightway": folder, "project": "toy", "database": "my datasets",
                                                  "rebuild": True, "reopen": True}).json())
    assert rebuilt["status"] == "done" and rebuilt["key"] == key
    assert client.get("/api/databases").json()[0]["outdated"] is None


def test_import_without_the_default_biosphere(tmp_path, monkeypatch):
    """Ticked in the page, the default biosphere and methods of a version are installed first
    (``--defaults``); unticked, nothing is installed (``--no-setup``)."""
    import subprocess
    import time

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    seen = []

    class Done:
        stdout = iter(["imported\n"])

        def __init__(self, command, **kwargs):
            seen.append(command)

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Done)
    monkeypatch.setattr(sources, "brightway_projects", lambda folder: [{"name": "p", "databases": []}])
    client = TestClient(create_library(tmp_path / "data"))
    export = tmp_path / "export.csv"
    export.write_text("{CSV}")
    for i, defaults in enumerate(("3.10", None)):
        Done.stdout = iter(["imported\n"])
        job = client.post("/api/brightway/import", json={"folder": str(tmp_path / "bw"), "project": "p", "format": "simapro-csv",
                                                          "path": str(export), "database": f"x{i}",
                                                          "biosphere": "biosphere3", "defaults": defaults}).json()
        for _ in range(100):
            if job["status"] != "running":
                break
            time.sleep(0.05)
            job = client.get(f"/api/jobs/{job['id']}").json()
        assert job["status"] == "done", job
    assert seen[0][seen[0].index("--defaults") + 1] == "3.10" and "--no-setup" not in seen[0]
    assert "--no-setup" in seen[1] and "--defaults" not in seen[1]
    bad = client.post("/api/brightway/import", json={"folder": str(tmp_path / "bw"), "project": "p", "format": "simapro-csv",
                                                     "path": str(export), "database": "y", "defaults": "--unlinked"})
    assert bad.status_code == 400


def test_delete_database(project):
    import bw2data as bd

    with pytest.raises(SystemExit, match="used by bg"):
        editing.delete_database("toy", "bio")
    with pytest.raises(SystemExit, match="No database"):
        editing.delete_database("toy", "nothing")
    editing.delete_database("toy", "bg")
    editing.delete_database("toy", "bio")
    assert "bg" not in bd.databases and "bio" not in bd.databases
    assert [d["name"] for p in sources.brightway_projects(project) for d in p["databases"]] == []


def test_delete_database_through_the_api(project, tmp_path):
    import time

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    def wait(job):
        for _ in range(600):
            if job["status"] != "running":
                return job
            time.sleep(0.1)
            job = client.get(f"/api/jobs/{job['id']}").json()
        raise AssertionError("job still running")

    client = TestClient(create_library(tmp_path / "data"))
    body = {"folder": str(project), "project": "toy"}
    used = client.post("/api/brightway/databases/delete", json={**body, "database": "bio"})
    assert used.status_code == 400 and "bg" in used.json()["detail"]
    assert client.post("/api/brightway/databases/delete", json={**body, "database": "nothing"}).status_code == 404
    assert client.post("/api/brightway/databases/delete", json={**body, "project": "nope", "database": "bg"}).status_code == 404
    job = wait(client.post("/api/brightway/databases/delete", json={**body, "database": "bg"}).json())
    assert job["status"] == "done", job
    names = [d["name"] for p in sources.brightway_projects(project) if p["name"] == "toy" for d in p["databases"]]
    assert names == ["bio"]


def test_add_defaults_from_the_repository(tmp_path, monkeypatch):
    """The Brightway repository option: ``lca-viewer defaults <version> --project …`` in a separate process."""
    import subprocess
    import time

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    seen = []

    class Done:
        stdout = iter(["Downloading the biosphere\n"])

        def __init__(self, command, **kwargs):
            seen.append(command)

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Done)
    monkeypatch.setattr(sources, "brightway_projects", lambda folder: [{"name": "p", "databases": []}])
    client = TestClient(create_library(tmp_path / "data"))
    job = client.post("/api/brightway/defaults/install", json={"folder": str(tmp_path / "bw"), "project": "p", "version": "3.10"}).json()
    for _ in range(100):
        if job["status"] != "running":
            break
        time.sleep(0.05)
        job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done" and "3.10" in job["message"], job
    assert seen[0][seen[0].index("defaults") + 1] == "3.10" and seen[0][seen[0].index("--project") + 1] == "p"
    assert client.post("/api/brightway/defaults/install",
                       json={"folder": str(tmp_path / "bw"), "project": "p", "version": "--x"}).status_code == 400
    assert client.post("/api/brightway/defaults/install",
                       json={"folder": str(tmp_path / "bw"), "project": "nope", "version": "3.10"}).status_code == 404
