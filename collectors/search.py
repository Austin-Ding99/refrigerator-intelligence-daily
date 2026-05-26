from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential


REFRIGERATOR_TERMS = {
    "refrigerator",
    "fridge",
    "freezer",
    "冷箱",
    "冰箱",
    "制冷",
    "冷柜",
    "compressor",
    "cooling",
    "defrost",
    "insulation",
    "refrigerant",
    "preservation",
    "thermal",
    "heat pump",
}

AI_TERMS = {
    "ai",
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "llm",
    "agent",
    "digital twin",
    "predictive",
    "智能",
    "人工智能",
    "大模型",
    "故障诊断",
    "预测维护",
}

TECH_TERMS = {
    "thermal",
    "compressor",
    "foam",
    "sensor",
    "energy",
    "defrost",
    "vacuum insulation",
    "refrigerant",
    "material",
    "patent",
    "制冷",
    "热管理",
    "压缩机",
    "节能",
    "冷媒",
}

MARKET_TERMS = {
    "copper",
    "aluminum",
    "steel",
    "export",
    "policy",
    "market",
    "supply chain",
    "chip",
    "消费",
    "出口",
    "政策",
    "铜",
    "铝",
    "钢",
}

LAST_ANYSEARCH_STATUS = "not_run"


@dataclass
class ReportItem:
    title: str
    url: str
    source: str
    category: str
    summary: str = ""
    published_at: datetime | None = None
    company: str = ""
    patent_number: str = ""
    raw_text: str = ""
    refrigerator_related: float = 0.0
    ai_related: float = 0.0
    technology_depth: float = 0.0
    market_impact: float = 0.0
    source_credibility: float = 0.0
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def text_for_scoring(self) -> str:
        return " ".join([self.title, self.summary, self.raw_text]).lower()


def normalize_title(title: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in title).split())


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl().rstrip("/")


def keyword_score(text: str, terms: set[str]) -> float:
    if not text:
        return 0.0
    hits = sum(1 for term in terms if term.lower() in text)
    return min(1.0, hits / 3)


def score_item(item: ReportItem, source_scores: dict[str, float]) -> ReportItem:
    text = item.text_for_scoring()
    item.refrigerator_related = keyword_score(text, REFRIGERATOR_TERMS)
    item.ai_related = keyword_score(text, AI_TERMS)
    item.technology_depth = keyword_score(text, TECH_TERMS)
    item.market_impact = keyword_score(text, MARKET_TERMS)
    item.source_credibility = source_scores.get(item.source, source_scores.get("AnySearch", 0.7))

    if item.category == "ai_application":
        item.ai_related = max(item.ai_related, 0.7)
    if item.category == "market":
        item.market_impact = max(item.market_impact, 0.7)
    if item.category == "patent":
        item.technology_depth = max(item.technology_depth, 0.7)

    item.score = round(
        0.35 * item.refrigerator_related
        + 0.25 * item.ai_related
        + 0.20 * item.technology_depth
        + 0.10 * item.market_impact
        + 0.10 * item.source_credibility,
        4,
    )
    return item


def dedupe_items(items: list[ReportItem]) -> list[ReportItem]:
    seen_urls: set[str] = set()
    kept: list[ReportItem] = []
    kept_titles: list[str] = []

    for item in sorted(items, key=lambda it: it.score, reverse=True):
        title_key = normalize_title(item.title)
        url_key = normalize_url(item.url)
        if url_key and url_key in seen_urls:
            continue
        if any(SequenceMatcher(None, title_key, existing).ratio() > 0.86 for existing in kept_titles):
            continue
        kept.append(item)
        kept_titles.append(title_key)
        if url_key:
            seen_urls.add(url_key)
    return kept


def filter_recent(items: list[ReportItem], now: datetime, hours: int, patent_days: int = 7) -> list[ReportItem]:
    kept: list[ReportItem] = []
    for item in items:
        if item.published_at is None:
            kept.append(item)
            continue
        age_seconds = (now - item.published_at).total_seconds()
        max_seconds = patent_days * 86400 if item.category == "patent" else hours * 3600
        if 0 <= age_seconds <= max_seconds:
            kept.append(item)
    return kept


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=6))
def http_get(url: str, timeout: int = 20) -> requests.Response:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "RefrigeratorIndustryAIAgent/1.0"},
    )
    response.raise_for_status()
    return response


def extract_page_summary(url: str) -> str:
    try:
        soup = BeautifulSoup(http_get(url, timeout=12).text, "html.parser")
        description = soup.find("meta", attrs={"name": "description"})
        if description and description.get("content"):
            return description["content"].strip()
        first_paragraph = soup.find("p")
        return first_paragraph.get_text(" ", strip=True)[:500] if first_paragraph else ""
    except Exception as exc:  # noqa: BLE001
        logging.info("Page summary unavailable for %s: %s", url, exc)
        return ""


def get_anysearch_status() -> str:
    return LAST_ANYSEARCH_STATUS


def anysearch_batch_search(queries: dict[str, str], max_results: int = 5) -> list[ReportItem]:
    global LAST_ANYSEARCH_STATUS

    api_key = os.getenv("ANYSEARCH_API_KEY", "")
    endpoint = os.getenv("ANYSEARCH_ENDPOINT", "https://api.anysearch.com/mcp")
    LAST_ANYSEARCH_STATUS = "called_with_key" if api_key else "called_anonymous"
    if not api_key:
        logging.info("ANYSEARCH_API_KEY is not set; trying AnySearch anonymous access.")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "batch_search",
            "arguments": {
                "queries": [
                    {"query": query, "max_results": max_results, "freshness": "day"}
                    for query in queries.values()
                ]
            },
        },
    }
    categories = list(queries.keys())

    try:
        response = requests.post(
            endpoint,
            json=payload,
            timeout=30,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        logging.warning("AnySearch batch search failed: %s", exc)
        LAST_ANYSEARCH_STATUS = "failed"
        return []

    results = parse_anysearch_results(data)
    items: list[ReportItem] = []
    for index, result_group in enumerate(results):
        category = categories[index] if index < len(categories) else "technology"
        group_items = result_group.get("items", result_group.get("results", [])) if isinstance(result_group, dict) else []
        for entry in group_items[:max_results]:
            title = entry.get("title", "").strip()
            url = entry.get("url", entry.get("link", "")).strip()
            if not title or not url:
                continue
            summary = entry.get("snippet", entry.get("summary", "")).strip()
            if not summary:
                summary = extract_page_summary(url)
            items.append(
                ReportItem(
                    title=title,
                    url=url,
                    source=entry.get("source", "AnySearch") or "AnySearch",
                    category=category,
                    summary=summary,
                    raw_text=summary,
                    metadata={"provider": "anysearch"},
                )
            )
    LAST_ANYSEARCH_STATUS = "called" if items else "called_no_results"
    return items


def parse_anysearch_results(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    result = data.get("result", data)
    if isinstance(result, str):
        return parse_markdown_anysearch_output(result)
    content = result.get("content", []) if isinstance(result, dict) else []
    for block in content:
        if block.get("type") != "text":
            continue
        text = block.get("text", "")
        try:
            parsed = json.loads(text)
            return parsed.get("results", parsed if isinstance(parsed, list) else [])
        except json.JSONDecodeError:
            return parse_markdown_anysearch_output(text)
    return result.get("results", []) if isinstance(result, dict) else []


def parse_markdown_anysearch_output(text: str) -> list[dict[str, list[dict[str, str]]]]:
    groups: list[dict[str, list[dict[str, str]]]] = []
    current: list[str] = []
    saw_query_heading = False

    def append_current_group() -> None:
        parsed_items = parse_markdown_search_results("\n".join(current))
        if parsed_items:
            groups.append({"items": parsed_items})

    for line in text.splitlines():
        if re.match(r"^##\s+Query\s+\d+:", line.strip(), flags=re.IGNORECASE):
            saw_query_heading = True
            if current:
                append_current_group()
                current = []
            continue
        current.append(line)

    if current:
        append_current_group()
    if saw_query_heading:
        return groups
    return [{"items": parse_markdown_search_results(text)}]


def parse_markdown_search_results(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    def flush_current() -> None:
        nonlocal current
        if current and current.get("title") and current.get("url"):
            items.append(current)
        current = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        markdown_link = re.search(r"\[([^\]]+)\]\((https?://[^)]+)\)", line)
        if markdown_link:
            items.append({"title": markdown_link.group(1).strip(), "url": markdown_link.group(2).strip(), "summary": ""})
            continue
        heading = re.match(r"^###\s+\d+\.\s+(.+)$", line)
        if heading:
            flush_current()
            current = {"title": heading.group(1).strip(), "url": "", "summary": ""}
            continue
        if current and line.startswith("- **URL**:"):
            current["url"] = line.split(":", 1)[1].strip()
            continue
        if current and line.startswith("- ") and not current.get("summary"):
            current["summary"] = line[2:].strip()

    flush_current()
    return items
