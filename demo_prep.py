#!/usr/bin/env python3
"""
demo_prep.py — material for showing MiDRASH Core at the NLI.

Part A: six search terms, each verified against the live database, chosen to
        show a different capability. Returns the exact term to type and what
        should come back.

Part B: the FL problem, side by side. For a handful of manuscripts it prints
        what NLI's own MARC field 907 says the image is, against what the IIIF
        manifest actually serves — and marks which of the two displays.

        907 comes from ALL_NLI_MSS_24082026 (the full Ktiv export, ingested
        31 Oct). Tsafra: "907 in Alma holds only the first FL per IE. Alma is
        not the source for FL numbers, it is a field for internal use."

Read-only.

    python3 demo_prep.py
"""
import sqlite3

DB = "midrash_core.db"
IIIF = "https://iiif.nli.org.il/IIIFv21/%s/full/max/0/default.jpg"

con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
c = con.cursor()

def rule(t):
    print("\n" + "=" * 70); print(t); print("=" * 70)

rule("PART A — SEARCH EXAMPLES")

# 1. a former shelfmark: the headline capability
print("\n1. SEARCH BY A NAME THE MANUSCRIPT NO LONGER HAS")
print("   Nothing else can do this. The former names come from MARC 942,")
print("   which the catalogue carries but no interface exposes.\n")
for r in c.execute("""SELECT s.value, s.owner, m.shelfmark, m.library, m.alma
    FROM shelfmarks s JOIN manuscripts m ON m.alma = s.alma
    WHERE s.kind='former_collection' AND LENGTH(s.value) BETWEEN 7 AND 18
      AND m.library LIKE '%National Library%'
    GROUP BY s.value HAVING COUNT(*)=1 LIMIT 4"""):
    n = c.execute("SELECT COUNT(*) FROM shelfmarks WHERE value=?", (r["value"],)).fetchone()[0]
    if n == 1:
        print("   type:  %-20s -> %s" % (r["value"], r["shelfmark"]))
        print("          (was %s)" % r["owner"])

# 2. a cancelled number
print("\n2. SEARCH BY A CANCELLED NUMBER")
print("   Numbers the library itself withdrew — 22,967 of them.\n")
for r in c.execute("""SELECT s.value, s.owner, m.shelfmark FROM shelfmarks s
    JOIN manuscripts m ON m.alma = s.alma
    WHERE s.kind='cancelled' AND LENGTH(s.value) BETWEEN 7 AND 18 LIMIT 3"""):
    print("   type:  %-20s -> %s" % (r["value"], r["shelfmark"]))

# 3. identifiers
print("\n3. SEARCH BY ANY IDENTIFIER — ALMA, IE, FL, FGP")
r = c.execute("""SELECT m.alma, m.shelfmark, m.fgp_inventory, s.ie, i.fl
    FROM manuscripts m JOIN scans s ON s.alma=m.alma JOIN images i ON i.alma=m.alma
    WHERE m.fgp_inventory IS NOT NULL AND m.fgp_inventory!='' LIMIT 1""").fetchone()
if r:
    print("   same manuscript (%s), four ways in:" % r["shelfmark"])
    for lbl, v in (("ALMA", r["alma"]), ("IE", r["ie"]),
                   ("FL", r["fl"]), ("FGP", r["fgp_inventory"])):
        print("     %-5s %s" % (lbl, v))

# 4. joins
print("\n4. FRAGMENTS THAT JOIN")
r = c.execute("""SELECT m.shelfmark, m.alma, m.n_joins FROM manuscripts m
    WHERE m.n_joins >= 2 LIMIT 1""").fetchone()
if r:
    print("   type:  %-20s -> in %d join groups (Friedberg assertions)"
          % (r["shelfmark"], r["n_joins"]))

# 5. Hebrew title
print("\n5. SEARCH IN HEBREW")
for r in c.execute("""SELECT title, COUNT(*) n FROM manuscripts
    WHERE title LIKE '%מדרש%' GROUP BY title ORDER BY n DESC LIMIT 2"""):
    print("   type:  מדרש  -> %s results include %s"
          % (format(c.execute("SELECT COUNT(*) FROM manuscripts WHERE title LIKE '%מדרש%'").fetchone()[0], ","),
             (r["title"] or "")[:40]))
    break

# 6. provenance chain
print("\n6. A FULL PROVENANCE CHAIN ON ONE RECORD")
r = c.execute("""SELECT alma, COUNT(*) k FROM shelfmarks
    GROUP BY alma HAVING k>=3 LIMIT 1""").fetchone()
if r:
    m = c.execute("SELECT shelfmark,title FROM manuscripts WHERE alma=?",
                  (r["alma"],)).fetchone()
    print("   search %s, then open the record:" % (m["shelfmark"] if m else r["alma"]))
    for s in c.execute("SELECT kind,value,owner FROM shelfmarks WHERE alma=? ORDER BY ind",
                       (r["alma"],)):
        print("     %-20s %-26s %s" % (s["kind"], (s["value"] or "")[:26], (s["owner"] or "")[:30]))

rule("PART B — THE FL PROBLEM, SIDE BY SIDE")
print("""
For each manuscript below:
  907 FL  — what NLI's own catalogue export gives as the image
  manifest FL — what the IIIF service actually serves for that same scanning

Where they differ, Core shows "image restricted by the NLI" — but the image
is not restricted. The number is simply not one IIIF will serve, and no
export NLI publishes contains the one that works.
""")

shown = 0
for r in c.execute("""SELECT DISTINCT i.alma, i.ie, i.fl AS fl907, m.shelfmark
    FROM images i JOIN manuscripts m ON m.alma=i.alma
    WHERE i.alma IN (SELECT DISTINCT alma FROM manifest_images) LIMIT 400"""):
    mf = c.execute("""SELECT fl, label FROM manifest_images
        WHERE alma=? AND ie=? ORDER BY canvas_no LIMIT 1""",
        (r["alma"], r["ie"])).fetchone()
    if not mf:
        continue
    match = (mf["fl"] == r["fl907"])
    if match and shown >= 2:
        continue
    print("  %s   (%s)" % (r["shelfmark"], r["alma"]))
    print("    IE           %s" % r["ie"])
    print("    907 says     %-16s %s" % (r["fl907"], "" if match else "<-- Core shows this"))
    print("    manifest has %-16s %s" % (mf["fl"], "(%s)" % (mf["label"] or "")))
    print("    %s" % ("SAME — image displays" if match else "DIFFERENT — Core's image fails"))
    if not match:
        print("    working url: " + IIIF % mf["fl"])
    print()
    shown += 1
    if shown >= 5:
        break

n_both = c.execute("""SELECT COUNT(DISTINCT alma) FROM images
    WHERE alma IN (SELECT alma FROM manifest_images)""").fetchone()[0]
print("  scale: %s manuscripts have both a 907 image and harvested manifest pages"
      % format(n_both, ","))
print("         harvest so far: %s manuscripts, %s pages"
      % (format(c.execute("SELECT COUNT(DISTINCT alma) FROM manifest_images").fetchone()[0], ","),
         format(c.execute("SELECT COUNT(*) FROM manifest_images").fetchone()[0], ",")))
print("         that is one HTTP request per manuscript; %s manuscripts remain"
      % format(c.execute("SELECT COUNT(*) FROM manuscripts WHERE alma NOT IN "
                         "(SELECT alma FROM harvest_log)").fetchone()[0], ","))

con.close()
