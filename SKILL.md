---
name: flywheel
description: "Use when starting a non-trivial coding task, feature, or project. Plan-first, bead-driven, multi-agent development with graph intelligence. Do NOT use for simple questions, single-file edits, or one-off commands."
version: 1.2.0
author: Hermes Agent + Dicklesworthstone methodology
license: MIT
metadata:
  hermes:
    tags: [workflow, methodology, planning, beads, multi-agent, graph, mcp]
    related_skills: [writing-plans, subagent-driven-development]
requires_toolsets: [terminal, file, delegation]
requires_mcp_servers: [flywheel]
config:
  FLYWHEEL_PROJECT_ROOT:
    description: "Project root directory for .beads storage"
    default: "."
    required: false
  FLYWHEEL_DEFAULT_AGENTS:
    description: "Default number of parallel agents for swarms"
    default: "3"
    required: false
---

# Agent Flywheel — Methodology for Hermes Agent

## Overview

The Agent Flywheel is a plan-first, bead-driven development methodology. It enforces the rule that 85% of time goes into planning and 15% into implementation, because catching an architectural error during planning costs 1×, during bead breakdown costs 5×, and during implementation costs 25×.

Based on Jeff Emanuel's (Dicklesworthstone) Agentic Coding Flywheel — the system that shipped 20,000+ lines of production Go in a single day using coordinated AI agent swarms. Adapted for Hermes Agent as a lightweight MCP server + skill pair.

This skill loads the methodology workflow into the current session. The agent switches to flywheel mode: create a comprehensive plan, decompose into beads with dependency graphs, use graph analytics (PageRank, critical path) to determine execution order, then spawn agents in parallel.

## When to Use

- Starting a new feature, project, or significant refactor
- The task has multiple interdependent components
- You want to coordinate multiple parallel agents on the same codebase
- You need persistent task tracking with dependency awareness across sessions

**Do NOT use for:** simple questions, single-file edits, one-off commands, debugging a single error. The flywheel is intentionally heavyweight — only load it when the complexity justifies the ceremony.

## Prerequisites

The `hermes-flywheel` MCP server must be installed and wired into `~/.hermes/config.yaml`:

```bash
git clone https://github.com/halaprix/hermes-flywheel.git ~/.hermes/mcp_servers/hermes-flywheel
pip install ~/.hermes/mcp_servers/hermes-flywheel
```

Config:

```yaml
mcp_servers:
  flywheel:
    command: python
    args: ["-m", "hermes_flywheel.server"]
    env:
      PYTHONPATH: "/home/user/.hermes/mcp_servers/hermes-flywheel"
      FLYWHEEL_PROJECT_ROOT: "/path/to/project"
```

Restart Hermes after adding. The agent should have `mcp_flywheel_beads_*` tools.

## The Three Reasoning Spaces

| Space | Artifact | Error Cost | What You Decide |
|-------|----------|-----------|-----------------|
| **Plan Space** | Markdown plan (.hermes/plans/) | 1× | Architecture, features, workflows, tradeoffs |
| **Bead Space** | .beads/issues.jsonl | 5× | Task boundaries, execution order, dependencies |
| **Code Space** | Source files + tests | 25× | Actual implementation — plan has constrained most decisions |

Rule: never jump to code without planning. An architectural error caught in Plan Space costs pennies. The same error in Code Space costs dollars.

## The Flywheel Loop

```
USER INTENT
    │
    ▼
┌─────────────────────────────────────────┐
│ PHASE 1: PLAN                           │
│ User creates comprehensive markdown     │
│ plan: architecture, components, data    │
│ flow, API contracts, edge cases,        │
│ testing strategy. Save as PLAN.md.      │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ PHASE 2: PLAN → BEADS                  │
│ Agent decomposes plan into beads: one   │
│ per component, set dependencies,        │
│ priorities (0=critical to 4=backlog).   │
│ Use formula_pour for standard workflows │
│ or manual beads_create for custom plans.│
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ PHASE 3: VALIDATE (beads_triage)        │
│ Check dependency graph for cycles,      │
│ compute critical path, identify         │
│ bottlenecks. Fix graph issues here.     │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ PHASE 4: EXECUTE SWARM                  │
│ Loop: beads_triage → robot_next →       │
│ beads_update(claim="agent-N") →         │
│ implement → self-review →               │
│ beads_update(status="closed") →         │
│ flywheel_land → repeat                  │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ PHASE 5: MEMORY                         │
│ beads_remember(patterns, gotchas, rules)│
│ → next swarm starts smarter             │
└─────────────────────────────────────────┘
                 │
                 └──→ Back to USER INTENT
```

## Bead Operations

All bead operations accept an optional `project_root` parameter for per-project isolation. When omitted, the default project root from `FLYWHEEL_PROJECT_ROOT` env var (or cwd) is used.

```
# Per-project usage:
beads_create(title="Add auth", project_root="/path/to/project")
beads_triage(project_root="/path/to/project")
```

Each project stores its beads in `<project_root>/.beads/issues.jsonl`, which can be committed to the repository alongside the code.

### Creating beads

```
beads_create(title="Implement auth middleware", type="feature", priority=1)
```

Priority: 0=critical (security/data loss), 1=high, 2=medium, 3=low, 4=backlog.

### Setting dependencies

```
beads_update(bead_id="bd-abc12345", dependencies=["bd-def67890"])
```

### Finding work

```
beads_ready()           → what can I work on right now?
beads_triage()          → full analysis with robot_next recommendation
```

### Claiming (prevents duplicate work in multi-agent setups)

```
beads_update(bead_id="bd-abc12345", claim="agent-1")
```

### Storing knowledge

```
beads_remember(bead_id="bd-abc12345", key="gotcha",
               value="Don't use async here — conflicts with the DB driver")
```

### Completing a session

```
flywheel_land(bead_id="bd-abc12345")
```

## Formulas (Reusable Workflows)

Formulas are TOML templates that expand into bead hierarchies. Built-in formulas work immediately without `formula_install` — that command only copies them to disk for customization.

**Available formulas** (all work without install):
- `feature` — Design → Implement → Review → Merge (4 steps)
- `bugfix` — Reproduce → Diagnose → Fix → Verify (4 steps)
- `refactor` — Plan → Add tests → Extract → Verify → Cleanup (5 steps)
- `release` — Bump version → Changelog → Test → Build → Tag → Publish (6 steps)

Pour any formula directly:

```
formula_pour("feature", {"feature_name": "dark mode"})
formula_pour("bugfix", {"bug_description": "null pointer in handler"})
```

Preview without creating beads:

```
formula_pour("refactor", {"target": "auth module"}, dry_run=True)
```

Install to disk (optional — needed only to customize a built-in template):

```
formula_install("feature")
```

Define custom formulas at `.beads/formulas/<name>.formula.toml` or `~/.beads/formulas/<name>.formula.toml`.

## Multi-Agent Swarm Pattern

When multiple beads are ready, spawn parallel agents:

```
1. beads_triage → get ready list
2. For each ready bead:
   delegate_task(goal="implement bead bd-xxx: {title}")
3. Each agent: claim → implement → land
4. After batch: beads_triage → next wave
```

Agents coordinate via bead claims. No merge conflicts because each bead is an isolated work unit on a single git branch.

## Dashboard

A live bead viewer ships with the MCP server at `dashboard/server.py`. Serves on `:9120` alongside the Hermes dashboard on `:9119`. Three views:

- **📋 Table** — all beads sorted by priority with color-coded status badges and 🐙 claim owner
- **🔗 Dependency Graph** — full DAG visualization: topological sort into layers (columns), SVG bezier arrows between dependent nodes, color-coded by status. Handles cycles gracefully (orphaned nodes go to layer 0). Scrollable canvas for large graphs.
- **🔧 Debug** — raw JSONL dump, pretty-printed

Auto-refreshes every 5 seconds. Zero external dependencies — pure stdlib `http.server` + embedded HTML/CSS/JS.

**Per-project support:** Dropdown selector switches between projects. URL query parameter `?project=/path` deep-links to a specific project. Projects are auto-discovered by scanning `--scan-dirs` for `.beads/issues.jsonl` files.

```bash
systemctl --user enable --now flywheel-dashboard
# → http://localhost:9120
# → http://localhost:9120/?project=/home/pkl/workspace/my-project
```

**⚠️ Bind address:** The dashboard binds to `0.0.0.0` so it's reachable via Tailscale. If you change it to `127.0.0.1`, it will only work locally — Tailscale clients won't connect. Always verify with `ss -tlnp | grep 9120` that it shows `0.0.0.0:9120`, not `127.0.0.1:9120`.

**⚠️ Browser cache:** After dashboard updates, the browser may serve a cached HTML version with stale JavaScript (e.g. old `renderTree` instead of new `renderGraph`). Tell users to hard-refresh (`Ctrl+Shift+R`) or open DevTools → disable cache. First symptom: "I see the dashboard but no beads" — the API works, the JS is stale.

### Sidecar mode

The MCP server can auto-start the dashboard as a background thread. Set `FLYWHEEL_DASHBOARD_PORT=9120` in your MCP server config and the dashboard starts alongside the MCP process — no separate systemd unit needed.

## Common Pitfalls

1. **Dashboard TDZ error: `const REFRESH` declared after usage.** If the dashboard shows a blank page and browser console has `Uncaught ReferenceError: Cannot access 'REFRESH' before initialization`, the template in `server.py` has `const REFRESH = REFRESH_SECS;` at the **bottom** of the `<script>` block (after `fetchData()`), but `let countdown = REFRESH;` at the **top** (line 147). The `REFRESH` constant is in the Temporal Dead Zone when used. Fix: move `const REFRESH = REFRESH_SECS;` to the first line after `<script>` (before `let data = []`), and remove the duplicate from the bottom. Then restart the dashboard (`systemctl --user restart flywheel-dashboard` or `pkill -f "dashboard/server.py"`).

2. **Skipping planning and jumping to beads.** Planning catches the 1× errors. Without it, you discover architecture flaws during implementation (25× cost).

3. **Creating beads without dependencies.** The graph IS the intelligence. A bead without dependencies is just a TODO item.

4. **Working on beads other than robot_next.** The graph math picks the optimal bead. Override only when you have a specific reason (e.g., a quick fix that unblocks a human).

5. **Leaving beads claimed across sessions.** Always `flywheel_land` at session end. Stranded claims block other agents.

6. **Using interactive editors (vim/nano).** They hang the RPC thread. Use non-interactive commands or `write_file`/`patch` tools.

7. **Not loading the skill explicitly.** The flywheel is opt-in. Without `/skill flywheel`, the agent won't know the methodology.

8. **Leaking machine identifiers in public repos.** The hermes-flywheel repo is public (github.com/halaprix/hermes-flywheel). Never commit IP addresses (even Tailscale 100.x), `/home/pkl` paths, API keys, tokens, or any machine-identifying data. Only `halaprix` as GitHub username is acceptable. Check both staged content AND git history before pushing.

## Known Limitations

### Plan → Beads conversion is manual

In the original Dicklesworthstone flywheel, the user writes a `PLAN.md` and the agent automatically decomposes it into beads with dependency structure. Our tooling currently requires manual bead creation — either one-by-one `beads_create` calls or pouring formulas (`formula_pour`). There is **no `plan_to_beads()` tool** that parses a markdown plan and generates the bead graph.

**Workaround:** Use formulas for standardized workflows (feature, bugfix, refactor, release). For custom plans, create beads manually and model dependencies explicitly. This is the #1 missing feature — a tool that reads markdown section headers, detects dependency language ("requires X", "after Y is done"), and builds the graph automatically.

## Verification Checklist

- [ ] MCP server installed and wired in `config.yaml`
- [ ] Hermes restarted — `mcp_flywheel_beads_*` tools visible
- [ ] PLAN.md created covering architecture, data flow, edge cases, testing
- [ ] All plan sections mapped to beads (zero orphaned sections)
- [ ] beads_triage reports zero cycles
- [ ] Each agent session ends with `flywheel_land`
- [ ] Git commits include bead ID: `fix: resolve auth race condition (bd-abc123)`

## Developer Notes

For contributors extending or debugging the MCP server, see:
- [references/mcp-server-dev-notes.md](references/mcp-server-dev-notes.md) — package structure, FastMCP patterns, TOML escaping, Hermes wiring, and testing conventions.
- [references/dashboard-dag-graph.md](references/dashboard-dag-graph.md) — DAG graph visualization algorithm, layout math, edge cases.
- [references/git-push-with-pat.md](references/git-push-with-pat.md) — pushing to GitHub with fine-grained PAT on this system (GIT_ASKPASS workaround).
