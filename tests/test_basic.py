from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from agents.daily_agent import append_daily_push_archive, has_successful_push
from collectors.search import ReportItem, dedupe_items, filter_recent, parse_markdown_anysearch_output, score_item
from renderers.report import render_html, render_markdown
from summarizers.llm import load_provider_config, template_summary


def make_item(title: str, category: str = "technology", published_at: datetime | None = None) -> ReportItem:
    return ReportItem(
        title=title,
        url=f"https://example.com/{abs(hash(title))}",
        source="Samsung Newsroom",
        category=category,
        summary="AI refrigerator cooling compressor energy saving thermal sensor market impact",
        published_at=published_at,
    )


def test_time_window_keeps_news_24h_and_patents_7d() -> None:
    now = datetime(2026, 5, 26, 8, 30, 0)
    items = [
        make_item("fresh news", "technology", now - timedelta(hours=23)),
        make_item("old news", "technology", now - timedelta(hours=25)),
        make_item("fresh patent", "patent", now - timedelta(days=6)),
        make_item("old patent", "patent", now - timedelta(days=8)),
    ]

    kept = filter_recent(items, now, hours=24, patent_days=7)

    assert [item.title for item in kept] == ["fresh news", "fresh patent"]


def test_dedupe_url_and_similar_title() -> None:
    first = make_item("AI refrigerator compressor control update")
    second = make_item("AI refrigerator compressor control updates")
    third = make_item("Different refrigerator sensor update")
    first.score = second.score = third.score = 0.9
    second.url = first.url

    kept = dedupe_items([first, second, third])

    assert [item.title for item in kept] == [first.title, third.title]


def test_scoring_threshold_formula() -> None:
    item = make_item("AI refrigerator cooling compressor energy market sensor")

    scored = score_item(item, {"Samsung Newsroom": 0.95})

    assert scored.score > 0.72


def test_empty_renderer_message_and_html() -> None:
    diagnostics = {
        "sources": {"rss": 0, "web": 0, "anysearch": 0, "raw_total": 0, "kept": 0},
        "statuses": {"anysearch": "not_configured", "llm": "skipped_no_candidates"},
    }
    markdown_text = render_markdown({}, "2026-05-26", datetime(2026, 5, 26, 8, 30, 0), diagnostics)
    html_text = render_html(markdown_text)

    assert "最近24小时内暂无更新" in markdown_text
    assert "AnySearch：未配置 ANYSEARCH_API_KEY，未执行搜索" in markdown_text
    assert "AI总结：无候选内容，未调用模型" in markdown_text
    assert "冰箱行业 AI 科技日报" in html_text


def test_template_summary_is_available_without_llm() -> None:
    item = make_item("AI refrigerator cooling compressor energy market sensor")
    grouped = {"technology": [item]}

    summary = template_summary(grouped)

    assert summary["technology"][0]["title"] == item.title


def test_provider_config_uses_env_and_supports_switching(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
default_provider: first
providers:
  first:
    api_key_env: FIRST_API_KEY
    base_url: https://first.example.com
    model: first-model
  second:
    api_key_env: SECOND_API_KEY
    base_url: https://second.example.com
    model: second-model
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("FIRST_API_KEY", "first-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    default_config = load_provider_config(str(config_path))
    assert default_config["api_key"] == "first-key"
    assert default_config["base_url"] == "https://first.example.com"
    assert default_config["model"] == "first-model"

    monkeypatch.setenv("LLM_PROVIDER", "second")
    monkeypatch.setenv("SECOND_API_KEY", "second-key")
    switched_config = load_provider_config(str(config_path))
    assert switched_config["api_key"] == "second-key"
    assert switched_config["base_url"] == "https://second.example.com"
    assert switched_config["model"] == "second-model"


def test_anysearch_markdown_batch_output_is_parsed_by_query_group() -> None:
    text = """
## Query 1: refrigerator AI

## Search Results (1 result)

### 1. AI Energy Mode refrigerator
- **URL**: https://example.com/ai-energy
- Smart compressor and defrost control.

---

## Query 2: refrigerator patent

## Search Results (1 result)

### 1. Refrigerator control method
- **URL**: https://example.com/patent
- Compressor patent summary.
"""

    groups = parse_markdown_anysearch_output(text)

    assert len(groups) == 2
    assert groups[0]["items"][0]["title"] == "AI Energy Mode refrigerator"
    assert groups[0]["items"][0]["url"] == "https://example.com/ai-energy"
    assert groups[1]["items"][0]["summary"] == "Compressor patent summary."


def test_daily_push_archive_records_success_marker(tmp_path: Path) -> None:
    archive_path = tmp_path / "daily_push_log.md"

    append_daily_push_archive(
        markdown_text="# Report\n\nBody",
        report_date="2026-05-26",
        generated_at=datetime(2026, 5, 26, 8, 30, 0),
        email_status="sent",
        item_count=2,
        archive_path=archive_path,
        md_path=tmp_path / "report.md",
        html_path=tmp_path / "report.html",
        json_path=tmp_path / "report.json",
        diagnostics={"sources": {"anysearch": 1}, "statuses": {"anysearch": "called", "llm": "called"}},
    )

    content = archive_path.read_text(encoding="utf-8")
    assert "## 2026-05-26 推送记录" in content
    assert "### 可补充备注" in content
    assert has_successful_push("2026-05-26", archive_path)
