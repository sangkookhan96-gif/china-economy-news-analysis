"""1회성 cron 래퍼: CNI perf 개선 검증 → 텔레그램 보고 → 자기 제거.

며칠 후(예약일) 실행되어 verify_perf_improvement.py 결과를 텔레그램으로 보내고,
crontab에서 자신을 삭제해 재실행되지 않게 한다.
"""
import os, sys, subprocess

ROOT = "/home/jeozeohan/vibe_temp/China Economy News Analysis"

# .env 로드(텔레그램 자격)
try:
    for line in open(os.path.join(ROOT, ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
except Exception:
    pass

out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "verify_perf_improvement.py")],
                     capture_output=True, text=True).stdout
with open(os.path.join(ROOT, "logs", "verify_perf_cron.log"), "a") as f:
    f.write(out + "\n")

# 요약 라인 추출(수정 전/후 + 판정)
tail = [l for l in out.splitlines() if any(k in l for k in ("수정 전", "수정 후", "중앙값", "개선", "악화", "유사"))]
msg = "📊 [CNI perf 검증] " + " | ".join(tail[-4:]) if tail else "📊 [CNI perf 검증] 결과 없음"
print(msg)
try:
    sys.path.insert(0, ROOT)
    from monitoring.notifier import send_telegram
    send_telegram(msg)
except Exception as e:
    print(f"telegram 실패: {e}")

# 자기 제거(1회성) — crontab에서 verify_perf_cron 라인 삭제
try:
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    new = "".join(l for l in cur.splitlines(keepends=True) if "verify_perf_cron" not in l)
    subprocess.run(["crontab", "-"], input=new, text=True)
    print("cron 자기 제거 완료")
except Exception as e:
    print(f"cron 제거 실패(수동 삭제 필요): {e}")
