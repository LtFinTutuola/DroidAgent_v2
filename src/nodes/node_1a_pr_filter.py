"""
Node 1a: PR Filter
Filters commits based on Azure DevOps PR work items.
Only retains commits linked to a PR where ALL linked work items are of type 'Bug'.
"""

from src.utils import logger, audit_snapshot
from src.devops_client import DevOpsClient

def node_1a_pr_filter(state):
    logger.info("=" * 60)
    logger.info("NODE 1a: PR Filter (Bug Fixes Only)")
    logger.info("=" * 60)

    config = state.get("config", {})
    commits = state.get("commits_to_process", [])
    logs = state.get("extraction_logs", [])
    repo_path = config.get("repo_path")

    if not commits:
        logger.warning("No commits to process.")
        return state

    client = DevOpsClient(config)
    filtered_commits = []
    discarded_commits = []

    from src.utils import execute_git

    for i, commit_hash in enumerate(commits):
        if i % 100 == 0 and i > 0:
            logger.info(f"  Progress: {i}/{len(commits)} commits checked...")

        commit_desc = execute_git(
            f'git show -s --format=%s {commit_hash}', cwd=repo_path, check=False
        )
        commit_desc = commit_desc.strip() if commit_desc else ""

        pr_id = client.extract_pr_id(commit_desc)
        if not pr_id:
            discarded_commits.append({"commit_hash": commit_hash, "reason": "no_pr_id_found"})
            logs.append(f"  DISCARDED {commit_hash}: No PR ID found in title.")
            continue

        is_bug = client.is_pr_bug_fix(pr_id)
        if is_bug:
            filtered_commits.append(commit_hash)
            logs.append(f"  COLLECTED {commit_hash}: PR {pr_id} is a bug fix.")
        else:
            discarded_commits.append({"commit_hash": commit_hash, "reason": "not_bug_fix", "pr_id": pr_id})
            logs.append(f"  DISCARDED {commit_hash}: PR {pr_id} is NOT exclusively a bug fix.")

    logger.info(f"PR Filter complete. Retained {len(filtered_commits)} out of {len(commits)} commits.")
    
    logs.append(f"PR FILTER: Retained {len(filtered_commits)} bug fix commits.")

    audit_snapshot({
        "initial_commits": len(commits),
        "retained_commits": len(filtered_commits),
        "discarded_commits": len(discarded_commits)
    }, "node_1a_pr_filter", "PR Filter Summary", config)

    return {
        "commits_to_process": filtered_commits,
        "extraction_logs": logs
    }
