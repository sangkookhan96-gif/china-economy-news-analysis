#!/usr/bin/env python3
"""Papago Client ID/Secret을 .env에 안전하게 반영하고 즉시 검증한다.

- 비밀번호처럼 숨김 입력(getpass)으로 받아 채팅/쉘 히스토리에 노출되지 않는다.
- .env의 PAPAGO_CLIENT_ID / PAPAGO_CLIENT_SECRET 줄만 교체(나머지 보존).
- 교체 전 .env를 .env.bak2로 백업.
- 반영 후 라이브 zh→ko 호출로 인증 성공 여부를 확인한다.

사용:  ! python3 scripts/set_papago_key.py
"""
import os
import re
import shutil
import sys
from getpass import getpass
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
PAPAGO_URL = "https://papago.apigw.ntruss.com/nmt/v1/translation"


def _mask(s: str) -> str:
    return (s[:4] + "..." + s[-3:]) if len(s) > 8 else "(짧음/빈값)"


def set_line(text: str, key: str, value: str) -> str:
    pat = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pat.search(text):
        return pat.sub(line, text)
    # 없으면 끝에 추가
    return text.rstrip("\n") + "\n" + line + "\n"


def live_test(cid: str, sec: str) -> bool:
    try:
        r = requests.post(
            PAPAGO_URL,
            headers={"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": sec},
            data={"source": "zh-CN", "target": "ko", "text": "你好，世界"},
            timeout=15,
        )
        print(f"  HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code == 200:
            ko = r.json()["message"]["result"]["translatedText"]
            print(f"  ✅ 번역 성공: 你好，世界 → {ko}")
            return True
        return False
    except Exception as e:
        print(f"  ❌ 호출 오류: {e}")
        return False


def main():
    print("=== Papago 키 설정 (NCP 콘솔 → Papago Application → 인증 정보) ===")
    print("입력값은 화면에 표시되지 않습니다. 붙여넣기 후 Enter.\n")

    cid = getpass("Client ID (X-NCP-APIGW-API-KEY-ID): ").strip()
    sec = getpass("Client Secret (X-NCP-APIGW-API-KEY): ").strip()

    if not cid or not sec:
        print("중단: 빈 값입니다.")
        sys.exit(1)

    print(f"\n입력 확인 → ID {_mask(cid)} (len {len(cid)}), SECRET {_mask(sec)} (len {len(sec)})")

    print("\n[1/3] 반영 전 라이브 인증 테스트...")
    ok = live_test(cid, sec)
    if not ok:
        ans = input("\n인증 실패. 그래도 .env에 저장할까요? (y/N): ").strip().lower()
        if ans != "y":
            print("저장하지 않고 종료했습니다. 키 화면을 다시 확인해 주세요.")
            sys.exit(2)

    print("\n[2/3] .env 백업 후 갱신...")
    text = ENV.read_text(encoding="utf-8")
    shutil.copy2(ENV, ROOT / ".env.bak2")
    text = set_line(text, "PAPAGO_CLIENT_ID", cid)
    text = set_line(text, "PAPAGO_CLIENT_SECRET", sec)
    ENV.write_text(text, encoding="utf-8")
    print(f"  저장 완료: {ENV}  (백업: .env.bak2)")

    print("\n[3/3] 최종 검증 (translator 경유)...")
    sys.path.insert(0, str(ROOT))
    os.environ["PAPAGO_CLIENT_ID"] = cid
    os.environ["PAPAGO_CLIENT_SECRET"] = sec
    from src.cni.translator import papago_translate
    out = papago_translate("国务院发布新政策支持半导体产业发展")
    print(f"  결과: {out}")
    if out and "国" not in out:
        print("\n🎉 파파고 복구 완료. 서비스 재시작을 권장합니다:")
        print("    sudo systemctl restart news-scheduler.service")
    else:
        print("\n⚠️ 아직 정상이 아닙니다. 키 화면(인증 정보)을 다시 확인해 주세요.")


if __name__ == "__main__":
    main()
