#!/usr/bin/env python3
"""
Open Library PR pre-review bot.

Runs hourly. For every recently-opened, non-draft PR that has no comments yet
and no Copilot reviewer assigned:

  1. Assigns Copilot as a reviewer (the "staff PR" signal — see README).
  2. Builds a comment from two layers:
       a. Hard-coded template sections (first-timer welcome, assignee/triage,
          CI failure notice) determined by cheap API checks.
       b. LLM-generated sections where nuanced judgment is needed (PR quality,
          test coverage, git hygiene, template compliance). Claude returns
          null for any field where no concern is warranted.
  3. Posts the assembled comment.

See PR_PREREVIEW_README.md for full rationale and tuning guide.

Usage:
    python3 new_pr_bot.py [--hours N] [--dry-run] [--repo owner/repo]

Environment:
    ANTHROPIC_API_KEY   Required for LLM analysis (falls back gracefully if absent)
    gh CLI              Must be authenticated via `gh auth login`
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Optional Anthropic import — degrade gracefully if not installed
# ---------------------------------------------------------------------------
try:
    import anthropic as _anthropic

    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_REPO = 'internetarchive/openlibrary'
BOT_MARKER = '<!-- ol-pr-bot -->'
LLM_MODEL = 'claude-opus-4-6'

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

# Resource URLs referenced in comments
GIT_CHEATSHEET_URL = 'https://github.com/internetarchive/openlibrary/wiki/git'
PRECOMMIT_GUIDE_URL = 'https://docs.openlibrary.org/developers/tools/pre-commit.html'
PR_TEMPLATE_URL = (
    'https://github.com/internetarchive/openlibrary/blob/master/'
    '.github/pull_request_template.md'
)
SCREENSHOT_GUIDE_URL = (
    'https://github.com/internetarchive/openlibrary/wiki/Testing-&-Tools#screenshots'
)

# ---------------------------------------------------------------------------
# System prompt for LLM analysis
# ---------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """\
You are a warm, experienced open-source mentor reviewing a new pull request for \
the Open Library project (https://openlibrary.org), a non-profit digital library \
run by the Internet Archive. Contributors range from first-timers to seasoned \
engineers, so your tone should always be encouraging and specific — like a senior \
teammate leaving a code review, never a gatekeeper.

Your job is to flag genuine concerns that would make this PR hard to review or \
merge. You will receive a JSON object describing the PR. Return a JSON object \
with exactly these four fields (each is either null or a short markdown string):

{
  "quality_concern": null,
  "test_concern": null,
  "git_concern": null,
  "template_concern": null
}

Guidelines for each field:

quality_concern
  Set to a message ONLY if the PR appears to be an unprompted, disruptive change
  with no clear motivation — e.g. a large codebase-wide refactor, style sweep, or
  dependency bump that wasn't discussed in an issue. Do NOT flag:
  - i18n/translation improvements
  - Accessibility fixes
  - Clear bug fixes, even small ones
  - PRs that reference an issue (has_linked_issue=true)
  When in doubt, leave null. Reviewers can ask; we don't want to discourage
  legitimate contributions.

test_concern
  Set to a message if the changed code paths clearly lack any tests AND the
  change is non-trivial. Also flag if tests look AI-generated (repetitive,
  exhaustive edge cases for trivial logic, over-mocked). We aim for meaningful
  coverage, not perfect coverage. Leave null for: pure template/CSS/i18n changes,
  small config tweaks, documentation.

git_concern
  Set to a message if the commit history shows clear confusion — e.g. multiple
  upstream merge commits ("Merge branch 'master'"), dozens of "fix", "wip",
  "update" commits suggesting iterative trial-and-error without cleanup. When
  flagging, name the specific pattern you saw and suggest one concrete technique
  (e.g. interactive rebase, squash). Link to our git cheatsheet:
  https://github.com/internetarchive/openlibrary/wiki/git

template_concern
  Set to a message ONLY if a required template section is obviously empty or
  missing AND it matters for reviewability — specifically: the Testing section
  (how to verify the fix) or the Screenshot section for UI changes. Don't flag
  minor template gaps; prioritize the ones that block reviewers from doing their
  job.

Style rules:
- 2–4 sentences max per field. Be specific (mention actual file names, commit
  messages, or sections from what you see).
- Use second person ("We noticed...", "It looks like..."), never accusatory.
- End each message with a link to a relevant resource when one exists.
- Return ONLY valid JSON. No prose outside the JSON object.
"""


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class GHError(Exception):
    """Raised when a gh CLI call fails."""


# ---------------------------------------------------------------------------
# gh CLI helpers
# ---------------------------------------------------------------------------


def _run_gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(['gh'] + args, capture_output=True, text=True)


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
    """Run a mutative gh command; in dry-run mode, print instead."""
    if dry_run:
        print(f'  [DRY RUN] {action_desc}')
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


def has_any_comment(repo: str, pr_number: int) -> bool:
    """Return True if the PR has any issue-thread comments (not the PR body)."""
    try:
        comments = gh_json(['api', f'repos/{repo}/issues/{pr_number}/comments'])
        return bool(comments)
    except GHError:
        return False


def copilot_already_assigned(repo: str, pr_number: int) -> bool:
    """Return True if Copilot is already a requested reviewer or has reviewed."""
    try:
        requested = gh_json(['api', f'repos/{repo}/pulls/{pr_number}/requested_reviewers'])
        for user in requested.get('users', []):
            if 'copilot' in user.get('login', '').lower():
                return True
        reviews = gh_json(['api', f'repos/{repo}/pulls/{pr_number}/reviews'])
        for review in reviews:
            login = (review.get('user') or {}).get('login', '')
            if 'copilot' in login.lower():
                return True
    except GHError:
        pass
    return False


def has_bot_comment(repo: str, pr_number: int) -> bool:
    """Secondary idempotency guard: check for our HTML marker."""
    try:
        comments = gh_json(['api', f'repos/{repo}/issues/{pr_number}/comments'])
        return any(BOT_MARKER in (c.get('body') or '') for c in comments)
    except GHError:
        return False


# ---------------------------------------------------------------------------
# Hard-coded signals
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
        return any(c.get('conclusion') == 'failure' for c in data.get('check_runs', []))
    except GHError:
        pass
    # Fallback to gh pr checks text output
    result = _run_gh(['pr', 'checks', str(pr_number), '--repo', repo])
    return result.returncode == 0 and 'fail' in result.stdout.lower()


def get_issue_priority(repo: str, issue_number: str) -> int | None:
    try:
        issue = gh_json(['api', f'repos/{repo}/issues/{issue_number}'])
        for label in issue.get('labels', []):
            m = re.match(r'Priority:\s*(\d+)', label.get('name', ''), re.IGNORECASE)
            if m:
                return int(m.group(1))
    except GHError:
        pass
    return None


def get_assignee_issue_count(repo: str, assignee: str, max_priority: int) -> int:
    total = 0
    for p in range(max_priority + 1):
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


# ---------------------------------------------------------------------------
# LLM analysis
# ---------------------------------------------------------------------------


def _get_pr_data_for_llm(repo: str, pr: dict) -> dict:
    """Collect all data needed for LLM analysis."""
    pr_number = pr['number']
    pr_body = pr.get('body') or ''

    # Fetch changed files
    files_changed = []
    test_files = []
    try:
        files = gh_json(['api', f'repos/{repo}/pulls/{pr_number}/files'])
        for f in files:
            fname = f.get('filename', '')
            additions = f.get('additions', 0)
            deletions = f.get('deletions', 0)
            files_changed.append({'path': fname, 'additions': additions, 'deletions': deletions})
            # Include patch for test files (they're the most relevant for LLM analysis)
            if 'test' in fname.lower() and f.get('patch'):
                test_files.append({'path': fname, 'patch': f['patch'][:2000]})
    except GHError:
        pass

    # Fetch commit messages
    commit_messages = []
    try:
        commits = gh_json(['api', f'repos/{repo}/pulls/{pr_number}/commits'])
        commit_messages = [
            c.get('commit', {}).get('message', '').splitlines()[0]
            for c in commits
        ]
    except GHError:
        pass

    return {
        'pr_title': pr.get('title', ''),
        'pr_body': pr_body[:3000],  # cap to avoid huge token counts
        'files_changed': files_changed[:50],
        'test_files': test_files[:5],
        'commit_messages': commit_messages,
        'labels': [lb.get('name', '') for lb in pr.get('labels', [])],
    }


def analyze_pr_with_llm(
    repo: str,
    pr: dict,
    signals: dict,
) -> dict:
    """
    Call Claude to evaluate the PR and return a dict of concern fields.
    Each field is either None or a short markdown string.
    Falls back to all-None if the API is unavailable or returns bad JSON.
    """
    empty = {
        'quality_concern': None,
        'test_concern': None,
        'git_concern': None,
        'template_concern': None,
    }

    if not _ANTHROPIC_AVAILABLE:
        print('  [LLM] anthropic package not installed — skipping LLM analysis.')
        return empty

    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        print('  [LLM] ANTHROPIC_API_KEY not set — skipping LLM analysis.')
        return empty

    pr_data = _get_pr_data_for_llm(repo, pr)
    pr_data.update(signals)  # merge in the pre-computed signals

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=1024,
            system=LLM_SYSTEM_PROMPT,
            messages=[
                {
                    'role': 'user',
                    'content': (
                        'Please analyze this pull request and return your assessment as JSON.\n\n'
                        + json.dumps(pr_data, indent=2)
                    ),
                }
            ],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith('```'):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)
        # Normalize: keep only the expected keys, coerce empty strings to None
        normalized = {}
        for key in empty:
            val = result.get(key)
            normalized[key] = val if val else None
        return normalized
    except Exception as exc:  # noqa: BLE001
        print(f'  [LLM] Analysis failed ({exc!r}) — posting hard-coded sections only.')
        return empty


# ---------------------------------------------------------------------------
# Copilot review request
# ---------------------------------------------------------------------------


def request_copilot_review(repo: str, pr_number: int, dry_run: bool) -> bool:
    """Returns True if the review was successfully requested (or dry-run)."""
    return gh_run(
        ['pr', 'edit', str(pr_number), '--repo', repo, '--add-reviewer', 'copilot'],
        dry_run=dry_run,
        action_desc=f'Request Copilot review on PR #{pr_number}',
    )


# ---------------------------------------------------------------------------
# Comment assembly
# ---------------------------------------------------------------------------


def build_comment(
    repo: str,
    pr: dict,
    first_timer: bool,
    has_issue_ref: bool,
    is_design: bool,
    visual_evidence: bool,
    ci_failing: bool,
    llm_concerns: dict,
    copilot_assigned: bool = True,
) -> str:
    parts: list[str] = []
    pr_body = pr.get('body') or ''

    # --- First-timer welcome ---
    if first_timer:
        parts.append(':tada: Thank you for making your first contribution to Open Library!\n')

        assignees = pr.get('assignees', [])
        if assignees:
            handle = assignees[0]['login']
            # Try to find the linked issue for priority context
            m = ISSUE_REF_RE.search(pr_body)
            issue_number = m.group(1) if m else None
            priority = get_issue_priority(repo, issue_number) if issue_number else None
            if priority is not None:
                count = get_assignee_issue_count(repo, handle, priority)
                parts.append(
                    f'This PR is assigned to @{handle} and they currently have '
                    f'**{count}** open issue(s) with equal or higher priority to review. '
                    f'Please be patient — it may take several days for maintainers to reply.'
                )
            else:
                parts.append(
                    f'This PR is assigned to @{handle}. '
                    f'Please be patient — it may take several days for maintainers to reply.'
                )
        else:
            parts.append(
                'On Mondays and Fridays, maintainers try to meet and set assignees, '
                "so please hold tight until we're able to triage."
            )

        if copilot_assigned:
            parts.append(
                '\n🤖 In the meantime, we are assigning Copilot to offer an initial '
                'code review and feedback.'
            )
        else:
            parts.append(
                '\n🤖 We attempted to assign Copilot for an automated code review but '
                'were unable to at this time. A maintainer will follow up.'
            )

    # --- LLM-generated concerns ---
    emoji_map = {
        'quality_concern': '🚦',
        'template_concern': '🚦',
        'test_concern': '🧪',
        'git_concern': '⚠️',
    }
    for key, emoji in emoji_map.items():
        text = llm_concerns.get(key)
        if text:
            parts.append(f'{emoji} {text}')

    # --- Design PR without visual evidence (hard-coded fallback) ---
    # Only add if LLM didn't already flag a template concern
    if is_design and not visual_evidence and not llm_concerns.get('template_concern'):
        parts.append(
            f'📸 It really helps maintainers provide code reviews when PRs that touch '
            f'the UI include screenshots or a short video. You can drag a video or image '
            f'directly into this comment box. '
            f'[Here]({SCREENSHOT_GUIDE_URL}) are instructions for capturing a recording.'
        )

    # --- CI failing (hard-coded, always shown) ---
    if ci_failing:
        parts.append(
            f'⛔ It looks like some CI checks are failing — please check the **Checks** '
            f'tab for errors. Running our '
            f'[pre-commit hooks]({PRECOMMIT_GUIDE_URL}) locally before pushing can catch '
            f'most issues early.'
        )

    if not parts:
        return ''

    parts.append(
        '\nIf you have any questions about anything above, please reply here and '
        "we'll be happy to help!"
    )
    parts.append(BOT_MARKER)
    return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# Per-PR processing
# ---------------------------------------------------------------------------


def process_pr(repo: str, pr: dict, dry_run: bool) -> None:
    pr_number = pr['number']
    author = pr.get('author', {}).get('login', 'unknown')
    print(f'\nPR #{pr_number}: "{pr.get("title", "")}" by @{author}')

    # Skip if Copilot already assigned (staff PR or already processed)
    if copilot_already_assigned(repo, pr_number):
        print('  Copilot already assigned — skipping (staff PR or already processed).')
        return
    time.sleep(0.5)

    # Skip if any comments exist (already had human/bot attention)
    if has_any_comment(repo, pr_number):
        print('  Already has comments — skipping.')
        return
    time.sleep(0.5)

    # Belt-and-suspenders: skip if our marker is present
    if has_bot_comment(repo, pr_number):
        print('  Bot marker found — skipping.')
        return

    # 1. Assign Copilot (may fail if credit limit hit or handle is wrong)
    copilot_assigned = request_copilot_review(repo, pr_number, dry_run)
    if not copilot_assigned:
        print('  Copilot assignment failed — will note in comment and continue.')
    time.sleep(1)

    # 2. Gather hard-coded signals
    pr_body = pr.get('body') or ''
    first_timer = is_first_contribution(repo, author)
    time.sleep(0.5)
    has_ref = has_issue_reference(pr_body)
    visual = has_visual_evidence(pr_body)
    is_design = is_design_pr(repo, pr_number, pr.get('labels', []))
    time.sleep(0.5)
    ci_fail = is_ci_failing(repo, pr_number)
    time.sleep(0.5)

    signals = {
        'has_linked_issue': has_ref,
        'is_first_contribution': first_timer,
        'ci_failing': ci_fail,
        'is_design_pr': is_design,
        'has_visual_evidence': visual,
    }
    print(
        f'  Signals: first_timer={first_timer} has_issue_ref={has_ref} '
        f'design={is_design} visual={visual} ci_failing={ci_fail}'
    )

    # 3. LLM analysis
    print('  Running LLM analysis...')
    llm_concerns = analyze_pr_with_llm(repo, pr, signals)
    flagged = [k for k, v in llm_concerns.items() if v]
    print(f'  LLM flagged: {flagged or "nothing"}')

    # 4. Assemble comment
    comment = build_comment(
        repo=repo,
        pr=pr,
        first_timer=first_timer,
        has_issue_ref=has_ref,
        is_design=is_design,
        visual_evidence=visual,
        ci_failing=ci_fail,
        llm_concerns=llm_concerns,
        copilot_assigned=copilot_assigned,
    )

    if not comment:
        print('  Nothing to say — no comment posted.')
        return

    if dry_run:
        print(f'  [DRY RUN] Would post comment:\n{"─"*60}\n{comment}\n{"─"*60}')
    else:
        result = _run_gh(['pr', 'comment', str(pr_number), '--repo', repo, '--body', comment])
        if result.returncode == 0:
            print(f'  Posted comment on PR #{pr_number}.')
        else:
            print(f'  Failed to post comment: {result.stderr.strip()}', file=sys.stderr)


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
        help='Print all planned actions without writing anything to GitHub. '
             'Still calls the LLM for realistic output.',
    )
    parser.add_argument(
        '--repo', default=DEFAULT_REPO,
        help=f'GitHub repo in owner/repo format (default: {DEFAULT_REPO})',
    )
    return parser


def main() -> None:
    args = _get_parser().parse_args()

    if args.dry_run:
        print('[DRY RUN — no GitHub writes will occur]\n')

    print(f'Fetching non-draft PRs opened in the last {args.hours}h on {args.repo}...')
    try:
        prs = get_recent_prs(args.repo, args.hours)
    except GHError as exc:
        print(f'Failed to fetch PRs: {exc}', file=sys.stderr)
        sys.exit(1)

    print(f'Found {len(prs)} candidate PR(s).')
    if not prs:
        sys.exit(0)

    for pr in prs:
        try:
            process_pr(args.repo, pr, dry_run=args.dry_run)
        except GHError as exc:
            print(f'  GH error on PR #{pr.get("number")}: {exc}', file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f'  Unexpected error on PR #{pr.get("number")}: {exc}', file=sys.stderr)


if __name__ == '__main__':
    main()
