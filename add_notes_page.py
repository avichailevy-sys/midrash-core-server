#!/usr/bin/env python3
"""
add_notes_page.py — add a MARC-500 notes browser to Core at /notes.

Background: extract_notes.py pulled 1,414,315 general notes out of the full
Ktiv export into notes.db. They can't be shipped as a CSV (Excel stops at
about a million rows) and the catalogue has no "midrash" genre, so nobody can
specify a useful filter in advance. A search page lets the reader decide.

Adds three routes to core_search.py:
    /notes         the page
    /notes/search  JSON, used by the page
    /notes/csv     downloads whatever the current search matches

notes.db is opened read-only and separately; midrash_core.db is untouched.

Backs up core_search.py first and refuses if the anchors have moved.

    python3 add_notes_page.py            # report
    python3 add_notes_page.py --apply
"""
import sys, os, shutil, time

APP = "core_search.py"
APPLY = "--apply" in sys.argv

ANCHOR_FUNCS = "class H(BaseHTTPRequestHandler):"
ANCHOR_ROUTE = '        self._s("{}")'

FUNCS = r'''
# ----------------------------------------------------------- MARC 500 notes
NOTES_DB = os.path.join(os.path.dirname(os.path.abspath(DB)), "notes.db")
CSV_CAP = 150000

def notes_connect():
    c = sqlite3.connect("file:%s?mode=ro" % NOTES_DB, uri=True)
    c.row_factory = sqlite3.Row
    return c

def notes_where(q, lib, shelf):
    w, p = [], []
    if q:
        w.append("(f500 LIKE ? OR title LIKE ?)"); p += ["%"+q+"%", "%"+q+"%"]
    if lib:
        w.append("library LIKE ?"); p.append("%"+lib+"%")
    if shelf:
        w.append("shelfmark LIKE ?"); p.append("%"+shelf+"%")
    return (" WHERE " + " AND ".join(w) if w else ""), p

def notes_search(q, lib, shelf, offset):
    con = notes_connect(); c = con.cursor()
    where, p = notes_where(q, lib, shelf)
    total = c.execute("SELECT COUNT(*) FROM notes" + where, p).fetchone()[0]
    rows = [dict(r) for r in c.execute(
        "SELECT alma,shelfmark,title,library,note_no,f500 FROM notes" + where +
        " ORDER BY shelfmark, alma, note_no LIMIT 100 OFFSET ?", p + [offset])]
    con.close()
    return {"total": total, "offset": offset, "rows": rows}

def notes_csv(q, lib, shelf):
    import csv, io
    con = notes_connect(); c = con.cursor()
    where, p = notes_where(q, lib, shelf)
    buf = io.StringIO()
    buf.write("\ufeff")                      # BOM, so Excel reads Hebrew
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["alma", "shelfmark", "title", "library", "genre",
                "language", "date", "note_no", "f500"])
    n = 0
    for r in c.execute("SELECT alma,shelfmark,title,library,genre,language,date,"
                       "note_no,f500 FROM notes" + where +
                       " ORDER BY shelfmark, alma, note_no LIMIT ?", p + [CSV_CAP]):
        w.writerow([r[k] or "" for k in r.keys()]); n += 1
    con.close()
    return buf.getvalue(), n

NOTES_PAGE = r"""<!doctype html><html lang=he dir=rtl><meta charset=utf-8>
<title>MiDRASH Core \u2014 \u05d4\u05e2\u05e8\u05d5\u05ea \u05e7\u05d8\u05dc\u05d5\u05d2 (MARC 500)</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{--bg:#faf8f3;--ink:#1c1a17;--mut:#5b5449;--line:#c9c2b4;--acc:#6a7a52}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:15px/1.5 Georgia,"Times New Roman",serif}
header{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;
       align-items:baseline;gap:14px;flex-wrap:wrap}
h1{font-size:20px;margin:0;font-weight:normal}
.sub{color:var(--mut);font-size:13px}
.bar{padding:12px 18px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input{font:14px Georgia,serif;padding:8px 10px;border:1px solid var(--line);
      border-radius:4px;background:#fff}
#q{flex:1;min-width:260px;font-size:16px}
button{font:14px Georgia,serif;padding:8px 14px;border:1px solid var(--acc);
       background:var(--acc);color:#fff;border-radius:4px;cursor:pointer}
button.ghost{background:#fff;color:var(--acc)}
.count{padding:0 18px;color:var(--mut);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:right;font-size:12px;text-transform:uppercase;letter-spacing:.4px;
   color:var(--mut);padding:8px 10px;border-bottom:1px solid var(--line);
   background:#f2efe7;position:sticky;top:0}
td{padding:8px 10px;border-bottom:1px solid #e8e3d8;vertical-align:top}
tr:hover td{background:#f4f1e9}
.sm{font-size:12px;color:var(--mut)}
.note{max-width:620px}
mark{background:#f3e9a8;padding:0 1px}
.pg{padding:14px 18px;display:flex;gap:8px;align-items:center}
.empty{padding:40px 18px;color:var(--mut);text-align:center}
</style>
<header>
  <h1>\u05d4\u05e2\u05e8\u05d5\u05ea \u05e7\u05d8\u05dc\u05d5\u05d2</h1>
  <span class=sub>\u05e9\u05d3\u05d4 MARC 500 \u2014 <span id=tot></span> \u05d4\u05e2\u05e8\u05d5\u05ea \u05de\u05ea\u05d5\u05da \u05e7\u05d8\u05dc\u05d5\u05d2 \u05db\u05ea\u05d9\u05d1 \u05d4\u05de\u05dc\u05d0</span>
  <span class=sub style=margin-right:auto><a href="/">\u2190 MiDRASH Core</a></span>
</header>
<div class=bar>
  <input id=q placeholder="\u05d7\u05d9\u05e4\u05d5\u05e9 \u05d1\u05d4\u05e2\u05e8\u05d5\u05ea \u05d5\u05d1\u05db\u05d5\u05ea\u05e8\u05d9\u05dd (\u05dc\u05de\u05e9\u05dc: \u05de\u05d3\u05e8\u05e9)" autofocus>
  <input id=lib placeholder="\u05e1\u05e4\u05e8\u05d9\u05d9\u05d4" size=18>
  <input id=shelf placeholder="\u05de\u05e1\u05e4\u05e8 \u05de\u05d3\u05e3" size=16>
  <button onclick=go(0)>\u05d7\u05e4\u05e9</button>
  <button class=ghost onclick=dl()>\u05d4\u05d5\u05e8\u05d3\u05d4 \u05dc-CSV</button>
</div>
<div class=count id=count></div>
<div id=out></div>
<div class=pg id=pg></div>
<script>
var OFF=0;
function esc(s){return (s||"").replace(/[&<>]/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c];});}
function hl(s,q){ if(!q) return esc(s);
  try{ return esc(s).replace(new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g,"\\$&"),"g"),
       function(m){return "<mark>"+m+"</mark>";}); }catch(e){ return esc(s); } }
function params(){
  return "q="+encodeURIComponent(q.value.trim())
       +"&lib="+encodeURIComponent(lib.value.trim())
       +"&shelf="+encodeURIComponent(shelf.value.trim()); }
function go(off){
  OFF=off||0;
  out.innerHTML="<div class=empty>\u05d8\u05d5\u05e2\u05df\u2026</div>"; pg.innerHTML="";
  fetch("/notes/search?"+params()+"&offset="+OFF).then(function(r){return r.json();})
  .then(function(d){
    count.textContent = d.total.toLocaleString()+" \u05d4\u05e2\u05e8\u05d5\u05ea";
    if(!d.rows.length){ out.innerHTML="<div class=empty>\u05d0\u05d9\u05df \u05ea\u05d5\u05e6\u05d0\u05d5\u05ea</div>"; return; }
    var qq=q.value.trim();
    var h="<table><tr><th>\u05de\u05e1\u05e4\u05e8 \u05de\u05d3\u05e3</th><th>\u05db\u05d5\u05ea\u05e8</th>"
         +"<th>\u05e1\u05e4\u05e8\u05d9\u05d9\u05d4</th><th>#</th><th>\u05d4\u05e2\u05e8\u05d4</th></tr>";
    d.rows.forEach(function(r){
      h+="<tr><td>"+esc(r.shelfmark)+"<div class=sm>"+esc(r.alma)+"</div></td>"
       +"<td>"+hl(r.title,qq)+"</td><td class=sm>"+esc(r.library)+"</td>"
       +"<td class=sm>"+r.note_no+"</td>"
       +"<td class=note>"+hl(r.f500,qq)+"</td></tr>";
    });
    out.innerHTML=h+"</table>";
    var p="";
    if(OFF>0) p+="<button class=ghost onclick='go("+Math.max(0,OFF-100)+")'>\u2192 \u05d4\u05e7\u05d5\u05d3\u05dd</button>";
    if(OFF+100<d.total) p+="<button class=ghost onclick='go("+(OFF+100)+")'>\u05d4\u05d1\u05d0 \u2190</button>";
    p+="<span class=sm>"+(OFF+1)+"\u2013"+Math.min(OFF+100,d.total)+"</span>";
    pg.innerHTML=p;
  });
}
function dl(){ window.location="/notes/csv?"+params(); }
q.addEventListener("keydown",function(e){ if(e.key==="Enter") go(0); });
lib.addEventListener("keydown",function(e){ if(e.key==="Enter") go(0); });
shelf.addEventListener("keydown",function(e){ if(e.key==="Enter") go(0); });
fetch("/notes/search?q=&lib=&shelf=&offset=0").then(function(r){return r.json();})
  .then(function(d){ tot.textContent=d.total.toLocaleString(); });
go(0);
</script></html>"""

'''

ROUTE = '''        if u.path=="/notes":
            return self._s(NOTES_PAGE,"text/html; charset=utf-8")
        if u.path=="/notes/search":
            try:
                return self._s(json.dumps(notes_search(
                    g("q"), g("lib"), g("shelf"), int(g("offset","0") or 0)),
                    ensure_ascii=False))
            except Exception as e:
                return self._s(json.dumps({"total":0,"rows":[],"error":str(e)}))
        if u.path=="/notes/csv":
            try:
                body, n = notes_csv(g("q"), g("lib"), g("shelf"))
            except Exception as e:
                self.send_response(500); self.end_headers(); return
            b = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type","text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="ktiv_notes_%d.csv"' % n)
            self.send_header("Content-Length",str(len(b)))
            self.end_headers(); self.wfile.write(b); return
'''

src = open(APP, encoding="utf-8").read()

if "NOTES_PAGE" in src:
    print("already added"); sys.exit(0)

ok = True
for name, a in (("helper functions", ANCHOR_FUNCS), ("route block", ANCHOR_ROUTE)):
    n = src.count(a)
    print("  %-18s %s" % (name, "ready" if n == 1 else "NOT FOUND (%d matches)" % n))
    if n != 1:
        ok = False

if not os.path.exists("notes.db"):
    print("\n  warning: notes.db not found in this directory")

if "import os" not in src:
    print("  note: will add 'import os' to the import line")

if not ok:
    print("\nrefusing to patch"); sys.exit(1)

if APPLY:
    shutil.copy(APP, APP + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    out = src
    if "import os" not in out:
        out = out.replace("import sys, sqlite3", "import os, sys, sqlite3", 1)
    out = out.replace(ANCHOR_FUNCS, FUNCS + "\n" + ANCHOR_FUNCS, 1)
    out = out.replace(ANCHOR_ROUTE, ROUTE + ANCHOR_ROUTE, 1)
    open(APP, "w", encoding="utf-8").write(out)
    import ast
    try:
        ast.parse(out)
        print("\n  written and parses cleanly (backup kept)")
        print("  now:  systemctl restart midrash   then open /notes")
    except SyntaxError as e:
        print("\n  SYNTAX ERROR after patch: %s" % e)
        print("  restore with: cp %s.bak-* %s" % (APP, APP))
else:
    print("\nreport only — re-run with --apply")
