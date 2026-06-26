import os
import json
from datetime import datetime
from src.utils import shutdown_subprocesses, logger, audit_snapshot

def node_6b_commit_exporter(state):
    logger.info("=" * 60)
    logger.info("NODE 6B: Commit Exporter (Final Dump & Teardown)")
    logger.info("=" * 60)

    config = state["config"]
    census_entries = state.get("census_entries", [])
    output_path = config.get("output_json_path", "output/pr_census.json")
    produce_log = config.get("produce_log", False)
    logs = state.get("extraction_logs", [])

    # Ensure impact scoring fields are present on every entry
    for entry in census_entries:
        if "impact_score" not in entry:
            entry["impact_score"] = 0.0

    output_dir = os.path.dirname(output_path) or "output"
    os.makedirs(output_dir, exist_ok=True)

    # Determine output naming based on commit evaluation mode
    commit_eval = config.get("commit_evaluation_parameters", {})
    analyze_overall = commit_eval.get("analyze_commit_overall_complexity", True)

    # Timestamped Filenames
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    census_filename = f"{timestamp}_code_mapping.json"
    log_filename = f"{timestamp}_log.txt"
    if analyze_overall:
        aggregated_filename = f"{timestamp}_commit_aggregated_scores.json"
    else:
        aggregated_filename = f"{timestamp}_single_commit_aggregated_scores.json"

    final_census_path = os.path.join(output_dir, census_filename)
    final_log_path = os.path.join(output_dir, log_filename)
    final_aggregated_path = os.path.join(output_dir, aggregated_filename)

    commits_data = {}

    for entry in census_entries:
        proj_name = entry.get("project", "Unknown Project")
        parent_obj = entry.get("parent_object", "")
        log_obj = entry.get("logical_object", "")
        class_name = parent_obj if parent_obj else log_obj

        for commit in entry.get("commits", []):
            chash = commit.get("commit_hash")
            if not chash:
                continue
                
            impact = commit.get("impact", {}).get("calculation_factors", {}).get("diff_score", 0.0)
            
            if impact > 0:
                if chash not in commits_data:
                    commits_data[chash] = {
                        "commit_hash": chash,
                        "commit_description": commit.get("commit_description", ""),
                        "commit_date": commit.get("commit_date", ""),
                        "classes": {}
                    }
                    # Propagate original_pr_id if present (remote extraction mode)
                    if commit.get("original_pr_id"):
                        commits_data[chash]["original_pr_id"] = commit["original_pr_id"]
                
                if class_name not in commits_data[chash]["classes"]:
                    commits_data[chash]["classes"][class_name] = {
                        "class_name": class_name,
                        "project_name": proj_name,
                        "logical_objects": []
                    }
                
                commits_data[chash]["classes"][class_name]["logical_objects"].append({
                    "logical_object_name": log_obj,
                    "actual_impact_score": impact
                })

    global_legacy_commits = state.get("global_legacy_commits", {})
    for class_name, legacy_data in global_legacy_commits.items():
        proj_name = legacy_data.get("project", "Unknown Project")
        for chash, commit_list in legacy_data.get("commits", {}).items():
            for commit in commit_list:
                impact = commit.get("impact", {}).get("calculation_factors", {}).get("diff_score", 0.0)
                if impact > 0:
                    if chash not in commits_data:
                        commits_data[chash] = {
                            "commit_hash": chash,
                            "commit_description": commit.get("commit_description", ""),
                            "commit_date": commit.get("commit_date", ""),
                            "classes": {}
                        }
                    
                    if class_name not in commits_data[chash]["classes"]:
                        commits_data[chash]["classes"][class_name] = {
                            "class_name": class_name,
                            "project_name": proj_name,
                            "logical_objects": []
                        }
                    
                    commits_data[chash]["classes"][class_name]["logical_objects"].append({
                        "logical_object_name": class_name,
                        "actual_impact_score": impact
                    })

    final_commits = []
    
    for chash, cdata in commits_data.items():

        commit_obj = {}
        if not analyze_overall and "original_pr_id" in cdata:
            commit_obj["parent_pull_request_id"] = cdata["original_pr_id"]
            
        commit_obj.update({
            "commit_hash": chash,
            "commit_description": cdata["commit_description"],
            "commit_date": cdata["commit_date"],
            "impact_score": 0.0,
            "classes": []
        })

        for class_name, class_data in cdata["classes"].items():
            logical_objects = class_data["logical_objects"]
            # Rank logical objects in descending order of actual impact score
            logical_objects.sort(key=lambda x: x["actual_impact_score"], reverse=True)

            class_impact_score = 0.0
            formatted_logical_objects = []
            
            for i, lo in enumerate(logical_objects):
                # Normalized impact score through harmonic decay within the same class for a commit
                normalized_score = lo["actual_impact_score"] / (i + 1)
                class_impact_score += normalized_score
                formatted_logical_objects.append({
                    "logical_object_name": lo["logical_object_name"],
                    "actual_impact_score": lo["actual_impact_score"],
                    "normalized_impact_score": normalized_score
                })

            commit_obj["classes"].append({
                "class_name": class_name,
                "project_name": class_data["project_name"],
                "impact_score": class_impact_score,
                "logical_objects": formatted_logical_objects
            })
            # Linear sum of impact scores across classes within the same commit
            commit_obj["impact_score"] += class_impact_score

        # Sort classes descending by their computed impact_score
        commit_obj["classes"].sort(key=lambda x: x["impact_score"], reverse=True)
        final_commits.append(commit_obj)

    # Sort commits descending by their total impact_score
    final_commits.sort(key=lambda x: x["impact_score"], reverse=True)

    audit_snapshot({"commit_aggregated_scores": final_commits}, "node_6b_commit_exporter", "After Aggregation", config)

    # Serialize census entries to JSON
    with open(final_census_path, "w", encoding="utf-8") as f:
        json.dump(census_entries, f, indent=2, ensure_ascii=False)

    file_size_kb = os.path.getsize(final_census_path) / 1024.0
    logger.info(f"Census exported to: {final_census_path} ({file_size_kb:.1f} KB)")

    # Serialize aggregated scores to JSON
    with open(final_aggregated_path, "w", encoding="utf-8") as f:
        json.dump(final_commits, f, indent=2, ensure_ascii=False)

    agg_file_size_kb = os.path.getsize(final_aggregated_path) / 1024.0
    logger.info(f"Commit aggregated scores exported to: {final_aggregated_path} ({agg_file_size_kb:.1f} KB)")

    # Impact Score Summary
    scored_entries = [e for e in census_entries if e.get("impact_score", 0) > 0]
    logger.info(f"Impact scoring summary:")
    logger.info(f"  Active entries with impact > 0: {len(scored_entries)}")
    logger.info(f"  Total Commits with impact > 0: {len(final_commits)}")

    if produce_log:
        logs.append(f"COMMIT EXPORTER: Wrote {len(census_entries)} entries to {final_census_path}.")
        with open(final_log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(logs) + "\n")
        logger.info(f"Extraction log written to: {final_log_path}")

    logger.info(f"Final census totals: {len(census_entries)} logical object entries.")

    shutdown_subprocesses()

    logger.info("Node 6B Finished. Pipeline complete.")

    return {
        **state,
        "report_aggregated_file": final_aggregated_path,
        "report_raw_file": final_census_path
    }
