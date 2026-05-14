#!/usr/bin/env python3
"""Flywheel Dashboard — live bead graph viewer for Hermes Agent.

Serves on :9120. Reads .beads/issues.jsonl from multiple projects,
renders table + dependency tree + debug view.
Auto-refreshes every 5 seconds via polling.

Usage: python dashboard.py [--port 9120] [--project-root /path] [--scan-dirs ~/workspace,~]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PROJECT_ROOT = os.environ.get("FLYWHEEL_PROJECT_ROOT", os.path.expanduser("~"))
BEADS_FILE = Path(PROJECT_ROOT) / ".beads" / "issues.jsonl"
REFRESH_SECONDS = 5
SCAN_DIRS: list[str] = [os.path.expanduser("~/workspace"), os.path.expanduser("~")]


def discover_projects(scan_dirs: list[str]) -> list[dict]:
    """Find all .beads/issues.jsonl files under scan directories (depth 3)."""
    found: dict[str, dict] = {}
    for scan_dir in scan_dirs:
        base = Path(os.path.expanduser(scan_dir))
        if not base.exists():
            continue
        # Search up to 3 levels deep
        for pattern in ["*/.beads/issues.jsonl", "*/*/.beads/issues.jsonl", "*/*/*/.beads/issues.jsonl"]:
            for p in base.glob(pattern):
                proj_dir = str(p.parent.parent)
                if proj_dir not in found:
                    found[proj_dir] = {
                        "path": proj_dir,
                        "name": Path(proj_dir).name,
                        "beads_file": str(p),
                    }
    # Sort by name
    result = sorted(found.values(), key=lambda x: x["name"])
    # Ensure default project is in list
    default = str(BEADS_FILE.parent.parent)
    if not any(r["path"] == default for r in result):
        result.insert(0, {
            "path": default,
            "name": Path(default).name,
            "beads_file": str(BEADS_FILE),
        })
    return result


def normalize_deps(beads: list[dict]) -> list[dict]:
    """Convert object dependencies to string IDs for frontend compatibility."""
    for b in beads:
        deps = []
        for dep in b.get("dependencies", []):
            if isinstance(dep, dict):
                deps.append(dep.get("id", str(dep)))
            else:
                deps.append(str(dep))
        b["dependencies"] = deps
    return beads


def load_beads(beads_file: Path | None = None) -> list[dict]:
    path = beads_file or BEADS_FILE
    if not path.exists():
        return []
    beads = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                beads.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return normalize_deps(beads)


def beads_json(beads_file: Path | None = None) -> str:
    return json.dumps(load_beads(beads_file), indent=2)


def bead_deps_map(beads: list[dict]) -> dict[str, list[str]]:
    """Build a map of bead_id -> list of beads that depend on it (forward deps)."""
    id_set = {b["id"] for b in beads}
    forward: dict[str, list[str]] = {b["id"]: [] for b in beads}
    for b in beads:
        for dep in b.get("dependencies", []):
            if dep in id_set:
                forward[dep].append(b["id"])
    return forward


STATUS_COLORS = {
    "open": "#6b7280",
    "in_progress": "#3b82f6",
    "blocked": "#ef4444",
    "closed": "#22c55e",
    "deferred": "#f59e0b",
}

PRIORITY_LABELS = {0: "CRIT", 1: "HIGH", 2: "MED", 3: "LOW", 4: "BACK"}

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🌀 Flywheel Dashboard</title>
<style>
/* ============================================
   Cyber-Glassmorphism Design System
   ============================================ */
:root {
  --bg: #0a0b0d;
  --surface: rgba(22,25,29,0.6);
  --surface-hover: rgba(255,255,255,0.05);
  --border: rgba(255,255,255,0.06);
  --border-strong: rgba(255,255,255,0.10);
  --text: #e2e4e9;
  --text-muted: #6b7280;
  --accent: #7c3aed;
  --accent-secondary: #06b6d4;
  --accent-glow: rgba(124,58,237,0.25);
  --green: #22c55e;
  --blue: #3b82f6;
  --red: #ef4444;
  --amber: #f59e0b;
  --gray: #6b7280;
  --font-body: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', 'SF Mono', 'Cascadia Code', monospace;
  --radius-sm: 6px;
  --radius: 10px;
  --radius-lg: 14px;
  --radius-full: 9999px;
  --shadow-glass: 0 4px 24px rgba(0,0,0,0.4), 0 1px 3px rgba(0,0,0,0.3);
  --transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

* { box-sizing: border-box; margin: 0; padding: 0; }

html { font-size: 14px; }

body {
  font-family: var(--font-body);
  font-size: 1rem;
  line-height: 1.5;
  background: var(--bg);
  color: var(--text);
  padding: 28px 32px;
  min-height: 100vh;
  position: relative;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* Dot-grid overlay */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image: radial-gradient(circle, rgba(255,255,255,0.10) 1px, transparent 1px);
  background-size: 16px 16px;
  pointer-events: none;
  z-index: 0;
}

body::after {
  content: '';
  position: fixed;
  inset: 0;
  background: radial-gradient(ellipse at 30% 10%, rgba(124,58,237,0.06) 0%, transparent 60%),
              radial-gradient(ellipse at 70% 90%, rgba(6,182,212,0.04) 0%, transparent 60%);
  pointer-events: none;
  z-index: 0;
}

/* All content above the dot grid */
body > * { position: relative; z-index: 1; }

/* ============================================
   Header
   ============================================ */
header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 18px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
  gap: 14px;
}

h1 {
  font-size: 1.45rem;
  font-weight: 700;
  letter-spacing: -0.01em;
  display: flex;
  align-items: center;
  gap: 10px;
  background: linear-gradient(135deg, var(--text) 0%, #a78bfa 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

h1 span {
  font-size: 0.85rem;
  color: var(--text-muted);
  font-weight: 400;
  -webkit-text-fill-color: var(--text-muted);
}

/* ============================================
   Stats row — 5 metric glass cards
   ============================================ */
.stats {
  display: flex;
  gap: 12px;
  font-size: 0.85rem;
  flex-wrap: wrap;
}

.stat {
  text-align: center;
  background: var(--surface);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 20px;
  min-width: 78px;
  transition: border-color var(--transition), box-shadow var(--transition);
}

.stat:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-glass);
}

.stat .num {
  font-size: 1.7rem;
  font-weight: 700;
  font-family: var(--font-mono);
  line-height: 1.1;
  letter-spacing: -0.03em;
}

.stat .label {
  color: var(--text-muted);
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-top: 2px;
}

/* ============================================
   Project selector — glass dropdown
   ============================================ */
.project-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  margin-bottom: 16px;
}

.project-bar .proj-label {
  color: var(--text-muted);
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.project-bar select {
  background: var(--surface);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 32px 8px 14px;
  font-size: 0.85rem;
  font-family: var(--font-body);
  cursor: pointer;
  min-width: 240px;
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' fill='%236b7280'%3E%3Cpath d='M1 1l5 5 5-5'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 12px center;
  transition: border-color var(--transition), box-shadow var(--transition);
}

.project-bar select:hover {
  border-color: var(--border-strong);
}

.project-bar select:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}

/* ============================================
   Tabs with violet active state
   ============================================ */
.tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 18px;
  align-items: center;
}

.tab {
  padding: 8px 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
  cursor: pointer;
  background: rgba(22,25,29,0.35);
  color: var(--text-muted);
  font-size: 0.85rem;
  font-weight: 500;
  transition: all var(--transition);
  user-select: none;
  position: relative;
}

.tab:hover {
  color: var(--text);
  border-color: var(--border-strong);
  background: rgba(22,25,29,0.55);
}

.tab.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
  box-shadow: 0 0 16px var(--accent-glow);
  font-weight: 600;
}

/* ============================================
   Refresh indicator
   ============================================ */
.refresh {
  color: var(--text-muted);
  font-size: 0.8rem;
  font-family: var(--font-mono);
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 8px;
}

.spark {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent-secondary);
  box-shadow: 0 0 8px rgba(6,182,212,0.6);
  animation: sparkPulse 2s ease-in-out infinite;
}

@keyframes sparkPulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.85); }
}

/* ============================================
   Views
   ============================================ */
.view { display: none; }
.view.active { display: block; }

/* ============================================
   Table — glass card with subtle rows
   ============================================ */
#table {
  background: var(--surface);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: var(--shadow-glass);
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

thead {
  background: rgba(255,255,255,0.03);
  border-bottom: 1px solid var(--border-strong);
}

th {
  text-align: left;
  padding: 12px 14px;
  color: var(--text-muted);
  font-weight: 600;
  text-transform: uppercase;
  font-size: 0.68rem;
  letter-spacing: 0.08em;
}

td {
  padding: 11px 14px;
  border-bottom: 1px solid rgba(255,255,255,0.035);
  vertical-align: middle;
}

tbody tr {
  transition: background var(--transition);
}

tbody tr:hover {
  background: var(--surface-hover);
}

tbody tr:last-child td {
  border-bottom: none;
}

/* ============================================
   Badges — pill-shaped with inner glow
   ============================================ */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  box-shadow: inset 0 1px 2px rgba(255,255,255,0.08);
  line-height: 1.4;
}

.badge-open             { background: rgba(107,114,128,0.18);  color: #6b7280; }
.badge-in_progress      { background: rgba(59,130,246,0.18);   color: #3b82f6; }
.badge-blocked          { background: rgba(239,68,68,0.18);    color: #ef4444; }
.badge-closed           { background: rgba(34,197,94,0.18);    color: #22c55e; }
.badge-deferred         { background: rgba(245,158,11,0.18);   color: #f59e0b; }
.badge-crit             { background: rgba(239,68,68,0.18);    color: #ef4444; }
.badge-high             { background: rgba(245,158,11,0.18);   color: #f59e0b; }

/* Pulse animation for active / blocked badges */
.badge-in_progress,
.badge-blocked {
  animation: badgePulse 2.2s ease-in-out infinite;
}

@keyframes badgePulse {
  0%, 100% { box-shadow: inset 0 1px 2px rgba(255,255,255,0.08), 0 0 6px currentColor; }
  50%      { box-shadow: inset 0 1px 2px rgba(255,255,255,0.08), 0 0 16px currentColor; }
}

/* ============================================
   Monospace helper
   ============================================ */
.mono {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: var(--text-muted);
}

.claimed {
  color: var(--accent-secondary);
  font-size: 0.78rem;
  font-weight: 500;
}

/* ============================================
   Dependency tags
   ============================================ */
.deps {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}

.dep {
  font-size: 0.7rem;
  font-family: var(--font-mono);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--border);
  color: var(--text-muted);
  transition: border-color var(--transition);
}

.dep:hover {
  border-color: var(--accent);
}

/* ============================================
   Empty state
   ============================================ */
.empty-state {
  padding: 48px 24px;
  text-align: center;
  color: var(--text-muted);
  font-size: 0.9rem;
}

.empty-state::before {
  content: '🌀';
  display: block;
  font-size: 2rem;
  margin-bottom: 12px;
  opacity: 0.5;
}

/* Error state */
.error-state {
  padding: 40px 24px;
  text-align: center;
  color: var(--red);
  font-size: 0.85rem;
  background: rgba(239,68,68,0.06);
  border: 1px solid rgba(239,68,68,0.2);
  border-radius: var(--radius-lg);
}

.error-state::before {
  content: '⚠️';
  display: block;
  font-size: 1.6rem;
  margin-bottom: 8px;
}

/* ============================================
   Graph view — layered glass nodes + SVG arrows
   ============================================ */
.graph-container {
  position: relative;
  overflow: auto;
  padding-bottom: 20px;
  background: var(--surface);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-glass);
}

.graph-svg {
  position: absolute;
  top: 0;
  left: 0;
  pointer-events: none;
  z-index: 1;
}

.gnode {
  position: absolute;
  width: 210px;
  background: var(--surface);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 10px 12px;
  cursor: default;
  z-index: 2;
  font-size: 0.78rem;
  box-shadow: 0 2px 12px rgba(0,0,0,0.3);
  transition: border-color var(--transition), box-shadow var(--transition), transform 0.15s ease;
}

.gnode:hover {
  border-color: var(--accent);
  box-shadow: 0 4px 20px rgba(124,58,237,0.2), 0 2px 12px rgba(0,0,0,0.3);
  z-index: 3;
  transform: translateY(-2px);
}

.gnode-id {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 0.65rem;
  margin-bottom: 3px;
  letter-spacing: 0.02em;
}

.gnode-title {
  color: var(--text);
  font-weight: 600;
  margin-bottom: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 0.8rem;
}

.gnode-meta {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 0.7rem;
}

.gnode-claimed {
  color: var(--accent-secondary);
  font-size: 0.68rem;
  margin-left: auto;
  font-weight: 500;
}

/* ============================================
   Debug view — code editor aesthetic
   ============================================ */
.debug-view {
  background: #0a0a0f;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px;
  overflow: auto;
  max-height: 80vh;
  font-family: var(--font-mono);
  box-shadow: inset 0 1px 3px rgba(0,0,0,0.4);
}

.debug-view pre {
  font-size: 0.75rem;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  color: #c9d1d9;
}

/* Syntax-highlightish key colours inside debug JSON */
.debug-view pre .key   { color: #7c3aed; }
.debug-view pre .str   { color: #22c55e; }
.debug-view pre .num   { color: #06b6d4; }
.debug-view pre .bool  { color: #f59e0b; }
.debug-view pre .null  { color: #6b7280; }

/* ============================================
   Scrollbar styling
   ============================================ */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: rgba(255,255,255,0.08);
  border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
  background: rgba(255,255,255,0.15);
}

::-webkit-scrollbar-corner {
  background: transparent;
}

/* ============================================
   Responsive adjustments
   ============================================ */
@media (max-width: 768px) {
  body {
    padding: 16px 12px;
  }

  header {
    flex-direction: column;
    gap: 12px;
  }

  .stats {
    gap: 8px;
  }

  .stat {
    padding: 10px 14px;
    min-width: 56px;
  }

  .stat .num {
    font-size: 1.3rem;
  }

  table { font-size: 0.75rem; }
  th, td { padding: 8px 10px; }

  .project-bar select {
    min-width: 160px;
  }

  .gnode {
    width: 160px;
    padding: 8px 10px;
  }
}
</style>
</head>
<body>
<header>
  <div>
    <h1>🌀 Flywheel Dashboard <span id="project"></span></h1>
  </div>
  <div class="stats" id="stats"></div>
</header>
<div class="project-bar">
  <span class="proj-label">Project:</span>
  <select id="projectSelect" onchange="onProjectChange(this.value)">
    <option value="">Loading...</option>
  </select>
</div>
<div class="tabs">
  <div class="tab active" onclick="switchTab('table')">📋 Table</div>
  <div class="tab" onclick="switchTab('tree')">🔗 Dependency Graph</div>
  <div class="tab" onclick="switchTab('debug')">🔧 Debug</div>
  <div class="refresh" style="margin-left:auto">
    <span class="spark"></span>
    <span id="countdown">REFRESH_SECS</span>s
  </div>
</div>
<div id="table" class="view active"></div>
<div id="tree" class="view"></div>
<div id="debug" class="view"></div>

<script>
const REFRESH = REFRESH_SECS;
let data = [];
let depsMap = {};
let countdown = REFRESH;
let currentProject = '';
let projects = [];

// URL query param support
function getQueryParam(name) {
  const url = new URL(window.location);
  return url.searchParams.get(name);
}
function setQueryParam(name, value) {
  const url = new URL(window.location);
  if (value) url.searchParams.set(name, value);
  else url.searchParams.delete(name);
  window.history.replaceState({}, '', url);
}

async function fetchProjects() {
  const r = await fetch('/api/projects');
  projects = await r.json();
  const sel = document.getElementById('projectSelect');
  sel.innerHTML = projects.map(p => '<option value="' + escAttr(p.path) + '">' + esc(p.name) + ' (' + esc(p.path) + ')</option>').join('');

  // Select project from URL param or first
  const urlProj = getQueryParam('project');
  if (urlProj && projects.find(p => p.path === urlProj)) {
    currentProject = urlProj;
  } else {
    currentProject = projects[0]?.path || '';
    if (currentProject) setQueryParam('project', currentProject);
  }
  sel.value = currentProject;
}

async function fetchData() {
  try {
    const r = await fetch('/api/beads?project=' + encodeURIComponent(currentProject));
    const d = await r.json();
    data = d.beads || [];
    depsMap = d.dep_map || {};
    document.getElementById('project').textContent = '\u2014 ' + (d.project_name || '');
    renderStats(d.stats);
  } catch(e) {
    data = [];
    depsMap = {};
    document.getElementById('project').textContent = '\u2014 error';
    renderStats(null);
  }
  renderTable();
  renderGraph();
  renderDebug();
  countdown = REFRESH;
}

function onProjectChange(value) {
  currentProject = value;
  setQueryParam('project', currentProject);
  fetchData();
}

function renderStats(stats) {
  if (!stats) {
    document.getElementById('stats').innerHTML = '<div class="stat error-state" style="padding:10px 14px;min-width:auto">⚠ Failed</div>';
    return;
  }
  const {total, closed, in_progress, open, blocked, claimed} = stats;
  document.getElementById('stats').innerHTML =
    '<div class="stat"><div class="num">' + (total||0) + '</div><div class="label">Total</div></div>' +
    '<div class="stat"><div class="num" style="color:var(--green)">' + (closed||0) + '</div><div class="label">Closed</div></div>' +
    '<div class="stat"><div class="num" style="color:var(--blue)">' + (in_progress||0) + '</div><div class="label">Active</div></div>' +
    '<div class="stat"><div class="num" style="color:var(--red)">' + (blocked||0) + '</div><div class="label">Blocked</div></div>' +
    '<div class="stat"><div class="num" style="color:var(--accent)">' + (claimed||0) + '</div><div class="label">Claimed</div></div>';
}

function badgeClass(s) { return 'badge badge-' + (s||'open'); }
function prioLabel(p) { return ['CRIT','HIGH','MED','LOW','BACK'][p]||'MED'; }
function prioClass(p) { return p===0?'badge-crit':p===1?'badge-high':''; }

function renderTable() {
  const sorted = [...data].sort((a,b) => (a.priority||2) - (b.priority||2));
  let html = '<table><thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Priority</th><th>Dependencies</th><th>Claimed By</th></tr></thead><tbody>';
  for (const b of sorted) {
    const deps = (b.dependencies||[]).map(d => '<span class="dep">' + d.substring(0,12) + '</span>').join('') || '\u2014';
    const claimed = b.metadata?.claimed_by ? '<span class="claimed">\uD83D\uDC19 ' + esc(b.metadata.claimed_by) + '</span>' : '\u2014';
    html += '<tr>\n' +
      '<td class="mono">' + esc(b.id) + '</td>\n' +
      '<td>' + esc(b.title) + '</td>\n' +
      '<td><span class="' + badgeClass(b.status) + '">' + esc(b.status) + '</span></td>\n' +
      '<td><span class="badge ' + prioClass(b.priority) + '">' + prioLabel(b.priority) + '</span></td>\n' +
      '<td><div class="deps">' + deps + '</div></td>\n' +
      '<td>' + claimed + '</td>\n' +
    '</tr>';
  }
  html += '</tbody></table>';
  document.getElementById('table').innerHTML = data.length ? html : '<div class="empty-state">No beads yet. Pour a formula or create one!</div>';
}

function renderGraph() {
  const idSet = new Set(data.map(b=>b.id));
  const children = {};
  for (const b of data) children[b.id] = [];
  for (const b of data) {
    for (const dep of (b.dependencies||[])) {
      if (idSet.has(dep)) children[dep].push(b.id);
    }
  }

  const inDeg = {};
  for (const b of data) inDeg[b.id] = 0;
  for (const b of data) {
    for (const dep of (b.dependencies||[])) {
      if (idSet.has(dep)) inDeg[b.id]++;
    }
  }

  const layer = {};
  const q = [];
  for (const b of data) {
    if (inDeg[b.id] === 0) {
      layer[b.id] = 0;
      q.push(b.id);
    }
  }

  while (q.length > 0) {
    const id = q.shift();
    for (const childId of (children[id]||[])) {
      layer[childId] = Math.max(layer[childId]||0, layer[id] + 1);
      inDeg[childId]--;
      if (inDeg[childId] === 0) q.push(childId);
    }
  }

  for (const b of data) {
    if (layer[b.id] === undefined) layer[b.id] = 0;
  }

  const layers = {};
  let maxLayer = 0;
  for (const b of data) {
    const l = layer[b.id];
    if (!layers[l]) layers[l] = [];
    layers[l].push(b);
    if (l > maxLayer) maxLayer = l;
  }

  const NODE_W = 210;
  const NODE_H = 68;
  const LAYER_GAP = 260;
  const NODE_GAP = 14;
  const PAD = 20;

  const maxNodesInLayer = Math.max(...Object.values(layers).map(arr=>arr.length), 1);
  const totalH = maxNodesInLayer * (NODE_H + NODE_GAP) + PAD * 2;
  const totalW = (maxLayer + 1) * LAYER_GAP + NODE_W + PAD;

  const positions = {};
  for (let l = 0; l <= maxLayer; l++) {
    const nodes = layers[l] || [];
    const layerH = nodes.length * (NODE_H + NODE_GAP);
    const startY = PAD + (totalH - layerH) / 2;
    nodes.forEach((b, i) => {
      positions[b.id] = {
        x: PAD + l * LAYER_GAP,
        y: startY + i * (NODE_H + NODE_GAP),
      };
    });
  }

  let svgArrows = '';
  const edgeColors = {'open':'#6b7280','in_progress':'#3b82f6','blocked':'#ef4444','closed':'#22c55e','deferred':'#f59e0b'};

  for (const b of data) {
    const from = positions[b.id];
    if (!from) continue;
    for (const childId of (children[b.id]||[])) {
      const to = positions[childId];
      if (!to) continue;
      const x1 = from.x + NODE_W;
      const y1 = from.y + NODE_H / 2;
      const x2 = to.x;
      const y2 = to.y + NODE_H / 2;
      const midX = (x1 + x2) / 2;
      const edgeColor = edgeColors[b.status] || '#6b7280';
      svgArrows += '<path d="M' + x1 + ',' + y1 + ' C' + midX + ',' + y1 + ' ' + midX + ',' + y2 + ' ' + x2 + ',' + y2 + '" stroke="' + edgeColor + '" stroke-width="1.5" fill="none" opacity="0.45" marker-end="url(#arrowhead)"/>';
    }
  }

  let nodesHtml = '';
  for (const b of data) {
    const pos = positions[b.id];
    if (!pos) continue;
    const edgeColor = edgeColors[b.status] || '#6b7280';
    const prioLabelText = ['CRIT','HIGH','MED','LOW','BACK'][b.priority]||'MED';
    const claimed = b.metadata?.claimed_by ? '<span class="gnode-claimed">\uD83D\uDC19 ' + esc(b.metadata.claimed_by) + '</span>' : '';
    nodesHtml += '<div class="gnode" style="left:' + pos.x + 'px;top:' + pos.y + 'px;border-left:4px solid ' + edgeColor + '">\n' +
      '<div class="gnode-id">' + esc(b.id.substring(0,14)) + '</div>\n' +
      '<div class="gnode-title" title="' + escAttr(b.title) + '">' + esc(b.title) + '</div>\n' +
      '<div class="gnode-meta"><span class="badge badge-' + esc(b.status) + '">' + esc(b.status) + '</span> <span class="badge ' + (b.priority===0?'badge-crit':b.priority===1?'badge-high':'') + '">' + prioLabelText + '</span>' + claimed + '</div>\n' +
    '</div>';
  }

  const emptyState = data.length ? '' : '<div class="empty-state">No beads yet. Pour a formula or create one!</div>';
  document.getElementById('tree').innerHTML = emptyState || '<div class="graph-container" style="height:' + totalH + 'px;min-width:' + totalW + 'px">\n' +
    '<svg class="graph-svg" style="width:' + totalW + 'px;height:' + totalH + 'px" viewBox="0 0 ' + totalW + ' ' + totalH + '">\n' +
      '<defs><marker id="arrowhead" markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto"><polygon points="0 0, 7 2.5, 0 5" fill="#484f58"/></marker></defs>\n' +
      svgArrows + '\n' +
    '</svg>\n' +
    nodesHtml + '\n' +
  '</div>';
}

function renderDebug() {
  const raw = JSON.stringify(data, null, 2);
  // Simple syntax highlighting for JSON
  const highlighted = raw
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"([^"]+)":/g, '<span class="key">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span class="str">"$1"</span>')
    .replace(/: (\d+\.?\d*)/g, ': <span class="num">$1</span>')
    .replace(/: (true|false)/g, ': <span class="bool">$1</span>')
    .replace(/: (null)/g, ': <span class="null">$1</span>');
  document.getElementById('debug').innerHTML = '<div class="debug-view"><pre>' + highlighted + '</pre></div>';
}

function esc(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function escAttr(s) { return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById(name).classList.add('active');
}

async function init() {
  await fetchProjects();
  await fetchData();
}

setInterval(() => { countdown--; document.getElementById('countdown').textContent=countdown; if(countdown<=0) fetchData(); }, 1000);

init();
</script>
</body>
</html>""".replace("REFRESH_SECS", str(REFRESH_SECONDS))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silent

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/projects":
            projects = discover_projects(SCAN_DIRS)
            self._json(projects)
            return

        if path == "/api/beads":
            # Support ?project=/path/to/project
            proj_path = params.get("project", [None])[0]
            if proj_path:
                bf = Path(proj_path) / ".beads" / "issues.jsonl"
            else:
                bf = BEADS_FILE
            beads = load_beads(bf)
            stats = {
                "total": len(beads),
                "closed": sum(1 for b in beads if b["status"] == "closed"),
                "in_progress": sum(1 for b in beads if b["status"] == "in_progress"),
                "open": sum(1 for b in beads if b["status"] == "open"),
                "blocked": sum(1 for b in beads if b["status"] == "blocked"),
                "claimed": sum(1 for b in beads if b.get("metadata", {}).get("claimed_by")),
            }
            self._json({
                "project": str(bf),
                "project_name": Path(bf).parent.parent.name if bf.exists() else "unknown",
                "beads": beads,
                "dep_map": bead_deps_map(beads),
                "stats": stats,
            })
            return

        if path == "/api/raw":
            proj_path = params.get("project", [None])[0]
            if proj_path:
                bf = Path(proj_path) / ".beads" / "issues.jsonl"
            else:
                bf = BEADS_FILE
            self._text(beads_json(bf))
            return

        # Default: serve HTML
        html = HTML.replace(
            "REFRESH_SECS", str(REFRESH_SECONDS)
        ).replace(
            "REFRESH = REFRESH_SECS;",
            f"const REFRESH = {REFRESH_SECONDS};"
        )
        self._html(html)

    def _json(self, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, body):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description="Flywheel Dashboard")
    parser.add_argument("--port", type=int, default=9120)
    parser.add_argument("--project-root", default=PROJECT_ROOT)
    parser.add_argument("--scan-dirs", default="~/workspace,~",
                        help="Comma-separated directories to scan for projects")
    args = parser.parse_args()

    global BEADS_FILE, SCAN_DIRS
    BEADS_FILE = Path(os.path.expanduser(args.project_root)) / ".beads" / "issues.jsonl"
    SCAN_DIRS = [d.strip() for d in args.scan_dirs.split(",") if d.strip()]

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"🌀 Flywheel Dashboard → http://0.0.0.0:{args.port}")
    print(f"   Default project: {BEADS_FILE}")
    print(f"   Scanning: {SCAN_DIRS}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
