"""JSONL storage with atomic writes, validation, and atomic claims.

Storage format: one JSON object per line in .beads/issues.jsonl
Atomic writes via temp file + os.replace() — prevents corruption on RPC thread kill.
Atomic claims via fcntl.flock() — prevents TOCTOU race in multi-agent setups.

Data model (aligned with beads_rust v0.2.7):
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
from typing import Any, Optional

# ── Constants (aligned with beads_rust) ──────────────────────────────────

MAX_TITLE_LENGTH = 500
MAX_DESCRIPTION_BYTES = 102_400  # 100 KB
VALID_PRIORITIES = frozenset({0, 1, 2, 3, 4})

VALID_STATUSES = frozenset({
    "open", "in_progress", "blocked", "closed", "deferred",
    # extended set (beads_rust compat)
    "draft", "tombstone", "pinned",
})

VALID_ISSUE_TYPES = frozenset({
    "bug", "feature", "task", "epic", "chore", "docs", "question",
})

# Dependency types that BLOCK ready status (bead is NOT ready if any
# blocking dep target is unresolved)
BLOCKING_DEP_TYPES = frozenset({
    "blocks", "parent-child", "waits-for", "conditional-blocks",
})

ALL_DEP_TYPES = BLOCKING_DEP_TYPES | {
    "related", "discovered-from", "replies-to", "relates-to",
    "duplicates", "supersedes", "caused-by",
}

# ── Filesystem helpers ───────────────────────────────────────────────────

def _beads_dir(project_root: str | None = None) -> Path:
    """Resolve .beads directory under project root or cwd."""
    base = Path(project_root) if project_root else Path.cwd()
    return base / ".beads"


def _issues_path(project_root: str | None = None) -> Path:
    return _beads_dir(project_root) / "issues.jsonl"


def _write_lock_path(project_root: str | None = None) -> Path:
    return _beads_dir(project_root) / ".write.lock"


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


# ── Backward compat: normalize old dep format ────────────────────────────

def _normalize_dep(dep: str | dict[str, Any]) -> dict[str, Any]:
    """Normalize a dependency entry to the object format.

    Old format: "bd-abc123" → {"id": "bd-abc123", "type": "blocks"}
    New format: {"id": "bd-abc123", "type": "blocks"} → passthrough
    """
    if isinstance(dep, str):
        return {"id": dep, "type": "blocks"}
    # Ensure it has 'id' and 'type' keys
    if "id" not in dep:
        raise ValueError(f"Dependency missing 'id': {dep}")
    dep.setdefault("type", "blocks")
    return dep


def _normalize_deps(deps: list) -> list[dict[str, Any]]:
    """Normalize a list of dependencies to object format."""
    return [_normalize_dep(d) for d in deps]


def _dep_ids(deps: list) -> list[str]:
    """Extract just the bead IDs from dependency objects/strings."""
    return [d["id"] if isinstance(d, dict) else d for d in deps]


# ── Backward compat: normalize old type field ────────────────────────────

def _normalize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Normalize an issue dict: migrate 'type' → 'issue_type', deps format."""
    # Field rename: type → issue_type
    if "type" in issue and "issue_type" not in issue:
        issue["issue_type"] = issue.pop("type")
    if "issue_type" not in issue:
        issue["issue_type"] = "task"
    # Normalize dependencies
    if "dependencies" in issue:
        issue["dependencies"] = _normalize_deps(issue["dependencies"])
    # Ensure metadata exists
    issue.setdefault("metadata", {})
    return issue


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
                issue = json.loads(line)
                issues.append(_normalize_issue(issue))
            except json.JSONDecodeError:
                continue
    return issues


# ── File locking (atomic claims) ─────────────────────────────────────────

class _WriteLock:
    """Exclusive file lock for atomic read-modify-write operations.

    Uses fcntl.flock() on .beads/.write.lock — OS-enforced exclusive access.
    This eliminates the TOCTOU race in claim operations.
    """

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
    is_create: bool = False,
) -> None:
    """Validate a bead dict. Raises ValidationError on failure.

    Args:
        issue: The bead dict to validate.
        all_ids: Set of all existing bead IDs (needed for dep validation).
        is_create: If True, applies create-only checks.
    """
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
        errors.append(
            f"description exceeds {MAX_DESCRIPTION_BYTES} bytes"
        )

    # dependencies
    bead_id = issue.get("id", "")
    deps = issue.get("dependencies", [])
    seen_dep_ids: set[str] = set()

    for i, dep in enumerate(deps):
        dep = _normalize_dep(dep)
        dep_id = dep["id"]
        dep_type = dep.get("type", "blocks")

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
    Types: bug, feature, task, epic, chore, docs, question
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

    # Validate
    _validate_bead(issue, is_create=True)

    with _WriteLock(project_root):
        existing = _read_all(path)

        # Check for duplicate ID
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

    claim: agent identifier for reservation lock (e.g. "agent-3")
    """
    path = _issues_path(project_root)

    # Normalize dependencies if provided
    if dependencies is not None:
        dependencies = _normalize_deps(dependencies)

    with _WriteLock(project_root):
        issues = _read_all(path)
        all_ids = {i["id"] for i in issues}

        # Find the target bead
        found = False
        target = None
        for issue in issues:
            if issue["id"] == bead_id:
                found = True
                target = issue
                break

        if not found:
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

        # Apply updates
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

        # Validate the updated bead
        _validate_bead(target, all_ids=all_ids)

        # Write back
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
        issues = [i for i in issues if i["issue_type"] == issue_type]
    if claimed_by:
        issues = [
            i
            for i in issues
            if i.get("metadata", {}).get("claimed_by") == claimed_by
        ]

    return {"count": len(issues), "beads": issues}


def beads_ready(project_root: str | None = None) -> dict[str, Any]:
    """Return actionable beads — open, unblocked, unclaimed, sorted by priority.

    Blocking check: only dependencies with blocking types (blocks, parent-child,
    waits-for, conditional-blocks) prevent a bead from being ready.
    Non-blocking deps (related, discovered-from, etc.) don't affect readiness.
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
            dep = _normalize_dep(dep)
            dep_type = dep.get("type", "blocks")
            # Only blocking types matter for readiness
            if dep_type not in BLOCKING_DEP_TYPES:
                continue
            dep_id = dep["id"]
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
        return {"total": 0, "by_status": {}, "by_priority": {}, "by_issue_type": {}}

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
        t = issue.get("issue_type", "task")
        by_type[t] = by_type.get(t, 0) + 1
        total_deps += len(issue.get("dependencies", []))
        if issue.get("metadata", {}).get("claimed_by"):
            claimed += 1

    return {
        "total": len(issues),
        "by_status": by_status,
        "by_priority": by_priority,
        "by_issue_type": by_type,
        "total_dependencies": total_deps,
        "claimed": claimed,
        "ready_now": sum(
            1
            for i in issues
            if i["status"] == "open"
            and not i.get("metadata", {}).get("claimed_by")
        ),
    }
