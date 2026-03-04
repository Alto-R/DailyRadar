# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Copy env template (local dev reads .env at repo root, NOT docker/.env)
cp docker/.env.example .env

# Run a scheduled digest (dry-run: no actual notifications sent)
python -m src.main --schedule-name "早间日报" --dry-run

# Run a scheduled digest (real send)
python -m src.main --schedule-name "早间日报"

# Interactive query mode (no notifications, side-effects disabled)
python -m src.main --query "今天 GitHub Trending 上有什么值得关注的？"

# Docker (production)
cd docker && docker compose up -d --build
docker logs -f signalnest
```

There are no automated tests in this project.

## Architecture

SignalNest is a self-hosted AI daily digest service. The execution flow is:

```
src/main.py
  └─ run_schedule() / run_query()
       └─ src/agent/kernel.py  (run_agent_turn)
            ├─ builds system prompt (injects config/personal/user.md as persona)
            ├─ calls LiteLLM with native tool_calls OR CLI backend as fallback
            └─ executes tools from src/agent/tools.py via ToolRuntime
```

### Agent Layer (`src/agent/`)

- **kernel.py** — Main agent loop. Two code paths: `litellm` backend uses native OpenAI tool calling (multi-turn messages); `claude-cli`/`codex-cli` backends fall back to JSON-in-text parsing.
- **tools.py** — 8 tools registered in `build_agent_tools()`. Tools operate on a shared `ToolRuntime.state` dict that persists across steps within a turn. Tool execution order matters: `collect_*` → `summarize_news` → `build_digest_payload` → `dispatch_notifications`.
- **policy.py** — Allowlist/denylist enforcement. Side-effect tools (i.e. `dispatch_notifications`) are blocked unless `allow_side_effects=True`. `--query` mode always sets this to `False`.
- **session_store.py** — SQLite persistence at `data/agent_sessions.db`. Stores session state, turn history, and tool call logs.

### AI Processing Pipeline (`src/ai/`)

`summarize_items()` in `summarizer.py` orchestrates a 4-stage pipeline:

1. **dedup.py** — History dedup against last 7 days of `data/history/*.json` (title/URL normalization)
2. **filter.py** — Batch title selection via single AI call; enforces `min_items_per_source` guarantees
3. **dedup.py** — Cross-source dedup on candidates
4. **scorer.py** — Parallel AI scoring + summary (ThreadPoolExecutor, `ai.max_workers`)

`digest.py` then generates an overall "today's highlights" paragraph.

### Data Flow for Feedback

After each run, `data/last_digest.json` is written with all items. Users can set `user_score: 1-5` on items. Next run's `_apply_pending_feedback()` reads these scores into `data/feedback.db`, which then surfaces as few-shot taste examples in future scoring prompts.

### Key Configuration Points

- **AI backend**: `AI_BACKEND` env var or `config.yaml ai.backend`. `litellm` requires `AI_API_KEY`. CLI backends (`claude-cli`, `codex-cli`) need the binary on PATH but no key.
- **Schedule content blocks**: Each schedule in `config.yaml` declares `content: [news, schedule, todos]`. The agent reads this intent from its system message and decides which tools to call.
- **Side-effects gate**: `agent.schedule_allow_side_effects` controls whether scheduled runs can actually send. `require_dispatch_tool_call: true` means the run fails if `dispatch_notifications` was never called.
- **Personal files**: `config/personal/user.md` is injected verbatim into the agent's system prompt. `schedule.md` and `projects.md` are AI-parsed by `src/personal/ai_reader.py`. Per-recipient variants follow the pattern `schedule-<name>.md`.

### Notification Dispatch (`src/notifications/`)

`dispatcher.py` reads enabled channels from config and calls the appropriate sender. For email, per-recipient customization works by checking for matching `schedule-<name>.md` / `projects-<name>.md` files. Recipients not matching `EMAIL_OPENING_AI_NAMES` get news-only content.

## Key Design Decisions

- **Agent-only architecture**: There is no non-agent code path. All scheduled runs go through the agent kernel.
- **State lives in ToolRuntime.state**: Tools mutate `rt.state` (a plain dict) as they execute. The session store serializes/deserializes this dict to SQLite between turns.
- **Environment variables override config.yaml**: `AI_BACKEND`, `AI_MODEL`, `AI_API_KEY`, `AI_API_BASE` always take priority over yaml values.
- **Local dev**: `src/config_loader.py` reads `.env` from the repo root. Docker reads `docker/.env`.
