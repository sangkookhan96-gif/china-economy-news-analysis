#!/bin/bash
# KG Daily Pipeline — runs after news collection
# Seoul 08:30 = UTC 23:30 previous day

set -e

cd /home/jeozeohan/vibe_temp/China\ Economy\ News\ Analysis

echo "=== KG Daily Pipeline Start: $(date) ==="

# ── Ollama health check ──
echo "Checking Ollama..."
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "Ollama not running. Starting..."
    ollama serve &
    sleep 10
    if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "FATAL: Ollama failed to start. Aborting."
        exit 1
    fi
fi
echo "Ollama OK"

# ── Step 1: Update KG with new reviews ──
echo "--- Step 1: KG Update ---"
python3 run_kg_update.py --limit 50 2>&1

# ── Step 2: Full pipeline (skip extract since step 1 did it) ──
echo "--- Step 2: Full Pipeline ---"
python3 run_full_pipeline.py --companies "BYD,CATL" --trend top10 --skip-extract 2>&1

# ── Step 3: Validate ──
echo "--- Step 3: Validate ---"
python3 -m src.kg.validate_pipeline 2>&1

# ── Step 4: Backup ──
echo "--- Step 4: Backup ---"
python3 -c "
import sqlite3, json, os
from datetime import datetime
conn = sqlite3.connect('data/news.db')
conn.row_factory = sqlite3.Row
d = datetime.now().strftime('%Y%m%d')
os.makedirs('backups', exist_ok=True)
for table in ['kg_entities', 'kg_events', 'kg_relations']:
    cond = \" WHERE status='active'\" if table == 'kg_entities' else ''
    with open(f'backups/{table}_{d}.jsonl', 'w') as f:
        for r in conn.execute(f'SELECT * FROM {table}{cond}'):
            f.write(json.dumps(dict(r), ensure_ascii=False) + '\n')
conn.close()
print('Backup complete')
"

# ── Step 5: CNI Auto Stage (요약 + KG, 번역 제외) ──
echo "--- Step 5: CNI Auto Stage ---"
python3 -c "from src.cni.cni_pipeline import run_auto_stage; run_auto_stage(50)" 2>&1

# ── Step 6: CNI Pipeline Selection + Field Generation ──
echo "--- Step 6: CNI Select + Generate ---"
python3 -c "
from src.cni.pipeline_service import set_pipeline_selected
from src.database.models import get_connection

conn = get_connection()
rows = conn.execute('''
    SELECT id FROM news
    WHERE analyzed_at IS NOT NULL
      AND original_content IS NOT NULL
      AND LENGTH(original_content) > 500
      AND pipeline_status IS NULL
      AND expert_review_status IN (\"none\", \"skipped\")
    ORDER BY importance_score DESC, collected_at DESC
    LIMIT 30
''').fetchall()
conn.close()

ids = [r['id'] for r in rows]
if ids:
    count = set_pipeline_selected(ids)
    print(f'CNI selected: {count} news')
else:
    print('No new news to select')
" 2>&1

python3 src/cni/generate_cni_fields.py --limit 30 2>&1

# ── Step 7: System Stabilization Check ──
echo "--- Step 7: System Stabilization ---"
python3 src/cni/system_stabilizer.py --fix 2>&1

echo "=== KG Daily Pipeline Complete: $(date) ==="
