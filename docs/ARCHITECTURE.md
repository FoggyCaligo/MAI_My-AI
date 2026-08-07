# MAI Architecture Specification

## 1. Core Idea

MAI uses one recursive Unit system rather than hard-coded semantic layers.

The storage model and the recognition model are intentionally separated:

```text
persistent observation
  -> store only what was actually observed at that time
  -> never retroactively rewrite inactive past sentences

current activation
  -> overlay currently known Units on the active area
  -> read literal CellElement overlap counts as density
  -> discover new Units only from the active structure
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

SQLite is the persistent representation of the conceptual cellophane structure.

Each Unit acts as a logical cell. A cell contains many observed elements from different sentence sheets.

Each `cell_elements` row contains:

```text
source_unit_id   : the current cell
next_unit_id     : address-like direct pointer to the next Unit
sentence_id      : unique id of the original sentence / cellophane sheet
position         : horizontal position inside that observed layer
pass_id          : recursive pass coordinate in that observation
link_depth       : max depth of the two connected Units
opacity          : feedback-adjustable weight used by higher-level thinking
```

Conceptually:

```text
cell [눈]
  - next=[꽃], sentence=S1, pass=0, position=0
  - next=[이], sentence=S2, pass=0, position=0
  - next=[꽃], sentence=S3, pass=0, position=2
  - ...
```

The database keeps:

- `units`: persistent Units and their structural depth
- `compositions`: parent -> positioned children
- `cell_elements`: observed address elements inside logical cells

Older `unit_links` data can be migrated into `cell_elements`. The engine no longer needs legacy `unit_links` for recognition.

---

## 3. Density Is the Structure Itself

MAI does not need a separate semantic score to decide how dark a top-view area is.

For the currently active sequence, the density of an adjacency is simply:

```text
density(A -> B) = number of CellElements in cell[A] whose next address is B
```

Example:

```text
눈 -> 꽃 : 17 elements
꽃 -> 의 : 3 elements
의 -> 계 : 2 elements
계 -> 절 : 15 elements
```

Those counts are already the top-view darkness. The engine does not convert them into a second artificial confidence value.

The cache exists only to avoid rereading the same logical cell repeatedly. The persistent source of truth remains SQLite.

`pass_id` is not used as a current-pass equality filter. If the exact same Unit adjacency was observed at different recursive passes, it is still the same structural adjacency from the top view. The stored `pass_id` remains available to reconstruct the historical layer from the side.

---

## 4. Lazy Projection: Past Observations Are Not Rewritten

New Units do **not** trigger a database-wide reprocessing of old sentences.

Suppose the first observation is:

```text
S1: 눈 / 꽃 / 의 / 계 / 절
```

Later observations may independently create:

```text
[눈꽃] d1 = [눈]d0 + [꽃]d0
[계절] d1 = [계]d0 + [절]d0
```

Those Units can live elsewhere in the graph. S1 does not need to be modified.

When S1 becomes active again, MAI overlays currently existing compositions on the active base sequence:

```text
stored S1 remains:
[눈] [꽃] [의] [계] [절]

current active projection:
[눈꽃] [의] [계절]
```

This is a **read-only lazy projection**. Inactive historical areas are not updated merely because new knowledge appeared elsewhere.

The implementation exposes this distinction explicitly:

```text
process_sentence(...)
  -> a new observation; may write new CellElements / Units

activate_sentence(sentence_id)
  -> read-only projection of a stored observation using currently known Units
  -> does not rewrite that sentence
  -> does not create new observation rows
```

This keeps growth practical when the database becomes large.

---

## 5. Existing Units Are Independent Objects

A Unit does not have to be physically stored inside every sentence where its children could match.

For example:

```text
Unit U37: [눈꽃] d1
children: [눈]d0, [꽃]d0
```

may have been learned from some other observation. When an active area contains the child sequence `[눈][꽃]`, U37 can be projected onto that area because its composition already expresses that structure.

Therefore:

```text
observation ownership != Unit identity
```

A sentence records what was observed. A Unit records a reusable structure.

---

## 6. Recursive Mixed-Depth Layering

Processing begins with character Units (`depth 0`). Existing higher Units may first be projected onto the active sequence, and literal CellElement density can then reveal new repeated structure.

Example:

```text
[눈]d0 + [꽃]d0
  -> [눈꽃]d1

[눈꽃]d1 + [의]d0 + [계절]d1
  -> [눈꽃의계절]d2
```

The lower Units are not replaced or deleted. `[눈]`, `[눈꽃]`, and `[눈꽃의계절]` may all coexist.

`pass_id` and `unit.depth` remain independent:

- `pass_id`: which recursive iteration observed a sequence in one sentence
- `unit.depth`: structural depth calculated from actual children

A newly created Unit always uses:

```text
max(child.depth) + 1
```

so recursive depth can continue growing without a fixed semantic ceiling.

---

## 7. Side View Remains Intact

Persistent side-view coordinates remain:

```text
sentence_id + pass_id + position
```

and vertical Unit structure remains available through `compositions`.

Conceptually:

```text
                  [눈꽃의계절] d2
                    /    |    \
               [눈꽃]d1 [의]d0 [계절]d1
                 /  \              /  \
              [눈]d0 [꽃]d0      [계]d0 [절]d0
```

There are therefore two related side views:

1. **historical side view** — what was actually observed and stored at that time;
2. **active side view** — what currently known Units reveal when projected over the active historical base.

The second can become richer over time without modifying the first.

---

## 8. Example Learning Sequence

Given these observations:

```text
1. 눈꽃의계절
2. 눈꽃이 흩날린다
3. 독서의 계절
```

The intended behavior is:

```text
Observation 1
  -> initially remains character-level where no overlap exists

Observation 2
  -> the previously observed 눈 -> 꽃 address overlaps
  -> [눈꽃] can be created as a new higher-depth Unit

Observation 3
  -> the previously observed 계 -> 절 address overlaps
  -> [계절] can be created as a new higher-depth Unit

Later activation of Observation 1
  -> no retroactive DB rewrite
  -> existing compositions are overlaid lazily
  -> active view can appear as [눈꽃] / [의] / [계절]
```

This is the intended distinction between persistent observation and current interpretation.

---

## 9. Side-View Thinking and Feedback

After recursive layering terminates, Think can use current Units whose actual `unit.depth >= 2` to find intersecting historical sentence layers.

`opacity` remains a feedback-adjustable weight for higher-level thinking:

```text
50  -> x1.00
100 -> x1.25
0   -> x0.75
```

Opacity does not redefine top-view structural density. Structural density remains the literal count of matching CellElements.

The current Think implementation still contains a lightweight Korean functional-particle filter. That heuristic belongs to the experimental Think layer and is not part of Unit identity or depth.

---

## 10. Core vs Application Boundary

```text
core/
  db.py       : SQLite Unit/composition/CellElement persistence and migration
  engine.py   : literal density, lazy active projection, recursive layering,
                side-view thinking, feedback
  __init__.py : core interface

app/
  cli.py      : interactive interface
  main.py     : application entry point
```

The core remains independent of UI, CLI, web frameworks, and product shells.
