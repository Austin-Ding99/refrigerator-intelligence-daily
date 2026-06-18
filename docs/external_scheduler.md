# 外部定时器配置

GitHub Actions 的 `schedule` 在本仓库中曾连续延迟数小时，因此改用外部 HTTP 定时器在北京时间 08:30 主动触发 `workflow_dispatch`。

## 1. 创建最小权限 GitHub Token

在 GitHub 网页创建 fine-grained personal access token：

- Repository access：仅选择 `refrigerator-intelligence-daily`
- Repository permissions：`Actions: Read and write`
- 建议设置到期提醒，并在到期前轮换

不要把 token 写入仓库、聊天或 Markdown 文件。

## 2. 创建外部定时任务

可使用支持自定义 HTTP method、headers、body 和时区的云定时服务。设置：

- 时区：`Asia/Shanghai`
- 时间：每天 `08:30`
- Method：`POST`
- URL：

```text
https://api.github.com/repos/Austin-Ding99/refrigerator-intelligence-daily/actions/workflows/daily_report.yml/dispatches
```

Headers：

```text
Accept: application/vnd.github+json
Authorization: Bearer YOUR_FINE_GRAINED_TOKEN
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
User-Agent: refrigerator-daily-scheduler
```

Body：

```json
{"ref":"main"}
```

GitHub 接受请求时返回 HTTP `204`。定时器应把 `204` 视为成功。

## 3. 首次验证

1. 在定时服务中执行一次立即测试，确认返回 `204`。
2. 打开 GitHub Actions，确认出现 `workflow_dispatch` 运行。
3. 查看 `Run daily report`：成功时输出 `email_status: sent`。
4. 确认邮箱收到邮件，并检查仓库中的 `daily_push_log.md` 已新增当天记录。

## 手动补发

在 GitHub Actions 点击 `Run workflow`，需要重复发送当天邮件时勾选 `force_send`。
