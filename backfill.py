"""
백필 스크립트 — news.json의 pub_date 기준으로 특정 일자 아카이브 생성.

사용법:
  python backfill.py 2026-04-28        # 한 일자만
  python backfill.py 2026-04-26 2026-04-27 2026-04-28   # 여러 일자

각 일자별로 분석/dedup/top10/저장. 기존 archive 파일은 덮어쓰니 주의.
"""
import json, os, sys, importlib.util
from pathlib import Path
from email.utils import parsedate_to_datetime

spec = importlib.util.spec_from_file_location("nl", "newsletter.py")
nl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nl)

if len(sys.argv) < 2:
    sys.exit("사용법: python backfill.py YYYY-MM-DD [YYYY-MM-DD ...]")

TARGET_DATES = sys.argv[1:]
print(f"백필 대상: {TARGET_DATES}", flush=True)

# news.json 로드
raw = json.loads(Path("news.json").read_text(encoding="utf-8"))

# pub_date로 버킷
buckets = {d: {cat: [] for cat in raw} for d in TARGET_DATES}
for cat, articles in raw.items():
    for a in articles:
        try:
            d = parsedate_to_datetime(a.get("pub_date", "")).strftime("%Y-%m-%d")
        except Exception:
            continue
        if d in buckets:
            buckets[d][cat].append(a)

for date_str in TARGET_DATES:
    by_cat = buckets[date_str]
    total = sum(len(v) for v in by_cat.values())
    print(f"\n=== {date_str}: {total}건 ===", flush=True)
    if total == 0:
        print(f"  → 기사 없음, 스킵", flush=True)
        continue

    # 단일 호출 분석 (총 80건 미만이면 한 방에 OK)
    enriched = nl.analyze_with_claude(by_cat)

    # 3건 이상이면 dedup
    surviving = sum(len(v) for v in enriched.values())
    if surviving >= 3:
        enriched = nl.dedup_articles_with_claude(enriched)

    # 10건 이상이면 top-10
    enriched = nl.select_top_articles(enriched)

    # 저장
    nl.save_daily_archive(enriched, date_str=date_str)

# 모든 일자 저장 후 cleanup + 사이트 빌드
nl.cleanup_old_archives()
nl.build_site()
print("\nDONE", flush=True)
