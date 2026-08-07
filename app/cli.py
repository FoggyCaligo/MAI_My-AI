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

            # 4. 피드백 점수 입력 (0~100점, 사고 레이어에만 반영)
            if res["sentence_id"]:
                try:
                    fb = input("\n피드백 점수를 입력하세요 (0~100점, 엔터 = 스킵): ").strip()
                    if fb:
                        score = float(fb)
                        new_op = engine.apply_feedback(res["sentence_id"], score)
                        print(
                            f"  [피드백 반영 완료] {score}점 → "
                            f"사고 레이어 평균 투명도 {new_op:.3f}"
                        )
                except ValueError:
                    print("  [알림] 숫자가 아닌 값이 입력되어 스킵합니다.")

    finally:
        engine.close()


if __name__ == "__main__":
    run_cli()
