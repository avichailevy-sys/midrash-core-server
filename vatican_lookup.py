#!/usr/bin/env python3
"""
vatican_lookup.py — match the Vatican transcription folders to Core records.

Input : data/vatican_almas.tsv, three columns, produced on the HPC:
            folder name, record number from the ALTO file names, page count

The record numbers come in two shapes. Most are the full 18-digit ALMA
(990001981090205171). 131 are 11 digits (99002497684). The 11-digit ones look
like the ALMA with its 7-digit institution suffix cut off, and one manuscript
proves it: MSS_Vat.ebr.384.pt.1 carries 99000193616 while pt.2 carries
990001936160205171. This script does not assume that. It tries the number as
given AND with 0205171 appended, reports which form Core actually knows, and
only then uses it.

Output: data/vatican_core.tsv — one row per folder with whatever Core holds.

Read-only. Standard library only.

Usage, on the server in /root/midrash_core:
    python3 vatican_lookup.py
    python3 vatican_lookup.py --db midrash_core.db --in data/vatican_almas.tsv
"""
import argparse
import collections
import os
import re
import sqlite3
import sys

SUFFIX = "0205171"           # the institution suffix on every NLI Hebrew ALMA
WANT = ["shelfmark", "library", "title", "date", "date_marc",
        "language", "genre", "subject", "extent", "source"]


def base_shelfmark(folder):
    """MSS_Vat.ebr.384.pt.2 -> MSS_Vat.ebr.384"""
    return re.sub(r"\.pt\.[A-Za-z0-9]+$", "", folder)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="midrash_core.db")
    ap.add_argument("--in", dest="infile", default="data/vatican_almas.tsv")
    ap.add_argument("--out", dest="outfile", default="data/vatican_core.tsv")
    args = ap.parse_args()

    if not os.path.exists(args.infile):
        sys.exit("can't find %s" % args.infile)

    rows = []
    for line in open(args.infile, encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3 and p[0]:
            rows.append((p[0], p[1], int(p[2]) if p[2].isdigit() else 0))
    print("read %d folders, %s pages" % (len(rows), format(sum(r[2] for r in rows), ",")))

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    c = con.cursor()
    cols = {r[1] for r in c.execute("PRAGMA table_info(manuscripts)")}
    use = [x for x in WANT if x in cols]
    print("columns available in Core: %s" % ", ".join(use))

    cache = {}

    def lookup(alma):
        if alma not in cache:
            cache[alma] = c.execute(
                "SELECT alma,%s FROM manuscripts WHERE alma=?" % ",".join(use),
                (alma,)).fetchone()
        return cache[alma]

    # ---------------------------------------------- test the suffix question
    short = sorted({i for _, i, _ in rows if len(i) == 11})
    bare_found = sum(1 for i in short if lookup(i))
    sufx_found = sum(1 for i in short if lookup(i + SUFFIX))
    print("\n11-digit numbers: %d" % len(short))
    print("  found in Core as given          : %d" % bare_found)
    print("  found in Core with %s appended : %d" % (SUFFIX, sufx_found))
    if sufx_found > bare_found:
        print("  -> the suffix rule holds; using it")
    elif bare_found == 0 and sufx_found == 0:
        print("  -> neither form is in Core; these 131 stay unidentified")
    else:
        print("  -> inconclusive, look at this before trusting the result")

    # ------------------------------------------------------------- the table
    out, stat = [], collections.Counter()
    for folder, num, pages in rows:
        rec, used, form = None, "", ""
        for cand, label in ((num, "as given"), (num + SUFFIX, "suffix added")):
            if not num:
                break
            rec = lookup(cand)
            if rec:
                used, form = cand, label
                break
        d = {"folder": folder, "shelfmark_folder": base_shelfmark(folder),
             "number_in_filename": num, "alma_used": used, "id_form": form,
             "pages": pages}
        if rec:
            stat["found"] += 1
            stat["found_pages"] += pages
            for k in use:
                d[k] = (rec[k] or "").replace("\t", " ").replace("\n", " ") \
                    if isinstance(rec[k], str) else (rec[k] if rec[k] is not None else "")
        else:
            stat["missing" if num else "no_number"] += 1
            for k in use:
                d[k] = ""
        out.append(d)

    cols_out = ["folder", "shelfmark_folder", "number_in_filename", "alma_used",
                "id_form", "pages"] + use
    os.makedirs(os.path.dirname(args.outfile) or ".", exist_ok=True)
    with open(args.outfile, "w", encoding="utf-8") as fh:
        fh.write("\t".join(cols_out) + "\n")
        for d in out:
            fh.write("\t".join(str(d.get(k, "")) for k in cols_out) + "\n")

    print("\nmatched to a Core record : %d of %d folders (%s pages)"
          % (stat["found"], len(rows), format(stat["found_pages"], ",")))
    print("number not in Core       : %d" % stat["missing"])
    print("no number in the filename: %d" % stat["no_number"])

    # does the folder's shelfmark agree with Core's?
    def squash(s):
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    agree = disagree = 0
    examples = []
    for d in out:
        if not d["alma_used"]:
            continue
        f = squash(d["shelfmark_folder"].replace("MSS_", ""))
        s = squash(d.get("shelfmark", ""))
        if f and s and (f in s or s in f):
            agree += 1
        else:
            disagree += 1
            if len(examples) < 12:
                examples.append((d["shelfmark_folder"], d.get("shelfmark", ""), d["alma_used"]))
    print("\nshelfmark check (folder name against Core's shelfmark)")
    print("  agree    : %d" % agree)
    print("  disagree : %d" % disagree)
    for a, b, al in examples:
        print("     %-24s Core says %-34s %s" % (a, repr(b), al))

    # what works are these? the top catalogue titles
    if "title" in use:
        titles = collections.Counter(d["title"] for d in out if d.get("title"))
        print("\nmost common catalogue titles (%d folders have one):"
              % sum(1 for d in out if d.get("title")))
        for t, n in titles.most_common(20):
            print("  %4d  %s" % (n, t[:90]))

    print("\nwritten: %s" % args.outfile)
    con.close()


if __name__ == "__main__":
    main()
