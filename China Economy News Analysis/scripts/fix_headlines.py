"""잘린 헤드라인 82건 + 헤드라인 없는 46건 수정"""
import re, time, requests, logging, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.database.models import get_connection
from src.cni.translator import papago_translate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/fix_headlines.log", encoding="utf-8"),
        logging.StreamHandler(),
    ], force=True)
logger = logging.getLogger("fix_hl")

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:14b"

HEADLINE_PROMPT = """역할: 뉴스 데스크 편집자.
목표: 독자가 헤드라인만 보고도 기사 핵심을 파악할 수 있게 한다.

규칙:
1. 반드시 18자 이내 (공백 포함, 절대 초과 금지)
2. 한국어만 사용 (중국어 한자 금지)
3. "사실 + 대상 + 결과" 구조
4. 숫자, 고유명사 포함
5. 추상적 표현 금지 ('논란', '파장', '관심' 금지)
6. 고유명사: 중국 기업은 영문 브랜드명 또는 한국어 음차

예시:
- BYD 매출 8040억 위안 돌파
- CATL 시가총액 1조 위안 돌파
- 나스닥 3.2% 급락 5주 연속 하락
- EU 중국 풍력기업 조사 착수

뉴스 제목: {title}
요약: {summary}

18자 이내 순수 한국어 헤드라인만 출력:"""

def call_ollama(prompt):
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.3, "num_predict": 30,
                        "num_gpu": 35, "num_ctx": 2048}
        }, timeout=60)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Ollama: {e}")
        return ""

def clean_headline(hl):
    if not hl:
        return ""
    hl = re.sub(r'\*\*', '', hl)
    hl = hl.strip('"\'「」""')
    hl = re.sub(r'^\d+[\.\)）]\s*', '', hl)
    hl = hl.split('\n')[0].strip()
    # 영어 메타 제거
    hl = re.sub(r"^(Here'?s?\s+|This title|Note:|Title:).*?[:：]\s*", '', hl, flags=re.IGNORECASE).strip()
    return hl

# === 대상 수집 ===
conn = get_connection()

# 1) 잘린 헤드라인 (조사/접속사로 끝남)
incomplete_endings = ['을', '를', '의', '에', '과', '와', '으로', '이', '가', '은', '는',
                       '하여', '되어', '에서', '으며', '하고', '및', '한', '된', '할',
                       '위', '대', '중', '수', '것', '바', '데', '등']

rows_all = conn.execute('''
    SELECT DISTINCT n.id, n.card_headline, n.translated_title, n.original_title,
           n.summary, n.summary_zh, n.original_content
    FROM news n
    LEFT JOIN expert_reviews er ON n.id = er.news_id
    WHERE (n.pipeline_status = 'published' OR er.publish_status = 'published')
    ORDER BY n.updated_at DESC
''').fetchall()

truncated_ids = []
missing_ids = []

for r in rows_all:
    hl = (r['card_headline'] or '').strip()
    if not hl:
        missing_ids.append(r)
        continue
    for ending in incomplete_endings:
        if hl.endswith(ending) or (hl.split() and hl.split()[-1] == ending):
            truncated_ids.append(r)
            break

conn.close()

targets = truncated_ids + missing_ids
total = len(targets)
logger.info(f"=== 헤드라인 수정: {total}건 (잘림 {len(truncated_ids)} + 없음 {len(missing_ids)}) ===")

stats = {"ok": 0, "fail": 0}

for i, r in enumerate(targets, 1):
    nid = r["id"]
    title = r["translated_title"] or r["original_title"] or ""
    summary = r["summary"] or r["summary_zh"] or ""

    if not title and not summary:
        stats["fail"] += 1
        continue

    logger.info(f"[{i:03d}/{total}] #{nid}: {title[:40]}...")

    raw = call_ollama(HEADLINE_PROMPT.format(title=title[:60], summary=summary[:150]))
    if not raw:
        stats["fail"] += 1
        continue

    hl = clean_headline(raw)

    # 중국어가 있으면 Papago
    if re.search(r'[\u4e00-\u9fff]', hl):
        translated = papago_translate(hl)
        if translated:
            hl = translated

    # 여전히 중국어 있으면 skip
    if re.search(r'[\u4e00-\u9fff]', hl):
        stats["fail"] += 1
        logger.warning(f"  한자 잔존: {hl}")
        continue

    if not hl or len(hl) < 3:
        stats["fail"] += 1
        continue

    # 36자 제한 (자연스러운 절단)
    if len(hl) > 36:
        hl = hl[:36]

    conn = get_connection()
    conn.execute("UPDATE news SET card_headline=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (hl, nid))
    conn.commit()
    conn.close()

    stats["ok"] += 1
    logger.info(f"  → {hl} ({len(hl)}자)")
    time.sleep(0.1)

    if i % 50 == 0:
        logger.info(f"  --- PROGRESS: {i}/{total} | OK:{stats['ok']} FAIL:{stats['fail']} ---")

logger.info(f"""
{'='*60}
  HEADLINE FIX COMPLETE
  Total: {total}
  Fixed: {stats['ok']}
  Failed: {stats['fail']}
{'='*60}
""")
