#!/usr/bin/env python3
"""
ingest_full_ktiv.py — bring the FULL Ktiv catalogue export into Core.

Core's catalogue layer was built from a PARTIAL export (79,537 records).
Tsafra delivered the complete one on 27 Aug 2026: ALL_NLI_MSS_24082026,
568,322 records. This loads it.

ADDITIVE, NOT A REBUILD. build_core_db.py begins by DROPping manuscripts,
scans and images, which would destroy the Genizah layer unless every original
source file were still present. This script instead:

  * INSERTS manuscripts whose ALMA is not already in Core  (source='ktiv_full')
  * On manuscripts already present, fills ONLY fields that are empty
    (the same conservative rule add_ktiv.py uses) — nothing is overwritten
  * Adds scans from field 907, and one image per scan from 907 $d

That last point matters: 907 $d holds FLs that IIIF actually serves (verified
in Aug 2026 — FL115671270 displays). Tsafra confirmed 907 holds only the first
FL per IE, so this gives ONE image per scanning, not a full page list. But
235,224 Ktiv-sourced records in Core currently show no image at all, and many
of them should get one from this.

ALWAYS back up first:
    cp midrash_core.db midrash_core.db.bak-preingest

Usage:
    python3 ingest_full_ktiv.py all_nli_mss.xml.gz --dry-run   # report only
    python3 ingest_full_ktiv.py all_nli_mss.xml.gz             # do it
"""
import sys, gzip, json, sqlite3, time, unicodedata
import xml.etree.ElementTree as ET

SRC = None
DRY = False
for a in sys.argv[1:]:
    if a == "--dry-run":
        DRY = True
    else:
        SRC = a
if not SRC:
    print(__doc__); sys.exit(1)

DB = "midrash_core.db"
SOURCE_LABEL = "ktiv_full"

# fields lifted into typed columns; the rest go to extra_json
MAPPED = {'001','245','260','008','041','655','300','943','340','090',
          '561','999','952','966','942','907','005','035',
          '958','650','700','773','710','091','093','751'}

# fields we will fill on an EXISTING row, only where it is currently empty
FILLABLE = ['title','date','date_marc','language','genre','subject','extent',
            'dimensions','material_notes','script','persons','provenance',
            'parent_record','record_types','rights','collection_set','call_no',
            'library','city','country','shelfmark']


def strip_ns(tag):
    return tag.rsplit('}', 1)[-1]


def sub_map(df):
    d = {}
    for sf in df:
        if strip_ns(sf.tag) != 'subfield':
            continue
        d.setdefault(sf.get('code'), []).append((sf.text or '').strip())
    return d


def first(d, code):
    return d.get(code, [''])[0]


def join(vals):
    seen, out = set(), []
    for v in vals:
        v = (v or '').strip()
        if v and v not in seen:
            seen.add(v); out.append(v)
    return '; '.join(out)


def norm(s):
    s = (s or '').lower()
    return ''.join(ch for ch in s
                   if not (ch.isspace() or unicodedata.category(ch).startswith('P')))


def opener(p):
    return gzip.open(p, 'rb') if p.endswith('.gz') else open(p, 'rb')


con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
c = con.cursor()

existing = set(r[0] for r in c.execute("SELECT alma FROM manuscripts"))
print("Core currently holds %s manuscripts" % format(len(existing), ","))
print("mode: %s\n" % ("DRY RUN — nothing will be written" if DRY else "WRITING"))

# the scans/images schemas differ between builds — detect rather than assume
scan_cols = [r[1] for r in c.execute("PRAGMA table_info(scans)")]
img_cols = [r[1] for r in c.execute("PRAGMA table_info(images)")]
print("  scans columns : %s" % ", ".join(scan_cols))
print("  images columns: %s\n" % ", ".join(img_cols))

SCAN_FL = next((x for x in ("first_fl", "fl", "fl_name", "rep_fl") if x in scan_cols), None)
SCAN_TYPE = next((x for x in ("scan_type", "type", "img_type") if x in scan_cols), None)
SCAN_INSERT_COLS = ["ie", "alma"] + ([SCAN_TYPE] if SCAN_TYPE else []) + \
                   ([SCAN_FL] if SCAN_FL else [])
IMG_LABEL = next((x for x in ("folio_label", "label", "lable_name") if x in img_cols), None)
IMG_INSERT_COLS = ["fl", "ie", "alma"] + ([IMG_LABEL] if IMG_LABEL else [])
print("  will write scans(%s), images(%s)\n"
      % (", ".join(SCAN_INSERT_COLS), ", ".join(IMG_INSERT_COLS)))

if not DRY:
    c.execute("PRAGMA synchronous=OFF")

n = n_new = n_touch = n_filled = n_scan = n_img = 0
new_rows, fill_ops, scan_rows, img_rows = [], [], [], []
t0 = time.time()


def flush():
    global new_rows, fill_ops, scan_rows, img_rows
    if DRY:
        new_rows, fill_ops, scan_rows, img_rows = [], [], [], []
        return
    if new_rows:
        c.executemany("""INSERT OR IGNORE INTO manuscripts
            (alma,title,date,date_marc,language,genre,subject,extent,dimensions,
             material_notes,script,persons,provenance,parent_record,record_types,
             rights,collection_set,call_no,library,city,country,shelfmark,
             n_scans,extra_json,source,shelfmark_norm,title_norm)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", new_rows)
    for alma, sets, vals in fill_ops:
        c.execute("UPDATE manuscripts SET %s WHERE alma=?"
                  % ", ".join("%s=?" % k for k in sets), vals + [alma])
    if scan_rows:
        c.executemany("INSERT OR IGNORE INTO scans(%s) VALUES (%s)"
                      % (",".join(SCAN_INSERT_COLS),
                         ",".join("?" * len(SCAN_INSERT_COLS))),
                      [r[:len(SCAN_INSERT_COLS)] for r in scan_rows])
    if img_rows:
        c.executemany("INSERT OR IGNORE INTO images(%s) VALUES (%s)"
                      % (",".join(IMG_INSERT_COLS),
                         ",".join("?" * len(IMG_INSERT_COLS))),
                      [r[:len(IMG_INSERT_COLS)] for r in img_rows])
    con.commit()
    new_rows, fill_ops, scan_rows, img_rows = [], [], [], []


with opener(SRC) as fh:
    for event, el in ET.iterparse(fh, events=('end',)):
        if strip_ns(el.tag) != 'record':
            continue
        n += 1

        cf, dfs = {}, {}
        for child in el:
            t = strip_ns(child.tag)
            if t == 'controlfield':
                cf[child.get('tag')] = (child.text or '')
            elif t == 'datafield':
                dfs.setdefault(child.get('tag'), []).append(sub_map(child))

        alma = cf.get('001', '').strip()
        if not alma:
            el.clear(); continue

        g = lambda tag, code: [first(m, code) for m in dfs.get(tag, []) if first(m, code)]

        # --- holding 942 (same rule build_core_db.py uses) ---
        f942_all = dfs.get('942', [])
        withz = [m for m in f942_all if first(m, 'z')]
        h942 = {}
        for m in withz:
            a = first(m, 'a')
            if a and not a.startswith('Catalogue') and a != 'Unknown Library':
                h942 = m; break
        if not h942:
            h942 = withz[0] if withz else (f942_all[0] if f942_all else {})

        library = first(h942, 'a') or first(dfs.get('710', [{}])[0], 'a')
        city    = first(h942, 'x') or first(dfs.get('710', [{}])[0], 'x')
        country = first(h942, 'c') or first(dfs.get('710', [{}])[0], 'c')
        shelfmark = first(h942, 'z')
        if not shelfmark:
            for tag in ('090', '091'):
                vals = g(tag, 'a')
                pref = [v for v in vals if v.startswith('Ms')]
                if pref: shelfmark = pref[0]; break
                if vals: shelfmark = vals[0]; break

        f008 = cf.get('008', '')
        vals = {
            'title':          first(dfs.get('245', [{}])[0], 'a'),
            'date':           first(dfs.get('260', [{}])[0], 'c'),
            'date_marc':      f008[7:15].strip() if len(f008) >= 15 else '',
            'language':       join(g('041', 'a')),
            'genre':          join(g('655', 'a')),
            'subject':        join(g('650', 'a')),
            'extent':         join(g('300', 'a') or g('943', 'a')),
            'dimensions':     join(g('300', 'c')),
            'material_notes': join(g('340', 'a')),
            'script':         join([(first(m,'a') + (' ' + first(m,'b') if first(m,'b') else '')).strip()
                                    for m in dfs.get('958', [])]),
            'persons':        join([(first(m,'a') + (' ' + first(m,'e') if first(m,'e') else '')).strip()
                                    for m in dfs.get('700', [])]),
            'provenance':     join(g('561', 'a')),
            'parent_record':  first(dfs.get('773', [{}])[0], 'w'),
            'record_types':   join(g('999', 'a')),
            'rights':         first(dfs.get('952', [{}])[0], 'a') or first(dfs.get('903', [{}])[0], 'a'),
            'collection_set': first(dfs.get('966', [{}])[0], 'a'),
            'call_no':        join(g('090', 'a')),
            'library':        library,
            'city':           city,
            'country':        country,
            'shelfmark':      shelfmark,
        }

        scans = dfs.get('907', [])

        if alma not in existing:
            n_new += 1
            extra = {t: ms for t, ms in dfs.items() if t not in MAPPED}
            new_rows.append((alma,) + tuple(vals[k] for k in FILLABLE) +
                            (len(scans),
                             json.dumps(extra, ensure_ascii=False) if extra else None,
                             SOURCE_LABEL, norm(shelfmark), norm(vals['title'])))
            existing.add(alma)
        else:
            cur = c.execute("SELECT %s FROM manuscripts WHERE alma=?"
                            % ", ".join(FILLABLE), (alma,)).fetchone()
            sets, sv = [], []
            for k in FILLABLE:
                if not (cur[k] or '').strip() and vals[k]:
                    sets.append(k); sv.append(vals[k])
            if sets:
                n_touch += 1; n_filled += len(sets)
                if 'shelfmark' in sets:
                    sets.append('shelfmark_norm'); sv.append(norm(vals['shelfmark']))
                if 'title' in sets:
                    sets.append('title_norm'); sv.append(norm(vals['title']))
                fill_ops.append((alma, sets, sv))

        # 907 -> scans, and one SERVABLE image per scan
        for s in scans:
            ie = first(s, 'c')
            if not ie:
                continue
            n_scan += 1
            scan_rows.append((ie, first(s, 'b') or alma, first(s, 'g'), first(s, 'd')))
            fl = first(s, 'd')
            if fl:
                n_img += 1
                img_rows.append((fl, ie, alma, first(s, 'e')))

        el.clear()
        if n % 25000 == 0:
            flush()
            print("  %s records | new %s | enriched %s | scans %s | images %s  (%.0fs)"
                  % (format(n, ","), format(n_new, ","), format(n_touch, ","),
                     format(n_scan, ","), format(n_img, ","), time.time() - t0), flush=True)

flush()

print("\n" + "=" * 62)
print("DRY RUN — nothing written" if DRY else "INGEST COMPLETE")
print("=" * 62)
print("  records read            : %s" % format(n, ","))
print("  manuscripts NEW to Core : %s" % format(n_new, ","))
print("  existing ones enriched  : %s  (%s empty fields filled)"
      % (format(n_touch, ","), format(n_filled, ",")))
print("  scans seen (907)        : %s" % format(n_scan, ","))
print("  images seen (907 $d)    : %s" % format(n_img, ","))
print("  elapsed                 : %.1f min" % ((time.time() - t0) / 60))

if not DRY:
    print("\nAFTER:")
    for r in c.execute("""SELECT source, COUNT(*) n,
        SUM(CASE WHEN alma IN (SELECT DISTINCT alma FROM images) THEN 1 ELSE 0 END) wi
        FROM manuscripts GROUP BY source ORDER BY n DESC"""):
        print("  %-12s %9s   with images: %9s"
              % (r[0], format(r[1], ","), format(r[2], ",")))
    print("  %-12s %9s" % ("TOTAL",
          format(c.execute("SELECT COUNT(*) FROM manuscripts").fetchone()[0], ",")))

con.close()
