#!/usr/bin/env python3
"""Hermes Flywheel MCP Server — lightweight beads task-graph for Hermes Agent.

A Flywheel-powered agentic workflow: Plan → Beads → Triage → Execute → Memory.

Provides:
  beads_create   — Create a new bead with hash ID (issue_type: bug/feature/task/epic/chore/docs/question)
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


# ── Import storage, graph, and formulas ────────────────────────────────
try:
    from . import graph as _graph
    from . import storage as _store
    from . import formulas as _formulas
    from .storage import _read_all, _issues_path
except ImportError:
    import graph as _graph  # type: ignore[no-redef]
    import storage as _store  # type: ignore[no-redef]
    import formulas as _formulas  # type: ignore[no-redef]
    from storage import _read_all, _issues_path  # type: ignore[no-redef]


# ═══════════════════════════════════════════════════════════════════════
# Core Bead Operations
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def beads_create(
    title: str,
    issue_type: str = "task",
    priority: int = 2,
    description: str = "",
    project_root: str = "",
) -> dict:
    """Create a new bead (task) in the flywheel.

    Priority scale: 0=critical(security/bug), 1=high, 2=medium(default), 3=low, 4=backlog.
    issue_type: bug, feature, task, epic, chore, docs, question.
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)

    Returns the created bead with its hash ID (bd-xxxxxxxx).
    """
    pr = project_root or PROJECT_ROOT
    return _store.beads_create(
        title=title,
        issue_type=issue_type,
        priority=priority,
        description=description,
        project_root=pr,
    )


@mcp.tool()
def beads_update(
    bead_id: str,
    status: str | None = None,
    priority: int | None = None,
    dependencies: list[str] | None = None,
    description: str | None = None,
    claim: str | None = None,
    project_root: str = "",
) -> dict:
    """Update an existing bead.

    status: open, in_progress, blocked, closed, deferred
    claim: agent identifier to atomically reserve this bead (prevents duplicate work).
           When claim is set, status auto-changes to 'in_progress'.
           Uses file locking — if already claimed by another agent, returns error.
    dependencies: list of bead IDs or dep objects that block this bead.
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)
    """
    pr = project_root or PROJECT_ROOT
    return _store.beads_update(
        bead_id=bead_id,
        status=status,
        priority=priority,
        dependencies=dependencies,
        description=description,
        claim=claim,
        project_root=pr,
    )


@mcp.tool()
def beads_list(
    status: str | None = None,
    priority_min: int | None = None,
    priority_max: int | None = None,
    issue_type: str | None = None,
    project_root: str = "",
) -> dict:
    """List beads with optional filters.

    status: open, in_progress, blocked, closed, deferred
    priority_min/priority_max: filter by priority range
    issue_type: bug, feature, task, epic, chore, docs, question
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)
    """
    pr = project_root or PROJECT_ROOT
    return _store.beads_list(
        status=status,
        priority_min=priority_min,
        priority_max=priority_max,
        issue_type=issue_type,
        project_root=pr,
    )


@mcp.tool()
def beads_ready(project_root: str = "") -> dict:
    """Return actionable beads — open, unblocked, unclaimed — sorted by priority.

    These are the beads you can start working on RIGHT NOW.
    Beads with unsatisfied dependencies or claimed by another agent are excluded.
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)
    """
    pr = project_root or PROJECT_ROOT
    return _store.beads_ready(project_root=pr)


# ═══════════════════════════════════════════════════════════════════════
# Graph Analytics
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def beads_triage(project_root: str = "") -> dict:
    """Full graph triage — the central intelligence of the flywheel.

    Returns:
    - robot_next: the SINGLE best bead to work on (highest PageRank among ready beads)
    - critical_path: the longest dependency chain determining minimum project time
    - bottlenecks: top 5 betweenness centrality beads (connecting otherwise disconnected work)
    - cycles: any cyclic dependencies that need fixing BEFORE implementation
    - ready_beads: top 10 actionable beads sorted by priority
    - stats: summary counts by status, priority, type

    Call this when you need to decide what to work on next or before launching a swarm.
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)
    """
    pr = project_root or PROJECT_ROOT
    return _graph.beads_triage(project_root=pr)


# ═══════════════════════════════════════════════════════════════════════
# Memory & Knowledge
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def beads_remember(
    bead_id: str,
    key: str,
    value: str,
    project_root: str = "",
) -> dict:
    """Store persistent knowledge inside a bead's metadata.

    Use this for semantic compaction — store architectural decisions, gotchas,
    or agent-discovered patterns directly in the task, avoiding separate MEMORY.md files
    that consume context window space.

    Examples:
    - key="pattern", value="use adapter pattern for payment integrations"
    - key="gotcha", value="don't use async in this module — conflicts with library X"
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)
    """
    pr = project_root or PROJECT_ROOT
    return _store.beads_remember(
        bead_id=bead_id,
        key=key,
        value=value,
        project_root=pr,
    )


# ═══════════════════════════════════════════════════════════════════════
# Operational Protocols
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def flywheel_land(bead_id: str | None = None, project_root: str = "") -> dict:
    """Execute the 'Land the Plane' shutdown protocol.

    Call this at the END of every agent session. Ensures clean state:
    1. Updates bead status to closed (if provided)
    2. Returns any open issues that need follow-up
    3. Returns checklist: lint, test, rebase, push

    This guarantees every session terminates with stable, production-ready state.
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)
    """
    pr = project_root or PROJECT_ROOT
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
            project_root=pr,
        )
        if "error" not in update_result:
            result["closed_bead"] = {
                "id": bead_id,
                "title": update_result.get("title", ""),
            }

    # Find any open beads that were touched but not closed
    all_issues = _read_all(_issues_path(pr))
    for issue in all_issues:
        if issue["status"] == "in_progress" and issue.get("metadata", {}).get("claimed_by"):
            result["follow_up_beads"].append({
                "id": issue["id"],
                "title": issue["title"],
                "status": issue["status"],
                "claimed_by": issue["metadata"]["claimed_by"],
            })

    return result


@mcp.tool()
def flywheel_stats(project_root: str = "") -> dict:
    """Return summary statistics for the current flywheel project.
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)
    """
    pr = project_root or PROJECT_ROOT
    return _store.beads_stats(project_root=pr)


# ═══════════════════════════════════════════════════════════════════════
# Formulas — Reusable Workflow Templates
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def formula_pour(
    formula_name: str,
    vars: dict | None = None,
    dry_run: bool = False,
    project_root: str = "",
) -> dict:
    """Pour a formula into a molecule — creates parent + child beads.

    Formulas are TOML workflow templates. Pouring expands them into a hierarchy
    of beads with dependencies. Built-in formulas: feature, bugfix, refactor, release.

    Example: formula_pour('feature', {'feature_name': 'dark mode'})
    Preview: formula_pour('feature', {'feature_name': 'dark mode'}, dry_run=True)
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)
    """
    pr = project_root or PROJECT_ROOT
    return _formulas.formula_pour(
        formula_name=formula_name,
        vars=vars,
        project_root=pr,
        dry_run=dry_run,
    )


@mcp.tool()
def formula_list(project_root: str = "") -> dict:
    """List available formulas (project, user, and built-in).

    Returns formula names, descriptions, step counts, and locations.
    Use formula_install to add a built-in formula to your project.
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)
    """
    pr = project_root or PROJECT_ROOT
    return _formulas.formula_list(project_root=pr)


@mcp.tool()
def formula_install(name: str, project_root: str = "") -> dict:
    """Install a built-in formula into the project's .beads/formulas/ directory.

    Built-in formulas: feature, bugfix, refactor, release.
    After installing, use formula_pour to create molecules.
    project_root: path to project directory (default: FLYWHEEL_PROJECT_ROOT env var or cwd)
    """
    pr = project_root or PROJECT_ROOT
    return _formulas.formula_install_builtin(name=name, project_root=pr)


DASHBOARD_PORT = int(os.environ.get("FLYWHEEL_DASHBOARD_PORT", "0"))


# ═══════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if DASHBOARD_PORT > 0:
        import threading
        from http.server import HTTPServer

        # Add dashboard dir to path
        import sys as _sys
        _dashboard_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "dashboard"
        )
        _sys.path.insert(0, _dashboard_dir)

        from server import Handler as DashHandler  # type: ignore[import-not-found]

        def _start_dashboard():
            httpd = HTTPServer(("127.0.0.1", DASHBOARD_PORT), DashHandler)
            print(f"🌀 Dashboard → http://127.0.0.1:{DASHBOARD_PORT}")
            httpd.serve_forever()

        t = threading.Thread(target=_start_dashboard, daemon=True)
        t.start()

    mcp.run()
