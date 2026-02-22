# -*- coding: utf-8 -*-
"""
报告生成模块 - 生成 Markdown 格式的基金分析报告
"""
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Tuple

from src.fund_data import FundAnalysisData

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))

_ADVICE_EMOJI = {
    "加仓": "🟢",
    "持有": "🔵",
    "减仓": "🔴",
    "观望": "🟡",
}

_TREND_EMOJI = {
    "多头排列": "📈",
    "空头排列": "📉",
    "震荡": "➡️",
}


def _fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _single_fund_block(data: FundAnalysisData, analysis: dict) -> str:
    """生成单只基金的 Markdown 文本块"""
    info = data.info
    hist = data.history
    advice = analysis.get("advice", "观望")
    reason = analysis.get("reason", "")
    risk = analysis.get("risk", "")

    trend_icon = _TREND_EMOJI.get(hist.trend_signal, "➡️")
    advice_icon = _ADVICE_EMOJI.get(advice, "⚪")

    lines = [
        f"━━━━━━━━━━━━━━━━━━",
        f"{advice_icon} **{info.name}**（{info.code}）",
        f"净值：**{info.latest_nav:.4f}** ({_fmt_pct(info.nav_change_pct)})　更新：{info.latest_date}",
        f"类型：{info.fund_type}　经理：{info.manager}",
        f"",
        f"📊 **技术指标**",
        f"近7日：{_fmt_pct(hist.ret_7d)}　近30日：{_fmt_pct(hist.ret_30d)}　近90日：{_fmt_pct(hist.ret_90d)}",
        f"最大回撤：{hist.max_drawdown_pct:.2f}%　{trend_icon} {hist.trend_signal}",
    ]

    if hist.ma5 or hist.ma10 or hist.ma20:
        ma_parts = []
        if hist.ma5:
            ma_parts.append(f"MA5: {hist.ma5:.4f}")
        if hist.ma10:
            ma_parts.append(f"MA10: {hist.ma10:.4f}")
        if hist.ma20:
            ma_parts.append(f"MA20: {hist.ma20:.4f}")
        lines.append("　".join(ma_parts))

    if data.top_holdings:
        lines.append("")
        lines.append("🏢 **前五大持仓**")
        for i, h in enumerate(data.top_holdings[:5]):
            lines.append(f"  {i+1}. {h['name']}({h['code']}) {h['ratio']:.1f}%")

    lines += [
        "",
        f"💡 **建议：{advice}** — {reason}",
        f"⚠️ 风险：{risk}",
    ]

    return "\n".join(lines)


def generate_report(
    results: List[Tuple[FundAnalysisData, dict]],
    report_days: int = 30,
) -> str:
    """
    生成完整 Markdown 报告

    Args:
        results: [(FundAnalysisData, analysis_dict), ...]
        report_days: 分析天数

    Returns:
        Markdown 字符串
    """
    now = datetime.now(TZ_CN)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    # 统计建议分布
    advice_count: dict = {}
    for _, a in results:
        adv = a.get("advice", "观望")
        advice_count[adv] = advice_count.get(adv, 0) + 1

    header = [
        f"# 📊 基金每日分析报告",
        f"",
        f"**日期**：{date_str}　**更新**：{time_str}　**分析基金**：{len(results)} 只",
        f"",
        f"**建议汇总**：" + "　".join(
            f"{_ADVICE_EMOJI.get(k, '⚪')}{k} {v}只" for k, v in advice_count.items()
        ),
        f"",
    ]

    blocks = []
    for data, analysis in results:
        blocks.append(_single_fund_block(data, analysis))

    footer = [
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"> ⚠️ 本报告由程序自动生成，仅供参考，不构成投资建议。",
        f"> 数据来源：东方财富（akshare），AI 分析：Gemini / OpenAI",
    ]

    return "\n".join(header + blocks + footer)


def save_report(content: str, report_dir: str = "./reports") -> str:
    """保存报告到文件，返回文件路径"""
    path = Path(report_dir)
    path.mkdir(parents=True, exist_ok=True)
    filename = path / f"fund_report_{datetime.now(TZ_CN).strftime('%Y%m%d_%H%M')}.md"
    filename.write_text(content, encoding="utf-8")
    logger.info(f"报告已保存: {filename}")
    return str(filename)


def split_message(text: str, max_len: int = 4000) -> List[str]:
    """将长 Markdown 文本按块分割，用于 Telegram 等有长度限制的渠道"""
    if len(text) <= max_len:
        return [text]

    parts = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_len:
            parts.append(current.strip())
            current = line
        else:
            current += line
    if current.strip():
        parts.append(current.strip())
    return parts
