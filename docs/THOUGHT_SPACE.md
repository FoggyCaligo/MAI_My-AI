# MAI 사고 공간 설계

## 1. 문서의 목적

현재 MAI Core는 다음 기능의 첫 버전을 구현하고 있다.

1. 관찰된 문장을 재사용 가능한 Unit과 CellElement로 누적한다.
2. 현재 입력과 교차하는 과거 구조를 Side View로 찾아 연상 Unit을 인출한다.
3. 연상 Unit을 Z축에서 연결·분기하고 전체 사고 흔적을 상태와 함께 영속화한다.
4. 결론 Unit과 과거 문장 층을 Unit position으로 겹쳐 자연어 표현을 만든다.

기존 `_think_side_view()`가 반환하던 결과는 완성된 사고라기보다 **기억의 인출과 연상**에 가까웠다. 이 문서는 인출된 Unit을 Z축에서 전개하여 생각을 만들고, 그 생각을 다시 자연어로 표현하는 개념 모델과 첫 구현의 동작을 정의한다.

Thought Space와 Expression View의 첫 구현은 `core/engine.py`와 `core/db.py`에 반영되어 있다. 현재 구현은 구조적 가능성을 검증하는 초기 버전이며, 이 문서의 남은 구현 세부사항은 실험과 테스트를 통해 계속 조정한다.

---

## 2. X Memory Cube, Y Association Plane, Z Thought Stack

X, Y, Z는 단순한 세 개의 숫자 좌표가 아니다. X는 영속적인 기억 큐브이고, Y는 현재 입력으로 X를 잘라 옆에서 본 연상 평면이며, Z는 그 Y 평면을 재료로 사고 셀로판을 쌓는 공간이다.

```text
X Memory Cube
  = Unit position × List/depth × observation Time

Y Side Association Plane
  = 현재 입력의 활성 Unit을 포함한 셀로판 전체를 골라,
    Unit 길이를 유지한 채 겹쳐 본 연상 단면

Z Thought Stack
  = Y 연상 평면 위에 영속적으로 쌓이는 사고 셀로판
```

### 2.1 X Memory Cube

문자를 하나의 거대한 평면 테이블로만 보지 않는다. 한 입력을 재귀 처리하면서 표현 단계마다 별도의 Unit 리스트를 만든다.

```text
                 Unit position ->
List 0    [엄] [마] [는] [집] [에] [있] [다]
List 1    [엄마] [는] [집] [에] [있다]
List 2    [엄마는] [집에 있다]
List 3    [엄마는 집에 있다]
  |
  v
recursive List
```

`List 1 = 단어`, `List 2 = 개념`처럼 의미 계층을 고정하지 않는다. 각 List는 한 입력을 재귀적으로 처리하면서 실제로 만들어진 Unit 배열이다.

이 2차원 `Unit position × List/depth` 셀로판이 새로운 입력마다 observation Time 방향으로 쌓여 하나의 큰 기억 큐브를 만든다.

```text
                       observation Time
                              ↑
                 +-------------------------+
입력 T3          | Unit position × List    |
                 +-------------------------+
입력 T2          | Unit position × List    |
                 +-------------------------+
입력 T1          | Unit position × List    |
                 +-------------------------+
```

X Memory Cube 안에서 반복되는 위치와 연결은 실제 CellElement 겹침만큼 진해진다. 반복 구조가 발견되면 다른 색상의 상위 Unit을 새 주소로 추가하고, 그 Unit 배열을 다음 List로 다시 입력한다.

```text
[엄] + [마] -> [엄마]
[엄마] + [는] -> [엄마는]
```

기존 문자, 낮은 List, 과거 입력 셀로판은 삭제하거나 다시 쓰지 않는다. `cell_elements`는 관찰된 연결을, `observed_layer_units`는 각 `sentenceId + passId` List의 정확한 Unit 칸을, `compositions`는 Unit의 재귀 구성을 보존한다.

### 2.2 List/depth 축

Memory Cube에서 List와 depth는 별도의 공간축으로 취급하지 않는다. 하나의 List는 한 입력을 재귀 처리해서 얻은 하나의 depth 층이며, 다음 List는 그 결과를 다시 입력해 만든 다음 depth 층이다.

```text
List/depth 0  [엄] [마] [는] [집] [에] [있] [다]
List/depth 1  [엄마] [는] [집] [에] [있다]
List/depth 2  [엄마는] [집에 있다]
```

DB의 `unit.depth`는 Unit이 자식 Unit으로부터 합성된 구조적 높이를 기록하는 생성 이력이다. 현재 구현에서 이 값이 `passId`와 항상 같지는 않더라도, Memory Cube에 `unit.depth`라는 별도의 공간 방향을 추가하지 않는다. 공간에서 사용하는 층 좌표는 `sentenceId + passId`, 즉 해당 입력의 List/depth 층이다.

### 2.3 Y Side Association Plane

새 입력이 들어오면 그 입력의 각 List/depth에서 현재 활성 Unit을 얻는다. 그 활성 Unit을 하나라도 포함한 과거 입력 셀로판만 X Memory Cube에서 선택한다. 활성 Unit만 잘라내는 것이 아니라, 선택된 셀로판의 전체 Unit List를 투영 대상으로 삼는다. 그래야 활성 Unit과 함께 관찰되었던 다른 Unit들이 연상으로 나타난다.

```text
현재 입력의 List/depth별 활성 Unit
  -> 활성 Unit을 포함한 observation Time 셀로판 선택
  -> 선택된 셀로판의 전체 Unit List 유지
  -> List/depth 방향에서 바라보되 Unit 길이와 시간 두께를 유지해 투영
  -> Y Side Association Plane 생성
```

따라서 Y 평면은 짧은 점선이 쌓인 면이 아니라, Unit 순서를 보존한 긴 선들이 observation Time 방향으로 겹쳐 두께를 이루는 면이다. Unit 방향을 정면으로 바라봐 각 List를 점으로 축약해서는 안 된다.

```text
선택된 T3  [철수] [는] [등산]   [을] [좋아한다]
선택된 T1  [철수] [는] [개발자] [다]
              ^ 선택 기준       ^ 함께 드러난 연상
```

셀로판 선택은 활성 Unit을 기준으로 하지만, Y 평면 투영은 선택된 셀로판 전체를 포함한다. Y는 별도 의미 유사도 계산 공간이 아니라 X Memory Cube에서 현재 입력과 관련된 기억만 골라 만든 측면 연상 평면이다.

역사적 Side View는 당시 저장된 List와 Unit을 보여주고, 활성 Side View는 현재 알려진 Unit을 과거 구조에 지연 투영한 결과를 보여준다.

### 2.4 Z Thought Stack

Y Side Association Plane에서 일부 Unit을 작업기억에 올리고, 그 연상 평면 위에 Thought 셀로판을 쌓으며 연결·분기·비교한다.

```text
[엄마]
   ↓
[출장]
   ├─ [숙박] -> [집에 없음]
   └─ [당일 복귀] -> [저녁 귀가]
```

Z축의 연결은 X Memory Cube의 Unit이나 과거 기억을 수정하지 않는다. Y 연상 평면을 재료로 실제로 거쳐 간 사고의 흔적이며, 완성된 Thought 셀로판은 append-only로 영속 저장된다.

```text
X Memory Cube       : Unit position × List/depth × observation Time
Y Association Plane : 활성 Unit을 포함한 셀로판 전체의 긴 Unit 선을 겹친 Side View
Z Thought Stack     : Y 평면 위에 쌓이는 영속 사고 기억
```

---

## 3. Memory, Recall, Thought

MAI의 기억과 사고는 다음 단계로 구분한다.

```text
Memory
  X Memory Cube에 Unit position × List/depth 셀로판이
  observation Time 방향으로 영속 누적된다.

Recall
  현재 입력의 List/depth별 활성 Unit을 포함한 셀로판 전체를 선택하고
  Y Side Association Plane에서 관련 Unit을 인출한다.

Thought
  인출된 Unit을 Y 연상 평면 위 Z Thought Stack에 배치하고,
  기존 Unit 구조를 변경하지 않은 채 연결, 분기, 비교, 제거를 반복한다.
```

Y Side Association Plane은 Thought 자체가 아니다. 현재 초점과 교차하는 X Memory Cube의 기억을 찾아 Z축에 놓을 후보와 그 연상 평면을 제공한다.

```text
입력 Unit 활성화
  -> Side View
  -> 관련 Unit 인출
  -> Z축에 배치하고 사고 연결 생성
  -> 배치된 Unit에서 새로운 초점 선택
  -> 다시 Side View
  -> 다음 Unit 배치
  -> 반복
```

---

## 4. 사고 구조 자체가 ThoughtPath다

ThoughtPath의 의미를 별도 객체에 복제하지 않는다.

```text
[엄마] -> [출장] -> [외부] -> [집에 없음]
```

위와 같이 Z축에 실제로 놓인 Unit의 연결과 분기 흔적 자체가 사고 경로다. 별도의 다음 구조를 만들어 같은 의미를 중복 저장하지 않는다.

```text
ThoughtPath(
  source="엄마",
  via=["출장", "외부"],
  target="집에 없음"
)
```

가설 역시 별도의 의미 점수나 문장으로 먼저 만들지 않는다. 동시에 가능한 구조를 분기하여 배치한다.

```text
             [숙박] -> [집에 없음]
           /
[출장] ---<
           \
             [당일 복귀] -> [저녁 귀가]
```

각 가지에서 다시 기억을 탐색하고, 현재 입력이나 기억의 연속 구조와 더 잘 이어지는 가지를 계속 활성화한다. 근거를 찾지 못한 가지는 흐려지거나 현재 작업기억에서 제외된다.

---

## 5. Unit과 Thought상의 출현

장기기억의 Unit 정체성과 Thought 공간에 나타난 한 번의 출현은 구분해야 한다.

동일한 Unit이 한 사고 안에서 서로 다른 가지와 맥락에 여러 번 나타날 수 있기 때문이다.

```text
[엄마] -> [출장] -> [집에 없음]
[엄마] -> [당일 복귀] -> [집에 있음]
```

두 위치의 `[엄마]`는 동일한 `unit_id`를 참조하지만 Thought 공간에서는 서로 다른 위치를 가진다.

초기 구현에서 필요한 최소 개념은 다음과 같다.

```text
ThoughtElement
- 어느 Thought에 속하는가
- 어떤 Unit을 참조하는가
- 어느 ThoughtElement에서 Z축으로 이어졌는가
- Z축의 어느 사고 위치에 놓였는가
- 현재 활성 상태인가
```

`ThoughtElement`는 새로운 의미 Unit이 아니다. 한 사고 과정에서 기존 Unit이 활성화된 위치를 나타내는 Z축 요소다. Thought가 끝난 뒤에도 사고 흔적으로 남을 수 있지만, 참조하는 X Memory Cube의 Unit 구조를 변경하지 않는다.

각 ThoughtElement는 참조하는 Unit을 통해 Y 연상 평면에 구조적 자국을 가진다.

```text
compositionFootprint
  Unit 자체가 어떤 하위 Unit과 위치로 구성됐는가

contextFootprint
  과거 sentenceId + passId 층에서 어떤 주변 Unit과 함께 있었는가
```

Z View는 Unit을 점 하나로만 보지 않는다. 두 Unit의 Y-plane footprint가 일부만 겹치면 겹친 부분에서만 Z층이 형성되고, 나머지 부분은 서로를 가리지 않는다. composition과 context의 겹침은 출처를 구분하여 보존한다.

---

## 6. 작업기억, 감쇠, 영속적 사고 흔적

Z축 전체와 현재 작업기억은 구분한다.

```text
Z축 전체
  과거와 현재에 실제로 전개된 ThoughtElement와 사고 연결의 영속적 흔적

Working Memory
  현재 Thought에서 활성화되어 탐색과 비교에 사용되는 일부
```

작업기억에서는 새로운 Unit이 들어올 때 가장 오래된 활성 요소를 먼저 제외할 수 있다. 그러나 이 작업기억 감쇠를 영속 Z View의 투명도와 동일시하지 않는다.

```text
새 ThoughtElement 추가
  -> 가장 오래된 활성 요소를 작업기억에서 제외
  -> 제외된 요소는 Z축 흔적에 계속 보존
```

작업기억에서 제외된 요소는 삭제되지 않으며 이후 Z View를 통해 역추적할 수 있다. 일반 Recall에 다시 기여할 수 있는지는 저장 상태에 따라 결정한다.

```text
density
  X Memory Cube에서 실제로 겹친 CellElement의 개수

opacity
  피드백에 따라 조정되는 X Memory Cube 기억의 지속적 가중치

activation
  현재 Thought의 작업기억에서 유지되는 활성 상태 또는 강도

thoughtDensity
  과거 Z축에서 같은 사고 구조가 반복되고 활성화된 실제 겹침
```

X Memory Cube와 Z Thought Stack은 모두 영속하지만 출처를 구분한다.

```text
observedMemory
  외부 관찰로 X Memory Cube에 쌓인 근거

thoughtMemory
  MAI가 과거에 전개한 Z축 사고 근거
```

Z축은 이후 Side View의 인출 대상이 될 수 있다. 반복된 사고 경로가 다시 활성화되면서 사고 습관, 관념, 성격이 형성될 가능성을 남긴다. 그러나 Z축 사고를 외부 관찰과 동일한 사실로 취급하거나 X Memory Cube의 관찰 기억으로 자동 승격하지 않는다.

Thought가 끝나면 역추적을 위해 탐색된 모든 Z축 가지를 상태와 함께 저장한다.

```text
conclusion
  최종 결론에 포함된 경로
  일반 Recall과 thoughtDensity에 기여

alternative
  구조적 지지가 있었지만 최종 선택되지 않은 경로
  외부 입력에서 직접 교차할 때만 다시 활성화 가능

rejected
  구조적 지지를 얻지 못한 가설
  역추적 전용이며 Recall과 thoughtDensity에 기여하지 않음

evicted
  작업기억 감쇠로 제외된 경로
  역추적 전용이며 Recall과 thoughtDensity에 기여하지 않음
```

따라서 저장 여부와 이후 사고에 영향을 줄 수 있는 활성 자격은 서로 다른 개념이다.

### 6.1 원본 Thought의 append-only 영속성

Thought가 끝나면 완성된 사고 셀로판을 기존 Z 기록 위에 새로 추가한다. 과거 Thought를 덮어쓰거나 미리 압축하지 않는다.

```text
실제 저장:
T1: [A] [ ] [ ]
T2: [ ] [B] [ ]
T3: [A] [ ] [ ]
T4: [ ] [ ] [C]
```

DB에는 각 Thought의 생성 순서, Unit 참조, 분기, 상태와 근거를 원본 그대로 남긴다. `ABC / A`는 저장 형식이 아니라 현재 Z View에서 계산되는 모습이다.

현재 구현은 `thoughts`, `thought_elements`, `thought_edges`에 원본 사고와 연결을 저장한다. 후속 구현에서는 같은 생성 시각에도 정확한 순서를 보장하는 단조 증가 `thoughtSequence`와 당시 footprint 근거를 추가한다.

새로운 A가 생겨도 과거 A의 opacity 행을 UPDATE하지 않는다.

```text
새 A 저장 전:
A 좌표 -> [과거 A]

새 A 저장 후:
A 좌표 -> [새 A, 과거 A]
```

### 6.2 좌표별 국소 Z 압축

Z축 깊이는 전역 Thought 번호나 경과 시간이 아니다. Y 연상 평면의 같은 좌표를 실제로 차지하는 Thought 자국만 최신순으로 모은 국소 깊이다.

```text
발생 순서:
T1: [A] [ ] [ ]
T2: [ ] [B] [ ]
T3: [A] [ ] [ ]
T4: [ ] [ ] [C]

현재 Z View:
[A] [B] [C]
[A]
```

B와 C는 A의 footprint를 덮지 않았으므로 A를 아래로 밀지 않는다. 빈 공간은 Z 깊이에 포함하지 않는다.

```text
localZDepth(coordinate, occurrence)
  = 같은 Y 평면 좌표를 실제로 차지한 더 최신 Thought occurrence의 수
```

부분적으로 겹치는 Unit은 교집합 좌표에서만 서로를 가린다.

```text
footprint(직업) = {a, b, c, d}
footprint(직종) = {b, c, e}

b, c 좌표: 직종 / 직업의 2층 스택
a, d 좌표: 직업만 존재
e 좌표: 직종만 존재
```

### 6.3 현재 투명도와 총 가시성

각 좌표의 현재 투명도는 원본에 저장된 고정된 나이가 아니라 조회 시 계산한 `localZDepth`에서 나온다.

```text
visibleOpacity
  = baseOpacity × decay(localZDepth)

coordinateVisibility
  = 같은 좌표에 쌓인 occurrence의 visibleOpacity 총합
```

가장 최근 A는 표면의 현재 활성 판단으로 보인다. 아래의 과거 A는 흐려지지만 삭제되지 않고 총 가시성에 계속 기여한다. 오래된 반복 A 여러 겹이 최신 한 겹과 비슷하거나 더 강한 존재감을 가질 수 있다.

시간이 흘렀거나 서로 겹치지 않는 다른 Thought가 생겼다는 이유만으로 과거 자국을 흐리게 하지 않는다.

```text
시간 경과                         != Z 깊이 증가
다른 좌표의 Thought 발생          != Z 깊이 증가
같은 Y 평면 자리에 새 자국이 겹침 == 해당 부분의 Z 깊이 증가
```

### 6.4 역사적 Thought와 현재 Thought 투영

과거 Thought가 실제로 사용한 Unit과 자국은 변경하지 않는다. 이후 새로운 상위 Unit을 학습하면 현재 지식으로 과거 Thought를 지연 투영할 수 있다.

```text
historicalThought
  당시 실제로 어떤 Unit과 footprint로 생각했는가

activeThoughtProjection
  현재 알려진 Unit으로 그 과거 Thought를 보면 무엇이 겹쳐 보이는가
```

이는 X Memory Cube의 역사적 관찰과 활성 투영을 분리한 기존 원칙을 Z축에도 동일하게 적용한 것이다.

---

## 7. 구조적 depth와 사고 위치

Unit의 depth와 사고의 전개 위치는 서로 다른 좌표다.

```text
unitDepth
  Unit이 자식으로부터 합성된 구조적 높이

thoughtPosition
  하나의 Thought 내부에서 해당 Unit 출현이 놓인 전개 위치

localZDepth
  현재 Z View에서 같은 Y 평면 좌표에 실제로 겹친 occurrence의 국소 순서
```

Unit depth, Thought 내부 전개 위치, 좌표별 localZDepth를 하나의 값으로 합치지 않는다.

---

## 8. Thought 탐색 정책

### 8.1 최초 초점

Side View에서 인출된 Unit 중 가장 진한 Unit을 최초 초점으로 사용한다. 진함은 별도의 의미 유사도 점수를 만들기보다 살아남은 구조의 실제 겹침을 우선한다.

```text
1. 살아남은 CellElement가 많다.
2. 같으면 opacity가 높은 기억에 포함된다.
3. 그래도 같으면 더 긴 연속 경로를 가진다.
```

현재 `_think_side_view()`는 최종 content 목록만 반환하므로 구현 시에는 `unitId`와 인출 근거를 함께 반환하도록 분리해야 한다.

### 8.2 한 칸 Side View

한 번의 Thought 단계에서는 현재 초점 Unit과 직접 연결된 한 칸만 탐색한다.

```text
현재 Unit -> 직접 연결된 Unit
  포함

현재 Unit -> 중간 Unit -> 다른 Unit
  현재 단계에서는 제외
```

두 칸 떨어진 Unit에 도달하려면 중간 Unit을 먼저 Z축에 배치한 뒤 다음 Thought 단계에서 다시 한 칸을 탐색해야 한다.

### 8.3 Z축 가설 연결

기억에 직접적인 연결 근거가 없어도 인출된 Unit을 Z축에서 임시 가설로 연결할 수 있다.

```text
가설 연결 생성
  -> 이후 한 칸 Side View에서 지지 구조 탐색
  -> 지지가 있으면 유지
  -> 지지가 없으면 감쇠
  -> 충분히 희미해지면 작업기억에서 제외
```

가설 연결은 X Memory Cube의 관찰 기억을 수정하지 않는다. Z축에는 실제로 거쳐 간 사고 흔적으로 남되, 지지를 얻은 연결과 얻지 못한 연결의 상태를 구분한다.

### 8.4 다음 초점

다음 Side View의 초점은 다음 순서로 선택한다.

```text
아직 탐색하지 않은 Unit
  -> 현재 구조에서 가장 진한 Unit
  -> 최근 Z축에 추가된 Unit
```

### 8.5 순환 방지

```text
- 같은 가지에서 동일 Unit의 즉시 재방문을 금지한다.
- 이미 탐색한 동일 경로를 다시 만들지 않는다.
- 서로 다른 가지에서는 같은 Unit을 다시 사용할 수 있다.
```

### 8.6 분기 유지와 비교

여러 가지가 가능하면 동시에 Z축에 펼친다. 하나의 종합 의미 점수를 만들지 않고 각 가지의 density, opacity, 연속 경로와 Z축 반복 근거를 그대로 유지한다. 명백히 지지되지 않는 가지부터 감쇠한다.

---

## 9. Thought 종료와 결론 구조

새로운 Unit이나 연결이 나타나지 않고 현재 활성 구조와 거의 동일한 구조가 반복되면 Thought를 종료한다.

```text
새 Unit 없음
+ 새 연결 없음
+ 동일하거나 거의 동일한 활성 구조 반복
= 사고가 더 이상 진행되지 않음
```

최대 Thought 단계와 최대 ThoughtElement 수는 의미적 종료 조건이 아니라 무한 순환을 막는 안전장치로 둔다.

종료 시 여러 가지가 남아 있다면 각 가지 전체의 진함 총합을 비교한다.

```text
branchDarkness
  = 해당 가지의 footprint가 Z View에서 차지하는 좌표별 총 가시성
```

개별 Unit 하나가 가장 진한 가지가 아니라 **가지 전체의 총합이 가장 진한 구조**를 최종 결론 구조로 선택한다. 동점 처리의 세부 규칙은 구현 단계에서 확정한다.

---

## 10. Thought에서 자연어로

최종 목표는 Z축 구조 자체를 노출하는 것이 아니라 자연어 응답을 만드는 것이다. 그러나 선택된 Unit의 content를 그대로 이어 붙이는 것만으로는 자연스러운 문장을 보장할 수 없다.

현재 depth는 고정 언어 계층이 아니므로 다음 등식은 성립하지 않는다.

```text
depth 0 = 문자
depth 1 = 단어
depth 2 = 의미
```

자연어 출력은 별도의 생성 지능이 아니라 **기억 기반 Expression View**로 정의한다.

```text
Thought
  무엇을 말할 것인가

Expression View
  그것을 과거에 관찰한 Unit 문장 구조로 어떻게 표현할 것인가
```

---

## 11. Expression View의 구조적 후보 탐색

최종 결론 구조의 각 Unit에서 다음 탐색을 수행한다.

```text
1. 결론 Unit에서 도달 가능한 더 깊고 뭉뚱그려진 Unit을 찾는다.
2. 그 깊은 Unit에서 결론 Unit과 같은 depth로 도달 가능한 모든 관련 Unit을 찾는다.
3. 관련 Unit들이 실제로 시작된 sentenceId + passId 층을 수집한다.
4. 어느 결론 Unit에서 파생된 후보인지 출처를 유지한다.
```

예를 들면 다음과 같다.

```text
결론 Unit [엄마]
  -> 더 깊은 Unit P
  -> 같은 depth 관련 Unit [아빠], [언니], [친구]
  -> 각 관련 Unit이 나타난 과거 sentence 층 수집
```

문자열이 비슷한 Unit을 찾는 것이 아니다. 같은 깊은 Unit에 도달할 수 있는 실제 composition 구조를 사용한다.

---

## 12. 결론 Unit이 표현 pass를 결정한다

표현 층은 pass 번호나 depth를 먼저 정한 뒤 Unit을 맞추는 방식으로 선택하지 않는다. 결론 그래프의 정확한 `unitId`와 관련 `unitId`가 실제로 등장한 층을 찾는다.

```text
결론에 [엄]이 있다
  -> [엄] Unit이 실제로 존재한 층을 후보로 사용

결론에 [엄마]가 있다
  -> [엄마] Unit이 실제로 존재한 층을 후보로 사용

결론에 [엄마는]이 있다
  -> [엄마는] Unit이 실제로 존재한 층을 후보로 사용
```

일반적으로 위 층이 각각 pass 0, 1, 2일 수 있지만 다음처럼 고정하지 않는다.

```text
unit.depth == passId
```

현재 MAI는 mixed-depth 배열을 허용하며 `unit.depth`와 `passId`는 독립적이다. 따라서 후보 표현 셀로판의 단위는 실제 출현 좌표인 다음 값이다.

```text
sentenceId + passId
```

서로 다른 pass의 Unit을 하나의 템플릿 층에 임의로 섞지 않는다.

---

## 13. Unit position 기반 문장 셀로판 중첩

문장 셀로판은 문자 위치나 화면상의 문자열 길이가 아니라 **Unit position**으로 겹친다.

```text
위치       0       1       2             3      4
문장 S1  [엄마]  [는]    [집]           [에]   [있다]
문장 S2  [아빠]  [는]    [회사]         [에]   [있다]
문장 S3  [아빠]  [는]    [직장동료집]   [에]   [있다]
```

`[집]`, `[회사]`, `[직장동료집]`은 content 길이와 관계없이 각각 한 칸을 차지한다. 따라서 `[에]`는 모든 문장에서 동일한 Unit position에 놓인다.

다음 문장들은 의미가 비슷하더라도 Unit의 개수와 순서가 다르므로 서로 다른 문장 템플릿이다.

```text
[엄마] [는] [내일] [집] [에] [없다]
[내일] [은] [엄마] [가] [집] [에] [없다]
[출장] [때문에] [엄마] [는] [집] [에] [없다]
```

표현 후보를 좌우로 이동하거나 순서를 재정렬하여 억지로 겹치지 않는다.

---

## 14. 문장 골격과 결론 Unit의 자리

수집한 `sentenceId + passId` 층을 Unit position별로 겹친다. 같은 칸에서 반복되는 Unit은 진한 문장 골격으로 나타나고, 관련 Unit들이 교체되어 나타나는 칸은 가변 자리로 나타난다.

```text
0번 칸        1번 칸   2번 칸               3번 칸  4번 칸
[엄마/아빠]   [는]     [집/회사/직장동료집] [에]     [있다]
```

```text
같은 position에서 같은 Unit이 반복됨
  -> 문장 골격

같은 position에서 같은 깊은 Unit에 연결된 관련 Unit들이 반복됨
  -> 결론 Unit을 놓을 수 있는 가변 자리
```

`은/는/이/가`, `is/a/the` 같은 기능 표현도 미리 사전으로 지정하지 않는다. Unit position별 실제 겹침이 충분히 진하면 문장 골격이나 칸의 경계로 드러난다.

각 결론 Unit 후보 문장의 출처를 유지하여 어느 칸에 해당 계열 Unit이 가장 진하게 겹치는지 계산한다.

```text
slotDensity(결론 Unit, position)
  = 해당 결론 Unit에서 depth 왕복으로 찾은 관련 Unit이
    그 position에 실제로 나타난 CellElement의 수
```

각 결론 Unit은 `slotDensity`가 가장 높은 가변 칸에 대응한다.

---

## 15. 기반 문장 선택과 다중 칸 교체

문장 골격과 결론 Unit의 자리를 찾은 뒤, 그 구조와 가장 진하게 겹치는 실제 `sentenceId + passId` 층을 기반 문장으로 선택한다.

```text
1. 결론 Unit의 자리를 많이 수용한다.
2. Unit position별 공통 골격이 많이 일치한다.
3. 일치하는 연속 Unit 경로가 길다.
4. 실제 겹침의 총합이 가장 진하다.
```

기반 문장에서 한 Unit만 교체하지 않는다. 결론 구조와 대응되는 모든 가변 칸을 현재 결론 Unit으로 교체한다.

```text
기반 문장:
[아빠] [는] [회사] [에] [있다]

결론 구조:
[엄마] / [집]

교체 결과:
[엄마] [는] [집] [에] [있다]
```

마지막에는 선택된 층의 Unit content를 position 순서대로 연결하여 자연어 문자열을 만든다. MAI가 관찰하지 않은 표현을 능숙하게 생성한다고 가정하지 않으며, 표현 능력도 실제 문장 셀로판의 누적과 겹침으로 성장한다.

---

## 16. 전체 처리 흐름과 API 경계

```text
Language Input
  -> Unit 활성화
  -> recall(): X Memory Cube의 Y Side View와 영속 Z축의 현재 투영
  -> think(): Y 연상 평면 위에서 Z축 연결 / 분기 / 작업기억 감쇠
  -> 원본 Thought 셀로판 append-only 영속화
  -> Unit의 composition/context footprint 복원
  -> 좌표별 빈 공간 제거와 localZDepth 계산
  -> 총 진함이 가장 큰 결론 구조
  -> express(): depth 왕복으로 관련 Unit과 문장 층 수집
  -> Unit position 기반 셀로판 중첩
  -> 기반 sentenceId + passId 층 선택
  -> 대응되는 모든 칸 교체
  -> respond(): Unit content를 연결한 자연어 응답
```

각 단계는 독립적으로 테스트하고 근거를 관찰할 수 있도록 분리한다.

```python
recallResult = engine.recall(...)
thoughtResult = engine.think(recallResult)
expressionResult = engine.express(thoughtResult)
response = engine.respond(expressionResult)
```

---

## 17. 남은 구현 세부사항

현재 구현은 Thought 원본의 Unit, 연결, 분기와 상태를 `thoughts`, `thought_elements`, `thought_edges`에 영속화한다. 아직 Y-plane footprint와 좌표별 localZDepth 계산은 구현하지 않았다.

다음 구조와 값은 후속 구현에서 추가하거나 설정값으로 둔다.

1. DB에서 보장하는 단조 증가 `thoughtSequence`
2. Thought 당시 composition/context footprint의 역사적 보존 형식
3. 현재 Unit 지식을 과거 Thought에 지연 투영하는 방법
4. 부분적으로 겹치는 footprint의 좌표 표현
5. `localZDepth`에 따른 투명도 감쇠 함수
6. 한 번에 최초 활성화할 Recall Unit의 최대 개수
7. 작업기억에서 동시에 활성화할 ThoughtElement의 최대 개수
8. 가설 연결을 지지됨 또는 지지되지 않음으로 판단하는 최소 구조 조건
9. Thought 구조를 거의 동일하다고 판단하는 비교 기준
10. `branchDarkness`가 같은 가지의 동점 처리
11. 여러 깊은 Unit에 동시에 도달할 때 후보 범위를 제한하는 방법
12. `slotDensity`가 같은 칸의 동점 처리
13. 표현할 수 있는 기반 문장이 발견되지 않았을 때의 대체 출력

이 값들은 임의의 의미 유사도 모델을 추가하기보다 실제 구조의 겹침과 역사적 연속성을 유지하는 방향으로 조정한다.
