#!/usr/bin/env python3
"""
sync_n8n.py — Syncs local workflow JSON to a running n8n instance.

Usage:
    python scripts/sync_n8n.py                  # one-shot sync
    python scripts/sync_n8n.py --watch          # watch for file changes and auto-sync

Requirements:
    pip install requests watchdog python-dotenv

n8n API key:
    n8n Settings → API → Create API Key
    Set N8N_API_KEY in .env or as env var.

n8n base URL defaults to http://localhost:5678.
Set N8N_BASE_URL in .env to override.
"""

import json
import os
import sys
import time
import argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed. Run: pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env optional

# ── Config ───────────────────────────────────────────────────────────────────

N8N_BASE_URL   = os.getenv("N8N_BASE_URL", "http://localhost:5678")
N8N_API_KEY    = os.getenv("N8N_API_KEY", "")
WORKFLOW_FILE  = Path(__file__).parent.parent / "n8n" / "workflow_ciri_agent.json"
WORKFLOW_NAME  = "CIRI Chargeback Agent"  # used to find existing workflow by name

# ── Helpers ───────────────────────────────────────────────────────────────────

def _headers() -> dict:
    if not N8N_API_KEY:
        print("WARNING: N8N_API_KEY not set. Most n8n instances require it.")
    return {
        "X-N8N-API-KEY": N8N_API_KEY,
        "Content-Type": "application/json",
    }


def _load_workflow() -> dict:
    with open(WORKFLOW_FILE, encoding="utf-8") as f:
        return json.load(f)


def _find_workflow_id(session: "requests.Session") -> str | None:
    """Return the ID of the first workflow whose name matches WORKFLOW_NAME."""
    url = f"{N8N_BASE_URL}/api/v1/workflows"
    r = session.get(url, headers=_headers())
    r.raise_for_status()
    for wf in r.json().get("data", []):
        if wf.get("name") == WORKFLOW_NAME:
            return str(wf["id"])
    return None


def sync(session: "requests.Session") -> bool:
    """Push local JSON to n8n. Returns True on success."""
    payload = _load_workflow()

    wf_id = _find_workflow_id(session)

    if wf_id:
        # Update existing
        url = f"{N8N_BASE_URL}/api/v1/workflows/{wf_id}"
        r = session.put(url, headers=_headers(), json=payload)
    else:
        # Create new
        url = f"{N8N_BASE_URL}/api/v1/workflows"
        r = session.post(url, headers=_headers(), json=payload)

    if r.status_code in (200, 201):
        action = "updated" if wf_id else "created"
        wf_id_result = r.json().get("id", wf_id or "?")
        print(f"[OK] Workflow {action} — id={wf_id_result}  →  {N8N_BASE_URL}/workflow/{wf_id_result}")
        return True
    else:
        print(f"[ERROR] {r.status_code}: {r.text[:300]}")
        return False


# ── Watch mode ────────────────────────────────────────────────────────────────

def watch(session: "requests.Session") -> None:
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("ERROR: 'watchdog' not installed. Run: pip install watchdog")
        sys.exit(1)

    print(f"[WATCH] Monitoring {WORKFLOW_FILE}")
    print("        Press Ctrl+C to stop.\n")

    class Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if Path(event.src_path).resolve() == WORKFLOW_FILE.resolve():
                print(f"\n[CHANGE] {WORKFLOW_FILE.name} modified — syncing…")
                sync(session)

    observer = Observer()
    observer.schedule(Handler(), str(WORKFLOW_FILE.parent), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync n8n workflow from local JSON")
    parser.add_argument("--watch", action="store_true", help="Watch file and auto-sync on change")
    parser.add_argument("--url",   default=N8N_BASE_URL, help=f"n8n base URL (default: {N8N_BASE_URL})")
    parser.add_argument("--key",   default=N8N_API_KEY,  help="n8n API key (default: $N8N_API_KEY)")
    args = parser.parse_args()

    global N8N_BASE_URL, N8N_API_KEY
    N8N_BASE_URL = args.url.rstrip("/")
    N8N_API_KEY  = args.key

    if not WORKFLOW_FILE.exists():
        print(f"ERROR: Workflow file not found: {WORKFLOW_FILE}")
        sys.exit(1)

    session = requests.Session()

    # One-shot sync first
    ok = sync(session)
    if not ok and not args.watch:
        sys.exit(1)

    if args.watch:
        watch(session)


if __name__ == "__main__":
    main()
