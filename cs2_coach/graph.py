"""Graphify-Integration – indexiert den CS2-Coach-Vault als Wissensgraph."""

from __future__ import annotations

from pathlib import Path

from graphify.extract import extract as gf_extract, collect_files as gf_collect_files
from graphify.build import build_from_json as gf_build
from graphify.cluster import cluster as gf_cluster, score_all as gf_score_all
from graphify.analyze import god_nodes as gf_god_nodes, surprising_connections as gf_surprising
from graphify.export import to_json as gf_to_json, to_html as gf_to_html, to_canvas as gf_to_canvas


def index_vault(vault_path: str, subfolder: str = "CS2-Coach") -> dict:
    """Indexiert den CS2-Coach-Ordner im Vault und baut einen Wissensgraphen."""
    coach_dir = Path(vault_path) / subfolder
    if not coach_dir.exists():
        raise FileNotFoundError(f"Coach-Ordner nicht gefunden: {coach_dir}")

    files = gf_collect_files(coach_dir, root=coach_dir)
    if not files:
        return {"nodes": 0, "edges": 0, "files": 0, "clusters": 0}

    extraction = gf_extract(files, root=coach_dir)
    G = gf_build(extraction, root=str(coach_dir))
    communities = gf_cluster(G)

    output_dir = coach_dir / "_graph"
    output_dir.mkdir(exist_ok=True)

    # JSON-Export des Graphen
    json_path = str(output_dir / "cs2_graph.json")
    gf_to_json(G, communities, json_path, force=True)

    # HTML-Visualisierung
    html_path = str(output_dir / "cs2_graph.html")
    gf_to_html(G, communities, html_path)

    # Obsidian Canvas
    canvas_path = str(output_dir / "cs2_graph.canvas")
    gf_to_canvas(G, communities, canvas_path)

    stats = {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "files": len(files),
        "clusters": len(communities),
    }

    # Graph-Analyse
    analysis = analyze_graph(G, communities)
    stats["analysis"] = analysis

    # Analyse als Markdown speichern
    _write_analysis_note(output_dir, stats, analysis)

    return stats


def analyze_graph(G, communities: dict) -> dict:
    """Analysiert den Graphen auf Schwerpunkte und Zusammenhänge."""
    result = {}

    god = gf_god_nodes(G, top_n=5)
    result["god_nodes"] = god

    surprising = gf_surprising(G, communities, top_n=5)
    result["surprising_connections"] = surprising

    scores = gf_score_all(G, communities)
    result["cluster_scores"] = scores

    return result


def _write_analysis_note(output_dir: Path, stats: dict, analysis: dict):
    """Schreibt eine Markdown-Zusammenfassung der Graph-Analyse."""
    lines = [
        "# CS2 Coach — Wissensgraph-Analyse\n",
        f"**Knoten:** {stats['nodes']} | "
        f"**Kanten:** {stats['edges']} | "
        f"**Dateien:** {stats['files']} | "
        f"**Cluster:** {stats['clusters']}\n",
    ]

    if analysis.get("god_nodes"):
        lines.append("\n## Zentrale Konzepte (God Nodes)\n")
        for node in analysis["god_nodes"]:
            name = node.get("label", node.get("node", node.get("name", "?")))
            degree = node.get("degree", "?")
            lines.append(f"- **{name}** (Verbindungen: {degree})")

    if analysis.get("surprising_connections"):
        lines.append("\n## Unerwartete Verbindungen\n")
        for conn in analysis["surprising_connections"]:
            src = conn.get("source", conn.get("node_a", "?"))
            tgt = conn.get("target", conn.get("node_b", "?"))
            lines.append(f"- {src} <-> {tgt}")

    if analysis.get("cluster_scores"):
        lines.append("\n## Cluster-Qualität\n")
        for cid, score in sorted(analysis["cluster_scores"].items()):
            lines.append(f"- Cluster {cid}: Kohäsion {score:.2f}")

    path = output_dir / "Graph-Analyse.md"
    path.write_text("\n".join(lines), encoding="utf-8")
