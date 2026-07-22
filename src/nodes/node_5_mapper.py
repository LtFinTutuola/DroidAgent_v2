"""
Node 5: Mapper (Overlay, Time Decay & Impact Scoring)

Responsibilities:
- Ingest the Baseline (from Node 1b) and Filtered Historical Diffs (from Node 4).
- Temporarily convert the Baseline into a dictionary for O(1) lookups.
- Iterate chronologically over the historical diffs and overlay them onto the baseline.
- For each diff, calculate:
    1. Time Decay multiplier (weighted average of repo-lifespan and object-lifespan decay)
    2. Final Impact = diff_score × time_multiplier
    3. Accumulate impact_score per active method
- Dead Code Paradox: if a historical diff involves a method that no longer exists,
  DO NOT add it to the active method map. Instead, sum its final_impact to
  legacy_impact_score on the parent_object.
- Per-commit auditability: each commit entry stores a 'scores' sub-object with
  diff_score and lifespan_time_multiplier.

Neuro-Symbolic Engine Integration:
- For modifications (NOT is_new_or_dead, is_signature_change, is_field_modification),
  the diff_score is synthesized from 4 weighted dimensions:
    D_structural (GumTree), D_semantic (CodeBERT), D_dataflow (Tree-Sitter), D_complexity
- The convex weights are configurable in config.yaml under neuro_symbolic_weights.
- Sub-scores are logged to calculation_factors for full auditability.
"""

from datetime import datetime
from src.utils import logger, audit_snapshot
from src.nodes.node_1b_baseline_manager import get_project_name
from src.nodes.dataflow_tracer import DataFlowTracer
from src.nodes.neural_semantic_engine import NeuralSemanticEngine


def _parse_date(date_str):
    """
    Parse a Git commit date string to a datetime object.
    Handles ISO-like formats: '2024-01-15 10:30:00 +0200'
    Returns None if parsing fails.
    """
    if not date_str:
        return None
    try:
        # Git %ci format: '2024-01-15 10:30:00 +0200'
        # Strip the timezone offset for naive datetime comparison
        clean = date_str.strip()
        # Try ISO format first
        if "T" in clean:
            return datetime.fromisoformat(clean)
        # Git %ci format: remove timezone offset
        parts = clean.rsplit(" ", 1)
        if len(parts) == 2 and (parts[1].startswith("+") or parts[1].startswith("-")):
            return datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
        return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        logger.warning(f"Failed to parse date: '{date_str}', defaulting to None")
        return None


def calculate_time_multiplier(commit_date_str, repo_first_date_str, repo_last_date_str):
    """
    Calculate a Time Decay multiplier ∈ [0, 1].

    The most recent a diff is, the closer its multiplier is to 1.0.
    The older a diff is, the closer its multiplier is to 0.0.
    This penalizes ancient changes (now "framework" code) and highlights current "Hot Zones".

    Formula:
        lifespan_decay = (commit_date - repo_first) / (repo_last - repo_first)

    IMPORTANT: repo_first_date and repo_last_date must be derived from the FULL repository
    timeline (all commits), NOT from the filtered analysis window. Using the filtered window
    compresses all multipliers into a narrow range and penalizes early-window commits unfairly.

    Returns:
        float: lifespan_time_multiplier
    """
    commit_dt = _parse_date(commit_date_str)
    repo_first_dt = _parse_date(repo_first_date_str)
    repo_last_dt = _parse_date(repo_last_date_str)

    # Edge case: if any critical date is missing, return neutral multiplier
    if not commit_dt or not repo_first_dt or not repo_last_dt:
        return 1.0

    # ── Lifespan Decay ────────────────────────────────────────────────────
    # How old is this commit relative to the FULL repository history?
    total_span = (repo_last_dt - repo_first_dt).total_seconds()
    if total_span <= 0:
        # Single-commit repo or same-day first/last: treat as maximally recent
        lifespan_decay = 1.0
    else:
        commit_age = (commit_dt - repo_first_dt).total_seconds()
        # Clamp: a commit before the repo start or after repo end stays in [0, 1]
        lifespan_decay = max(0.0, min(1.0, commit_age / total_span))

    return lifespan_decay


def _load_neuro_symbolic_weights(config):
    """
    Load and validate the neuro-symbolic convex weights from config.

    Returns:
        tuple: (w_structural, w_semantic, w_dataflow, w_complexity)
    """
    weights_config = config.get("neuro_symbolic_weights", {})
    w_structural = float(weights_config.get("structural", 0.40))
    w_semantic = float(weights_config.get("semantic", 0.25))
    w_dataflow = float(weights_config.get("dataflow", 0.20))
    w_complexity = float(weights_config.get("complexity", 0.15))

    total = w_structural + w_semantic + w_dataflow + w_complexity
    if abs(total - 1.0) > 0.001:
        logger.error(
            f"Neuro-symbolic weights do not sum to 1.0 (sum={total:.4f}). "
            f"Normalizing to maintain convex combination."
        )
        w_structural /= total
        w_semantic /= total
        w_dataflow /= total
        w_complexity /= total

    return w_structural, w_semantic, w_dataflow, w_complexity


def node_5_mapper(state):
    logger.info("=" * 60)
    logger.info("NODE 5: Mapper (Overlay, Time Decay & Impact Scoring)")
    logger.info("=" * 60)

    config = state["config"]
    repo_path = config["repo_path"]
    baseline_objects = state.get("baseline_objects", [])
    parsed_hunks = state.get("parsed_hunks", [])
    logs = state.get("extraction_logs", [])

    # Removed beginning snapshot to reduce log size

    # ── Load Time Decay configuration ────────────────────────────────────
    # FLAW 4 FIX: Use the full-repository timeline (not the filtered window)
    # for time decay calibration so that the multiplier reflects the commit's
    # age relative to the entire project lifespan, not just the analysis slice.
    repo_first_date = state.get("repo_full_first_commit_date") or state.get("repo_first_commit_date", "")
    repo_last_date = state.get("repo_full_last_commit_date") or state.get("repo_last_commit_date", "")

    logger.info(f"Loaded {len(baseline_objects)} baseline objects.")
    logger.info(f"Loaded {len(parsed_hunks)} historical diff hunks.")
    logger.info(f"Repo temporal boundaries: first={repo_first_date}, last={repo_last_date}")

    # ── Load Neuro-Symbolic weights ──────────────────────────────────────
    w_struct, w_semantic, w_dataflow, w_complexity = _load_neuro_symbolic_weights(config)
    logger.info(
        f"Neuro-symbolic weights: structural={w_struct:.2f}, semantic={w_semantic:.2f}, "
        f"dataflow={w_dataflow:.2f}, complexity={w_complexity:.2f}"
    )

    # ── Initialize Neuro-Symbolic engines (lazy singletons) ──────────────
    dataflow_tracer = DataFlowTracer.get_instance()
    neural_engine = NeuralSemanticEngine.get_instance()

    # ── Convert Baseline to O(1) lookup dictionary ───────────────────────
    mapping_dict = {}
    for obj in baseline_objects:
        obj_id = obj["logical_object"]
        # Initialize impact scoring fields
        obj["impact_score"] = 0.0
        mapping_dict[obj_id] = obj

    dead_code_impacts = 0
    global_legacy_commits = {}

    # ── Process hunks chronologically ────────────────────────────────────
    for i, hunk in enumerate(parsed_hunks):
        if i % 50 == 0 and i > 0:
            logger.info(f"  Neural Semantic Mapper progress: {i}/{len(parsed_hunks)} hunks processed...")

        obj_id = hunk["logical_object"]
        commit_hash = hunk["commit_hash"]
        commit_date = hunk["commit_date"]
        commit_desc = hunk["commit_description"]
        file_path = hunk["file_path"]
        parent_obj = hunk["parent_object"]
        diff_score = hunk.get("diff_score", 0.0)

        # ── Neuro-Symbolic sub-scores (defaults) ─────────────────
        structural_score = hunk.get("structural_score", diff_score)
        semantic_score = 0.0
        dataflow_score = 0.0
        complexity_score = 0.0
        used_neuro_symbolic = False

        # ── Universal Neuro-Symbolic Engine Routing ──────────────
        clean_old = hunk.get("clean_old", "")
        clean_new = hunk.get("clean_new", "")
        obj_type = hunk.get("object_type", "method")

        if hunk.get("is_new_or_dead", False):
            # Short-circuit external evaluations for pure additions/deletions.
            # They naturally exhibit maximum divergence against a null tree.
            raw_struct = 1.0
            raw_semantic = 1.0
            raw_dataflow = 1.0
            raw_complex = 1.0

            # Apply dynamic magnitude scaling based on cognitive mass vs historical percentiles
            threshold_key = f"max_{obj_type}_threshold"
            specific_threshold = float(config.get(threshold_key, 17.0))
            raw_score = float(hunk.get("raw_complexity_score", 0))

            # FLAW 6 FIX: Guard against silent data loss when Roslyn fails to populate
            # raw_complexity_score. A missing score must not silently zero out the entry.
            if raw_score == 0:
                logger.warning(
                    f"  FLAW6-GUARD: Missing raw_complexity_score for is_new_or_dead hunk "
                    f"'{obj_id}'. Roslyn did not compute complexity. Using minimal floor (0.1) "
                    f"to prevent silent data loss."
                )
                scale_factor = 0.1  # Non-zero floor: the method exists and was added/deleted
            else:
                scale_factor = min(1.0, raw_score / max(1.0, specific_threshold))

            # Dynamically scale all 4 dimensions
            structural_score = raw_struct * scale_factor
            semantic_score = raw_semantic * scale_factor
            dataflow_score = raw_dataflow * scale_factor
            complexity_score = raw_complex * scale_factor
            
        else:
            # ── Standard Modification Evaluation ──
            
            # D_structural: already computed by Roslyn (GumTree)
            structural_score = hunk.get("structural_score", diff_score)

            # D_semantic: CodeBERT cosine divergence
            if clean_old and clean_new:
                semantic_score = neural_engine.compute_semantic_divergence(clean_old, clean_new)
            else:
                semantic_score = 0.0
            
            # D_dataflow: Tree-Sitter Jaccard distance
            if clean_old and clean_new:
                dataflow_score = dataflow_tracer.compute_dataflow_divergence(clean_old, clean_new)
            else:
                dataflow_score = 0.0

            # D_complexity: normalized complexity delta
            # FLAW 1 FIX: The previous implementation set complexity_score = structural_score * 0.5,
            # which is NOT a complexity measurement — it was a proxy that double-counted structural
            # information and made the effective structural weight 47.5% instead of 40%.
            #
            # The Roslyn server does not yet provide cognitive complexity for modified methods,
            # only for new/dead ones. Until that capability is added, we honestly set this to 0.0
            # for modifications rather than fabricating a value from structural data.
            #
            # Impact: the complexity weight (15%) is currently unused for modifications.
            # This is correct behavior — an honest zero is better than a dishonest proxy.
            complexity_score = 0.0

        # ── Convex synthesis ──────────────────────────────────
        diff_score = (
            w_struct * structural_score +
            w_semantic * semantic_score +
            w_dataflow * dataflow_score +
            w_complexity * complexity_score
        )
        diff_score = round(max(0.0, min(1.0, diff_score)), 6)
        used_neuro_symbolic = True


        # Ensure the project field is resolved dynamically
        project = get_project_name(file_path, repo_path)

        # ── Calculate Time Decay multiplier ──────────────────────────
        lifespan_mult = calculate_time_multiplier(
            commit_date, repo_first_date, repo_last_date
        )

        # ── Final Impact = diff_score × time_multiplier ──────────────
        final_impact = diff_score * lifespan_mult

        # ── Build per-commit auditability scores ─────────────────────
        diff_score_calculation_factors = {
            "structural_score": round(structural_score, 6),
            "semantic_score": round(semantic_score, 6),
            "dataflow_score": round(dataflow_score, 6),
            "complexity_score": round(complexity_score, 6)
        }

        calculation_factors = {
            "lifespan_time_multiplier": round(lifespan_mult, 6),
            "diff_score": round(diff_score, 6),
            "diff_score_calculation_factors": diff_score_calculation_factors
        }

        commit_obj = {
            "commit_hash": commit_hash,
            "commit_description": commit_desc,
            "commit_date": commit_date,
            "impact": {
                "final_impact_score": round(final_impact, 6),
                "calculation_factors": calculation_factors
            }
        }

        if obj_id in mapping_dict:
            # ── Active method: accumulate impact ─────────────────────
            target = mapping_dict[obj_id]
            target["commits"].append(commit_obj)
            target["impact_score"] += final_impact

            logs.append(
                f"  MAPPED active: {obj_id} | diff_score={diff_score:.4f} × "
                f"time_mult={lifespan_mult:.4f} = impact={final_impact:.4f}"
            )
        else:
            # ── Dead Code Paradox ────────────────────────────────────
            # The method no longer exists in the current codebase.
            # DO NOT add it to the active method map.
            # Instead, populate the new global state dictionary.
            dead_code_impacts += 1

            if not parent_obj:
                parts = obj_id.split(".")
                if len(parts) > 1:
                    parent_obj = ".".join(parts[:-1])
                else:
                    parent_obj = obj_id

            # Safe Cascading Initialization to prevent KeyErrors
            parent_dict = global_legacy_commits.setdefault(parent_obj, {
                "project": project,
                "commits": {}
            })
            
            # Ensure the project field is populated if it was initialized empty elsewhere
            if not parent_dict.get("project"):
                parent_dict["project"] = project
                
            commit_list = parent_dict["commits"].setdefault(commit_hash, [])
            commit_list.append(commit_obj)

            logs.append(
                f"  DEAD CODE: {obj_id} → legacy impact {final_impact:.4f} "
                f"added to global parent {parent_obj}"
            )

    logs.append(f"MAPPED {len(parsed_hunks)} hunks onto the baseline.")
    logs.append(f"DEAD CODE impacts distributed: {dead_code_impacts}")

    logger.info(f"Mapping complete. Dead code impacts: {dead_code_impacts}")
    logger.info("Node 5 Finished.")

    # Flatten the mapping_dict to a list before passing to Node 6
    final_census = list(mapping_dict.values())

    # ── Final Cleanup ──
    for entry in final_census:
        if not entry.get("commits"):
            entry.pop("raw_complexity_score", None)
            
        # Move 'commits' to the end for cosmetic ordering
        if "commits" in entry:
            commits_val = entry.pop("commits")
            entry["commits"] = commits_val

    output_state = {
        "census_entries": final_census,
        "global_legacy_commits": global_legacy_commits,
        "extraction_logs": logs
    }
    active_census = [entry for entry in final_census if entry.get("impact_score", 0) > 0]
    audit_snapshot({
        "mapped_census_entries": active_census,
        "global_legacy_commits": global_legacy_commits
    }, "node_5_mapper", "Active Scored Objects", config)
    return output_state
