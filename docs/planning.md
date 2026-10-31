# Planning and delivery rules

tower-bot uses the contracts level of a lightweight planning process: no roadmap or PRD files, just a few rules for issues and pull requests so it stays clear what is done and what is only merged.

## Where things live

| What | Where |
|---|---|
| Execution: what to do, what is left | the GitHub issue |
| What changed, and its evidence | the pull request, filled from `.github/pull_request_template.md` |
| How to run and deploy the bot | `README.md` and `deploy/` |

Nothing in a repo file, issue or PR cites a path outside this repository, such as a local report or another machine's files; copy the substance instead. This repo is public: no hosts, addresses, tokens or private links anywhere.

## Four claims, kept apart

1. **Implemented**: the code exists and its tests pass at a commit.
2. **Merged**: the change is on `main`.
3. **Verified**: someone checked it on the running bot.
4. **Delivered**: it is in use and nothing is left to do.

Never report "live" or "done" from a merge alone.

## Closing issues

- Use `Closes #<n>` (or a Development-sidebar link, which also closes on merge) only when merging finishes the issue.
- Otherwise use `Refs #<n>`. Keep the issue open with the `open-item` label and a comment saying what is left and how to check it. Close it once the check passes.

## Filing an issue

- **Outcome**: what should be true when it is done.
- **Planning link**: a related issue, or "none - bounded maintenance".
- **Scope**: what is in, and what is not.
- **Verification target**: where it is checked (tests, the running bot).
- **Operational impact**: deploy, config, data or permission changes, or none.

## Handing off unfinished work

Leave a comment on the issue: branch and commit, facts established, commands run, next action. No handoff files.
