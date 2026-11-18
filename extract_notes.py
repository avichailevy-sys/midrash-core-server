#!/usr/bin/env python3
"""
extract_notes.py — pull every MARC field 500 (general notes) out of the full
Ktiv export into a table, one row per note.

Field 500 is free text a cataloguer writes when something doesn't fit a
structured field — where the record came from, what the text is, a rhyme
scheme, a comparison with another manuscript. It repeats: the full export has
1,414,315 occurrences across 568,322 records, about 2.5 per manuscript.

Source: ALL_NLI_MSS_24082026 (NLI, 27 Aug 2026).
Writes to its own file, notes.db, leaving midrash_core.db alone.

One row per note:
    alma, shelfmark, title, library, genre, language, date, note_no, f500

    python3 extract_notes.py all_nli_mss.xml.gz
"""
import sys, gzip, sqlite3, time
import xml.etree.ElementTree as ET

SRC = sys.argv[1] if len(sys.argv) > 1 else "all_nli_mss.xml.gz"
OUT = sys.argv[2] if len(sys.argv) > 2 else "notes.db"


def strip_ns(t):
    return t.rsplit("}", 1)[-1]


def opener(p):
    return gzip.open(p, "rb") if p.endswith(".gz") else open(p, "rb")


def subs(df):
    d = {}
    for sf in df:
        if strip_ns(sf.tag) == "subfield":
            d.setdefault(sf.get("code"), []).append((sf.text or "").strip())
    return d


def first(d, code):
    return (d.get(code) or [""])[0]


con = sqlite3.connect(OUT); c = con.cursor()
c.executescript("""
DROP TABLE IF EXISTS notes;
CREATE TABLE notes(
    alma TEXT, shelfmark TEXT, title TEXT, library TEXT,
    genre TEXT, language TEXT, date TEXT,
    note_no INTEGER, f500 TEXT);
""")
c.execute("PRAGMA journal_mode=OFF")
c.execute("PRAGMA synchronous=OFF")

n_rec = n_note = 0
batch = []
t0 = time.time()

with opener(SRC) as fh:
    for _ev, el in ET.iterparse(fh, events=("end",)):
        if strip_ns(el.tag) != "record":
            continue
        n_rec += 1

        alma = title = shelf = lib = lang = date = ""
        genres, notes = [], []

        for ch in el:
            t = strip_ns(ch.tag)
            if t == "controlfield" and ch.get("tag") == "001":
                alma = (ch.text or "").strip()
            elif t == "datafield":
                tag = ch.get("tag")
                if tag == "500":
                    v = first(subs(ch), "a")
                    if v:
                        notes.append(v)
                elif tag == "245" and not title:
                    title = first(subs(ch), "a")
                elif tag == "942":
                    s = subs(ch)
                    if first(s, "z") and (ch.get("ind1") or "") == "1":
                        shelf = first(s, "z"); lib = first(s, "a")
                    elif first(s, "z") and not shelf:
                        shelf = first(s, "z"); lib = lib or first(s, "a")
                elif tag == "710" and not lib:
                    lib = first(subs(ch), "a")
                elif tag == "655":
                    g = first(subs(ch), "a")
                    if g:
                        genres.append(g)
                elif tag == "041" and not lang:
                    lang = first(subs(ch), "a")
                elif tag == "260" and not date:
                    date = first(subs(ch), "c")

        genre = "; ".join(dict.fromkeys(genres))
        for i, nt in enumerate(notes, start=1):
            batch.append((alma, shelf, title, lib, genre, lang, date, i, nt))
            n_note += 1

        el.clear()
        if len(batch) >= 50000:
            c.executemany("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?)", batch)
            con.commit(); batch = []
            print("  %s records, %s notes  (%.0fs)"
                  % (format(n_rec, ","), format(n_note, ","), time.time() - t0), flush=True)

if batch:
    c.executemany("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?)", batch)
con.commit()

print("\nbuilding indexes ...")
for ix in ("CREATE INDEX ix_n_alma ON notes(alma)",
           "CREATE INDEX ix_n_shelf ON notes(shelfmark)",
           "CREATE INDEX ix_n_lib ON notes(library)"):
    c.execute(ix)
c.execute("CREATE VIRTUAL TABLE notes_fts USING fts5(f500, title, content='notes', content_rowid='rowid')")
c.execute("INSERT INTO notes_fts(rowid, f500, title) SELECT rowid, f500, title FROM notes")
con.commit()

print("\n" + "=" * 58)
print("EXTRACTED")
print("=" * 58)
print("  records read : %s" % format(n_rec, ","))
print("  notes        : %s" % format(n_note, ","))
print("  manuscripts with at least one note: %s"
      % format(c.execute("SELECT COUNT(DISTINCT alma) FROM notes").fetchone()[0], ","))

print("\n  how many notes per manuscript:")
for r in c.execute("""SELECT k, COUNT(*) FROM
    (SELECT alma, COUNT(*) k FROM notes GROUP BY alma)
    GROUP BY k ORDER BY k LIMIT 8"""):
    print("    %d note(s): %s manuscripts" % (r[0], format(r[1], ",")))

print("\n  SIZE OF A MIDRASHIC SLICE — which filter, and how many notes:")
for lbl, where in (
    ("title contains מדרש",   "title LIKE '%מדרש%'"),
    ("genre contains midrash","LOWER(genre) LIKE '%midrash%'"),
    ("genre contains מדרש",   "genre LIKE '%מדרש%'"),
    ("note itself mentions מדרש", "f500 LIKE '%מדרש%'"),
):
    n = c.execute("SELECT COUNT(*) FROM notes WHERE " + where).fetchone()[0]
    m = c.execute("SELECT COUNT(DISTINCT alma) FROM notes WHERE " + where).fetchone()[0]
    print("    %-28s %9s notes   %8s manuscripts" % (lbl, format(n, ","), format(m, ",")))

print("\n  most common genre values (for choosing a filter):")
for r in c.execute("SELECT genre, COUNT(DISTINCT alma) n FROM notes "
                   "WHERE genre!='' GROUP BY genre ORDER BY n DESC LIMIT 12"):
    print("    %7s  %s" % (format(r[1], ","), r[0][:62]))

print("\n  sample notes:")
for r in c.execute("SELECT shelfmark, note_no, f500 FROM notes LIMIT 5"):
    print("    %-18s [%d] %s" % ((r[0] or "")[:18], r[1], (r[2] or "")[:60]))

print("\nwritten to %s  (%.1f min)" % (OUT, (time.time() - t0) / 60))
con.close()
