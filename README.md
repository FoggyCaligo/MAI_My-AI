# MAI (My AI)

MAI는 llm을 대체하기 위한, 새로운 개념의 AI 구조 입니다.



## 구조

- **재귀적 다중 depth Unit 엔진 (`core/`)**
  - 입력에서 반복되는 구조를 발견하여 기존 Unit을 더 큰 Unit으로 재귀적으로 합성합니다.
  - depth는 단어·의미·개념처럼 고정된 언어 계층이 아니라 Unit이 형성된 구조적 높이입니다.
- **셀로판 구조 저장소 (`mai_core.db`)**
  - `units`, `compositions`, `cell_elements`에 Unit의 정체성, 합성 구조, 관찰된 연결과 역사적 좌표를 저장합니다.
- **Side View 기반 연상**
  - 현재 입력과 교차하는 과거 문장 층과 상위 Unit을 탐색하여 관련 기억을 인출합니다.
- **0~100점 opacity 피드백**
  - 고차원 기억 연결의 장기 가중치를 조절하며, 실제 CellElement 겹침 개수인 density와는 구분합니다.
- **Thought Space와 Expression View**
  - 인출된 Unit을 세 번째 축에서 전개하고 사고 흔적을 영속적으로 누적합니다.
  - Y 연상 평면은 현재 활성 Unit을 포함한 기억 셀로판 전체를 선택하고, Unit 길이를 보존한 긴 선들을 시간 방향으로 겹쳐 봅니다.
  - Z View에서는 같은 Y 연상 평면 자국에 실제로 겹친 Thought만 빈칸 없이 압축하여 국소 깊이와 투명도를 읽습니다.
  - 최종 결론 Unit에 맞는 과거 문장 층을 Unit position별로 겹쳐 자연어 표현 골격을 찾습니다.
  - 현재 구현은 구조를 검증하기 위한 첫 번째 실험 버전입니다.

## 실행

대화형 CLI를 실행합니다.

```bash
python -m app.main
```

또는 다음과 같이 실행합니다.

```bash
python app/cli.py
```

## 문서

- [현재 아키텍처](./docs/ARCHITECTURE.md)
- [셀로판 저장과 활성 투영](./docs/CELLOPHANE_STORAGE.md)
- [사고 공간과 자연어 표현 설계](./docs/THOUGHT_SPACE.md)
