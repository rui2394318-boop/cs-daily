# 库博标准 · 每日学习包（云端版）

> 每天 07:30（北京时间）自动生成一份学习包，电脑不用开机。
> 英语 25 分钟 · 产品专题 25 分钟 · 新闻解读 10 分钟。
> 内容推送到手机（飞书/钉钉/企业微信），同时在网页上留档。

---

## 它是怎么跑的

```
GitHub Actions（每天 07:30）
      │
      ├─ 抓新闻：Google News RSS（世界/中国/汽车/零部件/市场，限近 7 天）
      ├─ 抓行情：Yahoo Finance（上证、深证、创业板、科创50、标普、纳指、道指、CPS）
      ├─ 调 DeepSeek：按 30 天课表生成当天学习包
      ├─ 写页面：docs/*.html → GitHub Pages（手机浏览器可看，可加到主屏幕）
      └─ 推送：群机器人 webhook → 手机弹通知
```

课程表在 `curriculum.py`，改了它就能改每天的主题。日期从 **2026-10-31 = Day 01** 起算，第 30 天之后循环。

---

## 一次性配置（约 10 分钟）

### 1. 建仓库并上传

1. 打开 <https://github.com/new>，仓库名随意（例如 `cs-daily`），**Public**（Pages 免费需要公开仓库）。
2. 把这个文件夹里的**全部内容**上传（含隐藏的 `.github` 目录）：
   - 网页上传：Add file → Upload files → 把文件夹里的东西全选拖进去 → Commit
   - 或用 Git 命令：
     ```bash
     cd cooper-standard-cloud
     git init -b main
     git add -A
     git commit -m "init"
     git remote add origin https://github.com/<你的用户名>/cs-daily.git
     git push -u origin main
     ```

### 2. 填 DeepSeek API Key

1. 去 <https://platform.deepseek.com/api_keys> 创建 Key（充值 10 元够用很久，一天一次调用约几分钱）。
2. 仓库 → **Settings → Secrets and variables → Actions → New repository secret**
   - Name：`DEEPSEEK_API_KEY`
   - Secret：粘贴你的 Key

### 3. 配置手机推送（推荐，不配也能用网页看）

1. **飞书**：建一个只有自己的群 → 群设置 → 群机器人 → 添加「自定义机器人」→ 复制 Webhook 地址
   （钉钉：群设置 → 智能群助手 → 自定义；企业微信：群 → 群机器人 → 添加）
2. 回到仓库 → Settings → Secrets and variables → Actions → New repository secret
   - Name：`PUSH_WEBHOOK`
   - Secret：粘贴 Webhook 地址

> 脚本会自动识别飞书 / 钉钉 / 企业微信的格式，不用改代码。

### 4. 打开网页版（可选但建议）

1. 仓库 → **Settings → Pages**
2. Source 选 **Deploy from a branch**，Branch 选 `main`，目录选 **`/docs`** → Save
3. 等一两分钟，地址是：`https://<你的用户名>.github.io/cs-daily/`

**iPhone 加到主屏幕**：Safari 打开上面的地址 → 分享 → 添加到主屏幕，以后一点就开，和 App 一样。

### 5. 先手动跑一次

仓库 → **Actions** → 左侧 `daily-lesson` → 右侧 **Run workflow** → 等 1–2 分钟。

成功后你会看到：`docs/` 里多了一篇当天的网页，手机上也收到了推送。

---

## 常见问题

**能改时间吗？**
改 `.github/workflows/daily.yml` 里的 `cron: "30 23 * * *"`。它是 **UTC 时间**，北京时间 = UTC+8。
例：想早上 6:30 推送 → `30 22 * * *`。

**GitHub 的定时任务准时吗？**
免费版在高峰期可能延迟几分钟到一小时（GitHub 官方说明），属于正常现象。

**会不会泄露我的 Key？**
Key 存在仓库 Secrets 里，不会出现在代码或日志中。别把 Key 直接写进文件再上传。

**推送太长被截断？**
推送只发摘要 + 网页链接，全文在 Pages 上看，不会截断。

**想改每天学什么？**
编辑 `curriculum.py` 里的 `PLAN` 列表，两个元素分别是「英语重点」和「产品专题」。

**抓不到新闻怎么办？**
脚本会降级：新闻部分写「未获取到」，英语和产品部分照常生成，不影响使用。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `daily.py` | 主程序：抓数据 → 生成 → 渲染 → 推送 |
| `curriculum.py` | 30 天课程表 |
| `.github/workflows/daily.yml` | 定时任务定义 |
| `requirements.txt` | 依赖（只有 markdown） |
| `docs/` | 生成的网页（GitHub Pages 根目录） |
