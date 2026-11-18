#!/usr/bin/env python3
"""
fix_notes_hebrew.py — the /notes page shows \\u05d4\\u05e2... instead of Hebrew.

Cause: NOTES_PAGE was written as a raw string (r\"\"\"...\"\"\") so that the
JavaScript regex inside it wouldn't trip Python's escape warnings. That also
stopped Python interpreting the \\uXXXX sequences, so they reach the browser
as literal text.

Fix: replace every \\uXXXX inside NOTES_PAGE with the character it stands for.
The string stays raw, so the JS regex is still safe.

    python3 fix_notes_hebrew.py            # report
    python3 fix_notes_hebrew.py --apply
"""
import sys, re, shutil, time, ast

APP = "core_search.py"
APPLY = "--apply" in sys.argv

src = open(APP, encoding="utf-8").read()

start = src.find('NOTES_PAGE = r"""')
if start == -1:
    start = src.find('NOTES_PAGE = """')
    if start == -1:
        print("NOTES_PAGE not found — is the notes page installed?"); sys.exit(1)
    open_len = len('NOTES_PAGE = """')
else:
    open_len = len('NOTES_PAGE = r"""')

body_start = start + open_len
end = src.find('"""', body_start)
if end == -1:
    print("couldn't find the end of NOTES_PAGE"); sys.exit(1)

page = src[body_start:end]
found = re.findall(r"\\u[0-9a-fA-F]{4}", page)
print("literal \\uXXXX sequences inside NOTES_PAGE: %d" % len(found))

if not found:
    print("nothing to fix — the page already has real characters"); sys.exit(0)

fixed = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), page)

print("\n  before: %s" % found[:4])
print("  after : %s" % [chr(int(f[2:], 16)) for f in found[:4]])

if APPLY:
    shutil.copy(APP, APP + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    out = src[:body_start] + fixed + src[end:]
    try:
        ast.parse(out)
    except SyntaxError as e:
        print("\n  refusing: result would not parse (%s)" % e); sys.exit(1)
    open(APP, "w", encoding="utf-8").write(out)
    print("\n  written (backup kept). now:  systemctl restart midrash")
else:
    print("\nreport only — re-run with --apply")
