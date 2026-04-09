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

A PR is processed only if **all** conditions hold:

| Condition | Why |
|---|---|
| Not a draft | Draft PRs are not ready for review; commenting would be premature. |
| Author is not a lead or staff member | Leads and staff know the project and don't need onboarding guidance. Check the PR author's association via `gh api repos/internetarchive/openlibrary/pulls/{number} --jq '.user.login'` and compare against the staff list: `mekarpeles`, `cdrini`, `jimchamp`, `hornc`, `scottbarnes`, `seabelis`, `RayBB`, `lokesh`. If the author is any of these, skip entirely — post nothing. |
| No human non-author comments | If a human (other than the PR author) has already commented, the PR has been attended to. Author self-pings and Copilot comments do not count — a contributor saying "@RayBB sorry for the delay" or Copilot posting a review are not human attention. |
| No `<!-- ol-pr-bot -->` marker | Primary idempotency guard — once our bot has commented, the marker prevents double-posting regardless of Copilot status. |

Copilot already being assigned is **not** a skip condition. If Copilot was pre-assigned (e.g. by a staff member), the bot still posts its comment but omits the "Copilot has been assigned" line and does not re-assign.

---

## How it works: two-part architecture

The system has two components that work together each hour:

**Part 1 — `new_pr_bot.py` (data gatherer, runs first)**

For each eligible PR the script:
1. Assigns Copilot as reviewer (via REST API: `gh api repos/{repo}/pulls/{number}/requested_reviewers --method POST --field 'reviewers[]=copilot-pull-request-reviewer[bot]'`)
2. Gathers signals via `gh` API calls
3. Prints a JSON array to stdout — one object per eligible PR

> **Note:** If the Copilot assignment stops working, the slug to check is `copilot-pull-request-reviewer[bot]` in `request_copilot_review()`. Test with: `gh api repos/internetarchive/openlibrary/pulls/{PR}/requested_reviewers --method POST --field 'reviewers[]=copilot-pull-request-reviewer[bot]'`

Signals gathered per PR:

| Field | Method |
|---|---|
| `first_contribution` | Count all PRs by this author (`--state all`) == 1 |
| `has_issue_reference` | Regex `#\d+` anywhere in PR body |
| `linked_issue_number` | First `#NNN` match in body |
| `linked_issue_priority` | Priority label on linked issue (`Priority: 0/1/2`) |
| `linked_issue_triaged` | True if linked issue has a `Priority: *` label |
| `linked_issue_assigned` | True if linked issue has at least one assignee |
| `linked_issue_triaged` | True if linked issue has a `Priority: *` label |
| `linked_issue_assigned` | True if linked issue has at least one assignee |
| `pr_assignee_login` | PR assignee login if set, else linked issue assignee login (the effective reviewer) |
| `assignee_pr_count` | Open non-draft PRs assigned to `pr_assignee_login` at equal/higher priority |
| `pr_queue_count` | Open non-draft PRs at equal/higher priority (only surfaced when there is no `pr_assignee_login`) |
| `assignee_issue_count` | Open issues for the assignee at equal/higher priority |
| `ci_failing` | check-runs API; treats `failure`, `timed_out`, `action_required` as failing |
| `is_design_pr` | Label contains "design" OR files touch `static/css/` |
| `has_visual_evidence` | Regex for markdown images, video URLs, GitHub CDN in body |
| `files_changed` | List of `{path, additions, deletions}` |
| `test_files` | Patches of changed test files (capped at 2000 chars each) |
| `commit_messages` | First line of each commit message |
| `copilot_assigned` | Whether Copilot is assigned (either pre-existing or newly assigned) |
| `copilot_was_preassigned` | True if Copilot was already assigned before the bot ran — omit the "Copilot assigned" line from the comment in this case |
| `dry_run` | True if `--dry-run` was passed |

**Part 2 — Claude Code agent (reads JSON, writes comments)**

The agent is invoked with the prompt in the **Scheduling** section below. It reads the JSON output, uses this README as its guide, and posts a comment on each PR via `gh pr comment`. If `dry_run` is true, it prints what it would post instead.

### Comment structure

Every eligible PR gets a comment. The comment has three parts:

---

**Part 1 — Body (always present)**

Warm acknowledgment + project management context. Structure in this order:
1. Thank you line (+ first-timer welcome if `first_contribution` is true)
2. **Reviewer expectations immediately after** — use the following logic to tell the contributor when and by whom their PR will be reviewed. Write this as separate paragraphs, not a wall of text.

   First sentence — Copilot reviewer line:
   - If `copilot_was_preassigned` is true: omit any mention of assigning Copilot (it was already there). You may optionally note "Copilot has been assigned for an initial review." if contextually helpful, but do not say you assigned it.
   - If `copilot_was_preassigned` is false: "🤖 Copilot has been assigned for an initial review."

   Second paragraph — use exactly one of these branches based on the data:

   - **If `pr_assignee_login` is set** (there is an effective reviewer):
     - "@{pr_assignee_login} is assigned to this PR and currently has:" followed by a bullet:
       - `* {assignee_pr_count} open PR(s) of equal or higher priority to review first`
     - Do **not** mention triage status when an assignee exists — it's not relevant to the contributor's wait.

   - **If `pr_assignee_login` is not set** (no effective reviewer):
     - If `linked_issue_triaged` is false or null: "The linked issue hasn't been triaged yet — triage happens on Mondays and Fridays. There are currently {pr_queue_count} open non-draft PRs ahead of yours."
     - If triaged but no assignee: "A reviewer must first be assigned. There are currently {pr_queue_count} open PRs of equal or higher priority ahead of yours."

   **Key rule**: `pr_queue_count` is only ever mentioned when `pr_assignee_login` is null/empty. When an assignee exists, use `assignee_pr_count` instead.

---

**Part 2 — Possible improvements for this PR (only if checklist items fail)**

If any of the checklist items below require action from the contributor, surface them here under the heading `### Possible improvements for this PR` as `- [ ]` items before the collapsed checklist. Apply the `Needs: Submitter Input` label to the PR.

Example items that belong here:
- **PR description is empty or a near-empty template skeleton** — always surface this. If the body is just unfilled headings, say so explicitly and link to `PR_TEMPLATE_URL`. Do not let this slide even for small changes.
- No issue reference (for non-trivial changes)
- Messy commit history (WIP messages, merge conflicts in history)
- CI is failing
- No proof of testing for a substantive change

**General rule: when in doubt, say nothing.** A false positive is worse than a false negative. Only surface items you are confident about.

---

**Part 3 — Triage checklist (always present, collapsed)**

```markdown
<details>
<summary>PR triage checklist (maintainers / Pam)</summary>

- [ ] **PR description** — not empty; explains what the change does and how to verify it
- [ ] **References an issue** — PR body contains a `#NNN` reference
  - [ ] **Linked issue is triaged** — has a `Priority: *` label (not just `Needs: Triage`)
  - [ ] **Linked issue is assigned** — has at least one assignee
- [ ] **Commit history clean** — no WIP/fixup/conflict noise; commit messages are meaningful
- [ ] **CI passing** — no failing check-runs
- [ ] **Test cases present** — if the change touches substantive logic, test coverage exists or is explained
- [ ] **Proof of testing** — PR body includes a description of what was tested, a screenshot, or a video

</details>
```

Use `[x]` where the criterion is met, `[ ]` where it is not. Every item must be `- [ ]` or `- [x]`.

---

**Footer** — end every comment with the Pam attribution note followed by `<!-- ol-pr-bot -->`. **Never mention Claude, Claude Code, or Anthropic anywhere in the comment.**

```markdown
> [!NOTE]
> This comment was automatically generated by [Pam](https://github.com/ArchiveLabs/openlibrary-pam), Open Library's Project AI Manager, on behalf of @mekarpeles. Pam is designed to provide status visibility, perform basic project management functions and relevant codebase research, and provide actionable feedback so contributors aren't left waiting.
```

---

### Checklist guidance

#### PR description
- **Fail when**: `body` is empty, under ~100 characters, or is a near-empty template skeleton (headings present but nothing filled in).
- **Pass when**: title + body together make the change and its purpose clear.
- **When failing**: fetch the linked issue and the PR diff, then surface it as a submitter action item with a generated draft. Use the framing "For future PRs, please follow the PR template" — not "before this PR can be reviewed" — since this is guidance for next time, not a hard gate.

```bash
gh pr diff {number} --repo internetarchive/openlibrary
gh issue view {linked_issue_number} --repo internetarchive/openlibrary --json title,body
```

Format:
> For future PRs, please follow the [PR template]({PR_TEMPLATE_URL}) and provide a description. Here's a draft based on the diff and linked issue:
>
> > *[2–4 sentence summary of what the diff does and why, written in the contributor's voice. Reference the issue. Do not invent details not present in the diff or issue.]*

#### Issue reference
- **Fail when**: `has_issue_reference` is false AND the change is non-trivial (not a typo fix, pure docs update, or trivial config change).
- **Pass when**: the PR title/description is fully self-explanatory without an issue, or an issue reference is present.

#### Linked issue triaged / assigned
- Only evaluate if `linked_issue_number` is set.
- `linked_issue_triaged`: true if `linked_issue_triaged` is true in the JSON.
- `linked_issue_assigned`: true if `linked_issue_assigned` is true in the JSON.
- If the issue is not triaged, mention this kindly in the body — don't treat it as a hard blocker on the PR itself.

#### Commit history
- **Fail when**: `commit_messages` contains obvious noise — "WIP", "fix", "temp", "fixup!", "asdf", merge conflict markers, or >5 commits for what reads as one logical change.
- **Pass when**: commits tell a coherent story, or there are 1–2 commits regardless of phrasing.
- **Say**: suggest squashing or tidying, link to `GIT_CHEATSHEET_URL`.

#### CI passing
- **Fail when**: `ci_failing` is true.
- **Say**: note CI is failing, link to `PRECOMMIT_GUIDE_URL`.

#### Test cases
- **Fail when**: the PR touches substantive logic (>10 meaningful lines in non-trivial files) and `test_files` is empty.
- **Pass when**: the PR is a pure refactor/rename, a trivial change, or the contributor describes tests in the body.

#### Proof of testing
- **Fail when**: `has_visual_evidence` is false AND the PR body contains no description of how the change was tested.
- **Pass when**: body includes a test description, screenshot, or video.
- **Say**: ask for a brief description of what was tested, or a screenshot/video — link to `GITHUB_ATTACH_GUIDE_URL` for how to attach files to a GitHub comment.

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

Edit the quality concern guidance section in this README. Claude reads it directly — no code changes required. Key levers:

- To make Claude **less aggressive** about a concern: add "When in doubt, do not flag" or raise the threshold description.
- To make Claude **more specific**: add examples of what should or shouldn't be flagged.
- To **add a new concern type**: add a new signal to `new_pr_bot.py` (see "Adding a new hard-coded signal" below), then add a guidance section here.

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
| `GITHUB_ATTACH_GUIDE_URL` | https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/attaching-files | Linked when asking for proof of testing |

---

## Known limitations and future ideas

- **Copilot reviewer handle**: The bot slug `copilot-pull-request-reviewer[bot]` may change if GitHub renames the app. Test with `gh api repos/internetarchive/openlibrary/pulls/{PR}/requested_reviewers --method POST --field 'reviewers[]=copilot-pull-request-reviewer[bot]'` if assignments stop working. Note: `gh pr edit --add-reviewer copilot` does NOT work — it cannot resolve this bot login.
- **First-timer detection races**: If a contributor opens two PRs within the same hour, both will be treated as first-timer PRs. Acceptable edge case.
- **LLM hallucinations**: Claude might occasionally flag a concern that isn't really there. The system prompt instructs it to err toward silence, but if you notice consistent false positives on a particular type of PR, add a clarifying example to the prompt's guidelines.
- **Response to bot comments**: Contributors may reply to the bot comment with questions. Those replies will trigger `has_any_comment` on future runs and prevent duplicate comments — but nobody is automatically notified of replies. Consider adding a Slack notification for replies (similar to `issue_comment_bot.py`).
- **Multi-language PRs**: The system prompt is English-only. If a contributor writes their PR body in another language, Claude will still respond in English. This is probably fine for now.
- **Priority label format**: The priority lookup assumes labels formatted as `Priority: 0`, `Priority: 1`, etc. If the label schema changes, update `get_issue_priority()`.
