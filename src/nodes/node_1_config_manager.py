"""
Node 1: Configuration Manager & Git Scope Initialization

Responsibilities:
- Load config.yaml
- Prepare a pristine git workspace (stash, reset, clean, checkout)
- Collect the chronologically ordered list of commit hashes to analyze
"""

import os
import yaml
from src.utils import execute_git, logger, audit_snapshot


def node_1_config_manager(state):
    logger.info("=" * 60)
    logger.info("NODE 1: Configuration Manager & Git Scope")
    logger.info("=" * 60)

    # ── Load configuration ───────────────────────────────────────────────────
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Elevate pipeline mode specific parameters to root config
    pipeline_mode = config.get("pipeline_mode", "git_log_history")
    mode_params_key = f"{pipeline_mode}_parameters"
    mode_params = config.get(mode_params_key, {})
    
    # Merge mode specific parameters into root config
    for k, v in mode_params.items():
        config[k] = v

    if "report_generation" not in config:
        config["report_generation"] = {}

    # Bypass class filter if mode is bug_fixes_only
    if pipeline_mode == "bug_fixes_only":
        config["report_generation"]["analyze_class_level"] = False
        
    # Enforce class filter if mode is single_commits_evaluation
    if pipeline_mode == "single_commits_evaluation":
        config["report_generation"]["analyze_class_level"] = True
        if "classes" in config:
            config["report_generation"]["classes"] = config["classes"]

    repo_path = config["repo_path"]
    target_branch = config["target_branch"]
    since_filter = config["since_filter"]

    if not repo_path or not os.path.isdir(repo_path):
        raise ValueError(f"Invalid repo_path in config.yaml: '{repo_path}'")

    logger.info(f"Repository   : {repo_path}")
    logger.info(f"Target Branch: {target_branch}")
    logger.info(f"Since Filter : {since_filter}")

    # ── Ensure pristine workspace ────────────────────────────────────────────
    # Remove stale git index lock if present
    lock_file = os.path.join(repo_path, ".git", "index.lock")
    if os.path.exists(lock_file):
        logger.info("Removing stale git index lock...")
        try:
            os.remove(lock_file)
        except OSError:
            pass

    # Stash any uncommitted changes
    status = execute_git("git status --porcelain", cwd=repo_path, check=False)
    if status:
        logger.info("Local changes detected. Stashing...")
        execute_git("git stash --include-untracked", cwd=repo_path, check=False)

    # Hard reset and clean to ensure pristine state
    execute_git("git reset --hard HEAD", cwd=repo_path, check=True)
    execute_git("git clean -fd", cwd=repo_path, check=True)

    # Checkout target branch and update it
    logger.info(f"Checking out and pulling latest changes for branch: {target_branch}")
    execute_git(f"git checkout {target_branch}", cwd=repo_path, check=True)
    execute_git(f"git pull origin {target_branch}", cwd=repo_path, check=True)

    # ── Collect commit hashes ────────────────────────────────────────────────
    since_flag = ""
    if str(since_filter).strip() != "-1":
        since_flag = f'--since="{since_filter}"'

    # --format="%H" gives one hash per line, oldest first with --reverse
    git_log_cmd = f'git log --format="%H" --reverse {since_flag} {target_branch}'
    log_output = execute_git(git_log_cmd, cwd=repo_path, check=True)

    commits = [h.strip().strip('"') for h in log_output.splitlines() if h.strip()]

    # ── Calculate repository temporal boundaries ─────────────────────────────
    # Two separate timelines are maintained:
    # 1. "filtered" (repo_first/last_commit_date): boundaries of the ANALYSIS WINDOW
    #    (i.e., filtered by since_filter). Used for logging and display.
    # 2. "full" (repo_full_first/last_commit_date): absolute boundaries of the ENTIRE
    #    repository history. Used by node_5_mapper for time decay calibration.
    #    FLAW 4 FIX: separating these prevents the decay multiplier from being compressed
    #    into a narrow band when analyzing only a 1-week window.

    report_config = config.get("report_generation", {})
    use_full_timeline = report_config.get("use_full_branch_timeline", True)

    first_commit_flag = "" if use_full_timeline else since_flag

    repo_first_commit_date = execute_git(
        f'git log --reverse --format="%ci" {first_commit_flag} {target_branch}',
        cwd=repo_path, check=True
    ).splitlines()[0].strip().strip('"') if commits else ""

    repo_last_commit_date = execute_git(
        f'git log --format="%ci" -1 {target_branch}',
        cwd=repo_path, check=True
    ).strip().strip('"') if commits else ""

    # Full repo timeline — always uses all commits regardless of since_filter
    repo_full_first_commit_date = execute_git(
        f'git log --reverse --format="%ci" {target_branch}',
        cwd=repo_path, check=True
    ).splitlines()[0].strip().strip('"') if commits else ""

    repo_full_last_commit_date = repo_last_commit_date  # same: newest commit on branch

    logger.info(f"Repo First Commit (window): {repo_first_commit_date}")
    logger.info(f"Repo Last Commit  (window): {repo_last_commit_date}")
    logger.info(f"Repo First Commit (full)  : {repo_full_first_commit_date}")
    logger.info(f"Repo Last Commit  (full)  : {repo_full_last_commit_date}")

    logs = [
        f"CONFIG LOADED: repo_path={repo_path}, target_branch={target_branch}, since_filter={since_filter}",
        f"COLLECTED {len(commits)} commits to process.",
        f"REPO TEMPORAL BOUNDARIES: first={repo_first_commit_date}, last={repo_last_commit_date}"
    ]

    output_state = {
        "config": config,
        "commits_to_process": commits,
        "repo_first_commit_date": repo_first_commit_date,
        "repo_last_commit_date": repo_last_commit_date,
        "repo_full_first_commit_date": repo_full_first_commit_date,
        "repo_full_last_commit_date": repo_full_last_commit_date,
        "extraction_logs": logs,
    }

    audit_snapshot({
        "config_parameters": config,
        "repo_first_commit_date": repo_first_commit_date,
        "repo_last_commit_date": repo_last_commit_date,
        "total_commits_to_process": len(commits)
    }, "node_1_config_manager", "Configuration and Scope", config)

    return output_state
