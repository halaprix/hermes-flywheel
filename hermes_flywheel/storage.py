"""JSONL storage with atomic writes — mirrors beads_rust sync safety protocol.

Storage format: one JSON object per line in .beads/issues.jsonl
Atomic writes via temp file + os.replace() — prevents corruption on RPC thread kill.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional


def _beads_dir(project_root: str | None = None) -> Path:
    """Resolve .beads directory under project root or cwd."""
    base = Path(project_root) if project_root else Path.cwd()
    return base / ".beads"


def _issues_path(project_root: str | None = None) -> Path:
    return _beads_dir(project_root) / "issues.jsonl"


def _make_hash(title: str, now: float | None = None) -> str:
    """Deterministic hash-based ID: bd-{8 hex chars}."""
    ts = now or time.time()
    raw = f"{title}:{ts}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"bd-{digest}"


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, lines: list[str]) -> None:
    """Write lines to temp file, then atomically replace target."""
    _ensure_dir(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line.rstrip("\n") + "\n")
    os.replace(tmp, path)


def _read_all(path: Path) -> list[dict[str, Any]]:
    """Read all issues from JSONL. Returns empty list if file missing."""
    if not path.exists():
        return []
    issues = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                issues.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return issues


def beads_create(
    title: str,
    type_: str = "task",
    priority: int = 2,
    description: str = "",
    project_root: str | None = None,
) -> dict[str, Any]:
    """Create a new bead. Returns the created issue dict.

    Priority: 0=critical, 1=high, 2=medium, 3=low, 4=backlog
    """
    path = _issues_path(project_root)
    bead_id = _make_hash(title)

    issue: dict[str, Any] = {
        "id": bead_id,
        "title": title,
        "description": description,
        "status": "open",
        "priority": priority,
        "type": type_,
        "dependencies": [],
        "metadata": {},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    existing = _read_all(path)
    # Check for duplicate ID (should be near-impossible with hash)
    if any(i["id"] == bead_id for i in existing):
        return {"error": f"Duplicate ID {bead_id} — re-run with different title"}

    existing.append(issue)
    lines = [json.dumps(i, ensure_ascii=False) for i in existing]
    _atomic_write(path, lines)

    return issue


def beads_update(
    bead_id: str,
    status: str | None = None,
    priority: int | None = None,
    dependencies: list[str] | None = None,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
    claim: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Update an existing bead. Returns updated issue or error.

    claim: agent identifier for reservation lock (e.g. "agent-3")
    """
    path = _issues_path(project_root)
    issues = _read_all(path)

    found = False
    for issue in issues:
        if issue["id"] == bead_id:
            found = True
            if status is not None:
                issue["status"] = status
            if priority is not None:
                issue["priority"] = priority
            if dependencies is not None:
                issue["dependencies"] = dependencies
            if description is not None:
                issue["description"] = description
            if metadata is not None:
                issue["metadata"].update(metadata)
            if claim is not None:
                issue["metadata"]["claimed_by"] = claim
                issue["metadata"]["claimed_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                )
                if status is None:
                    issue["status"] = "in_progress"
            issue["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            break

    if not found:
        return {"error": f"Bead {bead_id} not found"}

    lines = [json.dumps(i, ensure_ascii=False) for i in issues]
    _atomic_write(path, lines)

    return issue


def beads_list(
    status: str | None = None,
    priority_min: int | None = None,
    priority_max: int | None = None,
    type_: str | None = None,
    claimed_by: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """List beads with optional filters."""
    path = _issues_path(project_root)
    issues = _read_all(path)

    if status:
        issues = [i for i in issues if i["status"] == status]
    if priority_min is not None:
        issues = [i for i in issues if i["priority"] >= priority_min]
    if priority_max is not None:
        issues = [i for i in issues if i["priority"] <= priority_max]
    if type_:
        issues = [i for i in issues if i["type"] == type_]
    if claimed_by:
        issues = [i for i in issues if i.get("metadata", {}).get("claimed_by") == claimed_by]

    return {"count": len(issues), "beads": issues}


def beads_ready(project_root: str | None = None) -> dict[str, Any]:
    """Return actionable beads — open, unblocked, unclaimed, sorted by priority."""
    path = _issues_path(project_root)
    issues = _read_all(path)

    if not issues:
        return {"count": 0, "beads": [], "message": "No beads found. Create beads first with beads_create."}

    # Build set of blocked-by IDs
    blocked: set[str] = set()
    for issue in issues:
        for dep in issue.get("dependencies", []):
            # Check if the dependency is NOT closed
            dep_issue = next((i for i in issues if i["id"] == dep), None)
            if dep_issue and dep_issue["status"] != "closed":
                blocked.add(issue["id"])
                break

    # Also block any that are already claimed
    for issue in issues:
        if issue.get("metadata", {}).get("claimed_by"):
            blocked.add(issue["id"])

    ready = [
        i for i in issues
        if i["id"] not in blocked
        and i["status"] in ("open", "deferred")
        and not i.get("metadata", {}).get("claimed_by")
    ]

    # Sort by priority (ascending: 0=critical first), then by created_at
    ready.sort(key=lambda i: (i["priority"], i.get("created_at", "")))

    return {"count": len(ready), "beads": ready}


def beads_remember(
    bead_id: str,
    key: str,
    value: Any,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Store persistent memory inside bead metadata (semantic compaction)."""
    return beads_update(
        bead_id=bead_id,
        metadata={key: value},
        project_root=project_root,
    )


def beads_stats(project_root: str | None = None) -> dict[str, Any]:
    """Return summary statistics about the bead graph."""
    path = _issues_path(project_root)
    issues = _read_all(path)

    if not issues:
        return {"total": 0, "by_status": {}, "by_priority": {}, "by_type": {}}

    by_status: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total_deps = 0
    claimed = 0

    for issue in issues:
        s = issue["status"]
        by_status[s] = by_status.get(s, 0) + 1
        p = str(issue["priority"])
        by_priority[p] = by_priority.get(p, 0) + 1
        t = issue["type"]
        by_type[t] = by_type.get(t, 0) + 1
        total_deps += len(issue.get("dependencies", []))
        if issue.get("metadata", {}).get("claimed_by"):
            claimed += 1

    return {
        "total": len(issues),
        "by_status": by_status,
        "by_priority": by_priority,
        "by_type": by_type,
        "total_dependencies": total_deps,
        "claimed": claimed,
        "ready_now": sum(1 for i in issues if i["status"] == "open" and not i.get("metadata", {}).get("claimed_by")),
    }
