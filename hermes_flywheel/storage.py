"""JSONL storage with atomic writes, validation, and atomic claims.

Storage format: one JSON object per line in .beads/issues.jsonl
Atomic writes via temp file + os.replace() — prevents corruption on RPC thread kill.
Atomic claims via fcntl.flock() — prevents TOCTOU race in multi-agent setups.

Data model (aligned with beads_rust):
  - id: "bd-{sha256[:8]}"
  - issue_type: bug, feature, task, epic, chore, docs, question
  - priority: 0=critical, 1=high, 2=medium, 3=low, 4=backlog
  - status: open, in_progress, blocked, closed, deferred
  - dependencies: [{"id": "bd-xxx", "type": "blocks"}, ...]
    Blocking types: blocks, parent-child, waits-for, conditional-blocks
    Non-blocking: related, discovered-from, replies-to, relates-to,
                  duplicates, supersedes, caused-by
  - metadata: {...} for semantic compaction (beads_remember)
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


# ── Constants ────────────────────────────────────────────────────────────

MAX_TITLE_LENGTH = 500
MAX_DESCRIPTION_BYTES = 102_400  # 100 KB
VALID_PRIORITIES = frozenset({0, 1, 2, 3, 4})

VALID_STATUSES = frozenset({
    "open", "in_progress", "blocked", "closed", "deferred",
})

VALID_ISSUE_TYPES = frozenset({
    "bug", "feature", "task", "epic", "chore", "docs", "question",
})

BLOCKING_DEP_TYPES = frozenset({
    "blocks", "waits-for", "conditional-blocks",
})
# parent-child is intentionally NOT blocking — it's informational for
# graph visualization (DAG layers). If it were blocking, epics/stories
# would deadlock: children can't start until parent closes, but parent
# can't close until children finish.

ALL_DEP_TYPES = BLOCKING_DEP_TYPES | {
    "related", "discovered-from", "replies-to", "relates-to",
    "duplicates", "supersedes", "caused-by",
}


# ── Filesystem helpers ───────────────────────────────────────────────────

def _beads_dir(project_root: str | None = None) -> Path:
    base = Path(project_root) if project_root else Path.cwd()
    return base / ".beads"


def _issues_path(project_root: str | None = None) -> Path:
    return _beads_dir(project_root) / "issues.jsonl"


def _write_lock_path(project_root: str | None = None) -> Path:
    return _beads_dir(project_root) / ".write.lock"


def _make_hash(title: str, now: float | None = None) -> str:
    ts = now or time.time()
    raw = f"{title}:{ts}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"bd-{digest}"


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, lines: list[str]) -> None:
    _ensure_dir(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line.rstrip("\n") + "\n")
    os.replace(tmp, path)


# ── I/O ──────────────────────────────────────────────────────────────────

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


# ── File locking (atomic claims) ─────────────────────────────────────────

class _WriteLock:
    """Exclusive file lock for atomic read-modify-write operations."""

    def __init__(self, project_root: str | None = None):
        self._path = _write_lock_path(project_root)
        self._fd: int | None = None

    def __enter__(self) -> "_WriteLock":
        _ensure_dir(self._path)
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


# ── Validation ───────────────────────────────────────────────────────────

class ValidationError(ValueError):
    """Raised when bead validation fails."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _validate_bead(
    issue: dict[str, Any],
    all_ids: set[str] | None = None,
) -> None:
    """Validate a bead dict. Raises ValidationError on failure."""
    errors: list[str] = []

    # title
    title = issue.get("title", "")
    if not title or not title.strip():
        errors.append("title is required")
    elif len(title) > MAX_TITLE_LENGTH:
        errors.append(
            f"title exceeds {MAX_TITLE_LENGTH} characters (got {len(title)})"
        )

    # priority
    priority = issue.get("priority")
    if priority is None:
        errors.append("priority is required")
    elif not isinstance(priority, int) or priority not in VALID_PRIORITIES:
        errors.append(
            f"priority must be 0-4 (0=critical..4=backlog), got {priority!r}"
        )

    # status
    status = issue.get("status", "")
    if status and status not in VALID_STATUSES:
        valid = ", ".join(sorted(VALID_STATUSES))
        errors.append(f"status '{status}' not valid. Allowed: {valid}")

    # issue_type
    issue_type = issue.get("issue_type", "")
    if issue_type and issue_type not in VALID_ISSUE_TYPES:
        valid = ", ".join(sorted(VALID_ISSUE_TYPES))
        errors.append(
            f"issue_type '{issue_type}' not valid. Allowed: {valid}"
        )

    # description size
    desc = issue.get("description", "")
    if isinstance(desc, str) and len(desc.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        errors.append(f"description exceeds {MAX_DESCRIPTION_BYTES} bytes")

    # dependencies (must be list of dicts: {"id": "...", "type": "..."})
    bead_id = issue.get("id", "")
    deps = issue.get("dependencies", [])
    seen_dep_ids: set[str] = set()

    for i, dep in enumerate(deps):
        if not isinstance(dep, dict):
            errors.append(
                f"dep[{i}]: must be object {{\"id\":\"...\", \"type\":\"...\"}}, got {type(dep).__name__}"
            )
            continue

        dep_id = dep.get("id", "")
        dep_type = dep.get("type", "blocks")

        if not dep_id:
            errors.append(f"dep[{i}]: missing 'id' field")
            continue

        # self-dependency check
        if bead_id and dep_id == bead_id:
            errors.append(f"self-dependency: bead {bead_id} cannot depend on itself")

        # valid dep type
        if dep_type not in ALL_DEP_TYPES:
            valid = ", ".join(sorted(ALL_DEP_TYPES))
            errors.append(
                f"dep[{i}]: type '{dep_type}' not valid. Allowed: {valid}"
            )

        # duplicate check
        if dep_id in seen_dep_ids:
            errors.append(f"dep[{i}]: duplicate dependency on {dep_id}")
        seen_dep_ids.add(dep_id)

        # target exists check
        if all_ids is not None and dep_id not in all_ids:
            errors.append(f"dep[{i}]: target bead '{dep_id}' does not exist")

    if errors:
        raise ValidationError(errors)


# ── CRUD Operations ──────────────────────────────────────────────────────

def beads_create(
    title: str,
    issue_type: str = "task",
    priority: int = 2,
    description: str = "",
    project_root: str | None = None,
) -> dict[str, Any]:
    """Create a new bead. Returns the created issue dict.

    Priority: 0=critical, 1=high, 2=medium, 3=low, 4=backlog
    issue_type: bug, feature, task, epic, chore, docs, question
    """
    path = _issues_path(project_root)
    bead_id = _make_hash(title)

    issue: dict[str, Any] = {
        "id": bead_id,
        "title": title,
        "description": description,
        "status": "open",
        "priority": priority,
        "issue_type": issue_type,
        "dependencies": [],
        "metadata": {},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    _validate_bead(issue)

    with _WriteLock(project_root):
        existing = _read_all(path)
        if any(i["id"] == bead_id for i in existing):
            return {
                "error": f"Duplicate ID {bead_id} — re-run with different title"
            }
        existing.append(issue)
        lines = [json.dumps(i, ensure_ascii=False) for i in existing]
        _atomic_write(path, lines)

    return issue


def beads_update(
    bead_id: str,
    status: str | None = None,
    priority: int | None = None,
    dependencies: list | None = None,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
    claim: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Update an existing bead. Returns updated issue or error.

    Atomic claims: when claim=<agent_id>, uses file lock to ensure
    exactly one agent gets the claim. If already claimed by another
    agent, returns an error with the current claimant.
    """
    path = _issues_path(project_root)

    with _WriteLock(project_root):
        issues = _read_all(path)
        all_ids = {i["id"] for i in issues}

        target = None
        for issue in issues:
            if issue["id"] == bead_id:
                target = issue
                break

        if target is None:
            return {"error": f"Bead {bead_id} not found"}

        # Atomic claim check
        if claim is not None:
            existing_claim = target.get("metadata", {}).get("claimed_by")
            if existing_claim and existing_claim != claim:
                return {
                    "error": f"Bead {bead_id} already claimed by '{existing_claim}'",
                    "claimed_by": existing_claim,
                    "claimed_at": target.get("metadata", {}).get("claimed_at"),
                }

        if status is not None:
            target["status"] = status
        if priority is not None:
            target["priority"] = priority
        if dependencies is not None:
            target["dependencies"] = dependencies
        if description is not None:
            target["description"] = description
        if metadata is not None:
            target.setdefault("metadata", {}).update(metadata)
        if claim is not None:
            target.setdefault("metadata", {})["claimed_by"] = claim
            target["metadata"]["claimed_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            if status is None:
                target["status"] = "in_progress"

        target["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        _validate_bead(target, all_ids=all_ids)

        # ── Auto-close parent containers ──────────────────────────────
        # When a bead is closed, check if all siblings are also closed.
        # If so, auto-close the parent bead (story/epic).
        if status == "closed":
            for parent in issues:
                if parent["id"] == target["id"]:
                    continue
                # Check if parent has a "parent-child" dep on this bead
                for dep in parent.get("dependencies", []):
                    if dep.get("type") == "parent-child" and dep.get("id") == target["id"]:
                        # Found parent. Check if ALL its parent-child children are closed.
                        all_children_closed = True
                        for child_dep in parent.get("dependencies", []):
                            if child_dep.get("type") != "parent-child":
                                continue
                            child_id = child_dep.get("id", "")
                            child = next((i for i in issues if i["id"] == child_id), None)
                            if child and child["status"] != "closed":
                                all_children_closed = False
                                break
                        if all_children_closed and parent["status"] not in ("closed",):
                            parent["status"] = "closed"
                            parent["updated_at"] = time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            )

        lines = [json.dumps(i, ensure_ascii=False) for i in issues]
        _atomic_write(path, lines)

    return target


def beads_list(
    status: str | None = None,
    priority_min: int | None = None,
    priority_max: int | None = None,
    issue_type: str | None = None,
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
    if issue_type:
        issues = [i for i in issues if i.get("issue_type") == issue_type]
    if claimed_by:
        issues = [
            i
            for i in issues
            if i.get("metadata", {}).get("claimed_by") == claimed_by
        ]

    return {"count": len(issues), "beads": issues}


def beads_ready(project_root: str | None = None) -> dict[str, Any]:
    """Return actionable beads — open, unblocked, unclaimed, sorted by priority.

    Only dependencies with blocking types (blocks, parent-child, waits-for,
    conditional-blocks) prevent a bead from being ready.
    """
    path = _issues_path(project_root)
    issues = _read_all(path)

    if not issues:
        return {
            "count": 0,
            "beads": [],
            "message": "No beads found. Create beads first with beads_create.",
        }

    # Build set of blocked-by IDs (only blocking dep types)
    blocked: set[str] = set()
    for issue in issues:
        for dep in issue.get("dependencies", []):
            dep_type = dep.get("type", "blocks")
            if dep_type not in BLOCKING_DEP_TYPES:
                continue
            dep_id = dep.get("id", "")
            dep_issue = next((i for i in issues if i["id"] == dep_id), None)
            if dep_issue and dep_issue["status"] != "closed":
                blocked.add(issue["id"])
                break

    # Also block any that are already claimed
    for issue in issues:
        if issue.get("metadata", {}).get("claimed_by"):
            blocked.add(issue["id"])

    ready = [
        i
        for i in issues
        if i["id"] not in blocked
        and i["status"] in ("open", "deferred")
        and not i.get("metadata", {}).get("claimed_by")
    ]

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
        return {"total": 0, "by_status": {}, "by_priority": {}, "by_issue_type": {}}

    by_status: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total_deps = 0
    claimed = 0

    for issue in issues:
        s = issue.get("status", "open")
        by_status[s] = by_status.get(s, 0) + 1
        p = str(issue.get("priority", 2))
        by_priority[p] = by_priority.get(p, 0) + 1
        t = issue.get("issue_type", "task")
        by_type[t] = by_type.get(t, 0) + 1
        total_deps += len(issue.get("dependencies", []))
        if issue.get("metadata", {}).get("claimed_by"):
            claimed += 1

    # Count truly ready beads (not just open+unclaimed)
    ready_count = 0
    for issue in issues:
        if issue.get("status") != "open":
            continue
        if issue.get("metadata", {}).get("claimed_by"):
            continue
        # Check if any blocking dep is unclosed
        blocked = False
        for dep in issue.get("dependencies", []):
            if dep.get("type", "blocks") not in BLOCKING_DEP_TYPES:
                continue
            dep_id = dep.get("id", "")
            dep_issue = next((i for i in issues if i["id"] == dep_id), None)
            if dep_issue and dep_issue["status"] != "closed":
                blocked = True
                break
        if not blocked:
            ready_count += 1

    return {
        "total": len(issues),
        "by_status": by_status,
        "by_priority": by_priority,
        "by_issue_type": by_type,
        "total_dependencies": total_deps,
        "claimed": claimed,
        "ready_now": ready_count,
    }
