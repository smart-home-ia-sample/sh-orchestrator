import os

from ag_ui.core.events import RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agui import stream_run
from app.graph.build import build_graph
from smart_home_common import AgentClient, HomeMcpClient, ServiceLogin, new_id, set_current_token

router = APIRouter()
agent_client = AgentClient(os.environ["BFA_URL"], sender="orchestrator")
mcp_client = HomeMcpClient(os.environ["BFA_URL"])
graph = build_graph(agent_client, mcp_client)

# For entry points that carry no user JWT (e.g. POST /converse): act as the demo
# user so the whole chain still has a valid token to forward to the BFF.
_service_login = ServiceLogin(
    os.environ.get("BFF_URL", "http://bff:8080"),
    os.environ.get("DEMO_USER", "demo"),
    os.environ.get("DEMO_PASS", "demo"),
)


class ConverseRequest(BaseModel):
    message: str
    correlation_id: str | None = None
    # Recent prior user messages in this conversation (oldest first), so short
    # follow-ups ("diminua mais") can be resolved against what was just discussed.
    history: list[str] = []


class ConverseResponse(BaseModel):
    status: str
    final_response: str
    observations: dict
    correlation_id: str | None = None


@router.post("/converse", response_model=ConverseResponse)
async def converse(request: ConverseRequest) -> ConverseResponse:
    correlation_id = request.correlation_id or new_id()
    # No Authorization on this route -> run the chain as the demo user. Best
    # effort: if the BFF login is unreachable, proceed tokenless and let the
    # downstream calls degrade explicitly rather than 500 here.
    try:
        set_current_token(_service_login.token())
    except Exception:
        set_current_token(None)

    result = await graph.ainvoke(
        {
            "request_id": new_id(),
            "user_message": request.message,
            "history": request.history,
            "correlation_id": correlation_id,
            "completed_tasks": [],
            "failed_tasks": [],
            "observations": {},
        }
    )

    status = "ok" if result.get("validation_ok") else "error"
    return ConverseResponse(
        status=status,
        final_response=result.get("final_response", ""),
        observations=result.get("observations", {}),
        correlation_id=correlation_id,
    )


@router.post("/agui/run")
async def agui_run(run_input: RunAgentInput) -> StreamingResponse:
    return StreamingResponse(stream_run(graph, run_input), media_type=EventEncoder().get_content_type())


@router.get("/health")
def health():
    return {"status": "healthy"}


@router.get("/ready")
def ready():
    return {"status": "ready"}
