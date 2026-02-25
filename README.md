# 国内基金每日分析系统（小白教程版）

每天自动分析你的基金清单，生成两种风格报告，并推送到：

- Telegram Bot
- 邮件（QQ/163/Gmail/Outlook 等 SMTP）

这是一个**基金每日分析报告**项目，报告结构参考  
[hayashishungenn/daily_stock_analysis](https://github.com/hayashishungenn/daily_stock_analysis)，支持：

- `simple`：精简快读（适合手机快速浏览）
- `full`：完整仪表盘（适合邮件/复盘）

---

## 你将得到什么

运行一次后，你会收到一份基金日报，包含：

- 操作建议汇总（加仓/持有/减仓/观望）
- 重点关注（偏强基金 + 风险预警）
- 快速总览表（信号分、风险等级、30日收益、回撤、趋势）
- 每只基金的详细分析与持仓摘要

---

## 目录

- [1. 先看 30 秒快速上手](#1-先看-30-秒快速上手)
- [2. 环境准备（小白必看）](#2-环境准备小白必看)
- [3. 安装项目](#3-安装项目)
- [4. 配置 .env（逐项解释）](#4-配置-env逐项解释)
- [5. 运行项目（本地）](#5-运行项目本地)
- [6. 两种报告格式（simple / full）](#6-两种报告格式simple--full)
- [7. Telegram 配置教程](#7-telegram-配置教程)
- [8. 邮箱配置教程](#8-邮箱配置教程)
- [9. GitHub Actions 自动运行（可选）](#9-github-actions-自动运行可选)
- [10. 常见问题排查](#10-常见问题排查)
- [11. 项目结构](#11-项目结构)

---

## 1. 先看 30 秒快速上手

如果你已经有 Python：

```powershell
git clone https://github.com/YOUR_USERNAME/daily_fund_analysis.git
cd daily_fund_analysis
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

然后编辑 `.env`，至少填这几项：

```env
FUND_LIST=110022,003095,100032
REPORT_TYPE=full

TELEGRAM_BOT_TOKEN=你的token
TELEGRAM_CHAT_ID=你的chat_id

EMAIL_SENDER=你的邮箱
EMAIL_PASSWORD=邮箱授权码
EMAIL_RECEIVERS=你的收件邮箱
```

最后运行：

```powershell
.\.venv\Scripts\python.exe main.py
```

---

## 2. 环境准备（小白必看）

### 2.1 安装 Python

- 推荐 Python `3.10+`（建议 `3.11`）
- Windows 安装时勾选 `Add python.exe to PATH`

检查是否安装成功：

```powershell
python --version
```

如果命令无效，请重开终端后再试。

### 2.2 安装 Git

```powershell
git --version
```

---

## 3. 安装项目

### Windows（PowerShell）

```powershell
git clone https://github.com/YOUR_USERNAME/daily_fund_analysis.git
cd daily_fund_analysis
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### macOS / Linux

```bash
git clone https://github.com/YOUR_USERNAME/daily_fund_analysis.git
cd daily_fund_analysis
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 4. 配置 .env（逐项解释）

先复制模板：

```powershell
copy .env.example .env
```

### 核心配置（必填）

```env
FUND_LIST=110022,003095,100032
REPORT_DAYS=30
REPORT_TYPE=full
```

说明：

- `FUND_LIST`：你的基金代码，逗号分隔
- `REPORT_DAYS`：回看天数
- `REPORT_TYPE`：
  - `simple` = 精简版
  - `full` = 完整版（默认，推荐）

### AI 配置（可选，但推荐）

```env
GEMINI_API_KEY=你的GeminiKey
GEMINI_MODEL=gemini-2.0-flash
GEMINI_TEMPERATURE=0.7
```

不填也能跑，会自动使用规则引擎分析。

### Telegram 配置（可选）

```env
TELEGRAM_BOT_TOKEN=123456:abcdef...
TELEGRAM_CHAT_ID=123456789
```

### 邮箱配置（可选）

```env
EMAIL_SENDER=your_email@qq.com
EMAIL_PASSWORD=邮箱授权码
EMAIL_RECEIVERS=receiver1@gmail.com,receiver2@qq.com
EMAIL_SENDER_NAME=基金每日分析助手
EMAIL_DEDUP_ENABLED=true
EMAIL_DEDUP_WINDOW_MINUTES=180
```

说明：

- `EMAIL_DEDUP_ENABLED=true`：开启邮件去重
- `EMAIL_DEDUP_WINDOW_MINUTES=180`：180 分钟内同内容不重复发送

### 系统配置（一般默认即可）

```env
MAX_WORKERS=3
DATA_SOURCE_CONNECT_TIMEOUT=3.0
DATA_SOURCE_READ_TIMEOUT=8.0
DATA_SOURCE_MAX_RETRIES=0
DATA_SOURCE_RETRY_BACKOFF=0.0
LOG_LEVEL=INFO
USE_PROXY=false
```

---

## 5. 运行项目（本地）

### 5.1 正常运行（分析 + 推送）

```powershell
.\.venv\Scripts\python.exe main.py
```

### 5.2 只看数据，不推送

```powershell
.\.venv\Scripts\python.exe main.py --no-notify
```

### 5.3 指定基金（覆盖 `FUND_LIST`）

```powershell
.\.venv\Scripts\python.exe main.py --funds 110022,003095
```

### 5.4 指定报告格式

```powershell
.\.venv\Scripts\python.exe main.py --report-type simple
.\.venv\Scripts\python.exe main.py --report-type full
```

---

## 6. 两种报告格式（simple / full）

### simple（精简快读）

适合在 Telegram 或手机通知中快速看：

- 建议汇总
- 今日重点
- 基金清单（按信号分排序）

启用方式：

```env
REPORT_TYPE=simple
```

### full（完整仪表盘）

适合邮件和复盘：

- 建议汇总
- 数据质量
- 今日关注
- 快速总览
- 按建议分组的详细分析

启用方式：

```env
REPORT_TYPE=full
```

---

## 7. Telegram 配置教程

1. 打开 Telegram，搜索 `@BotFather`
2. 发送 `/newbot` 创建机器人，拿到 `TELEGRAM_BOT_TOKEN`
3. 给你的机器人发一条消息（这一步必须做）
4. 获取 Chat ID：
   - 方法 A：找 `@userinfobot`
   - 方法 B：访问  
     `https://api.telegram.org/bot<你的token>/getUpdates`
5. 将 token 和 chat_id 写入 `.env`

---

## 8. 邮箱配置教程

### QQ 邮箱

1. QQ 邮箱设置 -> 账户
2. 开启 `POP3/SMTP服务`
3. 生成“授权码”（不是登录密码）
4. `EMAIL_PASSWORD` 填授权码

常用 SMTP：

- QQ：`smtp.qq.com:465`
- 163：`smtp.163.com:465`
- Gmail：`smtp.gmail.com:587`

本项目会根据邮箱域名自动识别 SMTP。

---

## 9. GitHub Actions 自动运行（可选）

如果你不想每天手动运行，可以用 GitHub Actions。

### 9.1 配置 Secrets

仓库 -> `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`

建议添加：

- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `EMAIL_SENDER`
- `EMAIL_PASSWORD`
- `EMAIL_RECEIVERS`

### 9.2 配置 Variables

同页 `Variables` 添加：

- `FUND_LIST`
- `REPORT_DAYS`
- `REPORT_TYPE`

### 9.3 手动触发

`Actions` -> 对应 workflow -> `Run workflow`

---

## 10. 常见问题排查

### Q1: 邮件收到两份内容

检查：

```env
EMAIL_DEDUP_ENABLED=true
EMAIL_DEDUP_WINDOW_MINUTES=180
```

### Q2: 提示代理连接失败（127.0.0.1:9）

请确保：

```env
USE_PROXY=false
```

并重新运行（项目会自动清理残留代理环境变量）。

### Q3: Telegram 不推送

- 机器人是否已创建成功
- 是否给机器人发过消息
- `TELEGRAM_CHAT_ID` 是否正确

### Q4: 报告只分析了 1 只基金

可能是你使用了 `--funds` 参数，它会覆盖 `.env` 的 `FUND_LIST`。

### Q5: 中文/emoji 打印报错（Windows）

用下面方式运行：

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe main.py
```

---

## 11. 项目结构

```text
daily_fund_analysis/
├── .github/workflows/
│   └── daily_fund_analysis.yml
├── src/
│   ├── config.py
│   ├── logging_config.py
│   ├── fund_data.py
│   ├── analyzer.py
│   ├── report.py
│   ├── notification.py
│   └── scheduler.py
├── main.py
├── .env.example
├── requirements.txt
└── README.md
```

---

## 免责声明

本项目仅用于学习与研究，不构成任何投资建议。基金有风险，投资需谨慎。
