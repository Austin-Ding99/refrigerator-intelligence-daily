from __future__ import annotations

import argparse
import json

from agents.daily_agent import run_daily_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="冰箱行业 AI 科技日报自动化系统")
    parser.add_argument("--dry-run", action="store_true", help="只生成报告，不发送邮件")
    parser.add_argument("--send-email", action="store_true", help="生成报告并发送邮件")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dry_run = args.dry_run or not args.send_email
    result = run_daily_report(dry_run=dry_run, send_email=args.send_email)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
