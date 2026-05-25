from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

import markdown


SECTION_LABELS = {
    "technology": "1. 最新技术",
    "ai_application": "2. AI赋能应用",
    "patent": "3. 最新专利",
    "market": "4. 市场行情",
}


def render_markdown(
    summary: dict[str, list[dict]],
    report_date: str,
    generated_at: datetime,
    diagnostics: dict | None = None,
) -> str:
    lines = [
        "# 冰箱行业 AI 科技日报",
        "",
        f"日期：{report_date}",
        "",
        "---",
        "",
    ]
    if diagnostics:
        lines.extend(render_diagnostics(diagnostics))
        lines.append("")

    for category, title in SECTION_LABELS.items():
        lines.extend([f"## {title}", ""])
        entries = summary.get(category, [])
        if not entries:
            lines.extend(["最近24小时内暂无更新", ""])
            continue
        for entry in entries[:5]:
            lines.extend(render_entry(category, entry))
            lines.append("")

    lines.extend(
        [
            "---",
            "本简报由 Refrigerator Industry AI Agent 自动生成",
            f"生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S')} 北京时间",
            "",
        ]
    )
    return "\n".join(lines)


def render_diagnostics(diagnostics: dict) -> list[str]:
    sources = diagnostics.get("sources", {})
    statuses = diagnostics.get("statuses", {})
    return [
        "## 运行状态",
        "",
        f"- 固定源采集：RSS {sources.get('rss', 0)} 条，官网/网页 {sources.get('web', 0)} 条",
        f"- AnySearch：{format_anysearch_status(statuses.get('anysearch'), sources.get('anysearch', 0))}",
        f"- 过滤结果：原始 {sources.get('raw_total', 0)} 条，保留 {sources.get('kept', 0)} 条",
        f"- AI总结：{format_llm_status(statuses.get('llm'))}",
        "",
        "---",
    ]


def format_anysearch_status(status: str | None, count: int) -> str:
    if status == "not_configured":
        return "未配置 ANYSEARCH_API_KEY，未执行搜索"
    if status == "called":
        return f"已执行 batch_search，返回 {count} 条"
    if status == "called_no_results_or_failed":
        return "已尝试执行 batch_search，但未返回可用结果；详见日志"
    return "状态未知"


def format_llm_status(status: str | None) -> str:
    labels = {
        "called": "已调用模型生成摘要",
        "skipped_no_candidates": "无候选内容，未调用模型",
        "fallback_no_provider_or_key": "未配置模型 provider/API key，使用规则模板",
        "fallback_llm_error": "模型调用失败，使用规则模板",
        "not_run": "未运行",
    }
    return labels.get(status or "not_run", status or "未运行")


def render_entry(category: str, entry: dict) -> list[str]:
    source = format_source(entry)
    if category == "ai_application":
        return [
            f"### {entry.get('title', '未命名更新')}",
            f"- 能力：{entry.get('capability') or entry.get('summary') or '暂无详细说明'}",
            f"- 应用场景：{entry.get('scenario') or entry.get('impact') or '冰箱行业相关场景'}",
            f"- 来源：{source}",
        ]
    if category == "patent":
        return [
            f"### {entry.get('title', entry.get('patent_title', '未命名专利'))}",
            f"- 专利号：{entry.get('patent_number') or '待确认'}",
            f"- 公司：{entry.get('company') or '待确认'}",
            f"- 核心创新：{entry.get('innovation') or entry.get('summary') or '暂无详细说明'}",
            f"- 行业影响：{entry.get('impact') or '可能影响冰箱核心技术路线和专利布局。'}",
            f"- 来源：{source}",
        ]
    if category == "market":
        return [
            f"### {entry.get('title', '未命名行情')}",
            f"- 趋势：{entry.get('trend') or entry.get('summary') or '暂无详细说明'}",
            f"- 行业影响：{entry.get('impact') or '可能影响冰箱供应链或市场判断。'}",
            f"- 来源：{source}",
        ]
    return [
        f"### {entry.get('title', '未命名技术更新')}",
        f"- 摘要：{entry.get('summary') or '暂无详细说明'}",
        f"- 行业影响：{entry.get('impact') or '可能影响冰箱产品性能、能效或制造工艺。'}",
        f"- 来源：{source}",
    ]


def format_source(entry: dict) -> str:
    source = entry.get("source") or "来源"
    url = entry.get("url") or ""
    return f"[{source}]({url})" if url else source


def render_html(markdown_text: str) -> str:
    body = markdown.markdown(markdown_text, extensions=["extra", "sane_lists"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>冰箱行业 AI 科技日报</title>
</head>
<body style="margin:0;padding:0;background:#f5f7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,'Noto Sans SC',sans-serif;color:#1f2937;">
  <main style="max-width:760px;margin:0 auto;padding:24px 16px;">
    <article style="background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;padding:24px;line-height:1.65;">
      <style>
        h1 {{ font-size: 26px; margin: 0 0 12px; color: #111827; }}
        h2 {{ font-size: 20px; margin-top: 28px; padding-top: 16px; border-top: 1px solid #e5e7eb; color: #111827; }}
        h3 {{ font-size: 16px; margin-bottom: 8px; color: #1f2937; }}
        p, li {{ font-size: 15px; }}
        a {{ color: #0f766e; text-decoration: none; }}
        ul {{ padding-left: 20px; }}
        hr {{ border: none; border-top: 1px solid #e5e7eb; margin: 24px 0; }}
        @media (max-width: 520px) {{
          main {{ padding: 12px !important; }}
          article {{ padding: 18px !important; }}
          h1 {{ font-size: 22px; }}
        }}
      </style>
      {body}
    </article>
  </main>
</body>
</html>"""


def write_outputs(markdown_text: str, html_text: str, report_date: str) -> tuple[Path, Path]:
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    md_path = output_dir / f"daily_report_{escape(report_date)}.md"
    html_path = output_dir / f"daily_report_{escape(report_date)}.html"
    md_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")
    return md_path, html_path
