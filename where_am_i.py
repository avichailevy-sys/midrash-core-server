#!/usr/bin/env python3
"""
where_am_i.py — full picture of MiDRASH Core's current state.

Run this after any gap to see what's in the database, how far the
manifest harvest got, and whether the full Ktiv export was ever loaded.

Read-only. Safe to run while the service is live.

Usage (on the server, in /root/midrash_core):
    python3 where_am_i.py
"""
import sqlite3, os, datetime

DB = "midrash_core.db"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
c = con.cursor()

def q1(sql, *a):
    try:
        return c.execute(sql, a).fetchone()[0]
    except Exception as e:
        return "(%s)" % type(e).__name__

def has(t):
    return c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                     (t,)).fetchone() is not None

print("=" * 66)
print("MiDRASH CORE — current state")
print("  db file : %s" % DB)
print("  size    : %.0f MB" % (os.path.getsize(DB) / 1e6))
print("  modified: %s" % datetime.datetime.fromtimestamp(
      os.path.getmtime(DB)).strftime("%Y-%m-%d %H:%M"))
print("=" * 66)

# ---------------------------------------------------------------- tables
print("\nTABLES")
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    t = r["name"]
    if t.startswith("sqlite_"):
        continue
    print("  %-20s %s rows" % (t, format(q1("SELECT COUNT(*) FROM %s" % t), ",")))

# ------------------------------------------------- manuscripts by source
print("\nMANUSCRIPTS BY INGESTION SOURCE")
print("  (if 'catalogue' is still ~79,537, the FULL Ktiv export has NOT been loaded)")
for r in c.execute("""SELECT source, COUNT(*) n,
    SUM(CASE WHEN alma IN (SELECT DISTINCT alma FROM images) THEN 1 ELSE 0 END) with_img
    FROM manuscripts GROUP BY source ORDER BY n DESC"""):
    print("  %-12s %9s   with images: %9s"
          % (r["source"], format(r["n"], ","), format(r["with_img"], ",")))
print("  %-12s %9s" % ("TOTAL", format(q1("SELECT COUNT(*) FROM manuscripts"), ",")))

# ------------------------------------------------------- the FL harvest
print("\nMANIFEST HARVEST (the servable-FL collection)")
if has("harvest_log"):
    tot = q1("SELECT COUNT(*) FROM harvest_log")
    print("  manuscripts attempted : %s of %s  (%.1f%%)"
          % (format(tot, ","), format(q1("SELECT COUNT(*) FROM manuscripts"), ","),
             100.0 * tot / max(q1("SELECT COUNT(*) FROM manuscripts"), 1)))
    for r in c.execute("SELECT status, COUNT(*) n FROM harvest_log "
                       "GROUP BY status ORDER BY n DESC"):
        print("    %-12s %s" % (r["status"], format(r["n"], ",")))
    print("  pages harvested       : %s" % format(q1("SELECT COUNT(*) FROM manifest_images"), ","))
    print("  manuscripts with pages: %s"
          % format(q1("SELECT COUNT(DISTINCT alma) FROM manifest_images"), ","))
    print("  last fetch            : %s" % q1("SELECT MAX(fetched_at) FROM harvest_log"))
    print("  still to attempt      : %s"
          % format(q1("SELECT COUNT(*) FROM manuscripts WHERE alma NOT IN "
                      "(SELECT alma FROM harvest_log)"), ","))
    # the overnight-404 trap
    print("\n  last 6 hours of activity (watch for all-404 blocks = NLI was down):")
    for r in c.execute("""SELECT substr(fetched_at,1,13) hr, status, COUNT(*) n
        FROM harvest_log WHERE fetched_at >= (SELECT datetime(MAX(fetched_at),'-6 hours')
        FROM harvest_log) GROUP BY hr, status ORDER BY hr DESC, n DESC LIMIT 12"""):
        print("    %s  %-12s %s" % (r["hr"], r["status"], format(r["n"], ",")))
else:
    print("  no harvest_log table — harvest never run")

# ------------------------------------------------------ what's loadable
print("\nSOURCE DATA ALREADY IN THE DB")
if has("nli_numbers"):
    print("  nli_numbers (Tsafra's crosswalk, INTERNAL FLs — not servable):")
    print("    %s rows, %s ALMAs, %s IEs"
          % (format(q1("SELECT COUNT(*) FROM nli_numbers"), ","),
             format(q1("SELECT COUNT(DISTINCT mms_id) FROM nli_numbers"), ","),
             format(q1("SELECT COUNT(DISTINCT ie) FROM nli_numbers"), ",")))
else:
    print("  nli_numbers: NOT loaded")

# --------------------------------------------- fields Liat's cases need
print("\nFIELDS LIAT'S OPEN CASES DEPEND ON")
cols = [r["name"] for r in c.execute("PRAGMA table_info(manuscripts)")]
print("  manuscripts columns: %s" % ", ".join(cols))
if "parent_record" in cols:
    print("  with a parent link  : %s"
          % format(q1("SELECT COUNT(*) FROM manuscripts "
                      "WHERE parent_record IS NOT NULL AND parent_record!=''"), ","))
print("  shelfmark variants table: %s"
      % ("present" if has("shelfmarks") else "NOT built — 942 variants still discarded"))

con.close()
print("\n" + "=" * 66)
