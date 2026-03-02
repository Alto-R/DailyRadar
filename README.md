<div align="center">

# DailyRadar 📡

每天定时推送的个人 AI 日报 —— 聚合 GitHub / YouTube / RSS，AI 摘要筛选，邮件直达收件箱

[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)](#-docker-部署)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![supercronic](https://img.shields.io/badge/scheduler-supercronic-orange?style=flat-square)](https://github.com/aptible/supercronic)

[![邮件推送](https://img.shields.io/badge/Email-HTML富文本-00D4AA?style=flat-square)](#)
[![飞书推送](https://img.shields.io/badge/飞书-Webhook-00D4AA?style=flat-square)](https://www.feishu.cn/)
[![企业微信推送](https://img.shields.io/badge/企业微信-Webhook-00D4AA?style=flat-square)](https://work.weixin.qq.com/)

**[中文](README.md)** | **[English](README-EN.md)**

</div>

<br>

## 📑 快速导航

<div align="center">

| | | |
|:---:|:---:|:---:|
| [🚀 快速开始](#-快速开始) | [⚙️ 配置详解](#️-配置详解) | [🐳 Docker 部署](#-docker-部署) |
| [🎯 核心功能](#-核心功能) | [🧠 偏好学习](#-内容偏好学习) | [❓ 常见问题](#-常见问题) |

</div>

<br>

## 🎯 核心功能

- **三大信息源**：GitHub 热门仓库 / YouTube 精选视频 / RSS 订阅，按需组合
- **AI 摘要**：对每条内容自动打分（1-10）、生成摘要、过滤低质量内容
- **偏好学习**：通过反馈打分，AI 逐渐学习你的内容偏好，推送越来越精准
- **个人助手**：晨间日程提醒 + TODO 到期检查（逾期 / 今日 / 即将到期）
- **多渠道推送**：邮件（HTML 富文本）+ 飞书 + 企业微信，按收件人分流内容
- **多时间点调度**：在 `config.yaml` 中任意定义 cron 时间点，不同时段推送不同内容
- **Docker 一键部署**：基于 supercronic，容器内稳定运行

<br>

## 🚀 快速开始

### 第一步：配置环境变量

```bash
cd DailyRadar/docker/
cp .env.example .env
```

编辑 `docker/.env`，填写必填项：

```dotenv
# AI（必填）
AI_API_KEY=your_api_key_here
AI_MODEL=openai/gpt-4o          # LiteLLM 格式：provider/model_name
AI_API_BASE=                    # 中转服务填写端点，官方接口留空

# 邮件（必填）
EMAIL_FROM=your_email@qq.com
EMAIL_PASSWORD=your_smtp_password   # QQ/163 邮箱使用「授权码」，非登录密码
EMAIL_TO=recipient@example.com      # 多收件人用逗号分隔
```

<details>
<summary>可选配置（GitHub / YouTube / 飞书 / 企业微信）</summary>

```dotenv
# GitHub Token（不填则每小时限 60 次请求）
GITHUB_TOKEN=ghp_xxxxx

# YouTube Data API v3（不填则跳过 YouTube 采集）
YOUTUBE_API_KEY=AIzaSy_xxxxx

# 飞书群机器人
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx

# 企业微信群机器人
WEWORK_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxx
```

</details>

> **QQ 邮箱授权码**：登录 QQ 邮箱 → 设置 → 账户 → 开启 SMTP 服务 → 生成授权码

---

### 第二步：调整调度与信息源

编辑 `config/config.yaml`：

```yaml
schedules:
  - name: "早间日报"
    cron: "0 8 * * *"
    content: [schedule, todos, news]   # 日程 + TODO + 新闻
    sources: [github, rss]
    subject_prefix: "早安 | DailyRadar"

  - name: "晚间日报"
    cron: "0 21 * * *"
    content: [news]
    sources: [github, youtube, rss]
    subject_prefix: "晚间精选 | DailyRadar"
```

`content` 可选值：

| 值 | 说明 |
|---|---|
| `news` | 抓取信息源 + AI 摘要 |
| `schedule` | 读取 `personal/schedule.yaml` 中今日日程 |
| `todos` | 读取 `personal/todos.yaml` 中到期 / 逾期 TODO |

---

### 第三步：配置个人助手（可选）

<details>
<summary><code>config/personal/schedule.yaml</code> — 每周日程</summary>

```yaml
daily:
  - time: "07:30"
    title: "晨间锻炼"

weekly:
  mon:
    - time: "09:00"
      title: "组会"
      location: "理科五号楼 201"
  tue:
    - time: "10:00"
      title: "导师 1v1"
```

</details>

<details>
<summary><code>config/personal/todos.yaml</code> — TODO 列表</summary>

```yaml
todos:
  - id: "r001"
    title: "提交论文初稿"
    due: "2026-03-10"
    priority: "high"    # high / medium / low
    done: false
```

日报中自动分组：
- ⚠ **逾期**（due < 今天）
- ★ **今日截止**（due == 今天）
- ○ **即将到期**（due 在未来 `lookahead_days` 天内）

</details>

---

### 第四步：启动

```bash
cd DailyRadar/docker/
docker compose up -d
```

查看日志：

```bash
docker logs -f dailyradar
```

<br>

## 🐳 Docker 部署

### 常用命令

```bash
# 启动（后台，按 cron 自动触发）
docker compose up -d

# 代码变更后重新构建
docker compose up -d --build

# 仅重启（config.yaml / personal/ 修改后）
docker compose restart

# 环境变量变更后重建容器
docker compose up -d --force-recreate

# 停止
docker compose down
```

### 立即触发测试

在 `docker/.env` 中设置：

```dotenv
IMMEDIATE_RUN=true          # 启动时立即执行一次
SCHEDULE_NAME=早间日报       # 留空则使用第一个 schedule
```

然后 `docker compose up -d --force-recreate`。

### 数据持久化

`data/` 通过 Docker volume 挂载到宿主机，`feedback.db`（偏好反馈历史）重建容器不丢失。

<br>

## 🧠 内容偏好学习

每次日报运行后自动生成 `data/last_digest.json`：

```json
{
  "date": "2026-03-02",
  "source": "github",
  "title": "vllm-project/vllm",
  "ai_score": 9,
  "ai_summary": "高性能 LLM 推理引擎...",
  "user_score": null,
  "user_notes": ""
}
```

将感兴趣的条目 `user_score` 改为 1-5 整数，**下次运行时自动应用**——AI 将参考你的历史高分内容进行过滤，推送越来越符合你的口味。

> `data/` 通过 Docker volume 挂载到宿主机，直接编辑文件即可，无需进入容器。

<br>

## ⚙️ 配置详解

### AI 设置

```yaml
ai:
  model: "openai/gpt-4o"       # LiteLLM 格式，env AI_MODEL 优先
  api_base: ""                  # 自定义端点，env AI_API_BASE 优先
  min_relevance_score: 5        # 低于此分数（1-10）的内容被过滤
  max_items_per_digest: 20      # 每次最多展示条目数
  max_tokens: 512               # 每条摘要最大 token 数
```

### GitHub 采集

```yaml
collectors:
  github:
    topics: [llm, ai-agent, python]
    min_stars: 50
    max_repos: 12
    days_lookback: 7
```

### RSS 订阅源

```yaml
  rss:
    feeds:
      - id: "hacker-news"
        name: "Hacker News"
        url: "https://hnrss.org/frontpage"
      # 添加更多...
```

修改 `config.yaml` 后直接 `docker compose restart` 即可，无需重新构建。

### 通知渠道

```yaml
notifications:
  email:  { enabled: true }
  feishu: { enabled: true }   # 同时在 .env 配置 FEISHU_WEBHOOK_URL
  wework: { enabled: true }   # 同时在 .env 配置 WEWORK_WEBHOOK_URL
```

> **隐私保护**：`schedule` / `todos` 属于个人内容，仅发送给 `EMAIL_FROM`（发件人自己），其他收件人只收到新闻部分。

<br>

## ❓ 常见问题

**Q：邮件发送失败，提示 535 认证错误**

A：QQ/163 邮箱需使用「授权码」而非登录密码。QQ 邮箱：设置 → 账户 → 开启 SMTP → 生成授权码。

**Q：GitHub 采集很慢或报速率限制错误**

A：未配置 `GITHUB_TOKEN` 时每小时只有 60 次 API 请求。在 GitHub Settings → Developer Settings → Personal Access Token 生成 Token（无需勾选任何权限）填入 `.env`。

**Q：YouTube 未采集到内容，报 403**

A：需在 Google Cloud Console 启用 YouTube Data API v3，并检查 API Key 无 HTTP 引用来源限制。

**Q：如何添加新的 RSS 源**

A：编辑 `config/config.yaml` 中的 `collectors.rss.feeds`，添加 `{id, name, url}`，`docker compose restart` 即生效。

**Q：如何只运行某个调度一次**

A：在 `docker/.env` 设置 `IMMEDIATE_RUN=true` 和 `SCHEDULE_NAME=早间日报`，然后重建容器。

<br>

## 📚 致谢

感谢[TrendRadar](https://github.com/sansan0/TrendRadar)和[obsidian-daily-digest](https://github.com/iamseeley/obsidian-daily-digest)的启发
