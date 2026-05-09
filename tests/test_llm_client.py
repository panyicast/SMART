from __future__ import annotations

from typing import Any

from smart.services import llm_client
from smart.services.llm_client import LLMRequestConfig


def test_stream_turn_hides_reasoning_by_default(monkeypatch) -> None:
    events = [
        {"choices": [{"delta": {"reasoning_content": "secret reasoning"}}]},
        {"choices": [{"delta": {"content": "final answer"}}]},
    ]

    def fake_stream_json_events(endpoint: str, payload: dict[str, Any], *, config: LLMRequestConfig):
        yield from events

    monkeypatch.setattr(llm_client, "_stream_json_events", fake_stream_json_events)
    progress: list[str] = []

    turn = llm_client._stream_deepseek_turn(
        LLMRequestConfig(api_key="test-key"),
        [{"role": "user", "content": "hello"}],
        tools=None,
        progress_callback=progress.append,
        expose_reasoning=False,
    )

    assert turn.content == "final answer"
    assert turn.reasoning_content == ""
    assert not any("secret reasoning" in item for item in progress)
    assert any("默认不显示思考流" in item for item in progress)


def test_stream_turn_can_expose_reasoning_when_enabled(monkeypatch) -> None:
    events = [
        {"choices": [{"delta": {"reasoning_content": "visible reasoning"}}]},
        {"choices": [{"delta": {"content": "final answer"}}]},
    ]

    def fake_stream_json_events(endpoint: str, payload: dict[str, Any], *, config: LLMRequestConfig):
        yield from events

    monkeypatch.setattr(llm_client, "_stream_json_events", fake_stream_json_events)
    progress: list[str] = []

    turn = llm_client._stream_deepseek_turn(
        LLMRequestConfig(api_key="test-key", expose_reasoning=True),
        [{"role": "user", "content": "hello"}],
        tools=None,
        progress_callback=progress.append,
        expose_reasoning=True,
    )

    assert turn.content == "final answer"
    assert turn.reasoning_content == "visible reasoning"
    assert any("visible reasoning" in item for item in progress)


def test_request_chat_completion_hides_tool_arguments_by_default(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_stream_deepseek_turn(
        config: LLMRequestConfig,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        progress_callback,
        expose_reasoning: bool,
    ) -> llm_client._AssistantTurn:
        calls["count"] += 1
        if calls["count"] == 1:
            return llm_client._AssistantTurn(
                content="",
                reasoning_content="hidden reasoning",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "inspect_project_files",
                            "arguments": '{"secret_path": "C:/mission/private"}',
                        },
                    }
                ],
                usage=None,
            )
        return llm_client._AssistantTurn(
            content="done",
            reasoning_content="hidden reasoning 2",
            tool_calls=[],
            usage=None,
        )

    monkeypatch.setattr(llm_client, "_stream_deepseek_turn", fake_stream_deepseek_turn)
    progress: list[str] = []
    executed: list[dict[str, Any]] = []

    def tool_executor(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        executed.append({"name": name, "arguments": arguments})
        return {"ok": True}

    response = llm_client.request_chat_completion(
        LLMRequestConfig(api_key="test-key"),
        "inspect",
        tools=[{"type": "function", "function": {"name": "inspect_project_files"}}],
        tool_executor=tool_executor,
        progress_callback=progress.append,
    )

    assert response.content == "done"
    assert response.reasoning_content == ""
    assert executed == [
        {"name": "inspect_project_files", "arguments": {"secret_path": "C:/mission/private"}}
    ]
    assert any("<参数已隐藏>" in item for item in progress)
    assert not any("C:/mission/private" in item for item in progress)


def test_request_chat_completion_can_log_tool_arguments_when_enabled(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_stream_deepseek_turn(
        config: LLMRequestConfig,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        progress_callback,
        expose_reasoning: bool,
    ) -> llm_client._AssistantTurn:
        calls["count"] += 1
        if calls["count"] == 1:
            return llm_client._AssistantTurn(
                content="",
                reasoning_content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "inspect_project_files",
                            "arguments": '{"path": "visible-for-debug"}',
                        },
                    }
                ],
                usage=None,
            )
        return llm_client._AssistantTurn(content="done", reasoning_content="", tool_calls=[], usage=None)

    monkeypatch.setattr(llm_client, "_stream_deepseek_turn", fake_stream_deepseek_turn)
    progress: list[str] = []

    llm_client.request_chat_completion(
        LLMRequestConfig(api_key="test-key", log_tool_arguments=True),
        "inspect",
        tools=[{"type": "function", "function": {"name": "inspect_project_files"}}],
        tool_executor=lambda name, arguments: {"ok": True},
        progress_callback=progress.append,
    )

    assert any("visible-for-debug" in item for item in progress)


def test_truncate_for_progress() -> None:
    value = "x" * 10

    assert llm_client._truncate_for_progress(value, limit=20) == value
    assert llm_client._truncate_for_progress(value, limit=5) == "xxxxx... <truncated 5 chars>"
