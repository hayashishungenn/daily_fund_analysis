# 国内基金每日分析系统

每天自动分析你关注的国内公募基金，通过 **Telegram Bot** 或**邮件**发送 AI 分析报告，支持 **GitHub Actions** 零成本运行。

---

## ✨ 功能特性

- 📊 **数据获取**：通过 [akshare](https://github.com/akfamily/akshare) 免费获取基金净值、持仓、趋势等数据
- 🤖 **AI 分析**：调用 Gemini / OpenAI 等大模型生成操作建议（加仓/持有/减仓/观望）；无 Key 时自动降级为规则引擎
- 📱 **多渠道推送**：Telegram Bot + 邮件，可同时启用
- ⚙️ **GitHub Actions**：每天北京时间 15:35（A股收盘后）自动运行，完全免费
- 🔧 **灵活配置**：通过环境变量或 `.env` 文件管理所有配置

---

## 🚀 快速开始

### 1. Fork 仓库 / 本地克隆

```bash
git clone https://github.com/YOUR_USERNAME/daily_fund_analysis.git
cd daily_fund_analysis
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的配置
```

**最少配置（仅控制台输出，无推送）：**

```env
FUND_LIST=110022,003095,100032   # 你的自选基金代码
```

**加上 AI 分析（推荐 Gemini，免费）：**

```env
GEMINI_API_KEY=your_key_here   # 从 https://aistudio.google.com/ 获取
```

**加上 Telegram 推送：**

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdef...  # 从 @BotFather 获取
TELEGRAM_CHAT_ID=123456789              # 从 @userinfobot 获取
```

**加上邮件推送：**

```env
EMAIL_SENDER=your_email@qq.com
EMAIL_PASSWORD=your_auth_code   # QQ邮箱授权码，非登录密码
EMAIL_RECEIVERS=                # 留空则发给自己
```

### 4. 运行

```bash
# 完整运行（获取数据 + 分析 + 推送）
python main.py

# 仅获取数据，不分析，不推送
python main.py --dry-run

# 分析但不推送
python main.py --no-notify

# 指定基金（覆盖 FUND_LIST 配置）
python main.py --funds 110022,003095

# 本地定时运行（每天 15:35）
python main.py --schedule
```

---

## ☁️ GitHub Actions 部署

### 配置 Secrets

在 GitHub 仓库 → **Settings → Secrets and variables → Actions** 中添加：

| Secret 名称 | 说明 |
|-------------|------|
| `GEMINI_API_KEY` | Gemini API Key（推荐，可选） |
| `OPENAI_API_KEY` | OpenAI 兼容 API Key（可选） |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID |
| `EMAIL_SENDER` | 发件邮箱 |
| `EMAIL_PASSWORD` | 邮箱授权码 |
| `EMAIL_RECEIVERS` | 收件人（可选） |

### 配置 Variables

在 GitHub 仓库 → **Settings → Secrets and variables → Actions → Variables** 中添加：

| Variable 名称 | 说明 | 默认值 |
|---------------|------|--------|
| `FUND_LIST` | 自选基金列表 | `110022,003095,100032` |
| `REPORT_DAYS` | 分析历史天数 | `30` |
| `GEMINI_MODEL` | Gemini 模型 | `gemini-2.0-flash` |

### 触发时间

- **自动触发**：每天北京时间 **15:35**（周一至周五，A股收盘后）
- **手动触发**：Actions → 每日基金分析 → Run workflow

---

## 📊 报告示例

```
📊 基金每日分析报告
日期：2026-02-22　更新：15:36　分析基金：3 只
建议汇总：🟢加仓 1只　🔵持有 1只　🟡观望 1只

━━━━━━━━━━━━━━━━━━
🟢 易方达蓝筹精选（110022）
净值：3.2150 (+0.58%)　更新：2026-02-21
类型：混合型　经理：张坤

📊 技术指标
近7日：+1.20%　近30日：+4.20%　近90日：+8.50%
最大回撤：-6.10%　📈 多头排列
MA5: 3.1980　MA10: 3.1500　MA20: 3.0800

🏢 前五大持仓
  1. 贵州茅台(600519) 9.8%
  2. 五粮液(000858) 7.2%

💡 建议：加仓 — 趋势良好，多头排列，近30日收益+4.2%，可适量加仓
⚠️ 风险：市场波动风险，请控制仓位
```

---

## 📦 项目结构

```
daily_fund_analysis/
├── .github/workflows/
│   └── daily_fund_analysis.yml  # GitHub Actions
├── src/
│   ├── config.py          # 配置管理
│   ├── logging_config.py  # 日志配置
│   ├── fund_data.py       # 数据获取（akshare）
│   ├── analyzer.py        # AI 分析
│   ├── report.py          # 报告生成
│   ├── notification.py    # 通知推送
│   └── scheduler.py       # 定时调度
├── main.py                # 主程序
├── requirements.txt
├── .env.example           # 配置模板
└── .gitignore
```

---

## 🆓 常用免费基金代码

| 代码 | 名称 | 类型 |
|------|------|------|
| 110022 | 易方达蓝筹精选 | 混合型 |
| 003095 | 中欧医疗健康 | 股票型 |
| 100032 | 富国天惠成长 | 混合型 |
| 161725 | 招商中证白酒 | 指数型 |
| 519671 | 银河创新成长 | 股票型 |
| 007119 | 沪深300ETF | 指数型 |

> 基金代码为**天天基金**（eastmoney）编码，可在 [fund.eastmoney.com](https://fund.eastmoney.com) 查询。

---

## ⚠️ 免责声明

本项目仅供学习研究，自动生成的分析报告**不构成投资建议**。基金投资有风险，请根据自身风险承受能力谨慎决策。
