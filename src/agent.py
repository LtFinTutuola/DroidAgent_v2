"""
LangGraph graph construction and compilation for the Semantic Census Pipeline.

Defines a strictly linear execution flow:
START → node_1 → node_2 → node_3 → node_4 → node_5 → node_6 → END
"""

import os
from langgraph.graph import StateGraph, START, END
from src.state import AgentState
from src.nodes.node_1_config_manager import node_1_config_manager
from src.nodes.node_1b_baseline_manager import node_1b_baseline_manager
from src.nodes.node_2_git_extractor import node_2_git_extractor
from src.nodes.node_2b_global_filter import node_2b_global_filter
from src.nodes.node_3_roslyn_parser import node_3_roslyn_parser
from src.nodes.node_4_semantic_filter import node_4_semantic_filter
from src.nodes.node_5_mapper import node_5_mapper
from src.nodes.node_6_exporter import node_6_exporter
from src.nodes.node_7_heatmap_report_generator import node_7_heatmap_report_generator

from src.nodes.node_1a_pr_filter import node_1a_pr_filter
from src.nodes.node_1c_pr_expander import node_1c_pr_expander

# ── Routing Logic ────────────────────────────────────────────────────────────

def route_after_config(state: AgentState) -> str:
    config = state.get("config", {})
    pipeline_mode = config.get("pipeline_mode", "git_log_history")
    
    if pipeline_mode == "bug_fixes_only":
        return "pr_filter"
    elif pipeline_mode == "single_commits_evaluation":
        return "pr_expander"
        
    report_config = config.get("report_generation", {})
    if report_config.get("generate_report", False):
        agg_path = report_config.get("aggregated_data_file_path", "")
        raw_path = report_config.get("code_mapping_file_path", "")
        if agg_path and raw_path and os.path.exists(agg_path) and os.path.exists(raw_path):
            return "heatmap_report_generator"
    return "baseline_manager"

def route_after_pr_filter(state: AgentState) -> str:
    config = state.get("config", {})
    report_config = config.get("report_generation", {})
    if report_config.get("generate_report", False):
        agg_path = report_config.get("aggregated_data_file_path", "")
        raw_path = report_config.get("code_mapping_file_path", "")
        if agg_path and raw_path and os.path.exists(agg_path) and os.path.exists(raw_path):
            return "heatmap_report_generator"
    return "baseline_manager"

def route_after_pr_expander(state: AgentState) -> str:
    config = state.get("config", {})
    report_config = config.get("report_generation", {})
    if report_config.get("generate_report", False):
        agg_path = report_config.get("aggregated_data_file_path", "")
        raw_path = report_config.get("code_mapping_file_path", "")
        if agg_path and raw_path and os.path.exists(agg_path) and os.path.exists(raw_path):
            return "heatmap_report_generator"
    return "baseline_manager"

def route_after_exporter(state: AgentState) -> str:
    config = state.get("config", {})
    report_config = config.get("report_generation", {})
    if report_config.get("generate_report", False):
        return "heatmap_report_generator"
    return END

# ── Build the LangGraph Pipeline ─────────────────────────────────────────────
workflow = StateGraph(AgentState)

# Register nodes
workflow.add_node("config_manager", node_1_config_manager)
workflow.add_node("pr_filter", node_1a_pr_filter)
workflow.add_node("pr_expander", node_1c_pr_expander)
workflow.add_node("baseline_manager", node_1b_baseline_manager)
workflow.add_node("git_extractor", node_2_git_extractor)
workflow.add_node("global_filter", node_2b_global_filter)
workflow.add_node("roslyn_parser", node_3_roslyn_parser)
workflow.add_node("semantic_filter", node_4_semantic_filter)
workflow.add_node("mapper", node_5_mapper)
workflow.add_node("exporter", node_6_exporter)
workflow.add_node("heatmap_report_generator", node_7_heatmap_report_generator)

# Define edges
workflow.add_edge(START, "config_manager")

workflow.add_conditional_edges(
    "config_manager",
    route_after_config,
    {
        "pr_filter": "pr_filter",
        "pr_expander": "pr_expander",
        "heatmap_report_generator": "heatmap_report_generator",
        "baseline_manager": "baseline_manager"
    }
)

workflow.add_conditional_edges(
    "pr_filter",
    route_after_pr_filter,
    {
        "heatmap_report_generator": "heatmap_report_generator",
        "baseline_manager": "baseline_manager"
    }
)

workflow.add_conditional_edges(
    "pr_expander",
    route_after_pr_expander,
    {
        "heatmap_report_generator": "heatmap_report_generator",
        "baseline_manager": "baseline_manager"
    }
)

workflow.add_edge("baseline_manager", "git_extractor")
workflow.add_edge("git_extractor", "global_filter")
workflow.add_edge("global_filter", "roslyn_parser")
workflow.add_edge("roslyn_parser", "semantic_filter")
workflow.add_edge("semantic_filter", "mapper")
workflow.add_edge("mapper", "exporter")

workflow.add_conditional_edges(
    "exporter",
    route_after_exporter,
    {
        "heatmap_report_generator": "heatmap_report_generator",
        END: END
    }
)

workflow.add_edge("heatmap_report_generator", END)

# Compile the graph
app = workflow.compile()
