"""
cli_backend.py - 本地 AI CLI 工具封装 + 统一调用入口
======================================================
支持三种后端（通过 config.ai.backend 或环境变量 AI_BACKEND 配置）：
  - litellm:    LiteLLM 库（默认），需要 AI_API_KEY
  - claude-cli: `claude --print "<prompt>"`  (Claude Code CLI)
  - codex-cli:  `codex -q "<prompt>"`         (OpenAI Codex CLI)

_call_ai() 是统一入口，供 summarizer.py 和 ai_reader.py 共用。
"""

import logging
import subprocess

logger = logging.getLogger(__name__)
_CLI_TIMEOUT = 120  # 秒


class CLIBackendError(RuntimeError):
    """CLI 调用失败时抛出（非零退出码、超时、找不到可执行文件）"""
    pass


def _combine_messages(messages: list[dict]) -> str:
    """将 OpenAI role/content 消息列表拼成单个字符串，供 CLI 工具使用。"""
    parts = []
    for msg in messages:
        role = msg.get("role", "user").upper()
        content = msg.get("content", "")
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def call_claude_cli(messages: list[dict]) -> str:
    """调用 `claude --print` 并返回 stdout 文本。"""
    prompt = _combine_messages(messages)
    try:
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True, text=True, timeout=_CLI_TIMEOUT,
        )
    except FileNotFoundError:
        raise CLIBackendError(
            "找不到 claude 命令，请先安装 Claude Code CLI: https://claude.ai/code"
        )
    except subprocess.TimeoutExpired:
        raise CLIBackendError(f"claude CLI 超时（{_CLI_TIMEOUT}s）")
    if result.returncode != 0:
        raise CLIBackendError(
            f"claude CLI 退出码 {result.returncode}: {(result.stderr or '')[:200]}"
        )
    if result.stderr:
        logger.debug(f"claude-cli stderr: {result.stderr[:200]}")
    return result.stdout.strip()


def call_codex_cli(messages: list[dict]) -> str:
    """调用 `codex -q` 并返回 stdout 文本。"""
    prompt = _combine_messages(messages)
    try:
        result = subprocess.run(
            ["codex", "-q", prompt],
            capture_output=True, text=True, timeout=_CLI_TIMEOUT,
        )
    except FileNotFoundError:
        raise CLIBackendError(
            "找不到 codex 命令，请先安装 OpenAI Codex CLI: npm install -g @openai/codex"
        )
    except subprocess.TimeoutExpired:
        raise CLIBackendError(f"codex CLI 超时（{_CLI_TIMEOUT}s）")
    if result.returncode != 0:
        raise CLIBackendError(
            f"codex CLI 退出码 {result.returncode}: {(result.stderr or '')[:200]}"
        )
    if result.stderr:
        logger.debug(f"codex-cli stderr: {result.stderr[:200]}")
    return result.stdout.strip()


def _call_ai(messages: list[dict], backend: str, call_kwargs: dict) -> str:
    """
    统一 AI 调用入口：LiteLLM 或本地 CLI，返回裸文本（已 strip）。
    供 summarizer.py 和 ai_reader.py 共用。

    Args:
        messages:    OpenAI 格式消息列表，如 [{"role": "system", "content": "..."}, ...]
        backend:     "litellm" | "claude-cli" | "codex-cli"
        call_kwargs: LiteLLM 参数（model, api_key, max_tokens, api_base）。
                     使用 CLI 后端时忽略。

    Returns:
        AI 返回的原始文本（已 strip）。

    Raises:
        CLIBackendError: CLI 调用失败。
        ValueError:      backend 值不合法。
        Exception:       LiteLLM 错误直接透传。
    """
    if backend == "litellm":
        import litellm
        response = litellm.completion(messages=messages, **call_kwargs)
        return response.choices[0].message.content.strip()
    elif backend == "claude-cli":
        return call_claude_cli(messages)
    elif backend == "codex-cli":
        return call_codex_cli(messages)
    else:
        raise ValueError(
            f"未知 AI 后端: {backend!r}，可选值: litellm | claude-cli | codex-cli"
        )


def call_litellm_with_tools(
    messages: list[dict],
    call_kwargs: dict,
    openai_tools: list[dict],
) -> tuple[list[dict] | None, str | None]:
    """
    使用原生 Tool Calling 调用 LiteLLM。仅支持 litellm 后端。

    Returns:
        (tool_calls, final_text) — 二者恰好一个非 None。
        tool_calls 列表中每个元素为:
            {"tool": str, "arguments": dict, "call_id": str}
        final_text: 无 tool_calls 时的纯文本最终回复。
    """
    import litellm

    kw = {**call_kwargs, "tools": openai_tools, "tool_choice": "auto"}
    response = litellm.completion(messages=messages, **kw)
    msg = response.choices[0].message

    if msg.tool_calls:
        calls = []
        for tc in msg.tool_calls:
            try:
                import json
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            calls.append({"tool": tc.function.name, "arguments": args, "call_id": tc.id})
        return calls, None

    return None, (msg.content or "").strip() or "Done."
