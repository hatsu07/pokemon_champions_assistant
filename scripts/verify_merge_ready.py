"""Fail-closed GitHub pre-merge audit for humans and development agents.

This is a procedural safety check, NOT a substitute for server-side branch
protection. Requires a GitHub token able to read branch-protection settings.
Never embed GitHub tokens in source, logs, or CI artifacts.
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


def verify(pr, branch, protection, runs, jobs_by_run, *, required_job=REQUIRED_JOB):
    """Return fail-closed reasons; checks must cover the latest PR head."""
    errors = []
    head = pr.get("head", {}).get("sha")
    if (pr.get("state") != "open" or pr.get("draft") is not False
            or pr.get("base", {}).get("ref") != "master" or not head):
        errors.append("PR must be open, Ready, based on master, with a HEAD SHA")
    if branch.get("protected") is not True:
        errors.append("master is not protected by GitHub")
    checks = protection.get("required_status_checks") or {}
    configured = set(checks.get("contexts") or [])
    configured.update(x.get("context") for x in checks.get("checks") or [])
    if required_job not in configured or checks.get("strict") is not True:
        errors.append("master must require the Windows check and up-to-date branches")
    if (protection.get("enforce_admins") or {}).get("enabled") is not True:
        errors.append("master must disallow administrator bypass")
    if protection.get("required_pull_request_reviews") is None:
        errors.append("master must require pull requests")
    matches = [run for run in runs
               if run.get("head_sha") == head
               and run.get("event") == "pull_request"
               and run.get("path") == REQUIRED_WORKFLOW_PATH]
    matches.sort(key=lambda run: (run.get("created_at") or "", run.get("id") or 0),
                 reverse=True)
    if not matches:
        errors.append("no matching Windows PR workflow run for latest HEAD")
        return errors
    latest = matches[0]
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        errors.append("latest Windows workflow must be completed/success")
    jobs = jobs_by_run.get(latest.get("id"), [])
    required = [j for j in jobs if j.get("name") == required_job]
    if len(required) != 1 or any(
        j.get("status") != "completed" or j.get("conclusion") != "success"
        for j in required
    ):
        errors.append("required Windows job must be explicitly completed/success")
    if any(j.get("conclusion") != "success" for j in jobs):
        errors.append("every Windows workflow job must be successful")
    return errors


def _get_json(url: str, token: str):
    req = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pokemon-champions-premerge-audit",
    })
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fail-closed GitHub pre-merge audit")
    parser.add_argument("--repo", default="hatsu07/pokemon_champions_assistant")
    parser.add_argument("--pr", required=True, type=int)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("invalid GitHub owner/repository")
    token = os.environ.get("GH_TOKEN")
    if not token:
        print("BLOCKED: GH_TOKEN is required for a read-only GitHub audit", file=sys.stderr)
        return 2
    base = "https://api.github.com/repos/" + args.repo
    try:
        pr = _get_json(f"{base}/pulls/{args.pr}", token)
        branch = _get_json(f"{base}/branches/master", token)
        protection = _get_json(f"{base}/branches/master/protection", token)
        head = pr.get("head", {}).get("sha", "")
        if not re.fullmatch(r"[a-f0-9]{40}", head):
            raise ValueError("invalid PR HEAD SHA")
        data = _get_json(
            f"{base}/actions/runs?event=pull_request&head_sha={head}&per_page=100",
            token)
        runs = data.get("workflow_runs", [])
        jobs = {}
        for run in runs:
            if (run.get("head_sha") == head
                    and run.get("path") == REQUIRED_WORKFLOW_PATH
                    and run.get("event") == "pull_request"):
                jobs[run["id"]] = _get_json(
                    f"{base}/actions/runs/{run['id']}/jobs?per_page=100", token
                ).get("jobs", [])
        failures = verify(pr, branch, protection, runs, jobs)
    except (HTTPError, URLError, ValueError, KeyError, TimeoutError) as exc:
        # Never degrade an inaccessible protection rule into "ready".
        print("BLOCKED: GitHub audit failed (" + type(exc).__name__ + ")",
              file=sys.stderr)
        return 2
    for failure in failures:
        print("BLOCKED: " + failure, file=sys.stderr)
    if failures:
        return 1
    print(f"PASS: PR #{args.pr} HEAD {head}, latest Windows CI success, master protected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
