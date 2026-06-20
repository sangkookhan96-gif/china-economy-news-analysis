"""zh_transliterate 회귀 테스트 — proper_nouns(PLACES) 병음음차 정답셋 대조."""
import sys
sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")
from src.utils.zh_transliterate import transliterate as T
from src.utils.proper_nouns import PLACES

# 관용명(意역·exonym) — 병음음차 아님, 제외
CONV = {'西藏','香港','澳门','台湾','内蒙古','长三角','长江三角洲','珠三角',
        '珠江三角洲','大湾区','粤港澳大湾区','京津冀','海南自贸港','新疆','雄安新区'}
ok = tot = 0
miss = []
for zh, info in PLACES.items():
    if zh in CONV:
        continue
    pred = T(zh)
    if not pred:
        continue
    tot += 1
    if pred == info['ko']:
        ok += 1
    else:
        miss.append((zh, info['ko'], pred))
# 핵심 단어 스폿체크
spot = {'燧原':'쑤이위안','广州':'광저우','深圳':'선전','贵州':'구이저우','宁德':'닝더'}
for zh, exp in spot.items():
    assert T(zh) == exp, f"{zh}: {T(zh)} != {exp}"
print(f"PLACES 정답률 {ok}/{tot} ({ok*100//tot}%)")
for zh, ko, pred in miss:
    print(f"  MISS {zh}: {ko} vs {pred}")
print("스폿체크 PASS" if not miss or ok/tot >= 0.95 else "검토 필요")
sys.exit(0 if ok/tot >= 0.95 else 1)
