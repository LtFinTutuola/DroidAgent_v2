import requests
import base64
from datetime import datetime

# Configurazione parametri
organization = "zucchetti-tcpos"
project = "V4"
repository_id = "core"
pull_request_id = 104913  # Sostituisci con l'ID della tua PR
pat = "BCndcL8So1dhwAPC6H3WnVD8Xp6UEN3qW8wNk5nWz4184aNqyK8OJQQJ99CFACAAAAACCOCXAAASAZDO46YV"

# Configurazione Autenticazione Basic
credentials = f":{pat}"
encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

# Utilizziamo una Session per ottimizzare le performance (keep-alive delle connessioni)
session = requests.Session()
session.headers.update({
    'Authorization': f'Basic {encoded_credentials}',
    'Accept': 'application/json'
})

# Base URL per le API Git e Work Items
base_url = f"https://dev.azure.com/{organization}/{project}/_apis"

try:
    print(f"=== ANALISI PULL REQUEST #{pull_request_id} ===\n")

    # 1. Recupero i Work Item associati alla Pull Request
    wi_url = f"{base_url}/git/repositories/{repository_id}/pullRequests/{pull_request_id}/workitems?api-version=7.1"
    wi_response = session.get(wi_url)
    wi_response.raise_for_status()
    
    work_items = wi_response.json().get('value', [])
    
    print("Work Item Collegati alla PR:")
    if not work_items:
        print("  (Nessun Work Item associato a questa Pull Request)")
    else:
        for wi in work_items:
            wi_id = wi.get('id')
            # L'endpoint della PR restituisce solo URL e ID. Per sapere se è Bug o Feature, 
            # interroghiamo il dettaglio del singolo Work Item
            wi_detail_url = f"{base_url}/wit/workitems/{wi_id}?api-version=7.1"
            wi_detail_response = session.get(wi_detail_url)
            
            if wi_detail_response.status_code == 200:
                wi_fields = wi_detail_response.json().get('fields', {})
                wi_type = wi_fields.get('System.WorkItemType', 'Unknown')
                wi_title = wi_fields.get('System.Title', 'Nessun Titolo')
                
                # Evidenziamo visivamente se si tratta di Bug o Feature
                print(f"  - [{wi_type.upper()}] #{wi_id}: {wi_title}")
            else:
                print(f"  - [ID: {wi_id}] Impossibile recuperare i dettagli del Work Item.")
    
    print("\n" + "="*60 + "\n")

    # 2. Recupero i commit associati alla Pull Request
    pr_commits_url = f"{base_url}/git/repositories/{repository_id}/pullRequests/{pull_request_id}/commits?api-version=7.1"
    response = session.get(pr_commits_url)
    response.raise_for_status()

    commits = response.json().get('value', [])
    print(f"Dettaglio storico dei Commit eliminati (ex-branch):\n")

    for commit in commits:
        full_commit_id = commit.get('commitId', '')
        short_id = full_commit_id[:8]
        comment = commit.get('comment', 'Nessuna descrizione').strip()
        
        # Gestione e formattazione della data
        raw_date = commit.get('author', {}).get('date', '')
        if raw_date:
            formatted_date = datetime.fromisoformat(raw_date.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S')
        else:
            formatted_date = "Data non disponibile"

        print(f"Commit: [{short_id}] | {formatted_date}")
        print(f"Descrizione: {comment}")
        print("File modificati:")

        # 3. Per ogni commit, recupero l'elenco dei file impattati
        changes_url = f"{base_url}/git/repositories/{repository_id}/commits/{full_commit_id}/changes?api-version=7.1"
        changes_response = session.get(changes_url)
        
        if changes_response.status_code == 200:
            changes = changes_response.json().get('changes', [])
            if not changes:
                print("  (Nessun file modificato)")
            for change in changes:
                path = change.get('item', {}).get('path', 'Unknown')
                change_type = change.get('changeType', 'unknown')
                print(f"  [{change_type.upper():<6}] {path}")
        else:
            print(f"  [ERRORE] Impossibile recuperare i file per questo commit.")
        
        print("-" * 40)

except requests.exceptions.RequestException as e:
    print(f"Errore di comunicazione con le API di Azure DevOps: {e}")
except Exception as e:
    print(f"Errore imprevisto nello script: {e}")