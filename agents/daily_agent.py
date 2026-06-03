from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import time
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from collectors.search import (
    ReportItem,
    dedupe_items,
    filter_recent,
    get_anysearch_diagnostics,
    get_anysearch_status,
    score_item,
)
from collectors.sources import collect_all_items, load_sources
from renderers.report import render_html, render_markdown, write_outputs
from summarizers.llm import get_llm_status, summarize_once


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
SCORE_THRESHOLD = 0.72
DEFAULT_PUSH_ARCHIVE = "daily_push_log.md"


def setup_logging(now: datetime) -> None:
    Path("logs").mkdir(exist_ok=True)
    log_path = Path("logs") / f"daily_report_{now.strftime('%Y-%m-%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )


def run_daily_report(
    dry_run: bool = True,
    send_email: bool = False,
    target_time: str | None = None,
    sleep_until_target: bool = False,
    send_window_minutes: int = 0,
    skip_if_sent: bool = False,
    force_send: bool = False,
) -> dict:
    load_dotenv()
    now = datetime.now(BEIJING_TZ)
    setup_logging(now)
    report_date = now.strftime("%Y-%m-%d")
    logging.info("Starting refrigerator industry AI daily report for %s", report_date)

    archive_path = Path(os.getenv("DAILY_PUSH_LOG_PATH", DEFAULT_PUSH_ARCHIVE))
    if send_email and not dry_run and skip_if_sent and not force_send and has_successful_push(report_date, archive_path):
        logging.info("A successful push already exists for %s; skipping duplicate email.", report_date)
        return {
            "date": report_date,
            "items": 0,
            "markdown": "",
            "html": "",
            "json": "",
            "daily_push_log": str(archive_path),
            "email_status": "skipped_already_sent",
        }

    if send_email and not dry_run and sleep_until_target and target_time:
        wait_until_target_time(target_time)
        now = datetime.now(BEIJING_TZ)
        report_date = now.strftime("%Y-%m-%d")

    if send_email and not dry_run and target_time and send_window_minutes > 0:
        if not is_inside_send_window(now, target_time, send_window_minutes):
            logging.warning(
                "Current time is outside send window %s + %s minutes; email skipped.",
                target_time,
                send_window_minutes,
            )
            return {
                "date": report_date,
                "items": 0,
                "markdown": "",
                "html": "",
                "json": "",
                "daily_push_log": str(archive_path),
                "email_status": "skipped_outside_send_window",
            }

    config = load_sources()
    raw_items = collect_all_items(config)
    logging.info("Collected %s raw items", len(raw_items))
    source_counts = count_items_by_provider(raw_items)

    naive_now = now.replace(tzinfo=None)
    recent_items = filter_recent(raw_items, naive_now, hours=24, require_timestamp=True)
    scored_items = [
        score_item(item, config.get("trusted_sources", {}))
        for item in recent_items
    ]
    filtered_items = [item for item in scored_items if item.score > SCORE_THRESHOLD]
    deduped_items = dedupe_items(filtered_items)
    logging.info("Kept %s items after recent filter, scoring and dedupe", len(deduped_items))
    retained_counts = count_items_by_provider(deduped_items)
    logging.info(
        "AnySearch counts: parsed=%s retained=%s",
        get_anysearch_diagnostics().get("parsed_item_count", 0),
        retained_counts.get("anysearch", 0),
    )

    summary = summarize_once(deduped_items)
    diagnostics = build_diagnostics(raw_items, deduped_items, source_counts, retained_counts)
    markdown_text = render_markdown(summary, report_date, now, diagnostics=diagnostics)
    html_text = render_html(markdown_text)
    md_path, html_path = write_outputs(markdown_text, html_text, report_date)
    json_path = write_json_output(summary, deduped_items, report_date, diagnostics=diagnostics)

    email_status = "skipped"
    if send_email and not dry_run:
        email_status = send_report_email(html_text, markdown_text, report_date)
    else:
        logging.info("Dry run enabled or email not requested; email sending skipped.")

    archive_entry_path = append_daily_push_archive(
        markdown_text=markdown_text,
        report_date=report_date,
        generated_at=now,
        email_status=email_status,
        item_count=len(deduped_items),
        archive_path=archive_path,
        md_path=md_path,
        html_path=html_path,
        json_path=json_path,
        diagnostics=diagnostics,
    )

    return {
        "date": report_date,
        "items": len(deduped_items),
        "markdown": str(md_path),
        "html": str(html_path),
        "json": str(json_path),
        "daily_push_log": str(archive_entry_path),
        "email_status": email_status,
    }


def wait_until_target_time(target_time: str) -> None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", target_time.strip())
    if not match:
        logging.warning("Invalid --target-time value %r; continuing immediately.", target_time)
        return

    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        logging.warning("Invalid --target-time value %r; continuing immediately.", target_time)
        return

    now = datetime.now(BEIJING_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    wait_seconds = int((target - now).total_seconds())
    if wait_seconds <= 0:
        return

    logging.info("Started before target time %s; sleeping %s seconds.", target_time, wait_seconds)
    time.sleep(wait_seconds)


def is_inside_send_window(now: datetime, target_time: str, window_minutes: int) -> bool:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", target_time.strip())
    if not match:
        logging.warning("Invalid --target-time value %r; send window check ignored.", target_time)
        return True

    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        logging.warning("Invalid --target-time value %r; send window check ignored.", target_time)
        return True

    window_start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    window_end = window_start + timedelta(minutes=window_minutes)
    return window_start <= now <= window_end


def has_successful_push(report_date: str, archive_path: Path) -> bool:
    if not archive_path.exists():
        return False
    content = archive_path.read_text(encoding="utf-8")
    pattern = rf"<!--\s*daily-push:{re.escape(report_date)}\s+status=sent\b"
    return re.search(pattern, content) is not None


def append_daily_push_archive(
    markdown_text: str,
    report_date: str,
    generated_at: datetime,
    email_status: str,
    item_count: int,
    archive_path: Path,
    md_path: Path,
    html_path: Path,
    json_path: Path,
    diagnostics: dict,
) -> Path:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        existing = archive_path.read_text(encoding="utf-8").rstrip()
    else:
        existing = "# 每日推送记录\n\n这里记录每天实际生成并推送的日报内容，可在每个日期条目下继续手动补充备注。"

    generated_iso = generated_at.astimezone(BEIJING_TZ).isoformat()
    sources = diagnostics.get("sources", {})
    statuses = diagnostics.get("statuses", {})
    anysearch = diagnostics.get("anysearch", {})
    entry = "\n".join(
        [
            "",
            "",
            f"<!-- daily-push:{report_date} status={email_status} generated_at={generated_iso} -->",
            f"## {report_date} 推送记录",
            "",
            f"- 生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S')} 北京时间",
            f"- 邮件状态：{email_status}",
            f"- 收录条目：{item_count}",
            f"- AnySearch：{statuses.get('anysearch', 'not_run')}，parsed {anysearch.get('parsed_item_count', 0)} 条，retained {anysearch.get('retained_item_count', 0)} 条",
            f"- AnySearch HTTP：{anysearch.get('http_statuses', [])}",
            f"- AI总结：{statuses.get('llm', 'not_run')}",
            f"- 输出文件：`{md_path}` / `{html_path}` / `{json_path}`",
            "",
            "### 可补充备注",
            "",
            "- ",
            "",
            "### 当日推送正文",
            "",
            markdown_text.strip(),
            "",
        ]
    )
    archive_path.write_text(existing + entry, encoding="utf-8")
    return archive_path


def write_json_output(summary: dict, items: list[ReportItem], report_date: str, diagnostics: dict | None = None) -> Path:
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
        "diagnostics": diagnostics or {},
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def count_items_by_provider(items: list[ReportItem]) -> dict[str, int]:
    counts = {"rss": 0, "web": 0, "anysearch": 0}
    for item in items:
        provider = item.metadata.get("provider", "")
        if provider in counts:
            counts[provider] += 1
    return counts


def build_diagnostics(
    raw_items: list[ReportItem],
    kept_items: list[ReportItem],
    source_counts: dict[str, int],
    retained_counts: dict[str, int] | None = None,
) -> dict:
    retained_counts = retained_counts or count_items_by_provider(kept_items)
    anysearch_count = source_counts.get("anysearch", 0)
    anysearch_status = get_anysearch_status()
    if anysearch_status == "called" and not anysearch_count:
        anysearch_status = "called_no_results"
    anysearch_diagnostics = get_anysearch_diagnostics()

    return {
        "sources": {
            "rss": source_counts.get("rss", 0),
            "web": source_counts.get("web", 0),
            "anysearch": anysearch_count,
            "anysearch_retained": retained_counts.get("anysearch", 0),
            "raw_total": len(raw_items),
            "kept": len(kept_items),
        },
        "statuses": {
            "anysearch": anysearch_status,
            "llm": get_llm_status(),
        },
        "anysearch": {
            "endpoint": anysearch_diagnostics.get("endpoint", ""),
            "domains": anysearch_diagnostics.get("domains", []),
            "http_statuses": anysearch_diagnostics.get("http_statuses", []),
            "raw_response_sample": anysearch_diagnostics.get("raw_response_sample", ""),
            "parsed_item_count": anysearch_diagnostics.get("parsed_item_count", 0),
            "retained_item_count": retained_counts.get("anysearch", 0),
        },
    }


def send_report_email(html_text: str, markdown_text: str, report_date: str) -> str:
    smtp_server = os.getenv("SMTP_SERVER", "")
    smtp_port_raw = os.getenv("SMTP_PORT") or "587"
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    email_to = os.getenv("EMAIL_TO") or "haoshi@tju.edu.cn"
    email_from = os.getenv("EMAIL_FROM") or smtp_user
    smtp_use_ssl = os.getenv("SMTP_USE_SSL", "").lower() in {"1", "true", "yes", "on"}
    smtp_use_tls = os.getenv("SMTP_USE_TLS", "true").lower() not in {"0", "false", "no", "off"}

    if not all([smtp_server, smtp_user, smtp_password, email_to]):
        logging.error("SMTP settings are incomplete; email not sent.")
        return "failed"

    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        logging.error("SMTP_PORT must be a number, got %r; email not sent.", smtp_port_raw)
        return "failed"

    message = EmailMessage()
    message["Subject"] = f"【冰箱行业AI科技日报】{report_date}"
    message["From"] = email_from
    message["To"] = email_to
    message.set_content(markdown_text)
    message.add_alternative(html_text, subtype="html")

    try:
        use_ssl = smtp_use_ssl or smtp_port == 465
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_cls(smtp_server, smtp_port, timeout=30) as server:
            if not use_ssl and smtp_use_tls:
                server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(message)
        logging.info("Email sent to %s", email_to)
        return "sent"
    except Exception as exc:  # noqa: BLE001
        logging.exception("Email sending failed: %s", exc)
        return "failed"
