#!/usr/bin/env python3
"""
fix_genizah_flag.py — set is_genizah on the records added by the full Ktiv ingest.

The 87,938 manuscripts loaded as source='ktiv_full' came in with no is_genizah
value, so they are invisible to the Genizah filter.

Core's existing rule (from add_ktiv.py) is origin-based: a manuscript is
Genizah if "geniz" or "גניז" appears in its genre or subject field.

NLI also carries its own type classification in MARC field 906 (per Tsafra &
Chicco's field table: a closed list of manuscript / binding / genizah). The
ingest preserved that in extra_json. This script REPORTS both signals so they
can be compared, then applies Core's existing rule so the flag keeps meaning
the same thing across the whole database.

    python3 fix_genizah_flag.py            # report only
    python3 fix_genizah_flag.py --apply    # report, then set the flag
"""
import sqlite3, sys, json

DB = "midrash_core.db"
APPLY = "--apply" in sys.argv

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
c = con.cursor()

RULE = ("(LOWER(COALESCE(genre,'')||' '||COALESCE(subject,'')) LIKE '%geniz%' "
        " OR COALESCE(genre,'')||' '||COALESCE(subject,'') LIKE '%גניז%')")

print("=" * 60)
print("GENIZAH FLAG — current state")
print("=" * 60)
for r in c.execute("""SELECT source,
        COUNT(*) n,
        SUM(CASE WHEN is_genizah=1 THEN 1 ELSE 0 END) flagged,
        SUM(CASE WHEN is_genizah IS NULL THEN 1 ELSE 0 END) unset
        FROM manuscripts GROUP BY source ORDER BY n DESC"""):
    print("  %-12s %8s   flagged %8s   unset %8s"
          % (r["source"], format(r["n"], ","), format(r["flagged"], ","),
             format(r["unset"], ",")))

print("\n  Core's rule applied to the NEW records (ktiv_full):")
n_new = c.execute("SELECT COUNT(*) FROM manuscripts WHERE source='ktiv_full'").fetchone()[0]
n_hit = c.execute("SELECT COUNT(*) FROM manuscripts WHERE source='ktiv_full' AND " + RULE).fetchone()[0]
print("    %s of %s would be flagged Genizah (%.1f%%)"
      % (format(n_hit, ","), format(n_new, ","), 100.0 * n_hit / max(n_new, 1)))

print("\n  sample of what the rule catches:")
for r in c.execute("SELECT shelfmark, library, genre FROM manuscripts "
                   "WHERE source='ktiv_full' AND " + RULE + " LIMIT 5"):
    print("    %-26s %-28s %s"
          % ((r["shelfmark"] or "")[:26], (r["library"] or "")[:28],
             (r["genre"] or "")[:40]))

# ---- the independent signal: MARC 906, preserved in extra_json ----
print("\n  cross-check — MARC 906 (NLI's own type classification):")
vals = {}
got = 0
for r in c.execute("SELECT extra_json FROM manuscripts "
                   "WHERE source='ktiv_full' AND extra_json IS NOT NULL LIMIT 40000"):
    try:
        d = json.loads(r["extra_json"])
    except Exception:
        continue
    for m in d.get("906", []):
        for v in m.get("a", []):
            vals[v] = vals.get(v, 0) + 1
            got += 1
if vals:
    for v, n in sorted(vals.items(), key=lambda x: -x[1])[:10]:
        print("    %-34s %s" % (v[:34], format(n, ",")))
else:
    print("    (no 906 found in extra_json for these records)")

if not APPLY:
    print("\nreport only — re-run with --apply to set the flag")
    con.close(); sys.exit(0)

print("\napplying Core's rule ...")
c.execute("UPDATE manuscripts SET is_genizah=1 WHERE source='ktiv_full' AND " + RULE)
set1 = c.rowcount
c.execute("UPDATE manuscripts SET is_genizah=0 WHERE source='ktiv_full' AND is_genizah IS NULL")
set0 = c.rowcount
con.commit()
print("  set to 1: %s" % format(set1, ","))
print("  set to 0: %s" % format(set0, ","))

tot = c.execute("SELECT COUNT(*) FROM manuscripts WHERE is_genizah=1").fetchone()[0]
print("\n  Genizah total across Core: %s" % format(tot, ","))
con.close()
