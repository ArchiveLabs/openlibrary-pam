#!/usr/bin/env python3
"""
Open Library PR pre-review bot — data gatherer.

Runs hourly (via a Claude Code scheduled task). For every recently-opened,
non-draft PR that has no comments yet and no Copilot reviewer assigned:

  1. Assigns Copilot as a reviewer.
  2. Gathers signals (first-timer, linked issue, CI status, design, etc.)
     plus rich PR data (files, commits, assignee workload).
  3. Prints a JSON array to stdout.

The calling Claude Code agent reads that JSON alongside PR_PREREVIEW_README.md
and decides what comment (if any) to post on each PR.

Usage:
    python3 new_pr_bot.py [--hours N] [--dry-run] [--repo owner/repo]

    --dry-run   Skip Copilot assignment; set dry_run=true in JSON output so
                the Claude Code agent knows to print rather than post.

Requirements:
    gh CLI authenticated via `gh auth login`
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_REPO = 'internetarchive/openlibrary'
BOT_MARKER = '<!-- ol-pr-bot -->'

# Any #NNN reference in the PR body counts as a linked issue
ISSUE_REF_RE = re.compile(r'#(\d+)')

# Visual evidence patterns in PR body
VISUAL_EVIDENCE_RE = re.compile(
    r'(!\[.*?\]\(.*?\)'
    r'|https?://\S+\.(png|jpg|jpeg|gif|mp4|mov|webm)(\?[^\s)]*)?'
    r'|user-images\.githubusercontent\.com'
    r'|github\.com/user-attachments)',
    re.IGNORECASE,
)

# Resource URLs — keep up-to-date; Claude Code reads these via the README and
# uses them when composing comments.
GIT_CHEATSHEET_URL = 'https://github.com/internetarchive/openlibrary/wiki/git'
PR_TEMPLATE_URL = (
    'https://github.com/internetarchive/openlibrary/blob/master/'
    '.github/pull_request_template.md'
)
PRECOMMIT_GUIDE_URL = 'https://docs.openlibrary.org/developers/tools/pre-commit.html'
SCREENSHOT_GUIDE_URL = (
    'https://github.com/internetarchive/openlibrary/wiki/Testing-&-Tools#screenshots'
)
CONTRIBUTING_URL = (
    'https://github.com/internetarchive/openlibrary/blob/master/CONTRIBUTING.md'
)
GITHUB_ATTACH_GUIDE_URL = (
    'https://docs.github.com/en/get-started/writing-on-github/'
    'working-with-advanced-formatting/attaching-files'
)

# CI check conclusions treated as "failing"
# Excluded: 'cancelled' — cancelled runs usually mean a newer commit superseded the old run,
# not that CI is broken.
_CI_FAILING_CONCLUSIONS = frozenset({'failure', 'timed_out', 'action_required'})

# Priority labels: 0 (highest) … _MAX_PRIORITY; cap to avoid runaway API loops
_MAX_PRIORITY = 2


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class GHError(Exception):
    """Raised when a gh CLI call fails or times out."""


# ---------------------------------------------------------------------------
# gh CLI helpers
# ---------------------------------------------------------------------------


def _run_gh(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(['gh'] + args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GHError(f"gh {' '.join(args[:4])}... timed out after {timeout}s")


def gh_json(args: list[str]) -> object:
    """Run a gh command that returns JSON; raises GHError on failure."""
    result = _run_gh(args)
    if result.returncode != 0:
        raise GHError(f"gh {' '.join(args[:4])}... failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GHError(f"JSON parse failed for: gh {' '.join(args[:4])}...") from exc


def gh_run(args: list[str], *, dry_run: bool, action_desc: str) -> bool:
    """Run a mutative gh command; in dry-run mode, log to stderr instead."""
    if dry_run:
        print(f'  [DRY RUN] {action_desc}', file=sys.stderr)
        return True
    result = _run_gh(args)
    if result.returncode != 0:
        print(f'  Warning: {action_desc} failed: {result.stderr.strip()}', file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# PR fetching
# ---------------------------------------------------------------------------


def get_recent_prs(repo: str, hours: float) -> list[dict]:
    """Return non-draft PRs opened within the last `hours` hours."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    since_str = since.strftime('%Y-%m-%dT%H:%M:%SZ')

    prs = gh_json([
        'pr', 'list', '--repo', repo,
        '--search', f'created:>={since_str}',
        '--limit', '100',
        '--json', 'number,title,author,isDraft,assignees,labels,body,url,createdAt',
    ])

    result = []
    for pr in prs:
        if pr.get('isDraft'):
            continue
        created = datetime.fromisoformat(pr['createdAt'].replace('Z', '+00:00'))
        if created >= since:
            result.append(pr)
    return result


# ---------------------------------------------------------------------------
# Skip / idempotency checks
# ---------------------------------------------------------------------------


def check_comments(repo: str, pr_number: int) -> tuple[bool, bool]:
    """
    Fetch PR comments once, return (has_any_comment, has_bot_marker).
    On API error, returns (True, False) — fail closed to avoid double-posting.
    """
    try:
        comments = gh_json(['api', f'repos/{repo}/issues/{pr_number}/comments'])
        has_any = bool(comments)
        has_marker = any(BOT_MARKER in (c.get('body') or '') for c in comments)
        return has_any, has_marker
    except GHError:
        return True, False


def copilot_already_assigned(repo: str, pr_number: int) -> bool:
    """Return True if Copilot is already a requested reviewer or has reviewed."""
    try:
        requested = gh_json(['api', f'repos/{repo}/pulls/{pr_number}/requested_reviewers'])
        for user in requested.get('users', []):
            if 'copilot' in user.get('login', '').lower():
                return True
        reviews = gh_json(['api', f'repos/{repo}/pulls/{pr_number}/reviews'])
        for review in reviews:
            login = (review.get('user') or {}).get('login', '').lower()
            if 'copilot' in login:
                return True
    except GHError:
        pass
    return False


# ---------------------------------------------------------------------------
# Signal gathering
# ---------------------------------------------------------------------------


def is_first_contribution(repo: str, username: str) -> bool:
    try:
        prs = gh_json([
            'pr', 'list', '--repo', repo,
            '--author', username, '--state', 'all', '--limit', '5', '--json', 'number',
        ])
        return len(prs) == 1
    except GHError:
        return False


def has_issue_reference(pr_body: str) -> bool:
    return bool(ISSUE_REF_RE.search(pr_body or ''))


def has_visual_evidence(pr_body: str) -> bool:
    return bool(VISUAL_EVIDENCE_RE.search(pr_body or ''))


def is_design_pr(repo: str, pr_number: int, labels: list[dict]) -> bool:
    for label in labels:
        if 'design' in label.get('name', '').lower():
            return True
    try:
        files = gh_json(['api', f'repos/{repo}/pulls/{pr_number}/files'])
        for f in files:
            fname = f.get('filename', '')
            if fname.startswith('static/css/') or fname.endswith(('.less', '.css')):
                return True
    except GHError:
        pass
    return False


def is_ci_failing(repo: str, pr_number: int) -> bool:
    try:
        pr_data = gh_json(['api', f'repos/{repo}/pulls/{pr_number}'])
        sha = pr_data['head']['sha']
        data = gh_json(['api', f'repos/{repo}/commits/{sha}/check-runs'])
        return any(
            c.get('conclusion') in _CI_FAILING_CONCLUSIONS
            for c in data.get('check_runs', [])
        )
    except GHError:
        pass
    result = _run_gh(['pr', 'checks', str(pr_number), '--repo', repo])
    return result.returncode == 0 and 'fail' in result.stdout.lower()


def get_linked_issue_details(repo: str, issue_number: str) -> dict:
    """Return priority, triaged status, and assigned status for a linked issue."""
    try:
        issue = gh_json(['api', f'repos/{repo}/issues/{issue_number}'])
        priority = None
        triaged = False
        for label in issue.get('labels', []):
            name = label.get('name', '')
            m = re.match(r'Priority:\s*(\d+)', name, re.IGNORECASE)
            if m:
                priority = int(m.group(1))
                triaged = True
        assignees = issue.get('assignees') or []
        assigned = bool(assignees)
        assignee_login = assignees[0].get('login') if assignees else None
        return {
            'priority': priority,
            'triaged': triaged,
            'assigned': assigned,
            'assignee_login': assignee_login,
        }
    except GHError:
        return {'priority': None, 'triaged': None, 'assigned': None, 'assignee_login': None}


def _priority_label_search(max_priority: int) -> str:
    """Return a GitHub search label qualifier matching Priority 0..max_priority (OR).

    GitHub search treats comma-separated label values as OR:
      label:"Priority: 0","Priority: 1","Priority: 2"
    """
    labels = ','.join(
        f'"Priority: {p}"' for p in range(min(max_priority, _MAX_PRIORITY) + 1)
    )
    return f'label:{labels}'


def get_pr_queue_count(repo: str, max_priority: int | None) -> int:
    """Count open non-draft PRs at equal or higher priority.

    If max_priority is None (untriaged), counts all open non-draft PRs.
    Only surfaced in comments when there is no PR assignee.
    """
    if max_priority is None:
        search = 'draft:false'
    else:
        search = f'draft:false {_priority_label_search(max_priority)}'
    try:
        prs = gh_json([
            'pr', 'list', '--repo', repo,
            '--state', 'open',
            '--search', search,
            '--limit', '500', '--json', 'number',
        ])
        return len(prs)
    except GHError:
        return 0


def get_assignee_pr_count(repo: str, assignee: str, max_priority: int | None) -> int:
    """Count open non-draft PRs assigned to `assignee` at equal or higher priority.

    If max_priority is None (untriaged), counts all open non-draft PRs for the assignee.
    If max_priority is set, uses GitHub label OR syntax to match P0..Pmax in one query.
    """
    if max_priority is None:
        search = 'draft:false'
    else:
        search = f'draft:false {_priority_label_search(max_priority)}'
    try:
        prs = gh_json([
            'pr', 'list', '--repo', repo,
            '--assignee', assignee,
            '--state', 'open',
            '--search', search,
            '--limit', '200', '--json', 'number',
        ])
        return len(prs)
    except GHError:
        return 0


def get_assignee_issue_count(repo: str, assignee: str, max_priority: int) -> int:
    total = 0
    for p in range(min(max_priority, _MAX_PRIORITY) + 1):
        try:
            issues = gh_json([
                'issue', 'list', '--repo', repo,
                '--assignee', assignee, '--label', f'Priority: {p}',
                '--state', 'open', '--limit', '200', '--json', 'number',
            ])
            total += len(issues)
            time.sleep(0.5)
        except GHError:
            pass
    return total


def get_pr_files(repo: str, pr_number: int) -> tuple[list[dict], list[dict]]:
    """Return (files_changed, test_files) for the PR."""
    files_changed = []
    test_files = []
    try:
        files = gh_json(['api', f'repos/{repo}/pulls/{pr_number}/files'])
        for f in files:
            fname = f.get('filename', '')
            files_changed.append({
                'path': fname,
                'additions': f.get('additions', 0),
                'deletions': f.get('deletions', 0),
            })
            if 'test' in fname.lower() and f.get('patch'):
                test_files.append({'path': fname, 'patch': f['patch'][:2000]})
    except GHError:
        pass
    return files_changed[:50], test_files[:5]


def get_commit_messages(repo: str, pr_number: int) -> list[str]:
    try:
        commits = gh_json(['api', f'repos/{repo}/pulls/{pr_number}/commits'])
        return [
            c.get('commit', {}).get('message', '').splitlines()[0]
            for c in commits
        ]
    except GHError:
        return []


# ---------------------------------------------------------------------------
# Copilot review request
# ---------------------------------------------------------------------------


def request_copilot_review(repo: str, pr_number: int, dry_run: bool) -> bool:
    """Returns True if the review was successfully requested (or dry-run).

    Uses the REST API directly — 'gh pr edit --add-reviewer copilot' does not
    resolve the Copilot bot. The correct slug is 'copilot-pull-request-reviewer[bot]'.
    """
    return gh_run(
        [
            'api', f'repos/{repo}/pulls/{pr_number}/requested_reviewers',
            '--method', 'POST',
            '--field', 'reviewers[]=copilot-pull-request-reviewer[bot]',
        ],
        dry_run=dry_run,
        action_desc=f'Request Copilot review on PR #{pr_number}',
    )


# ---------------------------------------------------------------------------
# Per-PR processing
# ---------------------------------------------------------------------------


def process_pr(repo: str, pr: dict, dry_run: bool) -> dict | None:
    """
    Check eligibility, assign Copilot, gather signals.
    Returns a data dict for the Claude Code agent, or None if the PR is skipped.
    All skip/status messages go to stderr to keep stdout clean for JSON.
    """
    pr_number = pr['number']
    author = pr.get('author', {}).get('login', 'unknown')
    print(f'PR #{pr_number}: "{pr.get("title", "")}" by @{author}', file=sys.stderr)

    if copilot_already_assigned(repo, pr_number):
        print('  Copilot already assigned — skipping.', file=sys.stderr)
        return None
    time.sleep(0.5)

    has_comments, has_marker = check_comments(repo, pr_number)
    if has_comments:
        print('  Already has comments — skipping.', file=sys.stderr)
        return None
    if has_marker:
        print('  Bot marker found — skipping.', file=sys.stderr)
        return None
    time.sleep(0.5)

    # Assign Copilot
    copilot_assigned = request_copilot_review(repo, pr_number, dry_run)
    if not copilot_assigned:
        print('  Copilot assignment failed — will include in output.', file=sys.stderr)
    time.sleep(1)

    # Gather signals
    pr_body = pr.get('body') or ''
    first_timer = is_first_contribution(repo, author)
    time.sleep(0.5)
    has_ref = has_issue_reference(pr_body)
    visual = has_visual_evidence(pr_body)
    is_design = is_design_pr(repo, pr_number, pr.get('labels', []))
    time.sleep(0.5)
    ci_fail = is_ci_failing(repo, pr_number)
    time.sleep(0.5)

    # Linked issue + assignee workload
    m = ISSUE_REF_RE.search(pr_body)
    linked_issue_number = m.group(1) if m else None
    linked_issue_priority = None
    linked_issue_triaged = None
    linked_issue_assigned = None
    linked_issue_assignee = None
    assignee_issue_count = None
    pr_queue_count = None
    if linked_issue_number:
        issue_details = get_linked_issue_details(repo, linked_issue_number)
        linked_issue_priority = issue_details['priority']
        linked_issue_triaged = issue_details['triaged']
        linked_issue_assigned = issue_details['assigned']
        linked_issue_assignee = issue_details['assignee_login']
        time.sleep(0.5)
    assignees = pr.get('assignees', [])
    if assignees and linked_issue_priority is not None:
        assignee_issue_count = get_assignee_issue_count(
            repo, assignees[0]['login'], linked_issue_priority
        )
        time.sleep(0.5)
    # Always compute queue count; pass None if untriaged (counts all open non-draft PRs)
    pr_queue_count = get_pr_queue_count(repo, linked_issue_priority)
    time.sleep(0.5)
    # PR assignee takes precedence over issue assignee for workload count
    pr_assignee_login = assignees[0]['login'] if assignees else linked_issue_assignee
    assignee_pr_count = None
    if pr_assignee_login:
        assignee_pr_count = get_assignee_pr_count(repo, pr_assignee_login, linked_issue_priority)
        time.sleep(0.5)

    # Rich PR data for Claude's analysis
    files_changed, test_files = get_pr_files(repo, pr_number)
    time.sleep(0.5)
    commit_messages = get_commit_messages(repo, pr_number)

    print(
        f'  Signals: first_timer={first_timer} has_issue_ref={has_ref} '
        f'design={is_design} visual={visual} ci_failing={ci_fail}',
        file=sys.stderr,
    )

    return {
        'number': pr_number,
        'title': pr.get('title', ''),
        'url': pr.get('url', ''),
        'author': author,
        'body': pr_body[:3000],
        'labels': [lb.get('name', '') for lb in pr.get('labels', [])],
        'assignees': [{'login': a['login']} for a in assignees],
        'first_contribution': first_timer,
        'has_issue_reference': has_ref,
        'linked_issue_number': linked_issue_number,
        'linked_issue_priority': linked_issue_priority,
        'linked_issue_triaged': linked_issue_triaged,
        'linked_issue_assigned': linked_issue_assigned,
        'linked_issue_assignee': linked_issue_assignee,
        'assignee_issue_count': assignee_issue_count,
        'pr_queue_count': pr_queue_count,
        'pr_assignee_login': pr_assignee_login,
        'assignee_pr_count': assignee_pr_count,
        'is_design_pr': is_design,
        'has_visual_evidence': visual,
        'ci_failing': ci_fail,
        'copilot_assigned': copilot_assigned,
        'files_changed': files_changed,
        'test_files': test_files,
        'commit_messages': commit_messages,
        'dry_run': dry_run,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--hours', type=float, default=1.0,
        help='Look back this many hours for newly opened PRs (default: 1)',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Skip Copilot assignment; set dry_run=true in JSON so the '
             'Claude Code agent prints rather than posts.',
    )
    parser.add_argument(
        '--repo', default=DEFAULT_REPO,
        help=f'GitHub repo in owner/repo format (default: {DEFAULT_REPO})',
    )
    return parser


def main() -> None:
    args = _get_parser().parse_args()

    print(
        f'Fetching non-draft PRs opened in the last {args.hours}h on {args.repo}...',
        file=sys.stderr,
    )
    try:
        prs = get_recent_prs(args.repo, args.hours)
    except GHError as exc:
        print(f'Failed to fetch PRs: {exc}', file=sys.stderr)
        sys.exit(1)

    print(f'Found {len(prs)} candidate PR(s).', file=sys.stderr)

    results = []
    for pr in prs:
        try:
            data = process_pr(args.repo, pr, dry_run=args.dry_run)
            if data:
                results.append(data)
        except GHError as exc:
            print(f'  GH error on PR #{pr.get("number")}: {exc}', file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f'  Unexpected error on PR #{pr.get("number")}: {exc}', file=sys.stderr)

    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
