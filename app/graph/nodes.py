import asyncio
import os
from typing import Any, Protocol

import httpx

from app.graph.interpret import (
    chitchat_message,
    fallback_message,
    interpret_command,
    resolve_device_id,
)
from app.graph.state import OrchestratorState
from smart_home_common import AgentUnavailableError

# Device-control capabilities are now generic verbs — the same names the Home MCP
# tools and the agent skills use.
CAPABILITIES_BY_INTENT: dict[str, list[str]] = {
    "turn_off_light": ["turn_off"],
    "bedtime": ["turn_off", "set_temperature", "turn_on", "check_security"],
    "leave_home": ["secure_home", "switch_off_nonessential", "inspect_consumption"],
    "energy_status": ["inspect_consumption"],
    "home_status": ["check_security", "check_environment", "inspect_consumption"],
    "unknown": [],
}

# Every verb mutates one device, so interpret() must resolve a device_id first
# (arm/disarm included — they act on the alarm device, id "alarm").
DEVICE_ID_REQUIRED_CAPABILITIES = {
    "turn_on",
    "turn_off",
    "set_brightness",
    "set_temperature",
    "open",
    "close",
    "lock",
    "unlock",
    "arm",
    "disarm",
}


def _state_on(result: dict, expected: bool) -> bool:
    return result.get("ok") is True and result.get("state", {}).get("on") is expected


def _state_field(result: dict, field: str, expected: object) -> bool:
    return result.get("ok") is True and result.get("state", {}).get(field) is expected


# Keyed by verb: what the resulting device state must show for the command to
# count as applied. `input_["value"]` is the numeric arg for set_* verbs.
EXPECTED_EFFECTS = {
    "turn_on": lambda input_, result: _state_on(result, True),
    "turn_off": lambda input_, result: _state_on(result, False),
    "set_temperature": lambda input_, result: (
        result.get("ok") is True and result.get("state", {}).get("temperature") == input_.get("value")
    ),
    "set_brightness": lambda input_, result: (
        result.get("ok") is True and result.get("state", {}).get("brightness") == input_.get("value")
    ),
    "open": lambda input_, result: _state_field(result, "open", True),
    "close": lambda input_, result: _state_field(result, "open", False),
    "lock": lambda input_, result: _state_field(result, "locked", True),
    "unlock": lambda input_, result: _state_field(result, "locked", False),
    "arm": lambda input_, result: _state_field(result, "armed", True),
    "disarm": lambda input_, result: _state_field(result, "armed", False),
}


class McpClientLike(Protocol):
    async def read_resource(self, uri: str) -> Any: ...


class AgentClientLike(Protocol):
    async def call(self, capability: str, intent: str, input: dict, correlation_id: str | None = None) -> dict: ...


async def _fetch_topology(mcp_client: McpClientLike) -> dict:
    """Live home topology (`home://devices` + `home://rooms`) so interpret/plan
    work off what's actually registered instead of hardcoded slugs. Best effort:
    an unreachable MCP yields an empty topology and every caller falls back to
    the legacy seeded slugs."""
    try:
        devices, rooms = await asyncio.wait_for(
            asyncio.gather(
                mcp_client.read_resource("home://devices"),
                mcp_client.read_resource("home://rooms"),
            ),
            timeout=3.0,
        )
    except Exception:
        return {"devices": [], "rooms": []}
    return {
        "devices": devices if isinstance(devices, list) else [],
        "rooms": rooms if isinstance(rooms, list) else [],
    }


async def _current_ac_context(mcp_client: McpClientLike) -> dict | None:
    """Current bedroom AC reading, so the interpreter can reason about relative
    requests ("ainda está frio", "aumenta um pouco") instead of guessing a fixed
    value every time. Returns None if unavailable — interpretation still proceeds,
    just without that extra context."""
    try:
        # This runs before every command is even classified, so it must never hang
        # waiting on a down/unreachable MCP — bound it well below the caller's own
        # request timeout instead of trusting the transport's own (often much longer
        # or absent) default.
        environment = await asyncio.wait_for(mcp_client.read_resource("home://environment"), timeout=3.0)
        ac = environment.get("bedroom", {}).get("ac")
    except Exception:
        return None
    if not ac:
        return None
    return {"on": ac.get("on"), "temperature": ac.get("temperature")}


def make_interpret(mcp_client: McpClientLike):
    async def interpret(state: OrchestratorState) -> dict:
        context, topology = await asyncio.gather(
            _current_ac_context(mcp_client),
            _fetch_topology(mcp_client),
        )
        history = state.get("history", [])
        try:
            command = await interpret_command(
                state["user_message"], context=context, history=history, topology=topology
            )
        except Exception:
            return {"intent": "unknown", "device_id": None, "capability": None, "topology": topology}

        # Runtime check against the live topology / announced capabilities:
        #  - a device_id the interpreter made up (not registered) can't be acted on
        #  - a verb the target device doesn't advertise can't be acted on either
        # Either way, treat the command as uninterpretable rather than dispatch it.
        devices_by_id = {d.get("id"): d for d in topology.get("devices", [])}
        if command.device_id and devices_by_id and command.device_id not in devices_by_id:
            return {"intent": "unknown", "device_id": None, "capability": None, "topology": topology}
        if command.intent == "device_control" and command.device_id in devices_by_id:
            allowed = devices_by_id[command.device_id].get("actions") or []
            if allowed and command.capability not in allowed:
                return {"intent": "unknown", "device_id": None, "capability": None, "topology": topology}

        return {
            "intent": command.intent,
            "device_id": command.device_id,
            "capability": command.capability,
            "temperature": command.temperature,
            "brightness": command.brightness,
            "topology": topology,
        }

    return interpret


def route_after_interpret(state: OrchestratorState) -> str:
    """A bare greeting short-circuits straight to a reply — no discover/plan/
    dispatch/collect/validate, which would otherwise treat 'olá' as a failure."""
    return "chitchat" if state["intent"] == "chitchat" else "continue"


async def chitchat(state: OrchestratorState) -> dict:
    message = await chitchat_message(state["user_message"])
    return {"final_response": message, "validation_ok": True}


async def _has_provider(client: httpx.AsyncClient, bfa_url: str, capability: str) -> bool:
    """Is there an agent in the BFA catalog that can do `capability`? Ask the
    ranked `POST /resolve/agents` with threshold 0 so a weak BM25 score on a
    real verb still counts. A transport error means the BFA is unreachable —
    report no provider and let the graph degrade via recovery_explain."""
    try:
        resolved = await client.post(
            f"{bfa_url}/resolve/agents",
            json={"query": capability.replace("_", " "), "top_k": 3, "threshold": 0.0},
        )
    except httpx.HTTPError:
        return False
    return resolved.status_code == 200 and bool(resolved.json())


async def discover(state: OrchestratorState) -> dict:
    intent = state["intent"]

    if intent == "unknown":
        return {
            "capabilities": [],
            "failed_tasks": [
                {"capability": "", "intent": "unknown", "input": {}, "error": "could not interpret the command"}
            ],
        }

    if intent == "device_control":
        capability = state.get("capability")
        if not capability:
            return {
                "capabilities": [],
                "failed_tasks": [
                    {
                        "capability": "",
                        "intent": "device_control",
                        "input": {},
                        "error": "no capability resolved for device_control",
                    }
                ],
            }
        capabilities = [capability]
    else:
        capabilities = CAPABILITIES_BY_INTENT.get(intent, [])

    bfa_url = os.environ["BFA_URL"]
    missing: list[str] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for capability in capabilities:
            if not await _has_provider(client, bfa_url, capability):
                missing.append(capability)

    if missing:
        return {
            "capabilities": capabilities,
            "failed_tasks": [
                {"capability": c, "intent": intent, "input": {}, "error": f"no healthy agent for capability '{c}'"}
                for c in missing
            ],
        }

    return {"capabilities": capabilities}


async def plan(state: OrchestratorState) -> dict:
    if state.get("failed_tasks"):
        return {"pending_tasks": []}

    intent = state["intent"]

    topology = state.get("topology") or {}

    if intent == "turn_off_light":
        device_id = state.get("device_id") or resolve_device_id(
            topology, "living_room", "light", "living_room_light"
        )
        tasks = [{"capability": "turn_off", "intent": "turn_off", "input": {"device_id": device_id}}]
    elif intent == "device_control":
        capability = state["capability"]
        target_device_id = state.get("device_id")

        if capability in DEVICE_ID_REQUIRED_CAPABILITIES and not target_device_id:
            failed = list(state.get("failed_tasks", []))
            failed.append(
                {
                    "capability": capability,
                    "intent": "device_control",
                    "input": {},
                    "error": "no device_id resolved for device_control",
                }
            )
            return {"pending_tasks": [], "failed_tasks": failed}

        # set_temperature / set_brightness carry a numeric arg in `value`.
        numeric = state.get("temperature") if capability == "set_temperature" else state.get("brightness")
        if capability in ("set_temperature", "set_brightness") and numeric is None:
            failed = list(state.get("failed_tasks", []))
            failed.append(
                {
                    "capability": capability,
                    "intent": "device_control",
                    "input": {},
                    "error": f"no value resolved for {capability}",
                }
            )
            return {"pending_tasks": [], "failed_tasks": failed}

        input_: dict[str, Any] = {}
        if target_device_id:
            input_["device_id"] = target_device_id
        if capability in ("set_temperature", "set_brightness"):
            input_["value"] = numeric

        tasks = [{"capability": capability, "intent": capability, "input": input_}]
    elif intent == "bedtime":
        living_light = resolve_device_id(topology, "living_room", "light", "living_room_light")
        kitchen_light = resolve_device_id(topology, "kitchen", "light", "kitchen_light")
        bedroom_ac = resolve_device_id(topology, "bedroom", "ac", "bedroom_ac")
        tasks = [
            {"capability": "turn_off", "intent": "turn_off", "input": {"device_id": living_light}},
            {"capability": "turn_off", "intent": "turn_off", "input": {"device_id": kitchen_light}},
            {
                "capability": "set_temperature",
                "intent": "set_temperature",
                "input": {"device_id": bedroom_ac, "value": 22},
            },
            {"capability": "turn_on", "intent": "turn_on", "input": {"device_id": bedroom_ac}},
            {"capability": "check_security", "intent": "check_security", "input": {}},
        ]
    elif intent == "leave_home":
        tasks = [
            {"capability": "secure_home", "intent": "secure_home", "input": {}},
            {"capability": "switch_off_nonessential", "intent": "switch_off_nonessential", "input": {}},
            {"capability": "inspect_consumption", "intent": "inspect_consumption", "input": {}},
        ]
    elif intent == "energy_status":
        tasks = [{"capability": "inspect_consumption", "intent": "inspect_consumption", "input": {}}]
    elif intent == "home_status":
        tasks = [
            {"capability": "check_security", "intent": "check_security", "input": {}},
            {"capability": "check_environment", "intent": "check_environment", "input": {}},
            {"capability": "inspect_consumption", "intent": "inspect_consumption", "input": {}},
        ]
    else:
        tasks = []

    return {"pending_tasks": tasks}


def make_dispatch(agent_client: AgentClientLike):
    async def dispatch(state: OrchestratorState) -> dict:
        completed = list(state.get("completed_tasks", []))
        failed = list(state.get("failed_tasks", []))

        for task in state.get("pending_tasks", []):
            try:
                response = await agent_client.call(
                    task["capability"], task["intent"], task["input"], correlation_id=state.get("correlation_id")
                )
            except AgentUnavailableError as exc:
                failed.append({**task, "error": str(exc)})
                continue

            if response["status"] == "ok":
                completed.append({**task, "result": response["result"]})
            else:
                failed.append({**task, "error": str(response["result"])})

        return {"completed_tasks": completed, "failed_tasks": failed}

    return dispatch


def make_collect(mcp_client: McpClientLike):
    async def collect(state: OrchestratorState) -> dict:
        observations = dict(state.get("observations", {}))
        for task in state.get("completed_tasks", []):
            observations[task["intent"]] = task["result"]

        if state["intent"] == "home_status":
            try:
                observations["events"] = await mcp_client.read_resource("home://events")
            except Exception as exc:
                observations["events_error"] = str(exc)

        return {"observations": observations}

    return collect


def validate(state: OrchestratorState) -> dict:
    if state.get("failed_tasks"):
        return {"validation_ok": False}

    for task in state.get("completed_tasks", []):
        expected_effect = EXPECTED_EFFECTS.get(task["intent"])
        if expected_effect and not expected_effect(task["input"], task["result"]):
            return {"validation_ok": False}

    return {"validation_ok": True}


def route_after_validate(state: OrchestratorState) -> str:
    return "ok" if state.get("validation_ok") else "failed"


async def recovery_explain(state: OrchestratorState) -> dict:
    if state["intent"] == "unknown":
        message = await fallback_message(state["user_message"])
    else:
        details = "; ".join(f"{t['intent']}: {t['error']}" for t in state.get("failed_tasks", []))
        message = f"Não consegui concluir a solicitação completamente. Detalhes: {details or 'validação falhou'}."
    return {"final_response": message}


# Keyed by verb — generic, since the same verb now drives lights, TVs, appliances…
DEVICE_ACTION_MESSAGES = {
    "turn_on": "Ligado",
    "turn_off": "Desligado",
    "set_brightness": "Brilho ajustado",
    "set_temperature": "Temperatura ajustada",
    "open": "Aberto",
    "close": "Fechado",
    "lock": "Trancado",
    "unlock": "Destrancado",
    "arm": "Alarme armado",
    "disarm": "Alarme desarmado",
}


def _device_control_message(state: OrchestratorState) -> str:
    capability = state.get("capability") or ""
    base = DEVICE_ACTION_MESSAGES.get(capability, "Ação realizada")
    device_id = state.get("device_id")

    if capability == "set_temperature" and state.get("temperature") is not None:
        base = f"{base} para {state['temperature']}°C"
    elif capability == "set_brightness" and state.get("brightness") is not None:
        base = f"{base} para {state['brightness']}%"

    return f"{base} ({device_id})." if device_id else f"{base}."


def _energy_status_message(state: OrchestratorState) -> str:
    consumption = state.get("observations", {}).get("inspect_consumption", {})
    total = consumption.get("total_watts")
    if total is None:
        return "Consumo de energia consultado com sucesso."

    message = f"Consumo atual: {total:.0f}W."
    top_consumers = consumption.get("top_consumers") or []
    if top_consumers:
        top = top_consumers[0]
        name = top["device_id"].replace("_", " ")
        message += f" Maior consumidor: {name} ({top['watts']:.0f}W)."
    recommendations = consumption.get("recommendations") or []
    if recommendations:
        message += f" {recommendations[0]}"
    return message


def _home_status_message(state: OrchestratorState) -> str:
    observations = state.get("observations", {})
    parts: list[str] = []

    security = observations.get("check_security") or {}
    if "alarm_armed" in security:
        alarm = "armado" if security["alarm_armed"] else "desarmado"
        presence = "presença detectada" if security.get("presence_detected") else "sem presença"
        parts.append(f"alarme {alarm}, {presence}")

    consumption = observations.get("inspect_consumption") or {}
    if consumption.get("total_watts") is not None:
        parts.append(f"consumo atual de {consumption['total_watts']:.0f}W")

    events = observations.get("events") or []
    if events:
        parts.append(f"{len(events)} eventos recentes registrados")

    if not parts:
        return "Status da casa consultado com sucesso."

    summary = "; ".join(parts)
    return summary[0].upper() + summary[1:] + "."


def final(state: OrchestratorState) -> dict:
    intent = state["intent"]
    if intent == "device_control":
        return {"final_response": _device_control_message(state)}
    if intent == "energy_status":
        return {"final_response": _energy_status_message(state)}
    if intent == "home_status":
        return {"final_response": _home_status_message(state)}

    messages = {
        "turn_off_light": f"Luz apagada ({state.get('device_id')}).",
        "bedtime": "Boa noite! Luzes comuns apagadas, quarto climatizado e segurança verificada.",
        "leave_home": "Casa protegida: portas trancadas, alarme armado e dispositivos não essenciais desligados.",
    }
    return {"final_response": messages.get(intent, "Solicitação concluída com sucesso.")}
