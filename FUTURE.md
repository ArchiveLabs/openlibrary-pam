# Future Workflows

Opportunities that are not yet engineered but are worth tracking.

---

## Per-Lead Executive Digest

**Context**: The repo already has a [daily team digest workflow](https://github.com/internetarchive/openlibrary/pull/12278/changes) that produces a summary of the whole team's activity. The gap is a per-lead view.

**What it would do**: For each lead (identified by `Lead: @username` labels), generate a digest of:
- Open issues assigned to them, sorted by priority
- Issues labeled `Needs: Response` where they are the lead
- PRs waiting on their review
- Recently closed issues / PRs for momentum visibility

**Format**: Could be posted as a GitHub comment on a standing "digest issue", sent to Slack, or written to a file for the lead to pull up.

**Why it matters**: Leads currently have no easy way to see their full queue in one place. A per-lead digest would reduce the "what do I work on next?" overhead and make it easier for leads to stay responsive.

**When to build**: When the team has validated that the team-wide digest is useful and wants a more granular view.

## Observations from the field

- **Issue #12298**: Feature requests that mirror existing functionality (e.g., "add delete button like on books") often hinge on a data integrity question (what happens to linked records) that the template doesn't prompt authors to answer — worth adding a "What should happen to linked records?" field to the feature request template for delete/merge proposals.
- **Issue #12300**: When the issue author is also a listed internal stakeholder/maintainer, the bot's generic community-member tone ("thanks for filing!") feels off — the workflow could detect author presence in the stakeholder list and use a more peer-level framing.
- **PR #12301**: RayBB (experienced contributor) submitted a 1-line script fix without Copilot pre-assigned, so it passed eligibility but warranted no comment — the bot could check if the author is a known team member and skip or apply a lighter touch instead of full community onboarding logic.
- **Issue #12303**: The proposal contained an unfilled template placeholder (`<docs-repo-url>`); the bot could scan issue bodies for `<...>` patterns and flag them as missing required information rather than leaving it to the commenter to notice.
- **Issue #12304**: When the issue author is a listed area lead (here @RayBB filed a bug they own), the `Needs: Help` label was pre-applied even though the fix is < 1 day — the workflow could check whether the author-assigned lead is the same person and flag potential label mismatches rather than silently leaving them.
- **PR #12305**: Renovate bot PRs pass the eligibility filter (no prior comments, Copilot not assigned) and land in the JSON, burning cycles with nothing to comment on — `new_pr_bot.py` should skip authors whose login starts with `app/` or is in a known-bot set (`renovate`, `dependabot`).
- **Issue #12311**: The local `~/Projects/openlibrary` checkout was behind HEAD (PR #12179 had already merged and removed `reduce_seeds`), so `grep` showed `SubjectProcessor` as still-used when it wasn't — the workflow should use `gh api` to fetch current file contents rather than relying on a potentially stale local clone.
- **Issue #12313**: For JS migration issues, the workflow should proactively grep for direct function call sites (e.g. `archive_analytics.ol_send_event_ping`) in addition to HTML attribute patterns — attribute-based searches alone miss programmatic call sites like those in `Carousel.js` that fall outside the proposed migration scope.
- **Issue #12314**: When a recently merged PR is the direct root cause of a new bug report, the workflow could proactively link the causal PR in the triage comment — `git log --oneline -- <affected file>` reliably surfaces it when the commit message names the PR.
- **PR #12316**: For PRs whose primary purpose is fixing or reorganizing tests, "proof of testing" is satisfied by CI passing — the checklist marks it `[ ]` regardless, which would be a false positive if surfaced. The bot could detect test-infrastructure PRs (files changed are exclusively test/CI files) and auto-pass the proof-of-testing criterion.
- **PR #12319**: When the PR description explicitly states changes "only affect local development environments," the proof-of-testing threshold should be lower — a brief local-run note suffices, but the bot currently treats it identically to a production change.
- **Issue #12323**: Issue body stated the dev carousel code was "already in the file locally," but the tracked `home/index.html` had no such code — the contributor would be adding it fresh. The workflow's codebase research surfaced this; a future improvement could flag issue descriptions that claim existing repo state ("it's already there") but don't match the actual file contents.
- **Issue #12322**: When sibling issues explicitly warn "do not work on both simultaneously" due to file overlap, the bot has no way to surface this warning in a sibling's triage — cross-linking paired issues with a mutual coordination note would help contributors scanning issues individually avoid conflicts.
- **PR #12325**: For infrastructure/configuration PRs (Docker compose, shell scripts), the "Proof of testing" criterion is ambiguous — contributors often write the Testing section as reviewer instructions ("Run `docker compose up` and verify…") rather than past-tense evidence ("I ran… and observed…"). The bot could detect future-tense phrasing and treat it differently from actual evidence, or simply lower the bar for pure config PRs where visual evidence isn't practical.
- **PR #12326**: The PR body had a `### Screenshot` section with nothing beneath it and a testing checklist with all items unchecked — both are clearer proof-of-testing failure signals than the current `has_visual_evidence` regex alone. The bot could detect (a) unfilled named template sections and (b) a testing checklist where every item is `- [ ]` with none checked, and treat either as a stronger "no proof of testing" signal.
- **Issue #12327**: `Good First Issue` and `Needs: Help` were both applied to the same issue — these labels are semantically contradictory (the latter is described as "typically substantial, needs a dedicated developer"). The workflow could flag co-occurrence of these two labels as a likely mislabeling.
- **PR #12328**: Security-sensitive fixes (e.g., passwords exposed in the DOM) are prioritized identically to other PRs of the same issue priority — the bot has no signal for security severity. A `security` label or keywords like "XSS", "password", "credential", "injection" in the title/body could trigger a higher-urgency note to the reviewer.
- **Issue #12332**: An issue labeled `Patch Deployed` + still OPEN is a strong signal to prompt closure — the workflow could detect this combination and add a closing checklist rather than a standard triage comment. Also, when the issue author is the bot persona (@mekarpeles), the "Thank you @{author}" opener should be suppressed; currently the step has no guard for self-authored issues.
- **Issue #12329**: When a security fix spans both external links and same-origin internal links, the uniform recommendation ("add rel attribute") diverges from the better fix for internal links ("remove target=_blank entirely") — the workflow could check link destinations during codebase research and split the recommended action accordingly.
- **Issue #12334**: When a `Type: Subtask of Epic` issue's parent carries `Patch Deployed` + still OPEN, the subtask is incomplete follow-up on an already-live deploy — the workflow could detect this via the parent's labels and flag it as higher urgency rather than standard triage.
- **Issue #12333**: Subtasks targeting the olsystem repo (or other sibling IA repos) require `gh api repos/internetarchive/olsystem/contents/{path}` to read the current file state; the local openlibrary checkout is irrelevant, so the codebase research step should explicitly attempt API reads on referenced external repos when the issue body names a file path in one.
- **PR #12336**: Staff-authored PRs appear in the script's JSON output even though the agent skips them — `new_pr_bot.py` should filter the staff list before outputting, avoiding wasted API calls (Copilot assignment, signal gathering) on PRs that will never get a comment.
- **PR #12337**: When commit messages reference a topic (e.g. "google signup screen") that doesn't match the PR title/body topic, the PR likely contains mixed unrelated changes — the script could pre-compute a `has_off_topic_commits` signal by comparing commit message keywords against PR title keywords, surfacing this to the agent without requiring it to reason across both fields independently.
