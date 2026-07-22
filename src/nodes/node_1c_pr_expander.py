"""
Node 1c: PR Expander
Used in Mode 2 (single_commits_evaluation).
Expands squashed PR commits into their constituent individual commits.
"""
from src.utils import execute_git, logger, audit_snapshot
from src.devops_client import DevOpsClient

def node_1c_pr_expander(state):
    logger.info("=" * 60)
    logger.info("NODE 1c: PR Expander (Single Commits Evaluation)")
    logger.info("=" * 60)

    config = state["config"]
    commits = state["commits_to_process"]
    repo_path = config["repo_path"]
    logs = state.get("extraction_logs", [])
    
    if not commits:
        return state
        
    devops_client = DevOpsClient(config)
    expanded_commits = []
    
    for commit_hash in commits:
        commit_desc = execute_git(f'git show -s --format=%s {commit_hash}', cwd=repo_path, check=False)
        commit_desc = commit_desc.strip() if commit_desc else ""
        
        pr_id = devops_client.extract_pr_id(commit_desc)
        if pr_id:
            logger.info(f"Expanding PR {pr_id} from squashed commit {commit_hash}...")
            pr_commits = devops_client.get_pr_commits(pr_id)
            if pr_commits:
                logs.append(f"PR EXPANDER: Replaced squashed {commit_hash} with {len(pr_commits)} individual commits from PR {pr_id}.")
                expanded_commits.extend(pr_commits)
            else:
                logs.append(f"PR EXPANDER: Failed to fetch commits for PR {pr_id}, retaining squashed commit {commit_hash}.")
                expanded_commits.append(commit_hash)
        else:
            logs.append(f"PR EXPANDER: No PR ID found in {commit_hash}, retaining as-is.")
            expanded_commits.append(commit_hash)
            
    logs.append(f"PR EXPANDER: Expanded {len(commits)} commits to {len(expanded_commits)} commits.")
    
    output_state = {
        **state,
        "commits_to_process": expanded_commits,
        "extraction_logs": logs
    }
    
    audit_snapshot({
        "original_commit_count": len(commits),
        "expanded_commit_count": len(expanded_commits)
    }, "node_1c_pr_expander", "PR Expansion", config)
    
    return output_state
