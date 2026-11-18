#!/usr/bin/env python3
"""
test_consecutive.py — does "first FL, then +1 per page" actually hold?

Omer's proposal: take the correct FL for page 1 from the manifest, then
assume page 2 is +1, page 3 is +2, and so on. That would remove the need to
fetch a manifest for every manuscript.

Core has 11.1 million harvested manifest pages, so this can be settled by
measurement rather than argument. For every scanning (IE) with at least two
pages, this checks two things:

  contiguous : the FL numbers form an unbroken block (max - min == n - 1)
  in order   : they ascend by exactly 1 in canvas order

Omer's rule needs BOTH. Anything else and the pages come out scrambled or
pointing at the wrong images.

Read-only. Takes a few minutes over 11M rows.

    python3 test_consecutive.py
"""
import sqlite3, time

con = sqlite3.connect("midrash_core.db")
c = con.cursor()
t0 = time.time()

print("scanning harvested manifests ...\n")

c.execute("""
CREATE TEMP TABLE per_ie AS
SELECT ie,
       COUNT(*)                                   AS n_pages,
       MAX(flnum) - MIN(flnum)                    AS span,
       SUM(CASE WHEN d IS NOT NULL AND d <> 1 THEN 1 ELSE 0 END) AS breaks,
       SUM(CASE WHEN d IS NOT NULL AND d < 0 THEN 1 ELSE 0 END)  AS backwards
FROM (
  SELECT ie,
         CAST(SUBSTR(fl, 3) AS INTEGER) AS flnum,
         CAST(SUBSTR(fl, 3) AS INTEGER)
           - LAG(CAST(SUBSTR(fl, 3) AS INTEGER))
             OVER (PARTITION BY ie ORDER BY canvas_no) AS d
  FROM manifest_images
  WHERE fl IS NOT NULL AND fl LIKE 'FL%'
)
GROUP BY ie
HAVING n_pages > 1
""")

tot = c.execute("SELECT COUNT(*) FROM per_ie").fetchone()[0]
perfect = c.execute("SELECT COUNT(*) FROM per_ie WHERE breaks = 0").fetchone()[0]
contig = c.execute("SELECT COUNT(*) FROM per_ie WHERE span = n_pages - 1").fetchone()[0]
backw = c.execute("SELECT COUNT(*) FROM per_ie WHERE backwards > 0").fetchone()[0]
pages = c.execute("SELECT SUM(n_pages) FROM per_ie").fetchone()[0]

def pct(x):
    return 100.0 * x / tot if tot else 0

print("=" * 64)
print("DOES '+1 PER PAGE' HOLD?")
print("=" * 64)
print("  scannings tested        : %s   (%s pages)" % (format(tot, ","), format(pages, ",")))
print()
print("  +1 every page, in order : %s  (%.1f%%)   <- Omer's rule works here"
      % (format(perfect, ","), pct(perfect)))
print("  NOT +1 somewhere        : %s  (%.1f%%)   <- rule gives wrong images"
      % (format(tot - perfect, ","), pct(tot - perfect)))
print()
print("  of those that fail:")
print("    FL numbers go BACKWARDS at some point : %s  (%.1f%%)"
      % (format(backw, ","), pct(backw)))
print("    numbers form an unbroken block anyway : %s  (%.1f%%)"
      % (format(contig, ","), pct(contig)))

print("\n  how many pages would be wrong, if the rule were applied everywhere?")
bad_pages = c.execute("SELECT SUM(n_pages) FROM per_ie WHERE breaks > 0").fetchone()[0] or 0
print("    %s pages sit in a scanning where the rule breaks  (%.1f%% of all harvested pages)"
      % (format(bad_pages, ","), 100.0 * bad_pages / pages if pages else 0))

print("\n  size matters? (rule holding, by number of pages in the scanning)")
for lo, hi, lbl in ((2, 3, "2-3 pages"), (4, 10, "4-10"), (11, 50, "11-50"),
                    (51, 200, "51-200"), (201, 99999, "over 200")):
    r = c.execute("""SELECT COUNT(*), SUM(CASE WHEN breaks=0 THEN 1 ELSE 0 END)
                     FROM per_ie WHERE n_pages BETWEEN ? AND ?""", (lo, hi)).fetchone()
    if r[0]:
        print("    %-12s %8s scannings, rule holds in %5.1f%%"
              % (lbl, format(r[0], ","), 100.0 * (r[1] or 0) / r[0]))

print("\n  examples where it fails:")
for r in c.execute("""SELECT ie, n_pages, breaks, backwards FROM per_ie
                      WHERE breaks > 0 AND n_pages BETWEEN 4 AND 12 LIMIT 4"""):
    print("\n    %s — %d pages, %d breaks%s"
          % (r[0], r[1], r[2], ", goes backwards" if r[3] else ""))
    for p in c.execute("""SELECT canvas_no, fl, label FROM manifest_images
                          WHERE ie=? ORDER BY canvas_no LIMIT 6""", (r[0],)):
        print("      page %-3s %-14s %s" % (p[0], p[1], (p[2] or "")[:24]))

print("\n(%.0f seconds)" % (time.time() - t0))
con.close()
