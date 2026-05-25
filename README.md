# 冰箱行业 AI 科技日报

一个低成本、稳定优先的自动化日报系统：每天北京时间 08:30 采集冰箱行业技术、AI 应用、专利和市场行情，生成 Markdown / HTML 报告并通过 SMTP 邮件发送。

## 设计原则

- RSS 和固定可信源优先，AnySearch 每天最多一次批量补充搜索。
- 先规则过滤和评分，再调用 LLM。
- LLM 每天最多一次总结调用，使用 OpenAI-compatible provider 架构；默认 provider 为 DeepSeek，可切换到 SiliconFlow 等兼容服务。
- LLM 或邮件失败不影响本地报告生成。
- 首版不引入数据库、缓存服务或复杂 Agent 框架。

## 项目结构

```text
agents/daily_agent.py          # 主流程、日志、邮件发送
collectors/sources.py          # RSS/网页/AnySearch 聚合
collectors/search.py           # 数据模型、评分、去重、AnySearch JSON-RPC 适配
summarizers/llm.py             # 单次 LLM 总结和模板降级
renderers/report.py            # Markdown 和 HTML 邮件渲染
config/sources.yaml            # 可信源、RSS、查询词、可信度
config/llm_providers.yaml      # OpenAI-compatible provider 配置
prompts/daily_summary.md       # LLM 总结提示词
outputs/                       # 每日报告输出
logs/                          # 每日运行日志
```

## 环境变量

复制示例文件：

```bash
cp .env.example .env
```

配置：

```text
LLM_PROVIDER=deepseek
LLM_BASE_URL=
LLM_MODEL=
DEEPSEEK_API_KEY=
SILICONFLOW_API_KEY=
ANYSEARCH_API_KEY=
SMTP_SERVER=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
EMAIL_TO=haoshi@tju.edu.cn
```

`ANYSEARCH_API_KEY` 可为空；为空时跳过 AnySearch，只使用 RSS 和固定网页源。

LLM provider 默认配置在 `config/llm_providers.yaml`：

```text
default_provider: deepseek
providers:
  deepseek:
    base_url: https://api.deepseek.com
    model: deepseek-chat
  siliconflow:
    base_url: https://api.siliconflow.cn/v1
```

切换 provider：

```text
LLM_PROVIDER=siliconflow
SILICONFLOW_API_KEY=...
```

临时覆盖兼容接口地址或模型：

```text
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=your-compatible-model
```

## 本地运行

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

只生成报告，不发送邮件：

```bash
python3 main.py --dry-run
```

生成并发送邮件：

```bash
python3 main.py --send-email
```

输出文件：

```text
outputs/daily_report_YYYY-MM-DD.md
outputs/daily_report_YYYY-MM-DD.html
outputs/daily_report_YYYY-MM-DD.json
logs/daily_report_YYYY-MM-DD.log
```

## GitHub Actions

工作流位于：

```text
.github/workflows/daily_report.yml
```

定时：

```text
30 0 * * *
```

即北京时间每天 08:30。

需要在 GitHub 仓库 Secrets 中配置：

```text
LLM_PROVIDER
LLM_BASE_URL
LLM_MODEL
DEEPSEEK_API_KEY
SILICONFLOW_API_KEY
ANYSEARCH_API_KEY
SMTP_SERVER
SMTP_PORT
SMTP_USER
SMTP_PASSWORD
EMAIL_TO
```

## 评分与过滤

系统使用规则评分，避免额外 LLM 消耗：

```text
score =
0.35 * refrigerator_related
+ 0.25 * ai_related
+ 0.20 * technology_depth
+ 0.10 * market_impact
+ 0.10 * source_credibility
```

仅保留 `score > 0.72`，再进行 URL 和相似标题去重。每个栏目最多 5 条。

## 测试

```bash
pytest
```

覆盖内容：

- 最近 24 小时窗口。
- 专利最近 7 天窗口。
- 去重。
- 评分阈值。
- 无内容提示。
- dry-run 输出 Markdown / HTML。
