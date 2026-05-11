"""Graph analytics for bead dependency DAG.

Pure Python, zero external dependencies. Runs in RPC child process.
Supports: PageRank, critical path, betweenness centrality, topological sort, cycle detection.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


def _build_adjacency(
    issues: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, float]]:
    """Build forward and reverse adjacency from bead dependencies.

    Returns (forward_edges, reverse_edges, node_weights) where:
    - forward_edges[id] = list of beads this bead blocks (outgoing)
    - reverse_edges[id] = list of beads that block this bead (incoming)
    - node_weights[id] = 1.0 / priority (higher priority = higher weight)
    """
    ids = {i["id"] for i in issues}

    forward: dict[str, list[str]] = {iid: [] for iid in ids}
    reverse: dict[str, list[str]] = {iid: [] for iid in ids}
    weights: dict[str, float] = {}

    for issue in issues:
        iid = issue["id"]
        # Weight: critical(0)=1.0, high(1)=0.5, medium(2)=0.33, low(3)=0.25, backlog(4)=0.2
        weights[iid] = 1.0 / (1.0 + issue.get("priority", 2))

        for dep in issue.get("dependencies", []):
            if dep in ids:
                forward[dep].append(iid)  # dep blocks → issue gets unblocked
                reverse[iid].append(dep)

    return forward, reverse, weights


def _topological_sort(
    forward: dict[str, list[str]],
    reverse: dict[str, list[str]],
) -> tuple[list[str], list[list[str]]]:
    """Kahn's algorithm. Returns (sorted_order, cycles).

    cycles is a list of cycle groups (lists of node IDs).
    Returns empty cycles list if DAG is acyclic.
    """
    in_degree = {node: len(rev) for node, rev in reverse.items()}

    queue = deque([node for node, deg in in_degree.items() if deg == 0])
    order: list[str] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in forward.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Detect cycles
    remaining = [node for node, deg in in_degree.items() if deg > 0]
    cycles: list[list[str]] = []
    if remaining:
        # Simple SCC detection for remaining nodes
        visited = set()
        for node in remaining:
            if node in visited:
                continue
            cycle_group = []
            stack = [node]
            while stack:
                n = stack.pop()
                if n in visited:
                    continue
                visited.add(n)
                cycle_group.append(n)
                for neighbor in forward.get(n, []):
                    if neighbor in remaining:
                        stack.append(neighbor)
            cycles.append(cycle_group)

    return order, cycles


def pagerank(
    issues: list[dict[str, Any]],
    damping: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Compute PageRank for bead dependency graph.

    Higher score = this bead blocks more downstream work.
    Use this for robot-next: pick the highest PageRank bead that is ready.
    """
    if not issues:
        return {}

    forward, reverse, _ = _build_adjacency(issues)
    n = len(forward)
    if n == 0:
        return {}

    # Initialize
    ranks = {node: 1.0 / n for node in forward}
    teleport = (1.0 - damping) / n

    for _ in range(max_iter):
        new_ranks: dict[str, float] = {}
        max_diff = 0.0

        for node in forward:
            incoming = reverse[node]
            rank_sum = 0.0
            for src in incoming:
                out_deg = len(forward[src])
                if out_deg > 0:
                    rank_sum += ranks[src] / out_deg
                else:
                    rank_sum += ranks[src] / n  # dangling node
            new_rank = teleport + damping * rank_sum
            new_ranks[node] = new_rank
            max_diff = max(max_diff, abs(new_rank - ranks[node]))

        ranks = new_ranks
        if max_diff < tol:
            break

    return ranks


def critical_path(
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute critical path — longest dependency chain determining minimum project time.

    Uses topological sort + dynamic programming with priority-weighted costs.
    Returns the critical path beads and total cost.
    """
    if not issues:
        return {"path": [], "length": 0, "cost": 0.0}

    forward, reverse, weights = _build_adjacency(issues)
    order, cycles = _topological_sort(forward, reverse)

    if cycles:
        return {
            "path": [],
            "length": 0,
            "cost": 0.0,
            "error": f"Graph has {len(cycles)} cycle(s). Fix cyclic dependencies first.",
            "cycles": cycles,
        }

    # Longest path DP
    dist: dict[str, float] = {node: weights[node] for node in order}
    prev: dict[str, str | None] = {node: None for node in order}

    for node in order:
        for neighbor in forward.get(node, []):
            new_dist = dist[node] + weights[neighbor]
            if new_dist > dist[neighbor]:
                dist[neighbor] = new_dist
                prev[neighbor] = node

    # Find end node
    if not dist:
        return {"path": [], "length": 0, "cost": 0.0}

    end_node = max(dist, key=dist.get)  # type: ignore[arg-type]
    path: list[str] = []
    current: str | None = end_node
    while current is not None:
        path.append(current)
        current = prev[current]
    path.reverse()

    # Lookup titles
    id_to_title = {i["id"]: i["title"] for i in issues}
    path_with_titles = [{"id": nid, "title": id_to_title.get(nid, nid)} for nid in path]

    return {
        "path": path_with_titles,
        "length": len(path),
        "cost": round(dist[end_node], 4),
    }


def betweenness_centrality(
    issues: list[dict[str, Any]],
) -> dict[str, float]:
    """Compute betweenness centrality — nodes that sit on many shortest paths.

    High betweenness = bottleneck bead that connects otherwise disconnected work.
    """
    if not issues:
        return {}

    forward, reverse, _ = _build_adjacency(issues)
    ids = list(forward.keys())
    n = len(ids)

    centrality: dict[str, float] = {nid: 0.0 for nid in ids}

    for s in ids:
        # BFS from source s
        stack: list[str] = []
        pred: dict[str, list[str]] = {nid: [] for nid in ids}
        sigma: dict[str, int] = {nid: 0 for nid in ids}
        dist: dict[str, int] = {nid: -1 for nid in ids}

        sigma[s] = 1
        dist[s] = 0
        queue = deque([s])

        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in forward.get(v, []):
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)

        # Back-propagation
        delta: dict[str, float] = {nid: 0.0 for nid in ids}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                centrality[w] += delta[w]

    # Normalize for directed graph
    norm = (n - 1) * (n - 2) if n > 2 else 1.0
    for nid in centrality:
        centrality[nid] /= norm

    return centrality


def beads_triage(project_root: str | None = None) -> dict[str, Any]:
    """Full triage: combine ready beads with graph analytics.

    Returns:
    - ready: actionable beads sorted by priority
    - robot_next: the single best bead to work on (highest PageRank among ready)
    - critical_path: longest dependency chain
    - stats: summary counts
    - cycles: any cyclic dependencies that need fixing
    - bottlenecks: top 5 betweenness centrality beads
    """
    try:
        from .storage import _issues_path, _read_all, beads_ready
    except ImportError:
        from storage import _issues_path, _read_all, beads_ready  # type: ignore[no-redef]

    path = _issues_path(project_root)
    issues = _read_all(path)

    if not issues:
        return {"message": "No beads found. Create beads first."}

    ready_result = beads_ready(project_root)
    ready = ready_result["beads"]

    # Graph analytics
    pr = pagerank(issues)
    bc = betweenness_centrality(issues)
    cp = critical_path(issues)
    _, cycles_list = _topological_sort(*_build_adjacency(issues)[:2])

    # Robot next: highest PageRank among ready beads
    robot_next = None
    if ready:
        ready_ranks = [(b, pr.get(b["id"], 0.0)) for b in ready]
        ready_ranks.sort(key=lambda x: x[1], reverse=True)
        best = ready_ranks[0][0]
        robot_next = {
            "id": best["id"],
            "title": best["title"],
            "priority": best["priority"],
            "pagerank": round(ready_ranks[0][1], 6),
            "reason": "Highest PageRank among actionable beads — unblocks the most downstream work",
        }

    # Top bottlenecks
    sorted_bc = sorted(bc.items(), key=lambda x: x[1], reverse=True)[:5]
    id_to_title = {i["id"]: i["title"] for i in issues}
    bottlenecks = [
        {"id": nid, "title": id_to_title.get(nid, nid), "betweenness": round(score, 6)}
        for nid, score in sorted_bc if score > 0
    ]

    try:
        from .storage import beads_stats
    except ImportError:
        from storage import beads_stats  # type: ignore[no-redef]
    stats = beads_stats(project_root)

    return {
        "robot_next": robot_next,
        "ready_count": len(ready),
        "total_beads": len(issues),
        "critical_path": cp,
        "bottlenecks": bottlenecks,
        "cycles": [{"nodes": c} for c in cycles_list] if cycles_list else [],
        "stats": stats,
        "ready_beads": ready[:10],  # top 10 by priority
    }
