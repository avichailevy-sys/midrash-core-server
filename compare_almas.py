#!/usr/bin/env python3
"""
compare_almas.py — how does Tsafra's numbers table compare to Core's coverage?

Answers:
  - how many ALMAs each side has
  - how many are in Tsafra's file but MISSING from Core  (would be new manuscripts)
  - how many are in Core but absent from her file        (Core's extra coverage)
  - for the missing ones: are they the kind of thing Core should ingest?
  - for the overlap: how many of Core's currently-imageless records does
    her file cover (i.e. how many could gain images from it)

Read-only.

Usage:
    python3 compare_almas.py tsafra_almas.txt
"""
import sqlite3, sys

DB   = "midrash_core.db"
PATH = sys.argv[1] if len(sys.argv) > 1 else "tsafra_almas.txt"

tsafra = set()
bad = 0
for line in open(PATH, encoding="utf-8-sig"):
    a = line.strip()
    if not a:
        continue
    if len(a) != 18 or not a.isdigit():
        bad += 1
        continue
    tsafra.add(a)

con = sqlite3.connect(DB)
c = con.cursor()
core = set(r[0] for r in c.execute("SELECT alma FROM manuscripts"))

print("=" * 64)
print("ALMA COVERAGE: Tsafra's numbers table vs Core")
print("=" * 64)
print("  Tsafra file : %8s  (%d malformed lines skipped)" % (format(len(tsafra), ","), bad))
print("  Core        : %8s" % format(len(core), ","))
print()

both      = tsafra & core
only_tsaf = tsafra - core
only_core = core - tsafra

print("  in both              : %8s" % format(len(both), ","))
print("  ONLY in Tsafra's file: %8s   <- new manuscripts Core doesn't have"
      % format(len(only_tsaf), ","))
print("  only in Core         : %8s" % format(len(only_core), ","))
print()

if only_tsaf:
    print("  sample of ALMAs Core is missing:")
    for a in sorted(only_tsaf)[:10]:
        print("    ", a)
    print()

# how much of Core's image gap does her file address?
imageless = set(r[0] for r in c.execute(
    "SELECT alma FROM manuscripts WHERE alma NOT IN (SELECT DISTINCT alma FROM images)"))
print("  Core records with NO images  : %8s" % format(len(imageless), ","))
print("  ...of which Tsafra's file has: %8s   (%.1f%%)"
      % (format(len(imageless & tsafra), ","),
         100.0 * len(imageless & tsafra) / len(imageless) if imageless else 0))
print()

# and by source layer, so we can see which part of Core benefits
print("  imageless records covered by her file, by source layer:")
for src, in c.execute("SELECT DISTINCT source FROM manuscripts"):
    rows = set(r[0] for r in c.execute(
        "SELECT alma FROM manuscripts WHERE source=? "
        "AND alma NOT IN (SELECT DISTINCT alma FROM images)", (src,)))
    if not rows:
        continue
    hit = len(rows & tsafra)
    print("    %-11s %8s imageless, %8s in her file (%.1f%%)"
          % (src, format(len(rows), ","), format(hit, ","),
             100.0 * hit / len(rows)))

con.close()
