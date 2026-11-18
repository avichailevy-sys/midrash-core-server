#!/usr/bin/env python3
"""
Adds a dating lookup to MiDRASH Core.

  /dating         - a page: enter an ALMA number or a shelfmark, get the dates
  /dating/lookup  - the same thing as JSON, for calling from code

Run with no arguments for a dry run (checks the anchors, writes nothing).
Run with --apply to patch core_search.py, then: systemctl restart midrash
"""

import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "core_search.py")

# ----------------------------------------------------------------- the block

BLOCK = r'''
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
'''

ROUTES = '''        if u.path=="/dating":
            return self._s(DATING_PAGE,"text/html; charset=utf-8")
        if u.path=="/dating/lookup":
            try:
                return self._s(json.dumps(
                    dating_lookup(g("alma"), g("shelfmark")),
                    ensure_ascii=False))
            except Exception as e:
                return self._s(json.dumps({"found":False,"error":str(e)}))
'''

BLOCK_ANCHOR  = "class H(BaseHTTPRequestHandler):"
ROUTES_ANCHOR = '        if u.path=="/notes":'


def main():
    apply = "--apply" in sys.argv

    if not os.path.exists(TARGET):
        print("core_search.py not found at", TARGET)
        return 1

    src = open(TARGET, encoding="utf-8").read()

    print("target:", TARGET, "(%d bytes)" % len(src))

    if "DATING_PAGE" in src:
        print("already patched: DATING_PAGE is present. Nothing to do.")
        return 1

    ok = True
    for name, anchor in (("block", BLOCK_ANCHOR), ("routes", ROUTES_ANCHOR)):
        n = src.count(anchor)
        print("%-7s anchor: %d match%s  %s"
              % (name, n, "" if n == 1 else "es", "ready" if n == 1 else "PROBLEM"))
        if n != 1:
            ok = False

    # these are used by the new code; check they are importable/defined,
    # allowing for a combined import line
    for label, test in (
            ("def connect(", "def connect(" in src),
            ("def norm(",    "def norm(" in src),
            ("os",           re.search(r"^import .*\bos\b", src, re.M) is not None),
            ("re",           re.search(r"^import .*\bre\b", src, re.M) is not None),
            ("json",         re.search(r"^import .*\bjson\b", src, re.M) is not None),
    ):
        print("%-7s %-14s %s" % ("needs:", label, "found" if test else "MISSING"))
        if not test:
            ok = False

    if not ok:
        print("\nanchors not clean, not writing.")
        return 1

    out = src.replace(BLOCK_ANCHOR, BLOCK + "\n" + BLOCK_ANCHOR, 1)
    out = out.replace(ROUTES_ANCHOR, ROUTES + ROUTES_ANCHOR, 1)

    if not apply:
        print("\ndry run only. %d bytes -> %d bytes" % (len(src), len(out)))
        print("run again with --apply to write.")
        return 0

    bak = TARGET + ".bak-dating"
    open(bak, "w", encoding="utf-8").write(src)
    open(TARGET, "w", encoding="utf-8").write(out)
    print("\nbackup:  ", bak)
    print("written: ", TARGET, "(%d bytes)" % len(out))
    print("\nnow run:  systemctl restart midrash")
    print("then open midrash-core.org/dating")
    return 0


if __name__ == "__main__":
    sys.exit(main())
