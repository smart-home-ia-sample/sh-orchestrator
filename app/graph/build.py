from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.nodes import (
    AgentClientLike,
    McpClientLike,
    chitchat,
    discover,
    final,
    make_collect,
    make_dispatch,
    make_interpret,
    plan,
    recovery_explain,
    route_after_interpret,
    route_after_validate,
    validate,
)
from app.graph.state import OrchestratorState


def build_graph(agent_client: AgentClientLike, mcp_client: McpClientLike) -> CompiledStateGraph:
    graph = StateGraph(OrchestratorState)

    graph.add_node("interpret", make_interpret(mcp_client))
    graph.add_node("chitchat", chitchat)
    graph.add_node("discover", discover)
    graph.add_node("plan", plan)
    graph.add_node("dispatch", make_dispatch(agent_client))
    graph.add_node("collect", make_collect(mcp_client))
    graph.add_node("validate", validate)
    graph.add_node("recovery_explain", recovery_explain)
    graph.add_node("final", final)

    graph.add_edge(START, "interpret")
    graph.add_conditional_edges(
        "interpret", route_after_interpret, {"chitchat": "chitchat", "continue": "discover"}
    )
    graph.add_edge("chitchat", END)
    graph.add_edge("discover", "plan")
    graph.add_edge("plan", "dispatch")
    graph.add_edge("dispatch", "collect")
    graph.add_edge("collect", "validate")
    graph.add_conditional_edges("validate", route_after_validate, {"ok": "final", "failed": "recovery_explain"})
    graph.add_edge("final", END)
    graph.add_edge("recovery_explain", END)

    return graph.compile()
