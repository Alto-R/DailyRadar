"""
Local agent kernel:
  - tool-driven planning loop (native tool calling for litellm, JSON fallback for CLI)
  - schema validation
  - minimal policy enforcement
  - session persistence
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.ai.cli_backend import _call_ai, call_litellm_with_tools
from src.agent.policy import ToolPolicy
from src.agent.schemas import ToolSchemaError, validate_tool_args
from src.agent.session_store import AgentSessionStore
from src.agent.tools import ToolRuntime, ToolSpec, build_agent_tools

logger = logging.getLogger(__name__)


@dataclass
class AgentRunOptions:
    session_id: str | None = None
    max_steps: int | None = None
    dry_run: bool = False
    session_title: str | None = None


def _build_call_kwargs(config: dict) -> tuple[str, dict]:
    ai_cfg = config.get("ai", {})
    backend = os.environ.get("AI_BACKEND") or ai_cfg.get("backend", "litellm")
    model = os.environ.get("AI_MODEL") or ai_cfg.get("model", "openai/gpt-4o-mini")
    api_key = os.environ.get("AI_API_KEY", "")
    api_base = os.environ.get("AI_API_BASE") or ai_cfg.get("api_base") or None
    max_tokens = int(ai_cfg.get("max_tokens", 1200))

    if backend == "litellm" and not api_key:
        raise RuntimeError("AI_API_KEY is required when backend=litellm")

    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "max_tokens": max_tokens,
    }
    if api_base:
        kwargs["api_base"] = api_base
    return backend, kwargs


# ── Tool spec builder ────────────────────────────────────────────────────────

def _build_openai_tool_specs(tools: dict[str, ToolSpec]) -> list[dict[str, Any]]:
    """Convert ToolSpec dict to OpenAI function calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_schema,
            },
        }
        for spec in tools.values()
    ]


# ── Legacy helpers (for CLI backend fallback) ────────────────────────────────

def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []

    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_obj(obj: Any) -> None:
        if isinstance(obj, dict):
            key = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                found.append(obj)
            return
        if isinstance(obj, list):
            for item in obj:
                _add_obj(item)

    try:
        obj = json.loads(text)
        _add_obj(obj)
        if found:
            return found
    except json.JSONDecodeError:
        pass

    for fence_match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE):
        try:
            obj = json.loads(fence_match.group(1).strip())
            _add_obj(obj)
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    idx = 0
    n = len(text)
    while idx < n:
        match = re.search(r"[{\[]", text[idx:])
        if not match:
            break
        start = idx + match.start()
        try:
            obj, end = decoder.raw_decode(text, start)
            _add_obj(obj)
            idx = end
        except json.JSONDecodeError:
            idx = start + 1

    return found


def _extract_action_objects(text: str) -> list[dict[str, Any]]:
    return [obj for obj in _extract_json_objects(text) if "action" in obj]


def _format_tool_catalog(tools: dict[str, ToolSpec]) -> str:
    lines = []
    for tool in tools.values():
        schema_str = json.dumps(tool.input_schema, ensure_ascii=False)
        lines.append(
            f"- {tool.name}: {tool.description}\n"
            f"  side_effect={str(tool.side_effect).lower()}\n"
            f"  input_schema={schema_str}"
        )
    return "\n".join(lines)


# ── Message builders ─────────────────────────────────────────────────────────

def _truncate_text(text: str, max_chars: int = 1400) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _state_overview(state: dict[str, Any]) -> dict[str, Any]:
    raw_items = state.get("raw_items", [])
    news_items = state.get("news_items", [])
    schedule_entries = state.get("schedule_entries", [])
    projects = state.get("projects", [])
    digest_summary = state.get("digest_summary", "")
    payload = state.get("payload")
    return {
        "raw_items_count": len(raw_items) if isinstance(raw_items, list) else 0,
        "news_items_count": len(news_items) if isinstance(news_items, list) else 0,
        "schedule_entries_count": len(schedule_entries) if isinstance(schedule_entries, list) else 0,
        "projects_count": len(projects) if isinstance(projects, list) else 0,
        "has_digest_summary": bool(digest_summary),
        "has_payload": bool(payload),
        "top_news_titles": [str(i.get("title", "")) for i in news_items[:5]]
        if isinstance(news_items, list)
        else [],
    }


def _format_recent_turns(turns: list[dict]) -> str:
    if not turns:
        return "(none)"
    chunks: list[str] = []
    for t in turns:
        user_msg = _truncate_text(str(t.get("user_message", "")), 280)
        assistant_msg = _truncate_text(str(t.get("assistant_reply", "")), 320)
        chunks.append(
            f"Turn #{t.get('turn_index')} | status={t.get('status')}\n"
            f"User: {user_msg}\n"
            f"Assistant: {assistant_msg}"
        )
    return "\n\n".join(chunks)


def _build_system_prompt(
    *,
    tools: dict[str, ToolSpec],
    policy: ToolPolicy,
    backend: str,
    user_profile: str = "",
    now_str: str = "",
) -> str:
    policy_note = (
        "工具策略：副作用工具（如发送通知）已允许。"
        if policy.allow_side_effects
        else "工具策略：副作用工具（如发送通知）已禁用，当前为查询/预览模式。"
    )

    if backend == "litellm":
        # Native tool calling — no JSON format instructions needed, no tool catalog
        persona = f"现在是 {now_str}。\n\n" if now_str else ""
        profile_section = f"## 关于用户\n{user_profile}\n\n" if user_profile else ""
        return (
            f"你是用户的个人 AI 助手，名叫 SignalNest。{persona}"
            f"{profile_section}"
            f"{policy_note}\n\n"
            "根据用户的需求，自主决定调用哪些工具、以什么顺序调用。"
            "完成任务后给出简洁清晰的最终回复。"
        )
    else:
        # CLI backend fallback — needs JSON format instructions and tool catalog
        allow_note = (
            f"Allowlist: {sorted(policy.allow_tools)}" if policy.allow_tools else "Allowlist: (not set)"
        )
        deny_note = f"Denylist: {sorted(policy.deny_tools)}" if policy.deny_tools else "Denylist: (none)"
        return f"""You are SignalNest local agent.
You can call tools to solve the user's request step by step.

{policy_note}
{allow_note}
{deny_note}

Output strictly one JSON object with one of these forms:
1) Tool call:
{{
  "action": "tool",
  "tool": "<tool_name>",
  "arguments": {{ ... }}
}}

2) Final answer:
{{
  "action": "final",
  "response": "<final response to user>"
}}

Rules:
- Return JSON only, no markdown.
- Return exactly one JSON object per response (do not output multiple objects).
- Never invent tool names.
- Respect tool schemas exactly.
- If enough information is already available, return action=final.

Available tools:
{_format_tool_catalog(tools)}
"""


def _build_initial_user_message(
    user_message: str,
    state: dict[str, Any],
    recent_turns: list[dict],
) -> str:
    overview = _state_overview(state)
    recent = _format_recent_turns(recent_turns)
    return (
        f"{user_message}\n\n"
        f"当前会话状态：\n{json.dumps(overview, ensure_ascii=False, indent=2)}\n\n"
        f"历史对话摘要：\n{recent}"
    )


def _normalize_final_text(text: str) -> str:
    return text.strip() or "Done."


def _synthesize_fallback_response(
    *,
    user_message: str,
    step_history: list[dict[str, Any]],
    backend: str,
    call_kwargs: dict,
    max_tokens: int,
) -> str:
    synth_messages = [
        {
            "role": "system",
            "content": "你是简洁的助手。根据已完成的工具步骤，总结结果回答用户。",
        },
        {
            "role": "user",
            "content": (
                f"用户请求：\n{user_message}\n\n"
                f"已执行步骤：\n{json.dumps(step_history, ensure_ascii=False, indent=2)}\n\n"
                "请给出最终回复。"
            ),
        },
    ]
    try:
        text = _call_ai(synth_messages, backend, {**call_kwargs, "max_tokens": max_tokens})
        return _normalize_final_text(text)
    except Exception:
        if not step_history:
            return "No action executed."
        last = step_history[-1]
        if "error" in last:
            return f"Stopped after tool error: {last['error']}"
        return f"Executed {len(step_history)} steps. Last result: {json.dumps(last, ensure_ascii=False)}"


def _execute_tool(
    tool_name: str,
    args: dict[str, Any],
    tools: dict[str, ToolSpec],
    policy: ToolPolicy,
    rt: ToolRuntime,
) -> tuple[dict[str, Any], bool, str | None]:
    """Execute a single tool. Returns (result_dict, success, error_msg)."""
    tool = tools.get(tool_name)
    if not tool:
        err = f"unknown tool '{tool_name}'"
        return {"error": err}, False, err

    allowed, reason = policy.check(tool)
    if not allowed:
        err = reason or "blocked by policy"
        return {"error": err}, False, err

    try:
        validated_args = validate_tool_args(tool_name, tool.input_schema, args)
        result = tool.handler(validated_args, rt)
        return result, True, None
    except ToolSchemaError as e:
        return {"error": str(e)}, False, str(e)
    except Exception as e:
        return {"error": str(e)}, False, str(e)


def _load_user_profile(config: dict) -> str:
    """Read config/personal/user.md and return its raw content for system prompt injection."""
    try:
        personal_dir = Path(config.get("_personal_dir", ""))
        profile_path = personal_dir / "user.md"
        if profile_path.exists():
            return profile_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def run_agent_turn(
    user_message: str,
    config: dict,
    options: AgentRunOptions | None = None,
) -> dict[str, Any]:
    """Execute one local agent turn."""
    options = options or AgentRunOptions()
    message = user_message.strip()
    if not message:
        raise ValueError("agent message is empty")

    agent_cfg = config["agent"]
    tools = build_agent_tools()
    policy = ToolPolicy.from_config(config)

    data_dir = Path(config.get("storage", {}).get("data_dir", "data"))
    session_db = data_dir / "agent_sessions.db"
    store = AgentSessionStore(session_db)

    session_id = options.session_id or str(uuid4())
    if options.session_title:
        session_title = options.session_title
    else:
        try:
            session_title = str(agent_cfg["session_title_template"]).format(schedule_name="")
        except Exception:
            session_title = str(agent_cfg["session_title_template"])
    store.ensure_session(session_id, title=session_title)
    state = store.load_state(session_id)
    recent_turns_limit = max(1, int(agent_cfg["recent_turns_context_limit"]))
    recent_turns = store.load_recent_turns(session_id, limit=recent_turns_limit)

    default_max_steps = int(agent_cfg["max_steps"])
    hard_limit = max(1, int(agent_cfg["max_steps_hard_limit"]))
    fallback_max_tokens = int(agent_cfg["fallback_response_max_tokens"])
    requested_max_steps = options.max_steps if options.max_steps is not None else default_max_steps
    max_steps = max(1, min(int(requested_max_steps), hard_limit))

    backend, call_kwargs = _build_call_kwargs(config)
    model_name = str(call_kwargs.get("model", ""))
    turn_ref = store.start_turn(session_id, message, backend=backend, model=model_name)

    tz_name = config.get("app", {}).get("timezone", "Asia/Shanghai")
    rt = ToolRuntime(
        config=config,
        state=state,
        dry_run=options.dry_run,
        now=datetime.now(ZoneInfo(tz_name)),
    )

    user_profile = _load_user_profile(config)
    now_str = rt.now.strftime("%Y年%m月%d日 %H:%M")
    system_prompt = _build_system_prompt(
        tools=tools,
        policy=policy,
        backend=backend,
        user_profile=user_profile,
        now_str=now_str,
    )

    step_history: list[dict[str, Any]] = []
    final_response: str | None = None
    status = "ok"

    try:
        if backend == "litellm":
            # ── Native tool calling path ─────────────────────────────────────
            openai_tools = _build_openai_tool_specs(tools)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _build_initial_user_message(message, state, recent_turns)},
            ]

            step_no = 1
            while step_no <= max_steps:
                tool_calls, final_text = call_litellm_with_tools(messages, call_kwargs, openai_tools)

                if final_text is not None:
                    final_response = _normalize_final_text(final_text)
                    break

                if not tool_calls:
                    break

                # Add assistant message with all tool_calls to history
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tc["call_id"],
                                "type": "function",
                                "function": {
                                    "name": tc["tool"],
                                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )

                # Execute each tool call and add tool result messages
                for tc in tool_calls:
                    if step_no > max_steps:
                        # Add placeholder error for remaining tool calls to keep message history valid
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["call_id"],
                                "content": json.dumps({"error": "max_steps reached"}),
                            }
                        )
                        continue

                    tool_name = tc["tool"]
                    args = tc["arguments"]
                    result, success, error = _execute_tool(tool_name, args, tools, policy, rt)

                    step_item: dict[str, Any] = {"step": step_no, "tool": tool_name, "arguments": args}
                    if success:
                        step_item["result"] = result
                    else:
                        step_item["error"] = error
                    step_history.append(step_item)

                    store.add_tool_call(
                        turn_ref.turn_id,
                        step_no=step_no,
                        tool_name=tool_name,
                        args=args,
                        result=result if success else None,
                        success=success,
                        error=error,
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["call_id"],
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )
                    step_no += 1

        else:
            # ── CLI backend fallback (legacy JSON-in-text) ────────────────────
            pending_actions: list[dict[str, Any]] = []
            step_no = 1
            while step_no <= max_steps:
                if not pending_actions:
                    legacy_messages = [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": (
                                f"{message}\n\n"
                                f"Session state: {json.dumps(_state_overview(state), ensure_ascii=False)}\n\n"
                                f"Step history: {json.dumps(step_history, ensure_ascii=False)}"
                            ),
                        },
                    ]
                    raw = _call_ai(legacy_messages, backend, call_kwargs)
                    pending_actions = _extract_action_objects(raw)
                    if not pending_actions:
                        final_response = _normalize_final_text(raw)
                        break

                action_obj = pending_actions.pop(0)
                action = str(action_obj.get("action", "")).strip().lower()
                if action == "final":
                    final_response = _normalize_final_text(str(action_obj.get("response", "")))
                    break

                if action != "tool":
                    step_history.append(
                        {"step": step_no, "error": f"invalid action {action!r}", "raw_action_object": action_obj}
                    )
                    step_no += 1
                    continue

                tool_name = str(action_obj.get("tool", "")).strip()
                args = action_obj.get("arguments", {})
                if not isinstance(args, dict):
                    args = {}

                result, success, error = _execute_tool(tool_name, args, tools, policy, rt)

                step_item = {"step": step_no, "tool": tool_name, "arguments": args}
                if success:
                    step_item["result"] = result
                else:
                    step_item["error"] = error
                step_history.append(step_item)

                store.add_tool_call(
                    turn_ref.turn_id,
                    step_no=step_no,
                    tool_name=tool_name,
                    args=args,
                    result=result if success else None,
                    success=success,
                    error=error,
                )
                step_no += 1

        if final_response is None:
            final_response = _synthesize_fallback_response(
                user_message=message,
                step_history=step_history,
                backend=backend,
                call_kwargs=call_kwargs,
                max_tokens=fallback_max_tokens,
            )

    except Exception as e:
        status = "error"
        final_response = f"Agent run failed: {e}"
        logger.exception("agent run failed")
    finally:
        store.save_state(session_id, state)
        store.finish_turn(turn_ref.turn_id, final_response or "", status)

    return {
        "session_id": session_id,
        "turn_index": turn_ref.turn_index,
        "status": status,
        "response": final_response,
        "steps": step_history,
        "state_overview": _state_overview(state),
        "policy": {
            "allow_tools": sorted(policy.allow_tools) if policy.allow_tools is not None else None,
            "deny_tools": sorted(policy.deny_tools),
            "allow_side_effects": policy.allow_side_effects,
        },
        "backend": backend,
        "model": model_name,
    }
