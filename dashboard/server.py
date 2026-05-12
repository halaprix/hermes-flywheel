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
    return beads


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
    "closed": "#10b981",
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
:root {
  --bg: #0d1117; --surface: #161b22; --border: #30363d;
  --text: #c9d1d9; --text-muted: #8b949e; --accent: #58a6ff;
  --green: #3fb950; --red: #f85149; --orange: #d2991d; --blue: #58a6ff;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 24px; }
header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid var(--border); flex-wrap: wrap; gap: 12px; }
h1 { font-size: 20px; display: flex; align-items: center; gap: 10px; }
h1 span { font-size: 14px; color: var(--text-muted); font-weight: normal; }
.stats { display: flex; gap: 20px; font-size: 13px; }
.stat { text-align: center; }
.stat .num { font-size: 24px; font-weight: 700; }
.stat .label { color: var(--text-muted); font-size: 11px; text-transform: uppercase; }
.project-bar { display: flex; align-items: center; gap: 10px; width: 100%; }
.project-bar select { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 12px; font-size: 13px; cursor: pointer; min-width: 200px; }
.project-bar select:focus { outline: none; border-color: var(--accent); }
.project-bar .proj-label { color: var(--text-muted); font-size: 12px; }
.tabs { display: flex; gap: 4px; margin-bottom: 16px; }
.tab { padding: 8px 16px; border: 1px solid var(--border); border-radius: 6px 6px 0 0; cursor: pointer; background: var(--surface); color: var(--text-muted); font-size: 13px; }
.tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.view { display: none; }
.view.active { display: block; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); color: var(--text-muted); font-weight: 600; text-transform: uppercase; font-size: 11px; }
td { padding: 10px 12px; border-bottom: 1px solid var(--border); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
.badge-open { background: #6b728033; color: #6b7280; }
.badge-in_progress { background: #3b82f633; color: #3b82f6; }
.badge-blocked { background: #ef444433; color: #ef4444; }
.badge-closed { background: #10b98133; color: #10b981; }
.badge-deferred { background: #f59e0b33; color: #f59e0b; }
.badge-crit { background: #ef444433; color: #ef4444; }
.badge-high { background: #f59e0b33; color: #f59e0b; }
.mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; }
.claimed { color: var(--blue); font-size: 12px; }
.deps { display: flex; gap: 4px; flex-wrap: wrap; }
.dep { font-size: 11px; padding: 1px 6px; border-radius: 4px; background: var(--surface); border: 1px solid var(--border); }
.debug-view { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px; overflow: auto; max-height: 80vh; }
.debug-view pre { font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.tree { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; line-height: 1.8; padding: 20px; }
.tree-node { padding: 2px 0; }
.tree-id { color: var(--text-muted); }
.tree-status { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.tree-claimed { color: var(--blue); font-size: 11px; margin-left: 8px; }
.refresh { color: var(--text-muted); font-size: 12px; }
.spark { display: inline-block; width: 8px; height: 8px; border-radius: 50%; animation: pulse 2s infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
.graph-container { position: relative; overflow: auto; padding-bottom: 20px; }
.graph-svg { position: absolute; top: 0; left: 0; pointer-events: none; z-index: 1; }
.gnode { position: absolute; width: 210px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; cursor: default; z-index: 2; font-size: 12px; transition: border-color 0.2s; }
.gnode:hover { border-color: var(--accent); z-index: 3; }
.gnode-id { color: var(--text-muted); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 10px; margin-bottom: 3px; }
.gnode-title { color: var(--text); font-weight: 600; margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.gnode-meta { display: flex; gap: 6px; align-items: center; font-size: 11px; }
.gnode-claimed { color: var(--blue); font-size: 10px; margin-left: auto; }
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
  <div class="refresh" style="margin-left:auto;padding-top:8px;">↻ <span id="countdown">5</span>s</div>
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
  sel.innerHTML = projects.map(p => `<option value="${escAttr(p.path)}">${esc(p.name)} (${esc(p.path)})</option>`).join('');

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
    document.getElementById('project').textContent = '— ' + (d.project_name || '');
    renderStats(d.stats);
  } catch(e) {
    data = [];
    depsMap = {};
    document.getElementById('project').textContent = '— error';
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
  if (!stats) return;
  const {total, closed, in_progress, open, blocked, claimed} = stats;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="num">${total||0}</div><div class="label">Total</div></div>
    <div class="stat"><div class="num" style="color:var(--green)">${closed||0}</div><div class="label">Closed</div></div>
    <div class="stat"><div class="num" style="color:var(--blue)">${in_progress||0}</div><div class="label">Active</div></div>
    <div class="stat"><div class="num" style="color:var(--red)">${blocked||0}</div><div class="label">Blocked</div></div>
    <div class="stat"><div class="num">${claimed||0}</div><div class="label">Claimed</div></div>
  `;
}

function badgeClass(s) { return 'badge badge-' + (s||'open'); }
function prioLabel(p) { return ['CRIT','HIGH','MED','LOW','BACK'][p]||'MED'; }
function prioClass(p) { return p===0?'badge-crit':p===1?'badge-high':''; }

function renderTable() {
  const sorted = [...data].sort((a,b) => (a.priority||2) - (b.priority||2));
  let html = '<table><thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Priority</th><th>Dependencies</th><th>Claimed By</th></tr></thead><tbody>';
  for (const b of sorted) {
    const deps = (b.dependencies||[]).map(d => '<span class="dep">'+d.substring(0,12)+'</span>').join('') || '—';
    const claimed = b.metadata?.claimed_by ? '<span class="claimed">🐙 '+b.metadata.claimed_by+'</span>' : '—';
    html += `<tr>
      <td class="mono">${b.id}</td>
      <td>${esc(b.title)}</td>
      <td><span class="${badgeClass(b.status)}">${b.status}</span></td>
      <td><span class="badge ${prioClass(b.priority)}">${prioLabel(b.priority)}</span></td>
      <td><div class="deps">${deps}</div></td>
      <td>${claimed}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  document.getElementById('table').innerHTML = data.length ? html : '<p style="padding:40px;text-align:center;color:var(--text-muted)">No beads yet. Pour a formula or create one!</p>';
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
  const colors = {'open':'#6b7280','in_progress':'#3b82f6','blocked':'#ef4444','closed':'#3fb950','deferred':'#f59e0b'};

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
      const edgeColor = colors[b.status] || '#6b7280';
      svgArrows += `<path d="M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}" stroke="${edgeColor}" stroke-width="1.5" fill="none" opacity="0.5" marker-end="url(#arrowhead)"/>`;
    }
  }

  let nodesHtml = '';
  for (const b of data) {
    const pos = positions[b.id];
    if (!pos) continue;
    const color = colors[b.status] || '#6b7280';
    const prioLabel = ['CRIT','HIGH','MED','LOW','BACK'][b.priority]||'MED';
    const claimed = b.metadata?.claimed_by ? `<span class="gnode-claimed">🐙 ${esc(b.metadata.claimed_by)}</span>` : '';
    nodesHtml += `<div class="gnode" style="left:${pos.x}px;top:${pos.y}px;border-left:4px solid ${color}">
      <div class="gnode-id">${esc(b.id.substring(0,14))}</div>
      <div class="gnode-title" title="${esc(b.title)}">${esc(b.title)}</div>
      <div class="gnode-meta"><span class="badge badge-${b.status}">${b.status}</span> <span class="badge ${b.priority===0?'badge-crit':b.priority===1?'badge-high':''}">${prioLabel}</span>${claimed}</div>
    </div>`;
  }

  const emptyState = data.length ? '' : '<p style="padding:40px;text-align:center;color:var(--text-muted)">No beads yet. Pour a formula or create one!</p>';
  document.getElementById('tree').innerHTML = emptyState || `<div class="graph-container" style="height:${totalH}px;min-width:${totalW}px">
    <svg class="graph-svg" style="width:${totalW}px;height:${totalH}px" viewBox="0 0 ${totalW} ${totalH}">
      <defs><marker id="arrowhead" markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto"><polygon points="0 0, 7 2.5, 0 5" fill="#484f58"/></marker></defs>
      ${svgArrows}
    </svg>
    ${nodesHtml}
  </div>`;
}

function renderDebug() {
  document.getElementById('debug').innerHTML = '<div class="debug-view"><pre>' + esc(JSON.stringify(data, null, 2)) + '</pre></div>';
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
