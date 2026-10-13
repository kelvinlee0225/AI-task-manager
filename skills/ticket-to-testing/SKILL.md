---
name: ticket-to-testing
description: Finish a ticket's development — run checks and reviewers, verify each acceptance criterion, push, open the PR, move the card to Testing on the local board and report what was verified. Use when a ticket's implementation is done, or when asked to open the PR or send a ticket to testing.
---

# Send a ticket to Testing

Board conventions are in `.claude/rules/project-board.md`. `B` means `.claude/board/board.py`, run from the repo root.

## 1. Identify the ticket
Take the ID from the current branch name (`git branch --show-current`, e.g. `feat/INV-012-...`) and read the card: `B show <ID>`.

## 2. Run the checks
For each app the branch changed (`git diff --stat <base>...HEAD`):
- Backend: `yarn build`, `yarn lint`, `yarn test` in `backend/`.
- Frontend: `yarn typecheck`, `yarn lint`, `yarn test` in `frontend/`.
Fix failures before continuing.

## 3. Run the reviewers
Run `backend-reviewer` and/or `frontend-reviewer` for the changed apps. Fix every "Must fix". List "Should fix" items you didn't fix in the PR body.

## 4. Verify the acceptance criteria
For each criterion, decide how it's verified:
- **Test:** name the test that covers it.
- **Browser:** for frontend behavior, run the `ui-verifier` agent if the dev servers are running.
- **Not verified:** if neither is possible, say so. Don't claim it.

Check off only what was verified, with how:
```bash
B check <ID> AC1 AC2 --note "test: <test name>"
B check <ID> AC4 --note "browser: ui-verifier"
B check <ID> DOD1 DOD2                 # checks pass, reviewer run
B check <ID> DOD3                      # only if every AC is checked
```

## 5. Open the PR
```bash
git push -u origin HEAD
gh pr create --base <base> --title "$(B title <ID>)" --body-file <tmpfile>
```
PR body:
```markdown
Ticket: <ID>

## Summary
What changed and why, in a few lines.

## Acceptance criteria
- [x] AC1 … (verified by `test name` / browser check)
- [ ] AC3 … (not verified automatically: reason)

## Notes
Migrations · breaking API changes · frontend client regenerated · reviewer items not fixed · screenshots if UI changed.
```

## 6. Update the board
```bash
B edit <ID> --pr <number>
B move <ID> Testing
B comment <ID> "PR #<number>: <one-paragraph summary>. Not verified: <list or none>."
```

## 7. Report
The PR URL, which criteria were verified and how, and anything the user should test by hand. Remind the user that after merging they move the card to Done (drag it in the board view, or `B move <ID> Done --user` in their own terminal). Never merge the PR and never move the card to Done yourself.
