"""
Node 3B: Commit Filter (Upstream Rejection)

Responsibilities:
- Runs immediately after Node 3 (Roslyn Parser) when commit evaluation mode is active.
- Groups parsed hunks by commit_hash to evaluate commit-level rejection criteria.
- Applies the `updated_classes_threshold` and `classes` filter exactly as Node 6b used to,
  but prevents the discarded commits from ever entering the heavyweight Node 5 (Neural Engine).
"""

from collections import defaultdict
from src.utils import logger, audit_snapshot

def node_3b_commit_filter(state):
    logger.info("=" * 60)
    logger.info("NODE 3B: Commit Filter (Upstream Rejection)")
    logger.info("=" * 60)

    config = state["config"]
    parsed_hunks = state.get("parsed_hunks", [])
    logs = state.get("extraction_logs", [])

    if not parsed_hunks:
        logger.warning("No hunks to filter.")
        return {"parsed_hunks": []}

    commit_eval = config.get("commit_evaluation_parameters", {})
    updated_classes_threshold = commit_eval.get("updated_classes_threshold", -1)
    classes_filter = commit_eval.get("classes", [])
    apply_strict_delimitation_area = commit_eval.get("apply_strict_delimitation_area", False)

    # If no filters are active, pass through quickly
    if updated_classes_threshold == -1 and not classes_filter:
        logger.info("No commit-level filters configured. Passing through.")
        return {"parsed_hunks": parsed_hunks}

    # Group hunks by commit_hash
    hunks_by_commit = defaultdict(list)
    for hunk in parsed_hunks:
        hunks_by_commit[hunk["commit_hash"]].append(hunk)

    discarded_commits = set()
    final_hunks = []

    for chash, hunks in hunks_by_commit.items():
        # Identify the unique classes (parent_objects) modified in this commit
        modified_classes = {h["parent_object"] for h in hunks if h["parent_object"]}

        # 1. Apply updated_classes_threshold
        if updated_classes_threshold != -1 and len(modified_classes) > updated_classes_threshold:
            discard_msg = f"NODE 3B: Discarding commit {chash} because updated classes ({len(modified_classes)}) exceeds threshold ({updated_classes_threshold})."
            logger.info(discard_msg)
            logs.append(discard_msg)
            discarded_commits.add(chash)
            continue

        # 2. Apply classes filter
        if classes_filter:
            if apply_strict_delimitation_area:
                # ALL modified classes must be in the filter
                if any(cls_name not in classes_filter for cls_name in modified_classes):
                    discard_msg = f"NODE 3B: Discarding commit {chash} because it modifies classes outside the strict delimitation area."
                    logger.info(discard_msg)
                    logs.append(discard_msg)
                    discarded_commits.add(chash)
                    continue
            else:
                # AT LEAST ONE modified class must be in the filter
                if not any(cls_name in classes_filter for cls_name in modified_classes):
                    discard_msg = f"NODE 3B: Discarding commit {chash} because none of its updated classes are in the classes filter."
                    logger.info(discard_msg)
                    logs.append(discard_msg)
                    discarded_commits.add(chash)
                    continue

        # If it survives, keep its hunks
        final_hunks.extend(hunks)

    logger.info(f"Node 3B filtered out {len(discarded_commits)} entire commits upstream.")
    logger.info(f"Hunks passing to Semantic Filter: {len(parsed_hunks)} → {len(final_hunks)}")
    logger.info("Node 3B Finished.")

    output_state = {
        "parsed_hunks": final_hunks,
        "extraction_logs": logs
    }
    audit_snapshot({
        "total_commits_discarded": len(discarded_commits),
        "total_hunks_passed": len(final_hunks)
    }, "node_3b_commit_filter", "Upstream Filtering Summary", config)
    
    return output_state
