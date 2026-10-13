---
name: start-ticket
description: Start work on a ticket from the local board — check it's ready, move it to In Progress, create the branch, then implement it with the right backend or frontend skill within the ticket's scope. Use when asked to start, pick up or work on a ticket (by ID like INV-012), or to take the next ticket.
---

# Start a ticket

Board conventions are in `.claude/rules/project-board.md`. `B` means `.claude/board/board.py`, run from the repo root.

## 1. Find the ticket
- By ID: `B show <ID>`.
- "Next ticket": `B ls --list "To Do"` and take the first card with no `*` in its deps. Then `B show <ID>`.

## 2. Check it's ready
Stop and tell the user if any of these fail:
- The card is in **To Do**. If it's in Backlog, ask whether to start it anyway (it wasn't planned into this cycle).
- Every "Depends on" ticket is Done. If one is in Testing, ask; the work may build on unmerged code.
- The ticket has acceptance criteria and a clear scope. If not, propose the missing parts rather than guessing.
- The CLI enforces the WIP limit of 2 on the move. If it refuses, ask the user; only with their agreement retry with `--allow-wip`.

## 3. Prepare the branch
```bash
git status --porcelain          # must be clean; if not, stop and ask
git switch <base> && git pull
git switch -c <type>/<ID>-<short-slug>
```
Type from the card: Feature → `feat`, Bug → `fix`, Chore → `chore`, Spike → `spike`.

## 4. Update the board
```bash
B move <ID> "In Progress" --top
B edit <ID> --branch <branch>
B comment <ID> "Started. Branch: <branch>"
```

## 5. Plan
Restate the acceptance criteria as a short implementation plan: files or modules to touch and how each criterion will be met and tested. If something in the ticket is ambiguous or contradicts the code, ask now, and record the answer with `B comment <ID> "..."`.

## 6. Implement
Use the matching skill:
| Ticket | Skill |
|---|---|
| Backend, new module | `backend-new-module` |
| Backend, change to an existing module | `backend-edit-module` |
| Frontend, new feature or screens | `frontend-new-feature` |
| Frontend, change to an existing feature | `frontend-edit-feature` |
| Frontend ticket after backend API changes | `sync-api-types` first |
| Spike | No production code. Investigate and write the finding to `docs/decisions/<ID>-<slug>.md` |

- Stay inside the ticket's **Scope**. Anything out of scope becomes a note for the report or a new ticket suggestion, not extra code.
- Write tests that map to the acceptance criteria where possible.
- Commit in small steps with the commit convention: `feat(<module>): <summary> (<ID>)`.

## 7. If blocked
`B comment <ID> "Blocked: <what> — <question>"`, leave the card in In Progress, and tell the user.

## 8. Finish
When every acceptance criterion is implemented and the checks pass, continue with the `ticket-to-testing` skill (or ask the user if they prefer to review first).
