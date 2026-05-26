from __future__ import annotations

import argparse
import json

from agents.daily_agent import run_daily_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="冰箱行业 AI 科技日报自动化系统")
    parser.add_argument("--dry-run", action="store_true", help="只生成报告，不发送邮件")
    parser.add_argument("--send-email", action="store_true", help="生成报告并发送邮件")
    parser.add_argument("--target-time", default="", help="目标发送时间，格式 HH:MM，北京时间")
    parser.add_argument("--sleep-until-target", action="store_true", help="如果早于目标时间启动，则等待到目标时间再运行")
    parser.add_argument("--skip-if-sent", action="store_true", help="当天已成功推送则跳过，避免补跑重复发送")
    parser.add_argument("--force-send", action="store_true", help="忽略当天已推送记录，强制发送")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dry_run = args.dry_run or not args.send_email
    result = run_daily_report(
        dry_run=dry_run,
        send_email=args.send_email,
        target_time=args.target_time or None,
        sleep_until_target=args.sleep_until_target,
        skip_if_sent=args.skip_if_sent,
        force_send=args.force_send,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
