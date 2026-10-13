#!/usr/bin/env python3
"""Local kanban board for Claude Code. SQLite, Python 3.8+ standard library only.

Run from the repo root:  .claude/board/board.py <command> ...   (-h for help)
Database: .board/board.db (override with BOARD_DB).
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
DB = Path(os.environ.get("BOARD_DB") or ROOT / ".board" / "board.db")
CLI = ".claude/board/board.py"

LISTS = ["Backlog", "To Do", "In Progress", "Testing", "Done"]
TYPES = ["Feature", "Bug", "Chore", "Spike"]
AREAS = ["Backend", "Frontend", "Infra"]
PRIORITIES = ["P1", "P2", "P3"]
POINTS = {"S": 1, "M": 2, "L": 3}
WIP_LIMITS = {"In Progress": 2}
STALE_DAYS = 3
DOD = ["Checks pass", "Reviewer agent run", "Acceptance criteria verified", "PR merged"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS modules(
  key TEXT PRIMARY KEY, title TEXT NOT NULL, prefix TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS cycles(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  capacity INTEGER NOT NULL, goal TEXT, closed INTEGER NOT NULL DEFAULT 0,
  committed_pts INTEGER, done_pts INTEGER);
CREATE TABLE IF NOT EXISTS cards(
  id TEXT PRIMARY KEY, prefix TEXT NOT NULL, num INTEGER NOT NULL,
  module TEXT NOT NULL REFERENCES modules(key),
  title TEXT NOT NULL, type TEXT NOT NULL, area TEXT NOT NULL, priority TEXT NOT NULL, size TEXT,
  list TEXT NOT NULL, pos REAL NOT NULL DEFAULT 0,
  spec TEXT, branch TEXT, pr TEXT, description TEXT NOT NULL DEFAULT '',
  cycle_id INTEGER REFERENCES cycles(id), archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, list_changed_at TEXT NOT NULL,
  UNIQUE(prefix, num));
CREATE TABLE IF NOT EXISTS deps(
  card TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  depends_on TEXT NOT NULL REFERENCES cards(id),
  PRIMARY KEY(card, depends_on));
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, card TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('ac','dod')), pos INTEGER NOT NULL, text TEXT NOT NULL,
  checked INTEGER NOT NULL DEFAULT 0, note TEXT);
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY, card TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  author TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity(
  id INTEGER PRIMARY KEY, card TEXT, action TEXT NOT NULL, detail TEXT,
  actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cards_list ON cards(list, pos);
CREATE INDEX IF NOT EXISTS ix_items_card ON items(card, kind, pos);
CREATE INDEX IF NOT EXISTS ix_activity_card ON activity(card, created_at);
"""


class BoardError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def age_days(ts):
    if not ts:
        return 0
    return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).days


def connect(init=False):
    if not init and not DB.exists():
        raise BoardError(f"No board at {DB}. Run: {CLI} init")
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def read_text(path):
    if path == "-":
        return sys.stdin.read()
    p = Path(path)
    if not p.exists():
        raise BoardError(f"File not found: {path}")
    return p.read_text(encoding="utf-8")


_LIST_KEYS = {re.sub(r"[^a-z]", "", l.lower()): l for l in LISTS}
_LIST_KEYS.update({"doing": "In Progress", "wip": "In Progress", "test": "Testing", "review": "Testing"})


def resolve_list(name):
    key = re.sub(r"[^a-z]", "", (name or "").lower())
    if key in _LIST_KEYS:
        return _LIST_KEYS[key]
    raise BoardError(f"Unknown list {name!r}. Lists: {', '.join(LISTS)}")


_ALIASES = {"be": "Backend", "fe": "Frontend", "ops": "Infra", "devops": "Infra"}


def pick(value, options, field, required=False):
    if value is None or str(value).strip() in ("", "-"):
        if required:
            raise BoardError(f"Missing {field} (one of: {', '.join(options)})")
        return None
    v = _ALIASES.get(str(value).strip().lower(), str(value).strip())
    for o in options:
        if o.lower() == v.lower():
            return o
    raise BoardError(f"Invalid {field} {value!r}. Use one of: {', '.join(options)}")


ID_RE = re.compile(r"^([A-Z]{2,5})-(\d{1,4})$")


def parse_id(cid):
    m = ID_RE.match((cid or "").strip().upper())
    if not m:
        raise BoardError(f"Bad ticket ID {cid!r} (expected e.g. INV-012)")
    return m.group(1), int(m.group(2))


def fmt_id(prefix, num):
    return f"{prefix}-{num:03d}"


def norm_id(cid):
    return fmt_id(*parse_id(cid))


def split_ids(s):
    if not s:
        return []
    out = []
    for x in re.split(r"[,\s]+", s.strip()):
        if x and x.lower() not in ("-", "—", "none"):
            out.append(norm_id(x))
    return sorted(set(out))


def next_num(con, prefix):
    r = con.execute("SELECT MAX(num) FROM cards WHERE prefix=?", (prefix,)).fetchone()[0]
    return (r or 0) + 1


CARD_SQL = "SELECT c.*, m.title AS module_title FROM cards c JOIN modules m ON m.key = c.module"


def get_card(con, cid):
    cid = norm_id(cid)
    row = con.execute(CARD_SQL + " WHERE c.id=?", (cid,)).fetchone()
    if not row:
        raise BoardError(f"No card {cid}")
    return row


def full_title(c):
    return f"{c['id']} [{c['module_title']}]: {c['title']}"


def points(size):
    return POINTS.get(size or "", 0)


def log(con, card, action, detail, actor):
    con.execute("INSERT INTO activity(card, action, detail, actor, created_at) VALUES (?,?,?,?,?)",
                (card, action, detail, actor, now()))


def actor_of(a):
    return "user" if getattr(a, "user", False) else "claude"


def deps_of(con, cid):
    return con.execute(
        "SELECT d.depends_on AS id, c.list AS list FROM deps d JOIN cards c ON c.id = d.depends_on "
        "WHERE d.card=? ORDER BY d.depends_on", (cid,)).fetchall()


def blocks_of(con, cid):
    return [r[0] for r in con.execute("SELECT card FROM deps WHERE depends_on=? ORDER BY card", (cid,))]


def progress(con, cid, kind="ac"):
    r = con.execute("SELECT COALESCE(SUM(checked),0), COUNT(*) FROM items WHERE card=? AND kind=?",
                    (cid, kind)).fetchone()
    return r[0], r[1]


def last_activity(con, cid):
    return con.execute("SELECT MAX(created_at) FROM activity WHERE card=?", (cid,)).fetchone()[0]


def current_cycle(con):
    return con.execute("SELECT * FROM cycles WHERE closed=0 ORDER BY id DESC LIMIT 1").fetchone()


def readiness_issues(con, c):
    issues = []
    if not c["size"]:
        issues.append("no size")
    if progress(con, c["id"])[1] == 0:
        issues.append("no acceptance criteria")
    return issues


def list_ids(con, lst, exclude=None):
    return [r[0] for r in con.execute(
        "SELECT id FROM cards WHERE list=? AND archived=0 ORDER BY pos, id", (lst,)) if r[0] != exclude]


def place(con, cid, lst, before=None, top=False):
    ids = list_ids(con, lst, exclude=cid)
    if top:
        ids.insert(0, cid)
    elif before and before in ids:
        ids.insert(ids.index(before), cid)
    else:
        ids.append(cid)
    for i, x in enumerate(ids, 1):
        con.execute("UPDATE cards SET pos=? WHERE id=?", (i, x))


def set_items(con, cid, kind, texts, keep_checked=False):
    old = {}
    if keep_checked:
        for r in con.execute("SELECT text, checked, note FROM items WHERE card=? AND kind=?", (cid, kind)):
            old[r["text"]] = (r["checked"], r["note"])
    con.execute("DELETE FROM items WHERE card=? AND kind=?", (cid, kind))
    for i, t in enumerate(texts, 1):
        checked, note = old.get(t, (0, None))
        con.execute("INSERT INTO items(card, kind, pos, text, checked, note) VALUES (?,?,?,?,?,?)",
                    (cid, kind, i, t, checked, note))


def set_deps(con, cid, dep_ids):
    for d in dep_ids:
        if d == cid:
            raise BoardError(f"{cid} cannot depend on itself")
        if not con.execute("SELECT 1 FROM cards WHERE id=?", (d,)).fetchone():
            raise BoardError(f"{cid}: depends on unknown ticket {d}")
        stack, seen = [d], set()  # reject cycles: d must not (transitively) depend on cid
        while stack:
            x = stack.pop()
            if x == cid:
                raise BoardError(f"Dependency cycle: {d} already depends on {cid}")
            if x in seen:
                continue
            seen.add(x)
            stack.extend(r[0] for r in con.execute("SELECT depends_on FROM deps WHERE card=?", (x,)))
    con.execute("DELETE FROM deps WHERE card=?", (cid,))
    for d in dep_ids:
        con.execute("INSERT INTO deps(card, depends_on) VALUES (?,?)", (cid, d))


def card_line(con, c, show_list=False):
    done, total = progress(con, c["id"])
    parts = [c["id"]]
    if show_list:
        parts.append(c["list"])
    parts += [f"{c['type']}/{c['area']}", c["priority"], c["size"] or "no size", f"AC {done}/{total}"]
    deps = deps_of(con, c["id"])
    if deps:
        parts.append("deps " + ",".join(d["id"] + ("" if d["list"] == "Done" else "*") for d in deps))
    return " · ".join(parts) + " | " + c["title"]


def current_branch():
    try:
        r = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True,
                           timeout=5, cwd=str(ROOT))
        return r.stdout.strip()
    except Exception:
        return ""


def pr_merged(c):
    """True/False if gh can tell, None if unknown."""
    try:
        if c["pr"]:
            r = subprocess.run(["gh", "pr", "view", str(c["pr"]), "--json", "state", "-q", ".state"],
                               capture_output=True, text=True, timeout=20, cwd=str(ROOT))
            return r.stdout.strip() == "MERGED" if r.returncode == 0 else None
        r = subprocess.run(["gh", "pr", "list", "--state", "merged", "--search", c["id"],
                            "--json", "number", "-q", "length"],
                           capture_output=True, text=True, timeout=20, cwd=str(ROOT))
        return r.stdout.strip() not in ("", "0") if r.returncode == 0 else None
    except Exception:
        return None


def backup(keep=10):
    bdir = DB.parent / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dst = bdir / f"board-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(str(DB))
    out = sqlite3.connect(str(dst))
    src.backup(out)
    out.close()
    src.close()
    for f in sorted(bdir.glob("board-*.db"))[:-keep]:
        f.unlink()
    return dst


def auto_backup():
    """One backup per day, taken the first time the SessionStart hook runs that day."""
    bdir = DB.parent / "backups"
    if not (bdir.exists() and list(bdir.glob(f"board-{datetime.now():%Y%m%d}-*.db"))):
        backup()


# ---------------------------------------------------------------- moves

def do_move(con, cid, target, actor="claude", before=None, top=False, force=False, allow_wip=False):
    c = get_card(con, cid)
    if c["archived"]:
        raise BoardError(f"{c['id']} is archived")
    src = c["list"]
    if target == "Done" and actor != "user":
        raise BoardError(f"Only the user moves cards to Done. Tell them to run: {CLI} move {c['id']} Done "
                         f"--user (or drag the card in the board UI).")
    if target == "In Progress" and src != target and actor != "user" and not allow_wip:
        n = len(list_ids(con, "In Progress"))
        if n >= WIP_LIMITS["In Progress"]:
            raise BoardError(f"WIP limit: {n} cards already In Progress. Ask the user; if they agree, retry "
                             f"with --allow-wip.")
    if target == "To Do" and src == "Backlog" and not force:
        issues = readiness_issues(con, c)
        if issues:
            raise BoardError(f"{c['id']} is not ready for To Do: {', '.join(issues)}")
    ts = now()
    con.execute("UPDATE cards SET list=?, updated_at=?, "
                "list_changed_at = CASE WHEN list=? THEN list_changed_at ELSE ? END WHERE id=?",
                (target, ts, target, ts, c["id"]))
    cyc = current_cycle(con)
    if cyc and target in ("To Do", "In Progress") and c["cycle_id"] is None:
        con.execute("UPDATE cards SET cycle_id=? WHERE id=?", (cyc["id"], c["id"]))
    place(con, c["id"], target, before=before, top=top)
    log(con, c["id"], "move" if src != target else "reorder", f"{src} -> {target}", actor)
    return src


# ---------------------------------------------------------------- ticket files

HEADER_KEYS = {"id", "module", "prefix", "title", "type", "area", "priority", "size", "depends", "spec", "list"}
START_RE = re.compile(r"^===\s*ticket\b", re.I)


def parse_tickets(text):
    tickets, cur, in_head = [], None, False
    for line in text.splitlines():
        if START_RE.match(line):
            cur = {"fields": {}, "body": []}
            tickets.append(cur)
            in_head = True
            continue
        if cur is None:
            continue  # anything before the first ticket is ignored
        if in_head:
            if not line.strip():
                in_head = False
                continue
            m = re.match(r"^([A-Za-z_]+)\s*:\s*(.*)$", line)
            if not m or m.group(1).lower() not in HEADER_KEYS:
                raise BoardError(f"Bad header line {line!r}. Keys: {', '.join(sorted(HEADER_KEYS))}. "
                                 f"End the header with a blank line.")
            cur["fields"][m.group(1).lower()] = m.group(2).strip()
        else:
            cur["body"].append(line)
    out = []
    for t in tickets:
        desc, ac, has_ac = split_ac("\n".join(t["body"]))
        out.append(dict(t["fields"], description=desc, ac=ac, has_ac=has_ac))
    return out


def split_ac(body):
    kept, ac, in_ac, has_ac = [], [], False, False
    for line in body.splitlines():
        if re.match(r"^#{2,3}\s+acceptance criteria\s*$", line.strip(), re.I):
            in_ac, has_ac = True, True
            continue
        if in_ac and re.match(r"^#{1,3}\s", line):
            in_ac = False
        if in_ac:
            m = re.match(r"^\s*(?:\d+[.)]|[-*])\s+(?:\[[ xX]\]\s*)?(?:AC\d+[:.]?\s*)?(.+)$", line)
            if m:
                ac.append(m.group(1).strip())
            elif line.strip() and ac:
                ac[-1] += " " + line.strip()
            continue
        kept.append(line)
    return "\n".join(kept).strip(), ac, has_ac


def resolve_module(con, t):
    if t.get("module"):
        k = t["module"].strip()
        r = con.execute("SELECT * FROM modules WHERE lower(key)=lower(?) OR lower(title)=lower(?)", (k, k)).fetchone()
        label = f"module {k!r}"
    elif t.get("prefix") or t.get("id"):
        p = t["prefix"].strip().upper() if t.get("prefix") else parse_id(t["id"])[0]
        r = con.execute("SELECT * FROM modules WHERE prefix=?", (p,)).fetchone()
        label = f"prefix {p}"
    else:
        raise BoardError(f"Ticket {t.get('title', '?')!r}: needs a module, prefix or id")
    if not r:
        raise BoardError(f"Unknown {label}. Add it first: {CLI} module add <key> \"<Title>\" <PREFIX>")
    return r


def export_ticket(con, c):
    deps = ", ".join(d["id"] for d in deps_of(con, c["id"]))
    head = [f"id: {c['id']}", f"module: {c['module']}", f"title: {c['title']}", f"type: {c['type']}",
            f"area: {c['area']}", f"priority: {c['priority']}", f"size: {c['size'] or ''}",
            f"depends: {deps}", f"spec: {c['spec'] or ''}"]
    ac = [r["text"] for r in con.execute(
        "SELECT text FROM items WHERE card=? AND kind='ac' ORDER BY pos", (c["id"],))]
    body = c["description"].strip()
    acs = "\n".join(f"{i}. {t}" for i, t in enumerate(ac, 1))
    return "=== ticket\n" + "\n".join(head) + "\n\n" + (body + "\n\n" if body else "") + \
        "## Acceptance criteria\n" + acs + "\n"


# ---------------------------------------------------------------- commands

def cmd_init(a):
    con = connect(init=True)
    con.executescript(SCHEMA)
    con.commit()
    print(f"Board ready at {DB}")
    if not con.execute("SELECT 1 FROM modules").fetchone():
        print(f"Next: register modules, e.g. {CLI} module add invoices Invoices INV")


def cmd_module(a):
    con = connect()
    if a.action == "add":
        if not (a.key and a.title and a.prefix):
            raise BoardError(f"Usage: {CLI} module add <key> \"<Title>\" <PREFIX>")
        prefix = a.prefix.upper()
        if not re.match(r"^[A-Z]{2,5}$", prefix):
            raise BoardError("Prefix must be 2-5 letters")
        try:
            con.execute("INSERT INTO modules(key, title, prefix) VALUES (?,?,?)", (a.key, a.title, prefix))
        except sqlite3.IntegrityError:
            raise BoardError(f"Module key {a.key!r} or prefix {prefix} already exists")
        log(con, None, "module", f"{a.key} {a.title} {prefix}", actor_of(a))
        con.commit()
        print(f"Module {a.key} ({a.title}) → {prefix}")
    else:
        rows = con.execute("SELECT m.*, (SELECT COUNT(*) FROM cards c WHERE c.module=m.key) AS n "
                           "FROM modules m ORDER BY key").fetchall()
        if not rows:
            print("No modules yet.")
        for r in rows:
            print(f"{r['key']} | {r['title']} | {r['prefix']} | {r['n']} cards | "
                  f"next {fmt_id(r['prefix'], next_num(con, r['prefix']))}")


def cmd_next(a):
    con = connect()
    p = a.prefix.upper()
    n = next_num(con, p)
    print(" ".join(fmt_id(p, n + i) for i in range(a.count)))


def cmd_import(a):
    tickets = parse_tickets(read_text(a.file))
    if not tickets:
        raise BoardError("No tickets found. Each ticket starts with a line '=== ticket'.")
    con = connect()
    actor = actor_of(a)
    try:
        lines = (import_update if a.update else import_create)(con, tickets, actor)
    except BoardError:
        con.rollback()
        raise
    if a.dry_run:
        con.rollback()
        print("DRY RUN, nothing saved.")
    else:
        con.commit()
    print("\n".join(lines))


def normalize_fields(t, rec):
    if "title" in t:
        title = re.sub(r"^[A-Z]{2,5}-\d+\s*\[[^\]]*\]:\s*", "", t["title"].strip())
        if not title:
            raise BoardError("Empty title")
        rec["title"] = title
    if "type" in t:
        rec["type"] = pick(t["type"], TYPES, "type", required=True)
    if "area" in t:
        rec["area"] = pick(t["area"], AREAS, "area", required=True)
    if "priority" in t:
        rec["priority"] = pick(t["priority"], PRIORITIES, "priority") or "P2"
    if "size" in t:
        rec["size"] = pick(t["size"], list(POINTS), "size")
    if "spec" in t:
        rec["spec"] = t["spec"] or None
    return rec


def import_create(con, tickets, actor):
    explicit = {}
    for t in tickets:
        if t.get("id"):
            p, n = parse_id(t["id"])
            explicit.setdefault(p, set()).add(n)
    counters, rows, warnings = {}, [], []
    for t in tickets:
        m = resolve_module(con, t)
        prefix = m["prefix"]
        if t.get("id"):
            p, n = parse_id(t["id"])
            if p != prefix:
                raise BoardError(f"{t['id']}: prefix doesn't match module {m['key']} ({prefix})")
            if con.execute("SELECT 1 FROM cards WHERE prefix=? AND num=?", (p, n)).fetchone():
                raise BoardError(f"{fmt_id(p, n)} already exists. Next free: {fmt_id(p, next_num(con, p))}")
        else:
            if prefix not in counters:
                counters[prefix] = max([next_num(con, prefix)] + [x + 1 for x in explicit.get(prefix, ())])
            n = counters[prefix]
            counters[prefix] += 1
        cid = fmt_id(prefix, n)
        if any(r["id"] == cid for r in rows):
            raise BoardError(f"Duplicate ID {cid} in file")
        for k in ("title", "type", "area"):
            if not t.get(k):
                raise BoardError(f"{cid}: missing {k}")
        rec = normalize_fields(t, {"id": cid, "prefix": prefix, "num": n, "module": m["key"],
                                   "priority": "P2", "size": None, "spec": None})
        rec["list"] = resolve_list(t.get("list") or "Backlog")
        if rec["list"] not in ("Backlog", "To Do"):
            raise BoardError(f"{cid}: new cards go to Backlog or To Do, not {rec['list']}")
        rec.update(description=t["description"], ac=t["ac"], depends=split_ids(t.get("depends")))
        if not rec["ac"]:
            warnings.append(f"{cid}: no acceptance criteria (not ready for To Do)")
        elif len(rec["ac"]) > 7:
            warnings.append(f"{cid}: {len(rec['ac'])} acceptance criteria, consider splitting")
        if not rec["size"]:
            warnings.append(f"{cid}: no size (not ready for To Do)")
        rows.append(rec)

    ts = now()
    cyc = current_cycle(con)
    for r in rows:
        pos = con.execute("SELECT COALESCE(MAX(pos),0) FROM cards WHERE list=?", (r["list"],)).fetchone()[0] + 1
        con.execute(
            "INSERT INTO cards(id, prefix, num, module, title, type, area, priority, size, list, pos, spec, "
            "description, cycle_id, created_at, updated_at, list_changed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["prefix"], r["num"], r["module"], r["title"], r["type"], r["area"], r["priority"],
             r["size"], r["list"], pos, r["spec"], r["description"],
             cyc["id"] if (cyc and r["list"] == "To Do") else None, ts, ts, ts))
        set_items(con, r["id"], "ac", r["ac"])
        set_items(con, r["id"], "dod", DOD)
        log(con, r["id"], "create", r["list"], actor)
    for r in rows:
        set_deps(con, r["id"], r["depends"])
    out = [f"{len(rows)} card(s) → " + ", ".join(sorted({r['list'] for r in rows}))
           + f" · {sum(points(r['size']) for r in rows)} pts"]
    for r in rows:
        out.append(card_line(con, get_card(con, r["id"])))
    out += ["warning: " + w for w in warnings]
    return out


def apply_update(con, c, fields, description=None, ac=None, actor="claude"):
    """Update an existing card. fields: header-style keys; description/ac: None means unchanged.
    Acceptance criteria are replaced; items whose text didn't change keep their checkmark."""
    rec = normalize_fields(fields, {})
    if description is not None:
        rec["description"] = description.strip()
    changed = [k for k, v in rec.items() if c[k] != v]
    if changed:
        con.execute("UPDATE cards SET " + ", ".join(f"{k}=?" for k in changed) + ", updated_at=? WHERE id=?",
                    [rec[k] for k in changed] + [now(), c["id"]])
    if "depends" in fields:
        old = [d["id"] for d in deps_of(con, c["id"])]
        new = split_ids(fields["depends"])
        if old != new:
            set_deps(con, c["id"], new)
            changed.append("depends")
    if ac is not None:
        old = [r[0] for r in con.execute(
            "SELECT text FROM items WHERE card=? AND kind='ac' ORDER BY pos", (c["id"],))]
        if old != ac:
            set_items(con, c["id"], "ac", ac, keep_checked=True)
            changed.append("acceptance criteria")
    if changed:
        log(con, c["id"], "edit", ", ".join(changed), actor)
    return changed


def import_update(con, tickets, actor):
    out = []
    for t in tickets:
        if not t.get("id"):
            raise BoardError("--update needs an id line in every ticket")
        c = get_card(con, t["id"])
        if t.get("module") and t["module"].lower() != c["module"].lower():
            raise BoardError(f"{c['id']}: changing a card's module isn't supported")
        fields = {k: v for k, v in t.items() if k in ("title", "type", "area", "priority", "size", "spec", "depends")}
        changed = apply_update(con, c, fields, description=t["description"] or None,
                               ac=t["ac"] if t["has_ac"] else None, actor=actor)
        if "list" in t:
            out.append(f"warning: {c['id']}: 'list' ignored on update, use move")
        out.append(f"{c['id']} updated: {', '.join(changed)}" if changed else f"{c['id']}: no changes")
    return out


def cmd_ls(a):
    con = connect()
    where, args = ["c.archived=0"], []
    if a.module:
        where.append("(lower(c.module)=lower(?) OR c.prefix=upper(?))")
        args += [a.module, a.module]
    lists = [resolve_list(a.list)] if a.list else LISTS
    rows = con.execute(CARD_SQL + " WHERE " + " AND ".join(where) + " ORDER BY c.pos, c.id", args).fetchall()
    if not rows and not a.list:
        print("Board is empty." if not a.module else f"No cards for {a.module}.")
        return
    for l in lists:
        cards = [c for c in rows if c["list"] == l]
        pts = sum(points(c["size"]) for c in cards)
        if l == "Done" and not (a.done or a.list):
            if cards:
                print(f"## Done: {len(cards)} cards, {pts} pts (hidden, --done to show)")
            continue
        if not cards and not a.list:
            continue
        print(f"## {l}: {len(cards)} cards, {pts} pts")
        for c in cards:
            print(card_line(con, c))


def cmd_show(a):
    con = connect()
    c = get_card(con, a.id)
    if a.ticket:
        print(export_ticket(con, c), end="")
        return
    deps = deps_of(con, c["id"])
    cyc = con.execute("SELECT name FROM cycles WHERE id=?", (c["cycle_id"],)).fetchone() if c["cycle_id"] else None
    meta = [c["type"], c["area"], c["priority"], c["size"] or "no size",
            f"{c['list']} ({age_days(c['list_changed_at'])}d)"]
    if c["archived"]:
        meta.append("ARCHIVED")
    if cyc:
        meta.append(cyc["name"])
    if c["branch"]:
        meta.append(f"branch {c['branch']}")
    if c["pr"]:
        meta.append(f"PR {c['pr']}")
    print(full_title(c))
    print(" · ".join(meta))
    print("Depends on: " + (", ".join(f"{d['id']} ({d['list']})" for d in deps) or "—") +
          " · Blocks: " + (", ".join(blocks_of(con, c["id"])) or "—") +
          (f" · Spec: {c['spec']}" if c["spec"] else ""))
    if not a.brief and c["description"]:
        print("\n" + c["description"].strip())
    for kind, label in (("ac", "Acceptance criteria"), ("dod", "Definition of done")):
        done, total = progress(con, c["id"], kind)
        print(f"\n{label} ({done}/{total})")
        for r in con.execute("SELECT * FROM items WHERE card=? AND kind=? ORDER BY pos", (c["id"], kind)):
            note = f" — {r['note']}" if r["note"] else ""
            print(f"[{'x' if r['checked'] else ' '}] {kind.upper()}{r['pos']} {r['text']}{note}")
    if not a.brief:
        comments = con.execute("SELECT * FROM comments WHERE card=? ORDER BY id", (c["id"],)).fetchall()
        if comments:
            print(f"\nComments ({len(comments)})")
            for r in comments:
                print(f"- {r['created_at'][:16].replace('T', ' ')} {r['author']}: {r['body']}")


def cmd_title(a):
    con = connect()
    print(full_title(get_card(con, a.id)))


def cmd_move(a):
    con = connect()
    target = resolve_list(a.list)
    before = norm_id(a.before) if a.before else None
    try:
        src = do_move(con, a.id, target, actor=actor_of(a), before=before, top=a.top,
                      force=a.force, allow_wip=a.allow_wip)
    except BoardError:
        con.rollback()
        raise
    con.commit()
    c = get_card(con, a.id)
    ids = list_ids(con, target)
    print(f"{c['id']}: {src} → {target} (position {ids.index(c['id']) + 1}/{len(ids)})")


def cmd_edit(a):
    con = connect()
    c = get_card(con, a.id)
    sets = {}
    if a.title:
        sets["title"] = a.title.strip()
    if a.type:
        sets["type"] = pick(a.type, TYPES, "type")
    if a.area:
        sets["area"] = pick(a.area, AREAS, "area")
    if a.priority:
        sets["priority"] = pick(a.priority, PRIORITIES, "priority")
    if a.size is not None:
        sets["size"] = pick(a.size, list(POINTS), "size")
    for k in ("spec", "branch", "pr"):
        v = getattr(a, k)
        if v is not None:
            sets[k] = v.strip() or None
    if a.desc_file:
        sets["description"] = read_text(a.desc_file).strip()
    if not sets and a.depends is None:
        raise BoardError("Nothing to change")
    if sets:
        con.execute("UPDATE cards SET " + ", ".join(f"{k}=?" for k in sets) + ", updated_at=? WHERE id=?",
                    list(sets.values()) + [now(), c["id"]])
    changed = list(sets)
    if a.depends is not None:
        set_deps(con, c["id"], split_ids(a.depends))
        changed.append("depends")
    log(con, c["id"], "edit", ", ".join(changed), actor_of(a))
    con.commit()
    print(f"{c['id']} updated: {', '.join(changed)}")


def resolve_item(con, cid, ref):
    m = re.match(r"^(AC|DOD)(\d+)$", ref.strip().upper())
    if not m:
        raise BoardError(f"Bad item {ref!r}: use AC1, AC2… or DOD1, DOD2…")
    row = con.execute("SELECT * FROM items WHERE card=? AND kind=? AND pos=?",
                      (cid, m.group(1).lower(), int(m.group(2)))).fetchone()
    if not row:
        raise BoardError(f"{cid} has no {ref.upper()}")
    return row


def cmd_check(a, value):
    con = connect()
    c = get_card(con, a.id)
    refs = []
    for ref in a.items:
        it = resolve_item(con, c["id"], ref)
        if value:
            con.execute("UPDATE items SET checked=1, note=COALESCE(?, note) WHERE id=?", (a.note, it["id"]))
        else:
            con.execute("UPDATE items SET checked=0, note=NULL WHERE id=?", (it["id"],))
        refs.append(ref.upper())
    log(con, c["id"], "check" if value else "uncheck",
        " ".join(refs) + (f" ({a.note})" if value and a.note else ""), actor_of(a))
    con.execute("UPDATE cards SET updated_at=? WHERE id=?", (now(), c["id"]))
    con.commit()
    ac, dod = progress(con, c["id"]), progress(con, c["id"], "dod")
    print(f"{c['id']}: {'checked' if value else 'unchecked'} {' '.join(refs)} · "
          f"AC {ac[0]}/{ac[1]} · DoD {dod[0]}/{dod[1]}")


def cmd_comment(a):
    con = connect()
    c = get_card(con, a.id)
    body = read_text("-") if a.text == "-" else a.text
    if not body.strip():
        raise BoardError("Empty comment")
    con.execute("INSERT INTO comments(card, author, body, created_at) VALUES (?,?,?,?)",
                (c["id"], actor_of(a), body.strip(), now()))
    log(con, c["id"], "comment", body.strip()[:80], actor_of(a))
    con.commit()
    print(f"{c['id']}: comment added")


def cmd_archive(a):
    con = connect()
    c = get_card(con, a.id)
    if not a.user:
        raise BoardError(f"Only the user archives cards ({CLI} archive {c['id']} --user).")
    con.execute("UPDATE cards SET archived=1, updated_at=? WHERE id=?", (now(), c["id"]))
    log(con, c["id"], "archive", c["list"], "user")
    con.commit()
    print(f"{c['id']} archived")


def cycle_points(con, cycle_id):
    rows = con.execute("SELECT size, list FROM cards WHERE cycle_id=? AND archived=0", (cycle_id,)).fetchall()
    return sum(points(r["size"]) for r in rows), sum(points(r["size"]) for r in rows if r["list"] == "Done")


def cycle_header(con, cyc):
    committed, done = cycle_points(con, cyc["id"])
    day = (date.today() - date.fromisoformat(cyc["start_date"])).days + 1
    length = (date.fromisoformat(cyc["end_date"]) - date.fromisoformat(cyc["start_date"])).days + 1
    return (f"{cyc['name']}: day {day}/{length} · {done}/{committed} pts done · capacity {cyc['capacity']}"
            + (f" · goal: {cyc['goal']}" if cyc["goal"] else ""))


def cmd_cycle(a):
    con = connect()
    cyc = current_cycle(con)
    actor = actor_of(a)
    if a.action == "new":
        if cyc:
            raise BoardError(f"'{cyc['name']}' is still open. Close it first: {CLI} cycle close")
        last = con.execute("SELECT * FROM cycles WHERE closed=1 ORDER BY id DESC LIMIT 1").fetchone()
        capacity = a.capacity or (last["done_pts"] if last and last["done_pts"] else round(8 * a.days / 7))
        start = date.fromisoformat(a.start) if a.start else date.today()
        end = start + timedelta(days=a.days - 1)
        n = con.execute("SELECT COUNT(*) FROM cycles").fetchone()[0] + 1
        name = a.name or f"Cycle {n}"
        cid = con.execute("INSERT INTO cycles(name, start_date, end_date, capacity, goal) VALUES (?,?,?,?,?)",
                          (name, start.isoformat(), end.isoformat(), capacity, a.goal)).lastrowid
        carried = con.execute("UPDATE cards SET cycle_id=? WHERE archived=0 AND list IN "
                              "('To Do','In Progress','Testing')", (cid,)).rowcount
        log(con, None, "cycle_new", name, actor)
        con.commit()
        committed, _ = cycle_points(con, cid)
        print(f"{name}: {start} → {end} · capacity {capacity} pts · carryover {carried} cards ({committed} pts)")
    elif a.action == "close":
        if not cyc:
            raise BoardError("No open cycle")
        committed, done = cycle_points(con, cyc["id"])
        con.execute("UPDATE cycles SET closed=1, committed_pts=?, done_pts=? WHERE id=?",
                    (committed, done, cyc["id"]))
        log(con, None, "cycle_close", f"{cyc['name']} {done}/{committed}", actor)
        con.commit()
        left = con.execute("SELECT id FROM cards WHERE cycle_id=? AND archived=0 AND list!='Done'",
                           (cyc["id"],)).fetchall()
        print(f"Closed {cyc['name']}: {done}/{committed} pts done (capacity {cyc['capacity']}). "
              f"Unfinished: {', '.join(r[0] for r in left) or 'none'} (carried into the next cycle)")
    elif a.action == "ls":
        rows = con.execute("SELECT * FROM cycles ORDER BY id").fetchall()
        if not rows:
            print("No cycles yet.")
        for r in rows:
            committed, done = (r["committed_pts"], r["done_pts"]) if r["closed"] else cycle_points(con, r["id"])
            print(f"{r['name']} | {r['start_date']} → {r['end_date']} | capacity {r['capacity']} | "
                  f"done {done}/{committed} pts | {'closed' if r['closed'] else 'OPEN'}"
                  + (f" | goal: {r['goal']}" if r["goal"] else ""))
    else:  # show
        if not cyc:
            print("No open cycle.")
            return
        print(cycle_header(con, cyc))
        for c in con.execute(CARD_SQL + " WHERE c.cycle_id=? AND c.archived=0 ORDER BY "
                             "CASE c.list WHEN 'Done' THEN 4 WHEN 'Testing' THEN 3 WHEN 'In Progress' THEN 2 "
                             "ELSE 1 END, c.pos", (cyc["id"],)):
            print(card_line(con, c, show_list=True))


def cmd_status(a):
    con = connect()
    cards = con.execute(CARD_SQL + " WHERE c.archived=0 ORDER BY c.pos").fetchall()
    by = {l: [c for c in cards if c["list"] == l] for l in LISTS}
    cyc = current_cycle(con)
    out = [cycle_header(con, cyc) if cyc else "No open cycle."]
    out.append(" | ".join(f"{l} {len(by[l])} ({sum(points(c['size']) for c in by[l])} pts)" for l in LISTS))

    merged = {c["id"]: pr_merged(c) for c in by["Testing"]} if a.prs else {}
    for l in ("In Progress", "Testing"):
        if by[l]:
            out.append(f"\n{l}")
            for c in by[l]:
                d, t = progress(con, c["id"])
                extra = [f"{age_days(c['list_changed_at'])}d", f"AC {d}/{t}"]
                if c["branch"]:
                    extra.append(c["branch"])
                if c["pr"]:
                    extra.append(f"PR {c['pr']}")
                if c["id"] in merged:
                    extra.append({True: "merged", False: "not merged", None: "PR state unknown"}[merged[c["id"]]])
                out.append(f"  {c['id']} {c['title']} · " + " · ".join(extra))

    problems = []
    n = len(by["In Progress"])
    if n > WIP_LIMITS["In Progress"]:
        problems.append(f"WIP: {n} cards In Progress (limit {WIP_LIMITS['In Progress']}). Finish one before starting more.")
    for c in by["In Progress"] + by["Testing"]:
        last = last_activity(con, c["id"]) or c["list_changed_at"]
        if age_days(last) >= STALE_DAYS:
            problems.append(f"Stale: {c['id']} in {c['list']}, no activity for {age_days(last)}d.")
    for c in by["To Do"] + by["In Progress"]:
        waiting = [f"{d['id']} ({d['list']})" for d in deps_of(con, c["id"]) if d["list"] != "Done"]
        if waiting:
            problems.append(f"Blocked: {c['id']} ({c['list']}) waits on {', '.join(waiting)}.")
    for c in by["To Do"]:
        issues = readiness_issues(con, c)
        if issues:
            problems.append(f"Not ready: {c['id']} in To Do has {' and '.join(issues)}.")
    if cyc:
        committed, _ = cycle_points(con, cyc["id"])
        if committed > cyc["capacity"]:
            problems.append(f"Over capacity: {committed} pts committed vs {cyc['capacity']}.")
    for c in by["Testing"]:
        d, t = progress(con, c["id"])
        if d < t:
            problems.append(f"Unverified: {c['id']} in Testing has {t - d} acceptance criteria not checked.")
    out.append("\nProblems" if problems else "\nProblems: none")
    out += [f"  - {p}" for p in problems]
    ready = [c for c in by["Testing"] if merged.get(c["id"])]
    if ready:
        out.append("\nReady for Done (PR merged): " + ", ".join(c["id"] for c in ready))
        out.append(f"  The user moves them: {CLI} move <ID> Done --user, or drags them in the board UI.")
    elif not a.prs and by["Testing"]:
        out.append("\n(Run with --prs to check which Testing PRs are merged.)")
    print("\n".join(out))


def cmd_brief(a):
    """Compact summary for the SessionStart hook. Never fails loudly."""
    try:
        if not DB.exists():
            print(f"[board] No local board yet. Create it with: {CLI} init")
            return
        try:
            auto_backup()
        except Exception:
            pass
        con = connect()
        cards = con.execute(CARD_SQL + " WHERE c.archived=0 ORDER BY c.pos").fetchall()
        counts = {l: sum(1 for c in cards if c["list"] == l) for l in LISTS}
        lines = ["[board] " + " | ".join(f"{l} {n}" for l, n in counts.items())]
        cyc = current_cycle(con)
        if cyc:
            committed, done = cycle_points(con, cyc["id"])
            lines.append(f"[board] {cyc['name']} ends {cyc['end_date']} · {done}/{committed} pts done")
        for c in cards:
            if c["list"] in ("In Progress", "Testing"):
                d, t = progress(con, c["id"])
                lines.append(f"[board] {c['list']}: {full_title(c)} · AC {d}/{t}")
        br = current_branch()
        m = re.search(r"([A-Z]{2,5}-\d{3,4})", br or "")
        if m:
            row = con.execute("SELECT list FROM cards WHERE id=?", (m.group(1),)).fetchone()
            if row:
                lines.append(f"[board] Current branch {br} → {m.group(1)} ({row['list']})")
        print("\n".join(lines))
    except Exception as e:
        print(f"[board] status unavailable: {e}")


def cmd_log(a):
    con = connect()
    if a.id:
        rows = con.execute("SELECT * FROM activity WHERE card=? ORDER BY id DESC LIMIT ?", (norm_id(a.id), a.limit))
    else:
        rows = con.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (a.limit,))
    for r in rows:
        print(f"{r['created_at'][:16].replace('T', ' ')} {r['actor']:6} {r['card'] or '-':8} "
              f"{r['action']} {r['detail'] or ''}")


def cmd_backup(a):
    connect()
    print(f"Backup written: {backup()}")


# ---------------------------------------------------------------- web UI

def board_json(con):
    out = []
    for c in con.execute(CARD_SQL + " WHERE c.archived=0 ORDER BY c.pos, c.id").fetchall():
        d, t = progress(con, c["id"])
        out.append(dict(id=c["id"], title=c["title"], module=c["module_title"], type=c["type"], area=c["area"],
                        priority=c["priority"], size=c["size"], list=c["list"], pos=c["pos"], ac=[d, t],
                        deps=[{"id": x["id"], "done": x["list"] == "Done"} for x in deps_of(con, c["id"])],
                        branch=c["branch"], pr=c["pr"], days=age_days(c["list_changed_at"])))
    cyc = current_cycle(con)
    return {"lists": LISTS, "cards": out, "cycle": dict(cyc) if cyc else None, "wip": WIP_LIMITS}


def card_json(con, cid):
    c = get_card(con, cid)
    return dict(
        {k: c[k] for k in c.keys()}, full_title=full_title(c),
        deps=[dict(d) for d in deps_of(con, c["id"])], blocks=blocks_of(con, c["id"]),
        items=[dict(r) for r in con.execute("SELECT * FROM items WHERE card=? ORDER BY kind, pos", (c["id"],))],
        comments=[dict(r) for r in con.execute("SELECT * FROM comments WHERE card=? ORDER BY id", (c["id"],))],
        activity=[dict(r) for r in con.execute(
            "SELECT * FROM activity WHERE card=? ORDER BY id DESC LIMIT 15", (c["id"],))])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype="application/json"):
        data = (json.dumps(body) if ctype == "application/json" else body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def handle_api(self, fn):
        con = None
        try:
            con = connect()
            fn(con)
        except BoardError as e:
            if con:
                con.rollback()
            self.reply(400, {"error": str(e)})
        except Exception as e:
            self.reply(500, {"error": str(e)})
        finally:
            if con:
                con.close()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self.reply(200, PAGE, "text/html")
        if u.path == "/api/board":
            return self.handle_api(lambda con: self.reply(200, board_json(con)))
        if u.path == "/api/card":
            cid = parse_qs(u.query).get("id", [""])[0]
            return self.handle_api(lambda con: self.reply(200, card_json(con, cid)))
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        # JSON only, so other websites can't post here without a CORS preflight (which is never answered).
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.reply(415, {"error": "JSON only"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        path = urlparse(self.path).path

        def move(con):
            do_move(con, body["id"], resolve_list(body["list"]), actor="user",
                    before=body.get("before"), force=True)
            con.commit()
            self.reply(200, {"ok": True})

        def check(con):
            it = con.execute("SELECT * FROM items WHERE id=?", (int(body["item"]),)).fetchone()
            if not it:
                raise BoardError("No such item")
            con.execute("UPDATE items SET checked=? WHERE id=?", (1 if body.get("checked") else 0, it["id"]))
            log(con, it["card"], "check" if body.get("checked") else "uncheck",
                f"{it['kind'].upper()}{it['pos']}", "user")
            con.commit()
            self.reply(200, {"ok": True})

        def comment(con):
            c = get_card(con, body["id"])
            text = (body.get("body") or "").strip()
            if not text:
                raise BoardError("Empty comment")
            con.execute("INSERT INTO comments(card, author, body, created_at) VALUES (?,?,?,?)",
                        (c["id"], "user", text, now()))
            log(con, c["id"], "comment", text[:80], "user")
            con.commit()
            self.reply(200, {"ok": True})

        def edit(con):
            c = get_card(con, body["id"])
            fields = {k: str(body[k]) for k in ("title", "type", "area", "priority", "size", "depends") if k in body}
            ac = [x.strip() for x in body["ac"] if str(x).strip()] if "ac" in body else None
            changed = apply_update(con, c, fields, description=body.get("description"), ac=ac, actor="user")
            con.commit()
            self.reply(200, {"ok": True, "changed": changed})

        routes = {"/api/move": move, "/api/check": check, "/api/comment": comment, "/api/edit": edit}
        if path not in routes:
            return self.reply(404, {"error": "not found"})
        self.handle_api(routes[path])


def cmd_serve(a):
    connect()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}"
    print(f"Board UI at {url} (Ctrl+C to stop)")
    if a.open:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Project Board</title>
<style>
:root{--bg:#f4f5f7;--col:#ebecf0;--card:#fff;--text:#172b4d;--muted:#5e6c84;--line:#dfe1e6;--accent:#0c66e4;--warn:#c9372c;--ok:#1f845a;--amber:#b65c02}
@media (prefers-color-scheme:dark){:root{--bg:#1d2125;--col:#22272b;--card:#2c333a;--text:#dee4ea;--muted:#9fadbc;--line:#384148;--accent:#579dff;--warn:#f87168;--ok:#4bce97;--amber:#f5cd47}}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--text)}
header{display:flex;gap:12px;align-items:center;padding:12px 16px;flex-wrap:wrap}
header h1{font-size:16px;margin:0 8px 0 0}
#cycle{color:var(--muted);font-size:13px;flex:1;min-width:200px}
select,input[type=search],textarea{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--text)}
label.tog{color:var(--muted);font-size:13px;display:flex;gap:4px;align-items:center}
#board{display:flex;gap:12px;padding:0 16px 24px;overflow-x:auto;align-items:flex-start;min-height:calc(100vh - 60px)}
.col{background:var(--col);border-radius:10px;width:280px;flex-shrink:0;padding:8px}
.col h2{font-size:13px;font-weight:600;margin:4px 6px 10px;display:flex;justify-content:space-between;gap:8px}
.col h2 .n{color:var(--muted);font-weight:400}.col h2 .over{color:var(--warn);font-weight:600}
.cards{min-height:48px;display:flex;flex-direction:column;gap:8px}
.card{background:var(--card);border-radius:8px;padding:8px 10px;box-shadow:0 1px 1px rgba(0,0,0,.18);cursor:grab;border-left:3px solid transparent}
.card.P1{border-left-color:var(--warn)}.card.P2{border-left-color:var(--amber)}
.card .id{font-size:12px;color:var(--muted);display:flex;justify-content:space-between}
.card .t{margin:2px 0 6px}
.tags{display:flex;gap:4px;flex-wrap:wrap}
.tag{font-size:11px;padding:1px 6px;border-radius:4px;background:var(--col);color:var(--muted)}
.tag.blocked{color:var(--warn)}.tag.full{color:var(--ok)}
.col.drop{outline:2px dashed var(--accent);outline-offset:2px}
.dragging{opacity:.4}
.hidden-note{font-size:12px;color:var(--muted);padding:4px 6px}
#panel{position:fixed;inset:0;background:rgba(9,30,66,.45);display:none;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto}
#panel.open{display:flex}
#detail{background:var(--card);border-radius:12px;max-width:760px;width:100%;padding:20px 22px}
#detail h3{margin:0 0 6px;font-size:18px}
#detail h4{margin:18px 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.meta{color:var(--muted);font-size:13px}
.desc{white-space:pre-wrap;font-size:13px;background:var(--bg);padding:12px;border-radius:8px;margin-top:12px}
.item{display:flex;gap:8px;align-items:flex-start;margin:5px 0;cursor:pointer}.item input{margin-top:3px}
.item small{color:var(--muted)}
.comments div{border-top:1px solid var(--line);padding:6px 0;font-size:13px;white-space:pre-wrap}
.comments small,.act small{color:var(--muted)}
.act{font-size:12px;color:var(--muted)}
textarea{width:100%;margin-top:8px}
.f{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted);margin-top:10px}
.f input,.f select,.f textarea{font:inherit;font-size:14px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--text);margin:0}
.f textarea{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:0 10px}
.row{display:flex;gap:8px;justify-content:flex-end;margin-top:8px}
button{font:inherit;padding:6px 12px;border-radius:6px;border:1px solid var(--line);background:var(--col);color:var(--text);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
#err{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--warn);color:#fff;padding:8px 14px;border-radius:8px;display:none}
@media (max-width:600px){#board{padding:0 8px 16px}.col{width:85vw}}
</style></head><body>
<header><h1>Project Board</h1><span id="cycle"></span>
<select id="module"><option value="">All modules</option></select>
<input type="search" id="q" placeholder="Filter by ID or title">
<label class="tog"><input type="checkbox" id="showDone"> Show Done</label></header>
<main id="board"></main>
<div id="panel"><div id="detail"></div></div>
<div id="err"></div>
<script>
let data=null, dragId=null;
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const PTS={S:1,M:2,L:3};
function showErr(m){const e=$('#err');e.textContent=m;e.style.display='block';setTimeout(()=>e.style.display='none',4000)}
async function api(path,body){
  const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const j=await r.json(); if(!r.ok) throw new Error(j.error||r.status); return j;}
async function load(){try{data=await api('/api/board');render()}catch(e){showErr(e.message)}}
function render(){
  if(!data||dragId) return;
  const sel=$('#module'),cur=sel.value,mods=[...new Set(data.cards.map(c=>c.module))].sort();
  sel.innerHTML='<option value="">All modules</option>'+mods.map(m=>`<option ${m===cur?'selected':''}>${esc(m)}</option>`).join('');
  const q=$('#q').value.trim().toLowerCase(), showDone=$('#showDone').checked, cy=data.cycle;
  $('#cycle').textContent=cy?`${cy.name} · ${cy.start_date} → ${cy.end_date} · capacity ${cy.capacity} pts${cy.goal?' · '+cy.goal:''}`:'No open cycle';
  const b=$('#board'); b.innerHTML='';
  for(const l of data.lists){
    const cards=data.cards.filter(c=>c.list===l&&(!cur||c.module===cur)&&(!q||(c.id+' '+c.title).toLowerCase().includes(q))).sort((a,b)=>a.pos-b.pos);
    const pts=cards.reduce((s,c)=>s+(PTS[c.size]||0),0), lim=data.wip[l], over=lim&&cards.length>lim;
    const col=document.createElement('section'); col.className='col'; col.dataset.list=l;
    col.innerHTML=`<h2><span>${l} <span class="${over?'over':'n'}">${cards.length}${lim?' / '+lim:''}</span></span><span class="n">${pts} pts</span></h2><div class="cards"></div>`;
    const box=col.querySelector('.cards');
    if(l==='Done'&&!showDone){ if(cards.length) box.innerHTML='<div class="hidden-note">Hidden. Tick “Show Done”, or drop cards here.</div>'; }
    else cards.forEach(c=>box.appendChild(cardEl(c)));
    col.addEventListener('dragover',e=>{e.preventDefault();col.classList.add('drop')});
    col.addEventListener('dragleave',e=>{if(!col.contains(e.relatedTarget))col.classList.remove('drop')});
    col.addEventListener('drop',async e=>{
      e.preventDefault(); col.classList.remove('drop'); const id=dragId; dragId=null; if(!id) return;
      const els=[...box.querySelectorAll('.card:not(.dragging)')]; let before=null;
      for(const el of els){const r=el.getBoundingClientRect(); if(e.clientY<r.top+r.height/2){before=el.dataset.id;break}}
      try{await api('/api/move',{id,list:l,before})}catch(err){showErr(err.message)} load();});
    b.appendChild(col);
  }
}
function cardEl(c){
  const d=document.createElement('div'); d.className='card '+c.priority; d.draggable=true; d.dataset.id=c.id;
  const waits=c.deps.filter(x=>!x.done).map(x=>x.id);
  d.innerHTML=`<div class="id"><span>${esc(c.id)} · ${esc(c.module)}</span><span>${c.days}d</span></div>
  <div class="t">${esc(c.title)}</div><div class="tags"><span class="tag">${c.type}</span><span class="tag">${c.area}</span>
  <span class="tag">${c.priority}</span>${c.size?`<span class="tag">${c.size}</span>`:'<span class="tag blocked">no size</span>'}
  ${c.ac[1]?`<span class="tag ${c.ac[0]===c.ac[1]?'full':''}">AC ${c.ac[0]}/${c.ac[1]}</span>`:'<span class="tag blocked">no AC</span>'}
  ${waits.length?`<span class="tag blocked">waits ${esc(waits.join(', '))}</span>`:''}${c.pr?'<span class="tag">PR</span>':''}</div>`;
  d.addEventListener('dragstart',e=>{dragId=c.id;d.classList.add('dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',c.id)});
  d.addEventListener('dragend',()=>{dragId=null;d.classList.remove('dragging')});
  d.addEventListener('click',()=>openCard(c.id)); return d;}
async function openCard(id){
  let c; try{c=await api('/api/card?id='+encodeURIComponent(id))}catch(e){return showErr(e.message)}
  const items=k=>c.items.filter(i=>i.kind===k).map(i=>`<label class="item"><input type="checkbox" data-item="${i.id}" ${i.checked?'checked':''}><span>${k.toUpperCase()}${i.pos} ${esc(i.text)}${i.note?` <small>— ${esc(i.note)}</small>`:''}</span></label>`).join('')||'<small class="meta">None</small>';
  const meta=[c.type,c.area,c.priority,c.size||'no size',c.list,c.branch&&'branch '+c.branch,c.pr&&'PR '+c.pr,c.spec&&'spec '+c.spec].filter(Boolean).map(esc).join(' · ');
  $('#detail').innerHTML=`<h3>${esc(c.full_title)}</h3><div class="meta">${meta}<br>Depends on: ${c.deps.map(d=>esc(d.id+' ('+d.list+')')).join(', ')||'—'} · Blocks: ${esc(c.blocks.join(', '))||'—'}</div>
  <div class="desc">${esc(c.description)||'<i>No description</i>'}</div>
  <h4>Acceptance criteria</h4>${items('ac')}<h4>Definition of done</h4>${items('dod')}
  <h4>Comments</h4><div class="comments">${c.comments.map(x=>`<div><small>${esc(x.created_at.slice(0,16).replace('T',' '))} · ${esc(x.author)}</small><br>${esc(x.body)}</div>`).join('')||'<small class="meta">None yet</small>'}</div>
  <textarea id="newc" rows="2" placeholder="Add a comment"></textarea>
  <div class="row"><button id="close">Close</button><button id="edit">Edit card</button><button class="primary" id="addc">Comment</button></div>
  <h4>Activity</h4><div class="act">${c.activity.map(x=>`<div><small>${esc(x.created_at.slice(0,16).replace('T',' '))}</small> ${esc(x.actor)} ${esc(x.action)} ${esc(x.detail||'')}</div>`).join('')}</div>`;
  $('#detail').querySelectorAll('input[data-item]').forEach(el=>el.addEventListener('change',async()=>{
    try{await api('/api/check',{item:el.dataset.item,checked:el.checked})}catch(e){showErr(e.message)} load();}));
  $('#addc').onclick=async()=>{const t=$('#newc').value.trim(); if(!t) return;
    try{await api('/api/comment',{id:c.id,body:t}); openCard(c.id)}catch(e){showErr(e.message)}};
  $('#close').onclick=closePanel; $('#edit').onclick=()=>editCard(c); $('#panel').classList.add('open');}
const OPTS={type:['Feature','Bug','Chore','Spike'],area:['Backend','Frontend','Infra'],priority:['P1','P2','P3'],size:['','S','M','L']};
function editCard(c){
  const sel=(k,v)=>`<label class="f"><span>${k}</span><select id="e_${k}">${OPTS[k].map(o=>`<option value="${o}" ${o===(v||'')?'selected':''}>${o||'—'}</option>`).join('')}</select></label>`;
  const ac=c.items.filter(i=>i.kind==='ac').map(i=>i.text).join('\n');
  $('#detail').innerHTML=`<h3>Edit ${esc(c.id)}</h3>
  <label class="f wide"><span>Title</span><input id="e_title" value="${esc(c.title)}"></label>
  <div class="fields">${sel('type',c.type)}${sel('area',c.area)}${sel('priority',c.priority)}${sel('size',c.size)}
  <label class="f"><span>Depends on</span><input id="e_depends" value="${esc(c.deps.map(d=>d.id).join(', '))}" placeholder="INV-011, INV-012"></label></div>
  <label class="f wide"><span>Description (markdown)</span><textarea id="e_desc" rows="12">${esc(c.description)}</textarea></label>
  <label class="f wide"><span>Acceptance criteria, one per line (unchanged lines keep their checkmark)</span><textarea id="e_ac" rows="7">${esc(ac)}</textarea></label>
  <div class="row"><button id="cancel">Cancel</button><button class="primary" id="save">Save</button></div>`;
  $('#cancel').onclick=()=>openCard(c.id);
  $('#save').onclick=async()=>{
    const body={id:c.id,title:$('#e_title').value,depends:$('#e_depends').value,description:$('#e_desc').value,
      ac:$('#e_ac').value.split('\n').map(s=>s.replace(/^\s*(\d+[.)]|[-*])\s+/,'').trim()).filter(Boolean)};
    for(const k of ['type','area','priority','size']) body[k]=$('#e_'+k).value;
    try{await api('/api/edit',body); load(); openCard(c.id)}catch(e){showErr(e.message)}};}
function closePanel(){$('#panel').classList.remove('open')}
$('#panel').addEventListener('click',e=>{if(e.target.id==='panel')closePanel()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closePanel()});
['#module','#q','#showDone'].forEach(s=>$(s).addEventListener('input',render));
load(); setInterval(()=>{if(!document.hidden)load()},4000);
</script></body></html>"""


# ---------------------------------------------------------------- CLI

def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--user", action="store_true", help=argparse.SUPPRESS)

    p = argparse.ArgumentParser(prog=CLI, description="Local kanban board (SQLite).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, fn, help_text):
        s = sub.add_parser(name, parents=[common], help=help_text)
        s.set_defaults(fn=fn)
        return s

    cmd("init", cmd_init, "create the database")

    s = cmd("module", cmd_module, "list or add modules and their ID prefixes")
    s.add_argument("action", choices=["ls", "add"], nargs="?", default="ls")
    s.add_argument("key", nargs="?")
    s.add_argument("title", nargs="?")
    s.add_argument("prefix", nargs="?")

    s = cmd("next", cmd_next, "next free ID(s) for a prefix")
    s.add_argument("prefix")
    s.add_argument("--count", type=int, default=1)

    s = cmd("import", cmd_import, "create cards from a ticket-format markdown file ('-' = stdin)")
    s.add_argument("file")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--update", action="store_true", help="update existing cards (every ticket needs an id)")

    s = cmd("ls", cmd_ls, "one line per card, grouped by list")
    s.add_argument("--list")
    s.add_argument("--module", help="module key or prefix")
    s.add_argument("--done", action="store_true", help="include Done")

    s = cmd("show", cmd_show, "card detail")
    s.add_argument("id")
    s.add_argument("--brief", action="store_true", help="skip description and comments")
    s.add_argument("--ticket", action="store_true", help="print in editable ticket format")

    s = cmd("title", cmd_title, "full card title")
    s.add_argument("id")

    s = cmd("move", cmd_move, "move a card to a list")
    s.add_argument("id")
    s.add_argument("list")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--top", action="store_true")
    g.add_argument("--before", metavar="ID")
    s.add_argument("--allow-wip", action="store_true", help="exceed the WIP limit (user agreed)")
    s.add_argument("--force", action="store_true", help="skip the readiness check (user agreed)")

    s = cmd("edit", cmd_edit, "change card fields")
    s.add_argument("id")
    for f in ("title", "type", "area", "priority", "size", "spec", "branch", "pr", "depends"):
        s.add_argument("--" + f)
    s.add_argument("--desc-file", help="replace the description from a file ('-' = stdin)")

    s = cmd("check", lambda a: cmd_check(a, True), "check off AC/DoD items, e.g. AC1 AC2 DOD1")
    s.add_argument("id")
    s.add_argument("items", nargs="+")
    s.add_argument("--note")

    s = cmd("uncheck", lambda a: cmd_check(a, False), "uncheck AC/DoD items")
    s.add_argument("id")
    s.add_argument("items", nargs="+")
    s.set_defaults(note=None)

    s = cmd("comment", cmd_comment, "add a comment ('-' = stdin)")
    s.add_argument("id")
    s.add_argument("text")

    s = cmd("archive", cmd_archive, "archive a card (user only)")
    s.add_argument("id")

    s = cmd("cycle", cmd_cycle, "cycles: show | new | close | ls")
    s.add_argument("action", choices=["show", "new", "close", "ls"], nargs="?", default="show")
    s.add_argument("--name")
    s.add_argument("--days", type=int, default=14)
    s.add_argument("--start", help="YYYY-MM-DD, default today")
    s.add_argument("--capacity", type=int)
    s.add_argument("--goal")

    s = cmd("status", cmd_status, "board report with problems")
    s.add_argument("--prs", action="store_true", help="ask gh which Testing PRs are merged")

    cmd("brief", cmd_brief, "tiny summary (SessionStart hook)")

    s = cmd("log", cmd_log, "recent activity")
    s.add_argument("id", nargs="?")
    s.add_argument("--limit", type=int, default=20)

    cmd("backup", cmd_backup, "copy the database to .board/backups")

    s = cmd("serve", cmd_serve, "kanban web UI on localhost")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--open", action="store_true")

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except BoardError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
