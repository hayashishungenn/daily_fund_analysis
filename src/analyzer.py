# -*- coding: utf-8 -*-
"""
AI 分析模块
- 优先使用 Gemini，备选 OpenAI 兼容 API
- 无 Key 时降级为规则引擎生成简单建议
"""
import logging
import time
from typing import Optional

from src.config import Config
from src.fund_data import FundAnalysisData

logger = logging.getLogger(__name__)

# 分析结果
ADVICE_OPTIONS = ("加仓", "持有", "减仓", "观望")


def _build_prompt(data: FundAnalysisData) -> str:
    info = data.info
    hist = data.history
    holdings_str = ""
    if data.top_holdings:
        lines = [f"  {i+1}. {h['name']}({h['code']}) {h['ratio']:.1f}%" for i, h in enumerate(data.top_holdings[:5])]
        holdings_str = "\n前五大持仓:\n" + "\n".join(lines)

    nav_series = ""
    if hist.navs and hist.dates:
        pairs = list(zip(hist.dates, hist.navs))[-10:]
        nav_series = " | ".join(f"{d}: {n:.4f}" for d, n in pairs)

    # Pre-compute optional MA strings — inline :.4f inside ternary causes format spec errors
    def _fmt_ma(v) -> str:
        try:
            return f"{float(v):.4f}" if v is not None else "N/A"
        except (TypeError, ValueError):
            return "N/A"
    ma5_str = _fmt_ma(hist.ma5)
    ma10_str = _fmt_ma(hist.ma10)
    ma20_str = _fmt_ma(hist.ma20)
    prompt = f"""你是一位专注于国内公募基金的资深分析师，请根据以下数据对该基金进行简短分析并给出操作建议。

【基金信息】
代码：{info.code}
名称：{info.name}
类型：{info.fund_type}
基金经理：{info.manager}
规模（亿元）：{info.size_billion:.2f}
最新净值：{info.latest_nav:.4f}（{info.latest_date}）
今日涨跌：{info.nav_change_pct:+.2f}%

【技术指标】
近7日收益：{hist.ret_7d:+.2f}%
近30日收益：{hist.ret_30d:+.2f}%
近90日收益：{hist.ret_90d:+.2f}%
最大回撤：{hist.max_drawdown_pct:.2f}%
MA5：{ma5_str}
MA10：{ma10_str}
MA20：{ma20_str}
趋势信号：{hist.trend_signal}

【近10日净值】
{nav_series}
{holdings_str}

请输出以下格式（不要添加其他内容）：
操作建议：<加仓|持有|减仓|观望>
理由：<100字以内，要有具体数据支撑>
风险提示：<30字以内>"""
    return prompt


def _rule_engine_advice(data: FundAnalysisData) -> dict:
    """无 AI Key 时的规则引擎"""
    hist = data.history

    # 多头排列 + 近30天正收益 -> 持有
    if hist.trend_signal == "多头排列" and hist.ret_30d > 0:
        advice = "持有"
        reason = (
            f"均线多头排列（MA5>MA10>MA20），近30日收益 {hist.ret_30d:+.2f}%，趋势向好。"
        )
    # 空头排列 + 大回撤 -> 观望/减仓
    elif hist.trend_signal == "空头排列" and hist.max_drawdown_pct < -10:
        advice = "减仓"
        reason = (
            f"均线空头排列，最大回撤 {hist.max_drawdown_pct:.2f}%，下行压力较大，建议减仓观望。"
        )
    elif hist.ret_30d < -5:
        advice = "观望"
        reason = f"近30日收益 {hist.ret_30d:+.2f}%，短期走弱，建议等待企稳信号再介入。"
    elif hist.ret_30d > 5 and hist.trend_signal != "空头排列":
        advice = "加仓"
        reason = f"近30日收益 {hist.ret_30d:+.2f}%，趋势较好，可适量加仓。"
    else:
        advice = "持有"
        reason = f"净值走势平稳，近30日收益 {hist.ret_30d:+.2f}%，维持现有仓位即可。"

    return {
        "advice": advice,
        "reason": reason,
        "risk": "基金投资有风险，过往业绩不代表未来表现。",
    }


def _parse_ai_response(text: str) -> dict:
    """解析 AI 输出文本"""
    result = {"advice": "观望", "reason": text[:200], "risk": "投资有风险，请谨慎决策。"}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("操作建议：") or line.startswith("操作建议:"):
            val = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            for opt in ADVICE_OPTIONS:
                if opt in val:
                    result["advice"] = opt
                    break
        elif line.startswith("理由：") or line.startswith("理由:"):
            result["reason"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif line.startswith("风险提示：") or line.startswith("风险提示:"):
            result["risk"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
    return result


def analyze_with_gemini(data: FundAnalysisData, config: Config) -> Optional[dict]:
    """使用 Gemini 生成分析"""
    try:
        import google.generativeai as genai
        genai.configure(api_key=config.gemini_api_key)
        model = genai.GenerativeModel(config.gemini_model)
        prompt = _build_prompt(data)
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(temperature=config.gemini_temperature),
        )
        return _parse_ai_response(response.text)
    except Exception as e:
        logger.warning(f"[{data.info.code}] Gemini 分析失败: {e}")
        return None


def analyze_with_openai(data: FundAnalysisData, config: Config) -> Optional[dict]:
    """使用 OpenAI 兼容 API 生成分析"""
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url or None,
        )
        prompt = _build_prompt(data)
        response = client.chat.completions.create(
            model=config.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.openai_temperature,
            max_tokens=512,
        )
        return _parse_ai_response(response.choices[0].message.content)
    except Exception as e:
        logger.warning(f"[{data.info.code}] OpenAI 分析失败: {e}")
        return None


def analyze_fund(data: FundAnalysisData, config: Config, request_delay: float = 2.0) -> dict:
    """
    主分析入口：依次尝试 Gemini -> OpenAI -> 规则引擎
    返回 dict: {"advice": str, "reason": str, "risk": str}
    """
    if data.error:
        return {"advice": "观望", "reason": f"数据获取失败: {data.error}", "risk": "暂无数据，请手动核查。"}

    result = None

    if config.gemini_api_key:
        result = analyze_with_gemini(data, config)
        if result:
            logger.info(f"[{data.info.code}] Gemini 分析完成: {result['advice']}")
            time.sleep(request_delay)
            return result

    if config.openai_api_key:
        result = analyze_with_openai(data, config)
        if result:
            logger.info(f"[{data.info.code}] OpenAI 分析完成: {result['advice']}")
            time.sleep(request_delay)
            return result

    logger.info(f"[{data.info.code}] 使用规则引擎生成建议")
    return _rule_engine_advice(data)
