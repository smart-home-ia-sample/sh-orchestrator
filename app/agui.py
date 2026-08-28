import json
from collections.abc import AsyncIterator

from ag_ui.core.events import (
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

from smart_home_common import new_id

encoder = EventEncoder()


def _last_user_message(run_input: RunAgentInput) -> str:
    for message in reversed(run_input.messages):
        if message.role == "user":
            return message.content or ""
    return ""


def _prior_user_history(run_input: RunAgentInput, limit: int = 3) -> list[str]:
    """Prior user messages in this thread, oldest first, excluding the current
    (last) one — the frontend already accumulates full conversation history and
    sends it on every run, so it's available here for free."""
    user_messages = [m.content or "" for m in run_input.messages if m.role == "user"]
    return user_messages[:-1][-limit:]


def _tool_call_events(task: dict, ok: bool) -> list[str]:
    tool_call_id = new_id()
    content = json.dumps(task["result"] if ok else {"error": task.get("error")})
    return [
        encoder.encode(ToolCallStartEvent(tool_call_id=tool_call_id, tool_call_name=task["capability"])),
        encoder.encode(ToolCallEndEvent(tool_call_id=tool_call_id)),
        encoder.encode(
            ToolCallResultEvent(message_id=new_id(), tool_call_id=tool_call_id, content=content, role="tool")
        ),
    ]


async def stream_run(graph, run_input: RunAgentInput) -> AsyncIterator[str]:
    thread_id = run_input.thread_id
    run_id = run_input.run_id

    yield encoder.encode(RunStartedEvent(thread_id=thread_id, run_id=run_id))

    initial_state = {
        "request_id": run_id,
        "user_message": _last_user_message(run_input),
        "history": _prior_user_history(run_input),
        "correlation_id": thread_id,
        "completed_tasks": [],
        "failed_tasks": [],
        "observations": {},
    }

    final_state: dict = dict(initial_state)

    try:
        async for chunk in graph.astream(initial_state, stream_mode="updates"):
            for step_name, partial_state in chunk.items():
                yield encoder.encode(StepStartedEvent(step_name=step_name))

                if step_name == "dispatch":
                    prior_completed = len(final_state.get("completed_tasks", []))
                    prior_failed = len(final_state.get("failed_tasks", []))

                    for task in partial_state.get("completed_tasks", [])[prior_completed:]:
                        for event in _tool_call_events(task, ok=True):
                            yield event
                    for task in partial_state.get("failed_tasks", [])[prior_failed:]:
                        for event in _tool_call_events(task, ok=False):
                            yield event

                final_state.update(partial_state)
                yield encoder.encode(StepFinishedEvent(step_name=step_name))

        message_id = new_id()
        yield encoder.encode(TextMessageStartEvent(message_id=message_id, role="assistant"))
        yield encoder.encode(
            TextMessageContentEvent(message_id=message_id, delta=final_state.get("final_response", ""))
        )
        yield encoder.encode(TextMessageEndEvent(message_id=message_id))

        yield encoder.encode(
            RunFinishedEvent(thread_id=thread_id, run_id=run_id, result=final_state.get("observations", {}))
        )
    except Exception as exc:
        yield encoder.encode(RunErrorEvent(message=str(exc)))
