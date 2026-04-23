"""Wires triage nodes: fetch → dedup → … → finalize → mark_read (Gmail UNREAD) → END."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from agent_hub_core.domain.enums import IncidentSeverity

from incident_triage.graph.state import TriageState
from incident_triage.graph.nodes import classify, dedup, enrich, fetch, finalize, mark_read
from incident_triage.graph.nodes.tools import slack_tool


def _route_after_dedup(state: TriageState) -> str:
    if state.duplicate_message:
        return "stop"
    return "continue"


def _route_by_severity(state: TriageState) -> str:
    """Pure branch: high-severity + confidence → ``notify`` (Slack), else ``skip`` to finalize."""
    if state.confidence < 0.6 or state.incident_type is None:
        return "skip"
    if state.severity in (
        IncidentSeverity.critical,
        IncidentSeverity.high,
        IncidentSeverity.medium,
    ):
        return "notify"
    return "skip"


def build_graph(checkpointer=None):
    """Compile the graph; pass a checkpointer from lifespan when Postgres persistence is enabled."""
    g = StateGraph(TriageState)
    g.add_node("fetch", fetch.run)
    g.add_node("dedup", dedup.run)
    g.add_node("enrich", enrich.run)
    g.add_node("classify", classify.run)
    g.add_node("slack", slack_tool.run)
    g.add_node("finalize", finalize.run)
    g.add_node("mark_read", mark_read.run)
    g.set_entry_point("fetch")
    g.add_edge("fetch", "dedup")
    g.add_conditional_edges(
        "dedup",
        _route_after_dedup,
        {"continue": "enrich", "stop": END},
    )
    g.add_edge("enrich", "classify")
    g.add_conditional_edges(
        "classify",
        _route_by_severity,
        {"notify": "slack", "skip": "finalize"},
    )
    g.add_edge("slack", "finalize")
    g.add_edge("finalize", "mark_read")
    g.add_edge("mark_read", END)
    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()
