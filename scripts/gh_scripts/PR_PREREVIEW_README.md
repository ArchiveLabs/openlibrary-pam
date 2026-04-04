# PR Pre-Review Bot

**File:** `scripts/gh_scripts/new_pr_bot.py`

This document is the authoritative record of what the bot does, why it works the way it does, and how to change it. It is written for future maintainers *and* for Claude or another AI agent reading this to understand context and adapt the bot over time.

---

## Why this exists

Open Library receives pull requests from community contributors who often have no context about:

- How long to expect to wait before someone reviews their work
- Who will review it and how busy that person is
- What the project actually needs from a PR (tests, screenshots, linked issues)
- Whether their git history or code style might cause friction

Without any automated first-touch, contributors either feel ignored or open follow-up issues asking for status. Maintainers end up re-explaining the same things repeatedly. The bot's job is to close that gap: give every community contributor a warm, specific, and timely acknowledgment the moment their PR lands, before any human has even seen it.

---

## Trigger logic: which PRs get processed

A PR is processed only if **all three** conditions hold:

| Condition | Why |
|---|---|
| Not a draft | Draft PRs are not ready for review; commenting would be premature. |
| No existing issue-thread comments | If anyone (human or bot) has already commented, the PR has been attended to. |
| Copilot **not** already assigned as reviewer | This is the key signal: if Copilot is already on the PR, a staff member opened it and it doesn't need the community onboarding treatment. It also serves as the primary idempotency guard — once our bot runs, it assigns Copilot, so the next hourly run will skip this PR. |

A secondary idempotency guard (`<!-- ol-pr-bot -->` HTML comment embedded in every comment we post) protects against edge cases where the Copilot assignment fails but the comment succeeds.

---

## How it works: two-part architecture

The system has two components that work together each hour:

**Part 1 — `new_pr_bot.py` (data gatherer, runs first)**

For each eligible PR the script:
1. Assigns Copilot as reviewer (`gh pr edit {number} --add-reviewer copilot`)
2. Gathers signals via `gh` API calls
3. Prints a JSON array to stdout — one object per eligible PR

> **Note:** If the `copilot` reviewer identifier ever stops working, update the string `'copilot'` in `request_copilot_review()` and the `_COPILOT_LOGINS` set.

Signals gathered per PR:

| Field | Method |
|---|---|
| `first_contribution` | Count all PRs by this author (`--state all`) == 1 |
| `has_issue_reference` | Regex `#\d+` anywhere in PR body |
| `linked_issue_number` | First `#NNN` match in body |
| `linked_issue_priority` | Priority label on linked issue (`Priority: 0/1/2`) |
| `assignee_issue_count` | Open issues for the assignee at equal/higher priority |
| `ci_failing` | check-runs API; treats `failure`, `timed_out`, `cancelled`, `action_required` as failing |
| `is_design_pr` | Label contains "design" OR files touch `static/css/` |
| `has_visual_evidence` | Regex for markdown images, video URLs, GitHub CDN in body |
| `files_changed` | List of `{path, additions, deletions}` |
| `test_files` | Patches of changed test files (capped at 2000 chars each) |
| `commit_messages` | First line of each commit message |
| `copilot_assigned` | Whether the Copilot assignment succeeded |
| `dry_run` | True if `--dry-run` was passed |

**Part 2 — Claude Code agent (reads JSON, writes comments)**

The agent is invoked with the prompt in the **Scheduling** section below. It reads the JSON output, uses this README as its guide, and posts a comment on each PR via `gh pr comment`. If `dry_run` is true, it prints what it would post instead.

### Comment structure Claude should follow

```
:tada:  First-timer welcome                      (if first_contribution)
        Assignee workload OR Mon/Fri triage msg   (if first_contribution)
🤖      Copilot mention                           (if first_contribution)

        Quality / test / git / template concerns  (Claude's judgment)

📸      No screenshot                             (if is_design_pr and not has_visual_evidence)
⛔      CI failing                                (if ci_failing)

        Footer + <!-- ol-pr-bot --> marker
```

If nothing warrants a comment, post nothing.

---

## Design decisions and trade-offs

### Why split into script + Claude Code agent?

Binary checks (CI status, first-timer, issue reference, visual evidence) are cheap, reliable, and don't need judgment — the script handles them with `gh` API calls.

Nuanced analysis (is this PR a pointless refactor? are tests meaningful? does the commit history show confusion?) benefits from natural language understanding. Claude Code provides this without needing a separate API key or credits — it uses the same session the maintainer already has.

### Why does the script output JSON instead of posting directly?

It separates concerns cleanly: the script knows GitHub's data model, Claude Code knows how to read context and write warmly. It also makes the system easy to test — pipe the JSON output anywhere, inspect it, replay it.

### Why Copilot-assigned = staff PR?

This is a heuristic, not a rule. The reasoning: staff members know the project well and don't need onboarding guidance; and if they submit a PR, Copilot will already be assigned through their own workflow. If this assumption breaks down (e.g., a prolific community contributor always assigns Copilot manually), the bot will silently skip their PRs. That's an acceptable false negative — missing one comment is better than spamming a contributor who doesn't need it.

### Why use the personal `mek` account instead of a bot account?

Two practical reasons: mek's account holds the Copilot credits that make the reviewer assignment possible, and it has the GitHub permissions required to add Copilot as a reviewer on PRs in this org. Beyond the technical requirement, using a personal account means accountability is clear — if a contributor replies or needs follow-up, the thread lands with the maintainer who can actually act on it.

---

## Tuning guide

### Adjusting what Claude flags

Edit `LLM_SYSTEM_PROMPT` in `new_pr_bot.py`. The guidelines for each field are clearly separated. Key levers:

- To make Claude **less aggressive** about a concern: add "When in doubt, return null" or "Only flag if X is very obvious."
- To make Claude **more specific**: add examples of what you want flagged or not flagged.
- To **add a new concern type**: add a new field to the JSON schema, add a guideline section, and handle the new key in `build_comment()`.

### Changing the message text

Hard-coded section text lives in `build_comment()`. LLM-generated text is written by Claude according to the system prompt — to change its tone or content, edit the prompt.

### Adjusting the trigger window

Default lookback is 1 hour (`--hours 1`). For testing, use `--hours 24` or `--hours 48` to catch real recent PRs.

### Adding a new hard-coded signal

1. Add a function (following the pattern of `is_ci_failing`, `is_design_pr`, etc.)
2. Call it in `process_pr()` and include the result in the returned dict
3. The Claude Code agent will see it in the JSON and can act on it if the README describes the expected behaviour

---

## Running and scheduling

### Dependencies

```bash
# No Python packages needed — stdlib + gh CLI only
gh auth login   # if not already authenticated
```

### Manual run (dry-run)

```bash
cd /path/to/openlibrary-pam
python3 scripts/gh_scripts/new_pr_bot.py --dry-run --hours 24
```

Status messages go to stderr; the JSON array goes to stdout. Pipe to `python3 -m json.tool` to pretty-print.

### Claude Code scheduled prompt

Use `/schedule` in Claude Code (or set up a desktop scheduled task) with this prompt:

```
cd /path/to/openlibrary-pam
Run: python3 scripts/gh_scripts/new_pr_bot.py --hours 1

Read the JSON array from stdout. For each PR object, read
scripts/gh_scripts/PR_PREREVIEW_README.md to decide what comment to post.
Then post it with:
  gh pr comment {number} --repo internetarchive/openlibrary --body "COMMENT"
Always include <!-- ol-pr-bot --> at the end of every comment you post.
If dry_run is true in the entry, print the comment instead of posting it.
```

---

## Canonical resource URLs

These URLs are cited in bot comments by the Claude Code agent. They are defined as named constants at the top of `new_pr_bot.py` so both the script and any AI agent reading this file have a single place to update them.

| Constant | URL | Purpose |
|---|---|---|
| `GIT_CHEATSHEET_URL` | https://github.com/internetarchive/openlibrary/wiki/git | Linked when flagging messy commit history |
| `PR_TEMPLATE_URL` | https://github.com/internetarchive/openlibrary/blob/master/.github/pull_request_template.md | Linked for template compliance concerns |
| `PRECOMMIT_GUIDE_URL` | https://docs.openlibrary.org/developers/tools/pre-commit.html | Linked in CI-failing section |
| `SCREENSHOT_GUIDE_URL` | https://github.com/internetarchive/openlibrary/wiki/Testing-&-Tools#screenshots | Linked in missing-screenshot section |
| `CONTRIBUTING_URL` | https://github.com/internetarchive/openlibrary/blob/master/CONTRIBUTING.md | Referenced in quality concern guidance |

---

## Known limitations and future ideas

- **Copilot reviewer handle**: The string `'copilot'` may need updating if GitHub changes the app's login. Test with `gh pr edit <PR> --add-reviewer copilot` manually if reviews stop being requested.
- **First-timer detection races**: If a contributor opens two PRs within the same hour, both will be treated as first-timer PRs. Acceptable edge case.
- **LLM hallucinations**: Claude might occasionally flag a concern that isn't really there. The system prompt instructs it to err toward silence, but if you notice consistent false positives on a particular type of PR, add a clarifying example to the prompt's guidelines.
- **Response to bot comments**: Contributors may reply to the bot comment with questions. Those replies will trigger `has_any_comment` on future runs and prevent duplicate comments — but nobody is automatically notified of replies. Consider adding a Slack notification for replies (similar to `issue_comment_bot.py`).
- **Multi-language PRs**: The system prompt is English-only. If a contributor writes their PR body in another language, Claude will still respond in English. This is probably fine for now.
- **Priority label format**: The priority lookup assumes labels formatted as `Priority: 0`, `Priority: 1`, etc. If the label schema changes, update `get_issue_priority()`.
