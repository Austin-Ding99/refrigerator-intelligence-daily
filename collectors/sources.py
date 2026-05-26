from __future__ import annotations

import logging
from datetime import datetime
from email.utils import parsedate_to_datetime

import feedparser
import yaml
from bs4 import BeautifulSoup

from collectors.search import ReportItem, anysearch_search_categories, http_get

BEIJING_TZ_NAME = "Asia/Shanghai"


def load_sources(path: str = "config/sources.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo:
            from zoneinfo import ZoneInfo

            return parsed.astimezone(ZoneInfo(BEIJING_TZ_NAME)).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def collect_rss_items(config: dict) -> list[ReportItem]:
    items: list[ReportItem] = []
    for source in config.get("rss_sources", []):
        try:
            feed = feedparser.parse(source["url"])
        except Exception as exc:  # noqa: BLE001
            logging.warning("RSS source failed: %s: %s", source["name"], exc)
            continue
        for entry in feed.entries[:8]:
            title = getattr(entry, "title", "").strip()
            url = getattr(entry, "link", "").strip()
            if not title or not url:
                continue
            summary = BeautifulSoup(getattr(entry, "summary", ""), "html.parser").get_text(" ", strip=True)
            published_at = parse_datetime(getattr(entry, "published", None) or getattr(entry, "updated", None))
            items.append(
                ReportItem(
                    title=title,
                    url=url,
                    source=source["name"],
                    category=infer_category(title + " " + summary),
                    summary=summary[:500],
                    published_at=published_at,
                    raw_text=summary,
                    metadata={"provider": "rss"},
                )
            )
    return items


def collect_web_items(config: dict) -> list[ReportItem]:
    items: list[ReportItem] = []
    for source in config.get("web_sources", []):
        try:
            soup = BeautifulSoup(http_get(source["url"], timeout=18).text, "html.parser")
        except Exception as exc:  # noqa: BLE001
            logging.warning("Web source failed: %s: %s", source["name"], exc)
            continue

        for link in soup.find_all("a", href=True)[:80]:
            title = link.get_text(" ", strip=True)
            href = link["href"].strip()
            if len(title) < 12:
                continue
            url = href if href.startswith("http") else source["url"].rstrip("/") + "/" + href.lstrip("/")
            items.append(
                ReportItem(
                    title=title[:220],
                    url=url,
                    source=source["name"],
                    category=infer_category(title),
                    summary="",
                    raw_text=title,
                    metadata={"provider": "web"},
                )
            )
            if len(items) >= 30:
                break
    return items


def collect_anysearch_items(config: dict) -> list[ReportItem]:
    anysearch_config = config.get("anysearch", {})
    domains = anysearch_config.get("domains", ["web"])
    max_results = int(anysearch_config.get("max_results", 5))
    return anysearch_search_categories(config.get("category_queries", {}), max_results=max_results, domains=domains)


def collect_all_items(config: dict) -> list[ReportItem]:
    return collect_rss_items(config) + collect_web_items(config) + collect_anysearch_items(config)


def infer_category(text: str) -> str:
    lowered = text.lower()
    if "patent" in lowered or "专利" in lowered:
        return "patent"
    if any(term in lowered for term in ["ai", "artificial intelligence", "digital twin", "agent", "大模型", "智能"]):
        return "ai_application"
    if any(term in lowered for term in ["copper", "aluminum", "steel", "export", "market", "policy", "价格", "出口"]):
        return "market"
    return "technology"
