"""news.json을 청크로 나눠 Claude 병렬 호출 → 아카이브 저장 → Fursys-Insight 사이트 빌드"""
import json, os, re, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import importlib.util
spec = importlib.util.spec_from_file_location("nl", str(Path(__file__).with_name("newsletter.py")))
nl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nl)

import anthropic

CHUNK_SIZE = 10
MAX_WORKERS = 7
CACHE_DIR = Path(__file__).with_name("_cache_v3")

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not API_KEY:
    raise SystemExit("ANTHROPIC_API_KEY 환경변수 필요")


def load_news():
    return json.loads(Path(__file__).with_name("news.json").read_text(encoding="utf-8"))


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def analyze_chunk(client, articles_chunk, chunk_id):
    cache_file = CACHE_DIR / f"chunk_{chunk_id}.json"
    if cache_file.exists():
        try:
            return chunk_id, json.loads(cache_file.read_text(encoding="utf-8")), None
        except Exception:
            pass
    prompt = nl.ANALYSIS_PROMPT.format(
        articles_json=json.dumps(articles_chunk, ensure_ascii=False, indent=2)
    )
    resp = client.messages.create(
        model=nl.MODEL, max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    try:
        items = json.loads(text)
        cache_file.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        return chunk_id, items, None
    except Exception as e:
        return chunk_id, [], f"{e}: {text[:200]}"


def main():
    print("[A] news.json 로드", flush=True)
    by_category = load_news()

    flat = []
    article_index = []
    for cat, articles in by_category.items():
        for a in articles:
            flat.append({
                "id": len(flat), "category": cat,
                "title": a["title"],
                "matched_keyword": a.get("matched_keyword", ""),
                "description": a.get("description", ""),
            })
            article_index.append((cat, a))

    print(f"   총 {len(flat)}건", flush=True)

    chunks = list(chunked(flat, CHUNK_SIZE))
    CACHE_DIR.mkdir(exist_ok=True)
    print(f"[B] {len(chunks)}개 청크 병렬 분석", flush=True)

    client = anthropic.Anthropic(api_key=API_KEY)
    all_analyses = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(analyze_chunk, client, c, i) for i, c in enumerate(chunks)]
        for f in as_completed(futures):
            cid, items, err = f.result()
            if err:
                print(f"   ! 청크 {cid}: {err[:160]}", flush=True)
            else:
                print(f"   ✓ 청크 {cid}: {len(items)}건", flush=True)
                for it in items:
                    if isinstance(it, dict) and "id" in it:
                        all_analyses[it["id"]] = it

    enriched = nl._merge_analyses(by_category, article_index, all_analyses)
    surviving = sum(len(v) for v in enriched.values())
    print(f"[C] 병합 ({len(all_analyses)}분석, {surviving}건 살아남음)", flush=True)

    # 아카이브 저장 + 오래된 것 정리 + 사이트 빌드
    enriched = nl.dedup_articles_with_claude(enriched)
    enriched = nl.select_top_articles(enriched)
    nl.save_daily_archive(enriched)
    nl.cleanup_old_archives()
    nl.build_site()
    nl.send_to_slack(enriched)
    print("DONE.", flush=True)


if __name__ == "__main__":
    main()
