import asyncio
import json
import os
import threading
import time

import pytest
import uvicorn
from ag_ui.core.events import RunAgentInput
from ag_ui.core.types import UserMessage
from fastapi import FastAPI

os.environ.setdefault("LLM_PROVIDER", "mock")

BFA_PORT = 9741

from app.agui import stream_run  # noqa: E402
from app.graph.build import build_graph  # noqa: E402


class FakeAgentClient:
    def __init__(self, responses: dict):
        self.responses = responses

    async def call(self, capability, intent, input, correlation_id=None):
        return self.responses.get(capability, {"status": "ok", "result": {}})


class FakeMcpClient:
    async def read_resource(self, uri):
        return {"events": []} if uri == "home://events" else {}


def _build_fake_bfa_app() -> FastAPI:
    app = FastAPI()

    @app.post("/resolve/agents")
    def resolve_agents(body: dict):
        return [{"kind": "agent", "service": "fake", "url": "http://fake", "id": body.get("query", ""), "score": 1.0}]

    return app


def _run(app_, port):
    uvicorn.run(app_, host="127.0.0.1", port=port, log_level="warning")


@pytest.fixture(scope="module", autouse=True)
def fake_bfa():
    os.environ["BFA_URL"] = f"http://127.0.0.1:{BFA_PORT}"
    threading.Thread(target=_run, args=(_build_fake_bfa_app(), BFA_PORT), daemon=True).start()
    time.sleep(1.0)
    yield


def _collect_events(user_message: str, agent_client) -> list[dict]:
    graph = build_graph(agent_client, FakeMcpClient())
    run_input = RunAgentInput(
        thread_id="thread-1",
        run_id="run-1",
        messages=[UserMessage(id="m1", content=user_message)],
        state={},
        tools=[],
        context=[],
        forwarded_props={},
    )

    async def _run():
        events = []
        async for chunk in stream_run(graph, run_input):
            line = chunk.strip()
            assert line.startswith("data: ")
            events.append(json.loads(line[len("data: ") :]))
        return events

    return asyncio.run(_run())


def test_leave_home_event_sequence():
    fake = FakeAgentClient(
        {
            "secure_home": {"status": "ok", "result": {"alarm_armed": True}},
            "switch_off_nonessential": {"status": "ok", "result": {}},
            "inspect_consumption": {"status": "ok", "result": {"total_watts": 150.0}},
        }
    )

    events = _collect_events("Vou sair de casa.", fake)

    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert "STEP_STARTED" in types
    assert "STEP_FINISHED" in types
    assert types.count("TOOL_CALL_START") == 3
    assert types.count("TOOL_CALL_RESULT") == 3
    assert "TEXT_MESSAGE_START" in types
    assert "TEXT_MESSAGE_CONTENT" in types
    assert "TEXT_MESSAGE_END" in types

    text_content = next(e for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "protegida" in text_content["delta"].lower()


def test_unrecognized_message_still_finishes_run():
    fake = FakeAgentClient({})

    events = _collect_events("Qual é a capital da França?", fake)

    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert "TOOL_CALL_START" not in types

    text_content = next(e for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "não entendi" in text_content["delta"].lower()
