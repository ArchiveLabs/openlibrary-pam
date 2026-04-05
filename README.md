# openlibrary-pam

**Project AI Manager for [Open Library](https://openlibrary.org)**

A Claude Code skill (`/ol-pam`) that automates recurring project management tasks for the [internetarchive/openlibrary](https://github.com/internetarchive/openlibrary) repository.

## Usage

```
/ol-pam                          # run default workflow (pr)
/ol-pam --workflow pr            # triage new community PRs
/ol-pam --workflow pr --dry-run  # preview without posting
/ol-pam --workflow pr --hours 24 # look back 24 hours
```

## Workflows

| Workflow | Status | Description |
|---|---|---|
| `pr` | ✅ Active | Triages new community PRs: assigns Copilot, posts a warm mentor-like first-touch comment based on signals gathered from the PR |
| `issue-refinement` | 🔜 Planned | Helps refine and label newly opened issues |
| `exec-summary` | 🔜 Planned | Produces a per-project-lead executive summary of open work |
| `needs-response` | 🔜 Planned | Reviews issues/PRs labeled `Needs: Response` and surfaces ones that need attention |

## Setup

```bash
git clone https://github.com/ArchiveLabs/openlibrary-pam
cd openlibrary-pam
gh auth login   # if not already authenticated
```

The `/ol-pam` command is available automatically in any Claude Code session opened in this repo.

## How the PR workflow works

See [`scripts/gh_scripts/PR_PREREVIEW_README.md`](scripts/gh_scripts/PR_PREREVIEW_README.md) for the full design rationale, trigger logic, signal reference, and tuning guide.

Briefly: the script (`scripts/gh_scripts/new_pr_bot.py`) gathers signals for each eligible PR via the `gh` CLI and outputs JSON. Claude reads that JSON alongside the README and decides what comment to post.
