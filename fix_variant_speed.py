#!/usr/bin/env python3
"""
fix_variant_speed.py — make the shelfmark-variant lookup fast.

The version wire_shelfmarks.py installed used:
    WHERE value_norm LIKE '%term%'
A leading wildcard cannot use an index, so every search scanned all 916,036
rows. Measured: 9.1s for a variant search vs 1.0s without — slow enough to
produce 504s through Cloudflare.

This replaces it with a prefix match, which does use ix_shv_norm:
    WHERE value_norm LIKE 'term%'
Shelfmark searches are prefix searches in practice ("Ms. Heb. 2268", "III 7"),
so little is lost. Very short or very long terms skip the lookup entirely.

    python3 fix_variant_speed.py            # report
    python3 fix_variant_speed.py --apply
"""
import sys, shutil, time

APP = "core_search.py"
APPLY = "--apply" in sys.argv

OLD = """        nt_sh = norm(term)
        if nt_sh:
            try:
                almas.update(r[0] for r in c.execute(
                    "SELECT DISTINCT alma FROM shelfmarks WHERE value_norm LIKE ? LIMIT 400",
                    ("%" + nt_sh + "%",)))
            except Exception:
                pass"""

NEW = """        nt_sh = norm(term)
        if nt_sh and 2 <= len(nt_sh) <= 40:
            try:
                almas.update(r[0] for r in c.execute(
                    "SELECT DISTINCT alma FROM shelfmarks WHERE value_norm >= ? "
                    "AND value_norm < ? LIMIT 400",
                    (nt_sh, nt_sh + "\\uffff")))
            except Exception:
                pass"""

src = open(APP, encoding="utf-8").read()

if "value_norm >= ?" in src:
    print("already fixed"); sys.exit(0)
if src.count(OLD) != 1:
    print("anchor not found (%d matches) — show me the file" % src.count(OLD)); sys.exit(1)

print("found the slow lookup, ready to replace")
print("  old: value_norm LIKE '%term%'   (full scan)")
print("  new: value_norm >= 'term' AND < 'term\\uffff'   (index range)")

if APPLY:
    shutil.copy(APP, APP + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    open(APP, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
    print("\nwritten. now:  systemctl restart midrash")
else:
    print("\nreport only — re-run with --apply")
