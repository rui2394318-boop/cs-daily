#!/usr/bin/env python3
"""每日英语 + 每日新闻 + 每周密封专题 · 云端版

每天在 GitHub Actions 上运行：

  1. 英语（每天）：24 个专业/会议词汇、8 组句式、6 个连词、
     法国口音听力训练、输出任务
  2. 每日新闻（每天）：只挑重要的，事实自带，不需要点原文
  3. 密封系统专题（每周日）：一次讲透一个主题

环境变量：
  DEEPSEEK_API_KEY  必填
  PUSH_WEBHOOK      选填，群机器人地址
  GITHUB_REPOSITORY 由 Actions 提供，用来拼站点地址
  FORCE_DATE        选填，补发指定日期 YYYY-MM-DD
  NEWS_ONLY         选填，填 1 时只重写新闻
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

from curriculum import sealing_topic_for, theme_for, word_zone_for

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
CST = dt.timezone(dt.timedelta(hours=8))
START_DATE = dt.date(2026, 10, 31)
UA = {"User-Agent": "Mozilla/5.0 (compatible; CS-Daily-Lesson/1.0)"}


# ---------------------------------------------------------------- 抓数据


def _get(url: str, headers: dict | None = None, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    last = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:
            last = exc
    raise last  # type: ignore[misc]


def _clean(text: str, limit: int = 320) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def wallstreetcn(channel: str, limit: int = 10) -> list[str]:
    """华尔街见闻快讯：全球 / A 股 / 美股，正文完整。"""
    url = (
        "https://api-one.wallstcn.com/apiv1/content/lives"
        f"?channel={channel}&client=pc&limit={limit}"
    )
    try:
        data = json.loads(_get(url, timeout=25))
        items = data["data"]["items"]
    except Exception as exc:
        print(f"[news] wsc {channel} 失败: {exc}", file=sys.stderr)
        return []
    out = []
    for it in items:
        txt = _clean(it.get("content_text") or "")
        if len(txt) > 20:
            out.append(f"- {txt}")
    return out


def sina_flash(limit: int = 14) -> list[str]:
    """新浪财经 7x24 快讯，正文完整。"""
    url = (
        "https://zhibo.sina.com.cn/api/zhibo/feed"
        f"?page=1&page_size={limit}&zhibo_id=152&tag_id=0&dire=f&dpc=1"
    )
    try:
        data = json.loads(_get(url, timeout=25))
        items = data["result"]["data"]["feed"]["list"]
    except Exception as exc:
        print(f"[news] sina 失败: {exc}", file=sys.stderr)
        return []
    out = []
    for it in items:
        txt = _clean(it.get("rich_text") or "")
        if len(txt) > 20:
            out.append(f"- {txt}")
    return out


def google_news(query: str, limit: int = 8) -> list[str]:
    """通用新闻标题（只有标题，没有正文），用于补快讯源漏掉的大事件。"""
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )
    try:
        root = ET.fromstring(_get(url))
    except Exception as exc:
        print(f"[news] google {query} 失败: {exc}", file=sys.stderr)
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if title:
            out.append(f"- {title}")
        if len(out) >= limit:
            break
    return out


def rss_feed(url: str, limit: int = 8) -> list[str]:
    """国际媒体 RSS（标题 + 摘要）。单次尝试，失败就跳过。"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
        root = ET.fromstring(raw)
    except Exception as exc:
        print(f"[news] rss 失败 {url}: {exc}", file=sys.stderr)
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        desc = _clean(item.findtext("description") or "", 140)
        out.append(f"- {title}｜{desc}" if desc else f"- {title}")
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
        tol = max(1e-6, abs(closes[-1]) * 1e-5)
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


# ---------------------------------------------------------------- 提示词

SYSTEM_ENGLISH = """你是一位英语母语者出身的商务英语教练，专攻「带口音的英语听力」和「职场口头表达」。
学生是汽车零部件行业的中国工程师，老板是法国人，需要高频用 Teams 和面对面沟通。
你的教学极其具体：给能直接背下来用的句子，而不是语法概念。
只输出 Markdown 正文，不要任何前言或说明。"""

SYSTEM_NEWS = """你是一位见多识广的新闻编辑兼评论员，长期观察国际政治、科技与商业、金融市场。
你为读者写每日新闻简报：事实准确、交代清楚、分析有洞察并且有分寸。
不喊口号，不说正确的废话，不给任何职业或学习建议。
只输出 Markdown 正文，不要任何前言或说明。"""

SYSTEM_SEALING = """你是一位资深汽车密封系统工程师，经历过完整的产品开发周期。
你写的东西必须工程准确：给出标准号、压头几何、试样尺寸、读数方式这类可核对的细节。
只输出 Markdown 正文，不要任何前言或说明。"""


def build_english_prompt(day_no: int, date: dt.date, theme: str, theme_desc: str,
                         word_zone: str) -> str:
    return f"""今天是 {date:%Y-%m-%d}，第 {day_no} 天。

今天的沟通场景：{theme}（{theme_desc}）
今天的词汇取材：{word_zone}

学生情况（写的时候时刻想着他）：
- 汽车密封系统产品开发培训生，日常要和法国老板用 Teams 开会，也常面对面；
- 老板是法国人，口音很重，他经常听不懂；
- 目标不是考试，是**能和外国人无障碍交流**。

写五部分，Markdown 格式，表格用标准 Markdown 表格。
第一行固定为：`# 英语 · {date:%Y-%m-%d}｜{theme}`

## Part 1 今日词汇 24 个（10 分钟）
一个表格，列为：词 / 音标 / 词性 / 释义 / 常用搭配。
- 16 个来自「{word_zone}」，8 个来自会议与职场沟通。
- 音标统一用一种体系（英式或美式），标注重音符号。
- 不要放 seal / meeting / report 这种太基础的词，除非它的搭配容易被用错。
- 表格后面挑 6 个最容易用错的词，各给一句真实工作场景的例句，并说明中国人常犯的错。

## Part 2 常用句式 8 组（5 分钟）
表格，列为：场景 / 直接版 / 委婉版。
场景要贴着他今天的主题「{theme}」和真实工作：汇报、提问、澄清、打断、争取时间、拒绝、确认、收尾。

## Part 3 连词与连接词 6 个（4 分钟）
表格，列为：连词 / 作用 / 例句 / 中国人常犯的错。
挑真正能提升口语连贯度的（比如 however / whereas / therefore / as long as / unless / due to / despite / in terms of / given that），不要放 and / but / so。
最后用 3 句话示范：把三个短句用连词串成一段连贯的汇报。

## Part 4 法国口音听力训练（8 分钟，重点）

### 4.1 法国人说英语的发音特征
表格，列为：特征 / 他会说成 / 你要听成 / 例子。
至少 5 条，覆盖：h 音脱落与乱加、th 变 s/z、词尾重音、/r/ 小舌音、/ɪ/ 与 /iː/ 不分、-ing 读成 -ingg、v/w 混淆、词尾辅音、数字读法（fifteen / fifty）。

### 4.2 三个高频句的「他说的 → 你听的」对照
每条写出：原句 → 他大概会怎么说（用字母模拟发音）→ 你怎么在脑子里还原。
句子要真实，例如他在会上说 "I think we have a problem with the hardness on the B-pillar"。

### 4.3 假朋友（faux amis）
表格：他会用的词 / 他其实想表达 / 你要怎么理解。
至少 4 条，例如 actually≠actuellement、eventually、assist、delay、control、formation、sensible。

### 4.4 一段模拟对话
8–10 轮，法国老板 vs 你。老板的台词要带口音标注（括号里写标准发音），
你的台词要示范「没听清时怎么优雅地让他重复」。

## Part 5 输出任务（2 分钟）
2 条具体任务：一条必须开口录音（给要念的句子），一条必须写（3–5 句）。
检查点写清楚，能自己判断做没做到。

长度：正文 2600–3400 字，必须完整写完 Part 5，不得中途截断。
英文术语首次出现给中文。"""


def build_sealing_prompt(date: dt.date, week_no: int, topic: str) -> str:
    return f"""今天是 {date:%Y-%m-%d}，第 {week_no} 周。本周专题：{topic}

写给汽车密封系统产品开发岗的工程师，一次讲透一个主题。按下面骨架写到底：

1. 一句话定义与它在图纸/流程中的位置
2. 怎么测：标准号、设备与压头几何、试样尺寸、环境条件、读数方式、取值规则
3. 参数族或标尺对比表
4. 典型值表（部位 × 材料 × 数值，标注“工程参考非规范”）
5. 该参数升高/降低的连锁影响表（性能 → 后果）
6. 配方与设计杠杆表
7. 温度 / 时间 / 老化导致的漂移
8. 图纸与规范该怎么写（代码块示例）
9. 5–6 个测量或应用陷阱
10. 延伸到当前产品与技术方向（可提及 Fortrex / FlexiFit / FlexiCore 等公开产品线）
11. 自测 5 题 + 「### 参考答案」

长度：3000–3800 字，必须写完参考答案。
术语首次出现中文 + 英文并列。"""


def build_news_prompt(date: dt.date, feeds: dict[str, list[str]], quotes: list[str]) -> str:
    blocks = []
    for k, v in feeds.items():
        blocks.append(f"### 素材：{k}\n" + ("\n".join(v) if v else "- 未获取到"))
    material = "\n\n".join(blocks)
    quote_text = "、".join(quotes) if quotes else "未获取到"

    return f"""今天是 {date:%Y-%m-%d}。读者想花 6 分钟搞清楚这两天世界上发生了什么重要的事。

# 素材（只能使用这些内容，禁止编造数字、禁止补充素材里没有的事实）

{material}

## 行情快照
{quote_text}

# 输出要求

第一行固定为：`# 每日新闻 · {date:%Y-%m-%d}`

## 今日三件事
3 条，每条一句话说清「发生了什么」，按重要性排序。

## 世界 / ## 中国 / ## 科技与商业 / ## 市场
每块 3–4 条。每条用这个格式：

### 一句话标题
**是什么事**：1–2 句，把事实讲完整，让读者不点原文也知道发生了什么。有数字就写数字，有来源就标来源。
**我的理解**：2–3 句，纯粹是你对这件事本身的判断——为什么重要、会往哪里传导、有什么反直觉的地方、接下来该看什么。

## 一句话总结
用一句话说清今天最该记住的是什么。

# 硬性规则
- 总条数 14–18 条，总长度 1800–2300 字，控制在 6 分钟能读完。
- 选题只看重要性：地缘冲突、政策转向、重大经济数据、科技突破、大公司动向、市场异动、重大社会事件。
- 「我的理解」只谈这件事本身，不要往任何职业或行业上靠，不要给建议。
- 素材是完整正文的可以写细节；只有标题的只写标题能确定的信息，看不懂就整条略过。
- 判断必须与事实分开。
- 不要输出链接。"""


def call_llm(prompt: str, system: str, max_tokens: int, label: str) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("缺少环境变量 DEEPSEEK_API_KEY")
    body = json.dumps({
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.75,
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        data = json.loads(r.read())
    choice = data["choices"][0]
    text = choice["message"]["content"].strip()
    if choice.get("finish_reason") == "length":
        print(f"[warn] {label} 达到 max_tokens，可能被截断", file=sys.stderr)
    return text


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
.sec{margin:2.2em 0 .6em;font-size:.8em;letter-spacing:.08em;text-transform:uppercase;
color:var(--muted);font-weight:700}
.cards{display:grid;gap:12px;margin:.6em 0 1.4em}
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


def _title_of(path: Path) -> str:
    html_path = path.with_suffix(".html")
    if html_path.exists():
        m = re.search(r"<title>(.*?)</title>", html_path.read_text(encoding="utf-8"), re.S)
        if m:
            return html.unescape(m.group(1))
    return path.stem


def rebuild_index() -> None:
    def cards(pattern: str) -> str:
        items = []
        for p in sorted(DOCS.glob(pattern), reverse=True):
            d = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
            if not d:
                continue
            items.append((d.group(1), _title_of(p), p.with_suffix(".html").name))
        return "".join(
            f'<a class="card" href="{html.escape(n)}"><div class="d">{html.escape(d)}</div>'
            f'<div class="t">{html.escape(t)}</div></a>'
            for d, t, n in items
        ) or "<p>还没有内容。</p>"

    body = (
        "<h1>每日英语 · 每日新闻</h1>"
        "<p>每天两份：一份英语（词汇 / 句式 / 连词 / 法国口音听力），一份 6 分钟新闻。</p>"
        '<div class="sec">英语 · 每天</div>'
        f'<div class="cards">{cards("*-english.md")}</div>'
        '<div class="sec">每日新闻</div>'
        f'<div class="cards">{cards("*-news.md")}</div>'
        '<div class="sec">密封系统专题 · 每周日</div>'
        f'<div class="cards">{cards("*-sealing.md")}</div>'
        f"<footer>更新于 {dt.datetime.now(CST):%Y-%m-%d %H:%M}</footer>"
    )
    (DOCS / "index.html").write_text(
        PAGE.format(title="每日英语 · 每日新闻", css=CSS, body=body), encoding="utf-8"
    )


# ---------------------------------------------------------------- 推送


def push(text: str) -> None:
    hook = os.environ.get("PUSH_WEBHOOK", "").strip()
    if not hook:
        print("[push] 未配置 PUSH_WEBHOOK，跳过推送")
        return
    if "feishu" in hook or "larksuite" in hook:
        payload = {"msg_type": "text", "content": {"text": text}}
    else:
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
    forced = os.environ.get("FORCE_DATE", "").strip()
    if forced:
        today = dt.date.fromisoformat(forced)
        print(f"[gen] 补发模式：{today}")
    else:
        today = dt.datetime.now(CST).date()
    day_no = (today - START_DATE).days + 1
    if day_no < 1:
        day_no = 1
    week_no = (today - START_DATE).days // 7 + 1

    theme, theme_desc = theme_for(day_no)
    word_zone = word_zone_for(day_no)
    news_only = os.environ.get("NEWS_ONLY", "").strip() == "1"

    DOCS.mkdir(exist_ok=True)
    english_stem = f"{today:%Y-%m-%d}-english"
    news_stem = f"{today:%Y-%m-%d}-news"
    sealing_stem = f"{today:%Y-%m-%d}-sealing"

    if news_only and (DOCS / f"{english_stem}.md").exists():
        english = (DOCS / f"{english_stem}.md").read_text(encoding="utf-8")
        print("[gen] 仅更新新闻，沿用已有英语文件")
    else:
        print(f"[gen] 英语 Day {day_no} · {theme}")
        english = call_llm(
            build_english_prompt(day_no, today, theme, theme_desc, word_zone),
            SYSTEM_ENGLISH, 8192, "英语")

    feeds = {
        "全球快讯（正文完整）": wallstreetcn("global-channel", 12),
        "中国快讯（正文完整）": sina_flash(16),
        "美股与海外市场（正文完整）": wallstreetcn("us-stock-channel", 8),
        "国际要闻（标题+摘要）": rss_feed("https://feeds.bbci.co.uk/zhongwen/simp/rss.xml", 8),
        "全球要闻（标题+摘要）": rss_feed("https://cn.nytimes.com/rss/", 6),
        "综合要闻（只有标题）": google_news("国际要闻 when:2d", 10),
        "科技与商业（只有标题）": google_news("科技 OR 人工智能 OR 芯片 when:2d", 8),
    }
    quotes = market_quotes()

    print("[gen] 每日新闻")
    bulletin = call_llm(build_news_prompt(today, feeds, quotes), SYSTEM_NEWS, 8192, "新闻")
    if "一句话总结" not in bulletin:
        print("[retry] 新闻输出不完整，重试", file=sys.stderr)
        bulletin = call_llm(build_news_prompt(today, feeds, quotes), SYSTEM_NEWS, 8192, "新闻")

    english_title = f"英语 · {today:%Y-%m-%d}｜{theme}"
    news_title = f"每日新闻 · {today:%Y-%m-%d}"

    (DOCS / f"{english_stem}.md").write_text(english, encoding="utf-8")
    (DOCS / f"{english_stem}.html").write_text(render(english, english_title), encoding="utf-8")
    (DOCS / f"{news_stem}.md").write_text(bulletin, encoding="utf-8")
    (DOCS / f"{news_stem}.html").write_text(render(bulletin, news_title), encoding="utf-8")

    (DOCS / "today.html").write_text(render(english, english_title), encoding="utf-8")
    (DOCS / "today-news.html").write_text(render(bulletin, news_title), encoding="utf-8")

    sealing_title = None
    if today.weekday() == 6:  # 周日出一期密封系统专题
        topic = sealing_topic_for(week_no)
        print(f"[gen] 密封系统专题：{topic}")
        sealing = call_llm(build_sealing_prompt(today, week_no, topic),
                           SYSTEM_SEALING, 8192, "密封专题")
        sealing_title = f"密封系统专题 · {topic}"
        (DOCS / f"{sealing_stem}.md").write_text(sealing, encoding="utf-8")
        (DOCS / f"{sealing_stem}.html").write_text(render(sealing, sealing_title), encoding="utf-8")
        (DOCS / "today-sealing.html").write_text(render(sealing, sealing_title), encoding="utf-8")

    rebuild_index()

    base = site_url()
    lines = [f"【{today:%m-%d}】英语：{theme}", ""]
    if base:
        lines += [f"英语：{base}today.html", f"新闻：{base}today-news.html"]
        if sealing_title:
            lines += [f"密封专题：{base}today-sealing.html"]
    push("\n".join(lines))
    print("[done]", english_stem, news_stem, sealing_title or "")


if __name__ == "__main__":
    main()
