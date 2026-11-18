#!/usr/bin/env python3
"""
ingest_nli_numbers.py — load Tsafra's All_Ktiv_Alma_Rosetta_Fgp_Numbers.txt
into Core as a NEW table, `nli_numbers`.

Deliberately does NOT touch `manuscripts`, `images` or `scans`. The FLs in
this file are the internal Rosetta series, which are NOT servable over IIIF
(verified: manifest FLs sit a few numbers higher). So this data is stored
alongside Core's own, not merged into it. Once the manifest harvest is done,
the two can be joined on (ie, page order) to derive the internal <-> servable
crosswalk.

Columns in the source file (tab-separated, UTF-8 with BOM):
    mms_id  ie  fl_name  file_name_org  letter  fgp_number  Lable_name
    src_name  img_name  rosetta_date  InventoryID  SiteTypeCode
    ImageOrder  FullShelfmark

The literal string "NULL" appears for missing values and is stored as NULL.

Usage:
    python3 ingest_nli_numbers.py nli_numbers.txt.gz
    python3 ingest_nli_numbers.py All_Ktiv_Alma_Rosetta_Fgp_Numbers.txt
"""
import sqlite3, sys, os, gzip, time

DB    = "midrash_core.db"
PATH  = sys.argv[1] if len(sys.argv) > 1 else "nli_numbers.txt.gz"
BATCH = 50000

COLS = ["mms_id", "ie", "fl_name", "file_name_org", "letter", "fgp_number",
        "lable_name", "src_name", "img_name", "rosetta_date", "inventory_id",
        "site_type_code", "image_order", "full_shelfmark"]


def opener(p):
    return gzip.open(p, "rt", encoding="utf-8-sig", errors="replace") \
        if p.endswith(".gz") else open(p, "r", encoding="utf-8-sig", errors="replace")


def clean(v):
    v = (v or "").strip()
    return None if v == "" or v.upper() == "NULL" else v


con = sqlite3.connect(DB)
c = con.cursor()

if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nli_numbers'").fetchone():
    print("Table nli_numbers already exists.")
    print("Drop it first if you want to reload:  DROP TABLE nli_numbers;")
    sys.exit(1)

print("creating table nli_numbers ...")
c.execute("CREATE TABLE nli_numbers (%s)" % ", ".join("%s TEXT" % x for x in COLS))

# speed settings for a large bulk load
c.execute("PRAGMA journal_mode=OFF")
c.execute("PRAGMA synchronous=OFF")
c.execute("PRAGMA cache_size=-200000")

sql = "INSERT INTO nli_numbers VALUES (%s)" % ",".join("?" * len(COLS))

n = skipped = 0
batch = []
t0 = time.time()

with opener(PATH) as fh:
    header = fh.readline()          # discard header row
    hdr_cols = header.rstrip("\n").split("\t")
    if len(hdr_cols) != len(COLS):
        print("WARNING: header has %d columns, expected %d" % (len(hdr_cols), len(COLS)))
        print("  header:", hdr_cols)

    for line in fh:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < len(COLS):
            parts += [""] * (len(COLS) - len(parts))
        elif len(parts) > len(COLS):
            skipped += 1
            continue
        batch.append(tuple(clean(x) for x in parts))
        n += 1
        if len(batch) >= BATCH:
            c.executemany(sql, batch); con.commit(); batch = []
            el = time.time() - t0
            print("  %s rows  (%.0f rows/sec)" % (format(n, ","), n / el if el else 0))

if batch:
    c.executemany(sql, batch); con.commit()

print("loaded %s rows (%s malformed lines skipped) in %.1f min"
      % (format(n, ","), format(skipped, ","), (time.time() - t0) / 60))

print("\nbuilding indexes (this takes a few minutes) ...")
for name, col in (("ix_nn_alma", "mms_id"), ("ix_nn_ie", "ie"),
                  ("ix_nn_fl", "fl_name"), ("ix_nn_inv", "inventory_id")):
    t = time.time()
    c.execute("CREATE INDEX %s ON nli_numbers(%s)" % (name, col))
    print("  %s  (%.0fs)" % (name, time.time() - t))
con.commit()

# ------------------------------------------------------------------ report
print("\n" + "=" * 60)
print("WHAT LANDED")
print("=" * 60)
for label, q in (
    ("rows",                 "SELECT COUNT(*) FROM nli_numbers"),
    ("distinct ALMAs",       "SELECT COUNT(DISTINCT mms_id) FROM nli_numbers"),
    ("distinct IEs",         "SELECT COUNT(DISTINCT ie) FROM nli_numbers"),
    ("distinct FLs",         "SELECT COUNT(DISTINCT fl_name) FROM nli_numbers"),
    ("rows with an FGP inv", "SELECT COUNT(*) FROM nli_numbers WHERE inventory_id IS NOT NULL"),
    ("rows with a label",    "SELECT COUNT(*) FROM nli_numbers WHERE lable_name IS NOT NULL"),
):
    print("  %-22s %s" % (label, format(c.execute(q).fetchone()[0], ",")))

print("\n  ALMAs here that Core does NOT have:")
print("    %s" % format(c.execute(
    "SELECT COUNT(DISTINCT mms_id) FROM nli_numbers "
    "WHERE mms_id NOT IN (SELECT alma FROM manuscripts)").fetchone()[0], ","))

print("\n  Core records with no images, that DO appear here:")
print("    %s" % format(c.execute(
    "SELECT COUNT(DISTINCT m.alma) FROM manuscripts m JOIN nli_numbers n ON n.mms_id=m.alma "
    "WHERE m.alma NOT IN (SELECT DISTINCT alma FROM images)").fetchone()[0], ","))

print("\n  src_name values (which NLI system the row came from):")
for r in c.execute("SELECT src_name, COUNT(*) FROM nli_numbers GROUP BY src_name ORDER BY 2 DESC LIMIT 8"):
    print("    %-16s %s" % (r[0], format(r[1], ",")))

print("\n  img_name values:")
for r in c.execute("SELECT img_name, COUNT(*) FROM nli_numbers GROUP BY img_name ORDER BY 2 DESC LIMIT 8"):
    print("    %-16s %s" % (r[0], format(r[1], ",")))

con.execute("PRAGMA journal_mode=DELETE")
con.close()
print("\ndone. db size now: %.0f MB" % (os.path.getsize(DB) / 1e6))
