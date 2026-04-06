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
