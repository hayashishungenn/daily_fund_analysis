# -*- coding: utf-8 -*-
"""
===================================
国内基金每日分析系统 - 主调度程序
===================================

使用方式：
    python main.py              # 正常运行（分析 + 推送）
    python main.py --dry-run    # 仅获取数据，不 AI 分析，不推送
    python main.py --no-notify  # 分析但不推送
    python main.py --funds 110022,003095  # 指定基金（覆盖配置）
    python main.py --report-type summary  # 仅汇总报告
    python main.py --schedule   # 定时任务模式
"""
import os

# 代理配置 - GitHub Actions 自动跳过
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url
else:
    # 显式关闭代理，避免继承到系统/会话中的残留代理变量
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(key, None)

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from src.config import get_config
from src.logging_config import setup_logging
from src.fund_data import fetch_fund_data, fetch_market_context, FundAnalysisData, FundInfo, FundHistory
from src.http_fastfail import install_requests_fast_fail
from src.analyzer import analyze_fund
from src.report import generate_report, save_report
from src.notification import send_report
from src.workday import should_run_today

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="国内基金每日分析系统",
        epilog="""
示例:
  python main.py                      # 正常运行
  python main.py --dry-run            # 仅获取数据，不分析不推送
  python main.py --no-notify          # 分析但不推送
  python main.py --funds 110022,003095  # 指定基金覆盖配置
  python main.py --report-type summary  # 仅汇总报告
  python main.py --schedule           # 定时任务模式
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--debug", action="store_true", help="调试模式")
    parser.add_argument("--dry-run", action="store_true", help="仅获取数据，不分析，不推送")
    parser.add_argument("--no-notify", action="store_true", help="分析但不发送通知")
    parser.add_argument("--funds", type=str, default=None, help="指定基金代码，逗号分隔")
    parser.add_argument("--schedule", action="store_true", help="启用定时任务模式")
    parser.add_argument("--schedule-time", type=str, default=None, help="定时执行时间 HH:MM，默认 14:00")
    parser.add_argument("--no-run-immediately", action="store_true", help="定时模式启动时不立即执行")
    parser.add_argument("--force-run", action="store_true", help="忽略法定工作日限制，强制执行")
    parser.add_argument(
        "--report-type",
        type=str,
        choices=["summary", "simple", "full"],
        default=None,
        help="报告格式：summary(仅汇总) / simple(精简) / full(完整)，默认读取 REPORT_TYPE",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# 核心流程
# ---------------------------------------------------------------------------

def run_analysis(
    fund_codes: List[str],
    dry_run: bool = False,
    send_notification: bool = True,
    max_workers: int = 1,
    report_type: Optional[str] = None,
):
    """执行完整分析流程并推送报告"""
    config = get_config()
    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"开始分析 {len(fund_codes)} 只基金: {', '.join(fund_codes)}")

    # === 1. 数据获取（并发） ===
    data_by_code = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {
            pool.submit(
                fetch_fund_data,
                code=code,
                report_days=config.report_days,
                market=None,
                backtest_enabled=config.backtest_enabled,
                backtest_forward_points=config.backtest_forward_points,
                backtest_min_train_points=config.backtest_min_train_points,
                backtest_neutral_band_pct=config.backtest_neutral_band_pct,
            ): code
            for code in fund_codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                data = future.result()
            except Exception as e:
                logger.error(f"[{code}] 数据获取线程异常: {e}")
                data = FundAnalysisData(
                    info=FundInfo(code=code),
                    history=FundHistory(code=code),
                    error=f"数据获取线程异常: {e}",
                )
            data_by_code[code] = data

    # 按用户配置顺序重建结果，确保输出覆盖全部 FUND_LIST 代码
    all_data: List[FundAnalysisData] = []
    for code in fund_codes:
        data = data_by_code.get(code)
        if data is None:
            logger.error(f"[{code}] 未返回数据，自动填充占位结果")
            data = FundAnalysisData(
                info=FundInfo(code=code),
                history=FundHistory(code=code),
                error="未返回数据",
            )
        all_data.append(data)

    if len(all_data) != len(fund_codes):
        logger.warning(f"数据数量异常: 输入 {len(fund_codes)}，输出 {len(all_data)}")

    if not all_data:
        logger.error("所有基金数据获取失败，退出")
        return

    # === 2. Dry-run 模式：仅展示数据 ===
    if dry_run:
        logger.info("Dry-run 模式，跳过 AI 分析和推送")
        for d in all_data:
            logger.info(f"  [{d.info.code}] {d.info.name} 净值={d.info.latest_nav:.4f} 趋势={d.history.trend_signal}")
        return

    # === 3. AI 分析 ===
    results = []
    for data in all_data:
        analysis = analyze_fund(data, config)
        results.append((data, analysis))
        logger.info(f"  [{data.info.code}] {data.info.name} -> {analysis['advice']}")

    # === 4. 生成报告 ===
    if config.report_summary_only and report_type is None:
        effective_report_type = "summary"
    else:
        effective_report_type = (report_type or config.report_type or "full").strip().lower()

    if effective_report_type not in ("summary", "simple", "full"):
        logger.warning(f"未知报告格式 {effective_report_type}，自动回退为 full")
        effective_report_type = "full"

    logger.info(f"使用报告格式: {effective_report_type}")
    report_content = generate_report(
        results,
        report_days=config.report_days,
        report_type=effective_report_type,
    )
    report_path = save_report(report_content, report_dir="./reports")

    # 打印到控制台
    print("\n" + "=" * 60)
    print(report_content)
    print("=" * 60 + "\n")

    # === 5. 发送通知 ===
    if send_notification:
        send_results = send_report(report_content, config)
        for channel, ok in send_results.items():
            status = "✅ 成功" if ok else "❌ 失败"
            logger.info(f"通知渠道 [{channel}]: {status}")
    else:
        logger.info("已跳过推送（--no-notify）")

    logger.info(f"分析完成，报告: {report_path}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_arguments()
    config = get_config()

    setup_logging(log_prefix="fund_analysis", debug=args.debug, log_dir=config.log_dir)

    logger.info("=" * 60)
    logger.info("国内基金每日分析系统 启动")
    logger.info(f"运行时间: {datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # 验证配置
    for w in config.validate():
        logger.warning(w)

    # 给数据源请求加短超时 + 快失败策略（覆盖 xalpha / AkShare 底层 requests）
    install_requests_fast_fail(
        connect_timeout=config.data_source_connect_timeout,
        read_timeout=config.data_source_read_timeout,
        max_retries=config.data_source_max_retries,
        retry_backoff=config.data_source_retry_backoff,
    )

    # 解析基金列表
    if args.funds:
        fund_codes = [c.strip() for c in args.funds.split(",") if c.strip()]
        logger.info(f"使用命令行指定基金: {fund_codes}")
    else:
        fund_codes = config.fund_list
        logger.info(f"使用配置基金列表: {fund_codes}")

    if not fund_codes:
        logger.error("基金列表为空，请配置 FUND_LIST 或使用 --funds 参数")
        return 1

    def task():
        can_run, day_desc = should_run_today(
            workday_only=config.cn_workday_only,
            force_run=args.force_run,
        )
        if not can_run:
            logger.info(f"今日 {day_desc}，跳过执行。可使用 --force-run 强制执行。")
            return
        logger.info(f"工作日检查通过：{day_desc}")
        run_analysis(
            fund_codes=fund_codes,
            dry_run=args.dry_run,
            send_notification=not args.no_notify,
            max_workers=config.max_workers,
            report_type=args.report_type,
        )

    try:
        if args.schedule:
            from src.scheduler import run_with_schedule
            schedule_time = args.schedule_time or config.schedule_time or "14:00"
            run_immediately = config.run_immediately and (not args.no_run_immediately)
            run_with_schedule(
                task=task,
                schedule_time=schedule_time,
                run_immediately=run_immediately,
            )
        else:
            task()

        logger.info("程序执行完成")
        return 0

    except KeyboardInterrupt:
        logger.info("用户中断，退出")
        return 130

    except Exception as e:
        logger.exception(f"程序执行失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
