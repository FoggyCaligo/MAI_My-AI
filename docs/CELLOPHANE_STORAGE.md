# Cellophane Storage and Active Projection

## 1. Mental model

MAI stores observations as transparent sentence layers stacked over reusable Units.

At the lowest level the floor is character Units. Higher Units are independent objects created recursively from existing Units.

Each observed transition is one `CellElement`:

```text
CellElement
- source_unit_id
- next_unit_id
- sentence_id
- position
- pass_id
- link_depth
- opacity
```

`next_unit_id` acts like a directly followable address. `sentence_id + pass_id + position` identifies where that address existed on one historical sheet.

## 2. Persistent storage and lazy cache

SQLite is the persistent store. The engine does not load the whole database into memory.

When an active sequence touches a source Unit, MAI lazily loads that logical cell and caches it as:

```text
source Unit
  -> next Unit
      -> (sentence_id, historical_pass_id, position)
          -> opacity
```

This keeps persistence scalable while making repeated traversal of an active area cheap.

## 3. Filter sheets first, then count

Suppose the active sequence is:

```text
A -> B -> C
```

At `A -> B`, MAI keeps only historical elements whose next address is `B`.

For `B -> C`, a previously surviving sheet remains only when the same historical `(sentence_id, pass_id)` contains the next transition at `position + 1`.

Therefore these do not form one historical `A -> B -> C` path:

```text
sentence X / pass 0: A -> B
sentence Y / pass 0: B -> C
```

Nor do these:

```text
sentence X / pass 0: A -> B
sentence X / pass 3: B -> C
```

Only after unrelated sheets have been removed does MAI count what remains.

```text
density = number of surviving CellElements
```

Density is not a separate semantic confidence formula. It is the visible overlap count of the surviving structure itself.

## 4. Current pass and historical pass are different concepts

The current processing pass does not need to equal the historical pass where a structure was observed.

`pass_id` is preserved inside one historical path so edges from different representations cannot be stitched together, but it is not a requirement that historical pass N only supports current pass N.

## 5. Higher Units do not rewrite old observations

When a new Unit is learned, old sentence rows are not retroactively updated.

Example:

```text
old observation:
눈 / 꽃 / 의 / 계 / 절

later learned elsewhere:
[눈꽃]
[계절]
```

The old observation stays unchanged in SQLite.

When that sentence becomes active again, MAI overlays currently existing `compositions` in memory:

```text
[눈꽃] / [의] / [계절]
```

This is a read-only active projection, not a historical rewrite.

## 6. Recursive depth remains structural

Higher Units are independent reusable objects and keep the canonical depth rule:

```text
new_depth = max(child.depth) + 1
```

Unmerged Units retain their original depths, so mixed-depth sequences are valid.

## 7. Legacy persistence

Older development databases may still contain `unit_links`.

Startup migration copies only missing legacy observations into `cell_elements`. The migration is row-idempotent, so a partially migrated database can be reopened without either duplicating already copied rows or skipping rows that were never copied.
