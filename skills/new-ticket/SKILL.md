---
name: new-ticket
description: Create a single ticket on the local board — a bug report, chore, spike or small feature — with the standard title, fields and acceptance criteria, in the Backlog. Use when asked to log a bug, create a ticket or card, or add one task to the board.
---

# Create one ticket

Read `.claude/skills/plan-feature/ticket-template.md` before writing the ticket. Board conventions are in `.claude/rules/project-board.md`. `B` means `.claude/board/board.py`, run from the repo root.

## 1. Gather what's needed
Ask only for what's missing:
- Type (Feature, Bug, Chore, Spike), module (`B module ls`) and area (Backend, Frontend or Infra).
- For a bug: steps to reproduce, expected vs actual behavior, where it happened (local, staging, production) and the user role. If you can reproduce or locate it in the code, add what you found to Technical notes.
- Priority. Default P2; a bug that blocks users in production is P1.

If the request is really several tickets or a whole feature, use `plan-feature` instead.

## 2. Write it and confirm
No file is saved for a single ticket; it's piped straight into the CLI.
- Get the ID with `B next <PREFIX>` and write the ticket in the ticket file format (Bug variant for bugs).
- Validate it by piping it in with a quoted heredoc, so backticks and `$` in the text are left alone:
  ```bash
  .claude/board/board.py import - --dry-run <<'TICKET'
  === ticket
  id: ...
  ...
  TICKET
  ```
- Show the ticket to the user and wait for approval.

## 3. Create it
Run the same heredoc without `--dry-run`, with the user's edits applied. The card goes in **Backlog**, unless the user explicitly asks for To Do (e.g. an urgent P1 bug); then `B move <ID> "To Do" --top`.

## 4. Report
The ID, full title (`B title <ID>`) and list.
