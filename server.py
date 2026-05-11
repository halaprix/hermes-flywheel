#!/usr/bin/env python3
"""Hermes Flywheel MCP Server — lightweight beads task-graph for Hermes Agent.

A Flywheel-powered agentic workflow: Plan → Beads → Triage → Execute → Memory.

Provides:
  beads_create   — Create a new bead with hash ID
  beads_update   — Update status, dependencies, metadata, claim
  beads_list     — List beads with optional filters
  beads_ready    — Return actionable (unblocked, unclaimed) beads
  beads_triage   — Full graph analytics: PageRank, critical path, bottlenecks
  beads_remember — Store persistent knowledge in bead metadata
  flywheel_land  — "Land the Plane" shutdown protocol
  flywheel_stats — Summary statistics

Run: python -m hermes_flywheel.server
Configure in ~/.hermes/config.yaml:
  mcp_servers:
    flywheel:
      command: "python"
      args: ["-m", "hermes_flywheel.server"]
      env:
        FLYWHEEL_PROJECT_ROOT: "/path/to/project"
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Hermes Flywheel")

PROJECT_ROOT = os.environ.get("FLYWHEEL_PROJECT_ROOT", os.getcwd())


# ── Import storage and graph (relative or absolute) ────────────────────
try:
    from . import graph as _graph
    from . import storage as _store
except ImportError:
    import graph as _graph  # type: ignore[no-redef]
    import storage as _store  # type: ignore[no-redef]


# ═══════════════════════════════════════════════════════════════════════
# Core Bead Operations
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool
def beads_create(
    title: str,
    type: str = "task",
    priority: int = 2,
    description: str = "",
) -> dict:
    """Create a new bead (task) in the flywheel.

    Priority scale: 0=critical(security/bug), 1=high, 2=medium(default), 3=low, 4=backlog.
    Types: bug, feature, task, epic, chore.

    Returns the created bead with its hash ID (bd-xxxxxxxx).
    """
    return _store.beads_create(
        title=title,
        type_=type,
        priority=priority,
        description=description,
        project_root=PROJECT_ROOT,
    )


@mcp.tool
def beads_update(
    bead_id: str,
    status: str | None = None,
    priority: int | None = None,
    dependencies: list[str] | None = None,
    description: str | None = None,
    claim: str | None = None,
) -> dict:
    """Update an existing bead.

    status: open, in_progress, blocked, closed, deferred
    claim: agent identifier to atomically reserve this bead (prevents duplicate work).
           When claim is set, status auto-changes to 'in_progress'.
    dependencies: list of bead IDs that block this bead.
    """
    return _store.beads_update(
        bead_id=bead_id,
        status=status,
        priority=priority,
        dependencies=dependencies,
        description=description,
        claim=claim,
        project_root=PROJECT_ROOT,
    )


@mcp.tool
def beads_list(
    status: str | None = None,
    priority_min: int | None = None,
    priority_max: int | None = None,
    type: str | None = None,
) -> dict:
    """List beads with optional filters.

    status: open, in_progress, blocked, closed, deferred
    priority_min/priority_max: filter by priority range
    type: bug, feature, task, epic, chore
    """
    return _store.beads_list(
        status=status,
        priority_min=priority_min,
        priority_max=priority_max,
        type_=type,
        project_root=PROJECT_ROOT,
    )


@mcp.tool
def beads_ready() -> dict:
    """Return actionable beads — open, unblocked, unclaimed — sorted by priority.

    These are the beads you can start working on RIGHT NOW.
    Beads with unsatisfied dependencies or claimed by another agent are excluded.
    """
    return _store.beads_ready(project_root=PROJECT_ROOT)


# ═══════════════════════════════════════════════════════════════════════
# Graph Analytics
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool
def beads_triage() -> dict:
    """Full graph triage — the central intelligence of the flywheel.

    Returns:
    - robot_next: the SINGLE best bead to work on (highest PageRank among ready beads)
    - critical_path: the longest dependency chain determining minimum project time
    - bottlenecks: top 5 betweenness centrality beads (connecting otherwise disconnected work)
    - cycles: any cyclic dependencies that need fixing BEFORE implementation
    - ready_beads: top 10 actionable beads sorted by priority
    - stats: summary counts by status, priority, type

    Call this when you need to decide what to work on next or before launching a swarm.
    """
    return _graph.beads_triage(project_root=PROJECT_ROOT)


# ═══════════════════════════════════════════════════════════════════════
# Memory & Knowledge
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool
def beads_remember(
    bead_id: str,
    key: str,
    value: str,
) -> dict:
    """Store persistent knowledge inside a bead's metadata.

    Use this for semantic compaction — store architectural decisions, gotchas,
    or agent-discovered patterns directly in the task, avoiding separate MEMORY.md files
    that consume context window space.

    Examples:
    - key="pattern", value="use adapter pattern for payment integrations"
    - key="gotcha", value="don't use async in this module — conflicts with library X"
    """
    return _store.beads_remember(
        bead_id=bead_id,
        key=key,
        value=value,
        project_root=PROJECT_ROOT,
    )


# ═══════════════════════════════════════════════════════════════════════
# Operational Protocols
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool
def flywheel_land(bead_id: str | None = None) -> dict:
    """Execute the 'Land the Plane' shutdown protocol.

    Call this at the END of every agent session. Ensures clean state:
    1. Updates bead status to closed (if provided)
    2. Returns any open issues that need follow-up
    3. Returns checklist: lint, test, rebase, push

    This guarantees every session terminates with stable, production-ready state.
    """
    result: dict = {
        "checklist": [
            "✓ Run linter/formatter on changed files",
            "✓ Run unit tests (or note why skipped)",
            "✓ git add + commit with bead ID in message",
            "✓ git rebase main (if needed)",
            "✓ git push",
        ],
        "follow_up_beads": [],
        "closed_bead": None,
    }

    if bead_id:
        update_result = _store.beads_update(
            bead_id=bead_id,
            status="closed",
            project_root=PROJECT_ROOT,
        )
        if "error" not in update_result:
            result["closed_bead"] = {
                "id": bead_id,
                "title": update_result.get("title", ""),
            }

    # Find any open beads that were touched but not closed
    try:
        from .storage import _read_all, _issues_path as _store_issues_path
    except ImportError:
        from storage import _read_all, _issues_path as _store_issues_path  # type: ignore[no-redef]
    all_issues = _read_all(_store_issues_path(PROJECT_ROOT))
    for issue in all_issues:
        if issue["status"] == "in_progress" and issue.get("metadata", {}).get("claimed_by"):
            result["follow_up_beads"].append({
                "id": issue["id"],
                "title": issue["title"],
                "status": issue["status"],
                "claimed_by": issue["metadata"]["claimed_by"],
            })

    return result


@mcp.tool
def flywheel_stats() -> dict:
    """Return summary statistics for the current flywheel project."""
    return _store.beads_stats(project_root=PROJECT_ROOT)


# ═══════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run()
