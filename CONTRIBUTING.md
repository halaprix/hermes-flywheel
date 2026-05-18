# Contributing to Hermes Flywheel

Thank you for your interest in contributing to hermes-flywheel. This document covers everything you need to know to set up a development environment, understand the code conventions, and get your changes merged.

> **⚠️ Maintenance Mode Notice**
>
> The Python MCP server (`hermes-flywheel`) is in **maintenance mode** — bug fixes and critical security patches only. New feature development has moved to the reference implementation: the [`bd` CLI from `gastownhall/beads`](https://github.com/gastownhall/beads).
>
> If your contribution adds new MCP server features, please consider whether it belongs in the `bd` CLI instead. Open an issue first to discuss before investing time in a large PR.

---

## Development Setup

### Prerequisites

- Python 3.11 or higher
- `pip` or `uv`
- Git

### Clone and Install

```bash
git clone https://github.com/halaprix/hermes-flywheel.git
cd hermes-flywheel
pip install -e .
```

This installs the package in editable mode with all dependencies, including the `mcp` library required by the server.

### Running the MCP Server

```bash
# Via the installed entry point
hermes-flywheel

# Or directly via Python
python -m hermes_flywheel.server
```

### Wiring into Hermes Agent

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  flywheel:
    command: python
    args: ["-m", "hermes_flywheel.server"]
    env:
      PYTHONPATH: "/path/to/hermes-flywheel"
      FLYWHEEL_PROJECT_ROOT: "/path/to/your/project"
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FLYWHEEL_PROJECT_ROOT` | Root directory for `.beads/` storage | `cwd` |
| `FLYWHEEL_DASHBOARD_PORT` | Port for the built-in bead dashboard | `9120` |

---

## Running Tests

There are currently no automated tests in this repository. The project uses manual validation via the bead dashboard and Hermes Agent integration testing.

**Before submitting any change**, manually verify:

```bash
# 1. Syntax check
python -m py_compile hermes_flywheel/server.py
python -m py_compile hermes_flywheel/graph.py
python -m py_compile hermes_flywheel/storage.py
python -m py_compile hermes_flywheel/formulas.py
python -m py_compile dashboard/server.py

# 2. Import check (canary for circular deps)
python -c "from hermes_flywheel.server import mcp"

# 3. Dashboard renders without error
python -c "from dashboard.server import make_page; print('OK')"
```

---

## Code Style

Python code in this project follows a minimal, pragmatic style:

- **No automated linter** (no ruff, no pre-commit, no flake8 config in the repo)
- Write clean, readable Python 3.11+ code
- Use `async`/`await` for all MCP tool handlers
- Use type hints where they aid readability
- One class per module is fine — don't over-structure
- Storage writes must be atomic (`os.replace()` pattern used in `storage.py`)

When in doubt, look at the existing code in `hermes_flywheel/` and follow the patterns you see there.

---

## Branching and Pull Request Process

### 1. Fork and Clone

```bash
git clone https://github.com/halaprix/hermes-flywheel.git
git remote add upstream https://github.com/halaprix/hermes-flywheel.git
```

### 2. Create a Feature Branch

```bash
git checkout -b feat/your-feature-name
```

Branch naming convention: `feat/`, `fix/`, `chore/`, `docs/` prefixes are preferred.

### 3. Make Your Changes

- Keep commits small and focused
- Write a clear commit message: `fix: resolve claim race in beads_update`
- Do not commit to `main`

### 4. Run Pre-Validation

```bash
# Verify no syntax errors
python -m py_compile hermes_flywheel/*.py dashboard/*.py

# Verify imports resolve
python -c "from hermes_flywheel.server import mcp"
```

### 5. Open a Pull Request

- **Title**: Clear and descriptive (`fix: handle missing dependencies in beads_triage`)
- **Description**: What does it change and why?
- **Link any related issues**
- Fill out the PR template if one exists

### 6. Review Criteria

Maintainers will review for:
- Correctness (does it work as intended?)
- Safety (no data loss, no security regressions)
- Clarity (can others understand and maintain it?)
- Fit with maintenance mode direction (new features may be redirected to `bd` CLI)

---

## Important CLA Reminder

> This repository is **public** on GitHub (`github.com/halaprix/hermes-flywheel`).

**Never leak sensitive information in any commit, PR, or issue:**

- ❌ IP addresses (including Tailscale `100.x` addresses)
- ❌ `/home/pkl` or any home directory paths
- ❌ API keys, tokens, secrets, credentials
- ❌ Machine IDs, hostnames
- ❌ Personal identifiers beyond `halaprix` as GitHub username

Check both **staged content** (`git diff --cached`) **and git history** (`git log`) before pushing. When in doubt, run:

```bash
git diff --cached
git log --oneline -5
```

---

## Semantic Versioning

This project follows **Semantic Versioning 2.0.0** (`MAJOR.MINOR.PATCH`).

### Version Bump Rules

| Change Type | Bump | Rationale |
|-------------|------|-----------|
| Bug fix (no API change) | **PATCH** (0.5.0 → 0.5.1) | Backward-compatible fixes |
| New feature, backward-compatible | **MINOR** (0.5.0 → 0.6.0) | Added functionality, no breakage |
| Breaking API change | **MAJOR** (0.5.0 → 1.0.0) | Removes or renames tools, changes data format |

### Pre-1.0.0 Rules

While in `0.x.y`:
- Anything CAN change — the API is unstable
- **PATCH bumps for fixes** (0.5.0 → 0.5.1)
- **MINOR bumps for any new behavior** (0.5.0 → 0.6.0), even if technically breaking
- **MAJOR bump to 1.0.0** when the CLI interface stabilizes and we commit to backward compatibility

### Commit Convention

All commits follow [Conventional Commits](https://www.conventionalcommits.org/):
```
feat: add bd triage command
fix: handle missing dependencies in graph
docs: update installation instructions
chore: bump version to 0.5.0
```

### Version Files

- `pyproject.toml` — Python package version
- `SKILL.md` frontmatter `version:` field — flywheel methodology version
- `bd --version` — gastownhall/beads CLI version (handled upstream)

---

## Where to Contribute

Given the maintenance mode status, the most valuable contributions are:

1. **Bug fixes** — broken tools, edge cases in graph algorithms, storage corruption
2. **Security patches** — any vulnerability in the MCP server
3. **Documentation** — this file, `README.md`, `SKILL.md` improvements
4. **Dashboard improvements** — the dashboard is purely decorative and open for enhancements
5. **Migration help** — anything that helps users transition to the `bd` CLI

For new features beyond bug fixes, please open an issue first to confirm the direction before writing code.

---

## Project Structure

```
hermes-flywheel/
├── hermes_flywheel/        # MCP server implementation
│   ├── server.py          # FastMCP server + tool definitions
│   ├── storage.py          # JSONL bead storage (atomic writes)
│   ├── graph.py            # PageRank, critical path, betweenness
│   └── formulas.py         # TOML formula parsing + pouring
├── dashboard/
│   └── server.py          # Standalone bead dashboard (port 9120)
├── pyproject.toml         # hatchling build config
├── README.md               # Project overview + quick start
├── SKILL.md                # Methodology skill for Hermes Agent
└── CONTRIBUTING.md         # This file
```

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License.