# MAI Architecture Specification

## 1. Core Idea

MAI uses one recursive Unit system rather than hard-coded semantic layers.

The storage and recognition model follows the cellophane metaphor directly:

```text
observation
  -> store address-like CellElements in logical Unit cells
  -> overlay the current sequence on those cells
  -> discard historical paths that point somewhere else
  -> count the surviving layers from the top view
  -> materialize the selected Unit
  -> feed the resulting mixed-depth Unit sequence back in
  -> repeat
```

`depth` is not a semantic label. A newly created Unit always follows:

```text
new_depth = max(child.depth) + 1
```

Unmerged Units keep their original depth, so mixed-depth sequences are valid.

---

## 2. Logical Cell / CellElement Storage

SQLite is the persistent representation of a conceptual 3D cellophane structure.

The floor consists of Units. Each Unit acts as a logical **cell**. A cell can contain many stacked elements from different observed sentences.

Each `cell_elements` row contains:

```text
source_unit_id   : the current cell
next_unit_id     : address-like direct pointer to the next Unit
sentence_id      : unique id of the original sentence / cellophane sheet
position         : horizontal position inside that stored layer
pass_id          : vertical derivation pass in that sentence
link_depth       : max depth of the two connected Units
opacity          : feedback-adjustable visibility weight
```

Therefore a logical cell may look conceptually like:

```text
cell [눈]
  - next=[꽃], sentence=S1, pass=0, position=0
  - next=[이], sentence=S2, pass=0, position=0
  - next=[꽃], sentence=S3, pass=0, position=2
  - ...
```

The database schema also keeps:

- `units`: persistent Units and their actual depth
- `compositions`: parent -> positioned children, used to reconstruct derivation
- `cell_elements`: stacked address/path elements inside logical cells

Older `unit_links` databases are copied into `cell_elements` on migration. The legacy table may remain physically present, but the engine no longer writes to or searches it.

---

## 3. Top View: Cellophane Overlap

Top-view recognition does not repeatedly query every candidate span from SQLite.

For a source Unit, the engine lazily loads its logical cell once and caches it in memory as:

```text
source_unit_id
  -> next_unit_id
      -> (sentence_id, stored_pass_id, position)
```

When the current sentence follows:

```text
A -> B -> C
```

MAI performs:

```text
cell[A]: keep only elements whose next address is B
cell[B]: among those surviving layers, keep only the same
         sentence_id + stored_pass_id at position + 1 whose next address is C
...
```

A historical path that diverges is removed from that candidate path only. The same sentence may still participate from another starting offset.

The density of a candidate span is simply the number of surviving historical layers. At each current start position:

1. choose the span with the largest surviving-layer count;
2. when counts are equal, choose the longer span;
3. if no historical layer survives, keep the original Unit unchanged.

Only the finally selected span is written as a new Unit. Candidate spans are never materialized merely for scoring.

`pass_id` is deliberately **not** required to equal the current processing pass. A structure learned in historical pass 1 can support the same Unit sequence observed in current pass 2. The stored pass id remains part of the historical path key so a layer can still be reconstructed correctly from the side.

---

## 4. Recursive Mixed-Depth Layering

Processing starts with character Units (`depth 0`).

Example:

```text
[눈]d0 + [꽃]d0
  -> [눈꽃]d1

[눈꽃]d1 + [의]d0 + [계절]d1
  -> [눈꽃의 계절]d2
```

The lower Units are not replaced or deleted. `[눈]`, `[눈꽃]`, and `[눈꽃의 계절]` can coexist permanently.

`pass_id` and `unit.depth` are independent:

- `pass_id`: which recursive iteration produced/observed a sequence in one sentence
- `unit.depth`: structural composition depth calculated from actual children

The next pass receives the selected Unit IDs exactly as they are, preserving mixed depths.

---

## 5. Side View Remains Intact

The top-view optimization does not remove the side view.

A stored layer can be reconstructed with:

```text
sentence_id + pass_id + position
```

and the vertical composition of a Unit can be reconstructed through `compositions`.

Conceptually:

```text
                  [눈꽃의 계절] d2
                    /    |    \
               [눈꽃]d1 [의]d0 [계절]d1
                 /  \              /  \
              [눈]d0 [꽃]d0      [계]d0 [절]d0
```

Thus the same persistent data supports two independent views:

```text
Top view  : follow next_unit_id and count surviving stacked elements
Side view : follow sentence/pass/position coordinates and compositions
```

`pass_id` is a side-view coordinate, not a top-view equality constraint.

---

## 6. Side-View Thinking

After recursive layering terminates, thinking uses current Units whose actual `unit.depth >= 2`.

MAI finds other sentence layers intersecting those Units in `cell_elements`, then retrieves higher-depth Units from those intersecting layers. `opacity` remains available as a feedback-adjustable weight for thought prominence.

The current implementation still contains a lightweight Korean functional-particle filter. This is an application heuristic around the current experimental Think implementation, not a definition of Unit depth.

---

## 7. Feedback

User feedback is stored as opacity adjustment on `cell_elements` at `link_depth >= 2`.

```text
50  -> x1.00
100 -> x1.25
0   -> x0.75
```

Top-view structural support is based on the number of surviving CellElements. Opacity does not redefine whether the historical path exists; it affects visibility/weight in higher-level thinking.

---

## 8. Core vs Application Boundary

```text
core/
  db.py       : SQLite Unit/composition/CellElement persistence and migration
  engine.py   : cached top-view overlap, recursive layering, side-view thinking, feedback
  __init__.py : core interface

app/
  cli.py      : interactive interface
  main.py     : application entry point
```

The core remains independent of UI, CLI, web frameworks, and product shells.
