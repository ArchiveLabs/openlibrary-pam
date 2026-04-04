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

## What the bot does for each eligible PR

### 1. Assign Copilot as reviewer

`gh pr edit {number} --add-reviewer copilot`

This serves two purposes: it triggers GitHub Copilot's automated code review, and it acts as the "we've seen this PR" flag for future runs.

> **Note for maintainers:** If the `copilot` reviewer identifier ever stops working (GitHub may change this), look for the correct handle in your repository's reviewer suggestions UI and update the string `'copilot'` in `request_copilot_review()`.

### 2. Gather hard-coded signals

These are cheap binary checks done via `gh` API calls before the LLM is invoked:

| Signal | Method | Used for |
|---|---|---|
| First-time contributor | Count all PRs by this author (`--state all`) == 1 | Decide whether to show the welcome + triage block |
| Issue reference | Regex `#\d+` anywhere in PR body | Passed to LLM as `has_linked_issue`; informs quality concern judgment |
| Open issue check | Verify the referenced issue is actually open via API | Passed to LLM — a closed or missing issue may indicate a misdirected PR |
| CI status | GitHub check-runs API on head commit SHA; treats `failure`, `timed_out`, `cancelled`, `action_required` as failing | Hard-coded ⛔ section |
| Design PR | Label contains "design" OR files touch `static/css/` | Hard-coded 📸 section |
| Visual evidence | Regex for markdown images, video URLs, GitHub CDN | Suppresses 📸 section if present |

### 3. LLM analysis (Claude)

The script calls `claude-opus-4-6` with a structured prompt and a JSON payload containing:

- PR title and body (capped at 3000 chars)
- List of changed files with addition/deletion counts
- Content of changed test files (capped at 2000 chars per file, up to 5 files)
- All commit messages
- Labels
- The pre-computed hard-coded signals above

Claude returns a JSON object with four nullable fields:

```json
{
  "quality_concern": null,     // unprompted refactor / no clear purpose
  "test_concern": null,        // missing or over-engineered tests
  "git_concern": null,         // messy commit history, upstream merges
  "template_concern": null     // obviously missing Testing or Screenshot sections
}
```

Each non-null value is a 2–4 sentence markdown string ready to paste directly into the comment. Claude is instructed to return `null` unless a concern is clearly warranted — erring toward silence over noise.

If `ANTHROPIC_API_KEY` is not set or the `anthropic` package is not installed, the LLM step is skipped (a `[LLM] ... skipping LLM analysis.` message is printed to stdout) and only the hard-coded sections appear.

### 4. Assemble and post the comment

Sections appear in this order, each only if its condition is met:

```
:tada:  First-timer welcome                      (if first contribution)
        Assignee workload OR Mon/Fri triage msg   (if first contribution)
🤖      Copilot mention                           (if first contribution)

🚦      quality_concern    (LLM)
🧪      test_concern       (LLM)
⚠️      git_concern        (LLM)
🚦      template_concern   (LLM)

📸      No screenshot      (hard-coded, design PRs without visual evidence,
                            only if LLM didn't already flag template_concern)
⛔      CI failing         (hard-coded)

        Footer + <!-- ol-pr-bot --> marker
```

If no section fires, no comment is posted.

---

## Design decisions and trade-offs

### Why LLM for some checks and not others?

Binary checks (CI status, first-timer, issue reference, visual evidence) are cheap, reliable, and have no need for judgment. They're done with API calls.

Nuanced checks (is the PR a pointless refactor? are tests meaningful? is the commit history confusing?) benefit enormously from natural language understanding. A regex for "messy commits" would produce false positives. Claude can read "feat: add user endpoint" vs "fix", "fix2", "oops", "final" and understand which pattern suggests a contributor who might benefit from a rebase tip.

### Why structured segments instead of a fully free-form LLM comment?

Two reasons:

1. **Predictability.** Maintainers and contributors can know what to expect. The hard-coded sections (welcome, CI, screenshot) are guaranteed to appear in the right cases regardless of what the LLM says.
2. **Graceful degradation.** If the LLM API is down or the key is missing, the hard-coded sections still post. The comment is never empty because of an API outage.

### Why Copilot-assigned = staff PR?

This is a heuristic, not a rule. The reasoning: staff members know the project well and don't need onboarding guidance; and if they submit a PR, Copilot will already be assigned through their own workflow. If this assumption breaks down (e.g., a prolific community contributor always assigns Copilot manually), the bot will silently skip their PRs. That's an acceptable false negative — missing one comment is better than spamming a contributor who doesn't need it.

### Why use the personal `mek` account instead of a bot account?

Comments appear more personal and trustworthy coming from a real human account. The downside is that mek's GitHub notifications will include replies to these automated comments. If that becomes noisy, the right solution is to create a dedicated account and authenticate `gh` with that account's token.

### Why `claude-opus-4-6`?

Tone judgment and nuanced mentorship require the most capable model. The cost per PR is low (a few cents at most) and the quality difference versus Sonnet is noticeable for the kind of "read between the lines" analysis we want. If costs become a concern, switch to Sonnet in the `LLM_MODEL` constant.

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
2. Call it in `process_pr()` and add the result to `signals`
3. Pass it to `build_comment()` and add the corresponding section there

---

## Running and scheduling

### Manual run (dry-run first)

```bash
# See what would happen without touching GitHub
ANTHROPIC_API_KEY=sk-... python3 scripts/gh_scripts/new_pr_bot.py --dry-run --hours 24

# Live run against the last hour
ANTHROPIC_API_KEY=sk-... python3 scripts/gh_scripts/new_pr_bot.py
```

### Dependencies

```bash
pip install anthropic>=0.40.0
# gh CLI must be authenticated: gh auth login
```

### Scheduling via Claude Code

Use the `/schedule` skill in Claude Code to run hourly:

```
Every hour: python3 /path/to/openlibrary/scripts/gh_scripts/new_pr_bot.py
```

Make sure `ANTHROPIC_API_KEY` is available in the environment where the schedule runs.

### Scheduling via cron

```cron
0 * * * * cd /path/to/openlibrary && ANTHROPIC_API_KEY=sk-... python3 scripts/gh_scripts/new_pr_bot.py >> /tmp/pr_bot.log 2>&1
```

---

## Canonical resource URLs

These URLs are the authoritative links cited in bot comments and injected into the LLM system prompt. They are defined as named constants at the top of `new_pr_bot.py` so both the script and any AI agent reading this file have a single place to update them.

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
