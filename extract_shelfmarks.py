#!/usr/bin/env python3
"""
extract_shelfmarks.py — pull EVERY shelfmark and parent link out of the full
Ktiv MARC export (ALL_NLI_MSS_24082026), into a separate database file.

Why this exists
---------------
build_core_db.py reads field 942 but keeps only ONE per record (the first
with a $z from a real library) and discards the rest. Worse, sub_map() reads
only subfield codes and throws the indicator away — so Core cannot tell a
current shelfmark from a cancelled one even in principle.

The indicator is what carries the meaning (per Tsafra & Chicco's MARC table):
    942 ind1=1  current shelfmark
    942 ind1=2  duplicate copy held in ANOTHER library
    942 ind1=3  catalogue number
    942 ind1=4  alternative number at the current owner (still in use)
    942 ind1=5  former number, previous collection (provenance)
    942 ind1=6  old number at current owner, CANCELLED, no longer in use
    942 ind1=7  someone who manually copied the item

This is what answers Liat's cases: a fragment known as "ENA 1205.1" whose
current shelfmark is "MS L825" has both, in two 942 fields.

Writes to a SEPARATE file (shelfmarks.db) so it can run while the manifest
harvest is writing to midrash_core.db. Merge later.

Usage:
    python3 extract_shelfmarks.py all_nli_mss.xml.gz
    python3 extract_shelfmarks.py ALL_NLI_MSS_24082026
"""
import sys, gzip, sqlite3, time, unicodedata
import xml.etree.ElementTree as ET

SRC = sys.argv[1] if len(sys.argv) > 1 else "all_nli_mss.xml.gz"
OUT = sys.argv[2] if len(sys.argv) > 2 else "shelfmarks.db"

KIND = {"1": "current", "2": "duplicate_elsewhere", "3": "catalogue_number",
        "4": "alternative", "5": "former_collection", "6": "cancelled",
        "7": "manual_copy"}


def norm(s):
    """Same normalisation core_search.py uses, so the two can be compared."""
    s = (s or "").lower()
    return "".join(ch for ch in s
                   if not (ch.isspace() or unicodedata.category(ch).startswith("P")))


def strip_ns(tag):
    return tag.rsplit("}", 1)[-1]


def opener(p):
    return gzip.open(p, "rb") if p.endswith(".gz") else open(p, "rb")


con = sqlite3.connect(OUT)
c = con.cursor()
c.executescript("""
DROP TABLE IF EXISTS shelfmarks;
DROP TABLE IF EXISTS parents;
DROP TABLE IF EXISTS record_titles;
CREATE TABLE shelfmarks(
    alma TEXT, ind TEXT, kind TEXT,
    value TEXT, value_norm TEXT,
    owner TEXT, city TEXT, country TEXT);
CREATE TABLE parents(alma TEXT, parent_alma TEXT);
CREATE TABLE record_titles(alma TEXT PRIMARY KEY, title TEXT);
""")
c.execute("PRAGMA journal_mode=OFF")
c.execute("PRAGMA synchronous=OFF")

n_rec = n_sh = n_par = 0
sh_batch, par_batch, tt_batch = [], [], []
t0 = time.time()

with opener(SRC) as fh:
    for event, el in ET.iterparse(fh, events=("end",)):
        if strip_ns(el.tag) != "record":
            continue
        n_rec += 1

        alma = ""
        title = ""
        for child in el:
            t = strip_ns(child.tag)
            if t == "controlfield" and child.get("tag") == "001":
                alma = (child.text or "").strip()
            elif t == "datafield":
                tag = child.get("tag")
                if tag == "245":
                    for sf in child:
                        if strip_ns(sf.tag) == "subfield" and sf.get("code") == "a":
                            title = (sf.text or "").strip()
                elif tag == "942":
                    subs = {}
                    for sf in child:
                        if strip_ns(sf.tag) == "subfield":
                            subs.setdefault(sf.get("code"), []).append((sf.text or "").strip())
                    val = (subs.get("z") or [""])[0]
                    if not val:
                        continue
                    ind = (child.get("ind1") or "").strip()
                    sh_batch.append((alma, ind, KIND.get(ind, "other_%s" % ind),
                                     val, norm(val),
                                     (subs.get("a") or [""])[0],
                                     (subs.get("x") or [""])[0],
                                     (subs.get("c") or [""])[0]))
                    n_sh += 1
                elif tag == "773":
                    for sf in child:
                        if strip_ns(sf.tag) == "subfield" and sf.get("code") == "w":
                            p = (sf.text or "").strip()
                            if p:
                                par_batch.append((alma, p)); n_par += 1

        if alma and title:
            tt_batch.append((alma, title))

        el.clear()

        if len(sh_batch) >= 50000:
            c.executemany("INSERT INTO shelfmarks VALUES (?,?,?,?,?,?,?,?)", sh_batch)
            c.executemany("INSERT INTO parents VALUES (?,?)", par_batch)
            c.executemany("INSERT OR IGNORE INTO record_titles VALUES (?,?)", tt_batch)
            con.commit(); sh_batch, par_batch, tt_batch = [], [], []
            print("  %s records, %s shelfmarks, %s parent links  (%.0fs)"
                  % (format(n_rec, ","), format(n_sh, ","), format(n_par, ","),
                     time.time() - t0), flush=True)

c.executemany("INSERT INTO shelfmarks VALUES (?,?,?,?,?,?,?,?)", sh_batch)
c.executemany("INSERT INTO parents VALUES (?,?)", par_batch)
c.executemany("INSERT OR IGNORE INTO record_titles VALUES (?,?)", tt_batch)
con.commit()

print("\nbuilding indexes ...")
for ix in ("CREATE INDEX ix_sh_alma ON shelfmarks(alma)",
           "CREATE INDEX ix_sh_norm ON shelfmarks(value_norm)",
           "CREATE INDEX ix_sh_kind ON shelfmarks(kind)",
           "CREATE INDEX ix_par_alma ON parents(alma)",
           "CREATE INDEX ix_par_parent ON parents(parent_alma)"):
    c.execute(ix)
con.commit()

# --------------------------------------------------------------- report
print("\n" + "=" * 62)
print("EXTRACTED")
print("=" * 62)
print("  records read      : %s" % format(n_rec, ","))
print("  shelfmarks        : %s" % format(n_sh, ","))
print("  parent links      : %s" % format(n_par, ","))
print("  distinct ALMAs with a shelfmark: %s"
      % format(c.execute("SELECT COUNT(DISTINCT alma) FROM shelfmarks").fetchone()[0], ","))

print("\n  by kind:")
for r in c.execute("SELECT kind, COUNT(*) n, COUNT(DISTINCT alma) a "
                   "FROM shelfmarks GROUP BY kind ORDER BY n DESC"):
    print("    %-22s %9s  (%s manuscripts)" % (r[0], format(r[1], ","), format(r[2], ",")))

print("\n  manuscripts carrying a NON-current shelfmark too:")
print("    %s" % format(c.execute(
    "SELECT COUNT(DISTINCT alma) FROM shelfmarks WHERE kind!='current'").fetchone()[0], ","))

print("\n  examples — same manuscript, several shelfmarks:")
for alma, in c.execute("""SELECT alma FROM shelfmarks GROUP BY alma
                          HAVING COUNT(*)>2 LIMIT 3"""):
    t = c.execute("SELECT title FROM record_titles WHERE alma=?", (alma,)).fetchone()
    print("\n    ALMA %s  %s" % (alma, (t[0][:46] if t else "")))
    for r in c.execute("SELECT kind, value, owner FROM shelfmarks WHERE alma=? "
                       "ORDER BY ind", (alma,)):
        print("       %-22s %-30s %s" % (r[0], (r[1] or "")[:30], (r[2] or "")[:24]))

print("\n  Liat's cases — does the file hold them?")
for probe in ("ena1205", "msl825", "ms4931a", "msr1948"):
    rows = c.execute("SELECT alma, kind, value FROM shelfmarks "
                     "WHERE value_norm LIKE ? LIMIT 4", ("%" + probe + "%",)).fetchall()
    print("    %-10s %d hit(s)" % (probe, len(rows)))
    for r in rows:
        print("        %s  %-20s %s" % (r[0], r[1], r[2]))

con.close()
print("\nwritten to %s" % OUT)
