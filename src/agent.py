"""
LangGraph graph construction and compilation for the Semantic Census Pipeline.

Defines a linear execution flow with conditional routing:
START → node_1 → [report-only shortcut OR full pipeline] → END

Full pipeline:
  node_1 → node_1b → [node_2 OR node_2b_remote] → node_2b_filter → node_3 → node_4 → node_5 → [node_6 OR node_6b] → [node_7 OR node_7b] → END
"""

import os
from langgraph.graph import StateGraph, START, END
from src.state import AgentState
from src.nodes.node_1_config_manager import node_1_config_manager
from src.nodes.node_1b_baseline_manager import node_1b_baseline_manager
from src.nodes.node_2_git_extractor import node_2_git_extractor
from src.nodes.node_2b_remote_git_extractor import node_2b_remote_git_extractor
from src.nodes.node_2b_global_filter import node_2b_global_filter
from src.nodes.node_3_roslyn_parser import node_3_roslyn_parser
from src.nodes.node_3b_commit_filter import node_3b_commit_filter
from src.nodes.node_4_semantic_filter import node_4_semantic_filter
from src.nodes.node_5_mapper import node_5_mapper
from src.nodes.node_6_exporter import node_6_exporter
from src.nodes.node_6b_commit_exporter import node_6b_commit_exporter
from src.nodes.node_7_heatmap_report_generator import node_7_heatmap_report_generator
from src.nodes.node_7b_commit_heatmap_report_generator import node_7b_commit_heatmap_report_generator


# ── Helper: Read commit evaluation parameters ───────────────────────────────

def _get_commit_eval_flags(config: dict) -> tuple:
    """
    Returns (use_commit_evaluation_mode, analyze_commit_overall_complexity)
    from the commit_evaluation_parameters config section.
    """
    commit_eval = config.get("commit_evaluation_parameters", {})
    use_commit_eval = commit_eval.get("use_commit_evaluation_mode", False)
    analyze_overall = commit_eval.get("analyze_commit_overall_complexity", True)
    return use_commit_eval, analyze_overall


# ── Routing Logic ────────────────────────────────────────────────────────────

def route_after_config(state: AgentState) -> str:
    """
    After loading config, decide whether to:
    - Jump directly to report generation (if data files are already present)
    - Or run the full pipeline from baseline_manager onward
    """
    config = state.get("config", {})
    report_config = config.get("report_generation", {})

    if report_config.get("generate_report", False):
        agg_path = report_config.get("aggregated_data_file_path", "")
        raw_path = report_config.get("code_mapping_file_path", "")
        if agg_path and raw_path and os.path.exists(agg_path) and os.path.exists(raw_path):
            use_commit_eval, _ = _get_commit_eval_flags(config)
            if use_commit_eval:
                return "commit_heatmap_report_generator"
            else:
                return "heatmap_report_generator"
    return "baseline_manager"


def route_after_baseline(state: AgentState) -> str:
    """
    After baseline is built, decide which git extractor to use:
    - commit_eval=false OR (commit_eval=true AND overall=true) → local git_extractor
    - commit_eval=true AND overall=false → remote_git_extractor (Azure DevOps API)
    """
    config = state.get("config", {})
    use_commit_eval, analyze_overall = _get_commit_eval_flags(config)

    if use_commit_eval and not analyze_overall:
        return "remote_git_extractor"
    return "git_extractor"


def route_after_roslyn(state: AgentState) -> str:
    """
    If commit evaluation mode is active, run the upstream commit filter (Node 3b)
    to discard irrelevant commits before they reach the expensive neural semantic engine.
    Otherwise, proceed directly to the semantic filter.
    """
    config = state.get("config", {})
    use_commit_eval, _ = _get_commit_eval_flags(config)
    if use_commit_eval:
        return "commit_filter"
    return "semantic_filter"


def route_after_mapper(state: AgentState) -> str:
    """
    After mapping, decide which exporter to use:
    - commit_eval=true → commit_exporter (node_6b)
    - commit_eval=false → exporter (node_6, original codebase heatmap)
    """
    config = state.get("config", {})
    use_commit_eval, _ = _get_commit_eval_flags(config)

    if use_commit_eval:
        return "commit_exporter"
    return "exporter"


def route_after_exporter(state: AgentState) -> str:
    config = state.get("config", {})
    report_config = config.get("report_generation", {})
    if report_config.get("generate_report", False):
        return "heatmap_report_generator"
    return END


def route_after_commit_exporter(state: AgentState) -> str:
    config = state.get("config", {})
    report_config = config.get("report_generation", {})
    if report_config.get("generate_report", False):
        return "commit_heatmap_report_generator"
    return END


# ── Build the LangGraph Pipeline ─────────────────────────────────────────────
workflow = StateGraph(AgentState)

# Register nodes
workflow.add_node("config_manager", node_1_config_manager)
workflow.add_node("baseline_manager", node_1b_baseline_manager)
workflow.add_node("git_extractor", node_2_git_extractor)
workflow.add_node("remote_git_extractor", node_2b_remote_git_extractor)
workflow.add_node("global_filter", node_2b_global_filter)
workflow.add_node("roslyn_parser", node_3_roslyn_parser)
workflow.add_node("commit_filter", node_3b_commit_filter)
workflow.add_node("semantic_filter", node_4_semantic_filter)
workflow.add_node("mapper", node_5_mapper)
workflow.add_node("exporter", node_6_exporter)
workflow.add_node("commit_exporter", node_6b_commit_exporter)
workflow.add_node("heatmap_report_generator", node_7_heatmap_report_generator)
workflow.add_node("commit_heatmap_report_generator", node_7b_commit_heatmap_report_generator)

# Define edges
workflow.add_edge(START, "config_manager")

workflow.add_conditional_edges(
    "config_manager",
    route_after_config,
    {
        "heatmap_report_generator": "heatmap_report_generator",
        "commit_heatmap_report_generator": "commit_heatmap_report_generator",
        "baseline_manager": "baseline_manager"
    }
)

workflow.add_conditional_edges(
    "baseline_manager",
    route_after_baseline,
    {
        "git_extractor": "git_extractor",
        "remote_git_extractor": "remote_git_extractor",
    }
)

workflow.add_edge("git_extractor", "global_filter")
workflow.add_edge("remote_git_extractor", "global_filter")
workflow.add_edge("global_filter", "roslyn_parser")

workflow.add_conditional_edges(
    "roslyn_parser",
    route_after_roslyn,
    {
        "commit_filter": "commit_filter",
        "semantic_filter": "semantic_filter"
    }
)

workflow.add_edge("commit_filter", "semantic_filter")
workflow.add_edge("semantic_filter", "mapper")

workflow.add_conditional_edges(
    "mapper",
    route_after_mapper,
    {
        "exporter": "exporter",
        "commit_exporter": "commit_exporter"
    }
)

workflow.add_conditional_edges(
    "exporter",
    route_after_exporter,
    {
        "heatmap_report_generator": "heatmap_report_generator",
        END: END
    }
)

workflow.add_conditional_edges(
    "commit_exporter",
    route_after_commit_exporter,
    {
        "commit_heatmap_report_generator": "commit_heatmap_report_generator",
        END: END
    }
)

workflow.add_edge("heatmap_report_generator", END)
workflow.add_edge("commit_heatmap_report_generator", END)

# Compile the graph
app = workflow.compile()
