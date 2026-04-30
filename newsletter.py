"""
Fursys-Insight 산업 인사이트 사이트
====================================
카드 그리드 + 모달 UI · 브랜드 자동 태깅 · 일자별 7일 보존 · 하루 10건 캡
"""

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

try:
    import anthropic
except ImportError:
    raise SystemExit("'pip install anthropic' 실행 필요")


# ============================ 설정 ============================

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")  # Incoming Webhook URL
SITE_URL = os.environ.get("FURSYS_INSIGHT_URL", "")  # 사내 호스팅 URL (선택)
MODEL = "claude-haiku-4-5"

KEYWORDS = {
    "경쟁사 동향 - 사무/B2B": [
        "한샘 사무가구", "현대리바트 사무가구",
        "오피스 가구 브랜드", "에넥스 사무",
    ],
    "경쟁사 동향 - 가정/리빙": [
        "한샘 리빙", "현대리바트 가정용", "신세계까사",
        "에이스침대", "시몬스침대",
    ],
    "일하는 방식·워크 트렌드": [
        "하이브리드 워크", "재택근무 트렌드",
        "오피스 디자인 트렌드", "사무공간 변화",
        "주 4일제 근무", "일하는 방식 변화",
        "사무용 의자 트렌드", "오피스 의자 인체공학",
    ],
    "홈리빙·소비자 트렌드": [
        "홈오피스 트렌드", "1인가구 인테리어",
        "리빙 트렌드", "침대 매트리스 시장", "소파 시장",
        "게이밍 의자 시장", "허리 의자",
    ],
    "제조·원자재 환경": [
        "가구 제조업 동향", "MDF 가격", "원목 가격",
        "가구 수출 통계", "목재 수입",
    ],
    "글로벌 시그널": [
        "future of work", "office design trends",
        "global furniture market 2026",
    ],
    # === 신규 카테고리 ===
    "인구·세대 변화": [
        "1인가구 통계", "고령화 소비",
        "잘파세대 라이프스타일", "MZ세대 가구",
        "출산율 가구 시장",
    ],
    "AI·자동화와 일의 변화": [
        "AI 에이전트 업무", "코파일럿 생산성",
        "RPA 사무 자동화", "ChatGPT 화이트칼라",
        "AI 시대 사무직",
    ],
    "물류 - 가구 라스트마일·설치": [
        "가구 라스트마일", "방문 설치 서비스",
        "가구 약속배송", "대형 화물 배송",
    ],
    "물류 - 이커머스 물류 재편": [
        "쿠팡 로켓설치", "네이버 도착보장",
        "이커머스 가구 배송", "쿠팡 풀필먼트",
    ],
    "물류 - 자동화·로봇·창고": [
        "물류 로봇", "자율주행 AGV",
        "스마트 물류센터", "창고 자동화",
    ],
    "자사 브랜드 소식": [
        "퍼시스 신제품", "퍼시스 매장",
        "일룸 신제품", "일룸 캠페인", "일룸 팝업", "일룸 콜라보",
        "시디즈 신제품", "시디즈 의자",
        "알로소 소파 신제품", "알로소 매장",
        "슬로우베드 매트리스 신제품",
        "레터스 가구 시공",
    ],
    "AI 도구·동향": [
        "Claude AI 신기능", "Anthropic 클로드",
        "ChatGPT 새 기능", "OpenAI 신제품",
        "Gemini 구글 AI",
        "Cursor AI 코딩", "GitHub Copilot",
        "AI 디자인 도구", "Figma AI",
        "바이브 코딩",
    ],
    "기업 이사·리뉴얼·성장 시그널": [
        # 이사·리뉴얼·클리닝 (레터스 핵심 영역)
        "사무실 이전", "사옥 이전 트렌드",
        "오피스 리노베이션", "사무공간 리뉴얼",
        "사무가구 클리닝", "오피스 청소 서비스",
        "리퍼브 가구",
        # 투자유치 — 사옥 확장 잠재고객 시그널
        "시리즈A 투자유치", "시리즈B 투자유치", "시리즈C 투자유치",
        "유니콘 기업 사옥",
    ],
}

MAX_ARTICLES_PER_KEYWORD = 3
MAX_TOTAL_ARTICLES = 80
RETENTION_DAYS = 7
MAX_INSIGHTS_PER_DAY = 20

# 출력 위치: 환경변수로 override 가능 (Vercel 배포 시 public/ 으로 지정)
OUTPUT_DIR = Path(os.environ.get("FURSYS_PUBLIC_DIR", str(Path(__file__).parent)))
ARCHIVE_DIR = OUTPUT_DIR / "archive"
SITE_PATH = OUTPUT_DIR / "index.html"


# ============================ 1. 뉴스 수집 ============================

def fetch_google_news(keyword, max_items=3):
    encoded = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    items = []
    for item in root.findall(".//item")[:max_items]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = (item.findtext("description") or "").strip()
        desc_clean = re.sub(r"<[^>]+>", " ", desc)
        desc_clean = re.sub(r"\s+", " ", desc_clean).strip()
        source = ""
        if " - " in title:
            parts = title.rsplit(" - ", 1)
            title, source = parts[0].strip(), parts[1].strip()
        items.append({
            "title": title, "link": link, "pub_date": pub,
            "source": source, "description": desc_clean[:400],
        })
    return items


def collect_all_news():
    print("[1/4] 뉴스 수집...", flush=True)
    tasks = [(cat, kw) for cat, kws in KEYWORDS.items() for kw in kws]
    results = {}
    def _fetch(t):
        cat, kw = t
        try: return cat, kw, fetch_google_news(kw, MAX_ARTICLES_PER_KEYWORD), None
        except Exception as e: return cat, kw, [], str(e)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_fetch, t) for t in tasks]
        for f in as_completed(futures):
            cat, kw, articles, err = f.result()
            if not err: results[kw] = (cat, articles)

    seen_links, seen_titles = set(), set()
    by_category = {cat: [] for cat in KEYWORDS}
    for kw, payload in results.items():
        cat, articles = payload
        for a in articles:
            if a["link"] in seen_links: continue
            tk = re.sub(r"\s+", "", a["title"])[:40]
            if tk in seen_titles: continue
            seen_links.add(a["link"]); seen_titles.add(tk)
            a["matched_keyword"] = kw; a["category"] = cat
            by_category[cat].append(a)
    total = sum(len(v) for v in by_category.values())
    print(f"   → {total}건", flush=True)
    return by_category


# ============================ 2. Claude 분석 (브랜드 태깅 포함) ============================

ANALYSIS_PROMPT = """당신은 퍼시스그룹 임직원을 위한 '산업 인사이트 큐레이터'입니다.

[목적] 임직원이 출근/점심/퇴근 시간에 가볍게 "어, 이거 흥미롭네"라고 곱씹으면서
우리 일·우리 산업·우리 회사를 약간 다르게 보게 만드는 가벼운 콘텐츠.
무거운 전략 제언/회의 자료체 금지. 동료와 점심에 얘기 나눌 만한 톤.

[다루는 영역]
- 외부 인사이트: 경쟁사·시장·일하는 방식·물류·인구·AI 트렌드
- 자사 브랜드 소식: 퍼시스/일룸/시디즈/알로소/슬로우베드/레터스의 가벼운 소식
  (신제품, 매장/팝업, 캠페인, 콜라보, 디자인 발표, 마케팅 활동 등)
- AI 가이드: AI 도구·신제품·산업 변화. **비개발자가 이해할 수 있게 일상 언어로 풀어서**.

[퍼시스그룹 브랜드]
- 퍼시스 (B2B): 사무가구·오피스 인테리어
- 일룸 (B2C): 가정용 가구·인테리어
- 시디즈 (B2C): 의자 (사무용/게이밍/일반)
- 알로소 (B2C): 소파·거실 가구
- 슬로우베드 (B2C): 침대·매트리스·토퍼
- 레터스/바로스: 기업 이사·사무공간 리뉴얼·사무가구 클리닝·유지보수 등 B2B 가구 서비스

[기사 목록]
{articles_json}

각 기사에 대해 JSON 객체 배열로 반환:
- "id": 기사 ID
- "skip": true/false. 다음이면 true:
  * 가구·리빙·일하는 방식·물류·AI 산업과 무관 (동명이인, 무관 회사 등)
  * 단순 단신·광고성·뻔한 정보
  * **자사가 주제인 기사 중**: 경영승계, 임원 인사, 실적/재무 발표, 주가/공시, M&A, 지배구조 → skip
    단, 자사 브랜드의 신제품/매장/팝업/캠페인/콜라보/디자인/마케팅 소식은 살릴 것.
- "brands": 가장 관련 있는 브랜드 1~3개 (배열):
  ["퍼시스", "일룸", "시디즈", "알로소", "슬로우베드", "레터스"]
  AI/일반 산업 동향이라 직접 관련 없으면 빈 배열 [].
  * 기업 투자유치(시리즈 A/B/C) 기사는 사옥 확장·이전 잠재 고객 신호 → ["퍼시스", "레터스"] 태그
  * 사무실 이전·리뉴얼 기사 → ["퍼시스", "레터스"]
  * 가구 클리닝/유지보수 기사 → ["레터스"]
- "importance": "상"/"중"/"하"
- "summary": 3~4문장 요약. 핵심 사실/맥락 잘 전달.
  **AI 카테고리 기사는 전문 용어를 풀어서 비개발자가 이해할 수 있게 작성**.
  예: "MCP" → "AI 도구가 다른 프로그램이랑 대화할 수 있게 해주는 표준",
      "RAG" → "외부 자료를 읽어와서 답을 만드는 방식".
- "insight": 1~2문장. 가볍게 "어, 이거 흥미롭네" 곱씹어볼 만한 관점.
  동료와 점심 자리에서 얘기 나눌 톤. 무거운 제언/명령형 금지.

[좋은 insight 톤 예시]
- "1인가구가 1000만 명을 넘었네요. 우리 신제품에 '1인용' 라인업이 충분히 두꺼운지 자연스럽게 궁금해집니다."
- "AI가 디자인까지 자동으로 만들어주는 시대인데, 디자이너들이 일하는 방식이 어떻게 바뀔지 한번 그려볼 만하네요."
- "쿠팡이 가구 설치까지 직접 해주기 시작했다면, 우리 시공팀의 차별점은 뭘까 한번 떠올려볼 만한 주제 같아요."
- "Stitch 같은 AI 디자인 도구가 Figma를 흔드는 걸 보면, 도구 자체가 산업 지형을 한번에 뒤집을 수 있다는 게 새삼 느껴집니다."

[금지 표현]
- "주목할 필요가 있다", "검토가 필요하다", "면밀히 살펴봐야 한다"
- "변화에 발맞춰야 한다", "전략 재구성이 필요하다", "관심을 가져야 한다"
- 호소형/명령형 / 회의자료체
- 기사 사실의 단순 재진술

JSON 배열만 출력. 코드 블록 없이.
"""


def analyze_with_claude(by_category):
    print("[2/4] Claude 분석...", flush=True)
    flat, article_index = [], []
    for cat, articles in by_category.items():
        for a in articles:
            flat.append({"id": len(flat), "category": cat, "title": a["title"],
                         "matched_keyword": a["matched_keyword"], "description": a["description"]})
            article_index.append((cat, a))
    if not flat: return {cat: [] for cat in by_category}
    if len(flat) > MAX_TOTAL_ARTICLES:
        flat = flat[:MAX_TOTAL_ARTICLES]
        article_index = article_index[:MAX_TOTAL_ARTICLES]
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = ANALYSIS_PROMPT.format(articles_json=json.dumps(flat, ensure_ascii=False, indent=2))
    resp = client.messages.create(model=MODEL, max_tokens=8000, messages=[{"role": "user", "content": prompt}])
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    try: analyses = json.loads(text)
    except json.JSONDecodeError: analyses = []
    abi = {a.get("id"): a for a in analyses if isinstance(a, dict)}
    return _merge_analyses(by_category, article_index, abi)


CATEGORY_TYPE = {
    "자사 브랜드 소식": "internal",
    "AI 도구·동향": "ai",
}

def get_article_type(category):
    return CATEGORY_TYPE.get(category, "external")


def _merge_analyses(by_category, article_index, analyses_by_id):
    enriched = {cat: [] for cat in by_category}
    for i, (cat, original) in enumerate(article_index):
        ana = analyses_by_id.get(i, {})
        if ana.get("skip", False): continue
        item = dict(original)
        item["importance"] = ana.get("importance", "중")
        item["summary"] = ana.get("summary", "")
        item["insight"] = ana.get("insight", "")
        item["brands"] = ana.get("brands", []) if isinstance(ana.get("brands"), list) else []
        item["type"] = get_article_type(cat)
        enriched[cat].append(item)
    order = {"상": 0, "중": 1, "하": 2}
    for cat in enriched:
        enriched[cat].sort(key=lambda x: order.get(x.get("importance"), 3))
    return enriched


# ============================ 2-2. 중복 기사 제거 ============================

DEDUP_PROMPT = """다음 기사 목록에서 '같은 사건'을 다루는 기사들을 클러스터로 묶어주세요.

[기사 목록]
{articles_json}

[같은 사건 정의]
- 동일 회사·기관의 동일 발표/사건/이벤트를 여러 매체가 각자 보도한 경우 → 같은 사건
- 단순히 같은 키워드만 공유하는 다른 사건은 → 다른 사건

[출력]
JSON 배열만 반환. 모든 ID가 정확히 하나의 cluster에 포함:
[{{"cluster": 0, "ids": [0, 3, 5]}}, {{"cluster": 1, "ids": [1]}}]

코드 블록 없이 JSON 배열만.
"""


def dedup_articles_with_claude(enriched):
    flat_ref, flat_for_llm = [], []
    for cat, arts in enriched.items():
        for a in arts:
            gid = len(flat_ref)
            flat_ref.append((cat, a))
            flat_for_llm.append({
                "id": gid, "title": a.get("title", ""),
                "summary": (a.get("summary", "") or a.get("description", ""))[:100],
            })
    if len(flat_for_llm) < 3: return enriched

    print(f"[2-2] 중복 제거 ({len(flat_for_llm)}건)...", flush=True)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = DEDUP_PROMPT.format(articles_json=json.dumps(flat_for_llm, ensure_ascii=False, indent=2))
    try:
        resp = client.messages.create(model=MODEL, max_tokens=4000,
                                      messages=[{"role": "user", "content": prompt}])
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n", "", text)
            text = re.sub(r"\n```$", "", text)
        clusters = json.loads(text)
        if not isinstance(clusters, list): raise ValueError("not list")
    except Exception as e:
        print(f"   ! 클러스터링 실패: {e}", flush=True)
        return enriched

    importance_order = {"상": 0, "중": 1, "하": 2}
    keep_ids, seen, sizes = set(), set(), []
    for cluster in clusters:
        if not isinstance(cluster, dict): continue
        ids = [i for i in cluster.get("ids", []) if isinstance(i, int) and 0 <= i < len(flat_ref)]
        if not ids: continue
        sizes.append(len(ids)); seen.update(ids)
        ranked = sorted(ids, key=lambda i: importance_order.get(flat_ref[i][1].get("importance"), 3))
        keep_ids.add(ranked[0])
    for gid in range(len(flat_ref)):
        if gid not in seen: keep_ids.add(gid)
    if not keep_ids: return enriched

    new_enriched = {cat: [] for cat in enriched}
    for gid, (cat, art) in enumerate(flat_ref):
        if gid in keep_ids: new_enriched[cat].append(art)
    removed = len(flat_ref) - len(keep_ids)
    multi = sum(1 for s in sizes if s > 1)
    print(f"   → {removed}건 제거 ({multi}개 클러스터)", flush=True)
    return new_enriched


LEADS_CATEGORY = "기업 이사·리뉴얼·성장 시그널"
LEADS_MIN = 2  # 사옥 이전·투자유치 최소 보장 건수


def select_top_articles(enriched, max_count=MAX_INSIGHTS_PER_DAY):
    """타입별 쿼터제 + 영업 시그널(사옥 이전·투자유치) 최소 2건 보장."""
    flat = [(cat, a) for cat, arts in enriched.items() for a in arts]
    if len(flat) <= max_count: return enriched

    importance = {"상": 0, "중": 1, "하": 2}
    def sort_key(item):
        cat, a = item
        return (
            0 if a.get("insight") else 1,
            importance.get(a.get("importance"), 3),
        )
    flat.sort(key=sort_key)

    QUOTAS = {"external": 12, "internal": 4, "ai": 4}
    by_type = {"external": [], "internal": [], "ai": []}
    for item in flat:
        t = item[1].get("type", "external")
        by_type.setdefault(t, []).append(item)

    # 영업 시그널 우선 확보 (external 카테고리에서 LEADS_CATEGORY인 것)
    leads_pool = [item for item in by_type.get("external", []) if item[0] == LEADS_CATEGORY]
    leads_chosen = leads_pool[:LEADS_MIN]
    leads_chosen_ids = {id(item[1]) for item in leads_chosen}

    selected, leftover = list(leads_chosen), []

    # external 잔여 슬롯 채우기 (12 - leads_chosen 만큼)
    ext_quota = QUOTAS["external"] - len(leads_chosen)
    ext_remaining = [item for item in by_type.get("external", []) if id(item[1]) not in leads_chosen_ids]
    selected.extend(ext_remaining[:ext_quota])
    leftover.extend(ext_remaining[ext_quota:])

    # internal, ai 정상 처리
    for t in ["internal", "ai"]:
        items = by_type.get(t, [])
        selected.extend(items[:QUOTAS[t]])
        leftover.extend(items[QUOTAS[t]:])

    leftover.sort(key=sort_key)
    while len(selected) < max_count and leftover:
        selected.append(leftover.pop(0))
    selected = selected[:max_count]

    selected_ids = {id(a) for _, a in selected}
    new_enriched = {cat: [] for cat in enriched}
    for cat, arts in enriched.items():
        for a in arts:
            if id(a) in selected_ids:
                new_enriched[cat].append(a)

    counts = {"external": 0, "internal": 0, "ai": 0}
    leads_count = 0
    for cat, a in selected:
        counts[a.get("type", "external")] = counts.get(a.get("type", "external"), 0) + 1
        if cat == LEADS_CATEGORY:
            leads_count += 1
    print(f"[2-3] {len(selected)}건 (외부 {counts['external']} / 자사 {counts['internal']} / AI {counts['ai']} · 영업시그널 {leads_count})", flush=True)
    return new_enriched


# ============================ 3. 아카이브 ============================

def save_daily_archive(enriched, date_str=None):
    ARCHIVE_DIR.mkdir(exist_ok=True)
    if date_str is None: date_str = datetime.now().strftime("%Y-%m-%d")
    path = ARCHIVE_DIR / f"{date_str}.json"
    payload = {"date": date_str, "generated_at": datetime.now().isoformat(timespec="seconds"),
               "articles": enriched}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/4] 저장: {path.name}", flush=True)
    return path


def cleanup_old_archives(retention_days=RETENTION_DAYS):
    if not ARCHIVE_DIR.exists(): return
    cutoff = datetime.now().date() - timedelta(days=retention_days)
    for f in ARCHIVE_DIR.glob("*.json"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.json$", f.name)
        if not m: continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            if d < cutoff:
                try: f.unlink()
                except Exception: pass
        except ValueError: pass


def load_recent_archives(days=RETENTION_DAYS):
    if not ARCHIVE_DIR.exists(): return []
    cutoff = datetime.now().date() - timedelta(days=days)
    entries = []
    for f in sorted(ARCHIVE_DIR.glob("*.json"), reverse=True):
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.json$", f.name)
        if not m: continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            if d < cutoff: continue
            entries.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception: pass
    return entries


# ============================ 4. 사이트 빌드 (카드 + 모달 + 브랜드 필터) ============================

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def fmt_date_long(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{d.year}.{d.month:02d}.{d.day:02d} {WEEKDAY_KO[d.weekday()]}요일"


def fmt_date_short(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{d.month:02d}.{d.day:02d} {WEEKDAY_KO[d.weekday()]}"


SITE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fursys-Insight · {today_long}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@400;600;700;900&family=Noto+Sans+KR:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #fafaf7;
    --ink: #1a1a1a;
    --ink-soft: #4a4a4a;
    --ink-light: #888;
    --rule: #e5e3dd;
    --accent: #b91c1c;
    --insight: #c9a961;
    --serif: 'Noto Serif KR', 'Nanum Myeongjo', Georgia, serif;
    --sans: 'Noto Sans KR', -apple-system, 'Segoe UI', sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font-family: var(--sans); line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  a { color: inherit; }
  .wrap { max-width: 760px; margin: 0 auto; padding: 56px 24px 80px; }

  /* Masthead - minimal */
  .mast { margin-bottom: 36px; padding-bottom: 18px; border-bottom: 1px solid var(--rule); }
  .mast h1 {
    font-family: var(--serif); font-weight: 900;
    font-size: 26px; letter-spacing: -.6px;
    margin: 0 0 5px; color: var(--ink);
  }
  .mast h1 .accent { color: var(--accent); }
  .mast .date {
    font-family: var(--sans); font-size: 13px;
    color: var(--ink-light); letter-spacing: .3px;
  }

  /* Filter - compact dropdowns */
  .filter {
    display: flex; gap: 8px;
    margin-bottom: 8px; align-items: center;
    font-family: var(--sans);
  }
  .filter select {
    appearance: none; -webkit-appearance: none; -moz-appearance: none;
    background: transparent;
    border: 1px solid var(--rule);
    padding: 6px 28px 6px 12px;
    font-family: inherit; font-size: 12px;
    color: var(--ink-soft);
    border-radius: 0;
    cursor: pointer;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'><path fill='%23888' d='M0 0l5 6 5-6z'/></svg>");
    background-position: right 10px center;
    background-repeat: no-repeat;
    background-size: 8px;
  }
  .filter select:focus { outline: none; border-color: var(--ink); color: var(--ink); }
  .filter select:hover { border-color: var(--ink); }
  .filter .vcount {
    margin-left: auto; color: var(--ink-light);
    font-size: 11px; letter-spacing: .5px;
  }

  /* Entries - editorial column */
  main { padding-top: 8px; }
  .entry {
    display: grid; grid-template-columns: 50px 1fr;
    gap: 24px; padding: 32px 0;
    border-bottom: 1px solid var(--rule);
  }
  .entry:last-child { border-bottom: none; }

  .entry-num {
    font-family: var(--serif); font-weight: 300;
    font-size: 30px; color: #c5c2b8;
    line-height: 1; padding-top: 4px;
    letter-spacing: -1px;
  }
  .entry-body { min-width: 0; }

  .entry-tags {
    margin-bottom: 8px;
    font-family: var(--sans); font-size: 10px;
    color: var(--ink-light); letter-spacing: 1.5px;
    text-transform: uppercase;
  }
  .entry-tags .brand { color: var(--ink); font-weight: 700; }
  .entry-tags .type-internal { color: #047857; font-weight: 700; }
  .entry-tags .type-ai { color: #6d28d9; font-weight: 700; }
  .entry-tags .imp-high { color: var(--accent); font-weight: 700; }
  .entry-tags .sep { color: #ccc; margin: 0 6px; }

  .headline {
    font-family: var(--serif); font-weight: 700;
    font-size: 22px; line-height: 1.4;
    margin: 0 0 14px; letter-spacing: -.3px;
    color: var(--ink);
  }
  .headline a { text-decoration: none; }
  .headline a:hover {
    text-decoration: underline;
    text-decoration-thickness: 1px;
    text-underline-offset: 4px;
  }

  .byline {
    font-family: var(--sans); font-size: 11px;
    color: var(--ink-light); letter-spacing: .8px;
    margin-bottom: 18px;
    text-transform: uppercase;
  }
  .byline a {
    color: var(--accent); font-weight: 600;
    text-decoration: none;
  }
  .byline a:hover { text-decoration: underline; }

  .summary {
    font-family: var(--serif); font-size: 15.5px;
    color: var(--ink); line-height: 1.75;
    margin: 0 0 18px;
  }

  .insight {
    font-family: var(--serif); font-size: 14.5px;
    color: var(--ink-soft); line-height: 1.75;
    margin: 0; padding: 4px 0 4px 16px;
    border-left: 2px solid var(--insight);
    font-style: italic;
  }

  .empty {
    text-align: center; padding: 80px 0;
    color: var(--ink-light); font-family: var(--serif);
    font-style: italic; font-size: 15px;
  }
  .hidden { display: none !important; }

  footer {
    margin-top: 80px; padding-top: 24px;
    border-top: 1px solid var(--rule);
    font-family: var(--sans); font-size: 10px;
    color: var(--ink-light); text-align: center;
    letter-spacing: .5px; line-height: 1.7;
  }

  @media (max-width: 640px) {
    .wrap { padding: 36px 20px 60px; }
    .entry { grid-template-columns: 36px 1fr; gap: 14px; padding: 26px 0; }
    .entry-num { font-size: 22px; padding-top: 2px; }
    .headline { font-size: 19px; }
    .summary { font-size: 15px; }
    .insight { font-size: 14px; }
  }
</style>
</head>
<body>
  <div class="wrap">
    <header class="mast">
      <h1>FURSYS<span class="accent">-</span>INSIGHT</h1>
      <div class="date">{today_long} · 오늘의 {today_count}건</div>
    </header>

    <nav class="filter">
      <select id="day-filter">{day_options}</select>
      <select id="brand-filter">{brand_options}</select>
      <select id="type-filter">{type_options}</select>
      <span class="vcount" id="vcount"></span>
    </nav>

    <main id="entries">{entries_html}
    </main>

    <div class="empty hidden" id="empty">선택한 조건에 해당하는 인사이트가 없습니다.</div>

    <footer>
      외부에서 우리가 다시 봐야 할 것들 · 출처: 구글 뉴스 RSS · 분석: Claude API · 사내 참고용<br>
      최종 발행: {generated_at} · 원문 저작권은 각 매체에 있습니다.
    </footer>
  </div>

<script>
const STATE = { day: "today", brand: "all", type: "all" };

function applyFilter() {
  let v = 0;
  document.querySelectorAll(".entry").forEach(e => {
    const dm = STATE.day === "all" || e.dataset.day === STATE.day;
    const brands = (e.dataset.brands || "").split(",").filter(b => b);
    const bm = STATE.brand === "all" || brands.includes(STATE.brand);
    const tm = STATE.type === "all" || e.dataset.type === STATE.type;
    const show = dm && bm && tm;
    e.style.display = show ? "" : "none";
    if (show) v++;
  });
  document.getElementById("empty").classList.toggle("hidden", v > 0);
  document.getElementById("vcount").textContent = v + "건";
}

document.getElementById("day-filter").addEventListener("change", e => {
  STATE.day = e.target.value;
  applyFilter();
});
document.getElementById("brand-filter").addEventListener("change", e => {
  STATE.brand = e.target.value;
  applyFilter();
});
document.getElementById("type-filter").addEventListener("change", e => {
  STATE.type = e.target.value;
  applyFilter();
});

applyFilter();
</script>
</body>
</html>
"""


def render_entry(article, day_str, day_short, rank):
    """단일 컬럼 entry — 헤드라인이 메인, 인사이트가 본문."""
    title = escape(article.get("title", ""))
    link = escape(article.get("link", "#"))
    source = escape(article.get("source", "") or "출처 미상")
    summary = escape(article.get("summary", "") or article.get("description", "")[:300])
    insight = article.get("insight", "")
    importance = article.get("importance", "중")
    brands = article.get("brands") or []
    brand_csv = ",".join(brands)
    article_type = article.get("type", "external")

    tag_parts = []
    if article_type == "internal":
        tag_parts.append('<span class="type-internal">자사 소식</span>')
    elif article_type == "ai":
        tag_parts.append('<span class="type-ai">AI 가이드</span>')
    if brands:
        tag_parts.append(f'<span class="brand">{escape(" · ".join(brands))}</span>')
    if importance == "상":
        tag_parts.append('<span class="imp-high">주목</span>')
    tags_html = '<span class="sep">·</span>'.join(tag_parts)

    insight_html = ""
    if insight:
        insight_html = f'<p class="insight">{escape(insight)}</p>'

    return f'''
      <article class="entry" data-day="{day_str}" data-brands="{brand_csv}" data-type="{article_type}">
        <div class="entry-num">{rank:02d}</div>
        <div class="entry-body">
          <div class="entry-tags">{tags_html}</div>
          <h2 class="headline"><a href="{link}" target="_blank" rel="noopener">{title}</a></h2>
          <div class="byline">{source} · {escape(day_short)} · <a href="{link}" target="_blank" rel="noopener">원문 보기 →</a></div>
          <p class="summary">{summary}</p>
          {insight_html}
        </div>
      </article>'''



def build_site():
    print("[4/4] 사이트 빌드...", flush=True)
    entries = load_recent_archives(RETENTION_DAYS)
    if not entries:
        print("   ! 아카이브 없음", flush=True); return None
    today_str = entries[0]["date"]

    entries_html = []
    today_count = 0
    for entry in entries:
        date_str = entry["date"]
        is_today = (date_str == today_str)
        day_short = fmt_date_short(date_str)
        day_attr = "today" if is_today else date_str
        rank = 0
        for cat, arts in entry.get("articles", {}).items():
            for a in arts:
                rank += 1
                # 카테고리 기반 type 보강 (구버전 archive 호환)
                if "type" not in a:
                    a["type"] = get_article_type(cat)
                entries_html.append(render_entry(a, day_attr, day_short, rank))
                if is_today: today_count += 1

    # 일자 옵션 (오늘이 디폴트, 그 다음 전체, 그 다음 과거 일자)
    day_options = [f'<option value="today" selected>오늘 ({fmt_date_short(today_str)})</option>']
    day_options.append('<option value="all">전체 7일치</option>')
    for entry in entries[1:]:
        d = entry["date"]
        day_options.append(f'<option value="{d}">{fmt_date_short(d)}</option>')

    # 브랜드 옵션
    BRANDS = ["퍼시스", "일룸", "시디즈", "알로소", "슬로우베드", "레터스"]
    brand_options = ['<option value="all" selected>모든 브랜드</option>']
    for b in BRANDS:
        brand_options.append(f'<option value="{b}">{b}</option>')

    type_options = [
        '<option value="all" selected>모든 구분</option>',
        '<option value="external">외부 인사이트</option>',
        '<option value="internal">자사 소식</option>',
        '<option value="ai">AI 가이드</option>',
    ]

    today_long = fmt_date_long(today_str)

    html = SITE_TEMPLATE
    html = html.replace("{today_long}", today_long)
    html = html.replace("{today_count}", str(today_count))
    html = html.replace("{day_options}", "".join(day_options))
    html = html.replace("{brand_options}", "".join(brand_options))
    html = html.replace("{type_options}", "".join(type_options))
    html = html.replace("{entries_html}", "".join(entries_html))
    html = html.replace("{generated_at}", datetime.now().strftime("%Y-%m-%d %H:%M"))

    SITE_PATH.write_text(html, encoding="utf-8")
    print(f"   완료: {SITE_PATH}", flush=True)
    return SITE_PATH




# ============================ 5. 슬랙 발송 ============================

def send_to_slack(enriched):
    """Incoming Webhook으로 오늘자 인사이트 요약을 슬랙 채널에 발송."""
    if not SLACK_WEBHOOK_URL:
        print("   ! SLACK_WEBHOOK_URL 미설정 — 슬랙 발송 스킵", flush=True)
        return False

    flat = [a for arts in enriched.values() for a in arts]
    if not flat:
        print("   ! 발송할 인사이트 없음", flush=True)
        return False

    today = datetime.now().strftime("%Y.%m.%d") + " " + WEEKDAY_KO[datetime.now().weekday()] + "요일"

    by_type = {"external": [], "internal": [], "ai": []}
    for a in flat:
        by_type.setdefault(a.get("type", "external"), []).append(a)

    type_label = {
        "external": "외부 인사이트",
        "internal": "자사 소식",
        "ai": "AI 가이드",
    }

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"Fursys-Insight · {today}"}},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                       "text": f"오늘의 *{len(flat)}가지 인사이트* · 외부 {len(by_type['external'])} / 자사 {len(by_type['internal'])} / AI {len(by_type['ai'])}"}]},
        {"type": "divider"},
    ]

    for t in ["internal", "external", "ai"]:
        items = by_type[t]
        if not items:
            continue
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{type_label[t]}*"}
        })
        for a in items:
            brands = a.get("brands") or []
            brand_str = ("`" + " · ".join(brands) + "` ") if brands else ""
            title = a.get("title", "").replace("|", "丨").replace("<", "").replace(">", "")[:90]
            link = a.get("link", "#")
            insight = a.get("insight", "") or a.get("summary", "")[:160]
            insight = insight.replace("\n", " ").strip()
            text = f"{brand_str}<{link}|*{title}*>"
            if insight:
                text += f"\n_{insight[:220]}_"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": text}
            })
        blocks.append({"type": "divider"})

    footer_text = "사내 참고용 · 출처: 구글 뉴스 RSS · 분석: Claude API"
    if SITE_URL:
        footer_text = f"<{SITE_URL}|전체 보기> · " + footer_text
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": footer_text}]
    })

    payload = {"blocks": blocks, "text": f"Fursys-Insight 오늘의 인사이트 ({today})"}
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            print(f"[5/5] 슬랙 발송 완료 (HTTP {status})", flush=True)
            return True
    except Exception as e:
        print(f"   ! 슬랙 발송 실패: {e}", flush=True)
        return False


# ============================ main ============================

def main():
    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY 환경변수 필요")
    raw = collect_all_news()
    Path(__file__).with_name("news.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    enriched = analyze_with_claude(raw)
    enriched = dedup_articles_with_claude(enriched)
    enriched = select_top_articles(enriched)
    save_daily_archive(enriched)
    cleanup_old_archives()
    build_site()
    send_to_slack(enriched)
    print("DONE.", flush=True)


if __name__ == "__main__":
    main()
