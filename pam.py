#!/usr/bin/env python3
"""
Pam — Open Library Project AI Manager, continuous polling service.

Polls GitHub every 60 seconds. When new PRs or issues are detected,
invokes Claude to run the appropriate workflow:

  - New PR  → PR pre-review  (scripts/gh_scripts/PR_PREREVIEW_README.md)
  - New issue → Issue refinement  (scripts/gh_scripts/ISSUE_REFINEMENT_README.md)

Run in tmux (recommended):
    tmux new-session -d -s pam 'python3 /path/to/pam.py | tee -a /tmp/ol-pam.log'
    tmux attach -t pam

Or with nohup:
    nohup python3 pam.py >> /tmp/ol-pam.log 2>&1 &

State is persisted to .pam_state.json so Pam resumes correctly after restarts.

Requirements:
    gh CLI authenticated via `gh auth login`
    claude CLI available (checks PATH, then ~/.local/bin/claude)
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO = 'internetarchive/openlibrary'
REPO_ROOT = Path(__file__).resolve().parent
STATE_FILE = REPO_ROOT / '.pam_state.json'
POLL_INTERVAL = 60  # seconds
CLAUDE_TIMEOUT = 300  # seconds per Claude invocation

BOT_MARKER_PR = '<!-- ol-pr-bot -->'
BOT_MARKER_ISSUE = '<!-- ol-issue-bot -->'

PR_README = REPO_ROOT / 'scripts/gh_scripts/PR_PREREVIEW_README.md'
ISSUE_README = REPO_ROOT / 'scripts/gh_scripts/ISSUE_REFINEMENT_README.md'
PR_SCRIPT = REPO_ROOT / 'scripts/gh_scripts/new_pr_bot.py'

# Find claude binary: prefer PATH, fall back to known install location
CLAUDE_BIN = (
    subprocess.run(['which', 'claude'], capture_output=True, text=True).stdout.strip()
    or os.path.expanduser('~/.local/bin/claude')
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    # Bootstrap: start from now so we don't replay old items on first run
    now = _now_str()
    return {'last_pr_check': now, 'last_issue_check': now}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _gh_json(args: list[str]) -> object:
    result = subprocess.run(
        ['gh'] + args, capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:4])}... failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def get_new_prs(since: str) -> list[dict]:
    """Return non-draft PRs created since `since` (ISO timestamp).

    Uses the REST API directly (not search) to avoid GitHub's search-index lag,
    which caused PRs created within the poll window to be permanently missed.
    """
    prs = _gh_json([
        'api', f'repos/{REPO}/pulls',
        '--method', 'GET',
        '-f', 'state=open',
        '-f', 'sort=created',
        '-f', 'direction=desc',
        '-F', 'per_page=50',
        '--jq', '[.[] | {number: .number, title: .title, isDraft: .draft, createdAt: .created_at}]',
    ])
    return [p for p in prs if p.get('createdAt', '') > since and not p.get('isDraft')]


def get_new_issues(since: str) -> list[dict]:
    """Return open issues created since `since` (ISO timestamp).

    Uses the REST API directly (not search) to avoid GitHub's search-index lag.
    """
    items = _gh_json([
        'api', f'repos/{REPO}/issues',
        '--method', 'GET',
        '-f', 'state=open',
        '-f', 'sort=created',
        '-f', 'direction=desc',
        '-F', 'per_page=50',
        '--jq', '[.[] | select(.pull_request == null) | {number: .number, title: .title, createdAt: .created_at}]',
    ])
    return [i for i in items if i.get('createdAt', '') > since]


def issue_has_bot_marker(issue_number: int) -> bool:
    """Return True if the issue already has our bot marker (idempotency guard)."""
    try:
        comments = _gh_json(['api', f'repos/{REPO}/issues/{issue_number}/comments'])
        return any(BOT_MARKER_ISSUE in (c.get('body') or '') for c in comments)
    except Exception:
        return True  # fail closed — don't double-post on API error


# ---------------------------------------------------------------------------
# Claude invocation
# ---------------------------------------------------------------------------

def _run_claude(prompt: str) -> None:
    subprocess.run(
        [CLAUDE_BIN, '--dangerously-skip-permissions', '-p', prompt],
        timeout=CLAUDE_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

def run_pr_workflow() -> None:
    log('Running PR pre-review workflow.')
    prompt = f"""\
You are running the Open Library PR pre-review bot.

Step 1 — Run the data-gathering script:
  python3 {PR_SCRIPT} --hours 1

The script prints status messages to stderr and a JSON array to stdout.
Parse the JSON array from the output.

Step 2 — Read {PR_README} for the full guide on what comments to post
and how to structure them.

Step 3 — For each PR object in the JSON array:
- Analyze the signals and determine what comment (if any) to post,
  following the README guide exactly.
- If nothing warrants a comment, skip that PR entirely.
- Post the comment with:
    gh pr comment {{number}} --repo {REPO} --body "COMMENT"
- Always end every posted comment with: {BOT_MARKER_PR}

Step 4 — For each PR you processed (commented on or skipped), if you noticed
anything that this workflow currently misses, gets wrong, or could handle
better, append a brief note to {REPO_ROOT}/FUTURE.md under a
"## Observations from the field" section. One or two sentences max per
observation. Include the PR number for context. Skip this step if you have
nothing genuinely useful to add — don't pad.
"""
    _run_claude(prompt)


def run_issue_workflow(issue_number: int) -> None:
    log(f'Running issue refinement workflow for #{issue_number}.')
    if issue_has_bot_marker(issue_number):
        log(f'  Issue #{issue_number} already processed — skipping.')
        return
    prompt = f"""\
Issue #{issue_number} was just opened on {REPO}.

Read {ISSUE_README} for your full instructions. Follow them to analyze
the issue.

After completing your analysis, post a comment on the issue:
  gh issue comment {issue_number} --repo {REPO} --body "COMMENT"
Always end the comment with: {BOT_MARKER_ISSUE}

Finally, if you noticed anything during this analysis that the workflow
currently misses, gets wrong, or could handle better, append a brief note
to {REPO_ROOT}/FUTURE.md under a "## Observations from the field" section.
One or two sentences max. Include the issue number for context. Skip this
step if you have nothing genuinely useful to add — don't pad.
"""
    _run_claude(prompt)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    log(f'Pam started. Polling {REPO} every {POLL_INTERVAL}s.')
    log(f'Claude binary: {CLAUDE_BIN}')
    log(f'State file: {STATE_FILE}')

    state = load_state()
    log(f'Resuming from: PRs since {state["last_pr_check"]}, '
        f'issues since {state["last_issue_check"]}')

    while True:
        pr_since = state['last_pr_check']
        issue_since = state['last_issue_check']
        now = _now_str()

        try:
            # --- PRs ---
            new_prs = get_new_prs(pr_since)
            if new_prs:
                log(f'Found {len(new_prs)} new PR(s): {[p["number"] for p in new_prs]}')
                run_pr_workflow()
            else:
                log('No new PRs.')

            # --- Issues ---
            new_issues = get_new_issues(issue_since)
            if new_issues:
                log(f'Found {len(new_issues)} new issue(s): {[i["number"] for i in new_issues]}')
                for issue in new_issues:
                    run_issue_workflow(issue['number'])
            else:
                log('No new issues.')

        except Exception as exc:
            log(f'Poll error: {exc}')

        state['last_pr_check'] = now
        state['last_issue_check'] = now
        save_state(state)

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
