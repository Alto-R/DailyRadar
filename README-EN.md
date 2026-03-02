<div align="center">

# DailyRadar 📡

A self-hosted personal AI digest service — aggregates GitHub / YouTube / RSS, filters with AI, delivered straight to your inbox on a schedule.

[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)](#-docker-deployment)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![supercronic](https://img.shields.io/badge/scheduler-supercronic-orange?style=flat-square)](https://github.com/aptible/supercronic)

[![Email](https://img.shields.io/badge/Email-HTML_Rich_Text-00D4AA?style=flat-square)](#)
[![Feishu](https://img.shields.io/badge/Feishu-Webhook-00D4AA?style=flat-square)](https://www.feishu.cn/)
[![WeCom](https://img.shields.io/badge/WeCom-Webhook-00D4AA?style=flat-square)](https://work.weixin.qq.com/)

**[中文](README.md)** | **[English](README-EN.md)**

</div>

<br>

## 📑 Quick Navigation

<div align="center">

| | | |
|:---:|:---:|:---:|
| [🚀 Quick Start](#-quick-start) | [⚙️ Configuration](#️-configuration) | [🐳 Docker Deployment](#-docker-deployment) |
| [🎯 Features](#-features) | [🧠 Preference Learning](#-preference-learning) | [❓ FAQ](#-faq) |

</div>

<br>

## 🎯 Features

- **Three source types**: GitHub trending repos / YouTube curated videos / RSS feeds — mix and match
- **AI digest**: auto-scores each item (1–10), generates summaries, filters low-quality content
- **Preference learning**: rate items to teach the AI your taste — recommendations improve over time
- **Personal assistant**: morning schedule reminder + TODO due-date checker (overdue / today / upcoming)
- **Multi-channel delivery**: Email (HTML) + Feishu + WeCom, with per-recipient content splitting
- **Multi-schedule**: define any number of cron triggers in `config.yaml`, push different content at different times
- **One-command Docker deploy**: powered by supercronic, stable in-container scheduling

<br>

## 🚀 Quick Start

### Step 1: Configure environment variables

```bash
cd DailyRadar/docker/
cp .env.example .env
```

Edit `docker/.env` with required fields:

```dotenv
# AI (required)
AI_API_KEY=your_api_key_here
AI_MODEL=openai/gpt-4o          # LiteLLM format: provider/model_name
AI_API_BASE=                    # Custom endpoint for proxy services; leave blank for official APIs

# Email (required)
EMAIL_FROM=your_email@example.com
EMAIL_PASSWORD=your_smtp_password   # Use app password / auth code, NOT your login password
EMAIL_TO=recipient@example.com      # Comma-separated for multiple recipients
```

<details>
<summary>Optional: GitHub / YouTube / Feishu / WeCom</summary>

```dotenv
# GitHub Token (without this, API rate limit is 60 req/hour)
GITHUB_TOKEN=ghp_xxxxx

# YouTube Data API v3 (skip YouTube collection if not set)
YOUTUBE_API_KEY=AIzaSy_xxxxx

# Feishu group bot
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx

# WeCom group bot
WEWORK_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxx
```

</details>

> **Gmail / SMTP**: Use an app password generated from your account security settings, not your login password.

---

### Step 2: Configure schedules and sources

Edit `config/config.yaml`:

```yaml
schedules:
  - name: "Morning Digest"
    cron: "0 8 * * *"
    content: [schedule, todos, news]   # schedule + todos + news
    sources: [github, rss]
    subject_prefix: "Good Morning | DailyRadar"

  - name: "Evening Digest"
    cron: "0 21 * * *"
    content: [news]
    sources: [github, youtube, rss]
    subject_prefix: "Evening Picks | DailyRadar"
```

`content` options:

| Value | Description |
|---|---|
| `news` | Collect sources + AI digest |
| `schedule` | Today's schedule from `personal/schedule.yaml` |
| `todos` | Due / overdue TODOs from `personal/todos.yaml` |

---

### Step 3: Set up personal assistant (optional)

<details>
<summary><code>config/personal/schedule.yaml</code> — Weekly schedule</summary>

```yaml
daily:
  - time: "07:30"
    title: "Morning workout"

weekly:
  mon:
    - time: "09:00"
      title: "Team meeting"
      location: "Room 201"
  tue:
    - time: "10:00"
      title: "1-on-1 with advisor"
```

</details>

<details>
<summary><code>config/personal/todos.yaml</code> — TODO list</summary>

```yaml
todos:
  - id: "r001"
    title: "Submit paper draft"
    due: "2026-03-10"
    priority: "high"    # high / medium / low
    done: false
```

TODOs are grouped automatically in the digest:

- ⚠ **Overdue** (due < today)
- ★ **Due today**
- ○ **Upcoming** (due within `lookahead_days` days)

</details>

---

### Step 4: Launch

```bash
cd DailyRadar/docker/
docker compose up -d
```

Check logs:

```bash
docker logs -f dailyradar
```

<br>

## 🐳 Docker Deployment

### Common commands

```bash
# Start (background, triggers automatically by cron)
docker compose up -d

# Rebuild after source code changes
docker compose up -d --build

# Restart only (after editing config.yaml or personal/)
docker compose restart

# Recreate container (after changing .env)
docker compose up -d --force-recreate

# Stop
docker compose down
```

### Trigger immediately for testing

In `docker/.env`:

```dotenv
IMMEDIATE_RUN=true          # Run once immediately on startup
SCHEDULE_NAME=Morning Digest  # Leave blank to use the first schedule
```

Then: `docker compose up -d --force-recreate`

### Data persistence

`data/` is mounted as a Docker volume to the host. `feedback.db` (preference history) survives container rebuilds.

<br>

## 🧠 Preference Learning

After each digest run, `data/last_digest.json` is generated automatically:

```json
{
  "date": "2026-03-02",
  "source": "github",
  "title": "vllm-project/vllm",
  "ai_score": 9,
  "ai_summary": "High-performance LLM inference engine...",
  "user_score": null,
  "user_notes": ""
}
```

Set `user_score` to an integer 1–5 for items you care about. **On the next run, scores are applied automatically** — the AI references your high-rated history when filtering new content.

> `data/` is volume-mounted to the host, so you can edit the file directly without entering the container.

<br>

## ⚙️ Configuration

### AI settings

```yaml
ai:
  model: "openai/gpt-4o"       # LiteLLM format; env AI_MODEL takes priority
  api_base: ""                  # Custom endpoint; env AI_API_BASE takes priority
  min_relevance_score: 5        # Filter items below this score (1–10)
  max_items_per_digest: 20      # Max items shown per digest
  max_tokens: 512               # Max tokens per summary
```

### GitHub collector

```yaml
collectors:
  github:
    topics: [llm, ai-agent, python]
    min_stars: 50
    max_repos: 12
    days_lookback: 7
```

### RSS feeds

```yaml
  rss:
    feeds:
      - id: "hacker-news"
        name: "Hacker News"
        url: "https://hnrss.org/frontpage"
      # Add more feeds here...
```

Changes to `config.yaml` take effect after `docker compose restart` — no rebuild needed.

### Notification channels

```yaml
notifications:
  email:  { enabled: true }
  feishu: { enabled: true }   # Also set FEISHU_WEBHOOK_URL in .env
  wework: { enabled: true }   # Also set WEWORK_WEBHOOK_URL in .env
```

> **Privacy**: `schedule` and `todos` are personal content — only sent to `EMAIL_FROM` (the sender). Other recipients receive only the news section.

<br>

## ❓ FAQ

**Q: Email sending fails with 535 authentication error**

A: Use an app password / SMTP auth code, not your account login password.

**Q: GitHub collection is slow or hitting rate limits**

A: Without `GITHUB_TOKEN`, you're limited to 60 API requests/hour. Generate a token at GitHub Settings → Developer Settings → Personal Access Tokens (no permissions needed).

**Q: YouTube returns 403 Forbidden**

A: Enable YouTube Data API v3 in Google Cloud Console, and make sure the API key has no HTTP referrer restrictions.

**Q: How to add more RSS feeds**

A: Edit `collectors.rss.feeds` in `config/config.yaml`, add `{id, name, url}`, then `docker compose restart`.

**Q: How to run only one schedule manually**

A: Set `IMMEDIATE_RUN=true` and `SCHEDULE_NAME=Morning Digest` in `docker/.env`, then recreate the container.

<br>

## 📚 Credits

| Component | Source |
|------|------|
| GitHub / YouTube / RSS collectors | Adapted from [obsidian-daily-digest](https://github.com/iamseeley/obsidian-daily-digest) |
| AI digest + preference learning | Adapted from [obsidian-daily-digest](https://github.com/iamseeley/obsidian-daily-digest) |
| Docker + supercronic multi-schedule | Adapted from [TrendRadar](https://github.com/sansan0/TrendRadar) |
| Feishu / WeCom push | Adapted from [TrendRadar](https://github.com/sansan0/TrendRadar) |
| Email HTML template | Adapted from [obsidian-daily-digest](https://github.com/iamseeley/obsidian-daily-digest) |
