import sys
sys.stdout.reconfigure(encoding='utf-8')

from core.engine import CognitiveEngine

e = CognitiveEngine("test_arch.db")

r1 = e.process_sentence("눈")
print("=== 눈 ===")
for pr in r1["pass_results"]:
    print(f"  Pass {pr['pass_id']}: {list(zip(pr['contents'], pr['depths']))}")

r2 = e.process_sentence("눈꽃")
print("=== 눈꽃 ===")
for pr in r2["pass_results"]:
    print(f"  Pass {pr['pass_id']}: {list(zip(pr['contents'], pr['depths']))}")

r3 = e.process_sentence("눈꽃의 계절")
print("=== 눈꽃의 계절 ===")
for pr in r3["pass_results"]:
    print(f"  Pass {pr['pass_id']}: {list(zip(pr['contents'], pr['depths']))}")

print("=== 생각 결과 ===", r3["thought_results"])

e.close()

import os
os.remove("test_arch.db")
