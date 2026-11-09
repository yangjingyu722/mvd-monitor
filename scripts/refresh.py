#!/usr/bin/env python3
"""Refresh the MiniVectorDB monitoring data and publish it.

The script reads the state of the upstream task package and of the submission
branch, writes data/latest.json and commits the change. It is meant to run
from a cron job or from a GitHub Action.

Usage:

    python scripts/refresh.py            # refresh and commit if something changed
    python scripts/refresh.py --dry-run  # only print what would happen
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = REPO_ROOT / "data" / "latest.json"
LOCAL_FILE = REPO_ROOT / "data" / "local.json"

UPSTREAM_REPO = "oceanbase/seekdb"
UPSTREAM_BRANCH = "2026-competition-round2"
SUBMISSION_REPO = "yangjingyu722/Myseekdb"
SUBMISSION_BRANCH = "2026-competition-round2"

PACKAGE_TAGS = ("task-package", "public-cases", "driver", "protocol")


def run(command: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        command, cwd=str(cwd) if cwd else None, check=False,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "%s failed: %s" % (" ".join(command), result.stderr.strip())
        )
    return result.stdout


def gh(path: str) -> object:
    """Calls the GitHub API through the gh command line."""
    raw = run(["gh", "api", path])
    return json.loads(raw)


def load_local() -> dict:
    """Reads the local measurements. The file is maintained by hand."""
    if not LOCAL_FILE.is_file():
        return {}
    try:
        return json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def upstream_commits(limit: int = 12) -> list[dict]:
    raw = gh(
        "repos/%s/commits?sha=%s&per_page=%d"
        % (UPSTREAM_REPO, UPSTREAM_BRANCH, limit)
    )
    commits = []
    for item in raw:
        commits.append(
            {
                "sha": item["sha"],
                "short": item["sha"][:9],
                "date": item["commit"]["author"]["date"],
                "author": item["commit"]["author"]["name"],
                "subject": item["commit"]["message"].split("\n")[0].strip(),
                "files_changed": len(item.get("files", [])),
                "tags": [],
            }
        )
    return commits


def upstream_package(head_sha: str) -> dict:
    """Reads the public task package from the upstream branch."""
    package = {
        "present": False,
        "version": "",
        "scope": "",
        "case_count": 0,
        "total_statements": 0,
        "cases": [],
        "assertion_format": "",
        "assertion_fields": [],
        "float_tolerance": "",
        "driver_published": False,
        "notes": "",
    }
    try:
        manifest = gh(
            "repos/%s/contents/mysqltest/manifest.json?ref=%s"
            % (UPSTREAM_REPO, UPSTREAM_BRANCH)
        )
    except RuntimeError:
        return package
    import base64

    data = json.loads(base64.b64decode(manifest["content"]).decode("utf-8"))
    package["present"] = True
    package["version"] = data.get("version", "")
    package["scope"] = data.get("scope", "")
    package["case_count"] = len(data.get("cases", []))
    package["total_statements"] = sum(
        int(c.get("statements", 0)) for c in data.get("cases", [])
    )
    for case in data.get("cases", []):
        package["cases"].append(
            {
                "id": case.get("id", ""),
                "group": case.get("group", ""),
                "statements": case.get("statements", 0),
                "checks": [],
            }
        )
    package["assertion_format"] = "json-array"
    package["assertion_fields"] = ["affected_rows", "columns", "rows", "unordered"]
    package["float_tolerance"] = "abs=1e-5, rel=1e-5"
    package["notes"] = (
        "The public package defines the SQL inputs and the result semantics. "
        "The process communication wrapper is defined by the accompanying "
        "test driver, which is not part of the public package."
    )
    return package


def submission_state() -> dict:
    """Reads the state of the submission branch."""
    state = {
        "repo": SUBMISSION_REPO,
        "branch": SUBMISSION_BRANCH,
        "head_sha": "",
        "head_short": "",
        "pushed": False,
        "pushed_at": None,
        "deliverables": {
            "source": {"present": False, "path": "minivectordb/", "files": 0},
            "dockerfile": {"present": False, "path": "Dockerfile", "lines": 0},
            "conversation": {
                "present": False,
                "path": "agent/conversation.json",
                "bytes": 0,
                "limit_bytes": 131072,
                "valid": False,
            },
        },
        "readiness": {
            "local_cases_total": 0,
            "local_cases_passed": 0,
            "public_cases_total": 0,
            "public_cases_passed": 0,
            "public_cases_status": "not-yet-run",
            "docker_image_built": False,
        },
        "local_benchmark": {},
        "resource_test": {},
    }
    try:
        branch = gh("repos/%s/branches/%s" % (SUBMISSION_REPO, SUBMISSION_BRANCH))
    except RuntimeError:
        return state
    state["head_sha"] = branch["commit"]["sha"]
    state["head_short"] = branch["commit"]["sha"][:9]
    state["pushed"] = True
    try:
        tree = gh(
            "repos/%s/git/trees/%s?recursive=1"
            % (SUBMISSION_REPO, branch["commit"]["sha"])
        )
        paths = [item["path"] for item in tree.get("tree", [])]
        source_files = [p for p in paths if p.startswith("minivectordb/") and p.count("/") > 1]
        state["deliverables"]["source"]["present"] = bool(source_files)
        state["deliverables"]["source"]["files"] = len(source_files)
        state["deliverables"]["dockerfile"]["present"] = "Dockerfile" in paths
        if "agent/conversation.json" in paths:
            entry = gh(
                "repos/%s/contents/agent/conversation.json?ref=%s"
                % (SUBMISSION_REPO, SUBMISSION_BRANCH)
            )
            state["deliverables"]["conversation"]["present"] = True
            state["deliverables"]["conversation"]["bytes"] = entry.get("size", 0)
            # The material must be a JSON file below the size limit. The
            # limit of the task is 128 KiB.
            state["deliverables"]["conversation"]["valid"] = (
                0 < entry.get("size", 0) <= 131072
            )
    except RuntimeError:
        pass
    return state


def tag_commits(commits: list[dict], previous: dict) -> list[dict]:
    """Marks the commits that are new since the last refresh."""
    known = {c["sha"] for c in previous.get("upstream", {}).get("commits", [])}
    for commit in commits:
        if commit["sha"] not in known:
            commit["tags"] = ["new"]
        if commit["subject"].lower().startswith("upload"):
            commit["tags"].append("task-package")
    return commits


def build(previous: dict) -> dict:
    commits = upstream_commits()
    package = upstream_package(commits[0]["sha"] if commits else "")
    upstream = {
        "repo": UPSTREAM_REPO,
        "branch": UPSTREAM_BRANCH,
        "head_sha": commits[0]["sha"] if commits else "",
        "head_short": commits[0]["short"] if commits else "",
        "head_date": commits[0]["date"] if commits else "",
        "head_subject": commits[0]["subject"] if commits else "",
        "commits": tag_commits(commits, previous),
        "package": package,
    }
    submission = submission_state()
    # The local measurements live in their own file, the script only reads
    # them. A generated run can therefore never drop a measurement.
    local = load_local()
    # The values that the API reports about the pushed branch win, because
    # they describe the state that the evaluation sees. The local
    # measurements fill the fields that the API cannot know.
    submission["readiness"].update(local.get("readiness", {}))
    submission["local_benchmark"] = local.get("local_benchmark", submission["local_benchmark"])
    submission["resource_test"] = local.get("resource_test", submission["resource_test"])
    # The deliverables of the local commit stay visible until the branch is
    # pushed, the API cannot see an unpushed commit.
    for key, value in local.get("deliverables", {}).items():
        if not submission["deliverables"].get(key, {}).get("present"):
            submission["deliverables"][key] = value
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "scripts/refresh.py",
        "upstream": upstream,
        "submission": submission,
        "timeline": previous.get(
            "timeline",
            {
                "competition_start": "2026-11-09T04:00:00Z",
                "competition_end": "2026-11-18T04:00:00Z",
                "submission_deadline": "2026-11-18T04:00:00Z",
                "daily_submission_limit": 2,
            },
        ),
        "alerts": build_alerts(upstream, submission),
    }
    return payload


def build_alerts(upstream: dict, submission: dict) -> list[dict]:
    alerts: list[dict] = []
    package = upstream.get("package", {})
    readiness = submission.get("readiness", {})
    if package.get("present") and not readiness.get("public_cases_passed"):
        alerts.append(
            {
                "level": "critical",
                "title": "The public cases have not been executed against the delivered package",
                "detail": (
                    "The task package %s changed the assertion format to structured "
                    "JSON with affected_rows, columns, rows and unordered. The local "
                    "runner still speaks the earlier text protocol."
                    % upstream.get("head_short", "")
                ),
                "action": "Align the protocol layer with the delivered package and run the five public cases before any submission.",
            }
        )
    if package.get("present") and not package.get("driver_published"):
        alerts.append(
            {
                "level": "warning",
                "title": "The evaluation driver is not published",
                "detail": (
                    "The public package states that the process communication "
                    "wrapper is defined by the accompanying test driver, which is "
                    "not included. The wire format is still an inference."
                ),
                "action": "Keep the protocol layer switchable and confirm the format before submitting.",
            }
        )
    if not submission.get("pushed"):
        alerts.append(
            {
                "level": "info",
                "title": "The submission branch is not pushed",
                "detail": "The platform reads the branch from the remote repository, a local commit is not visible to the evaluation.",
                "action": "Push the branch with the source, the Dockerfile and the conversation.json in one commit.",
            }
        )
    return alerts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    previous = {}
    if DATA_FILE.is_file():
        previous = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    try:
        payload = build(previous)
    except RuntimeError as exc:
        print("refresh failed: %s" % exc, file=sys.stderr)
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if DATA_FILE.is_file() and DATA_FILE.read_text(encoding="utf-8") == text:
        print("nothing changed")
        return 0
    if args.dry_run:
        print(text)
        return 0
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(text, encoding="utf-8")
    run(["git", "add", "data/latest.json"], REPO_ROOT)
    run(
        [
            "git", "commit", "-m",
            "chore: refresh the monitoring data (%s)" % payload["upstream"]["head_short"],
        ],
        REPO_ROOT,
    )
    run(["git", "push"], REPO_ROOT)
    print("published %s" % payload["generated_at"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
