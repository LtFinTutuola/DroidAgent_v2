"""
Node 6: Exporter (Final Dump & Teardown)

Responsibilities:
- Serialize the census_dictionary to the output JSON file
- Gracefully terminate all persistent subprocesses (GitBatcher, RoslynServer)
- Log final summary statistics
"""

import os
import json
from datetime import datetime
from src.utils import shutdown_subprocesses, logger, audit_snapshot


def node_6_exporter(state):
    logger.info("=" * 60)
    logger.info("NODE 6: Exporter (Final Dump & Teardown)")
    logger.info("=" * 60)

    config = state["config"]
    census_entries = state.get("census_entries", [])
    output_path = config.get("output_json_path", "output/pr_census.json")
    produce_log = config.get("produce_log", False)
    logs = state.get("extraction_logs", [])

    # ── Ensure impact scoring fields are present on every entry ──────────
    for entry in census_entries:
        if "impact_score" not in entry:
            entry["impact_score"] = 0.0

    # ── Ensure output directory exists ───────────────────────────────────
    output_dir = os.path.dirname(output_path) or "output"
    os.makedirs(output_dir, exist_ok=True)

    # ── Timestamped Filenames ────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    census_filename = f"{timestamp}_code_mapping.json"
    log_filename = f"{timestamp}_log.txt"
    aggregated_filename = f"{timestamp}_aggregated_scores.json"

    final_census_path = os.path.join(output_dir, census_filename)
    final_log_path = os.path.join(output_dir, log_filename)
    final_aggregated_path = os.path.join(output_dir, aggregated_filename)

    # ── Phase 1-5: Aggregation Logic ─────────────────────────────────────
    projects_data = {}
    
    global_legacy_commits = state.get("global_legacy_commits", {})
    
    report_config = config.get("report_generation", {})
    analyze_class_level = report_config.get("analyze_class_level", False)
    target_classes = report_config.get("classes", [])

    for entry in census_entries:
        proj_name = entry.get("project", "Unknown Project")
        parent_obj = entry.get("parent_object", "")
        log_obj = entry.get("logical_object", "")
        class_name = parent_obj if parent_obj else log_obj
        
        if analyze_class_level:
            if class_name not in target_classes:
                continue
            group_key_1 = class_name
            group_key_2 = log_obj
        else:
            group_key_1 = proj_name
            group_key_2 = class_name

        if group_key_1 not in projects_data:
            projects_data[group_key_1] = {}
            
        if group_key_2 not in projects_data[group_key_1]:
            projects_data[group_key_1][group_key_2] = {
                "class_name": group_key_2,
                "logical_objects": [],
                "active_commits": {},
                "legacy_commits": {},
                "base_legacy_score": 0.0
            }
            
        class_data = projects_data[group_key_1][group_key_2]
        class_data["logical_objects"].append(entry)
        
        for commit in entry.get("commits", []):
            chash = commit.get("commit_hash")
            if not chash:
                continue
                
            impact = commit.get("impact", {}).get("final_impact_score", 0.0)
            
            if impact > 0:
                if chash not in class_data["active_commits"]:
                    class_data["active_commits"][chash] = {
                        "commit_hash": chash,
                        "commit_description": commit.get("commit_description", ""),
                        "commit_date": commit.get("commit_date", ""),
                        "impacts": []
                    }
                class_data["active_commits"][chash]["impacts"].append(impact)

    # Process global_legacy_commits
    if not analyze_class_level:
        for class_name, legacy_data in global_legacy_commits.items():
            proj_name = legacy_data.get("project", "Unknown Project")
            if proj_name not in projects_data:
                projects_data[proj_name] = {}
                
            if class_name not in projects_data[proj_name]:
                projects_data[proj_name][class_name] = {
                    "class_name": class_name,
                    "logical_objects": [],
                    "active_commits": {},
                    "legacy_commits": {},
                    "base_legacy_score": 0.0
                }
                
            class_data = projects_data[proj_name][class_name]
            
            for chash, commit_list in legacy_data.get("commits", {}).items():
                for commit in commit_list:
                    impact = commit.get("impact", {}).get("final_impact_score", 0.0)
                    
                    if impact > 0:
                        if chash not in class_data["legacy_commits"]:
                            class_data["legacy_commits"][chash] = {
                                "commit_hash": chash,
                                "commit_description": commit.get("commit_description", ""),
                                "commit_date": commit.get("commit_date", ""),
                                "impacts": []
                            }
                        class_data["legacy_commits"][chash]["impacts"].append(impact)

    def calculate_harmonic_score(impacts):
        sorted_impacts = sorted(impacts, reverse=True)
        return sum(score / (i + 1) for i, score in enumerate(sorted_impacts))

    final_projects = []
    
    for proj_name, classes_dict in projects_data.items():
        project_obj = {
            "project_name": proj_name,
            "impact_score": 0.0,
            "classes": []
        }
        
        for class_name, class_data in classes_dict.items():
            active_score = 0.0
            commit_contributions = []
            
            for chash, cdata in class_data["active_commits"].items():
                h_score = calculate_harmonic_score(cdata["impacts"])
                active_score += h_score
                commit_contributions.append({
                    "commit_hash": cdata["commit_hash"],
                    "commit_description": cdata["commit_description"],
                    "commit_date": cdata["commit_date"],
                    "impact_score": h_score
                })
                
            # Rank descending by impact_score
            commit_contributions.sort(key=lambda x: x["impact_score"], reverse=True)
            
            legacy_score = class_data["base_legacy_score"]
            for chash, cdata in class_data["legacy_commits"].items():
                legacy_score += calculate_harmonic_score(cdata["impacts"])
                
            class_obj = {
                "class_name": class_name,
                "impact_score": active_score,
                "legacy_impact_score": legacy_score,
                "logical_objects_count": len(class_data["logical_objects"]),
                "commit_contributions": commit_contributions
            }
            
            project_obj["classes"].append(class_obj)
            project_obj["impact_score"] += active_score
            
        final_projects.append(project_obj)

    audit_snapshot({"aggregated_scores": final_projects}, "node_6_exporter", "After Aggregation", config)

    # ── Serialize census entries to JSON ─────────────────────────────────
    with open(final_census_path, "w", encoding="utf-8") as f:
        json.dump(census_entries, f, indent=2, ensure_ascii=False)

    file_size_kb = os.path.getsize(final_census_path) / 1024.0
    logger.info(f"Census exported to: {final_census_path} ({file_size_kb:.1f} KB)")

    # ── Serialize aggregated scores to JSON ──────────────────────────────
    with open(final_aggregated_path, "w", encoding="utf-8") as f:
        json.dump(final_projects, f, indent=2, ensure_ascii=False)

    agg_file_size_kb = os.path.getsize(final_aggregated_path) / 1024.0
    logger.info(f"Aggregated scores exported to: {final_aggregated_path} ({agg_file_size_kb:.1f} KB)")

    # ── Impact Score Summary ─────────────────────────────────────────────
    scored_entries = [e for e in census_entries if e.get("impact_score", 0) > 0]
    global_legacy_commits = state.get("global_legacy_commits", {})
    legacy_classes_count = len(global_legacy_commits)

    logger.info(f"Impact scoring summary:")
    logger.info(f"  Active entries with impact > 0: {len(scored_entries)}")
    logger.info(f"  Classes with legacy impact > 0: {legacy_classes_count}")
    if scored_entries:
        max_impact = max(e["impact_score"] for e in scored_entries)
        avg_impact = sum(e["impact_score"] for e in scored_entries) / len(scored_entries)
        logger.info(f"  Max cumulative impact: {max_impact:.4f}")
        logger.info(f"  Avg cumulative impact: {avg_impact:.4f}")

    # ── Write log if configured ──────────────────────────────────────────
    if produce_log:
        logs.append(f"EXPORTER: Wrote {len(census_entries)} entries to {final_census_path}.")
        with open(final_log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(logs) + "\n")
        logger.info(f"Extraction log written to: {final_log_path}")

    # ── Summary ──────────────────────────────────────────────────────────
    logger.info(f"Final census totals: {len(census_entries)} logical object entries.")

    # ── Teardown: terminate subprocesses ─────────────────────────────────
    shutdown_subprocesses()

    logger.info("Node 6 Finished. Pipeline complete.")

    return {
        **state,
        "report_aggregated_file": final_aggregated_path,
        "report_raw_file": final_census_path
    }
