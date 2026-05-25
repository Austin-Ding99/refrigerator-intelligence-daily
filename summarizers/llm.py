from __future__ import annotations

import json
import logging
import os
from collections import defaultdict

import yaml
from openai import OpenAI

from collectors.search import ReportItem


CATEGORY_TITLES = {
    "technology": "最新技术",
    "ai_application": "AI赋能应用",
    "patent": "最新专利",
    "market": "市场行情",
}

LAST_LLM_STATUS = "not_run"


def group_items(items: list[ReportItem]) -> dict[str, list[ReportItem]]:
    grouped: dict[str, list[ReportItem]] = defaultdict(list)
    for item in sorted(items, key=lambda it: it.score, reverse=True):
        if len(grouped[item.category]) < 5:
            grouped[item.category].append(item)
    return grouped


def summarize_once(
    items: list[ReportItem],
    prompt_path: str = "prompts/daily_summary.md",
    provider_config_path: str = "config/llm_providers.yaml",
) -> dict[str, list[dict]]:
    global LAST_LLM_STATUS

    grouped = group_items(items)
    if not items:
        LAST_LLM_STATUS = "skipped_no_candidates"
        return template_summary(grouped)

    prompt = read_prompt(prompt_path)
    payload = [
        {
            "category": item.category,
            "title": item.title,
            "summary": item.summary,
            "source": item.source,
            "url": item.url,
            "company": item.company,
            "patent_number": item.patent_number,
            "score": item.score,
        }
        for item in items[:20]
    ]

    provider_config = load_provider_config(provider_config_path)
    if provider_config:
        try:
            content = call_llm(provider_config, prompt, payload)
            parsed = json.loads(content)
            LAST_LLM_STATUS = "called"
            return normalize_summary(parsed, grouped)
        except Exception as exc:  # noqa: BLE001
            LAST_LLM_STATUS = "fallback_llm_error"
            logging.warning("LLM summary failed, using template summary: %s", exc)
    else:
        LAST_LLM_STATUS = "fallback_no_provider_or_key"

    return template_summary(grouped)


def get_llm_status() -> str:
    return LAST_LLM_STATUS


def read_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def load_provider_config(path: str) -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    providers = config.get("providers", {})
    selected_name = os.getenv("LLM_PROVIDER") or config.get("default_provider", "")
    selected = providers.get(selected_name, {})
    if not selected:
        logging.warning("LLM provider %r is not configured; using template summary.", selected_name)
        return {}

    api_key_env = selected.get("api_key_env", "")
    api_key = os.getenv(api_key_env, "")
    if not api_key:
        logging.info("API key env %s is not set; using template summary.", api_key_env)
        return {}

    return {
        "api_key": api_key,
        "base_url": os.getenv("LLM_BASE_URL") or selected.get("base_url", ""),
        "model": os.getenv("LLM_MODEL") or selected.get("model", ""),
    }


def call_llm(provider_config: dict[str, str], system_prompt: str, payload: list[dict]) -> str:
    client = OpenAI(api_key=provider_config["api_key"], base_url=provider_config["base_url"])

    response = client.chat.completions.create(
        model=provider_config["model"],
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "请输出 JSON，格式为："
                    '{"technology":[],"ai_application":[],"patent":[],"market":[]}。'
                    "每条使用字段 title, summary, impact, source, url；"
                    "AI栏目可用 capability, scenario；专利栏目可用 patent_number, company, innovation。\n"
                    + json.dumps(payload, ensure_ascii=False)
                ),
            },
        ],
    )
    return response.choices[0].message.content or "{}"


def normalize_summary(parsed: dict, fallback_grouped: dict[str, list[ReportItem]]) -> dict[str, list[dict]]:
    normalized: dict[str, list[dict]] = {}
    for category in CATEGORY_TITLES:
        entries = parsed.get(category, [])
        if not isinstance(entries, list):
            entries = []
        normalized[category] = entries[:5] or template_summary(fallback_grouped).get(category, [])
    return normalized


def template_summary(grouped: dict[str, list[ReportItem]]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for category in CATEGORY_TITLES:
        result[category] = []
        for item in grouped.get(category, [])[:5]:
            result[category].append(
                {
                    "title": item.title,
                    "summary": item.summary or "规则采集到相关更新，暂无更详细摘要。",
                    "impact": infer_impact(category),
                    "source": item.source,
                    "url": item.url,
                    "capability": "AI相关能力" if category == "ai_application" else "",
                    "scenario": "冰箱研发、制造或运行场景" if category == "ai_application" else "",
                    "patent_number": item.patent_number,
                    "company": item.company,
                    "innovation": item.summary or item.title,
                }
            )
    return result


def infer_impact(category: str) -> str:
    if category == "market":
        return "可能影响冰箱材料成本、供应链或消费需求判断。"
    if category == "patent":
        return "可能影响冰箱核心技术路线和专利布局。"
    if category == "ai_application":
        return "可作为冰箱智能控制、制造或服务能力升级参考。"
    return "可能影响冰箱产品性能、能效或制造工艺。"
