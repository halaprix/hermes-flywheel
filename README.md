# Hermes Flywheel

<p align="center">
  <pre>
   ██╗  ██╗███████╗██████╗ ███╗   ███╗███████╗███████╗    ███████╗██╗  ██╗   ██╗██╗    ██╗██████╗ ██╗  ██╗███████╗███████╗██╗
   ██║  ██║██╔════╝██╔══██╗████╗ ████║██╔════╝██╔════╝    ██╔════╝██║  ╚██╗ ██╔╝██║    ██║██╔══██╗██║  ██║██╔════╝██╔════╝██║
   ███████║█████╗  ██████╔╝██╔████╔██║█████╗  ███████╗    █████╗  ██║   ╚████╔╝ ██║ █╗ ██║██████╔╝███████║█████╗  █████╗  ██║
   ██╔══██║██╔══╝  ██╔══██╗██║╚██╔╝██║██╔══╝  ╚════██║    ██╔══╝  ██║    ╚██╔╝  ██║███╗██║██╔══██╗██╔══██║██╔══╝  ██╔══╝  ╚═╝
   ██║  ██║███████╗██║  ██║██║ ╚═╝ ██║███████╗███████║    ██║     ███████╗██║   ╚███╔███╔╝██║  ██║██║  ██║███████╗███████╗██╗
   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚══════╝    ╚═╝     ╚══════╝╚═╝    ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝╚═╝
  </pre>
  <em>A beads task-graph MCP server for <a href="https://github.com/NousResearch/hermes-agent">Hermes Agent</a></em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/hermes-agent-mcp-purple" alt="Hermes MCP">
</p>

---

Plan-first, bead-driven development for Hermes Agent. Reverse-engineered from Jeff Emanuel's [Agentic Coding Flywheel](https://agent-flywheel.com/) — the same methodology that shipped 20,000+ lines of production Go in a single day with coordinated AI swarms.

Tracks work as **beads** (self-contained tasks with dependency graphs) in a flat JSONL file. A pure-Python graph engine runs PageRank, critical path, and betweenness centrality so agents know exactly which bead to work on next. No Dolt, no SQLite, no daemons. No VPS required.

```
Plan → Beads → Triage → Execute → Land → Repeat
```

## Why a flywheel?

Because flat TODO lists don't know what blocks what. A bead graph tells you:

- **Which task unblocks the most downstream work** (PageRank)
- **The longest dependency chain** determining project minimum time (critical path)
- **Where your bottleneck sits** (betweenness centrality)
- **What you can safely work on** right now without stepping on anyone's toes

An agent with a flat list picks whatever looks interesting. An agent with a bead graph picks the mathematically optimal next step.

## Install

### Quick (uvx — zero install)

```bash
uvx hermes-flywheel
```

### From source

```bash
git clone https://github.com/halaprix/hermes-flywheel.git
cd hermes-flywheel
pip install .
```

### Docker

```bash
docker run -v $(pwd):/workspace -e FLYWHEEL_PROJECT_ROOT=/workspace ghcr.io/halaprix/hermes-flywheel
```

## Wire into Hermes

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  flywheel:
    command: uvx
    args: ["hermes-flywheel"]
    env:
      FLYWHEEL_PROJECT_ROOT: "/path/to/your/project"
```

Or for a local clone:

```yaml
mcp_servers:
  flywheel:
    command: python
    args: ["-m", "hermes_flywheel.server"]
    env:
      PYTHONPATH: "/path/to/hermes-flywheel"
      FLYWHEEL_PROJECT_ROOT: "/path/to/your/project"
```

Restart Hermes. You should see `mcp_flywheel_beads_*` tools in your session.

## Usage

Load the skill:

```
/skill flywheel
```

The agent now operates in flywheel mode. Planning happens before any code is written. Work is tracked as beads with dependencies. Graph analytics tell the agent what to work on next.

### Quick start — your first beads

```
beads_create(title="Add auth middleware", type="feature", priority=1)
beads_create(title="Add login UI", type="feature", priority=2)
beads_update(bead_id="bd-xxx", dependencies=["bd-yyy"])
beads_ready()          → what can I work on?
beads_triage()         → what should I work on? (robot_next)
beads_update(bead_id="bd-xxx", claim="agent-1")   → mine now
# ... do the work ...
flywheel_land(bead_id="bd-xxx")                   → done, clean exit
```

### Formulas — reusable workflows

Pour a formula into a molecule. The formula expands into a hierarchy of beads with dependencies:

```
formula_install("feature")
formula_pour("feature", {"feature_name": "dark mode"})
```

Built-in formulas: `feature`, `bugfix`, `refactor`, `release`. Define your own in `.beads/formulas/*.formula.toml`.

## Tools

| Tool | What it does |
|------|-------------|
| `beads_create` | Create a bead with deterministic hash ID |
| `beads_update` | Update status, dependencies, claim, metadata |
| `beads_list` | List beads with optional filters |
| `beads_ready` | Beads you can work on right now |
| `beads_triage` | Full graph analytics: robot_next, critical path, bottlenecks, cycles |
| `beads_remember` | Store knowledge in bead metadata (no separate MEMORY.md) |
| `flywheel_land` | Shutdown protocol: close, checklist, follow-ups |
| `flywheel_stats` | Summary statistics |
| `formula_pour` | Expand a formula into a molecule |
| `formula_list` | List available formulas |
| `formula_install` | Install a built-in formula |

## Storage

All beads live in `.beads/issues.jsonl`. One JSON object per line. Git-friendly. Writes use `os.replace()` for atomicity — the file is either old or new, never a half-written mess.

## How it fits into Hermes

The flywheel layers on top, replacing nothing:

- **Planning**: converts plans to beads with graph dependencies
- **Execution**: tells you which beads to delegate, then you spawn subagents with `delegate_task`
- **Memory**: adds structured per-task memory via `beads_remember` — cleaner than `MEMORY.md`
- **Kanban**: different beast. Kanban is for multi-profile work queues. Flywheel is for graph-driven single-project execution

Load `/skill flywheel` when you want graph intelligence. Don't load it for quick file edits.

## Credits

Methodology: [Jeff Emanuel](https://github.com/Dicklesworthstone) (Dicklesworthstone).  
Original beads: [gastownhall/beads](https://github.com/gastownhall/beads).  
Reverse-engineered and adapted for Hermes by [@halaprix](https://github.com/halaprix).

## License

MIT
