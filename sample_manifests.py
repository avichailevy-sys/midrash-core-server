#!/usr/bin/env python3
"""
sample_manifests.py — measure before committing to a full harvest.

Takes a sample of ALMAs from each source layer, fetches each manuscript's
IIIF manifest from the NLI, and reports:

  1. HIT RATE      — what fraction of ALMAs actually have a manifest
  2. TIMING        — seconds per request, so the full run can be estimated
  3. CANVAS COUNTS — how many images per manuscript (how big the harvest is)
  4. LABEL FORMS   — "Page 1" vs "Frag. 001r" vs whatever else exists
  5. FL MISMATCH   — for rows where Core already stores an FL, does the
                     manifest give a different one? (i.e. is Core holding
                     the internal series rather than the servable one)
  6. IE CHECK      — does the manifest's sequence match the IE Core has,
                     i.e. is the "-1" suffix ever wrong

Read-only against the database. Writes a report to sample_report.txt.

Usage:
    python3 sample_manifests.py                 # 60 per source
    python3 sample_manifests.py 20              # 20 per source (quick)
"""
import sqlite3, json, sys, time, re, urllib.request, urllib.error
from collections import Counter

DB       = "midrash_core.db"
PER_SRC  = int(sys.argv[1]) if len(sys.argv) > 1 else 60
DELAY    = 1.0          # seconds between requests — be polite to the NLI
TIMEOUT  = 30
URL      = "https://iiif.nli.org.il/IIIFv21/DOCID/PNX_MANUSCRIPTS%s-1/manifest"

out = open("sample_report.txt", "w", encoding="utf-8")
def say(*a):
    line = " ".join(str(x) for x in a)
    print(line); out.write(line + "\n"); out.flush()


def fetch(alma):
    """Return (manifest_dict, error_string, elapsed_seconds)."""
    t0 = time.time()
    try:
        req = urllib.request.Request(URL % alma, headers={"User-Agent": "MiDRASH-Core/0.1"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8")), None, time.time() - t0
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code, time.time() - t0
    except Exception as e:
        return None, type(e).__name__, time.time() - t0


def canvases_of(man):
    """Yield (label, FL) for each canvas. Returns [] if shape is unexpected."""
    got = []
    for seq in man.get("sequences", []):
        for cv in seq.get("canvases", []):
            label = cv.get("label", "")
            fl = ""
            m = re.search(r"(FL\d+)", cv.get("@id", "") or "")
            if m:
                fl = m.group(1)
            else:                                    # fall back to the image resource
                for im in cv.get("images", []):
                    m = re.search(r"(FL\d+)", json.dumps(im))
                    if m: fl = m.group(1); break
            got.append((label, fl))
    return got


def sequence_ies(man):
    return [m.group(1) for s in man.get("sequences", [])
            for m in [re.search(r"(IE\d+)", s.get("@id", "") or "")] if m]


con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
c = con.cursor()

say("=" * 72)
say("MANIFEST SAMPLE — %d per source, %.1fs delay" % (PER_SRC, DELAY))
say("=" * 72)

stats     = {}
labels    = Counter()
mismatch  = {"same": 0, "different": 0, "core_has_none": 0}
ie_check  = {"match": 0, "mismatch": 0, "core_has_no_ie": 0}
canvas_ns = []
times     = []
examples  = []

for source in ("catalogue", "genizah", "ktiv"):
    rows = c.execute(
        "SELECT alma, shelfmark, library FROM manuscripts WHERE source=? "
        "ORDER BY RANDOM() LIMIT ?", (source, PER_SRC)).fetchall()
    ok = fail = 0
    say("\n--- %s (%d sampled) ---" % (source, len(rows)))

    for r in rows:
        alma = r["alma"]
        man, err, dt = fetch(alma)
        times.append(dt)
        if man is None:
            fail += 1
            if fail <= 3:
                say("    no manifest: %s  (%s)  %s" % (alma, err, (r["shelfmark"] or "")[:34]))
            time.sleep(DELAY); continue
        ok += 1

        cvs = canvases_of(man)
        canvas_ns.append(len(cvs))
        for label, _fl in cvs:
            # record the SHAPE of the label, not the label itself
            labels[re.sub(r"\d+", "N", str(label)).strip()] += 1

        # does Core's stored FL agree with the manifest's?
        core_fls = [x[0] for x in c.execute(
            "SELECT fl FROM images WHERE alma=?", (alma,)).fetchall()]
        man_fls = set(fl for _l, fl in cvs if fl)
        if not core_fls:
            mismatch["core_has_none"] += 1
        elif set(core_fls) & man_fls:
            mismatch["same"] += 1
        else:
            mismatch["different"] += 1
            if len(examples) < 8:
                examples.append((alma, sorted(core_fls)[:3], sorted(man_fls)[:3]))

        # is the "-1" suffix giving us the IE Core knows about?
        core_ies = [x[0] for x in c.execute(
            "SELECT ie FROM scans WHERE alma=?", (alma,)).fetchall()]
        man_ies = sequence_ies(man)
        if not core_ies:
            ie_check["core_has_no_ie"] += 1
        elif set(core_ies) & set(man_ies):
            ie_check["match"] += 1
        else:
            ie_check["mismatch"] += 1

        time.sleep(DELAY)

    stats[source] = (ok, fail)
    say("    manifests found: %d / %d" % (ok, ok + fail))

# ----------------------------------------------------------------- report
say("\n" + "=" * 72)
say("RESULTS")
say("=" * 72)

say("\n1. HIT RATE")
tot_ok = tot_n = 0
for s, (ok, fail) in stats.items():
    n = ok + fail; tot_ok += ok; tot_n += n
    say("   %-11s %3d/%3d   %5.1f%%" % (s, ok, n, 100.0*ok/n if n else 0))
say("   %-11s %3d/%3d   %5.1f%%" % ("OVERALL", tot_ok, tot_n,
                                    100.0*tot_ok/tot_n if tot_n else 0))

say("\n2. TIMING")
if times:
    avg = sum(times)/len(times)
    say("   mean %.2fs, max %.2fs per request" % (avg, max(times)))
    for label, n in (("488,460 (all)", 488460), ("235,224 (ktiv only)", 235224)):
        secs = n * (avg + DELAY)
        say("   %-22s -> %.1f hours (%.1f days) at this rate, single-threaded"
            % (label, secs/3600, secs/86400))

say("\n3. IMAGES PER MANUSCRIPT")
if canvas_ns:
    canvas_ns.sort()
    say("   min %d, median %d, max %d, mean %.1f"
        % (canvas_ns[0], canvas_ns[len(canvas_ns)//2], canvas_ns[-1],
           sum(canvas_ns)/len(canvas_ns)))
    say("   -> a full harvest would be roughly %s rows"
        % format(int(sum(canvas_ns)/len(canvas_ns) * 488460), ","))

say("\n4. LABEL FORMS  (digits replaced by N)")
for form, n in labels.most_common(12):
    say("   %6d  %r" % (n, form))
say("   %d distinct label shapes -> %s"
    % (len(labels),
       "uniform" if len(labels) <= 2 else "NOT uniform; page matching needs care"))

say("\n5. DOES CORE'S STORED FL MATCH THE MANIFEST?")
for k, v in mismatch.items():
    say("   %-14s %d" % (k, v))
if examples:
    say("   examples where they differ:")
    for alma, cf, mf in examples:
        say("     %s  core=%s  manifest=%s" % (alma, cf, mf))

say("\n6. DOES THE '-1' SUFFIX GIVE THE IE CORE KNOWS?")
for k, v in ie_check.items():
    say("   %-16s %d" % (k, v))

say("\nreport written to sample_report.txt")
out.close(); con.close()
