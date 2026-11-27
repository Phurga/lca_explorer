# Methods library

Impact methods of the user's own, kept by the app, edited in *Projects*, and chosen at a build next to the methods
of the Brightway project. Code: `lca_viewer/methods_library.py` (storage, matching, CSV, API), `MethodsLibrary`,
`LibraryForm` and `MethodEditor` in `static/app.js`; tests: `tests/test_methods_library.py`.

## Storage

One JSON file per method in `data/methods/<id>.json` (`id`: 10 random hex digits, kept when the method is renamed):

```json
{"format": 1, "id": "3f2a9c41d0", "name": ["My EF", "climate change", "GWP100"], "unit": "kg CO2-eq",
 "description": "…", "origin": {"project": "ei", "method": ["EF v3.1", "climate change", "GWP100"]},
 "former_names": [], "modified": "2026-11-27T10:00:00",
 "factors": [{"database": "biosphere3", "code": "349b29d1-…", "name": "Carbon dioxide, fossil",
              "categories": ["air"], "unit": "kilogram", "cf": 1.0}]}
```

A factor is keyed by its elementary flow, `(database, code)`, one factor per flow (a project method's factors on one
flow, by location, are summed, as Brightway's LCA does). The flow's name, compartments and unit are kept to show the
factor without a project, and to find the flow in a project whose biosphere database has another name.

Decision: **files of the app, not methods written into a Brightway project.** A library method is not tied to one
project: the same one is built into databases of several projects (ecoinvent 3.10 and 3.12, BAFU…). Written into a
project, it would have to be written into each, through a `lca-viewer` subprocess for every edit (bw2data is never
imported in the server), and each write changes the project's methods stamp, which marks every database built from
that project as to rebuild. The app's own files are edited at once, by the server, and are read only when a build
needs them. JSON rather than CSV: a method has a name of several parts, a unit, a description, an origin and the key
of each flow; CSV is its import and export format.

## Names and source

In a model a library method is named `["library", *name]`: *library* is its **source**, as the release is the
source of the methods that come with ecoinvent (`("ecoinvent-3.10.1", family, category, indicator)`).
`_method_family` (server) and `methodPath` (page) read a first part `library` as the source (with two parts or more),
so the method lists group a library family apart from the project's: "EF v3.1 (copy) · library". Decision: a prefix
rather than a name of its own: a copy keeps the name of the method it was copied from, and still never collides with
it in a model, and the source is what a user sees in the lists (and can show in the names, *Name display*).

A name is unique in the library. A method renamed keeps its former names (`former_names`): a project cache built
with the former name finds it again (the rebuild takes the new name; its column is solved again, after the others).

## Matching flows

`Matcher` finds the flow of a factor in a project, read from the project's file (`lci/databases.db`, no bw2data):

1. by key, `(database, code)`;
2. else by code alone, when one flow of the project has it: ecoinvent's elementary flows keep their codes from one
   version of the biosphere to the next, under another database name (`biosphere3`, `ecoinvent-3.12-biosphere`);
3. else by name and compartments (case and spaces ignored; a last subcompartment *unspecified* is none, as SimaPro
   writes it), the unit checked (`kg` = `kilogram`, `m3` = `cubic meter`…).

A factor of no flow of the project is left out of a build of that project. The editor greys it out for the project
chosen, and the build's method picker gives the count (*2 of 3 factors on flows of the project*).

## Adding methods

- **Copy a method of a project** (`POST /api/library/methods/copy`): its factors are read from the project's files
  (`methods.json` gives the method's `abbreviation`, `intermediate/<abbreviation>.pickle` its factors, read with the
  unpickler that refuses any class, as the project metadata is), with the flows' keys and names. The copy is named
  after the method, its family marked `(copy)` and an ecoinvent release left out:
  `("ecoinvent-3.10.1", "EF v3.1", "climate change", …)` → `("EF v3.1 (copy)", "climate change", …)`.
- **New empty method** (`POST /api/library/methods`): name parts and unit; factors are added in the editor.
- **Import a CSV file** (`POST /api/library/methods/import`, the file given as a path or uploaded with *Choose
  file…*): one factor per row, *flow name, compartment, subcompartment, unit, factor*. A header row naming them gives
  them in any order and may add *database* and *code* columns (what the export writes); without one the order above
  is read. Comma, semicolon or tab separated, a decimal comma read. The rows are matched to the flows of a project
  chosen in the form; the rows of no flow (and those whose factor is not a number) are listed with the reason and
  left out. With `id`, the factors are set in that method instead of a new one (*Import CSV…* of the editor).

## Editing

`MethodEditor`, a form under the method's row in the *Methods library* section of *Projects*, in the style of the
project forms: name parts, unit, description (*Save*); the factors, with a filter (from 10 factors) and 300 rows drawn
before *Show all*; a factor's value is saved when the field is left (Enter); × removes it; a factor is added by
searching the emissions or resources of a project (`NodePicker`, `/api/brightway/search`) with the value typed next
to it. *Import CSV…*, *Export CSV* (`GET …/{id}/csv`), *Duplicate…* and *Delete…* (with a confirmation). Decision:
every change of a factor is written at once (no draft, no *Save* for the factors): the library is the app's own data
and nothing is built from it until a build is asked for.

## Builds

`/api/brightway/methods` lists the library's methods under `library`, with their names in a model and how many of
their factors are on flows of the project; `BuildForm` and `AddMethods` put them after the project's methods in the
picker. The chosen names go to the build as before (`--methods-file`); for those of the library the server writes
their factors on the project's node ids (`build_entries`, matched as above) to a second file, `--library-file`, which
the build process registers (`methods_library.register_file`). `brightway.method_factors` and `_unlinked_factors`
read a registered method from it instead of `bd.Method(name).load()`, so a library method enters `methods.pkl`, the
characterisation matrix and the columns of `G` like a project method. A build of library methods only works too (the
matrices are read with no Brightway method).

The project cache records the digest of each library method it was built with (`project.json`, the method's
`library` entry: its unit and factors). When the library changes (a factor, the unit, a rename, a deletion),
`Library.stale` marks the databases built with it as to rebuild (*methods of the library changed since the model was
built*), as a change of the project's methods does. A build always reads the library methods again; a method whose
factors did not change keeps its token, so its columns of `G` are not solved again.

`/api/methods/add` (*Add impact methods* of the Methods panel) adds library methods to an open database the same
way; the library methods the project cache already has are given to the build process again, which keeps only the
methods it knows.

## API

| request | |
|---|---|
| `GET /api/library/methods` | the methods (no factors) |
| `POST /api/library/methods` | `{name, unit, description}`: a new empty method |
| `POST /api/library/methods/copy` | `{folder, project, method, name?}`: a copy of a project's method |
| `POST /api/library/methods/import` | `{folder, project, path, name, unit, description, id?}`: a CSV file → `{method, matched, unmatched}` |
| `GET /api/library/methods/{id}?folder=&project=` | the method and its factors; with a project, each factor's `flow` (node id) or `reason` |
| `POST /api/library/methods/{id}` | `{name?, unit?, description?}` |
| `POST /api/library/methods/{id}/factors` | `{set: [{database, code, cf, …}], remove: [[database, code]]}` |
| `POST /api/library/methods/{id}/duplicate` | `{name}` |
| `POST /api/library/methods/{id}/delete` | |
| `GET /api/library/methods/{id}/csv` | the factors as CSV |
