from __future__ import annotations

import json
import logging
import os
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
LAST_ANYSEARCH_DIAGNOSTICS: dict[str, Any] = {
    "status": "not_run",
    "endpoint": "",
    "http_statuses": [],
    "raw_response_sample": "",
    "parsed_item_count": 0,
}
DEFAULT_ANYSEARCH_ENDPOINT = "https://api.anysearch.com/v1/search"
DEFAULT_ANYSEARCH_DOMAINS = ["tech"]
RAW_RESPONSE_SAMPLE_LIMIT = 1200


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


@dataclass
class SearchProviderResult:
    items: list[ReportItem]
    raw_response_sample: str = ""
    parsed_item_count: int = 0
    http_status: int | None = None
    status: str = "not_run"


@dataclass
class AnySearchProvider:
    endpoint: str
    api_key: str
    domains: list[str]
    timeout: int = 30

    def search(self, query: str, category: str, max_results: int = 5) -> SearchProviderResult:
        payload = {
            "query": query,
            "domains": self.domains,
            "max_results": max_results,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = requests.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
            logging.info("AnySearch response status for %s: %s", category, response.status_code)
            raw_sample = sample_response_text(response)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            logging.warning("AnySearch search failed for %s: %s", category, exc)
            return SearchProviderResult(
                items=[],
                raw_response_sample=locals().get("raw_sample", ""),
                http_status=getattr(locals().get("response", None), "status_code", None),
                status="failed",
            )

        parsed_entries = parse_anysearch_v1_results(data)
        items = [
            build_anysearch_item(entry, category)
            for entry in parsed_entries[:max_results]
        ]
        items = [item for item in items if item is not None]
        return SearchProviderResult(
            items=items,
            raw_response_sample=raw_sample,
            parsed_item_count=len(parsed_entries),
            http_status=response.status_code,
            status="called",
        )


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


def get_anysearch_diagnostics() -> dict[str, Any]:
    return dict(LAST_ANYSEARCH_DIAGNOSTICS)


def anysearch_batch_search(queries: dict[str, str], max_results: int = 5) -> list[ReportItem]:
    return anysearch_search_categories(queries, max_results=max_results)


def anysearch_search_categories(
    queries: dict[str, str],
    max_results: int = 5,
    domains: list[str] | None = None,
) -> list[ReportItem]:
    global LAST_ANYSEARCH_STATUS
    global LAST_ANYSEARCH_DIAGNOSTICS

    api_key = os.getenv("ANYSEARCH_API_KEY", "")
    endpoint = os.getenv("ANYSEARCH_ENDPOINT", DEFAULT_ANYSEARCH_ENDPOINT)
    search_domains = domains or DEFAULT_ANYSEARCH_DOMAINS
    provider = AnySearchProvider(endpoint=endpoint, api_key=api_key, domains=search_domains)
    http_statuses: list[int] = []
    raw_response_sample = ""
    parsed_item_count = 0
    items: list[ReportItem] = []

    LAST_ANYSEARCH_STATUS = "called_with_key" if api_key else "called_anonymous"
    if not api_key:
        logging.info("ANYSEARCH_API_KEY is not set; trying AnySearch without Authorization header.")

    for category, query in queries.items():
        result = provider.search(query=query, category=category, max_results=max_results)
        if result.http_status is not None:
            http_statuses.append(result.http_status)
        if result.raw_response_sample and not raw_response_sample:
            raw_response_sample = result.raw_response_sample
        parsed_item_count += result.parsed_item_count
        items.extend(result.items)

    if items:
        LAST_ANYSEARCH_STATUS = "called"
    elif http_statuses:
        LAST_ANYSEARCH_STATUS = "called_no_results"
    else:
        LAST_ANYSEARCH_STATUS = "failed"

    LAST_ANYSEARCH_DIAGNOSTICS = {
        "status": LAST_ANYSEARCH_STATUS,
        "endpoint": endpoint,
        "domains": search_domains,
        "http_statuses": http_statuses,
        "raw_response_sample": raw_response_sample,
        "parsed_item_count": parsed_item_count,
    }
    logging.info(
        "AnySearch diagnostics: endpoint=%s statuses=%s parsed_item_count=%s item_count=%s raw_response_sample=%s",
        endpoint,
        http_statuses,
        parsed_item_count,
        len(items),
        raw_response_sample,
    )
    return items


def sample_response_text(response: requests.Response) -> str:
    try:
        body = response.json()
        sample = json.dumps(body, ensure_ascii=False)
    except ValueError:
        sample = response.text
    return sample[:RAW_RESPONSE_SAMPLE_LIMIT]


def parse_anysearch_v1_results(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if not isinstance(data, dict):
        return []

    candidates: list[Any] = [
        data.get("results"),
        data.get("items"),
        data.get("data"),
        data.get("organic_results"),
    ]
    nested_data = data.get("data")
    if isinstance(nested_data, dict):
        candidates.extend(
            [
                nested_data.get("results"),
                nested_data.get("items"),
                nested_data.get("organic_results"),
            ]
        )

    for candidate in candidates:
        if isinstance(candidate, list):
            return [entry for entry in candidate if isinstance(entry, dict)]
    return []


def build_anysearch_item(entry: dict[str, Any], category: str) -> ReportItem | None:
    title = str(entry.get("title") or entry.get("name") or "").strip()
    url = str(entry.get("url") or entry.get("link") or entry.get("href") or "").strip()
    if not title or not url:
        return None

    summary = str(
        entry.get("snippet")
        or entry.get("summary")
        or entry.get("description")
        or entry.get("content")
        or ""
    ).strip()
    if not summary:
        summary = extract_page_summary(url)

    source = str(
        entry.get("source")
        or entry.get("source_name")
        or entry.get("domain")
        or "AnySearch"
    ).strip() or "AnySearch"
    return ReportItem(
        title=title,
        url=url,
        source=source,
        category=category,
        summary=summary,
        raw_text=summary,
        metadata={"provider": "anysearch"},
    )
