from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL_V4_FLASH = "deepseek-v4-flash"
DEEPSEEK_MODEL_V4_PRO = "deepseek-v4-pro"
DEEPSEEK_REASONING_EFFORT_HIGH = "high"
DEEPSEEK_REASONING_EFFORT_MAX = "max"
DEFAULT_DEEPSEEK_MODEL = DEEPSEEK_MODEL_V4_PRO
DEFAULT_SYSTEM_PROMPT = "你是严谨的航天任务分析助手，回答应面向工程复核和项目改进。"
SMART_LLM_EXPOSE_REASONING_ENV = "SMART_LLM_EXPOSE_REASONING"
SMART_LLM_LOG_TOOL_ARGS_ENV = "SMART_LLM_LOG_TOOL_ARGS"
SMART_LLM_MAX_PROGRESS_CHARS = 2_000

ProgressCallback = Callable[[str], None]
ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]


class LLMClientError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LLMRequestConfig:
    api_key: str
    model: str = DEFAULT_DEEPSEEK_MODEL
    base_url: str = DEEPSEEK_BASE_URL
    reasoning_effort: str = DEEPSEEK_REASONING_EFFORT_HIGH
    thinking_enabled: bool = True
    timeout_s: float = 300.0
    max_tool_rounds: int = 6
    expose_reasoning: bool = False
    log_tool_arguments: bool = False


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    reasoning_content: str
    tool_call_count: int = 0
    usage: dict[str, Any] | None = None


@dataclass(slots=True)
class _ToolCallAccumulator:
    index: int
    id: str = ""
    type: str = "function"
    name: str = ""
    arguments: str = ""


@dataclass(frozen=True, slots=True)
class _AssistantTurn:
    content: str
    reasoning_content: str
    tool_calls: list[dict[str, Any]]
    usage: dict[str, Any] | None


def request_chat_completion(
    config: LLMRequestConfig,
    prompt: str,
    *,
    system_prompt: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_executor: ToolExecutor | None = None,
    progress_callback: ProgressCallback | None = None,
) -> LLMResponse:
    if not config.api_key.strip():
        raise LLMClientError("DeepSeek API key is empty.")
    if config.model not in {DEEPSEEK_MODEL_V4_FLASH, DEEPSEEK_MODEL_V4_PRO}:
        raise LLMClientError(
            f"SMART only supports DeepSeek V4 models: {DEEPSEEK_MODEL_V4_FLASH}, {DEEPSEEK_MODEL_V4_PRO}."
        )

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": system_prompt or DEFAULT_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]
    expose_reasoning = bool(config.expose_reasoning) or _env_flag(SMART_LLM_EXPOSE_REASONING_ENV)
    log_tool_arguments = bool(config.log_tool_arguments) or _env_flag(SMART_LLM_LOG_TOOL_ARGS_ENV)
    all_reasoning: list[str] = []
    tool_call_count = 0
    usage: dict[str, Any] | None = None

    for round_index in range(max(1, int(config.max_tool_rounds))):
        _emit(progress_callback, f"[DeepSeek] request round {round_index + 1}")
        turn = _stream_deepseek_turn(
            config,
            messages,
            tools=tools,
            progress_callback=progress_callback,
            expose_reasoning=expose_reasoning,
        )
        usage = turn.usage or usage
        if expose_reasoning and turn.reasoning_content:
            all_reasoning.append(turn.reasoning_content)
        if not turn.tool_calls:
            return LLMResponse(
                content=turn.content.strip(),
                reasoning_content="\n".join(all_reasoning).strip() if expose_reasoning else "",
                tool_call_count=tool_call_count,
                usage=usage,
            )
        if tool_executor is None:
            raise LLMClientError("DeepSeek requested tool calls, but no SMART tool executor is configured.")

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": turn.content,
            "tool_calls": turn.tool_calls,
        }
        if expose_reasoning and turn.reasoning_content:
            assistant_message["reasoning_content"] = turn.reasoning_content
        messages.append(assistant_message)

        for tool_call in turn.tool_calls:
            tool_call_count += 1
            function = tool_call.get("function") if isinstance(tool_call, dict) else None
            if not isinstance(function, dict):
                raise LLMClientError(f"Invalid DeepSeek tool call payload: {tool_call}")
            name = str(function.get("name", "")).strip()
            arguments_text = str(function.get("arguments", "") or "{}")
            try:
                arguments = json.loads(arguments_text)
            except json.JSONDecodeError as exc:
                raise LLMClientError(f"DeepSeek emitted invalid tool arguments for {name}: {arguments_text}") from exc
            if not isinstance(arguments, dict):
                raise LLMClientError(f"DeepSeek tool arguments for {name} must be a JSON object.")
            if log_tool_arguments:
                arguments_log = _truncate_for_progress(json.dumps(arguments, ensure_ascii=False))
                _emit(progress_callback, f"[工具调用] {name}({arguments_log})")
            else:
                _emit(progress_callback, f"[工具调用] {name}(<参数已隐藏>)")
            result = tool_executor(name, arguments)
            result_text = json.dumps(result, ensure_ascii=False, indent=2)
            _emit(progress_callback, f"[工具结果] {name} 返回 {len(result_text):,} 字符")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(tool_call.get("id", "")),
                    "content": result_text,
                }
            )

    raise LLMClientError("DeepSeek tool call loop exceeded max_tool_rounds.")


def _stream_deepseek_turn(
    config: LLMRequestConfig,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
    progress_callback: ProgressCallback | None,
    expose_reasoning: bool,
) -> _AssistantTurn:
    endpoint = _join_endpoint(config.base_url, "chat/completions", default_version="v1")
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "reasoning_effort": _normalize_reasoning_effort(config.reasoning_effort),
        "thinking": {"type": "enabled" if config.thinking_enabled else "disabled"},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, _ToolCallAccumulator] = {}
    usage: dict[str, Any] | None = None
    content_started = False
    reasoning_started = False

    for event in _stream_json_events(endpoint, payload, config=config):
        if "usage" in event and isinstance(event["usage"], dict):
            usage = event["usage"]
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            reasoning_parts.append(reasoning)
            if expose_reasoning:
                _emit(progress_callback, f"[DeepSeek 思考流] {reasoning}")
            elif not reasoning_started:
                _emit(progress_callback, "[DeepSeek] 正在进行内部推理（默认不显示思考流）")
                reasoning_started = True
        content = delta.get("content")
        if isinstance(content, str) and content:
            if not content_started:
                _emit(progress_callback, "[DeepSeek] 正在生成报告正文")
                content_started = True
            content_parts.append(content)
        _merge_tool_call_deltas(tool_calls, delta.get("tool_calls"))

    return _AssistantTurn(
        content="".join(content_parts),
        reasoning_content="".join(reasoning_parts) if expose_reasoning else "",
        tool_calls=[_tool_call_to_payload(item) for _, item in sorted(tool_calls.items()) if item.name],
        usage=usage,
    )


def _stream_json_events(endpoint: str, payload: dict[str, Any], *, config: LLMRequestConfig) -> Iterator[dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(1.0, float(config.timeout_s))) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                parsed = _parse_sse_line(line)
                if parsed is None:
                    continue
                yield parsed
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMClientError(f"DeepSeek API HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise LLMClientError(f"DeepSeek API request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMClientError("DeepSeek API request timed out.") from exc


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    data = line.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"DeepSeek API returned invalid SSE JSON: {data[:500]}") from exc
    if not isinstance(parsed, dict):
        raise LLMClientError(f"DeepSeek API returned unexpected SSE payload: {parsed}")
    return parsed


def _merge_tool_call_deltas(
    tool_calls: dict[int, _ToolCallAccumulator],
    delta_tool_calls: object,
) -> None:
    if not isinstance(delta_tool_calls, list):
        return
    for item in delta_tool_calls:
        if not isinstance(item, dict):
            continue
        index = int(item.get("index", len(tool_calls)))
        current = tool_calls.setdefault(index, _ToolCallAccumulator(index=index))
        if item.get("id"):
            current.id += str(item["id"])
        if item.get("type"):
            current.type = str(item["type"])
        function = item.get("function")
        if isinstance(function, dict):
            if function.get("name"):
                current.name += str(function["name"])
            if function.get("arguments"):
                current.arguments += str(function["arguments"])


def _tool_call_to_payload(item: _ToolCallAccumulator) -> dict[str, Any]:
    return {
        "id": item.id or f"call_{item.index}",
        "type": item.type or "function",
        "function": {
            "name": item.name,
            "arguments": item.arguments or "{}",
        },
    }


def _normalize_reasoning_effort(value: str) -> str:
    effort = value.strip().lower()
    if effort in {DEEPSEEK_REASONING_EFFORT_HIGH, DEEPSEEK_REASONING_EFFORT_MAX}:
        return effort
    return DEEPSEEK_REASONING_EFFORT_HIGH


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _truncate_for_progress(value: str, limit: int = SMART_LLM_MAX_PROGRESS_CHARS) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"{value[:limit]}... <truncated {omitted:,} chars>"


def _join_endpoint(base_url: str, suffix: str, *, default_version: str = "v1") -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise LLMClientError("DeepSeek API base_url is empty.")
    suffix = suffix.strip("/")
    if normalized.endswith(f"/{default_version}"):
        return f"{normalized}/{suffix}"
    return f"{normalized}/{default_version}/{suffix}"


def _emit(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
