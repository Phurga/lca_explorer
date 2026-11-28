# Methods library

Impact methods of the user's own, kept by the app, edited in *Projects*, and chosen at a build next to the methods
of the Brightway project. Code: `lca_viewer/methods_library.py` (storage, matching, CSV, API), `MethodsLibrary`,
`LibraryForm`, `AddReport`, `CurateNames` and `MethodEditor` in `static/app.js`; tests:
`tests/test_methods_library.py`.

## Storage

One JSON file per method in `data/methods/<id>.json` (`id`: 10 random hex digits, kept when the method is renamed):

```json
{"format": 1, "id": "3f2a9c41d0", "name": ["EF v3.1", "climate change", "GWP100"], "source": "ecoinvent-3.10.1",
 "unit": "kg CO2-eq", "description": "…",
 "origin": {"project": "ei", "method": ["ecoinvent-3.10.1", "EF v3.1", "climate change", "GWP100"]},
 "former_names": [["library", "ecoinvent-3.10.1_EF_climate"]], "modified": "2026-11-27T10:00:00",
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

A library method has a name of parts, `name` (family, category, indicator, more parts), and may have a **source** of
its own, `source` (empty for none): the release of the method it was copied from, or any text curated in. In a model
it is named `["library", *name]`, or `["library/<source>", *name]` when it has a source: *library* is its source in
the method lists, as the release is the source of the methods that come with ecoinvent
(`("ecoinvent-3.10.1", family, category, indicator)`). `_method_family` (server) and `methodPath` (page) read a first
part `library` or `library/…` as the source (with two parts or more), so the method lists group a library family apart
from the project's: "EF v3.1 · library/ecoinvent-3.10.1" next to the project's "EF v3.1 · ecoinvent-3.10.1".
Decision: a prefix rather than a name of its own: a copy keeps the name of the method it was copied from, and still
never collides with it in a model, and the source is what a user sees in the lists (and can show in the names, *Name
display*). Decision: the source as `library/<source>` in one first part, rather than a part of its own after
`library`: the name of a library method is read like any other (source, family, category…), and the builds, the
project cache and the lists need no other change.

A name in a model is unique in the library (`Library.taken`). A method renamed keeps its former names in a model
(`former_names`; files written before the sources kept names in the library, read as `["library", *name]`):
`Library.find` finds it under them, so a project cache built with a former name still finds the method, and
`Library.stale` reports it as changed, so the database is shown as to rebuild (*methods of the library changed since
the model was built*). The rebuild takes the new name; its column is solved again, after the others. A name of
another method later given a former name wins (`find` looks at current names first).

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

Copies and imports add **several methods at once**, under the names they come with: neither asks for nor fixes the
name structure, which is curated afterwards (below). When the library has a method of the name already (in a model),
the method is skipped (`on_conflict: "skip"`, the default), and the page offers to add the skipped ones again with
`on_conflict: "rename"`: a `suffix` (" (copy)" by default) after the last part, numbered while taken ("GWP100 (copy)",
"GWP100 (copy) 2"; with no suffix, "GWP100 (2)"). `AddReport` lists the methods added (a renamed one with the name it
wanted), skipped (and why) and the rows of no flow.

- **Copy methods of a project** (`POST /api/library/methods/copy`, `methods`: a list of names; `copy_methods`), picked
  in `MethodPicker` (filter, folding families, a family ticked at once): their factors are read from the project's
  files (`methods.json` gives each method's `abbreviation`, `intermediate/<abbreviation>.pickle` its factors, read
  with the unpickler that refuses any class, as the project metadata is), with the flows' keys and names. A copy keeps
  the method's name; an ecoinvent release first in a name of four parts or more becomes its source (`split_name`, as
  `methodPath` reads it): `("ecoinvent-3.10.1", "EF v3.1", "climate change", …)` → name
  `("EF v3.1", "climate change", …)`, source `ecoinvent-3.10.1`. A method the project does not have, or whose factors
  cannot be read, is skipped with the reason. With `method` (one name) instead, the copy is returned, and a name taken
  is an error (`name` gives another).
- **New empty method** (`POST /api/library/methods`): name parts and unit; factors are added in the editor.
- **Import CSV files** (`POST /api/library/methods/import`, `paths`: typed, or files chosen in the page, several at
  once, uploaded with `/api/upload`; `names`: their names as chosen): one factor per row, *flow name, compartment,
  subcompartment, unit, factor*. A header row naming them gives them in any order and may add *database* and *code*
  columns (what the export writes); without one the order above is read. Comma, semicolon or tab separated, a decimal
  comma read. **The method of each row**: a `method` column (its parts separated by `|`, an ecoinvent release first
  read as its source), or `source`, `family`, `impact category` and `indicator` columns (a `category` column is the
  impact category when there is a `compartment` column, else the compartment); a `method unit` column gives a
  method's unit (else the form's *Unit*). A file without those columns is one method, named after the file (its
  name as chosen). The rows are matched to the flows of a project chosen in the form; the rows of no flow (and those
  whose factor is not a number, or of no method name) are listed with the reason, their file and method, and left
  out; a method with no row matched is skipped. `only` (names in a model, as read from the files) adds just those
  methods: the page sends the files again with it to add the skipped ones renamed (an uploaded file is kept until
  then, when methods were skipped for their names; the uploads left are deleted when the app starts). With `id`, all
  the factors are set in that method instead (*Import CSV…* of the editor).

## Curating names

*Curate names* (`CurateNames`, a view of the library in place of its list) shows every method as a table of its
name's parts: source, family, category, indicator, the parts after, and unit, each edited in place. The rows ticked
are changed together: a part set to a value, a text replaced in a part (or every part), a part split in two at the
first separator, a part merged with the next one. The source is the first of the parts for the split and the merge:
merging an empty source with the family makes the family the source. A preview groups the methods as the method
lists will (by family and source, category and indicator per row). Nothing is written until *Apply*, which sends the
changed methods in one request (`POST /api/library/methods/names`, `Library.rename_many`): all or none, the names
checked against the library as it will be (two methods may swap their names; two of one name, a name starting with
`library` or an empty one are refused, and the page marks them first). The unit may be changed there too. A method
renamed keeps its former name, as above: databases built with it are shown as to rebuild.

Decision: names curated apart from adding methods. Adding many methods at once cannot ask for each name, and the
same fix (a family, a source, a separator to split on) is usually needed on many methods: a table of all of them with
bulk changes does it once, and previews the lists' grouping before anything is written.

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
| `POST /api/library/methods/copy` | `{folder, project, methods, on_conflict?, suffix?}`: copies of a project's methods → `{added, skipped}`; or `{…, method, name?}`: one copy |
| `POST /api/library/methods/import` | `{folder, project, paths, names?, unit, on_conflict?, suffix?, only?, id?}` (or `path`): CSV files → `{methods, method, skipped, matched, unmatched}` |
| `POST /api/library/methods/names` | `{changes: [{id, name?, source?, unit?}]}`: names curated, all or none → `{changed, methods}` |
| `GET /api/library/methods/{id}?folder=&project=` | the method and its factors; with a project, each factor's `flow` (node id) or `reason` |
| `POST /api/library/methods/{id}` | `{name?, source?, unit?, description?}` |
| `POST /api/library/methods/{id}/factors` | `{set: [{database, code, cf, …}], remove: [[database, code]]}` |
| `POST /api/library/methods/{id}/duplicate` | `{name}` |
| `POST /api/library/methods/{id}/delete` | |
| `GET /api/library/methods/{id}/csv` | the factors as CSV |
