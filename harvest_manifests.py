#!/usr/bin/env python3
"""
harvest_manifests.py — fetch every manuscript's IIIF manifest from the NLI
and store the servable FL numbers.

Why: the FLs Core currently holds come from NLI's file exports and are the
internal Rosetta series, which IIIF will not serve (verified: they return
"unauthorized", while the manifest gives a different FL a few numbers higher).
The manifest is NLI's authoritative answer to "what images does this
manuscript have, and how do I address them".

Writes into TWO NEW tables. Nothing existing is touched:
    manifest_images  — one row per page: alma, ie, canvas index, label, fl
    harvest_log      — one row per ALMA attempted, so the run is RESUMABLE

Resumable: re-running skips any ALMA already in harvest_log. If the run is
interrupted (ssh drop, reboot, ctrl-C), just start it again.

RUN IT UNDER nohup OR screen — it takes many hours:
    nohup python3 harvest_manifests.py > harvest.out 2>&1 &
    tail -f harvest.out

Options:
    --limit N       only attempt N ALMAs (use this to test first)
    --workers N     parallel fetchers (default 4)
    --delay S       seconds each worker waits between requests (default 1.0)
    --retry-failed  also re-attempt ALMAs that previously failed
"""
import sqlite3, json, sys, time, re, threading, queue, argparse
import urllib.request, urllib.error

DB  = "midrash_core.db"
URL = "https://iiif.nli.org.il/IIIFv21/DOCID/PNX_MANUSCRIPTS%s-%d/manifest"
UA  = "MiDRASH-Core/0.1 (Tel Aviv University; avichaile@tauex.tau.ac.il)"

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--delay", type=float, default=1.0)
ap.add_argument("--retry-failed", action="store_true")
ap.add_argument("--every", type=int, default=50,
                help="print a progress line every N manuscripts (default 50)")
args = ap.parse_args()

# ------------------------------------------------------------------ schema
con = sqlite3.connect(DB)
c = con.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS manifest_images (
    alma TEXT, ie TEXT, seq_no INTEGER, canvas_no INTEGER,
    label TEXT, fl TEXT)""")
c.execute("""CREATE TABLE IF NOT EXISTS harvest_log (
    alma TEXT PRIMARY KEY, status TEXT, n_canvases INTEGER,
    n_seqs INTEGER, detail TEXT, fetched_at TEXT)""")
c.execute("CREATE INDEX IF NOT EXISTS ix_mi_alma ON manifest_images(alma)")
c.execute("CREATE INDEX IF NOT EXISTS ix_mi_ie   ON manifest_images(ie)")
c.execute("CREATE INDEX IF NOT EXISTS ix_mi_fl   ON manifest_images(fl)")
con.commit()

# ------------------------------------------- how many scannings per ALMA?
# A manuscript scanned twice has two IEs, and the manifest URL suffix (-1, -2)
# selects between them. Use what we already know to decide how many to try.
print("working out how many sequences to try per manuscript ...", flush=True)
ie_counts = {}
has_nn = c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                   "AND name='nli_numbers'").fetchone() is not None
if has_nn:
    for alma, n in c.execute(
            "SELECT mms_id, COUNT(DISTINCT ie) FROM nli_numbers GROUP BY mms_id"):
        ie_counts[alma] = min(int(n), 4)
for alma, n in c.execute("SELECT alma, COUNT(DISTINCT ie) FROM scans GROUP BY alma"):
    ie_counts[alma] = max(ie_counts.get(alma, 1), min(int(n), 4))
print("  have scan counts for %s manuscripts" % format(len(ie_counts), ","), flush=True)

# ------------------------------------------------------------- target list
done = set(r[0] for r in c.execute(
    "SELECT alma FROM harvest_log" if not args.retry_failed
    else "SELECT alma FROM harvest_log WHERE status='ok'"))
todo = [r[0] for r in c.execute("SELECT alma FROM manuscripts")]
todo = [a for a in todo if a not in done]
if args.limit:
    todo = todo[:args.limit]
print("  %s already done, %s to attempt" % (format(len(done), ","), format(len(todo), ",")),
      flush=True)
if not todo:
    print("nothing to do."); sys.exit(0)

# ------------------------------------------------------------------ worker
q_in, q_out = queue.Queue(), queue.Queue()
stop = threading.Event()
throttle = {"hits": 0}
lock = threading.Lock()


def fetch_one(alma, suffix):
    req = urllib.request.Request(URL % (alma, suffix), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def parse(man):
    """-> (list of (ie, seq_no, canvas_no, label, fl), n_seqs)"""
    rows, n_seq = [], 0
    for si, seq in enumerate(man.get("sequences", []) or []):
        n_seq += 1
        m = re.search(r"(IE\d+)", seq.get("@id", "") or "")
        ie = m.group(1) if m else None
        for ci, cv in enumerate(seq.get("canvases", []) or []):
            label = cv.get("label")
            if isinstance(label, list):
                label = label[0] if label else None
            if isinstance(label, dict):
                label = label.get("@value")
            fl = None
            mm = re.search(r"(FL\d+)", cv.get("@id", "") or "")
            if mm:
                fl = mm.group(1)
            else:
                mm = re.search(r"(FL\d+)", json.dumps(cv.get("images", [])))
                if mm: fl = mm.group(1)
            rows.append((ie, si + 1, ci + 1, label, fl))
    return rows, n_seq


def worker():
    while not stop.is_set():
        try:
            alma = q_in.get(timeout=2)
        except queue.Empty:
            return
        allrows, nseq, status, detail = [], 0, "ok", None
        tries = ie_counts.get(alma, 1)
        for suffix in range(1, tries + 1):
            for attempt in (1, 2):
                try:
                    man = fetch_one(alma, suffix)
                    r, n = parse(man)
                    allrows += r; nseq += n
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (429, 503):
                        with lock:
                            throttle["hits"] += 1
                        time.sleep(15 * attempt)
                        continue
                    if suffix == 1:
                        status, detail = "http_%d" % e.code, None
                    break
                except Exception as e:
                    if attempt == 1:
                        time.sleep(3); continue
                    if suffix == 1:
                        status, detail = "error", type(e).__name__
                    break
            time.sleep(args.delay)
        if allrows:
            status = "ok"
        elif status == "ok":
            status = "empty"
        q_out.put((alma, status, allrows, nseq, detail))
        q_in.task_done()


for a in todo:
    q_in.put(a)
threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
for t in threads:
    t.start()

# ------------------------------------------------------- main: write results
n_ok = n_fail = n_rows = 0
t0 = time.time()
pending_img, pending_log = [], []
total = len(todo)

try:
    for i in range(total):
        alma, status, rows, nseq, detail = q_out.get()
        if status == "ok":
            n_ok += 1
            for ie, si, ci, label, fl in rows:
                pending_img.append((alma, ie, si, ci, label, fl))
            n_rows += len(rows)
        else:
            n_fail += 1
        pending_log.append((alma, status, len(rows), nseq, detail,
                            time.strftime("%Y-%m-%d %H:%M:%S")))

        if len(pending_log) >= 200:
            c.executemany("INSERT INTO manifest_images VALUES (?,?,?,?,?,?)", pending_img)
            c.executemany("INSERT OR REPLACE INTO harvest_log VALUES (?,?,?,?,?,?)", pending_log)
            con.commit(); pending_img, pending_log = [], []

        if (i + 1) % args.every == 0:
            el = time.time() - t0
            rate = (i + 1) / el
            left = (total - i - 1) / rate / 3600 if rate else 0
            print("  %s/%s  ok=%s fail=%s  rows=%s  %.1f/s  %.0fm elapsed  ~%.1fh left  throttled=%d"
                  % (format(i + 1, ","), format(total, ","), format(n_ok, ","),
                     format(n_fail, ","), format(n_rows, ","), rate, el / 60, left,
                     throttle["hits"]), flush=True)
except KeyboardInterrupt:
    print("\ninterrupted — flushing what we have ...")
finally:
    stop.set()
    if pending_img or pending_log:
        c.executemany("INSERT INTO manifest_images VALUES (?,?,?,?,?,?)", pending_img)
        c.executemany("INSERT OR REPLACE INTO harvest_log VALUES (?,?,?,?,?,?)", pending_log)
        con.commit()

print("\n" + "=" * 60)
print("attempted %s   ok %s   failed %s   rows %s   %.1f min"
      % (format(n_ok + n_fail, ","), format(n_ok, ","), format(n_fail, ","),
         format(n_rows, ","), (time.time() - t0) / 60))
print("\nstatus breakdown so far:")
for r in c.execute("SELECT status, COUNT(*) FROM harvest_log GROUP BY status ORDER BY 2 DESC"):
    print("   %-12s %s" % (r[0], format(r[1], ",")))
print("\nre-run the same command to continue where this left off.")
con.close()
