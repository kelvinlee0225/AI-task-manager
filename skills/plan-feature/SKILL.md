---
name: plan-feature
description: Turn a feature request or spec into well-formed tickets on the local board — split into backend and frontend tickets with IDs, fields, acceptance criteria and dependencies — and create them in the Backlog after approval. Use when asked to plan a feature, break work into tickets, or create tickets for a spec.
---

# Plan a feature into tickets

Read `ticket-template.md` in this folder before writing any ticket. Board conventions and the CLI reference are in `.claude/rules/project-board.md`. `B` below means `.claude/board/board.py`, run from the repo root.

## 1. Get the requirements
- If a spec exists in `docs/specs/`, read it.
- - If not, and the feature is more than one or two tickets, write `docs/specs/<feature>.md` first from `spec-template.md` in this folder: Problem, Users and roles, Scope (in/out), Business rules, Open questions. Show it to the user and wait for approval. Open questions must be answered or turned into Spike tickets.
- For a small request, the user's description is enough.

## 2. Understand what exists
- Backend modules in `backend/src/modules/` and frontend features in `frontend/src/features/` that this touches.
- `B module ls` for prefixes. If the module is new, propose a key, title and prefix (3–5 letters).
- `B ls --module <key> --done` for existing tickets in the module, to avoid duplicates.

## 3. Break it down
Follow "Sizing" and "Splitting a feature" in the template:
- Chores first (setup the feature needs), then backend tickets, then frontend tickets.
- Each frontend ticket lists the backend ticket(s) it depends on.
- Every ticket is S, M or L. Split anything bigger.
- Every ticket has 3–7 testable acceptance criteria, including the relevant permission and error cases.
- Priority: use what the user said; otherwise P2, and say so.

## 4. Write the tickets file
- Get IDs: `B next <PREFIX> --count <N>` (for a new module, they start at 001).
- Write all tickets to `.board/drafts/<feature>.tickets.md`, in dependency order, in the ticket file format. This file is temporary: it exists so the user can review and edit the plan, and it's deleted once the tickets are on the board.
- If the module exists, validate: `B import <file> --dry-run`. Fix what it reports. (A new module can't be validated until it's added in step 6.)

## 5. Show the plan and wait
Present:
1. A table: ID · Title · Type · Area · Priority · Size · Depends on. Total points.
2. Each ticket's acceptance criteria, condensed.
3. Any new module and prefix.
4. Assumptions and open questions.
5. The path of the tickets file, so the user can edit it directly.

**Do not create anything until the user approves.** Apply their edits to the file and show the changed tickets again if the changes are substantial.

## 6. Create the tickets
- New module approved: `B module add <key> "<Title>" <PREFIX>`.
- `B import <file>`. If it fails, nothing was created; fix the file and run it again.
- Once the import succeeds, delete the tickets file. From now on the board is the only copy; edits go through `B show <ID> --ticket` + `B import - --update`, or the user's edit form in the board view. If the import failed and you're stopping, keep the file and tell the user where it is.

## 7. Link and report
- Fill the spec's "Tickets" section with one line per ticket: `ID · title`.
- Report: tickets created (the CLI output lines are enough), total points, and the suggested order. Mention that `plan-cycle` moves them into To Do, and that the user can see them with `B serve`.
