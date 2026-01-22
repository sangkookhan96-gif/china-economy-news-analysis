#!/bin/bash
cd "$(dirname "$0")"
gnome-terminal -- bash -c "python3 mnist_train.py 2>&1 | tee output.log; echo ''; echo '=== 실행 완료 ==='; echo 'Enter를 누르면 창이 닫힙니다...'; read"
