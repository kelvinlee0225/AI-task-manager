#!/usr/bin/env python3
"""PreToolUse hook (Bash): keeps Claude inside the board rules.

Blocks: moving cards to Done / archiving (the --user flag), merging PRs,
and touching the board database directly. Exit code 2 blocks the command
and shows the reason to Claude.
"""
import json
import re
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if data.get("tool_name") != "Bash":
    sys.exit(0)

cmd = (data.get("tool_input") or {}).get("command", "")

RULES = [
    (lambda c: "board.py" in c and re.search(r"(^|\s)--user(\s|=|$)", c),
     "Only the user moves cards to Done or archives them. Tell the user which card is ready and that they can "
     "drag it in the board view or run the command themselves."),
    (lambda c: re.search(r"\bgh\s+pr\s+merge\b", c),
     "Claude never merges PRs. Leave the merge to the user."),
    (lambda c: re.search(r"\bsqlite3?\b", c) and re.search(r"board\.db", c),
     "Don't access the board database directly. Use .claude/board/board.py."),
    (lambda c: re.search(r"\b(rm|mv|truncate|shred)\b[^|;&]*\.board/?(board\.db|backups|\s|$)", c)
     or re.search(r">\s*\S*\.board/board\.db", c),
     "Don't delete or overwrite the board database or its backups. Use .claude/board/board.py."),
]

for test, reason in RULES:
    if test(cmd):
        print(f"Blocked by board rules: {reason}", file=sys.stderr)
        sys.exit(2)

sys.exit(0)
