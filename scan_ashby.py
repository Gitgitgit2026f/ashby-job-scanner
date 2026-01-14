import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple

import requests

STATE_PATH = "state.json"
BOARDS_PATH = "boards.txt"
TIMEOUT_SECONDS = 25

SUMMARY_PATH = "run_summary.json"
REPORT_DIR = "reports"
LATEST_MD_PATH = os.path.join(REPORT_DIR, "latest.md")
HISTORY_MD_PATH = os.path.join(REPORT_DIR, "history.md")

# Basic safety: identify ourselves politely
HEADERS = {
    "User-Agent": "ashby-job-scanner/1.0 (+https://github.com/)"
}

MAX_JOBS_PER_BOARD_LATEST_MD = 100   # in latest.md mag het wat ruimer
MAX_HISTORY_RUNS = 500               # voorkom dat history.md oneindig groeit


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


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


def md_escape(s: str) -> str:
    # Minimal escaping: keep markdown stable in list items
    if s is None:
        return ""
    return str(s).replace("\n", " ").strip()


def build_latest_md(
    scanned_at_utc: str,
    boards: List[str],
    newly: List[Tuple[str, List[Dict[str, Any]]]],
    errors: List[str],
    first_run: bool,
) -> str:
    new_count = sum(len(jobs) for _, jobs in newly)

    lines: List[str] = []
    lines.append("# Ashby scan report")
    lines.append("")
    lines.append(f"- **Scanned at (UTC):** {scanned_at_utc}")
    lines.append(f"- **Boards scanned:** {len(boards)}")
    lines.append(f"- **New jobs found:** {new_count}")
    lines.append(f"- **Errors:** {len(errors)}")
    lines.append(f"- **First run (baseline):** {'yes' if first_run else 'no'}")
    lines.append("")

    lines.append("## Boards")
    lines.append("")
    # small table for quick overview
    lines.append("| Board | New jobs |")
    lines.append("|---|---:|")
    if newly:
        new_map = {slug: len(jobs) for slug, jobs in newly}
    else:
        new_map = {}
    for b in boards:
        lines.append(f"| {md_escape(b)} | {new_map.get(b, 0)} |")
    lines.append("")

    lines.append("## New jobs")
    lines.append("")
    if not newly:
        lines.append("_No new jobs found._")
        lines.append("")
    else:
        for slug, jobs in newly:
            lines.append(f"### {md_escape(slug)}")
            lines.append("")
            show = jobs[:MAX_JOBS_PER_BOARD_LATEST_MD]
            for j in show:
                title = md_escape(j.get("title", ""))
                url = md_escape(j.get("jobUrl", ""))
                published = md_escape(j.get("publishedAt", ""))
                location = md_escape(j.get("location", ""))

                meta_parts = [p for p in [location, published] if p]
                meta = " — ".join(meta_parts)

                if url:
                    lines.append(f"- [{title}]({url})" + (f" — {meta}" if meta else ""))
                else:
                    lines.append(f"- {title}" + (f" — {meta}" if meta else ""))

            if len(jobs) > MAX_JOBS_PER_BOARD_LATEST_MD:
                lines.append(f"- …and {len(jobs) - MAX_JOBS_PER_BOARD_LATEST_MD} more")
            lines.append("")

    if errors:
        lines.append("## Errors")
        lines.append("")
        for e in errors:
            lines.append(f"- {md_escape(e)}")
        lines.append("")

    return "\n".join(lines)


def append_history_entry(entry_md: str) -> None:
    """
    Append a run entry to reports/history.md but keep it bounded.
    We'll store entries separated by a divider. Newest goes on top.
    """
    ensure_dir(REPORT_DIR)
    divider = "\n\n---\n\n"

    if os.path.exists(HISTORY_MD_PATH):
        with open(HISTORY_MD_PATH, "r", encoding="utf-8") as f:
            existing = f.read()
    else:
        existing = "# Scan history\n\n"

    parts = existing.split(divider)
    header = parts[0].rstrip()
    entries = [p.strip() for p in parts[1:] if p.strip()]

    entries = [entry_md.strip()] + entries
    entries = entries[:MAX_HISTORY_RUNS]

    new_content = header + divider + divider.join(entries) + "\n"
    with open(HISTORY_MD_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)


def main() -> None:
    state = load_json(STATE_PATH, default={})

    # Detect first run: no boards stored yet (only empty or just _meta)
    existing_board_keys = [k for k in state.keys() if not k.startswith("_")]
    first_run = (len(existing_board_keys) == 0)

    boards = load_boards()
    scanned_at_utc = datetime.now(timezone.utc).isoformat()

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
        "updatedAtUtc": scanned_at_utc,
        "boardsCount": len(boards),
        "errorsCount": len(any_errors),
    }
    save_json(STATE_PATH, state)

    # Write a summary file for the workflow step to read
    summary = {
        "firstRun": first_run,
        "scannedAtUtc": scanned_at_utc,
        "new": [{"board": slug, "jobs": jobs} for slug, jobs in all_new],
        "errors": any_errors,
    }
    save_json(SUMMARY_PATH, summary)

    # Generate markdown reports
    ensure_dir(REPORT_DIR)
    latest_md = build_latest_md(
        scanned_at_utc=scanned_at_utc,
        boards=boards,
        newly=all_new,
        errors=any_errors,
        first_run=first_run,
    )
    with open(LATEST_MD_PATH, "w", encoding="utf-8") as f:
        f.write(latest_md + "\n")

    # History entry (compact)
    new_count = sum(len(jobs) for _, jobs in all_new)
    entry_lines: List[str] = []
    entry_lines.append(f"## {scanned_at_utc}")
    entry_lines.append("")
    entry_lines.append(f"- Boards: {len(boards)}")
    entry_lines.append(f"- New jobs: {new_count}")
    entry_lines.append(f"- Errors: {len(any_errors)}")
    entry_lines.append(f"- First run: {'yes' if first_run else 'no'}")
    entry_lines.append("")
    if all_new:
        entry_lines.append("New per board:")
        for slug, jobs in all_new:
            entry_lines.append(f"- **{md_escape(slug)}**: {len(jobs)}")
        entry_lines.append("")
    if any_errors:
        entry_lines.append("Errors:")
        for e in any_errors:
            entry_lines.append(f"- {md_escape(e)}")
        entry_lines.append("")

    append_history_entry("\n".join(entry_lines))

    # Console output (Actions logs)
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
    else:
        print("No new jobs.")


if __name__ == "__main__":
    main()
