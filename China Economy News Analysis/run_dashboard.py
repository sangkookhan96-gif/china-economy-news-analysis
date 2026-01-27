#!/usr/bin/env python3
"""Run the expert dashboard."""

import subprocess
import sys
from pathlib import Path

def main():
    dashboard_path = Path(__file__).parent / "src" / "ui" / "expert_dashboard.py"

    cmd = [sys.executable, "-m", "streamlit", "run", str(dashboard_path),
           "--server.headless", "true"]

    print("🚀 전문가 대시보드를 시작합니다...")
    print("브라우저에서 http://localhost:8501 을 열어주세요.")
    print("종료하려면 Ctrl+C를 누르세요.\n")

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n대시보드가 종료되었습니다.")

if __name__ == "__main__":
    main()
