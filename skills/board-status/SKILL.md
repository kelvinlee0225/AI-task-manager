---
name: board-status
description: Report the state of the local project board — what's in each list, what's blocked, stale or over the WIP limit, cycle progress, and which Testing tickets are ready for Done. Read-only. Use when asked for the board status, standup summary, progress, or what's pending.
---

# Board status

Board conventions are in `.claude/rules/project-board.md`. This skill only reads; it never moves or edits cards unless the user asks afterwards.

## 1. Get the report
```bash
.claude/board/board.py status --prs
```
It already computes: cycle progress, count and points per list, In Progress and Testing cards with age and AC progress, problems (WIP, stale 3+ days, blocked, not ready, over capacity, unverified criteria) and Testing cards whose PR is merged. Add `.claude/board/board.py log --limit 15` only if the user asks what happened recently.

## 2. Report
Relay it short, in this order:
1. One line for the cycle and one per list (count and points).
2. In Progress and Testing cards: ID, title, age, AC progress.
3. Problems, most important first, each with a suggested action (e.g. "INV-014 is blocked by INV-013 in Backlog: plan INV-013 first").
4. Cards ready for Done, telling the user to drag them to Done in the board view or run `.claude/board/board.py move <ID> Done --user`.

Don't paste the raw output when a shorter summary says the same.
