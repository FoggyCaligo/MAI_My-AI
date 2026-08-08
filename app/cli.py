from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.engine import CognitiveEngine


def run_cli() -> None:
    print("=" * 60)
    print(" MAI (My AI) Cognitive Engine Interface")
    print(" 종류: 'exit' 또는 'q' 입력 시 종료")
    print("=" * 60)

    engine = CognitiveEngine()
    try:
        while True:
            try:
                user_input = input("\n입력> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n종료합니다.")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "q", "quit"):
                print("종료합니다.")
                break

            res = engine.process_sentence(user_input)

            # 1. 단어 구분 결과 (pass 0 output)
            words_str = " / ".join(res["word_segments"]) if res["word_segments"] else "(없음)"
            print(f"\n[단어 구분 결과]\n  {words_str}")

            # 2. 계층 분해 결과 (pass 0, 1, 2 …)
            print("\n[계층 분해 결과]")
            for pr in res["pass_results"]:
                depth_info = list(zip(pr["contents"], pr["depths"]))
                parts = " | ".join(
                    f"{c}(d{d})" for c, d in depth_info
                )
                print(f"  Pass {pr['pass_id']}: {parts}")

            # 3. 최종 생각 결과 (Side-View)
            thoughts = res["thought_results"]
            if thoughts:
                print(f"\n[최종 생각 결과 (Side-View 연상 단어)]\n  {', '.join(thoughts)}")
            else:
                print("\n[최종 생각 결과 (Side-View 연상 단어)]\n  (교차 연상된 학습 이력이 아직 없습니다)")

            conclusionContents = [
                engine.get_unit_content(unitId)
                for unitId in res["thought_result"]["conclusion_unit_ids"]
            ]
            if conclusionContents:
                print(f"\n[Z축 결론 구조]\n  {' -> '.join(conclusionContents)}")

            if res["response"]:
                print(f"\n[자연어 응답]\n  {res['response']}")

    finally:
        engine.close()


if __name__ == "__main__":
    run_cli()
