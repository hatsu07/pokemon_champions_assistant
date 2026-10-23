"""Read-only, fail-closed verification of the latest PR's entire required CI.

Server-side branch protection is unavailable on this private repository's
current GitHub plan. This is a process gate, NOT GitHub-enforced protection.
Pass only for the latest PR HEAD and a completed/success Windows workflow.
Never log or commit GitHub credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REQUIRED_WORKFLOW_PATH = ".github/workflows/windows-validation-artifact.yml"
REQUIRED_JOB = "desktop-windows-validation"


def verify(pr, runs, jobs_by_run, *, required_job=REQUIRED_JOB):
    """Return all blocking CI reasons, without relying on repository protection."""
    errors = []
    head = pr.get("head", {}).get("sha")
    if (pr.get("state") != "open" or pr.get("draft") is not False
            or pr.get("base", {}).get("ref") != "master"
            or not isinstance(head, str) or not re.fullmatch(r"[a-f0-9]{40}", head)):
        errors.append("PR must be open, Ready, target master and have a valid HEAD SHA")
    matches = [run for run in runs if run.get("head_sha") == head
               and run.get("event") == "pull_request"
               and run.get("path") == REQUIRED_WORKFLOW_PATH]
    matches.sort(key=lambda run: (run.get("created_at") or "", run.get("id") or 0),
                 reverse=True)
    # Ignore the workflow dispatch of a different revision and never use an
    # old revision's green check. Never pick an older green run after a newer
    # failure. Draft-only skipped runs are never success.
    if not matches:
        errors.append("no Windows PR workflow run for latest HEAD")
        return errors
    latest = matches[0]
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        errors.append("latest Windows workflow must be completed/success")
    jobs = jobs_by_run.get(latest.get("id"), [])
    required = [job for job in jobs if job.get("name") == required_job]
    if len(required) != 1 or required[0].get("status") != "completed" or required[0].get("conclusion") != "success":
        errors.append("required Windows job must be completed/success")
    if not jobs or any(job.get("status") != "completed" or job.get("conclusion") != "success" for job in jobs):
        errors.append("all Windows jobs must be completed/success")
    return errors


def _get_json(url: str, token: str):
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pokemon-champions-premerge-audit",
    })
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only latest-HEAD Windows CI pre-merge audit")
    parser.add_argument("--repo", default="hatsu07/pokemon_champions_assistant")
    parser.add_argument("--pr", required=True, type=int)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("invalid GitHub owner/repository")
    token = os.environ.get("GH_TOKEN")
    if not token:
        print("BLOCKED: GH_TOKEN required for read-only GitHub audit", file=sys.stderr)
        return 2
    base = "https://api.github.com/repos/" + args.repo
    try:
        pr = _get_json(f"{base}/pulls/{args.pr}", token)
        head = pr.get("head", {}).get("sha", "")
        if not re.fullmatch(r"[a-f0-9]{40}", head):
            raise ValueError("invalid PR HEAD SHA")
        data = _get_json(
            f"{base}/actions/runs?event=pull_request&head_sha={head}&per_page=100",
            token)
        runs = data.get("workflow_runs", [])
        jobs = {}
        for run in runs:
            if (run.get("head_sha") == head and run.get("event") == "pull_request"
                    and run.get("path") == REQUIRED_WORKFLOW_PATH):
                jobs[run["id"]] = _get_json(
                    f"{base}/actions/runs/{run['id']}/jobs?per_page=100", token
                ).get("jobs", [])
        failures = verify(pr, runs, jobs)
    except (HTTPError, URLError, ValueError, KeyError, TimeoutError) as exc:
        print("BLOCKED: GitHub CI audit unavailable (" + type(exc).__name__ + ")", file=sys.stderr)
        return 2
    for failure in failures:
        print("BLOCKED: " + failure, file=sys.stderr)
    if failures:
        return 1
    print(f"PASS: PR #{args.pr} HEAD {head}, latest Windows PR CI completed/success")
    print("NOTE: This repository has no server-side required-check merge protection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
