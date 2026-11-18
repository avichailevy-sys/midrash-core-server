#!/usr/bin/env python3
"""
core_search.py — MiDRASH Core V0.1 search.

Search across the whole store (all manuscripts + Genizah fragments) by ANY
identifier (ALMA / IE / FL / C-scan / FGP number) or any catalogue field
(title, library, shelfmark, city, country, genre, script...). Forgiving on
shelfmark input (ignores case / spaces / punctuation). Click a result to see
its full record and page image, plus its joins. A "Joins view" button opens
the original Genizah join crosswalk.

Standard library only.

Usage:
    python core_search.py midrash_core.db
then open http://localhost:8000
"""
import os, sys, sqlite3, json, re, webbrowser, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DB = sys.argv[1] if len(sys.argv) > 1 else "midrash_core.db"
PORT = 8000
JOINS_URL = "https://avichailevy-sys.github.io/midrash-core/"

import unicodedata
def norm(s):
    s=(s or '').lower()
    return ''.join(ch for ch in s if not (ch.isspace() or unicodedata.category(ch).startswith('P')))

def connect():
    con = sqlite3.connect(DB, check_same_thread=False); con.row_factory = sqlite3.Row
    con.create_function("normjs", 1, norm)
    return con

def migrate():
    con = connect(); c = con.cursor()
    cols = {r[1] for r in c.execute("PRAGMA table_info(manuscripts)")}
    need = ("shelfmark_norm" not in cols) or ("title_norm" not in cols)
    if "shelfmark_norm" not in cols: c.execute("ALTER TABLE manuscripts ADD COLUMN shelfmark_norm TEXT")
    if "title_norm" not in cols: c.execute("ALTER TABLE manuscripts ADD COLUMN title_norm TEXT")
    if need:
        print("First run: preparing search index (normalising shelfmarks & titles)…", flush=True)
        rows = c.execute("SELECT alma, shelfmark, title FROM manuscripts").fetchall()
        c.executemany("UPDATE manuscripts SET shelfmark_norm=?, title_norm=? WHERE alma=?",
                      [(norm(r["shelfmark"]), norm(r["title"]), r["alma"]) for r in rows])
    for ix in ("CREATE INDEX IF NOT EXISTS ix_ms_snorm ON manuscripts(shelfmark_norm)",
               "CREATE INDEX IF NOT EXISTS ix_ms_tnorm ON manuscripts(title_norm)",
               "CREATE INDEX IF NOT EXISTS ix_ms_fgpinv ON manuscripts(fgp_inventory)",
               "CREATE INDEX IF NOT EXISTS ix_img_scan ON images(fgp_scan)"):
        try: c.execute(ix)
        except sqlite3.OperationalError: pass
    con.commit(); con.close()

def search(term, gz, joinonly, offset, ftitle="", fshelf="", flib="", fplace="", limit=50):
    con = connect(); c = con.cursor()
    term = (term or "").strip()
    almas = set()
    if term:
        for sql in ("SELECT alma FROM manuscripts WHERE alma=?",
                    "SELECT alma FROM manuscripts WHERE fgp_inventory=?",
                    "SELECT alma FROM join_members WHERE inventory=?",
                    "SELECT alma FROM images WHERE fl=? COLLATE NOCASE",
                    "SELECT alma FROM images WHERE fgp_scan=? COLLATE NOCASE",
                    "SELECT alma FROM scans WHERE ie=? COLLATE NOCASE"):
            almas.update(r[0] for r in c.execute(sql, (term,)))
        # former / alternative / cancelled shelfmarks, from MARC 942
        nt_sh = norm(term)
        if nt_sh and 2 <= len(nt_sh) <= 40:
            try:
                almas.update(r[0] for r in c.execute(
                    "SELECT DISTINCT alma FROM shelfmarks WHERE value_norm >= ? "
                    "AND value_norm < ? LIMIT 400",
                    (nt_sh, nt_sh + "\uffff")))
            except Exception:
                pass
    where, args = [], []
    if term:
        like = f"%{term}%"; nt = norm(term)
        parts = ["title LIKE ?", "library LIKE ?", "city LIKE ?", "country LIKE ?",
                 "genre LIKE ?", "script LIKE ?", "persons LIKE ?"]
        args += [like, like, like, like, like, like, like]
        if nt:                              # only add normalised matches when non-empty
            parts += ["title_norm LIKE ?", "shelfmark_norm LIKE ?"]; args += [f"%{nt}%", f"%{nt}%"]
        if almas:
            parts.append("alma IN (%s)" % ",".join("?"*len(almas))); args += list(almas)
        where.append("(" + " OR ".join(parts) + ")")
    # per-column filters (ANDed; use normalised cols so Hebrew punctuation/quotes don't block)
    if ftitle.strip():
        nt = norm(ftitle)
        if nt: where.append("(title LIKE ? OR title_norm LIKE ?)"); args += [f"%{ftitle}%", f"%{nt}%"]
        else:  where.append("title LIKE ?"); args.append(f"%{ftitle}%")
    if fshelf.strip():
        ns = norm(fshelf)
        if ns: where.append("shelfmark_norm LIKE ?"); args.append(f"%{ns}%")
        else:  where.append("shelfmark LIKE ?"); args.append(f"%{fshelf}%")
    if flib.strip():
        where.append("library LIKE ?"); args.append(f"%{flib}%")
    if fplace.strip():
        where.append("(city LIKE ? OR country LIKE ?)"); args += [f"%{fplace}%", f"%{fplace}%"]
    if gz == "gz":   where.append("is_genizah=1")
    elif gz == "nogz": where.append("(is_genizah IS NULL OR is_genizah=0)")
    if joinonly: where.append("n_joins>0")
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = c.execute(f"SELECT COUNT(*) FROM manuscripts {w}", args).fetchone()[0]
    rows = c.execute(f"""SELECT alma,shelfmark,title,library,city,country,is_genizah,
                                fgp_inventory,n_scans,n_joins FROM manuscripts {w}
                         ORDER BY (library IS NULL OR library=''), (shelfmark IS NULL OR shelfmark=''), library, shelfmark
                         LIMIT ? OFFSET ?""", args+[limit, offset]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["scan_types"] = [x[0] for x in c.execute(
            "SELECT DISTINCT scan_type FROM scans WHERE alma=? AND scan_type IS NOT NULL AND scan_type!=''", (r["alma"],))]
        out.append(d)
    con.close()
    return {"total": total, "rows": out, "offset": offset, "limit": limit}

def detail(alma):
    con = connect(); c = con.cursor()
    m = c.execute("SELECT * FROM manuscripts WHERE alma=?", (alma,)).fetchone()
    if not m: con.close(); return {}
    d = dict(m)
    try:
        d["variants"] = [dict(r) for r in c.execute(
            "SELECT kind,value,owner FROM shelfmarks WHERE alma=? ORDER BY ind", (alma,))]
    except Exception:
        d["variants"] = []
    d["scans"] = [dict(r) for r in c.execute("SELECT ie,scan_type,image_count FROM scans WHERE alma=?", (alma,))]
    d["images"] = [dict(r) for r in c.execute(
        "SELECT fl,folio_label,fgp_scan FROM images WHERE alma=? LIMIT 12", (alma,))]
    d["joins"] = [dict(r) for r in c.execute("""
        SELECT DISTINCT j.join_id, j.join_type, j.scholar, j.confidence
        FROM join_members jm JOIN joins j ON j.join_id=jm.join_id WHERE jm.alma=?""", (alma,))]
    jids = [j["join_id"] for j in d["joins"]]
    d["partners"] = []
    if jids:
        q = ("SELECT jm.alma, MIN(jm.inventory) inventory, m.shelfmark, m.library, m.title "
             "FROM join_members jm LEFT JOIN manuscripts m ON m.alma=jm.alma "
             "WHERE jm.join_id IN (%s) AND jm.alma!=? GROUP BY jm.alma "
             "ORDER BY m.shelfmark" % ",".join("?"*len(jids)))
        d["partners"] = [dict(r) for r in c.execute(q, jids+[alma])]
    con.close()
    return d

PAGE = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>MiDRASH Core — search</title>
<meta name="referrer" content="no-referrer">
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#FBFAF6;color:#1F2B3A}
 header{padding:13px 22px;border-bottom:1px solid #D2CBB8;background:#fff;display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
 h1{font-size:18px;margin:0;font-family:Georgia,serif}
 .stat{color:#5A6473;font-size:12px}
 .bar{padding:11px 22px;display:flex;gap:9px;flex-wrap:wrap;align-items:center;border-bottom:1px solid #ECE7D9;background:#fff}
 input,select{font-size:14px;padding:8px 11px;border:1px solid #D2CBB8;border-radius:8px;background:#fff;color:#1F2B3A}
 input#q{flex:1;min-width:300px}
 label{font-size:13px;color:#5A6473;display:inline-flex;gap:5px;align-items:center}
 main{display:grid;grid-template-columns:1fr 400px;align-items:start}
 .list{overflow:auto}
 .count{padding:7px 22px;color:#5A6473;font-size:12px;display:flex;gap:12px;align-items:center}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{text-align:left;padding:8px 22px;border-bottom:1px solid #ECE7D9;vertical-align:top}
 th{position:sticky;top:0;background:#F3F0E7;font-size:10.5px;text-transform:uppercase;letter-spacing:.4px;color:#5A6473}
 tr.filters th{top:33px;padding:5px 22px;background:#F8F6EF}
 .cf{width:100%;font-size:12px;padding:5px 8px;border:1px solid #D9D3C2;border-radius:6px;text-transform:none;letter-spacing:0;font-weight:400}
 tr.r{cursor:pointer} tr.r:hover{background:#fff}
 tr.sel{background:#E8EEF6;box-shadow:inset 4px 0 0 #26456B}
 .mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:#8a8f98}
 .shelf{font-family:Georgia,serif;font-size:14px}
 .heb{font-size:14px}
 .type{font-size:10px;font-weight:700;padding:1px 8px;border-radius:99px}
 .type.gz{background:#E7F3EE;color:#0F6E56} .type.ms{background:#E9EEF6;color:#26456B}
 .tag{display:inline-block;background:#F1F2F4;border-radius:5px;padding:0 6px;margin:1px;font-size:10.5px;color:#54606F}
 .jn{background:#F8EEDD;color:#7a4e00;border-radius:99px;padding:0 7px;font-size:11px;font-weight:700}
 .muted{color:#b3b8bf}
 .btn{padding:5px 12px;border:1px solid #D2CBB8;border-radius:7px;background:#fff;cursor:pointer;font-size:13px}
 .btn:disabled{opacity:.4;cursor:default}
 .detail{border-left:1px solid #D2CBB8;background:#fff;padding:18px 20px;position:sticky;top:0;align-self:start;max-height:calc(100vh - 108px);overflow:auto}
 .detail h2{font-family:Georgia,serif;font-size:17px;margin:0 0 2px;line-height:1.2}
 .detail .k{font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:#5A6473;margin:14px 0 4px;font-weight:700;border-bottom:1px solid #ECE7D9;padding-bottom:3px}
 .kv{font-size:13px;margin:3px 0} .kv b{color:#5A6473;font-weight:600}
 .img{margin:10px 0;border:1px solid #E2DDD0;border-radius:8px;overflow:hidden;background:#F3F0E7}
 .img img{display:block;width:100%;max-height:340px;object-fit:contain;background:#F3F0E7}
 .empty{color:#b3b8bf;padding:40px;text-align:center}
 .partner{padding:6px 0;border-bottom:1px solid #F0ECE0;font-size:12.5px;cursor:pointer}
 .partner:hover{color:#0F6E56}
 #joinsPanel{display:none;position:fixed;inset:0;background:#fff;z-index:50;flex-direction:column}
</style></head><body>
<header><h1>MiDRASH Core</h1><span class="stat" id="stat">…</span>
 <button class="btn" id="joinsBtn" style="margin-left:auto;color:#26456B">Joins view</button></header>
<div class="bar">
 <input id="q" placeholder="Search everything, or any ID (ALMA / IE / FL / C-scan / FGP no.)" autocomplete="off">
 <select id="gz"><option value="">all manuscripts</option><option value="gz">from the Cairo Genizah</option><option value="nogz">not from the Genizah</option></select>
 <label><input type="checkbox" id="joinonly"> in a join</label>
</div>
<main>
 <div class="list">
  <div class="count"><span id="count"></span>
    <button class="btn" id="prev">‹ prev</button><button class="btn" id="next">next ›</button></div>
  <table><thead>
  <tr><th></th><th>Shelfmark</th><th>Title</th><th>Library</th><th>Place</th><th>Scans</th><th>Joins</th></tr>
  <tr class="filters"><th></th>
    <th><input class="cf" id="fshelf" placeholder="filter shelfmark"></th>
    <th><input class="cf" id="ftitle" placeholder="filter title (e.g. רש״י)"></th>
    <th><input class="cf" id="flib" placeholder="filter library"></th>
    <th><input class="cf" id="fplace" placeholder="filter place"></th>
    <th></th><th></th></tr>
  </thead>
  <tbody id="rows"></tbody></table>
 </div>
 <div class="detail" id="detail"><div class="empty">Select a result to see its full record and image.</div></div>
</main>
<div id="joinsPanel">
  <div style="padding:9px 18px;border-bottom:1px solid #D2CBB8;display:flex;align-items:center;gap:12px;background:#FBFAF6">
    <button class="btn" id="joinsBack">‹ Back to search</button>
    <span style="font-family:Georgia,serif;font-size:15px">Joins view — Genizah join crosswalk</span>
    <a class="btn" style="margin-left:auto;text-decoration:none;color:#26456B" href="https://avichailevy-sys.github.io/midrash-core/" target="_blank" rel="noopener">Open in new tab ↗</a>
  </div>
  <iframe id="joinsFrame" style="flex:1;border:0;width:100%" title="Joins view"></iframe>
</div>
<script>
let off=0, total=0;
const JOINS_URL="https://avichailevy-sys.github.io/midrash-core/";
const esc=s=>(s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const IIIF=fl=>`https://nli-proxy.avichai-levy.workers.dev/IIIFv21/${fl}/full/max/0/default.jpg`;
async function stats(){const d=await (await fetch('/stats')).json();
 document.getElementById('stat').textContent=d.total.toLocaleString()+' manuscripts · '+d.genizah.toLocaleString()+' Genizah · '+d.images.toLocaleString()+' images · '+d.joins.toLocaleString()+' joins';}
function params(){const p=new URLSearchParams();p.set('q',document.getElementById('q').value);
 p.set('gz',document.getElementById('gz').value);p.set('joinonly',document.getElementById('joinonly').checked?'1':'');
 p.set('ftitle',document.getElementById('ftitle').value);p.set('fshelf',document.getElementById('fshelf').value);
 p.set('flib',document.getElementById('flib').value);p.set('fplace',document.getElementById('fplace').value);
 p.set('offset',off);return p;}
async function run(){const d=await (await fetch('/search?'+params())).json();total=d.total;
 const from=d.rows.length?off+1:0,to=off+d.rows.length;
 document.getElementById('count').textContent=d.total.toLocaleString()+' results'+(d.total?`  ·  ${from}–${to}`:'');
 document.getElementById('prev').disabled=off<=0;document.getElementById('next').disabled=to>=d.total;
 document.getElementById('rows').innerHTML=d.rows.map(x=>`<tr class="r" data-a="${esc(x.alma)}">
  <td>${x.is_genizah?'<span class="type gz">Cairo Genizah</span>':''}</td>
  <td><div class="shelf">${esc(x.shelfmark)||'<span class=muted>—</span>'}</div><div class="mono">${esc(x.alma)}</div></td>
  <td class="heb">${esc(x.title)||''}</td>
  <td>${esc(x.library)||'<span class=muted>—</span>'}</td>
  <td>${[x.city,x.country].filter(Boolean).map(esc).join(', ')||'<span class=muted>—</span>'}</td>
  <td>${x.n_scans||0}${(x.scan_types||[]).map(t=>` <span class="tag">${esc(t)}</span>`).join('')}</td>
  <td>${x.n_joins?`<span class="jn">${x.n_joins}</span>`:'<span class=muted>—</span>'}</td></tr>`).join('');
}
async function gotoPartner(alma, shelf){
  // navigate the left list to this fragment (as if searched), then open + select it
  document.getElementById('q').value = shelf || alma;
  ['ftitle','fshelf','flib','fplace'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('gz').value=''; document.getElementById('joinonly').checked=false;
  off=0;
  await run();
  await openMs(alma);
  const row=document.querySelector('tr.r[data-a="'+alma+'"]');
  if(row) row.scrollIntoView({block:'center'});
}
async function openMs(alma){
 document.querySelectorAll('tr.r').forEach(t=>t.classList.toggle('sel',t.dataset.a===alma));
 const d=await (await fetch('/ms?alma='+encodeURIComponent(alma))).json();
 const kv=(k,v)=>v?`<div class="kv"><b>${k}:</b> ${esc(v)}</div>`:'';
 const fl=(d.images&&d.images[0])?d.images[0].fl:null;
 const imgHtml=fl?`<div class="img"><a href="https://nli-proxy.avichai-levy.workers.dev/IIIFv21/${fl}/full/max/0/default.jpg" target="_blank" rel="noopener"><img loading="lazy" referrerpolicy="no-referrer" src="${IIIF(fl)}" onerror="this.closest('.img').innerHTML='<div class=empty style=padding:20px>image restricted by the NLI — not available for display</div>'"></a></div>`:'';
 document.getElementById('detail').innerHTML=`<h2 class="heb">${esc(d.shelfmark||d.alma)}</h2>
  <div class="kv heb">${esc(d.title||'')}</div>
  ${d.is_genizah?'<div class="kv"><b>Origin:</b> Cairo Genizah</div>':''}
  ${imgHtml}
  <div class="k">Where it is</div>
  ${kv('Library',d.library)}${kv('Place',[d.city,d.country].filter(Boolean).join(', '))}${kv('Shelfmark',d.shelfmark)}
  <div class="k">Identifiers</div>
  ${kv('ALMA',d.alma)}${kv('FGP number',d.fgp_inventory)}
  ${(d.variants&&d.variants.length>1)?'<div class="k">Also known as</div>'+d.variants.filter(function(v){return v.kind!=='current';}).map(function(v){return '<div class="kv"><b>'+esc(v.value)+'</b> <span class=muted>'+esc(v.kind.replace(/_/g,' '))+(v.owner?' \u00b7 '+esc(v.owner):'')+'</span></div>';}).join(''):''}
  <div class="k">Description</div>
  ${kv('Date',d.date)}${kv('Language',d.language)}${kv('Script',d.script)}${kv('Genre',d.genre)}${kv('Subject',d.subject)}
  ${kv('Extent',d.extent)}${kv('Dimensions',d.dimensions)}${kv('Material',d.material_notes)}${kv('People',d.persons)}${kv('Provenance',d.provenance)}
  <div class="k">Scans (${d.scans.length})</div>
  ${d.scans.map(s=>`<div class="kv mono">${esc(s.ie)} · ${esc(s.scan_type||'?')} · ${s.image_count||'?'} img</div>`).join('')||'<div class=muted>—</div>'}
  <div class="k">Images (first ${d.images.length})</div>
  ${d.images.map(i=>`<div class="kv mono">${esc(i.fl)} ${esc(i.folio_label||'')} ${i.fgp_scan?'· '+esc(i.fgp_scan):''}</div>`).join('')||'<div class=muted>—</div>'}
  <div class="k">Joins (${d.joins.length})</div>
  ${d.joins.map(j=>{const clean=v=>{v=esc(v||'');return /^NULL$/i.test(v)?'':v;};const bits=['join '+esc(j.join_id)];[clean(j.join_type),clean(j.scholar),clean(j.confidence)].forEach(x=>{if(x)bits.push(x);});return `<div class="kv">${bits.join(' · ')}</div>`;}).join('')||'<div class=muted>none</div>'}
  ${d.partners.length?'<div class="k">Join partners ('+d.partners.length+')</div>'+d.partners.map(function(p){var label=esc(p.shelfmark||p.title||('FGP '+(p.inventory||''))||p.alma);var sub=esc(p.library||'')+(p.alma?'  \u00b7  '+esc(p.alma):'')+(p.inventory?'  \u00b7  FGP '+esc(p.inventory):'');var sh=(p.shelfmark||'').replace(/'/g,'');var click=p.alma?('gotoPartner("'+esc(p.alma)+'","'+esc(sh)+'")'):'';return '<div class="partner"'+(p.alma?' onclick=\''+click+'\'':'')+' title="'+(p.alma?'go to this fragment':'not resolvable')+'">'+'<div>'+label+(p.alma?' <span style="color:#0F6E56;font-size:11px">\u21b5 open</span>':'')+'</div>'+'<div class="mono" style="font-size:10.5px">'+sub+'</div></div>';}).join(''):''}`;
 document.getElementById('detail').scrollTop=0;
}
let t;document.getElementById('rows').addEventListener('click',e=>{const r=e.target.closest('tr.r');if(r)openMs(r.dataset.a);});
['q','gz','joinonly','ftitle','fshelf','flib','fplace'].forEach(id=>document.getElementById(id).addEventListener('input',()=>{clearTimeout(t);t=setTimeout(()=>{off=0;run();},220);}));
document.getElementById('prev').onclick=()=>{off=Math.max(0,off-50);run();};
document.getElementById('next').onclick=()=>{if(off+50<total){off+=50;run();}};
document.getElementById('joinsBtn').onclick=()=>{const f=document.getElementById('joinsFrame');if(!f.src)f.src=JOINS_URL;document.getElementById('joinsPanel').style.display='flex';};
document.getElementById('joinsBack').onclick=()=>{document.getElementById('joinsPanel').style.display='none';};
stats();run();
</script></body></html>"""


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
<title>MiDRASH Core — הערות קטלוג (MARC 500)</title>
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
  <h1>הערות קטלוג</h1>
  <span class=sub>שדה MARC 500 — <span id=tot></span> הערות מתוך קטלוג כתיב המלא</span>
  <span class=sub style=margin-right:auto><a href="/">← MiDRASH Core</a></span>
</header>
<div class=bar>
  <input id=q placeholder="חיפוש בהערות ובכותרים (למשל: מדרש)" autofocus>
  <input id=lib placeholder="ספרייה" size=18>
  <input id=shelf placeholder="מספר מדף" size=16>
  <button onclick=go(0)>חפש</button>
  <button class=ghost onclick=dl()>הורדה ל-CSV</button>
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
  out.innerHTML="<div class=empty>טוען…</div>"; pg.innerHTML="";
  fetch("/notes/search?"+params()+"&offset="+OFF).then(function(r){return r.json();})
  .then(function(d){
    count.textContent = d.total.toLocaleString()+" הערות";
    if(!d.rows.length){ out.innerHTML="<div class=empty>אין תוצאות</div>"; return; }
    var qq=q.value.trim();
    var h="<table><tr><th>מספר מדף</th><th>כותר</th>"
         +"<th>ספרייה</th><th>מס׳</th><th>שדה 500 — הערה</th></tr>";
    d.rows.forEach(function(r){
      h+="<tr><td>"+esc(r.shelfmark)+"<div class=sm>"+esc(r.alma)+"</div></td>"
       +"<td>"+hl(r.title,qq)+"</td><td class=sm>"+esc(r.library)+"</td>"
       +"<td class=sm>"+r.note_no+"</td>"
       +"<td class=note>"+hl(r.f500,qq)+"</td></tr>";
    });
    out.innerHTML=h+"</table>";
    var p="";
    if(OFF>0) p+="<button class=ghost onclick='go("+Math.max(0,OFF-100)+")'>→ הקודם</button>";
    if(OFF+100<d.total) p+="<button class=ghost onclick='go("+(OFF+100)+")'>הבא ←</button>";
    p+="<span class=sm>"+(OFF+1)+"–"+Math.min(OFF+100,d.total)+"</span>";
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



# ---------------------------------------------------------------- dating ---

DATING_DOCS = {
  "date": "MARC field 260 subfield c: the date as the cataloguer wrote it, "
          "usually in Hebrew. Present on about 128,000 of 576,000 records.",
  "date_marc": "MARC field 008, characters 7-14: two four-character dates, "
               "the earliest then the latest, with 'u' for an unknown digit. "
               "So 17uu is somewhere in the 1700s, 17uu18uu a range across "
               "the 1700s and 1800s, 1812 a single year, uuuuuuuu unknown. "
               "Present on about 569,000 of 576,000 records.",
  "caveat": "Both values come from the NLI catalogue as supplied. How the "
            "cataloguer arrived at a date, whether from a colophon, the "
            "script, or an earlier description, is not recorded in the data."
}

def _parse_marc_date(s):
    """008/07-14 -> {'earliest':int|None,'latest':int|None,'precision':str}."""
    out = {"earliest": None, "latest": None, "precision": "unknown"}
    if not s:
        return out
    s = s.strip()
    parts = [s[0:4], s[4:8]] if len(s) >= 8 else [s[0:4], ""]

    def one(p):
        # returns (low, high) or (None, None)
        if not p or len(p) < 4:
            return (None, None)
        if p == "uuuu" or not p[0].isdigit():
            return (None, None)
        lo = p.replace("u", "0")
        hi = p.replace("u", "9")
        try:
            return (int(lo), int(hi))
        except ValueError:
            return (None, None)

    lo1, hi1 = one(parts[0])
    lo2, hi2 = one(parts[1])

    lows  = [v for v in (lo1, lo2) if v is not None]
    highs = [v for v in (hi1, hi2) if v is not None]
    if not lows:
        return out
    out["earliest"] = min(lows)
    out["latest"]   = max(highs) if highs else max(lows)

    if out["earliest"] == out["latest"]:
        out["precision"] = "exact year"
    elif out["latest"] - out["earliest"] == 99:
        out["precision"] = "century"
    else:
        out["precision"] = "range"
    return out


def _dating_con():
    con = connect()
    sp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shelfmarks.db")
    if os.path.exists(sp):
        try:
            con.execute("ATTACH DATABASE ? AS sm", (sp,))
        except Exception:
            pass
    return con


def dating_lookup(alma="", shelfmark=""):
    alma = (alma or "").strip()
    shelfmark = (shelfmark or "").strip()
    if not alma and not shelfmark:
        return {"found": False, "error": "give an alma number or a shelfmark"}

    con = _dating_con()
    c = con.cursor()
    row = None
    matched_on = ""

    try:
        if alma:
            row = c.execute(
                "SELECT alma,shelfmark,title,date,date_marc,library,city "
                "FROM manuscripts WHERE alma=?", (alma,)).fetchone()
            matched_on = "alma"

        if row is None and shelfmark:
            n = norm(shelfmark)          # the file's own normalisation
            row = c.execute(
                "SELECT alma,shelfmark,title,date,date_marc,library,city "
                "FROM manuscripts WHERE shelfmark_norm=?", (n,)).fetchone()
            if row:
                matched_on = "current shelfmark"
            else:
                # try every recorded variant, including former and cancelled
                hit = None
                try:
                    hit = c.execute(
                        "SELECT alma,kind,value FROM sm.shelfmarks "
                        "WHERE value_norm=? LIMIT 1", (n,)).fetchone()
                except Exception:
                    hit = None
                if hit is None:
                    # value_norm may have been built with other rules
                    try:
                        hit = c.execute(
                            "SELECT alma,kind,value FROM sm.shelfmarks "
                            "WHERE normjs(value)=? LIMIT 1", (n,)).fetchone()
                    except Exception:
                        hit = None
                if hit:
                    row = c.execute(
                        "SELECT alma,shelfmark,title,date,date_marc,library,city "
                        "FROM manuscripts WHERE alma=?", (hit[0],)).fetchone()
                    matched_on = "%s shelfmark (%s)" % (hit[1] or "other", hit[2])
    finally:
        try:
            con.close()
        except Exception:
            pass

    if row is None:
        return {"found": False,
                "error": "no record found",
                "notes": DATING_DOCS}

    alma_v, shelf_v, title, date_v, marc_v, lib, city = row
    parsed = _parse_marc_date(marc_v)

    return {
        "found": True,
        "matched_on": matched_on,
        "alma": alma_v,
        "shelfmark": shelf_v or "",
        "title": title or "",
        "library": lib or "",
        "city": city or "",
        "date": date_v or "",
        "date_marc": marc_v or "",
        "earliest": parsed["earliest"],
        "latest": parsed["latest"],
        "precision": parsed["precision"],
        "notes": DATING_DOCS,
    }


DATING_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MiDRASH Core - dating lookup</title>
<style>
  :root{
    --bg:#faf8f3; --panel:#f4f1e9; --panel-2:#eae5d8; --rule:#c9c2b4;
    --rule-soft:#ddd8cc; --ink:#1c1a17; --muted:#5b5449; --hl:#6a7a52;
    --hl-soft:#e8ecdd;
  }
  *{box-sizing:border-box}
  body{font-family:Georgia,"Times New Roman",serif;background:var(--bg);
       color:var(--ink);margin:0;padding:28px 20px;line-height:1.55}
  .wrap{max-width:720px;margin:0 auto}
  h1{font-size:22px;font-weight:normal;margin:0 0 2px}
  .sub{color:var(--muted);font-size:13px;margin:0 0 24px}
  form{background:var(--panel);border:1px solid var(--rule);padding:16px;
       margin-bottom:20px}
  label{display:block;font-size:12px;text-transform:uppercase;
        letter-spacing:.5px;color:var(--muted);margin-bottom:4px}
  input[type=text]{width:100%;padding:8px 10px;font-size:15px;
       font-family:inherit;border:1px solid var(--rule);background:#fff;
       color:var(--ink)}
  .row{margin-bottom:12px}
  .or{text-align:center;color:var(--muted);font-size:12px;margin:10px 0}
  button{font-family:inherit;font-size:15px;padding:8px 20px;cursor:pointer;
         background:var(--hl);color:#fff;border:none}
  button:hover{background:#5a6a46}
  table{border-collapse:collapse;width:100%;margin:0 0 18px}
  th{text-align:left;font-size:11px;text-transform:uppercase;
     letter-spacing:.5px;color:var(--muted);font-weight:bold;
     padding:6px 12px 6px 0;width:170px;vertical-align:top;
     border-bottom:1px solid var(--rule-soft)}
  td{padding:6px 0;border-bottom:1px solid var(--rule-soft);
     vertical-align:top}
  .mono{font-family:"Courier New",monospace;font-size:14px}
  .big{font-size:17px}
  .empty{color:var(--muted);font-style:italic}
  .msg{padding:12px;background:var(--panel-2);border:1px solid var(--rule)}
  details{margin-top:26px;font-size:13px;color:var(--muted)}
  summary{cursor:pointer;color:var(--hl)}
  details p{margin:10px 0}
  details b{color:var(--ink);font-family:"Courier New",monospace}
  .api{margin-top:22px;font-size:12px;color:var(--muted)}
  .api code{font-family:"Courier New",monospace;background:var(--panel);
            padding:1px 5px;border:1px solid var(--rule-soft)}
</style>
</head>
<body>
<div class="wrap">

<h1>Dating lookup</h1>
<p class="sub">MiDRASH Core &middot; catalogue dates from the NLI Ktiv records</p>

<form id="f">
  <div class="row">
    <label for="alma">ALMA number</label>
    <input type="text" id="alma" placeholder="990001469420205171" autocomplete="off">
  </div>
  <div class="or">or</div>
  <div class="row">
    <label for="shelf">Shelfmark</label>
    <input type="text" id="shelf" placeholder="Ms. Heb. 10429=28" autocomplete="off" dir="auto">
  </div>
  <button type="submit">Look up</button>
</form>

<div id="out"></div>

<details>
  <summary>Where these dates come from</summary>
  <p><b>date</b> &mdash; MARC field 260 subfield c: the date as the cataloguer
  wrote it, usually in Hebrew. Present on about 128,000 of 576,000 records.</p>
  <p><b>date_marc</b> &mdash; MARC field 008, characters 7 to 14: two
  four-character dates, the earliest then the latest, with <b>u</b> for an
  unknown digit. So <b>17uu</b> is somewhere in the 1700s, <b>17uu18uu</b> a
  range across the 1700s and 1800s, <b>1812</b> a single year, and
  <b>uuuuuuuu</b> unknown. Present on about 569,000 of 576,000 records.</p>
  <p>Earliest, latest and precision are worked out here from date_marc. They
  are not in the catalogue.</p>
  <p>Both values come from the NLI catalogue as supplied. How the cataloguer
  arrived at a date, whether from a colophon, the script, or an earlier
  description, is not recorded in the data.</p>
</details>

<p class="api">The same lookup as JSON:
<code>/dating/lookup?alma=...</code> or <code>/dating/lookup?shelfmark=...</code></p>

</div>
<script>
var f=document.getElementById('f'), out=document.getElementById('out');
var ia=document.getElementById('alma'), is=document.getElementById('shelf');

function esc(s){return String(s==null?'':s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function row(label,val,cls){
  var v = (val===null||val===undefined||val==='')
        ? '<span class="empty">not in the catalogue</span>'
        : '<span class="'+(cls||'')+'" dir="auto">'+esc(val)+'</span>';
  return '<tr><th>'+label+'</th><td>'+v+'</td></tr>';
}

function rawRow(label,html){
  return '<tr><th>'+label+'</th><td>'+html+'</td></tr>';
}

function show(d){
  if(!d.found){
    out.innerHTML='<div class="msg">'+esc(d.error||'not found')+'</div>';
    return;
  }
  var span='';
  if(d.earliest!=null){
    span = (d.earliest===d.latest) ? String(d.earliest)
         : d.earliest+' to '+d.latest;
    span += ' <span class="empty">('+esc(d.precision)+')</span>';
  }
  out.innerHTML='<table>'
    + row('Date (catalogue)', d.date, 'big')
    + row('Date (coded)', d.date_marc, 'mono')
    + rawRow('Years', span || '<span class="empty">unknown</span>')
    + row('ALMA', d.alma, 'mono')
    + row('Shelfmark', d.shelfmark)
    + row('Title', d.title)
    + row('Library', [d.library,d.city].filter(Boolean).join(', '))
    + (d.matched_on ? row('Matched on', d.matched_on) : '')
    + '</table>';
  // fill in the other identifier
  if(d.alma) ia.value=d.alma;
  if(d.shelfmark) is.value=d.shelfmark;
}

f.onsubmit=function(e){
  e.preventDefault();
  var a=ia.value.trim(), s=is.value.trim();
  if(!a && !s){ out.innerHTML=''; return; }
  out.innerHTML='<div class="msg">Looking up...</div>';
  var q='/dating/lookup?alma='+encodeURIComponent(a)
       +'&shelfmark='+encodeURIComponent(s);
  fetch(q).then(function(r){return r.json();}).then(show)
   .catch(function(err){
      out.innerHTML='<div class="msg">'+esc(String(err))+'</div>';});
};
</script>
</body>
</html>
"""

# ------------------------------------------------------------ end of block

class H(BaseHTTPRequestHandler):
    def _s(self, body, ctype="application/json"):
        b = body.encode("utf-8"); self.send_response(200)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def log_message(self,*a): pass
    def do_GET(self):
        u=urlparse(self.path); qs=parse_qs(u.query); g=lambda k,d="": qs.get(k,[d])[0]
        if u.path=="/": return self._s(PAGE,"text/html; charset=utf-8")
        if u.path=="/stats":
            con=connect();c=con.cursor()
            d={"total":c.execute("SELECT COUNT(*) FROM manuscripts").fetchone()[0],
               "genizah":c.execute("SELECT COUNT(*) FROM manuscripts WHERE is_genizah=1").fetchone()[0],
               "images":c.execute("SELECT COUNT(*) FROM images").fetchone()[0],
               "joins":c.execute("SELECT COUNT(*) FROM joins").fetchone()[0]}
            con.close(); return self._s(json.dumps(d))
        if u.path=="/search":
            return self._s(json.dumps(search(g("q"),g("gz"),g("joinonly")=="1",int(g("offset","0") or 0), g("ftitle"),g("fshelf"),g("flib"),g("fplace")), ensure_ascii=False))
        if u.path=="/ms":
            return self._s(json.dumps(detail(g("alma")), ensure_ascii=False))
        if u.path=="/img":
            fl=(qs.get("fl",[""])[0] or "").strip()
            size=(qs.get("s",["600"])[0] or "600").strip()
            if not re.match(r"^FL\d+$", fl):
                self.send_response(400); self.end_headers(); return
            spec = "max" if size=="max" else "!%s,%s" % (size, size)
            src = "https://iiif.nli.org.il/IIIFv21/%s/full/%s/0/default.jpg" % (fl, spec)
            req = urllib.request.Request(src, headers={
                "Referer": "https://avichailevy-sys.github.io/",
                "User-Agent": "Mozilla/5.0 (MiDRASH Core image proxy)"})
            try:
                with urllib.request.urlopen(req, timeout=12) as r:
                    data = r.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data)
            except Exception:
                self.send_response(404); self.end_headers()
            return
        if u.path=="/dating":
            return self._s(DATING_PAGE,"text/html; charset=utf-8")
        if u.path=="/dating/lookup":
            try:
                return self._s(json.dumps(
                    dating_lookup(g("alma"), g("shelfmark")),
                    ensure_ascii=False))
            except Exception as e:
                return self._s(json.dumps({"found":False,"error":str(e)}))
        if u.path=="/notes":
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
        self._s("{}")

if __name__=="__main__":
    try: connect().execute("SELECT 1 FROM manuscripts LIMIT 1")
    except Exception as e:
        print(f"Can't open '{DB}': {e}"); sys.exit(1)
    migrate()
    url=f"http://localhost:{PORT}"
    print(f"MiDRASH Core search running at {url}   (Ctrl+C to stop)")
    try: webbrowser.open(url)
    except Exception: pass
    ThreadingHTTPServer(("127.0.0.1",PORT),H).serve_forever()
