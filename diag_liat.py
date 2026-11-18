#!/usr/bin/env python3
"""
diag_liat.py — investigate the two cases Liat raised (24 Aug 2026).

  Case 1: "MS heb. c.10/3" returns TWO ALMA numbers, one of them empty.
  Case 2: "JTS: ENA 1205" missing entirely; FGP has ENA 1205.1
          (current shelfmark MS L825, fol. 1).

Prints, for every candidate row: which ingester put it there (source),
whether it has any descriptive content, and what scans/images/joins hang
off it — which is what tells us whether it's an NLI duplicate, a
Rosetta-only stub, or a genuine coverage gap.

Usage (PowerShell):
    python diag_liat.py midrash_core.db
"""
import sys, sqlite3, re, unicodedata

DB = sys.argv[1] if len(sys.argv) > 1 else "midrash_core.db"

def norm(s):
    s = (s or '').lower()
    return ''.join(ch for ch in s
                   if not (ch.isspace() or unicodedata.category(ch).startswith('P')))

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
c = con.cursor()

DESCRIPTIVE = ('title', 'date', 'language', 'genre', 'subject', 'extent',
               'dimensions', 'material_notes', 'script', 'persons', 'provenance')


def show(alma):
    m = c.execute("SELECT * FROM manuscripts WHERE alma=?", (alma,)).fetchone()
    if not m:
        print(f"    {alma}: NOT IN manuscripts"); return
    d = dict(m)
    filled = [k for k in DESCRIPTIVE if (d.get(k) or '').strip()]
    n_sc = c.execute("SELECT COUNT(*) FROM scans WHERE alma=?", (alma,)).fetchone()[0]
    n_im = c.execute("SELECT COUNT(*) FROM images WHERE alma=?", (alma,)).fetchone()[0]
    n_jn = c.execute("SELECT COUNT(DISTINCT join_id) FROM join_members WHERE alma=?",
                     (alma,)).fetchone()[0]
    print(f"    ALMA {alma}")
    print(f"      source={d.get('source')!r}  is_genizah={d.get('is_genizah')!r}  "
          f"fgp_inventory={d.get('fgp_inventory')!r}")
    print(f"      library={d.get('library')!r}  shelfmark={d.get('shelfmark')!r}")
    print(f"      title={(d.get('title') or '')[:70]!r}")
    print(f"      descriptive fields filled: {filled or 'NONE — empty stub'}")
    print(f"      scans={n_sc}  images={n_im}  joins={n_jn}")
    print()


def by_shelf(label, needle):
    """Same match the live search does: substring on shelfmark_norm."""
    n = norm(needle)
    print(f"\n=== {label}  (normalised: {n!r}) ===")
    rows = c.execute(
        "SELECT alma, shelfmark, library, source FROM manuscripts "
        "WHERE shelfmark_norm LIKE ? ORDER BY library, shelfmark", (f"%{n}%",)).fetchall()
    print(f"  {len(rows)} matching row(s)\n")
    for r in rows:
        show(r["alma"])


# ---------------------------------------------------------------- case 1
by_shelf("CASE 1 — MS heb. c.10/3", "MS heb. c.10/3")
# the parent, to see how the whole c.10 group is structured
print("\n--- context: all shelfmarks starting 'MS heb. c.10' ---")
for r in c.execute(
        "SELECT alma, shelfmark, library, source, is_genizah, "
        "       (title IS NULL OR title='') AS no_title "
        "FROM manuscripts WHERE shelfmark_norm LIKE ? ORDER BY shelfmark",
        (f"%{norm('MS heb. c.10')}%",)).fetchall():
    print(f"  {r['alma']:>20}  {(r['shelfmark'] or ''):<24} "
          f"{(r['library'] or ''):<28} src={r['source']:<10} "
          f"gz={r['is_genizah']} empty_title={r['no_title']}")

# ---------------------------------------------------------------- case 2
by_shelf("CASE 2a — ENA 1205", "ENA 1205")
by_shelf("CASE 2b — MS L825", "MS L825")

print("\n--- context: ENA 1205.x family ---")
for r in c.execute(
        "SELECT alma, shelfmark, library, source, fgp_inventory "
        "FROM manuscripts WHERE shelfmark_norm LIKE ? ORDER BY shelfmark",
        (f"%{norm('ENA 1205')}%",)).fetchall():
    print(f"  {r['alma']:>20}  {(r['shelfmark'] or ''):<24} "
          f"{(r['library'] or ''):<28} src={r['source']:<10} fgp={r['fgp_inventory']}")

print("\n--- is ENA 1205 known to the FGP JOINS layer (even without a manuscript row)? ---")
for r in c.execute(
        "SELECT jm.join_id, jm.alma, jm.inventory FROM join_members jm "
        "WHERE jm.inventory LIKE ? LIMIT 20", ("%1205%",)).fetchall():
    print(f"  join={r['join_id']}  alma={r['alma']}  inventory={r['inventory']}")

# ---------------------------------------------------------------- how big is the stub problem
print("\n=== HOW COMMON ARE EMPTY STUBS? (bears on Liat's general complaint) ===")
for r in c.execute("""
        SELECT source,
               COUNT(*) AS n,
               SUM(CASE WHEN title IS NULL OR title='' THEN 1 ELSE 0 END) AS no_title,
               SUM(CASE WHEN shelfmark IS NULL OR shelfmark='' THEN 1 ELSE 0 END) AS no_shelf
        FROM manuscripts GROUP BY source ORDER BY n DESC"""):
    print(f"  source={r['source']!r:<14} total={r['n']:>7,}  "
          f"no_title={r['no_title']:>7,}  no_shelfmark={r['no_shelf']:>7,}")

print("\n=== HOW MANY SHELFMARKS ARE SHARED BY >1 ALMA? (the duplicate problem) ===")
r = c.execute("""
    SELECT COUNT(*) FROM (
      SELECT shelfmark_norm FROM manuscripts
      WHERE shelfmark_norm IS NOT NULL AND shelfmark_norm!=''
      GROUP BY shelfmark_norm HAVING COUNT(DISTINCT alma) > 1)""").fetchone()[0]
print(f"  {r:,} normalised shelfmarks map to more than one ALMA")

print("\n  top 15 most-duplicated shelfmarks:")
for row in c.execute("""
    SELECT shelfmark_norm, COUNT(DISTINCT alma) n, MIN(shelfmark) example
    FROM manuscripts WHERE shelfmark_norm IS NOT NULL AND shelfmark_norm!=''
    GROUP BY shelfmark_norm HAVING n>1 ORDER BY n DESC LIMIT 15"""):
    print(f"    {row['n']:>5} ALMAs  {row['example']!r}")

con.close()
