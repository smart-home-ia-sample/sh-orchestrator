import asyncio
import os
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

os.environ.setdefault("LLM_PROVIDER", "mock")

BFA_PORT = 9731

from app.graph.build import build_graph  # noqa: E402
from smart_home_common import AgentUnavailableError  # noqa: E402


class FakeAgentClient:
    def __init__(self, responses: dict, unavailable_capabilities: frozenset = frozenset()):
        self.responses = responses
        self.unavailable_capabilities = unavailable_capabilities
        self.calls: list[tuple] = []

    async def call(self, capability, intent, input, correlation_id=None):
        self.calls.append((capability, intent, input))
        if capability in self.unavailable_capabilities:
            raise AgentUnavailableError(f"agent for '{capability}' unreachable")
        return self.responses.get(capability, {"status": "ok", "result": {}})


class FakeMcpClient:
    def __init__(self, environment: dict | None = None):
        self._environment = environment or {}
        self._topology = {"devices": [], "rooms": []}

    async def read_resource(self, uri):
        if uri == "home://events":
            return {"events": []}
        if uri == "home://environment":
            return self._environment
        if uri == "home://devices":
            return self._topology.get("devices", [])
        if uri == "home://rooms":
            return self._topology.get("rooms", [])
        return {}


def _build_fake_bfa_app() -> FastAPI:
    app = FastAPI()

    @app.get("/agents")
    def list_agents(capability: str | None = None):
        return [{"name": "fake", "endpoint": "http://fake"}]

    return app


def _run(app_, port):
    uvicorn.run(app_, host="127.0.0.1", port=port, log_level="warning")


@pytest.fixture(scope="module", autouse=True)
def fake_bfa():
    os.environ["BFA_URL"] = f"http://127.0.0.1:{BFA_PORT}"
    threading.Thread(target=_run, args=(_build_fake_bfa_app(), BFA_PORT), daemon=True).start()
    time.sleep(1.0)
    yield


def _run_graph(user_message: str, agent_client, mcp_client=None) -> dict:
    graph = build_graph(agent_client, mcp_client or FakeMcpClient())
    return asyncio.run(
        graph.ainvoke(
            {
                "request_id": "r1",
                "user_message": user_message,
                "correlation_id": "c1",
                "completed_tasks": [],
                "failed_tasks": [],
                "observations": {},
            }
        )
    )


def test_turn_off_light_scenario():
    fake = FakeAgentClient(
        {"turn_off": {"status": "ok", "result": {"ok": True, "state": {"id": "living_room_light", "on": False}}}}
    )

    result = _run_graph("Apague as luzes da sala.", fake)

    assert result["validation_ok"] is True
    assert fake.calls == [("turn_off", "turn_off", {"device_id": "living_room_light"})]
    assert "apagada" in result["final_response"].lower()


def test_bedtime_scenario():
    fake = FakeAgentClient(
        {
            "turn_off": {"status": "ok", "result": {"ok": True, "state": {"on": False}}},
            "set_temperature": {"status": "ok", "result": {"ok": True, "state": {"temperature": 22}}},
            "turn_on": {"status": "ok", "result": {"ok": True, "state": {"on": True}}},
            "check_security": {"status": "ok", "result": {"alarm_armed": False}},
        }
    )

    result = _run_graph("Estou indo dormir.", fake)

    assert result["validation_ok"] is True
    capabilities_called = [c[0] for c in fake.calls]
    assert capabilities_called == [
        "turn_off",
        "turn_off",
        "set_temperature",
        "turn_on",
        "check_security",
    ]


def test_device_control_close_curtain_scenario():
    fake = FakeAgentClient(
        {"close": {"status": "ok", "result": {"ok": True, "state": {"id": "living_room_curtain", "open": False}}}}
    )

    result = _run_graph("Fecha a cortina da sala.", fake)

    assert result["validation_ok"] is True
    assert fake.calls == [("close", "close", {"device_id": "living_room_curtain"})]
    assert "fechado" in result["final_response"].lower()


def test_device_control_appliance_scenario():
    fake = FakeAgentClient(
        {"turn_off": {"status": "ok", "result": {"ok": True, "state": {"id": "kitchen_coffee_maker", "on": False}}}}
    )

    result = _run_graph("Desliga a cafeteira.", fake)

    assert result["validation_ok"] is True
    assert fake.calls == [("turn_off", "turn_off", {"device_id": "kitchen_coffee_maker"})]


def test_device_control_set_temperature_scenario():
    fake = FakeAgentClient(
        {"set_temperature": {"status": "ok", "result": {"ok": True, "state": {"id": "bedroom_ac", "temperature": 22.0}}}}
    )

    result = _run_graph("Ajuste a temperatura para 22 graus no quarto.", fake)

    assert result["validation_ok"] is True
    assert fake.calls == [("set_temperature", "set_temperature", {"device_id": "bedroom_ac", "value": 22.0})]
    assert "22" in result["final_response"]


def test_device_control_cold_complaint_uses_current_ac_temperature():
    mcp = FakeMcpClient(environment={"bedroom": {"ac": {"on": True, "temperature": 24.0}}})
    fake = FakeAgentClient(
        {
            "set_temperature": {
                "status": "ok",
                "result": {"ok": True, "state": {"id": "bedroom_ac", "temperature": 26.0}},
            }
        }
    )

    result = _run_graph("Ainda está frio.", fake, mcp_client=mcp)

    assert result["validation_ok"] is True
    assert fake.calls == [("set_temperature", "set_temperature", {"device_id": "bedroom_ac", "value": 26.0})]


def test_leave_home_scenario():
    fake = FakeAgentClient(
        {
            "secure_home": {"status": "ok", "result": {"alarm_armed": True}},
            "switch_off_nonessential": {"status": "ok", "result": {}},
            "inspect_consumption": {"status": "ok", "result": {"total_watts": 150.0}},
        }
    )

    result = _run_graph("Vou sair de casa.", fake)

    assert result["validation_ok"] is True
    assert result["observations"]["inspect_consumption"]["total_watts"] == 150.0


def test_energy_status_scenario():
    fake = FakeAgentClient({"inspect_consumption": {"status": "ok", "result": {"total_watts": 200.0}}})

    result = _run_graph("Como está o consumo de energia?", fake)

    assert result["validation_ok"] is True
    assert result["observations"]["inspect_consumption"]["total_watts"] == 200.0


def test_energy_status_final_response_includes_real_numbers():
    fake = FakeAgentClient(
        {
            "inspect_consumption": {
                "status": "ok",
                "result": {
                    "total_watts": 1780.0,
                    "top_consumers": [{"device_id": "bedroom_ac", "type": "ac", "watts": 1500.0}],
                    "recommendations": ["Desligue o ar-condicionado se não estiver em uso."],
                },
            }
        }
    )

    result = _run_graph("Quanto gastou de energia?", fake)

    assert "1780" in result["final_response"]
    assert "bedroom ac" in result["final_response"]
    assert "1500" in result["final_response"]


def test_home_status_scenario_includes_events():
    fake = FakeAgentClient(
        {
            "check_security": {"status": "ok", "result": {"alarm_armed": True}},
            "check_environment": {"status": "ok", "result": {}},
            "inspect_consumption": {"status": "ok", "result": {"total_watts": 100.0}},
        }
    )

    result = _run_graph("O que está acontecendo na casa?", fake)

    assert result["validation_ok"] is True
    assert "events" in result["observations"]


def test_chitchat_skips_discovery_and_dispatch():
    fake = FakeAgentClient({})

    result = _run_graph("Olá!", fake)

    assert result["validation_ok"] is True
    assert fake.calls == []
    assert "ajudar" in result["final_response"].lower()


def test_bedtime_resolves_devices_from_live_topology():
    mcp = FakeMcpClient()
    mcp._topology = {  # noqa: SLF001 - test double
        "devices": [
            {"id": "sala_teto", "room": "living_room", "type": "light"},
            {"id": "cozinha_teto", "room": "kitchen", "type": "light"},
            {"id": "quarto_ac", "room": "bedroom", "type": "ac"},
        ],
        "rooms": [],
    }
    fake = FakeAgentClient(
        {
            "turn_off": {"status": "ok", "result": {"ok": True, "state": {"on": False}}},
            "set_temperature": {"status": "ok", "result": {"ok": True, "state": {"temperature": 22}}},
            "turn_on": {"status": "ok", "result": {"ok": True, "state": {"on": True}}},
            "check_security": {"status": "ok", "result": {"alarm_armed": False}},
        }
    )

    result = _run_graph("Estou indo dormir.", fake, mcp_client=mcp)

    assert result["validation_ok"] is True
    device_ids = [c[2].get("device_id") for c in fake.calls if "device_id" in c[2]]
    assert device_ids == ["sala_teto", "cozinha_teto", "quarto_ac", "quarto_ac"]


def test_unrecognized_message_routes_to_recovery_explain():
    fake = FakeAgentClient({})

    result = _run_graph("Qual é a capital da França?", fake)

    assert result["validation_ok"] is False
    assert fake.calls == []
    assert "não entendi" in result["final_response"].lower()


def test_agent_failure_routes_to_recovery_explain():
    fake = FakeAgentClient({}, unavailable_capabilities=frozenset({"inspect_consumption"}))

    result = _run_graph("Como está o consumo de energia?", fake)

    assert result["validation_ok"] is False
    assert "não consegui" in result["final_response"].lower()
