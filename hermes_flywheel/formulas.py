"""Formula engine — TOML workflow templates that expand into bead hierarchies.

Formulas are reusable, parameterized workflow templates stored in:
  .beads/formulas/<name>.formula.toml  (project)
  ~/.beads/formulas/<name>.formula.toml (user)

A formula poured into a molecule creates a parent bead + child beads
with full dependency structure from the template's step definitions.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from . import storage as _store


# ── Formula loading ──────────────────────────────────────────────────────

def _find_formula(
    name: str,
    project_root: str | None = None,
) -> Path | None:
    """Search for a formula file by name in project, user dirs, and built-ins."""
    candidates = [
        Path(project_root) / ".beads" / "formulas" / f"{name}.formula.toml"
        if project_root
        else Path(".beads") / "formulas" / f"{name}.formula.toml",
        Path.home() / ".beads" / "formulas" / f"{name}.formula.toml",
    ]
    for path in candidates:
        if path.exists():
            return path
    # Check built-in formulas (in-memory, not on disk)
    if name in BUILTIN_FORMULAS:
        return None  # Signal: use built-in, not file
    return None


def _load_formula(path: Path) -> dict[str, Any]:
    """Load a formula from a TOML file. Falls back to JSON."""
    raw = path.read_text(encoding="utf-8")

    if path.suffix == ".toml":
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        return tomllib.loads(raw)

    # JSON fallback
    import json
    return json.loads(raw)


def _validate_vars(formula: dict[str, Any], vars_provided: dict[str, str]) -> list[str]:
    """Check required vars are present and match patterns. Returns list of errors."""
    errors: list[str] = []
    for name, spec in formula.get("vars", {}).items():
        if spec.get("required") and name not in vars_provided:
            errors.append(f"Missing required variable: {name}")
        if name in vars_provided:
            val = vars_provided[name]
            if "pattern" in spec and not re.match(spec["pattern"], val):
                errors.append(
                    f"Variable {name}='{val}' does not match pattern {spec['pattern']}"
                )
            if "enum" in spec and val not in spec["enum"]:
                errors.append(
                    f"Variable {name}='{val}' not in allowed values: {spec['enum']}"
                )
    return errors


def _interpolate(text: str, vars_map: dict[str, str]) -> str:
    """Replace {{var}} placeholders with values."""
    def _replace(m: re.Match) -> str:
        key = m.group(1)
        return vars_map.get(key, m.group(0))
    return re.sub(r"\{\{(\w+)\}\}", _replace, text)


# ── Pouring ──────────────────────────────────────────────────────────────

def formula_pour(
    formula_name: str,
    vars: dict[str, str] | None = None,
    project_root: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Pour a formula into a molecule — creates parent + child beads.

    Returns the parent bead and list of child beads created.
    With dry_run=True, previews without creating.
    """
    vars = vars or {}

    # Load formula
    path = _find_formula(formula_name, project_root)
    if path is None and formula_name not in BUILTIN_FORMULAS:
        return {"error": f"Formula '{formula_name}' not found. Install with formula_install('{formula_name}') or create .beads/formulas/{formula_name}.formula.toml"}
    
    if path:
        formula = _load_formula(path)
    else:
        # Built-in formula — load from TOML string
        import tomllib
        formula = tomllib.loads(BUILTIN_FORMULAS[formula_name])

    # Validate vars
    errors = _validate_vars(formula, vars)
    if errors:
        return {"error": "Variable validation failed", "errors": errors}

    # Fill in defaults
    vars_full: dict[str, str] = {}
    for name, spec in formula.get("vars", {}).items():
        vars_full[name] = vars.get(name, spec.get("default", ""))
    # Also include any extra vars provided
    for k, v in vars.items():
        vars_full.setdefault(k, v)

    # Create parent molecule bead
    title = _interpolate(formula.get("formula", formula_name), vars_full)
    description = formula.get("description", "")

    if dry_run:
        steps_preview = []
        for i, step in enumerate(formula.get("steps", [])):
            step_title = _interpolate(step.get("title", step.get("id", f"step-{i}")), vars_full)
            step_deps = step.get("needs", [])
            steps_preview.append({
                "index": i + 1,
                "id": step.get("id", f"step-{i}"),
                "title": step_title,
                "needs": step_deps,
            "issue_type": step.get("issue_type", "task"),
            })
        return {
            "dry_run": True,
            "formula": formula_name,
            "parent_title": title,
            "parent_description": description,
            "step_count": len(formula.get("steps", [])),
            "steps": steps_preview,
        }

    # Create parent bead
    parent = _store.beads_create(
        title=title,
        issue_type=formula.get("issue_type", "epic"),
        priority=formula.get("priority", 1),
        description=description,
        project_root=project_root,
    )
    if "error" in parent:
        return parent

    parent_id = parent["id"]

    # Create child beads with dependencies
    children: list[dict[str, Any]] = []
    step_id_to_bead: dict[str, str] = {}

    for step in formula.get("steps", []):
        step_id = step.get("id", f"step-{len(children)}")
        step_title = _interpolate(step.get("title", step_id), vars_full)
        step_type = step.get("issue_type", "task")
        step_priority = step.get("priority", formula.get("priority", 2))
        step_desc = step.get("description", "")

        # Compute dependencies: step "needs" + unresolved external deps
        deps: list[str] = [parent_id]  # child always depends on parent
        for need_id in step.get("needs", []):
            if need_id in step_id_to_bead:
                deps.append(step_id_to_bead[need_id])
            # External needs (not in this formula) are skipped — they're advisory

        child = _store.beads_create(
            title=step_title,
            issue_type=step_type,
            priority=step_priority,
            description=step_desc,
            project_root=project_root,
        )
        if "error" in child:
            children.append({"error": child["error"], "step": step_id})
            continue

        child_id = child["id"]
        step_id_to_bead[step_id] = child_id

        # Set dependencies
        _store.beads_update(
            bead_id=child_id,
            dependencies=deps,
            project_root=project_root,
        )

        # Store step metadata
        if step.get("gate"):
            _store.beads_update(
                bead_id=child_id,
                metadata={"formula_gate": step["gate"]},
                project_root=project_root,
            )

        if step.get("on_complete"):
            _store.beads_update(
                bead_id=child_id,
                metadata={"on_complete": step["on_complete"]},
                project_root=project_root,
            )

        children.append({
            "step_id": step_id,
            "bead_id": child_id,
            "title": step_title,
            "issue_type": step_type,
            "dependencies": deps,
        })

    return {
        "parent": parent,
        "formula": formula_name,
        "child_count": len(children),
        "children": children,
    }


# ── Formula management ───────────────────────────────────────────────────

def formula_list(project_root: str | None = None) -> dict[str, Any]:
    """List available formulas from project and user directories."""
    formulas: list[dict[str, Any]] = []
    seen: set[str] = set()

    for base in [
        Path(project_root) / ".beads" / "formulas" if project_root else Path(".beads") / "formulas",
        Path.home() / ".beads" / "formulas",
    ]:
        if not base.exists():
            continue
        for fpath in sorted(base.glob("*.formula.*")):
            name = fpath.stem.split(".formula")[0]
            if name in seen:
                continue
            seen.add(name)
            try:
                formula = _load_formula(fpath)
                formulas.append({
                    "name": name,
                    "title": formula.get("formula", name),
                    "description": formula.get("description", ""),
                    "type": formula.get("type", "workflow"),
                    "step_count": len(formula.get("steps", [])),
                    "location": str(fpath),
                })
            except Exception:
                formulas.append({
                    "name": name,
                    "title": name,
                    "description": "(parse error)",
                    "type": "unknown",
                    "step_count": 0,
                    "location": str(fpath),
                })

    # Add built-in formulas not yet on disk
    import tomllib
    for name, raw in BUILTIN_FORMULAS.items():
        if name in seen:
            continue
        seen.add(name)
        try:
            formula = tomllib.loads(raw)
            formulas.append({
                "name": name,
                "title": formula.get("formula", name),
                "description": formula.get("description", ""),
                "type": formula.get("type", "workflow"),
                "step_count": len(formula.get("steps", [])),
                "location": "(built-in — install with formula_install to customize)",
            })
        except Exception:
            pass

    return {"count": len(formulas), "formulas": formulas}


# ── Built-in formulas ────────────────────────────────────────────────────

BUILTIN_FORMULAS: dict[str, str] = {
    "feature": """# Standard feature development workflow
formula = "feature"
description = "Design → Implement → Review → Merge workflow for a new feature"
version = 1
type = "epic"

[vars.feature_name]
description = "Name of the feature"
required = true

[[steps]]
id = "design"
title = "Design {{feature_name}}"
description = "Create design document with architecture, data flow, and API contracts"
type = "task"

[[steps]]
id = "implement"
title = "Implement {{feature_name}}"
description = "Write the code with tests"
needs = ["design"]

[[steps]]
id = "review"
title = "Code review {{feature_name}}"
description = "Self-review then cross-review with another agent"
needs = ["implement"]
type = "task"

[[steps]]
id = "merge"
title = "Merge {{feature_name}} to main"
description = "Land the plane: lint, test, rebase, push"
needs = ["review"]
""",

    "bugfix": """# Standard bug fix workflow
formula = "bugfix"
description = "Reproduce → Diagnose → Fix → Verify workflow"
version = 1
type = "epic"
priority = 0

[vars.bug_description]
description = "Description of the bug"
required = true

[[steps]]
id = "reproduce"
title = "Reproduce: {{bug_description}}"
description = "Write a failing test that reproduces the bug"
type = "task"

[[steps]]
id = "diagnose"
title = "Diagnose root cause"
description = "Find the root cause using debugging tools"
needs = ["reproduce"]
type = "task"

[[steps]]
id = "fix"
title = "Fix: {{bug_description}}"
description = "Implement the fix"
needs = ["diagnose"]

[[steps]]
id = "verify"
title = "Verify fix"
description = "Run the repro test, full test suite, and manual verification"
needs = ["fix"]
""",

    "refactor": """# Refactoring workflow
formula = "refactor"
description = "Plan → Extract → Verify → Cleanup refactoring workflow"
version = 1
type = "epic"

[vars.target]
description = "What is being refactored"
required = true

[[steps]]
id = "plan"
title = "Plan refactor of {{target}}"
description = "Identify extraction boundaries, new abstractions, and migration path"

[[steps]]
id = "add-tests"
title = "Add characterization tests"
description = "Tests that capture current behavior before changes"
needs = ["plan"]

[[steps]]
id = "extract"
title = "Extract {{target}}"
description = "Perform the actual refactoring"
needs = ["add-tests"]

[[steps]]
id = "verify"
title = "Verify behavior preserved"
description = "Run all tests, compare outputs"
needs = ["extract"]

[[steps]]
id = "cleanup"
title = "Cleanup old code"
description = "Remove deprecated code, update imports"
needs = ["verify"]
""",

    "release": """# Standard release workflow
formula = "release"
description = "Version bump → Changelog → Test → Build → Tag → Publish"
version = 1
type = "epic"
priority = 0

[vars.version]
description = "Release version number"
required = true

[[steps]]
id = "bump-version"
title = "Bump version to v{{version}}"
description = "Update version strings in code and config files"

[[steps]]
id = "changelog"
title = "Update CHANGELOG for v{{version}}"
description = "Document all changes since last release"
needs = ["bump-version"]

[[steps]]
id = "test"
title = "Run full test suite"
description = "All unit, integration, and e2e tests must pass"
needs = ["changelog"]

[[steps]]
id = "build"
title = "Build release artifacts"
description = "Build binaries, packages, or containers"
needs = ["test"]

[[steps]]
id = "tag"
title = "Create git tag v{{version}}"
description = "Tag the release commit"
needs = ["build"]

[[steps]]
id = "publish"
title = "Publish release v{{version}}"
description = "Push tag, publish packages, update docs"
needs = ["tag"]
""",
}


def formula_install_builtin(
    name: str,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Install a built-in formula into the project."""
    if name not in BUILTIN_FORMULAS:
        available = ", ".join(BUILTIN_FORMULAS)
        return {"error": f"Unknown built-in formula '{name}'. Available: {available}"}

    formulas_dir = (
        Path(project_root) / ".beads" / "formulas"
        if project_root
        else Path(".beads") / "formulas"
    )
    formulas_dir.mkdir(parents=True, exist_ok=True)

    target = formulas_dir / f"{name}.formula.toml"
    if target.exists():
        return {"error": f"Formula '{name}' already exists at {target}. Delete it first to reinstall."}

    target.write_text(BUILTIN_FORMULAS[name], encoding="utf-8")
    return {
        "installed": name,
        "path": str(target),
        "message": f"Formula '{name}' installed. Use formula_pour('{name}', vars={{...}}) to create a molecule.",
    }
