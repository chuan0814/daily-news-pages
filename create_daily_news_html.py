from __future__ import annotations

import html
import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree


BASE_DIR = Path(__file__).resolve().parent
SOURCE_SCRIPT = BASE_DIR / "create_daily_news_doc_20260623.py"
PRIMARY_OUTPUT = BASE_DIR / "每日新聞核心話題.html"
LEGACY_OUTPUT = BASE_DIR / "每日新聞核心話題_近24小時_20260623.html"
PAGES_OUTPUT = BASE_DIR / "index.html"
TAIPEI_TZ = timezone(timedelta(hours=8))
FETCH_WINDOW_HOURS = 6
TARGET_COUNT = 30

RSS_FEEDS = [
    ("Yahoo新聞", "https://tw.news.yahoo.com/rss"),
    ("TVBS新聞網", "https://news.tvbs.com.tw/web_api/play_feed_realtime"),
    ("東森新聞網", "https://feeds.feedburner.com/ettoday/realtime"),
]

CHINATIMES_URL = "https://www.chinatimes.com/realtimenews/?chdtv"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def load_news_items():
    try:
        live_items = fetch_recent_news()
        if len(live_items) >= TARGET_COUNT:
            return live_items[:TARGET_COUNT]
        raise RuntimeError(f"最近 {FETCH_WINDOW_HOURS} 小時只抓到 {len(live_items)} 則，未達 {TARGET_COUNT} 則")
    except Exception as exc:
        if __name__ == "__main__":
            raise
        print(f"即時新聞抓取失敗，改用本地備援資料：{exc}", file=sys.stderr)

    spec = importlib.util.spec_from_file_location("daily_news_doc_20260623", SOURCE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"無法讀取新聞資料：{SOURCE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.NEWS_ITEMS


def fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset)
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def short_summary(title: str, description: str) -> str:
    text = strip_tags(description) or title
    text = re.sub(r"^[^：:]{1,12}[：:]", "", text).strip()
    return text[:50]


def parse_datetime(raw: str, now: datetime) -> datetime | None:
    raw = strip_tags(raw)
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        return dt.astimezone(TAIPEI_TZ) if dt.tzinfo else dt.replace(tzinfo=TAIPEI_TZ)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%m/%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt == "%m/%d %H:%M":
                dt = dt.replace(year=now.year)
            return dt.replace(tzinfo=TAIPEI_TZ)
        except ValueError:
            continue
    return None


def classify_item(title: str, description: str, source_category: str = "") -> str:
    text = f"{title} {description} {source_category}"

    def has_keyword(keyword: str) -> bool:
        if keyword.isascii():
            return re.search(rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])", text, re.I) is not None
        return keyword.lower() in text.lower()

    rules = [
        ("電競遊戲", ["電競", "遊戲", "Steam", "Switch", "PS5", "Xbox", "英雄聯盟", "實況", "任天堂"]),
        ("AI科技", ["AI", "人工智慧", "輝達", "NVIDIA", "台積電", "半導體", "晶片", "科技", "機器人", "資料中心"]),
        ("財經/證券", ["台股", "股", "證券", "金管會", "匯率", "美元", "日圓", "財經", "經濟", "央行", "關稅", "基金", "投資"]),
        ("政治", ["總統", "行政院", "立院", "立法院", "民進黨", "國民黨", "民眾黨", "罷免", "選舉", "市長", "政治"]),
        ("國際", ["美國", "中國", "日本", "韓國", "歐盟", "以色列", "伊朗", "烏克蘭", "川普", "國際", "全球"]),
    ]
    for category, keywords in rules:
        if any(has_keyword(keyword) for keyword in keywords):
            return category
    return "國內"


def parse_rss_feed(source: str, url: str, now: datetime) -> list[dict[str, str]]:
    xml_text = fetch_url(url)
    root = ElementTree.fromstring(xml_text.encode("utf-8"))
    items = []
    cutoff = now - timedelta(hours=FETCH_WINDOW_HOURS)
    for node in root.findall(".//item"):
        get = lambda name: (node.findtext(name) or "").strip()
        title = strip_tags(get("title"))
        link = strip_tags(get("link"))
        description = get("description")
        pub_date = parse_datetime(get("pubDate") or get("published"), now)
        if not title or not link or pub_date is None or pub_date < cutoff or pub_date > now + timedelta(minutes=10):
            continue
        category_hint = get("category")
        items.append({
            "category": classify_item(title, description, category_hint),
            "source": source,
            "time": pub_date.strftime("%m/%d %H:%M"),
            "title": title,
            "summary": short_summary(title, description),
            "url": link,
            "_sort_time": pub_date.isoformat(),
        })
    return items


def parse_chinatimes(now: datetime) -> list[dict[str, str]]:
    page = fetch_url(CHINATIMES_URL)
    cutoff = now - timedelta(hours=FETCH_WINDOW_HOURS)
    pattern = re.compile(
        r'<h3 class="title"><a href="(?P<link>[^"]+)">(?P<title>.*?)</a></h3>\s*'
        r'<div class="meta-info">\s*<time datetime="(?P<time>[^"]+)".*?</time>\s*'
        r'<div class="category"><a [^>]*>(?P<category>.*?)</a></div>\s*</div>\s*'
        r'<p class="intro">(?P<summary>.*?)</p>',
        re.S,
    )
    items = []
    for match in pattern.finditer(page):
        title = strip_tags(match.group("title"))
        description = strip_tags(match.group("summary"))
        pub_date = parse_datetime(match.group("time"), now)
        if not title or pub_date is None or pub_date < cutoff or pub_date > now + timedelta(minutes=10):
            continue
        link = match.group("link")
        if link.startswith("/"):
            link = "https://www.chinatimes.com" + link
        category_hint = strip_tags(match.group("category"))
        items.append({
            "category": classify_item(title, description, category_hint),
            "source": "中時新聞網",
            "time": pub_date.strftime("%m/%d %H:%M"),
            "title": title,
            "summary": short_summary(title, description),
            "url": link,
            "_sort_time": pub_date.isoformat(),
        })
    return items


def fetch_recent_news() -> list[dict[str, str]]:
    now = datetime.now(TAIPEI_TZ)
    fetched: list[dict[str, str]] = []
    errors: list[str] = []

    for source, url in RSS_FEEDS:
        try:
            fetched.extend(parse_rss_feed(source, url, now))
        except (urllib.error.URLError, ElementTree.ParseError, TimeoutError, ValueError) as exc:
            errors.append(f"{source}: {exc}")

    try:
        fetched.extend(parse_chinatimes(now))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        errors.append(f"中時新聞網: {exc}")

    seen = set()
    unique_items = []
    preferred_categories = {"政治", "國際", "財經/證券", "AI科技", "電競遊戲"}
    for item in sorted(fetched, key=lambda row: row["_sort_time"], reverse=True):
        key = re.sub(r"\W+", "", item["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        cleaned = {k: v for k, v in item.items() if not k.startswith("_")}
        unique_items.append(cleaned)

    focused = [item for item in unique_items if item["category"] in preferred_categories]
    source_order = ["Yahoo新聞", "中時新聞網", "TVBS新聞網", "東森新聞網"]
    source_buckets = {source: [item for item in focused if item["source"] == source] for source in source_order}
    balanced: list[dict[str, str]] = []
    while len(balanced) < TARGET_COUNT and any(source_buckets.values()):
        for source in source_order:
            if source_buckets[source] and len(balanced) < TARGET_COUNT:
                balanced.append(source_buckets[source].pop(0))
    combined = balanced + [item for item in focused if item not in balanced] + [item for item in unique_items if item not in focused]
    if len(combined) < TARGET_COUNT and errors:
        raise RuntimeError("; ".join(errors))
    return combined[:TARGET_COUNT]


def parse_sort_key(item: dict[str, str]) -> tuple[int, str]:
    raw = item["time"].strip()
    for fmt in ("%m/%d %H:%M", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt == "%m/%d %H:%M":
                dt = dt.replace(year=2026)
            return (int(dt.timestamp()), item["title"])
        except ValueError:
            continue
    return (0, item["title"])


def normalize_items(news_items: list[dict[str, str]]) -> list[dict[str, str]]:
    items = []
    for idx, item in enumerate(sorted(news_items, key=parse_sort_key, reverse=True), 1):
        row = dict(item)
        row["id"] = idx
        items.append(row)
    return items


def category_sections(news_items: list[dict[str, str]]) -> OrderedDict[str, list[dict[str, str]]]:
    groups: OrderedDict[str, list[dict[str, str]]] = OrderedDict()
    for item in news_items:
        groups.setdefault(item["category"], []).append(item)
    return groups


def stat_blocks(news_items: list[dict[str, str]]) -> str:
    source_counts = Counter(item["source"] for item in news_items)
    category_counts = Counter(item["category"] for item in news_items)

    def chips(counter: Counter[str]) -> str:
        return "".join(
            f'<span class="chip"><strong>{html.escape(name)}</strong><em>{count} 則</em></span>'
            for name, count in counter.items()
        )

    return f"""
    <section class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">本輪新聞數</div>
        <div class="stat-value">{len(news_items)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">更新頻率</div>
        <div class="stat-value stat-value-small">每 6 小時</div>
      </div>
      <div class="stat-card stat-card-wide">
        <div class="stat-label">來源分布</div>
        <div class="chips">{chips(source_counts)}</div>
      </div>
      <div class="stat-card stat-card-wide">
        <div class="stat-label">類別分布</div>
        <div class="chips">{chips(category_counts)}</div>
      </div>
    </section>
    """


def filter_tabs(groups: OrderedDict[str, list[dict[str, str]]]) -> str:
    buttons = ['<button class="filter-chip is-active" type="button" data-filter="全部">全部</button>']
    for category, items in groups.items():
        buttons.append(
            f'<button class="filter-chip" type="button" data-filter="{html.escape(category)}">'
            f'{html.escape(category)} <span>{len(items)}</span></button>'
        )
    return "".join(buttons)


def build_lead_cards(news_items: list[dict[str, str]]) -> str:
    cards = []
    for item in news_items[:4]:
        cards.append(
            f"""
            <article class="lead-card" data-category="{html.escape(item['category'])}" data-source="{html.escape(item['source'])}">
              <div class="lead-meta">
                <span class="badge">{item['id']}</span>
                <span class="badge badge-soft">{html.escape(item["category"])}</span>
                <span class="badge badge-soft">{html.escape(item["source"])}</span>
              </div>
              <h2>{html.escape(item["title"])}</h2>
              <p>{html.escape(item["summary"])}</p>
              <div class="lead-footer">
                <span>{html.escape(item["time"])}</span>
                <a href="{html.escape(item["url"], quote=True)}" target="_blank" rel="noopener noreferrer">查看原文</a>
              </div>
            </article>
            """
        )
    return "".join(cards)


def build_category_sections(groups: OrderedDict[str, list[dict[str, str]]]) -> str:
    parts: list[str] = []
    for category, items in groups.items():
        cards = []
        for item in items:
            cards.append(
                f"""
                <article class="news-item" data-category="{html.escape(category)}" data-source="{html.escape(item['source'])}" data-title="{html.escape(item['title'])}">
                  <div class="item-top">
                    <div class="item-badges">
                      <span class="item-index">{item['id']:02d}</span>
                      <span class="item-source">{html.escape(item["source"])}</span>
                    </div>
                    <time>{html.escape(item["time"])}</time>
                  </div>
                  <h3>{html.escape(item["title"])}</h3>
                  <p>{html.escape(item["summary"])}</p>
                  <a href="{html.escape(item["url"], quote=True)}" target="_blank" rel="noopener noreferrer">開啟原文</a>
                </article>
                """
            )
        parts.append(
            f"""
            <section class="category-block" data-category-group="{html.escape(category)}">
              <div class="section-head">
                <h2>{html.escape(category)}</h2>
                <span>{len(items)} 則</span>
              </div>
              <div class="news-grid">
                {''.join(cards)}
              </div>
            </section>
            """
        )
    return "".join(parts)


def build_table_rows(news_items: list[dict[str, str]]) -> str:
    rows = []
    for item in news_items:
        rows.append(
            f"""
            <tr data-category="{html.escape(item['category'])}" data-source="{html.escape(item['source'])}" data-title="{html.escape(item['title'])}">
              <td class="cell-index">{item['id']}</td>
              <td>{html.escape(item["category"])}</td>
              <td>{html.escape(item["source"])}</td>
              <td>{html.escape(item["time"])}</td>
              <td class="cell-title">{html.escape(item["title"])}</td>
              <td>{html.escape(item["summary"])}</td>
              <td><a href="{html.escape(item["url"], quote=True)}" target="_blank" rel="noopener noreferrer">原文</a></td>
            </tr>
            """
        )
    return "".join(rows)


def build_html(news_items: list[dict[str, str]]) -> str:
    normalized = normalize_items(news_items)
    groups = category_sections(normalized)
    stats_html = stat_blocks(normalized)
    lead_cards = build_lead_cards(normalized)
    section_html = build_category_sections(groups)
    rows_html = build_table_rows(normalized)
    tab_html = filter_tabs(groups)
    updated_at = datetime.now().strftime("%Y/%m/%d %H:%M")
    data_json = html.escape(json.dumps(normalized, ensure_ascii=False))

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>每日新聞核心話題 即時版</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #eef3f8;
      --surface: #ffffff;
      --surface-2: #f7fafe;
      --line: #d7e0eb;
      --text: #122033;
      --muted: #617286;
      --accent: #0b57d0;
      --accent-2: #08306b;
      --accent-soft: #e9f1ff;
      --shadow: 0 10px 28px rgba(15, 35, 55, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Microsoft JhengHei", "Noto Sans TC", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
    }}
    a:hover {{ text-decoration: underline; }}
    .page {{
      width: min(1360px, calc(100% - 28px));
      margin: 18px auto 34px;
    }}
    .hero {{
      background: linear-gradient(135deg, #0a2342, #154c79 68%, #1c7293);
      color: #fff;
      border-radius: 8px;
      padding: 28px;
      box-shadow: var(--shadow);
    }}
    .hero-top {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: flex-start;
      margin-bottom: 20px;
    }}
    .hero h1 {{
      margin: 0 0 10px;
      font-size: 32px;
      line-height: 1.2;
    }}
    .hero p {{
      margin: 6px 0;
      color: rgba(255,255,255,0.9);
      font-size: 14px;
    }}
    .hero-side {{
      min-width: 240px;
      background: rgba(255,255,255,0.1);
      border: 1px solid rgba(255,255,255,0.12);
      border-radius: 8px;
      padding: 14px 16px;
    }}
    .hero-side strong {{
      display: block;
      font-size: 12px;
      opacity: 0.8;
      margin-bottom: 6px;
    }}
    .hero-side span {{
      display: block;
      font-size: 20px;
      font-weight: 700;
      margin-bottom: 10px;
    }}
    .hero-side small {{
      display: block;
      font-size: 12px;
      color: rgba(255,255,255,0.86);
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
      gap: 14px;
      margin-top: 18px;
    }}
    .toolbar-card {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 14px 16px;
    }}
    .toolbar-label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 10px;
    }}
    .search-input {{
      width: 100%;
      height: 42px;
      border-radius: 8px;
      border: 1px solid var(--line);
      padding: 0 14px;
      font-size: 15px;
      outline: none;
      background: #fff;
    }}
    .search-input:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(11, 87, 208, 0.12);
    }}
    .filter-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .filter-chip {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 13px;
      cursor: pointer;
    }}
    .filter-chip span {{
      color: var(--muted);
      margin-left: 4px;
    }}
    .filter-chip.is-active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    .filter-chip.is-active span {{
      color: rgba(255,255,255,0.88);
    }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin: 18px 0;
    }}
    .stat-card {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
    }}
    .stat-card-wide {{
      grid-column: span 2;
    }}
    .stat-label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 10px;
    }}
    .stat-value {{
      font-size: 32px;
      font-weight: 800;
      color: var(--accent-2);
    }}
    .stat-value-small {{
      font-size: 22px;
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 11px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent-2);
      font-size: 13px;
    }}
    .chip em {{
      color: var(--muted);
      font-style: normal;
    }}
    .lead-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .lead-card, .category-block, .table-panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .lead-card {{
      padding: 16px;
      min-height: 220px;
      display: flex;
      flex-direction: column;
    }}
    .lead-meta, .item-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 28px;
      height: 28px;
      padding: 0 10px;
      border-radius: 999px;
      background: var(--accent);
      color: #fff;
      font-size: 12px;
      font-weight: 700;
    }}
    .badge-soft {{
      background: var(--accent-soft);
      color: var(--accent-2);
    }}
    .lead-card h2 {{
      margin: 14px 0 10px;
      font-size: 20px;
      line-height: 1.35;
    }}
    .lead-card p {{
      margin: 0;
      color: #36495f;
      font-size: 14px;
    }}
    .lead-footer {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-top: auto;
      padding-top: 14px;
      color: var(--muted);
      font-size: 13px;
    }}
    .category-stack {{
      display: grid;
      gap: 16px;
    }}
    .category-block {{
      padding: 16px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .section-head h2 {{
      margin: 0;
      font-size: 22px;
    }}
    .section-head span {{
      color: var(--muted);
      font-size: 13px;
    }}
    .news-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}
    .news-item {{
      background: var(--surface-2);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .item-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }}
    .item-index {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 34px;
      height: 28px;
      border-radius: 999px;
      background: #dce9ff;
      color: var(--accent-2);
      font-size: 12px;
      font-weight: 800;
    }}
    .item-source {{
      font-size: 12px;
      color: var(--muted);
      font-weight: 700;
    }}
    .item-top time {{
      font-size: 12px;
      color: var(--muted);
    }}
    .news-item h3 {{
      margin: 0 0 8px;
      font-size: 18px;
      line-height: 1.35;
    }}
    .news-item p {{
      margin: 0 0 10px;
      color: #3b4d61;
      font-size: 14px;
    }}
    .table-panel {{
      margin-top: 18px;
      overflow: hidden;
    }}
    .panel-head {{
      padding: 14px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      border-bottom: 1px solid var(--line);
    }}
    .panel-head h2 {{
      margin: 0;
      font-size: 20px;
    }}
    .panel-head span {{
      color: var(--muted);
      font-size: 13px;
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      min-width: 1050px;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 12px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #edf4ff;
      color: #15385a;
      z-index: 1;
    }}
    .cell-index {{
      width: 60px;
      text-align: center;
      font-weight: 800;
      color: var(--accent);
    }}
    .cell-title {{
      min-width: 320px;
      font-weight: 700;
    }}
    .empty-state {{
      display: none;
      background: #fff7f6;
      border: 1px solid #f3d2cf;
      color: #7a2620;
      border-radius: 8px;
      padding: 14px 16px;
      margin: 18px 0 0;
    }}
    .footer-note {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 14px;
      text-align: center;
    }}
    .hidden {{
      display: none !important;
    }}
    @media (max-width: 1180px) {{
      .lead-grid, .news-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .stats-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}
    @media (max-width: 860px) {{
      .page {{
        width: min(100% - 18px, 100%);
        margin: 10px auto 24px;
      }}
      .hero {{
        padding: 18px;
      }}
      .hero-top, .toolbar {{
        grid-template-columns: 1fr;
        display: grid;
      }}
      .hero h1 {{
        font-size: 26px;
      }}
      .lead-grid, .news-grid, .stats-grid {{
        grid-template-columns: 1fr;
      }}
      .stat-card-wide {{
        grid-column: auto;
      }}
      .panel-head {{
        align-items: flex-start;
        flex-direction: column;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="hero-top">
        <div>
          <h1>每日新聞核心話題 即時版</h1>
          <p>聚焦最近 6 小時內的重要新聞，來源限定 Yahoo新聞、中時新聞網、TVBS新聞網、東森新聞網。</p>
          <p>主題涵蓋政治、財經、證券、AI科技、電競遊戲，方便你直接在瀏覽器閱讀。</p>
        </div>
        <aside class="hero-side">
          <strong>最後更新</strong>
          <span id="updatedAt">{updated_at}</span>
          <small>排程時間：每日 01:00 / 07:00 / 13:00 / 19:00</small>
        </aside>
      </div>

      <div class="toolbar">
        <div class="toolbar-card">
          <div class="toolbar-label">搜尋標題、摘要或來源</div>
          <input id="searchInput" class="search-input" type="search" placeholder="例如：台積電、AI、以色列、遊戲股">
        </div>
        <div class="toolbar-card">
          <div class="toolbar-label">依類別篩選</div>
          <div class="filter-row" id="filterRow">
            {tab_html}
          </div>
        </div>
      </div>
    </section>

    {stats_html}

    <section class="lead-grid" id="leadGrid">
      {lead_cards}
    </section>

    <div class="empty-state" id="emptyState">目前沒有符合搜尋或篩選條件的新聞。</div>

    <section class="category-stack" id="categoryStack">
      {section_html}
    </section>

    <section class="table-panel">
      <div class="panel-head">
        <h2>完整清單</h2>
        <span>想快速掃過全部標題時，可以看這個表格。</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>類別</th>
              <th>來源</th>
              <th>時間</th>
              <th>標題</th>
              <th>摘要</th>
              <th>原文</th>
            </tr>
          </thead>
          <tbody id="newsTableBody">
            {rows_html}
          </tbody>
        </table>
      </div>
    </section>

    <p class="footer-note">這是本機 HTML 檔，直接雙擊就能在瀏覽器開啟；之後每次排程都會覆蓋同一份檔案。</p>
  </main>

  <script id="newsData" type="application/json">{data_json}</script>
  <script>
    const state = {{ keyword: "", category: "全部" }};
    const filterRow = document.getElementById("filterRow");
    const searchInput = document.getElementById("searchInput");
    const tableRows = Array.from(document.querySelectorAll("#newsTableBody tr"));
    const cards = Array.from(document.querySelectorAll(".news-item"));
    const leadCards = Array.from(document.querySelectorAll(".lead-card"));
    const groups = Array.from(document.querySelectorAll("[data-category-group]"));
    const emptyState = document.getElementById("emptyState");

    function textMatch(node, keyword) {{
      if (!keyword) return true;
      return node.textContent.toLowerCase().includes(keyword);
    }}

    function categoryMatch(category) {{
      return state.category === "全部" || category === state.category;
    }}

    function applyFilters() {{
      const keyword = state.keyword.trim().toLowerCase();
      let visibleCount = 0;

      cards.forEach((card) => {{
        const ok = categoryMatch(card.dataset.category) && textMatch(card, keyword);
        card.classList.toggle("hidden", !ok);
        if (ok) visibleCount += 1;
      }});

      groups.forEach((group) => {{
        const category = group.dataset.categoryGroup;
        const hasVisible = Array.from(group.querySelectorAll(".news-item")).some((card) => !card.classList.contains("hidden"));
        group.classList.toggle("hidden", !categoryMatch(category) || !hasVisible);
      }});

      tableRows.forEach((row) => {{
        const ok = categoryMatch(row.dataset.category) && textMatch(row, keyword);
        row.classList.toggle("hidden", !ok);
      }});

      leadCards.forEach((card) => {{
        const ok = categoryMatch(card.dataset.category) && textMatch(card, keyword);
        card.classList.toggle("hidden", !ok);
      }});

      emptyState.style.display = visibleCount === 0 ? "block" : "none";
    }}

    filterRow.addEventListener("click", (event) => {{
      const button = event.target.closest(".filter-chip");
      if (!button) return;
      state.category = button.dataset.filter;
      document.querySelectorAll(".filter-chip").forEach((chip) => chip.classList.remove("is-active"));
      button.classList.add("is-active");
      applyFilters();
    }});

    searchInput.addEventListener("input", () => {{
      state.keyword = searchInput.value;
      applyFilters();
    }});
  </script>
</body>
</html>
"""


def write_outputs(content: str):
    PRIMARY_OUTPUT.write_text(content, encoding="utf-8")
    LEGACY_OUTPUT.write_text(content, encoding="utf-8")
    PAGES_OUTPUT.write_text(content, encoding="utf-8")


def main():
    news_items = load_news_items()
    if len(news_items) < TARGET_COUNT:
        raise RuntimeError(f"新聞數不足：只取得 {len(news_items)} 則")
    content = build_html(news_items)
    write_outputs(content)
    print(f"HTML 已輸出：{PRIMARY_OUTPUT}")


if __name__ == "__main__":
    main()
