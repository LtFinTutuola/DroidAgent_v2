from typing import TypedDict, List, Dict, Any


class AgentState(TypedDict):
    """
    LangGraph state definition for the Semantic Census Pipeline.

    Each field flows between nodes as part of the shared state dictionary.
    Nodes return partial dicts to update only the keys they own.
    """

    # Loaded configuration parameters from config.yaml
    config: Dict[str, Any]

    # The flat baseline of all logical objects currently alive in the repository
    baseline_objects: List[Dict[str, Any]]

    # Chronologically ordered list of commit hashes to analyze
    commits_to_process: List[str]

    # Raw diff payloads optimized for AST analysis.
    # Each entry: {commit_hash, commit_date, file_path, old_text, new_text, old_lines, new_lines}
    raw_diffs: List[Dict[str, Any]]

    # Semantic nodes returned by the Roslyn parser.
    # Each entry: {commit_hash, commit_date, commit_description, file_path, signature, parent_signature,
    #              clean_old, clean_new, added_lines, removed_lines}
    parsed_hunks: List[Dict[str, Any]]

    # Flat census entries at logical object level.
    # Each entry is structured as follows:
    # {
    #   "commit_description": str,
    #   "commit_hash": str,
    #   "commit_date": str,
    #   "logical_object": str,
    #   "parent_object": str,
    #   "added_lines": int,
    #   "removed_lines": int
    # }
    census_entries: List[Dict[str, Any]]

    # Global tracking of legacy impacts (dead code) grouped by class
    global_legacy_commits: Dict[str, Any]

    # Absolute date of the repository's very first commit (ISO format string)
    repo_first_commit_date: str

    # Absolute date of the repository's most recent commit (ISO format string)
    repo_last_commit_date: str

    # Full-repository timeline (all commits, not filtered by since_filter).
    # Used by node_5_mapper for time decay calibration (Flaw 4 fix).
    repo_full_first_commit_date: str
    repo_full_last_commit_date: str

    # Detailed logs reporting the collecting and discarding operations.
    extraction_logs: List[str]

    # Dynamically generated paths from the exporter, used by the report generator if needed
    report_aggregated_file: str
    report_raw_file: str
