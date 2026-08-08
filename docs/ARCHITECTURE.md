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

## 11. Thought Space와 자연어 표현

Core의 `recall()`은 입력과 교차하는 과거 구조에서 연상 Unit과 근거를 인출한다. `think()`는 이 인출 결과를 완성된 사고로 간주하지 않고 세 번째 축인 Thought Space에 배치하여 사고를 전개한다.

```text
X Memory Cube
  = Unit position × List/depth × observation Time

Y Side Association Plane
  = 활성 Unit을 포함한 셀로판 전체를 골라
    Unit 길이를 유지한 채 겹쳐 본 연상 단면

Z Thought Stack
  = Y 연상 평면 위에 영속적으로 쌓이는 사고 셀로판
```

한 입력은 재귀 처리 단계마다 별도의 Unit List를 만든다. 이 문서의 공간 모델에서 List와 depth는 별도 축이 아니다. 하나의 List가 하나의 재귀 depth 층이며, `Unit position × List/depth`로 이루어진 한 장의 입력 셀로판이 observation Time 방향으로 누적되면서 X Memory Cube가 된다. DB의 `unit.depth`는 Unit의 생성 이력이므로 현재 구현상 `passId`와 항상 같지는 않지만, 별도의 공간축으로 사용하지 않는다.

새 입력의 각 List/depth에서 활성화된 Unit을 하나라도 포함하는 과거 입력 셀로판을 선택한다. 이후 활성 Unit만 잘라내지 않고 선택된 셀로판의 전체 Unit List를 투영한다. 이때 Unit 방향을 정면으로 보아 짧은 점선으로 축약하지 않고, Unit 순서가 보존된 긴 선들이 observation Time 방향으로 겹치도록 List/depth 방향에서 바라본 결과가 Y Side Association Plane이다. 선택 기준이 된 Unit과 함께 셀로판에 있던 다른 Unit들이 이 면에서 즉각적인 연상으로 드러난다.

Thought는 X Memory Cube의 관찰 기억이나 Unit 구조를 수정하지 않는다. Y 연상 평면에서 선택한 Unit을 Z축에 놓고 연결·분기하며, 완성된 원본 Thought 셀로판을 append-only로 영속 저장한다. 외부 관찰 기억과 자체 사고 기억은 출처를 구분한다.

현재 Z View는 저장된 Thought를 그대로 시간 간격으로 세지 않는다. 문장 셀로판은 Unit을 새 좌표에 복제하지 않고 기존 Unit 셀을 참조하며, 순서는 `sentenceId + passId + position`으로 별도 저장한다. 따라서 같은 `unitId`의 Thought occurrence는 항상 같은 Unit 셀에서 겹친다. 서로 다른 depth의 Unit은 `compositions`를 아래로 펼쳤을 때 공유하는 실제 하위 Unit 셀 영역에서만 부분적으로 겹친다. 같은 Unit 셀 영역을 차지한 occurrence만 최신순으로 빈칸 없이 압축하며, 서로 겹치지 않는 Thought는 과거 자국의 투명도를 낮추지 않는다.

```text
저장:
T1: [A] [ ] [ ]
T2: [ ] [B] [ ]
T3: [A] [ ] [ ]
T4: [ ] [ ] [C]

현재 Z View:
[A] [B] [C]
[A]
```

가장 최근 A는 표면의 현재 활성 판단으로 보이고, 아래의 과거 A는 흐려져도 총 가시성에 계속 기여한다. DB에는 원본 사고 순서와 직접 참조한 Unit 셀 및 당시 composition footprint를 보존하고, 셀별 localZDepth와 visibleOpacity는 조회 시 계산한다. 동일 Thought 안의 중복 셀은 같은 깊이를 공유하고, 현재 영향력 View에서는 `conclusion`과 `alternative`만 깊이를 만든다. 첫 구현의 감쇠 기본값은 `0.8 ** localZDepth`다.

같은 출발 Unit 셀에서 갈라진 Thought 연결은 출발 셀의 localZDepth를 공유한다. `A -> B` 이후 `A -> C`가 생기면 최근 연결은 depth 0, 과거 연결은 depth 1의 감쇠를 받는다. 원시 반복 횟수인 `thoughtDensity`는 보존하고, Recall 후보와 결론 경로의 현재 영향력 비교에는 감쇠된 `thoughtVisibility`를 사용한다.

완성된 Thought를 자연어로 표현할 때는 특정 depth의 Unit을 단순 연결하지 않는다. 결론 Unit에서 더 깊고 뭉뚱그려진 Unit을 거쳐 같은 depth의 관련 Unit을 찾고, 그 Unit들이 실제로 나타난 `sentenceId + passId` 층을 수집한다. 문장은 문자 길이가 아닌 Unit position별로 겹치며, 가장 진한 골격의 대응 칸들을 현재 결론 Unit으로 교체해 문자열을 만든다.

```text
Memory -> Recall -> Thought -> Expression -> Natural Language
```

Thought Space와 Expression View의 상세 설계, 현재 구현 범위, 남은 조정 항목은 [THOUGHT_SPACE.md](./THOUGHT_SPACE.md)에 정리한다.
