import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple

import requests

STATE_PATH = "state.json"
BOARDS_PATH = "boards.txt"
TIMEOUT_SECONDS = 25

# Basic safety: identify ourselves politely
HEADERS = {
    "User-Agent": "ashby-job-scanner/1.0 (+https://github.com/)"
}

def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)

def load_boards() -> List[str]:
    boards: List[str] = []
    with open(BOARDS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            slug = line.strip()
            if not slug or slug.startswith("#"):
                continue
            # allow full URL too; extract slug from https://jobs.ashbyhq.com/<slug>
            m = re.match(r"^https?://jobs\.ashbyhq\.com/([^/\s]+)", slug)
            if m:
                slug = m.group(1)
            boards.append(slug)
    return boards

def fetch_board(slug: str) -> Dict[str, Any]:
    # Public Ashby endpoint
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
    r.raise_for_status()
    return r.json()

def simplify_jobs(board_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    jobs = board_data.get("jobs", []) or []
    simplified: List[Dict[str, Any]] = []
    for j in jobs:
        job_id = j.get("id") or j.get("_id") or j.get("jobId")
        title = j.get("title")
        if not job_id or not title:
            continue
        simplified.append({
            "id": str(job_id),
            "title": title,
            "publishedAt": j.get("publishedAt"),
            "location": j.get("location"),
            "department": j.get("department"),
            "team": j.get("team"),
            "jobUrl": j.get("jobUrl") or j.get("applyUrl"),
        })
    return simplified

def main() -> None:
    state = load_json(STATE_PATH, default={})
    # Detect first run: no boards stored yet (only empty or just _meta)
    existing_board_keys = [k for k in state.keys() if not k.startswith("_")]
    first_run = (len(existing_board_keys) == 0)

    boards = load_boards()

    # state structure:
    # {
    #   "<slug>": ["jobid1","jobid2",...],
    #   "_meta": {...}
    # }
    all_new: List[Tuple[str, List[Dict[str, Any]]]] = []
    any_errors: List[str] = []

    for slug in boards:
        prev_ids = set(state.get(slug, []))

        try:
            data = fetch_board(slug)
            jobs = simplify_jobs(data)
        except Exception as e:
            any_errors.append(f"{slug}: {e}")
            continue

        current_ids = set(j["id"] for j in jobs)
        new_ids = current_ids - prev_ids

        if new_ids:
            new_jobs = [j for j in jobs if j["id"] in new_ids]
            # best-effort: newest first
            new_jobs.sort(key=lambda x: (x.get("publishedAt") or ""), reverse=True)
            all_new.append((slug, new_jobs))

        # store compact list of ids
        state[slug] = sorted(list(current_ids))

    state["_meta"] = {
        "updatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "boardsCount": len(boards),
        "errorsCount": len(any_errors),
    }
    save_json(STATE_PATH, state)

    # Write a summary file for the workflow step to read
    summary = {
        "firstRun": first_run,
        "new": [
            {"board": slug, "jobs": jobs}
            for slug, jobs in all_new
        ],
        "errors": any_errors,
    }

    save_json("run_summary.json", summary)

    if any_errors:
        print("Some boards failed:")
        for e in any_errors:
            print(" -", e)

    if all_new:
        print("\nNEW JOBS FOUND:")
        for slug, jobs in all_new:
            print(f"\n== {slug} ==")
            for j in jobs:
                print(f"- {j['title']} | {j.get('location')} | {j.get('publishedAt')} | {j.get('jobUrl')}")
        # exit 0: we still want workflow success (and create an issue)
    else:
        print("No new jobs.")

if __name__ == "__main__":
    main()
