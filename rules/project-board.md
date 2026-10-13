# Project board (local)

Work is planned and tracked on a local kanban board: SQLite at `.board/board.db`, driven by the CLI `.claude/board/board.py`. Run it from the repo root. Tickets are written in English. Never read or write the database any other way.

- Base branch: `refactor`   <!-- during the PostgreSQL refactor; back to `main` once `refactor` is merged -->
- The user watches and drags cards in the web view (`.claude/board/board.py serve`, http://127.0.0.1:8765).

## Lists
| List | Meaning | Moved there by |
|---|---|---|
| Backlog | Planned, not committed to a cycle | `plan-feature` / `new-ticket` create cards here |
| To Do | Committed for the current cycle and ready to start | `plan-cycle`, after the user approves |
| In Progress | Branch exists, work started | `start-ticket` |
| Testing | PR open, checks passed, acceptance criteria being verified | `ticket-to-testing` |
| Done | PR merged and acceptance criteria verified | **The user only** |

- Ready for To Do: acceptance criteria and a size, and dependencies Done or planned before it. The CLI refuses Backlog → To Do otherwise.
- At most **2** cards in In Progress. The CLI refuses a third; pass `--allow-wip` only after the user agrees.

## Card fields
Every card has one type (`Feature`, `Bug`, `Chore`, `Spike`), one area (`Backend`, `Frontend`, `Infra`), one priority (`P1`–`P3`, default P2) and, once ready, a size (`S`=1, `M`=2, `L`=3 points). Each card also has an "Acceptance criteria" checklist (AC1…) and a "Definition of done" checklist (DOD1 Checks pass · DOD2 Reviewer agent run · DOD3 Acceptance criteria verified · DOD4 PR merged), added automatically.

## Modules and ticket IDs
Each module has a prefix; IDs are `<PREFIX>-<3 digits>` (`INV-012`), assigned by the CLI and never reused. `module ls` shows modules, prefixes and the next free ID. A new module gets a prefix (3–5 letters) proposed during planning and is added with `module add <key> "<Title>" <PREFIX>` after the user approves.

## CLI reference
```
ls [--list "To Do"] [--module invoices] [--done]   one line per card, grouped by list (Done hidden by default)
show ID [--brief | --ticket]                       detail; --ticket prints the editable ticket format
next PREFIX [--count N]                            next free IDs
import FILE [--dry-run] [--update]                 create cards from ticket-format markdown (--update edits existing ones)
move ID LIST [--top | --before ID2] [--allow-wip] [--force]
edit ID [--title …] [--size M] [--priority P1] [--depends A,B] [--branch …] [--pr N] [--desc-file F]
check ID AC1 AC3 DOD1 [--note "verified by …"]     uncheck ID AC1
comment ID "text"                                  ("-" reads stdin)
title ID                                           full title, for PRs
status [--prs] · cycle [show|new|close|ls] · module [ls|add] · log [ID]
```
`ls` line: `ID · Type/Area · Priority · Size · AC done/total · deps A,B* | title`, where `*` marks a dependency that isn't Done. The ticket format, and how to create and edit tickets, is in `.claude/skills/plan-feature/ticket-template.md`. The board is the only source of truth: ticket text is never kept in files after import, and edits always start from `show <ID> --ticket`.

## Conventions linked to tickets
- Title: `<ID> [<Module>]: <Short action-oriented goal>` (the CLI builds it; store only the goal). `title ID` prints it.
- Branch: `<type>/<ID>-<short-slug>` with type `feat`, `fix`, `chore` or `spike`, e.g. `feat/INV-012-filter-by-status`.
- Commits: Conventional Commits with the ID at the end: `feat(invoices): filter invoice list by status (INV-012)`.
- PR title: the card title. PR body starts with `Ticket: <ID>`.

## What Claude may and may not do on the board
- May: create cards in Backlog; move cards To Do → In Progress → Testing through the skills; comment; check off criteria it verified.
- Must ask first: Backlog → To Do, changing a ticket's acceptance criteria or scope, starting a ticket that isn't in To Do, exceeding the WIP limit, `--force`.
- Never: move cards to Done, archive cards, merge PRs, pass `--user`, or touch `.board/` files directly. A hook blocks these.
