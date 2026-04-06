# openlibrary-pam

**Pam — Project AI Manager for [Open Library](https://openlibrary.org)**

Pam is a continuous AI-assisted project management layer for [internetarchive/openlibrary](https://github.com/internetarchive/openlibrary). It addresses four recurring challenges that slow down contributors and drain staff bandwidth.

---

## The four problems Pam addresses

### 1. Issues aren't ready to be worked on

Staff often create issue stubs — no acceptance criteria, no approach, no context. Contributors pick them up prematurely, get stuck, and block waiting on staff who don't have bandwidth for breakdown. Meanwhile, good issues that could be first contributions aren't labeled as such, and issues that require staff/admin access don't clearly communicate that.

**Pam's response**: When a new issue is opened, Pam runs the [Issue Refinement workflow](scripts/gh_scripts/ISSUE_REFINEMENT_README.md). It analyzes the issue, applies labels (`Good First Issue`, `Needs: Staff / Admin`, `Needs: Triage`), suggests breakdowns of complex issues, and posts a warm first-touch comment that sets expectations and asks for what's missing.

### 2. PRs go too long without a response

Contributors submit PRs and hear nothing. No acknowledgment, no feedback on whether CI is passing, no guidance on what the project needs (linked issue, clean commit history, screenshots, test evidence). This erodes contributor trust and leads to follow-up pings or abandoned PRs.

**Pam's response**: When a new community PR is opened, Pam runs the [PR Pre-Review workflow](scripts/gh_scripts/PR_PREREVIEW_README.md). It assigns Copilot as a reviewer (for early AI feedback), and posts a warm, specific comment covering: first-timer welcome, missing issue reference, messy commit history, missing testing evidence, missing screenshots (for UI PRs), and CI failures — but only flags things that are genuinely actionable, and never in isolation.

### 3. `Needs: Response` issues pile up without leads noticing

A GitHub workflow labels issues `Needs: Response` when a contributor comments and the lead hasn't replied. But leads don't always notice, and not every comment actually requires a response. The result: contributors feel ignored, and leads feel overwhelmed when they finally check.

**Pam's response**: The [Needs: Response workflow](scripts/gh_scripts/NEEDS_RESPONSE_README.md) reviews these issues and acts: removes the label for trivial comments, handles `Needs: Staff / Admin` politely, prompts contributors asking to be assigned for their plan, and drafts responses to questions where the answer is clear. When in doubt, it does nothing.

### 4. Leads have no easy view of their personal queue *(planned)*

See [FUTURE.md](FUTURE.md).

---

## How Pam runs

`pam.py` is a long-lived polling service. It checks GitHub every 60 seconds. When a new PR or issue is found, it invokes Claude to run the appropriate workflow. No activity = no Claude invocation.

```bash
# Start in tmux (recommended)
tmux new-session -d -s pam 'python3 /path/to/openlibrary-pam/pam.py | tee -a /tmp/ol-pam.log'
tmux attach -t pam

# Or with nohup
nohup python3 pam.py >> /tmp/ol-pam.log 2>&1 &
```

State is persisted to `.pam_state.json` so Pam resumes correctly after restarts.

---

## Manual / interactive use

The `/ol-pam` Claude Code skill lets you run any workflow interactively:

```
/ol-pam                                          # run PR workflow (default)
/ol-pam --workflow pr --dry-run --hours 24       # preview PR comments for last 24h
/ol-pam --workflow issue-refinement --issue 123  # refine a specific issue
/ol-pam --workflow needs-response --issue 123    # handle a Needs: Response issue
/ol-pam --workflow needs-response --issue 123 --dry-run
```

---

## Workflows

| Workflow | Status | Entry point |
|---|---|---|
| PR pre-review | ✅ Active | `scripts/gh_scripts/PR_PREREVIEW_README.md` |
| Issue refinement | ✅ Active | `scripts/gh_scripts/ISSUE_REFINEMENT_README.md` |
| Needs: Response | ✅ Prototype (manual) | `scripts/gh_scripts/NEEDS_RESPONSE_README.md` |
| Per-lead digest | 🔜 Planned | `FUTURE.md` |

---

## Setup

```bash
git clone https://github.com/ArchiveLabs/openlibrary-pam
cd openlibrary-pam
gh auth login   # if not already authenticated
```

The `/ol-pam` skill is available automatically in any Claude Code session opened in this repo.

---

## Architecture

Each workflow follows the same two-part pattern:

1. **Data gatherer** (Python + `gh` CLI) — fetches the raw GitHub state and emits structured JSON or a summary. Cheap, fast, no AI.
2. **Claude Code agent** — reads the JSON alongside the workflow's README guide and decides what to do. Handles nuance, tone, and judgment.

This split keeps the code simple, the AI prompts focused, and the whole system easy to test by replaying the JSON output.
