"""
Node 2b: Remote Git Extractor (Azure DevOps PR-Aware)

Responsibilities:
- Iterate over commits_to_process from local branch history
- For each commit, extract the PR identifier from the commit title via regex
- Use Azure DevOps REST APIs to retrieve individual commits within each PR
- For each individual commit, retrieve file diffs (old_text / new_text) via the API
- Produce raw_diffs in the same format as node_2_git_extractor, with an added 'original_pr_id' field
"""

import os
import re
import base64
import time
import requests
from src.utils import execute_git, get_changed_line_numbers, logger, audit_snapshot


# ── Default PR title pattern ────────────────────────────────────────────────
DEFAULT_PR_PATTERN = r"Merged PR (\d+):"

# ── Retry configuration ─────────────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds


def _is_excluded_file(filepath: str) -> bool:
    """Exclude auto-generated files, tests, and designer files from analysis."""
    fp_lower = filepath.lower().replace("\\", "/")

    # Designer / resource generated files
    if fp_lower.endswith(".designer.cs") or ".g." in fp_lower:
        return True

    # Test files
    test_markers = (".test/", ".tests/", ".unittests/", "/test/", "/tests/")
    if any(marker in fp_lower for marker in test_markers):
        return True
    if fp_lower.endswith("test.cs") or fp_lower.endswith("tests.cs"):
        return True

    return False


def _create_session(devops_params: dict) -> requests.Session:
    """Create an authenticated requests.Session for Azure DevOps API calls."""
    env_var_name = devops_params.get("api_key_env_var", "DEVOPS_PAT")
    pat = os.environ.get(env_var_name)
    if not pat:
        raise ValueError(
            f"Environment variable '{env_var_name}' is not set. "
            f"Please set it to your Azure DevOps Personal Access Token."
        )

    credentials = f":{pat}"
    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Basic {encoded_credentials}",
        "Accept": "application/json",
    })
    return session


def _api_get(session: requests.Session, url: str, description: str = "") -> dict | None:
    """
    Perform a GET request with retry logic and exponential backoff.
    Returns the parsed JSON response, or None on failure.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 404:
                logger.warning(f"  API 404 Not Found: {description} — {url}")
                return None
            logger.warning(
                f"  API HTTP error (attempt {attempt}/{MAX_RETRIES}): "
                f"{description} — {e}"
            )
        except requests.exceptions.RequestException as e:
            logger.warning(
                f"  API request error (attempt {attempt}/{MAX_RETRIES}): "
                f"{description} — {e}"
            )

        if attempt < MAX_RETRIES:
            wait_time = RETRY_BACKOFF_BASE ** attempt
            logger.info(f"  Retrying in {wait_time}s...")
            time.sleep(wait_time)

    logger.error(f"  API call failed after {MAX_RETRIES} retries: {description} — {url}")
    return None


def _api_get_text(session: requests.Session, url: str, description: str = "") -> str:
    """
    Perform a GET request that returns raw text content (not JSON).
    Used for fetching file content from the Azure DevOps Items endpoint.
    The Accept header is overridden to text/plain per-request to ensure raw file
    content is returned even though the session default is application/json.
    Returns the text content, or empty string on failure.
    """
    text_headers = {"Accept": "text/plain"}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, headers=text_headers, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.exceptions.HTTPError as e:
            if response.status_code == 404:
                # File may not exist at this commit (new file or deleted file)
                return ""
            logger.warning(
                f"  API HTTP error (attempt {attempt}/{MAX_RETRIES}): "
                f"{description} — {e}"
            )
        except requests.exceptions.RequestException as e:
            logger.warning(
                f"  API request error (attempt {attempt}/{MAX_RETRIES}): "
                f"{description} — {e}"
            )

        if attempt < MAX_RETRIES:
            wait_time = RETRY_BACKOFF_BASE ** attempt
            time.sleep(wait_time)

    logger.error(f"  API text call failed after {MAX_RETRIES} retries: {description}")
    return ""


def node_2b_remote_git_extractor(state):
    logger.info("=" * 60)
    logger.info("NODE 2b: Remote Git Extractor (Azure DevOps PR-Aware)")
    logger.info("=" * 60)

    config = state["config"]
    commits = state["commits_to_process"]
    repo_path = config["repo_path"]
    commit_eval = config.get("commit_evaluation_parameters", {})
    devops_params = commit_eval.get("devops_parameters", {})

    if not commits:
        logger.warning("No commits to process.")
        return {"raw_diffs": []}

    # ── Build API base URL and session ───────────────────────────────────────
    organization = devops_params.get("organization", "")
    project = devops_params.get("project", "")
    repository = devops_params.get("repository", "")

    if not all([organization, project, repository]):
        raise ValueError(
            "Missing Azure DevOps parameters: organization, project, and repository are required."
        )

    base_url = f"https://dev.azure.com/{organization}/{project}/_apis"
    session = _create_session(devops_params)

    # ── PR title regex ───────────────────────────────────────────────────────
    pr_pattern_str = devops_params.get("pr_title_pattern", DEFAULT_PR_PATTERN)
    try:
        pr_pattern = re.compile(pr_pattern_str)
    except re.error as e:
        logger.error(f"Invalid pr_title_pattern regex: '{pr_pattern_str}' — {e}. Falling back to default.")
        pr_pattern = re.compile(DEFAULT_PR_PATTERN)

    raw_diffs = []
    discarded_commits = []
    discarded_files = []
    logs = state.get("extraction_logs", [])

    processed_prs = set()  # Avoid processing the same PR twice if multiple squash commits reference it

    for i, commit_hash in enumerate(commits):
        if i % 50 == 0 and i > 0:
            logger.info(f"  Progress: {i}/{len(commits)} branch commits scanned...")

        # Get commit title from local git
        commit_title = execute_git(
            f'git show -s --format=%s {commit_hash}', cwd=repo_path, check=False
        )
        commit_title = commit_title.strip() if commit_title else ""

        # Try to extract PR ID from commit title
        match = pr_pattern.search(commit_title)
        if not match:
            discard_msg = (
                f"  DISCARDED commit {commit_hash[:8]}: PR ID extraction failed "
                f"from title '{commit_title}'. Skipping."
            )
            logger.info(discard_msg)
            logs.append(discard_msg)
            discarded_commits.append({
                "commit_hash": commit_hash,
                "commit_title": commit_title,
                "reason": "pr_id_extraction_failed",
            })
            continue

        pr_id = int(match.group(1))

        # Skip if we already processed this PR
        if pr_id in processed_prs:
            logs.append(f"  PR {pr_id} already processed (duplicate squash commit {commit_hash[:8]}). Skipping.")
            continue

        processed_prs.add(pr_id)
        logger.info(f"  Processing PR #{pr_id} (from squash commit {commit_hash[:8]}: '{commit_title}')")
        logs.append(f"PR PROCESSING: id={pr_id}, squash_commit={commit_hash}, title='{commit_title}'")

        # ── Fetch commits for this PR ────────────────────────────────────────
        pr_commits_url = (
            f"{base_url}/git/repositories/{repository}"
            f"/pullRequests/{pr_id}/commits?api-version=7.1"
        )
        pr_commits_data = _api_get(session, pr_commits_url, f"PR #{pr_id} commits")
        if pr_commits_data is None:
            logs.append(f"  FAILED to fetch commits for PR #{pr_id}. Skipping.")
            discarded_commits.append({
                "commit_hash": commit_hash,
                "pr_id": pr_id,
                "reason": "api_fetch_pr_commits_failed",
            })
            continue

        pr_individual_commits = pr_commits_data.get("value", [])
        logger.info(f"    PR #{pr_id}: Found {len(pr_individual_commits)} individual commit(s).")
        logs.append(f"  PR #{pr_id}: {len(pr_individual_commits)} individual commits found.")

        # ── Process each individual commit ───────────────────────────────────
        for pr_commit in pr_individual_commits:
            individual_commit_id = pr_commit.get("commitId", "")
            individual_comment = pr_commit.get("comment", "No description").strip()

            # Extract commit date from the API response
            raw_date = pr_commit.get("author", {}).get("date", "")
            commit_date = raw_date if raw_date else ""

            logs.append(
                f"    COMMIT PROCESSING: hash={individual_commit_id[:8]}, "
                f"date={commit_date}, desc='{individual_comment}'"
            )

            # ── Fetch changed files for this commit ──────────────────────────
            changes_url = (
                f"{base_url}/git/repositories/{repository}"
                f"/commits/{individual_commit_id}/changes?api-version=7.1"
            )
            changes_data = _api_get(session, changes_url, f"Commit {individual_commit_id[:8]} changes")
            if changes_data is None:
                logs.append(f"    COMMIT {individual_commit_id[:8]}: Failed to fetch changes. Skipping.")
                continue

            changes = changes_data.get("changes", [])
            if not changes:
                logs.append(f"    COMMIT {individual_commit_id[:8]}: No changed files found.")
                continue

            # Determine parent commit ID for old_text retrieval
            parents = changes_data.get("parents", [])
            # The changes endpoint may not provide parents directly; fall back to commit details
            parent_commit_id = ""
            if parents:
                parent_commit_id = parents[0] if isinstance(parents[0], str) else parents[0].get("commitId", "")

            # If parents not available from changes, fetch from the commit detail
            if not parent_commit_id:
                commit_detail_url = (
                    f"{base_url}/git/repositories/{repository}"
                    f"/commits/{individual_commit_id}?api-version=7.1"
                )
                commit_detail = _api_get(session, commit_detail_url, f"Commit {individual_commit_id[:8]} detail")
                if commit_detail:
                    parent_ids = commit_detail.get("parents", [])
                    if parent_ids:
                        parent_commit_id = parent_ids[0]

            for change in changes:
                item = change.get("item", {})
                file_path = item.get("path", "")
                change_type = change.get("changeType", "").lower()

                # Skip non-.cs files
                if not file_path.endswith(".cs"):
                    continue

                # Apply exclusion filter
                if _is_excluded_file(file_path):
                    discarded_files.append({
                        "commit_hash": individual_commit_id,
                        "file_path": file_path,
                        "reason": "excluded_file_type",
                    })
                    logs.append(f"    DISCARDED file (excluded): {file_path}")
                    continue

                # ── Fetch file content ───────────────────────────────────────
                # $format=text forces the Items API to return raw file content.
                # The _api_get_text function also overrides Accept: text/plain per-request.

                # new_text: file at the current commit version
                new_text = ""
                if change_type != "delete":
                    new_item_url = (
                        f"{base_url}/git/repositories/{repository}"
                        f"/items?path={file_path}"
                        f"&version={individual_commit_id}&versionType=commit"
                        f"&$format=text&api-version=7.1"
                    )
                    new_text = _api_get_text(session, new_item_url, f"new_text for {file_path}")

                # old_text: file at the parent commit version
                old_text = ""
                if parent_commit_id and change_type != "add":
                    old_item_url = (
                        f"{base_url}/git/repositories/{repository}"
                        f"/items?path={file_path}"
                        f"&version={parent_commit_id}&versionType=commit"
                        f"&$format=text&api-version=7.1"
                    )
                    old_text = _api_get_text(session, old_item_url, f"old_text for {file_path}")

                if not old_text and not new_text:
                    discarded_files.append({
                        "commit_hash": individual_commit_id,
                        "file_path": file_path,
                        "reason": "no_content",
                    })
                    logs.append(f"    DISCARDED file (no content): {file_path}")
                    continue

                if old_text == new_text:
                    discarded_files.append({
                        "commit_hash": individual_commit_id,
                        "file_path": file_path,
                        "reason": "no_csharp_changes_identical",
                    })
                    logs.append(f"    DISCARDED file (no C# changes - identical texts): {file_path}")
                    continue

                # Compute exact changed line numbers
                old_lines, new_lines = get_changed_line_numbers(old_text, new_text)

                if not old_lines and not new_lines:
                    discarded_files.append({
                        "commit_hash": individual_commit_id,
                        "file_path": file_path,
                        "reason": "no_changed_line_coordinates",
                    })
                    logs.append(f"    DISCARDED file (no changed line coordinates): {file_path}")
                    continue

                logs.append(f"    COLLECTED file: {file_path}")
                raw_diffs.append({
                    "commit_hash": individual_commit_id,
                    "commit_date": commit_date,
                    "commit_description": individual_comment,
                    "file_path": file_path,
                    "old_text": old_text,
                    "new_text": new_text,
                    "old_lines": old_lines,
                    "new_lines": new_lines,
                    "original_pr_id": pr_id,
                })

    logger.info(f"Extracted {len(raw_diffs)} raw diff payloads from {len(processed_prs)} PRs.")
    logger.info(f"Discarded {len(discarded_commits)} commits (PR ID extraction or API failures).")
    logger.info("Node 2b (Remote) Finished.")

    output_state = {
        "raw_diffs": raw_diffs,
        "extraction_logs": logs,
    }
    audit_snapshot({
        "total_raw_diffs_extracted": len(raw_diffs),
        "total_prs_processed": len(processed_prs),
        "discarded_commits": discarded_commits,
        "discarded_files": discarded_files,
    }, "node_2b_remote_git_extractor", "Remote Git Extraction Summary", config)
    return output_state
