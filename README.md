# DailyRadar

每天定时推送的个人 AI 日报服务，融合内容聚合、AI 分析与个人助手功能，支持 Docker 一键部署。

## 功能特性

- **三大信息源**：GitHub 热门仓库 / YouTube 精选视频 / RSS 订阅
- **Claude AI 摘要**：对每条内容自动打分（1-10）、生成摘要、过滤低质量内容
- **偏好学习**：通过反馈打分，AI 逐渐学习你的内容偏好
- **个人助手**：晨间日程提醒 + TODO 到期检查（逾期/今日/即将到期）
- **多渠道推送**：邮件（HTML 富文本）+ 飞书 + 企业微信
- **多时间点调度**：在 `config.yaml` 中定义任意数量的 cron 时间点，不同时段推送不同内容
- **Docker 部署**：基于 supercronic，容器内稳定运行

---

## 目录结构

```
DailyRadar/
├── config/
│   ├── config.yaml              # 主配置（信源、调度、AI、通知）
│   └── personal/
│       ├── schedule.yaml        # 个人每周日程
│       └── todos.yaml           # 个人 TODO 列表
├── data/                        # 运行时自动创建，feedback.db 持久化于此
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── entrypoint.sh
│   ├── .env.example             # 环境变量模板
│   └── .env                     # 环境变量（不提交，从 .env.example 复制）
├── src/                         # Python 源码
└── requirements.txt
```

---

## 快速开始

### 前置要求

- Docker + Docker Compose（推荐部署方式）
- 或 Python 3.11+（本地调试）
- 任意 OpenAI 兼容 API（Claude / Gemini / DeepSeek / 中转服务均可）
- 邮件 SMTP 账号（推荐使用 QQ 邮箱授权码）

---

## 第一步：配置 `docker/.env`

```bash
cp docker/.env.example docker/.env
```

编辑 `docker/.env`，至少填写以下必填项：

```dotenv
# 必填：AI（LiteLLM 格式，兼容任意 OpenAI 兼容接口）
AI_API_KEY=your_api_key_here
# 格式：provider/model_name
AI_MODEL=openai/gemini-3.1-pro-preview
# 中转或本地服务端点（使用官方接口则留空）
AI_API_BASE=https://jeniya.cn/v1

# 必填：邮件推送
EMAIL_FROM=your_email@qq.com
EMAIL_PASSWORD=your_smtp_password   # QQ邮箱：授权码，非登录密码
EMAIL_TO=recipient@example.com      # 多收件人用逗号分隔
EMAIL_SMTP_SERVER=smtp.qq.com
EMAIL_SMTP_PORT=465

# 可选：GitHub（不填则受速率限制 60次/小时）
GITHUB_TOKEN=ghp_xxxxx

# 可选：YouTube（不填则跳过 YouTube 采集）
YOUTUBE_API_KEY=AIzaSy_xxxxx

# 可选：飞书群机器人
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx

# 可选：企业微信群机器人
WEWORK_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxx
```

> **QQ 邮箱授权码获取**：登录 QQ 邮箱 → 设置 → 账户 → 开启 SMTP 服务 → 生成授权码

---

## 第二步：配置 `config/config.yaml`

### 调度时间

```yaml
schedules:
  - name: "早间日报"
    cron: "0 8 * * *"            # 每天 08:00
    content: [schedule, todos, news]   # 包含日程 + TODO + 新闻
    sources: [github, rss]
    subject_prefix: "早安 | DailyRadar"

  - name: "晚间日报"
    cron: "0 21 * * *"           # 每天 21:00
    content: [news]              # 仅新闻
    sources: [github, youtube, rss]
    subject_prefix: "晚间精选 | DailyRadar"
```

`content` 字段可选值：
| 值 | 说明 |
|---|---|
| `news` | 抓取信息源 + Claude AI 摘要 |
| `schedule` | 读取 `personal/schedule.yaml` 中今日日程 |
| `todos` | 读取 `personal/todos.yaml` 中到期/逾期 TODO |

### 信息源

```yaml
collectors:
  github:
    topics: [llm, ai-agent, python]   # 监控的 GitHub Topics
    min_stars: 50                      # 最低 star 数过滤
    max_repos: 12                      # 每次最多抓取数
    days_lookback: 7                   # 只看最近 N 天有更新的仓库

  rss:
    feeds:
      - id: "hacker-news"
        name: "Hacker News"
        url: "https://hnrss.org/frontpage"
      # 按需添加更多 RSS 源...

  youtube:
    enabled: true      # 需要 YOUTUBE_API_KEY
    keywords:
      - "Sam Altman interview"
      - "Andrej Karpathy lecture"
```

### AI 设置

```yaml
ai:
  model: "openai/gemini-3.1-pro-preview"  # LiteLLM 格式，env AI_MODEL 优先
  api_base: "https://jeniya.cn/v1"         # 自定义端点，env AI_API_BASE 优先
  min_relevance_score: 5    # 低于此分数（1-10）的内容被过滤
  max_items_per_digest: 15  # 每次最多展示条目数
```

### 启用飞书 / 企业微信

在 `config.yaml` 中将对应渠道设为 `enabled: true`：

```yaml
notifications:
  email:  { enabled: true }
  feishu: { enabled: true }   # 同时在 .env 配置 FEISHU_WEBHOOK_URL
  wework: { enabled: true }   # 同时在 .env 配置 WEWORK_WEBHOOK_URL
```

---

## 第三步：配置个人助手（可选）

### `config/personal/schedule.yaml` — 每周日程

```yaml
daily:                          # 每天都有的事项
  - time: "07:30"
    title: "晨间锻炼"
    notes: "跑步 30 分钟"

weekly:
  mon:                          # 周一
    - time: "09:00"
      title: "组会"
      location: "理科五号楼 201"
      notes: "带论文进展 PPT"
  tue:
    - time: "10:00"
      title: "导师 1v1"
      location: "教授办公室"
  # ... 其余星期类似
```

### `config/personal/todos.yaml` — TODO 列表

```yaml
settings:
  lookahead_days: 3     # 提前几天提醒即将到期项目

todos:
  - id: "r001"
    title: "提交论文初稿"
    due: "2026-03-10"
    priority: "high"    # high / medium / low
    notes: "包含实验结果和讨论章节"
    done: false

  - id: "a001"
    title: "回复 Alice 邮件"
    due: "2026-03-03"
    priority: "medium"
    done: false
```

日报中会自动分组显示：
- ⚠ **逾期**（红色）：due < 今天
- ★ **今日截止**（黄色）：due == 今天
- ○ **即将到期**（蓝色）：due 在未来 `lookahead_days` 天内

---

## Docker 部署

```bash
cd DailyRadar/docker/

# 构建镜像
docker compose build

# 启动（后台运行，按 config.yaml 的 cron 自动触发）
docker compose up -d

# 查看日志
docker logs -f dailyradar

# 停止
docker compose down
```

### 立即触发一次测试发送

```bash
# 发送"早间日报"
IMMEDIATE_RUN=true SCHEDULE_NAME="早间日报" RUN_MODE=once \
  docker compose up --abort-on-container-exit
```

### 数据持久化

`data/` 目录通过 volume 挂载到容器内，以下数据会持久化：

- `data/feedback.db` — 内容偏好反馈历史，重建容器不丢失

---

## 本地调试（无 Docker）

```bash
cd DailyRadar/
pip install -r requirements.txt

# 预览输出，不发送通知
python -m src.main --schedule-name "早间日报" --dry-run

# 正式发送
python -m src.main --schedule-name "晚间日报"
```

---

## 内容偏好学习

服务内置基于 few-shot 的偏好学习系统：在 `data/last_digest.json` 中为内容填写评分后，下次运行时 AI 会自动参考历史高分内容进行过滤。

**工作流程：**

1. 每次日报运行后，自动生成 `data/last_digest.json`，每条记录预留打分字段：

   ```json
   {
     "date": "2026-03-02",
     "source": "github",
     "title": "anthropics/claude-code",
     "url": "https://github.com/anthropics/claude-code",
     "ai_score": 9,
     "ai_summary": "Claude 的官方 CLI 工具...",
     "user_score": null,
     "user_notes": ""
   }
   ```

2. 打开 `data/last_digest.json`，将感兴趣的条目 `user_score` 改为 1-5 的整数（`null` 表示跳过）：

   ```json
   "user_score": 5,
   "user_notes": "工程质量高，直接相关"
   ```

3. **下次日报运行时自动应用**——服务启动时会读取文件、将已填分数写入 `feedback.db`，并清空 `user_score` 字段避免重复写入。无需任何额外操作。

> `data/` 目录通过 Docker volume 挂载到宿主机，直接在宿主机编辑文件即可，无需进入容器。

---

## 常见问题

**Q：邮件发送失败，提示 535 认证错误**
A：QQ/163 邮箱需使用「授权码」而非登录密码。QQ 邮箱：设置 → 账户 → 开启 SMTP → 生成授权码。

**Q：GitHub 采集很慢或报错**
A：未配置 `GITHUB_TOKEN` 时每小时只有 60 次 API 请求。在 GitHub Settings → Developer Settings → Personal Access Token 生成一个 Token（无需任何权限）填入 `.env`。

**Q：YouTube 未采集到内容**
A：需要在 Google Cloud Console 开启 YouTube Data API v3 并获取 API Key，填入 `YOUTUBE_API_KEY`。

**Q：如何添加新的 RSS 源**
A：编辑 `config/config.yaml` 中的 `collectors.rss.feeds` 列表，添加一行 `{id, name, url}`，重启容器即生效（无需重新构建镜像）。

**Q：如何只运行某一个调度**
A：设置环境变量 `SCHEDULE_NAME` 和 `RUN_MODE=once`，见上方"立即触发一次测试发送"。

---

## 技术栈

| 组件 | 来源 |
|------|------|
| GitHub / YouTube / RSS 采集器 | 改编自 `obsidian-daily-digest` |
| Claude AI 摘要 + 偏好学习 | 改编自 `obsidian-daily-digest` |
| Docker + supercronic 多调度 | 改编自 `TrendRadar` |
| 飞书 / 企业微信推送 | 改编自 `TrendRadar` |
| Email HTML 模板 | 改编自 `obsidian-daily-digest/mailer.py` |
