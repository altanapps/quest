"""Cross-user runs must not change the target's connected-service state."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from chat.gemini_api import tool_dispatch
from chat.llm.tool_schemas import mutating_tool_call_tools
from tests.prompt_support import named_under_skill, tool_offered


def _dispatch(tool_name, args, **kwargs):
    return asyncio.run(tool_dispatch._dispatch_tool_call(
        app=kwargs.pop("app", None), provider=None,
        user={"id": 22, "email": "target@example.com"},
        conversation_id="target-run", timezone="UTC",
        tool_name=tool_name, args=args, **kwargs,
    ))


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("name", sorted(mutating_tool_call_tools()))
def test_cross_user_mutation_is_blocked_before_handler(monkeypatch, name, nested):
    handler = AsyncMock(return_value='{"changed": true}')
    monkeypatch.setitem(tool_dispatch.TOOL_CALL_HANDLERS, name, handler)

    result, extra = _dispatch(
        "tool_call", {"tool_name": name, "arguments": {}},
        is_user_subagent=True, is_sub_agent=nested,
    )

    assert "not available in cross-user subagent runs" in json.loads(result)["error"]
    assert extra == []
    handler.assert_not_awaited()


def test_plugin_mutation_is_blocked(monkeypatch, slack_plugin):
    handler = AsyncMock(return_value='{"changed": true}')
    with monkeypatch.context() as patch:
        patch.setitem(tool_dispatch.TOOL_CALL_HANDLERS, "send_slack_dm_to_self", handler)
        result, _ = _dispatch(
            "tool_call", {"tool_name": "send_slack_dm_to_self", "arguments": {}},
            is_user_subagent=True,
        )
    assert "read-only" in json.loads(result)["error"]
    handler.assert_not_awaited()


@pytest.mark.parametrize("restricted", [False, True])
def test_normal_mutations_and_cross_user_reads_still_work(monkeypatch, restricted):
    archive = AsyncMock(return_value='{"archived": true}')
    monkeypatch.setattr(tool_dispatch, "_handle_archive_gmail_message", archive)
    result, _ = _dispatch(
        "tool_call", {"tool_name": "archive_gmail_message", "arguments": {"message_id": "m1"}},
        is_user_subagent=restricted,
    )
    assert archive.await_count == (0 if restricted else 1)
    if not restricted:
        assert json.loads(result)["archived"] is True
    result, _ = _dispatch(
        "tool_call", {"tool_name": "get_current_time", "arguments": {}},
        is_user_subagent=restricted,
    )
    assert "error" not in json.loads(result)


@pytest.mark.parametrize("path", ["/api/gmail-simple/drafts", "/api/gmail-simple/send-self"])
@pytest.mark.parametrize("restricted", [False, True])
def test_proxy_mutation_is_blocked_before_route(path, restricted):
    app = FastAPI()
    calls = []

    async def mutate():
        calls.append(True)
        return {"changed": True}

    app.post(path)(mutate)
    result, _ = _dispatch(
        "curl_proxy_post", {"url": "http://localhost:8000" + path, "body": "{}"},
        app=app, is_user_subagent=restricted,
    )
    if restricted:
        assert "read-only" in json.loads(result)["error"]
        assert calls == []
    else:
        assert json.loads(result)["changed"] is True
        assert calls == [True]


@pytest.mark.parametrize("name", ["run_script", "run_python"])
@pytest.mark.parametrize("restricted", [False, True])
def test_sandbox_runner_receives_mutation_policy(monkeypatch, name, restricted):
    runner = AsyncMock(return_value='{"ok": true}')
    monkeypatch.setattr(tool_dispatch, "_handle_" + name, runner)
    _dispatch(name, {"path": "test.py", "script": "pass"}, is_user_subagent=restricted)
    assert runner.await_args.kwargs["block_mutating_tools"] is restricted


def test_cross_user_prompt_hides_connector_mutations(slack_plugin):
    from chat.gemini_api.system_prompt import get_user_subagent_system_prompt

    prompt = get_user_subagent_system_prompt(
        "", connected_services={"google_services": True, "slack": True},
    )
    for name in mutating_tool_call_tools():
        assert not tool_offered(prompt, name), name
    assert named_under_skill(prompt, "system:gmail", "get_gmail_messages")
    assert named_under_skill(prompt, "system:slack", "search_slack_messages")
