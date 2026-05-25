from __future__ import annotations

import json
import logging
import os
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from collectors.search import ReportItem, dedupe_items, filter_recent, score_item
from collectors.sources import collect_all_items, load_sources
from renderers.report import render_html, render_markdown, write_outputs
from summarizers.llm import summarize_once


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
SCORE_THRESHOLD = 0.72


def setup_logging(now: datetime) -> None:
    Path("logs").mkdir(exist_ok=True)
    log_path = Path("logs") / f"daily_report_{now.strftime('%Y-%m-%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )


def run_daily_report(dry_run: bool = True, send_email: bool = False) -> dict:
    load_dotenv()
    now = datetime.now(BEIJING_TZ)
    setup_logging(now)
    report_date = now.strftime("%Y-%m-%d")
    logging.info("Starting refrigerator industry AI daily report for %s", report_date)

    config = load_sources()
    raw_items = collect_all_items(config)
    logging.info("Collected %s raw items", len(raw_items))

    naive_now = now.replace(tzinfo=None)
    recent_items = filter_recent(raw_items, naive_now, hours=24, patent_days=7)
    scored_items = [
        score_item(item, config.get("trusted_sources", {}))
        for item in recent_items
    ]
    filtered_items = [item for item in scored_items if item.score > SCORE_THRESHOLD]
    deduped_items = dedupe_items(filtered_items)
    logging.info("Kept %s items after recent filter, scoring and dedupe", len(deduped_items))

    summary = summarize_once(deduped_items)
    markdown_text = render_markdown(summary, report_date, now)
    html_text = render_html(markdown_text)
    md_path, html_path = write_outputs(markdown_text, html_text, report_date)
    json_path = write_json_output(summary, deduped_items, report_date)

    email_status = "skipped"
    if send_email and not dry_run:
        email_status = send_report_email(html_text, markdown_text, report_date)
    else:
        logging.info("Dry run enabled or email not requested; email sending skipped.")

    return {
        "date": report_date,
        "items": len(deduped_items),
        "markdown": str(md_path),
        "html": str(html_path),
        "json": str(json_path),
        "email_status": email_status,
    }


def write_json_output(summary: dict, items: list[ReportItem], report_date: str) -> Path:
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"daily_report_{report_date}.json"
    payload = {
        "summary": summary,
        "items": [
            {
                "title": item.title,
                "url": item.url,
                "source": item.source,
                "category": item.category,
                "score": item.score,
            }
            for item in items
        ],
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def send_report_email(html_text: str, markdown_text: str, report_date: str) -> str:
    smtp_server = os.getenv("SMTP_SERVER", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    email_to = os.getenv("EMAIL_TO", "haoshi@tju.edu.cn")

    if not all([smtp_server, smtp_user, smtp_password, email_to]):
        logging.error("SMTP settings are incomplete; email not sent.")
        return "failed"

    message = EmailMessage()
    message["Subject"] = f"【冰箱行业AI科技日报】{report_date}"
    message["From"] = smtp_user
    message["To"] = email_to
    message.set_content(markdown_text)
    message.add_alternative(html_text, subtype="html")

    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(message)
        logging.info("Email sent to %s", email_to)
        return "sent"
    except Exception as exc:  # noqa: BLE001
        logging.exception("Email sending failed: %s", exc)
        return "failed"
