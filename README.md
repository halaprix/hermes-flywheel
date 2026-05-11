# Hermes Flywheel

A beads task-graph MCP server for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Reverse-engineered from Jeff Emanuel's [Agentic Coding Flywheel](https://agent-flywheel.com/) methodology — the same system that shipped 20,000+ lines of production Go in a single day with coordinated AI swarms.

This is a **lightweight reimplementation** for people who want the flywheel workflow without renting a 64GB VPS. No Dolt, no SQLite, no daemons. Just Python stdlib and a JSONL file.

## What it does

Tracks work as **beads** — self-contained tasks with dependencies — in a flat JSONL file. A graph engine runs PageRank, critical path, and betweenness centrality on the dependency DAG so agents know which bead to work on next. No more agents guessing what matters.

The core loop:

```
Plan → Beads → Triage → Execute → Land → Repeat
```

Each turn through the loop feeds the next one. Completed sessions produce memory. Memory makes future planning faster. Tools improve because agents use them, complain about them, then help improve them.

## Why not just use a TODO list?

Because flat lists don't know what blocks what. A bead graph tells you:

- **Which task unblocks the most downstream work** (PageRank)
- **The longest dependency chain** determining minimum project time (critical path)
- **Where your bottleneck is** (betweenness centrality)
- **What you can work on right now** without stepping on anyone else's work

An agent working from a flat list picks whatever looks interesting. An agent working from a bead graph picks the mathematically optimal next step.

## Install

```bash
git clone https://github.com/halaprix/hermes-flywheel.git ~/.hermes/mcp_servers/hermes-flywheel
```

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  flywheel:
    command: "python"
    args: ["-m", "hermes_flywheel.server"]
    env:
      FLYWHEEL_PROJECT_ROOT: "/path/to/your/project"  # optional, defaults to cwd
```

Restart Hermes. You should see `mcp_flywheel_beads_*` tools available.

## Load the skill

In your Hermes session:

```
/skill flywheel
```

This loads the methodology workflow. The agent now operates in flywheel mode: plan first, beads second, code last. Remove the skill to go back to normal operation.

## Tools

### beads_create

```python
beads_create(title="Add rate limiting middleware", type="feature", priority=1)
```

Creates a bead with a deterministic hash ID. Priority: 0=critical, 1=high, 2=medium, 3=low, 4=backlog.

### beads_update

```python
beads_update(bead_id="bd-abc12345", dependencies=["bd-def67890"], claim="agent-1")
```

Update status, dependencies, or metadata. Setting `claim` atomically reserves the bead and sets status to `in_progress`. Other agents calling `beads_ready` will not see claimed beads.

### beads_ready

Returns beads you can work on right now: open, all dependencies satisfied, not claimed by anyone else. Sorted by priority.

### beads_triage

The central intelligence. Runs the full graph analytics suite:

- **robot_next**: the single bead with the highest PageRank among ready beads
- **critical_path**: the longest dependency chain determining minimum project time
- **bottlenecks**: top 5 betweenness centrality beads
- **cycles**: any circular dependencies that need breaking
- **ready_beads**: top 10 actionable beads by priority

Call this before every work session.

### beads_remember

```python
beads_remember(bead_id="bd-abc12345", key="gotcha", value="Don't use async here — conflicts with the DB driver")
```

Stores knowledge directly in bead metadata. Avoids separate MEMORY.md files that eat context window space.

### flywheel_land

Shutdown protocol. Call at the end of every session:

```python
flywheel_land(bead_id="bd-abc12345")
```

Marks the bead closed, surfaces any in-progress beads that need follow-up, and returns a checklist: lint, test, commit, push.

## Storage

All beads live in `.beads/issues.jsonl` at your project root. One JSON object per line. Git-friendly format — changes are diffable, merges are straightforward, no binary blobs.

Writes use `os.replace()` for atomicity. If the process dies mid-write, the file is either the old version or the new version, never a half-written mess.

## Graph engine

Pure Python, zero external dependencies. Runs in the RPC child process so it never touches the LLM's context window. The agent gets a pre-computed JSON result: "here's the critical path, here's robot_next." No tokens spent on graph math.

Algorithms implemented:
- PageRank with teleport damping
- Critical path (longest-path DP on DAG)
- Betweenness centrality (Brandes algorithm)
- Kahn's topological sort with cycle detection

## How it fits into Hermes

The flywheel doesn't replace any existing Hermes tool. It layers on top:

- **Planning**: still use `/plan` or the model's reasoning. The flywheel converts plans to beads.
- **Execution**: still use `delegate_task` or terminal. The flywheel tells you which beads to delegate.
- **Memory**: still use `memory` tool. The flywheel adds structured per-task memory via `beads_remember`.
- **Kanban**: different philosophy. Kanban is for multi-profile work queues. Flywheel is for graph-driven single-project execution.

Load the skill when you want graph intelligence. Don't load it when you're doing a quick file edit.

## Context engineering

This matters. Every token you spend on task management is a token you can't spend on code.

- `beads_ready` returns only actionable beads, not the full database
- `beads_triage` computes graph math in Python, not in LLM reasoning
- `beads_remember` stores knowledge in structured metadata instead of verbose markdown
- Closed beads are summarized by title and status, descriptions are dropped
- The agent never sees the raw JSONL file. It only sees filtered, sorted results

## Credits

Methodology by [Jeff Emanuel](https://github.com/Dicklesworthstone) (Dicklesworthstone). The flywheel concept, beads architecture, and three-space reasoning model are his work.

Reverse-engineered and adapted for Hermes Agent by [@halaprix](https://github.com/halaprix).

## License

MIT
