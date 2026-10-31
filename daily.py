#!/usr/bin/env python3
"""库博标准 · 每日学习包 · 云端版

每天在 GitHub Actions 上运行：
  1. 抓新闻（Google News RSS）+ 行情（新浪 / Yahoo）
  2. 调 DeepSeek 生成当天学习包
  3. 写成 docs/ 下的静态网页（GitHub Pages 直接可看）
  4. 推送到飞书 / 钉钉 / 企业微信机器人

环境变量：
  DEEPSEEK_API_KEY  必填
  PUSH_WEBHOOK      选填，群机器人地址
  GITHUB_REPOSITORY 由 Actions 提供，用来拼站点地址
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import markdown

from curriculum import topic_for

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
CST = dt.timezone(dt.timedelta(hours=8))
START_DATE = dt.date(2026, 10, 31)
UA = {"User-Agent": "Mozilla/5.0 (compatible; CS-Daily-Lesson/1.0)"}


# ---------------------------------------------------------------- 抓数据


def _get(url: str, headers: dict | None = None, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    last = None
    for _ in range(3):  # 偶发 SSL / 5xx 重试
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:
            last = exc
    raise last  # type: ignore[misc]


def google_news(query: str, limit: int = 6) -> list[str]:
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )
    try:
        root = ET.fromstring(_get(url))
    except Exception as exc:  # 网络问题不应中断整篇
        print(f"[news] {query} 抓取失败: {exc}", file=sys.stderr)
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        when = (item.findtext("pubDate") or "")[:16]
        if title:
            out.append(f"- {title}｜{when}｜{link}")
        if len(out) >= limit:
            break
    return out


def _yahoo(sym: str, name: str) -> str | None:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(sym)}?range=5d&interval=1d"
    )
    try:
        res = json.loads(_get(url, timeout=20))["chart"]["result"][0]
    except Exception as exc:
        print(f"[market] {sym} 失败: {exc}", file=sys.stderr)
        return None
    closes = [c for c in (res["indicators"]["quote"][0].get("close") or []) if c is not None]
    meta = res["meta"]
    price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
    if price is None:
        return None

    # 基准价：盘中 → 用上一根日线收盘；已收盘 → 用倒数第二根收盘
    base = None
    if closes:
        tol = max(1e-6, abs(closes[-1]) * 1e-5)  # 容差：避免浮点误差误判为盘中
        if abs(price - closes[-1]) > tol:
            base = closes[-1]
        elif len(closes) >= 2:
            base = closes[-2]
    if base is None:
        base = meta.get("previousClose") or meta.get("chartPreviousClose")

    if base:
        pct = (price - base) / base * 100
        return f"{name} {price:,.2f}（{pct:+.2f}%）"
    return f"{name} {price:,.2f}"


def market_quotes() -> list[str]:
    """A 股指数 + 美股 + 库博标准。数据源 Yahoo Finance。"""
    targets = [
        ("000001.SS", "上证指数"),
        ("399001.SZ", "深证成指"),
        ("399006.SZ", "创业板指"),
        ("000688.SS", "科创50"),
        ("^GSPC", "标普500"),
        ("^IXIC", "纳斯达克"),
        ("^DJI", "道琼斯"),
        ("CPS", "库博标准 CPS"),
    ]
    return [q for q in (_yahoo(s, n) for s, n in targets) if q]


# ---------------------------------------------------------------- 生成


SYSTEM = """你是一位资深汽车密封系统工程师，同时是英语教练和财经分析师。
你为「库博标准（Cooper Standard）密封系统产品开发培训生」写每日学习包。
写作要求：中文讲解、英文术语并列；专业、具体、可执行；不说空话套话。
只输出 Markdown 正文，不要任何前言或说明。"""


def build_prompt(day_no: int, date: dt.date, en_focus: str, product_topic: str,
                 news: dict[str, list[str]], quotes: list[str]) -> str:
    blocks = []
    for k, v in news.items():
        blocks.append(f"### {k}\n" + ("\n".join(v) if v else "- 未获取到"))
    news_text = "\n\n".join(blocks)
    quote_text = "、".join(quotes) if quotes else "未获取到"

    return f"""今天是 {date:%Y-%m-%d}，第 {day_no} 天。

今天的英语重点：{en_focus}
今天的产品专题：{product_topic}

# 今日素材（只能使用这些事实，禁止编造行情数字或新闻）

## 新闻标题（含来源链接）
{news_text}

## 行情快照
{quote_text}

# 输出要求

写四部分，Markdown 格式，表格用标准 Markdown 表格：

## Part 1 英语 25 分钟
1. 语音热身 3min：三个中国人常见错音 + 最小对立练习。
2. 影子跟读 8min：两段真实工作场景稿（各 30–60 秒英文原文），给发音点、连读弱读、节奏。
3. 功能句 6 组 6min：表格，列为「场景 / 直接版 / 委婉版」。
4. 词汇 12 个 6min：表格，列为「词 / 音标 / 释义 / 搭配」，再给一个词族或词根词缀专栏。
5. 场景对话 4min：6–8 轮 A/B 对话，标出地道表达。
6. 输出任务 2min：要具体可检查（录音 + 写句子）。

## Part 2 产品专题 25 分钟：{product_topic}
必须按这个骨架讲到底：
1. 一句话定义与它在图纸上的位置
2. 怎么测：标准号、设备/压头几何、试样、环境、读数方式、取值规则
3. 参数族或标尺对比表
4. 典型值表（部位×材料×数值，标注“工程参考非规范”）
5. 该参数升高/降低的连锁影响表（性能→后果）
6. 配方与设计杠杆表
7. 温度/时间/老化导致的漂移
8. 图纸与规范该怎么写（给代码块示例）
9. 5–6 个测量或应用陷阱
10. 延伸到当前产品与技术方向（可提及库博标准 Fortrex / FlexiFit / FlexiCore 等公开产品线）
11. 自测 5 题 + 「### 参考答案」（不要用 details 标签）

## Part 3 新闻 + 我的理解 10 分钟
分「世界 / 中国 / 汽车行业 / 经济与市场」四块。
每块先列**事实**（只能用上面素材里的标题，保留链接），再写「**我的理解**」：
因果链、二阶影响、该盯什么指标。明确标注这是我的判断，不是新闻内容。

## Part 4 收尾
- 「今天记住三句话」
- 「对你的实操含义」：连到密封系统产品开发岗位

长度：正文 2600–3400 字，必须完整写完 Part 4 的四条，不得中途截断。
所有英文术语首次出现给中文。新闻部分不要逐条复述标题，只挑最关键的事实，其余合并成一句话。"""


def call_llm(prompt: str) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("缺少环境变量 DEEPSEEK_API_KEY")
    body = json.dumps({
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.75,
        "max_tokens": 8192,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------- 渲染


CSS = """
:root{--bg:#fbfaf7;--fg:#1c1b19;--muted:#6b6862;--line:#e2ded6;--card:#fff;
--accent:#0a6b5f;--accent2:#b4531a;--code:#f2efe9}
@media (prefers-color-scheme:dark){:root{--bg:#16181a;--fg:#e8e6e1;--muted:#9a968e;
--line:#2c2f33;--card:#1e2124;--accent:#5fd3bd;--accent2:#e6a06a;--code:#24282c}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0 auto;background:var(--bg);color:var(--fg);max-width:760px;
font:17px/1.78 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
padding:20px 18px 72px;word-break:break-word;overflow-wrap:anywhere}
h1{font-size:1.6em;line-height:1.35;margin:.2em 0 .8em}
h1::after{content:"";display:block;width:46px;height:3px;margin-top:.5em;
background:var(--accent);border-radius:2px}
h2{font-size:1.26em;margin:2em 0 .6em;padding-top:.9em;border-top:1px solid var(--line)}
h3{font-size:1.08em;margin:1.6em 0 .5em;color:var(--accent)}
p{margin:.75em 0}a{color:var(--accent)}
blockquote{margin:1em 0;padding:.7em 1em;border-left:3px solid var(--accent2);
background:var(--card);border-radius:0 8px 8px 0;color:var(--muted)}
ul,ol{padding-left:1.35em}li{margin:.36em 0}
code{background:var(--code);padding:.14em .38em;border-radius:5px;
font:.88em/1.5 ui-monospace,Menlo,Consolas,monospace}
pre{background:var(--code);padding:14px 16px;border-radius:10px;overflow-x:auto;font-size:.86em}
pre code{background:none;padding:0}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:1.1em -18px;padding:0 18px}
table{border-collapse:collapse;width:100%;font-size:.88em}
th,td{border:1px solid var(--line);padding:9px 11px;text-align:left;vertical-align:top}
th{background:var(--code);font-weight:650;white-space:nowrap}
.topbar{position:sticky;top:0;z-index:9;margin:-20px -18px 18px;padding:11px 18px;
background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(12px);
border-bottom:1px solid var(--line);font-size:.92em}
.topbar a{text-decoration:none;font-weight:600}
.cards{display:grid;gap:12px;margin-top:1.4em}
.card{display:block;padding:16px 18px;background:var(--card);border:1px solid var(--line);
border-radius:14px;text-decoration:none;color:inherit}
.card .d{font-size:.78em;color:var(--accent2);font-weight:600;letter-spacing:.04em}
.card .t{font-size:1.03em;font-weight:600;margin:.35em 0 .2em;line-height:1.45}
footer{margin-top:3em;padding-top:1.2em;border-top:1px solid var(--line);
color:var(--muted);font-size:.82em;text-align:center}
"""

PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#fbfaf7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#16181a" media="(prefers-color-scheme: dark)">
<title>{title}</title><style>{css}</style></head><body>{body}</body></html>"""


def render(md_text: str, title: str) -> str:
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html5",
    )
    body = re.sub(r"<table>.*?</table>", lambda m: f'<div class="tw">{m.group(0)}</div>',
                  body, flags=re.S)
    back = '<div class="topbar"><a href="./index.html">← 返回目录</a></div>'
    return PAGE.format(title=html.escape(title), css=CSS, body=back + body)


def rebuild_index() -> None:
    items = []
    for p in sorted(DOCS.glob("*.html")):
        if p.name in ("index.html", "today.html"):
            continue
        text = p.read_text(encoding="utf-8")
        m = re.search(r"<title>(.*?)</title>", text, re.S)
        title = html.unescape(m.group(1)) if m else p.stem
        d = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
        items.append((d.group(1) if d else p.stem, title, p.name))
    items.sort(reverse=True)
    cards = "".join(
        f'<a class="card" href="{html.escape(n)}"><div class="d">{html.escape(d)}</div>'
        f'<div class="t">{html.escape(t)}</div></a>'
        for d, t, n in items
    ) or "<p>还没有内容。</p>"
    body = (
        "<h1>库博标准 · 每日学习包</h1>"
        "<p>英语 25 分钟 · 产品专题 25 分钟 · 新闻解读 10 分钟</p>"
        f'<div class="cards">{cards}</div>'
        f"<footer>更新于 {dt.datetime.now(CST):%Y-%m-%d %H:%M} · 共 {len(items)} 期</footer>"
    )
    (DOCS / "index.html").write_text(
        PAGE.format(title="库博标准 · 每日学习包", css=CSS, body=body), encoding="utf-8"
    )


# ---------------------------------------------------------------- 推送


def push(text: str) -> None:
    hook = os.environ.get("PUSH_WEBHOOK", "").strip()
    if not hook:
        print("[push] 未配置 PUSH_WEBHOOK，跳过推送")
        return
    if "feishu" in hook or "larksuite" in hook:
        payload = {"msg_type": "text", "content": {"text": text}}
    else:  # 钉钉 / 企业微信
        payload = {"msgtype": "text", "text": {"content": text}}
    try:
        req = urllib.request.Request(
            hook,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"[push] {r.status} {r.read()[:160]!r}")
    except Exception as exc:
        print(f"[push] 失败: {exc}", file=sys.stderr)


def site_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return ""


# ---------------------------------------------------------------- 主流程


def main() -> None:
    today = dt.datetime.now(CST).date()
    day_no = (today - START_DATE).days + 1
    if day_no < 1:
        day_no = 1
    en_focus, product_topic = topic_for(day_no)

    news = {
        "世界": google_news("国际局势 OR 地缘政治 OR 中东 when:7d", 4),
        "中国": google_news("中国经济 OR 宏观政策 when:7d", 4),
        "汽车行业": google_news("汽车 新能源 产销 乘用车 when:7d", 5),
        "密封与零部件": google_news("汽车零部件 OR 橡胶 OR 密封 when:7d", 3),
        "市场": google_news("A股 OR 美股 OR 财报 when:7d", 4),
    }
    quotes = market_quotes()

    prompt = build_prompt(day_no, today, en_focus, product_topic, news, quotes)
    print(f"[gen] Day {day_no} {today} · {product_topic}")
    md_text = call_llm(prompt)

    DOCS.mkdir(exist_ok=True)
    stem = f"{today:%Y-%m-%d}-Day{day_no:02d}"
    title = f"Day {day_no:02d} · {today:%Y-%m-%d}｜{product_topic}"

    (DOCS / f"{stem}.md").write_text(md_text, encoding="utf-8")
    (DOCS / f"{stem}.html").write_text(render(md_text, title), encoding="utf-8")
    # 让当天成为站点首页
    (DOCS / "today.html").write_text(render(md_text, title), encoding="utf-8")
    rebuild_index()

    url = f"{site_url()}{stem}.html"
    lines = [f"【Day {day_no:02d}】{product_topic}", f"英语：{en_focus}", "", "今日新闻："]
    lines += [ln.split("｜")[0].lstrip("- ")[:46] for ln in news["汽车行业"][:3]]
    if url:
        lines += ["", f"全文：{url}"]
    push("\n".join(lines))
    print("[done]", stem)


if __name__ == "__main__":
    main()
