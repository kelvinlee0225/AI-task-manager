---
name: plan-cycle
description: Propose which Backlog tickets go into the next work cycle, respecting dependencies, priorities and capacity, then open the cycle and move the approved tickets to To Do on the local board. Use when asked to plan the next cycle, sprint or week, or decide what to work on next.
---

# Plan the next cycle

Board conventions are in `.claude/rules/project-board.md`. `B` means `.claude/board/board.py`, run from the repo root.

## 1. Inputs
- `B cycle show` and `B cycle ls`: is a cycle open, and how many points reached Done in past cycles (velocity).
- Ask only for what's missing:
  - Cycle length (default 14 days) and capacity in points (S = 1, M = 2, L = 3). If the user doesn't know, suggest the last closed cycle's done points, or 8 points per week for one developer.
  - Goals for this cycle (a feature to finish, a deadline, bugs first).
- If a cycle is still open, ask whether to close it and start a new one, or to add tickets to the current one.

## 2. Read the board
`B ls` gives every list. Backlog cards are candidates; To Do, In Progress and Testing are carryover and count against capacity first. A `*` after a dependency means it isn't Done.

## 3. Select
- A ticket is **not ready** if it shows `no size` or `AC 0/0`. List those separately; don't select them.
- A ticket can be selected only if each dependency is Done, already in To Do / In Progress / Testing, or selected earlier in this plan.
- Order: the user's stated goals first, then P1, then P2, then P3. Within the same priority, prefer tickets that unblock others and finish features already started.
- Stop when the next ticket would exceed capacity. Leave about 15% slack for bugs.

## 4. Show the proposal and wait
- Selected tickets in execution order: ID · Title · Size · Priority · Depends on. Total points vs capacity.
- Carryover included.
- Left out, with the reason (capacity, dependency, not ready).
- Risks (large tickets, open spikes, external dependencies).

**Do not move anything until the user approves.** Apply their changes.

## 5. Apply
1. If starting a new cycle: `B cycle close` (if one is open), then `B cycle new --days <N> --capacity <P> --goal "<goal>"`. Carryover cards join the new cycle automatically.
2. Move each approved ticket in execution order: `B move <ID> "To Do"`. Each lands at the bottom, so the list reads in execution order.

## 6. Report
The cycle line from `B cycle show`, the ticket list, total points, and which ticket to start first.
