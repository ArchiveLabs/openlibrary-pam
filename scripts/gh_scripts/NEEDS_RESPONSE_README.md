# Needs: Response Workflow

When a contributor comments on a GitHub issue and the assigned Lead hasn't replied yet, a GitHub workflow automatically adds the `Needs: Response` label. This workflow reviews those issues and decides what to do — whether to draft a response, remove the label, or do nothing.

**Default: do nothing.** When in doubt, leave the label and let the lead handle it. The purpose of this workflow is to reduce noise for leads, not to create new noise for contributors.

---

## Input

`ISSUE_NUMBER` — the issue number to process. Optionally: `--dry-run` to print actions without taking them.

---

## Step 1: Fetch issue data

```bash
gh issue view {ISSUE_NUMBER} --repo internetarchive/openlibrary --json number,title,body,labels,assignees,comments
gh api repos/internetarchive/openlibrary/issues/{ISSUE_NUMBER}/comments
```

---

## Step 2: Confirm label is present

If `Needs: Response` is not in the issue's labels, stop — nothing to do.

---

## Step 3: Identify the lead

Look for a label starting with `Lead: ` (e.g. `Lead: @mekarpeles`). The lead is the person who should have replied. If no lead label exists, the issue is unassigned — default to doing nothing.

---

## Step 4: Identify the triggering comment

Find the most recent comment from a non-lead, non-bot account. Read it carefully.

---

## Step 5: Categorize and act

### Case A — Trivial comment / no reply needed
**Signals**: emoji reaction, "thanks!", "sounds good", "will do", simple one-line acknowledgment that doesn't pose a question or request anything.

**Action**: Remove the `Needs: Response` label. Post nothing.
```bash
gh issue edit {ISSUE_NUMBER} --repo internetarchive/openlibrary --remove-label "Needs: Response"
```

---

### Case B — Issue is labeled `Needs: Staff / Admin` and contributor is asking to help
**Signals**: Contributor says "I'd like to work on this", "can I be assigned?", "I want to help" — but the issue has `Needs: Staff / Admin`.

**Action**: Post a warm explanation, then remove `Needs: Response`.

```
Thanks for your interest! This issue is marked `Needs: Staff / Admin`, which means it requires access to internal systems or tools that aren't available to outside contributors. We'll take care of it from the staff side.

If you're looking for something to contribute, our [good first issues](https://github.com/internetarchive/openlibrary/issues?q=label%3A%22Good+First+Issue%22+is%3Aopen) are a great place to start!
```

Then:
```bash
gh issue edit {ISSUE_NUMBER} --repo internetarchive/openlibrary --remove-label "Needs: Response"
```

---

### Case C — Contributor asking to be assigned
**Signals**: "Can I work on this?", "I'd like to take this", "assigning myself", "I want to try this".

Before responding, assess the issue's readiness:

**C1 — Issue is not ready** (vague, missing acceptance criteria, labeled `Needs: Triage`, or clearly requires staff decisions first):
Post:
```
Thanks for your interest! This issue still needs some scoping before it's ready to be picked up — we want to make sure whoever takes it has everything they need to succeed. We'll update the issue once it's triaged.

In the meantime, our [good first issues](https://github.com/internetarchive/openlibrary/issues?q=label%3A%22Good+First+Issue%22+is%3Aopen) might have something ready to go!
```
Then remove `Needs: Response`.

**C2 — Issue is ready but contributor hasn't described their approach:**
Post:
```
Thanks for your interest in this! Before we assign it, could you share a brief outline of how you're thinking about approaching it? This helps us make sure you have all the context you need and that your plan aligns with how we're thinking about the solution.
```
Leave `Needs: Response` in place (we're now waiting on them).

**C3 — Issue is ready and contributor has described their approach:**
Review their plan against your understanding of the issue. If it looks correct, post any clarifying questions that would confirm their understanding or surface gotchas. If their plan looks sound, draft a message the lead can send to assign them (dry-run only — don't assign directly).

---

### Case D — Contributor asking a question
**Signals**: asking about implementation details, status, approach, or context.

Check whether the answer is:
- **Findable in the issue** → Point them to the relevant section/comment. Remove `Needs: Response`.
- **Requires lead judgment** → Do nothing. Leave the label.
- **A common question with a known answer** → Draft a helpful response for the lead to review (dry-run) or post directly if the answer is unambiguous.

---

### Case E — Uncertain
**Default**: Do nothing. Leave `Needs: Response` in place. Log what you observed but took no action.

---

## Step 6: Log what you did

Always print a brief summary of what you observed and what action (if any) you took. In dry-run mode, print what you *would* have done instead.

---

## Stakeholders reference

- @mekarpeles — program lead
- @cdrini — solr, search, ILE
- @jimchamp — librarian merge, subjects, bookshelf
- @hornc — metadata, MARC, imports
- @scottbarnes — ops, sentry
- @seabelis — patron services
- @RayBB — dev experience, fastapi
- @lokesh — frontend, design
