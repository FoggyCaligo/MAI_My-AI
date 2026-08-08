# MAI Architecture Specification

## 1. Core Idea

MAI uses one recursive Unit system rather than hard-coded semantic layers.

Persistent observations and current interpretation are separate:

```text
persistent observation
  -> store only what was actually observed at that time
  -> do not retroactively rewrite inactive past sentences

current activation
  -> overlay currently known Units on the active area
  -> read literal CellElement counts as top-view density
  -> discover new Units only from the active structure
  -> feed the resulting mixed-depth Unit sequence back in
```

A new Unit always follows:

```text
new_depth = max(child.depth) + 1
```

`depth` is structural history, not a fixed semantic class.

---

## 2. Logical Cells and CellElements

Each Unit acts as a logical cell. A cell contains observed next-address elements from sentence layers.

Each `cell_elements` row stores:

```text
source_unit_id
next_unit_id
sentence_id
position
pass_id
link_depth
opacity
```

Conceptually:

```text
cell [눈]
  - next=[꽃], sentence=S1, pass=0, position=0
  - next=[이], sentence=S2, pass=0, position=0
  - next=[꽃], sentence=S3, pass=0, position=2
```

SQLite is the persistent source of truth. Cells are loaded lazily into memory only to avoid repeatedly reading the same source cell.

---

## 3. Density Is Already Visible in the Structure

MAI does not convert overlap into a separate semantic confidence score.

For an active adjacency:

```text
density(A -> B)
= number of CellElements in cell[A] whose next_unit_id is B
```

For example:

```text
눈 -> 꽃 : 17
꽃 -> 의 : 3
의 -> 계 : 2
계 -> 절 : 15
```

Those counts themselves are the top-view darkness.

`pass_id` is not a current-pass equality filter. The same Unit adjacency remains the same structural adjacency even if it was observed during different recursive passes. `pass_id` remains stored as a historical side-view coordinate.

---

## 4. Existing Units Live Independently

A higher-depth Unit does not need to be copied into every historical sentence where its children could fit.

Example:

```text
[눈꽃] d1
children = [눈]d0, [꽃]d0
```

may be learned from a different sentence entirely. Once it exists, any currently active sequence containing `[눈][꽃]` can project `[눈꽃]` over that position through the composition structure.

Therefore:

```text
observation ownership != Unit identity
```

A sentence stores an observation. A Unit stores reusable structure.

---

## 5. Lazy Active Projection

New knowledge does not trigger database-wide reprocessing.

Suppose the first sentence is stored as:

```text
S1: [눈] [꽃] [의] [계] [절]
```

Later, other observations create:

```text
[눈꽃] d1
[계절] d1
```

S1 remains untouched in persistent storage.

When S1 becomes active again, MAI can produce a read-only current projection:

```text
historical base:
[눈] [꽃] [의] [계] [절]

active projection:
[눈꽃] [의] [계절]
```

The implementation distinguishes the two operations:

```text
process_sentence(...)
  -> new observation; may create Units and CellElements

activate_sentence(sentence_id)
  -> read-only projection using currently known compositions
  -> does not rewrite the old sentence
  -> does not create a new observation
```

This keeps inactive regions cheap even when the database becomes large.

---

## 6. Recursive Mixed-Depth Layering

Mixed depths remain valid.

```text
[눈]d0 + [꽃]d0
  -> [눈꽃]d1

[눈꽃]d1 + [의]d0 + [계절]d1
  -> [눈꽃의계절]d2
```

The lower Units remain in the database. `[눈]`, `[눈꽃]`, and `[눈꽃의계절]` can coexist.

`pass_id` and `unit.depth` are independent:

- `pass_id`: recursive processing coordinate of one observation
- `unit.depth`: `max(child.depth) + 1`

Depth can therefore continue growing recursively without a fixed semantic ceiling.

---

## 7. Side View

Historical observed layers remain reconstructable through:

```text
sentence_id + pass_id + position
```

Vertical Unit structure remains reconstructable through `compositions`.

This creates two useful side views:

1. **historical side view** — what was actually stored at the time;
2. **active side view** — what currently known Units reveal when lazily projected onto that historical base.

The active side view may become richer over time while the historical data stays unchanged.

---

## 8. Example Learning Sequence

Given:

```text
1. 눈꽃의계절
2. 눈꽃이 흩날린다
3. 독서의 계절
```

Expected behavior:

```text
1. first observation
   -> no prior overlap, so it may remain character-level

2. second observation
   -> 눈 -> 꽃 already exists in the cells
   -> [눈꽃] can be created

3. third observation
   -> 계 -> 절 already exists in the cells
   -> [계절] can be created

later activation of observation 1
   -> no retroactive rewrite
   -> existing [눈꽃] and [계절] compositions are projected lazily
   -> active view: [눈꽃] / [의] / [계절]
```

---

## 9. Feedback and Think

Opacity remains a higher-level feedback weight:

```text
50  -> x1.00
100 -> x1.25
0   -> x0.75
```

Opacity does not redefine top-view density. Top-view density is the literal count of matching CellElements.

Think may still use higher-depth active Units and opacity-weighted historical intersections. The current Korean particle filter belongs to the experimental Think layer and is not part of Unit identity or depth.

---

## 10. Core / Application Boundary

```text
core/
  db.py       : SQLite persistence and migration
  engine.py   : literal density, lazy projection, recursive layering,
                side-view thinking, feedback
  __init__.py : core interface

app/
  cli.py
  main.py
```

The core remains independent from UI or product-specific shells.

---

## 11. 다음 단계: Thought Space와 자연어 표현

현재 Core의 Side View는 입력과 교차하는 과거 구조에서 연상 Unit을 인출한다. 다음 단계에서는 이 인출 결과를 완성된 사고로 간주하지 않고, 세 번째 축인 Thought Space에 임시로 배치하여 사고를 전개한다.

```text
X축: 구성 요소가 수평으로 연결되어 하나의 Unit을 정의하는 방향
Y축: 만들어진 Unit이 새로운 depth에 쌓이며 관계와 맥락을 형성하는 방향
Z축: 기억에서 선택한 Unit을 임시로 연결하며 사고를 전개하는 Thought Space
```

X축은 특정 문장 하나의 절대 좌표축이 아니다. 관찰된 구성 요소가 수평으로 연결되어 하나의 개념 덩어리인 Unit을 정의하는 방향이다. `position`은 전체 X축의 절대 좌표가 아니라 특정 관찰이나 Unit 구성 안의 상대적 위치다.

Thought는 X축을 수정하거나 새로운 Unit 구조를 정의하지 않는다. Side View가 X/Y 기억 공간에서 선택한 완성된 Unit을 Z축에 놓고 연결·분기·비활성화한다. 그 Z축 흔적 자체가 사고 경로가 되며, 현재 활성화된 일부 Unit만 작업기억으로 유지한다.

완성된 Thought를 자연어로 표현할 때는 특정 depth의 Unit을 단순 연결하지 않는다. 최종 Thought와 겹치는 과거 X/Y 언어 구조를 Expression View로 찾고, 표현 골격에 Thought Unit을 투영한 뒤 composition을 선택적으로 펼쳐 문자열로 만든다.

```text
Memory -> Recall -> Thought -> Expression -> Natural Language
```

Thought Space와 Expression View의 상세 설계, 초기 구현 범위, 미결정 정책은 [THOUGHT_SPACE.md](./THOUGHT_SPACE.md)에 정리한다. 이 부분은 현재 구현이 아니라 다음 단계의 설계안이다.
