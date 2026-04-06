Run the Open Library PAM (Project AI Manager).

Parse $ARGUMENTS for `--workflow <name>` (default: `pr`), `--dry-run`, `--hours <N>` (default: 1), and `--issue <N>`.

---

## Workflow: pr (default)

Triage new community pull requests on internetarchive/openlibrary.

**Step 1 — gather**

Run from the repo root, passing through any --dry-run and --hours flags found in $ARGUMENTS:

```
python3 scripts/gh_scripts/new_pr_bot.py [--dry-run] [--hours N]
```

**Step 2 — analyze and respond**

Read `scripts/gh_scripts/PR_PREREVIEW_README.md` for the full guide.

For each PR object in the JSON output:
- If `dry_run` is true: print the comment you would post, do not post it.
- Otherwise: post the comment with `gh pr comment {number} --repo internetarchive/openlibrary --body "..."`

Always end every posted comment with `<!-- ol-pr-bot -->`.

---

## Workflow: issue-refinement

Analyze and enrich a newly opened issue.

Requires `--issue <N>` in $ARGUMENTS.

Read `scripts/gh_scripts/ISSUE_REFINEMENT_README.md` for the full guide.

Follow the steps in that README for the given issue number. Apply labels where appropriate, then post a comment on the issue.

- If `dry_run` is true: print the comment and label changes you would make, do not post or apply them.
- Otherwise: apply labels with `gh issue edit {number} --repo internetarchive/openlibrary --add-label "..."` and post with `gh issue comment {number} --repo internetarchive/openlibrary --body "..."`

Always end every posted comment with `<!-- ol-issue-bot -->`.

---

## Workflow: needs-response

Review an issue labeled `Needs: Response` and take the appropriate action.

Requires `--issue <N>` in $ARGUMENTS.

Read `scripts/gh_scripts/NEEDS_RESPONSE_README.md` for the full guide.

Follow the steps in that README for the given issue number.

- If `dry_run` is true: print what you would do (post, remove label, or nothing) without taking any action.
- Otherwise: act as described in the README.

When in doubt, do nothing.

---

## Workflow: exec-summary

*Coming soon — see FUTURE.md.*
