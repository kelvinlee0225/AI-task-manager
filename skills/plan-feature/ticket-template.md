# Ticket template and creation procedure

Used by `plan-feature` and `new-ticket`. Board conventions are in `.claude/rules/project-board.md`.

## Title
Store only the goal; the CLI shows it as `<ID> [<Module>]: <goal>`.
- Start with an imperative verb: Add, Create, Allow, Show, Validate, Fix, Remove, Investigate.
- Describe the outcome, not the implementation: "Filter the invoice list by status", not "Add useState for status".
- Keep the full title under about 70 characters.
- Bugs describe what is fixed: "Fix duplicate email error shown as a server error".

## Ticket file format
Tickets are written to a markdown file and created with `.claude/board/board.py import <file>`. A file can hold one ticket or many. Each ticket is:

```markdown
=== ticket
id: INV-012
module: invoices
title: Filter the invoice list by status
type: Feature
area: Frontend
priority: P2
size: S
depends: INV-011
spec: docs/specs/invoices.md

## Goal
As an analyst, I want to filter invoices by status so that I can find pending ones quickly.

## Context
What the developer needs to know that isn't obvious: business rules, why now, related tickets.

## Scope
- In: <what this ticket delivers>
- Out: <what it does not deliver, and which ticket does, if any>

## Acceptance criteria
1. Given <situation>, when <action>, then <observable result>.
2. ...

## Technical notes
- Frontend: feature folder, pages, generated hooks used.
- Roles allowed.
```

Rules of the format:
- The ticket starts at a line beginning with `=== ticket`. Header lines (`key: value`) follow until the first blank line. Allowed keys: `id`, `module` (or `prefix`), `title`, `type`, `area`, `priority`, `size`, `depends`, `spec`, `list`.
- `module`, `title`, `type` and `area` are required. `priority` defaults to P2. `list` defaults to Backlog (only Backlog or To Do are allowed).
- `id` is optional; without it the CLI assigns the next number. When showing a plan to the user, get real IDs first with `next <PREFIX> --count N` and write them in, so the IDs the user approves are the IDs created.
- `depends` takes comma-separated IDs, either existing cards or tickets earlier in the same file.
- Items under `## Acceptance criteria` (numbered or bulleted) become the AC checklist (AC1, AC2…). Everything else in the body becomes the description. Don't write a Definition of done section; the CLI adds it.
- Text before the first `=== ticket` line is ignored, so the file can start with notes or a summary table.

## Bug variant
Replace Goal/Scope with:

```markdown
## Problem
One sentence: what is wrong and who is affected.

## Steps to reproduce
1. ...

## Expected
## Actual
## Environment
Where it was seen (local, staging, production), browser, user role, date.
```
Keep Acceptance criteria (the first one states the fixed behavior; add one for "a test covers this case") and Technical notes.

## Writing good acceptance criteria
- 3 to 7 per ticket. More means the ticket should be split (the CLI warns above 7).
- Each one is observable and testable by someone who didn't write the code. "Works correctly" or "is fast" are not criteria.
- Backend tickets state API behavior: status codes, response fields, validation errors, permissions (e.g. "a user without the `analyst` role gets 403").
- Frontend tickets state what the user sees and does: states (loading, empty, error), messages, navigation, what persists after reload.
- Include the unhappy paths that matter: invalid input, not found, no permission.

## Sizing
- **S**: under half a day, a few files. **M**: about a day. **L**: two to three days.
- Anything bigger than L is split. Split by behavior (create + list first, then edit/delete, then the business rule), not by layer inside the same app.
- Unknowns that block estimating become a **Spike** ticket (time-boxed, output is a written finding in `docs/decisions/`), and the real ticket depends on it.

## Splitting a feature
- Backend and frontend are separate tickets. The frontend ticket depends on the backend ticket that provides its endpoints.
- One ticket = one PR.
- Order in the file: setup/chores → backend → frontend.

## Creating the cards
The ticket text is only input. After the import, the board (SQLite) is the source of truth and no ticket file is kept.
- **One ticket:** pipe it in with a quoted heredoc (`import - <<'TICKET' … TICKET`); no file.
- **A feature:** write `.board/drafts/<feature>.tickets.md` so the user can review and edit it, then import it and delete it after the import succeeds.

1. Validate first with `--dry-run`. Fix any error it reports (unknown module, missing field, unknown dependency, ID already taken).
2. After the user approves, import without `--dry-run`. It creates everything in one transaction, or nothing if any ticket is invalid.

## Editing a ticket later
Always start from the board, never from an old file:
1. `.claude/board/board.py show <ID> --ticket` prints the current ticket in this format.
2. Change what's needed and show the user the change (changing acceptance criteria or scope needs their approval).
3. Pipe it back: `.claude/board/board.py import - --update <<'TICKET' … TICKET`.

Only the fields present are updated. Acceptance criteria whose text didn't change keep their checkmark; reworded or new ones come back unchecked. For a single field, `edit <ID> --size L` (or `--priority`, `--title`, `--depends`) is shorter. The user can also edit a card in the board view's "Edit card" form.
