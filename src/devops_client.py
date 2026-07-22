import requests
import base64
import os
import re
import json
import time
from src.utils import logger

class DevOpsClient:
    def __init__(self, config):
        self.config = config
        devops_params = config.get("devops_parameters")
        if not devops_params:
            pipeline_mode = config.get("pipeline_mode", "")
            mode_params = config.get(f"{pipeline_mode}_parameters", {})
            devops_params = mode_params.get("devops_parameters", {})
            
        self.organization = devops_params.get("organization")
        self.project = devops_params.get("project")
        self.repository = devops_params.get("repository")
        self.api_key_env_var = devops_params.get("api_key_env_var", "DEVOPS_PAT")
        self.pr_title_pattern = devops_params.get("pr_title_pattern", r"Merged PR (\d+):")
        
        self.pat = devops_params.get("pat", "") or os.environ.get(self.api_key_env_var, "")
        if not self.pat:
            logger.warning(f"DevOps PAT not found in config.yaml ('devops_parameters.pat') or environment variable '{self.api_key_env_var}'. DevOps API calls will likely fail.")
        else:
            logger.info("DevOps PAT successfully loaded.")
        credentials = f":{self.pat}"
        encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
        
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Basic {encoded_credentials}',
            'Accept': 'application/json'
        })
        
        self.base_url = f"https://dev.azure.com/{self.organization}/{self.project}/_apis"
        
        # Setup Cache
        self.cache_folder = config.get("pr_evaluation_cache_folder", "cache/pr_evaluation")
        os.makedirs(self.cache_folder, exist_ok=True)
        self.cache_file = os.path.join(self.cache_folder, "pr_cache.json")
        self.cache = self._load_cache()

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load DevOps PR cache: {e}")
        return {}

    def _save_cache(self):
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.cache, f)
        except Exception as e:
            logger.error(f"Failed to save DevOps PR cache: {e}")

    def extract_pr_id(self, commit_message):
        match = re.search(self.pr_title_pattern, commit_message)
        if match:
            return match.group(1)
        return None

    def _get_with_retry(self, url, max_retries=5):
        retry_delay = 1
        for attempt in range(max_retries):
            try:
                response = self.session.get(url)
                if response.status_code == 429: # Rate limit
                    logger.warning(f"Rate limited (429). Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    logger.error(f"API request failed after {max_retries} attempts: {e}")
                    raise
                logger.warning(f"API request failed: {e}. Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2
        return None

    def is_pr_bug_fix(self, pr_id):
        pr_id_str = str(pr_id)
        if pr_id_str in self.cache:
            return self.cache[pr_id_str]

        try:
            wi_url = f"{self.base_url}/git/repositories/{self.repository}/pullRequests/{pr_id}/workitems?api-version=7.1"
            response = self._get_with_retry(wi_url)
            if not response:
                return False
                
            work_items = response.json().get('value', [])
            
            if not work_items:
                self.cache[pr_id_str] = False
                self._save_cache()
                return False

            # Strict mode: ALL work items must be 'Bug'
            is_bug = True
            for wi in work_items:
                wi_id = wi.get('id')
                wi_detail_url = f"{self.base_url}/wit/workitems/{wi_id}?api-version=7.1"
                detail_response = self._get_with_retry(wi_detail_url)
                if not detail_response:
                    is_bug = False
                    break
                    
                wi_type = detail_response.json().get('fields', {}).get('System.WorkItemType', 'Unknown')
                if wi_type.lower() != 'bug':
                    is_bug = False
                    break
            
            self.cache[pr_id_str] = is_bug
            self._save_cache()
            return is_bug

        except Exception as e:
            logger.error(f"Failed to evaluate PR {pr_id}: {e}")
            return False

    def get_pr_commits(self, pr_id):
        pr_id_str = str(pr_id)
        cache_key = f"{pr_id_str}_commits"
        if cache_key in self.cache:
            return self.cache[cache_key]

        try:
            commits_url = f"{self.base_url}/git/repositories/{self.repository}/pullRequests/{pr_id}/commits?api-version=7.1"
            response = self._get_with_retry(commits_url)
            if not response:
                return []
                
            commits_data = response.json().get('value', [])
            commit_hashes = [commit.get('commitId') for commit in commits_data if commit.get('commitId')]
            
            # The API might return them in any order, usually chronological or reverse chronological.
            # We want them in chronological order. We can reverse if needed, or assume they are returned in order.
            # Azure DevOps returns PR commits from most recent to oldest by default, so we reverse it.
            commit_hashes.reverse()

            self.cache[cache_key] = commit_hashes
            self._save_cache()
            return commit_hashes

        except Exception as e:
            logger.error(f"Failed to fetch commits for PR {pr_id}: {e}")
            return []
