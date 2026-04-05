Run the Open Library PAM (Project AI Manager).

Parse $ARGUMENTS for `--workflow <name>` (default: `pr`), `--dry-run`, and `--hours <N>` (default: 1).

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

*Coming soon.*

---

## Workflow: exec-summary

*Coming soon.*

---

## Workflow: needs-response

*Coming soon.*
