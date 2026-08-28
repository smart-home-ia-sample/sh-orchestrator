from typing import Any, TypedDict


class Task(TypedDict):
    capability: str
    intent: str
    input: dict[str, Any]


class CompletedTask(TypedDict):
    capability: str
    intent: str
    input: dict[str, Any]
    result: dict[str, Any]


class FailedTask(TypedDict):
    capability: str
    intent: str
    input: dict[str, Any]
    error: str


class OrchestratorState(TypedDict, total=False):
    request_id: str
    user_message: str
    # Recent prior user messages in the same conversation (oldest first, current
    # message NOT included) — lets the interpreter resolve short follow-ups like
    # "diminua mais" that only make sense in light of what was just discussed.
    history: list[str]
    correlation_id: str
    intent: str
    # Live home topology ({"devices": [...], "rooms": [...]}) fetched once in the
    # interpret node and reused by plan to resolve devices by (type, room).
    topology: dict
    device_id: str | None
    capability: str | None
    temperature: float | None
    brightness: int | None
    capabilities: list[str]
    selected_agents: list[str]
    pending_tasks: list[Task]
    completed_tasks: list[CompletedTask]
    failed_tasks: list[FailedTask]
    observations: dict[str, Any]
    validation_ok: bool
    final_response: str
