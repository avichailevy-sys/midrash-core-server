#!/usr/bin/env python3
"""
wire_shelfmarks.py — make the 916,036 extracted shelfmark variants searchable.

extract_shelfmarks.py pulled every 942 field out of the full Ktiv export into
shelfmarks.db: current, alternative, former-collection, cancelled and catalogue
numbers, 916,036 in all, covering 493,547 manuscripts. 323,676 of those carry a
name other than their current one.

None of it is reachable yet: core_search.py only looks at manuscripts.shelfmark,
so a search for a former designation finds nothing. This script:

  1. copies the shelfmarks table into midrash_core.db (and indexes it)
  2. patches core_search.py so the main search box also matches variants
  3. patches the detail panel to list a record's other names

Backs core_search.py up first. The patches match exact strings, so if the file
has changed since, it refuses rather than corrupting anything.

    python3 wire_shelfmarks.py              # report what it would do
    python3 wire_shelfmarks.py --apply
"""
import sqlite3, sys, os, shutil, time

DB   = "midrash_core.db"
SRC  = "shelfmarks.db"
APP  = "core_search.py"
APPLY = "--apply" in sys.argv

# ------------------------------------------------------------- 1. the data
con = sqlite3.connect(DB); c = con.cursor()
have = c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                 "AND name='shelfmarks'").fetchone() is not None
print("shelfmarks table already in %s : %s" % (DB, have))

if not have:
    if not os.path.exists(SRC):
        print("  %s not found — run extract_shelfmarks.py first" % SRC); sys.exit(1)
    n = sqlite3.connect(SRC).execute("SELECT COUNT(*) FROM shelfmarks").fetchone()[0]
    print("  %s holds %s shelfmarks, ready to copy" % (SRC, format(n, ",")))
    if APPLY:
        t0 = time.time()
        c.execute("ATTACH DATABASE ? AS sh", (SRC,))
        c.execute("""CREATE TABLE shelfmarks AS SELECT * FROM sh.shelfmarks""")
        c.execute("CREATE INDEX ix_shv_alma ON shelfmarks(alma)")
        c.execute("CREATE INDEX ix_shv_norm ON shelfmarks(value_norm)")
        con.commit()
        c.execute("DETACH DATABASE sh")
        print("  copied and indexed in %.0fs" % (time.time() - t0))

if c.execute("SELECT name FROM sqlite_master WHERE type='table' "
             "AND name='shelfmarks'").fetchone():
    print("\n  variants now in the main database:")
    for r in c.execute("SELECT kind, COUNT(*) FROM shelfmarks GROUP BY kind ORDER BY 2 DESC"):
        print("    %-22s %s" % (r[0], format(r[1], ",")))
con.close()

# ---------------------------------------------------------- 2. the search
SEARCH_OLD = """                    "SELECT alma FROM scans WHERE ie=? COLLATE NOCASE"):
            almas.update(r[0] for r in c.execute(sql, (term,)))"""

SEARCH_NEW = """                    "SELECT alma FROM scans WHERE ie=? COLLATE NOCASE"):
            almas.update(r[0] for r in c.execute(sql, (term,)))
        # former / alternative / cancelled shelfmarks, from MARC 942
        nt_sh = norm(term)
        if nt_sh:
            try:
                almas.update(r[0] for r in c.execute(
                    "SELECT DISTINCT alma FROM shelfmarks WHERE value_norm LIKE ? LIMIT 400",
                    ("%" + nt_sh + "%",)))
            except Exception:
                pass"""

DETAIL_OLD = """    d["scans"] = [dict(r) for r in c.execute("SELECT ie,scan_type,image_count FROM scans WHERE alma=?", (alma,))]"""

DETAIL_NEW = """    try:
        d["variants"] = [dict(r) for r in c.execute(
            "SELECT kind,value,owner FROM shelfmarks WHERE alma=? ORDER BY ind", (alma,))]
    except Exception:
        d["variants"] = []
    d["scans"] = [dict(r) for r in c.execute("SELECT ie,scan_type,image_count FROM scans WHERE alma=?", (alma,))]"""

UI_OLD = """  ${kv('ALMA',d.alma)}${kv('FGP number',d.fgp_inventory)}"""

UI_NEW = """  ${kv('ALMA',d.alma)}${kv('FGP number',d.fgp_inventory)}
  ${(d.variants&&d.variants.length>1)?'<div class="k">Also known as</div>'+d.variants.filter(function(v){return v.kind!=='current';}).map(function(v){return '<div class="kv"><b>'+esc(v.value)+'</b> <span class=muted>'+esc(v.kind.replace(/_/g,' '))+(v.owner?' \\u00b7 '+esc(v.owner):'')+'</span></div>';}).join(''):''}"""

src = open(APP, encoding="utf-8").read()
# each edit carries a MARKER unique to the patched version, so "already done"
# is detected reliably (the old anchor may still legitimately appear afterwards)
edits = [("search lookup", SEARCH_OLD, SEARCH_NEW,
          "FROM shelfmarks WHERE value_norm LIKE ?"),
         ("detail query",  DETAIL_OLD,  DETAIL_NEW,
          'd["variants"] = [dict(r) for r in c.execute('),
         ("detail panel",  UI_OLD,      UI_NEW,
          "Also known as")]

print("\npatching %s" % APP)
ok = True
todo = []
for name, old, new, marker in edits:
    if marker in src:
        print("  %-14s already applied — skipping" % name); continue
    n = src.count(old)
    if n == 1:
        print("  %-14s ready" % name); todo.append((old, new))
    else:
        print("  %-14s NOT FOUND (%d matches) — file has changed" % (name, n)); ok = False

if not ok:
    print("\nrefusing to patch; show me the file and I'll adjust"); sys.exit(1)

if APPLY:
    if todo:
        shutil.copy(APP, APP + ".bak-" + time.strftime("%Y%m%d-%H%M"))
        for old, new in todo:
            src = src.replace(old, new, 1)
        open(APP, "w", encoding="utf-8").write(src)
        print("\n  %d edit(s) written (backup kept)" % len(todo))
    else:
        print("\n  nothing to patch")
    print("  now:  systemctl restart midrash")
else:
    print("\nreport only — re-run with --apply")
