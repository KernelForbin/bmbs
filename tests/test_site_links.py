"""
Walks every internal `href` across all six HTML pages that make up the site
and checks it resolves to a real file on disk. No other test does this --
each page test only asserts its OWN footer link works, never the whole graph.

Pure filesystem + regex, no browser, no network, nothing under data/ touched.

The resolver mirrors GitHub Pages' actual behaviour, not a guess: a bare
extensionless path resolves to a same-named sibling FILE.html BEFORE it ever
tries `<name>/index.html` -- verified against the live site and documented in
CLAUDE.md (it's exactly why `features.html` exists as a redirect stub next to
`features/index.html`). Getting that backwards would make this test claim a
broken link is fine, so the resolver's own gotcha case is checked directly
(section A) before it's trusted to check anything else.

    python tests/test_site_links.py
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGES = [
    REPO / "index.html",
    REPO / "football" / "index.html",
    REPO / "history" / "index.html",
    REPO / "football" / "history" / "index.html",
    REPO / "features" / "index.html",
    REPO / "features.html",
]
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


HREF_RE = re.compile(r'href="([^"]*)"')


def resolve(path, repo=REPO):
    """GitHub Pages' resolution order for an internal path, exactly as
    verified against the live site (CLAUDE.md, features/index.html's entry):
    a bare extensionless path tries a same-named sibling FILE.html BEFORE
    <name>/index.html. Query strings and fragments never reach the server."""
    path = path.split("#", 1)[0].split("?", 1)[0]
    if path == "/":
        return repo / "index.html"
    trimmed = path.strip("/")
    if path.endswith("/"):
        return repo / trimmed / "index.html"
    last_segment = trimmed.rsplit("/", 1)[-1]
    if "." in last_segment:
        return repo / trimmed                      # already names a file (e.g. /CNAME)
    sibling_html = repo / f"{trimmed}.html"
    if sibling_html.exists():
        return sibling_html                         # the trap: this wins over trimmed/index.html
    return repo / trimmed / "index.html"


# ---------------- A. the resolver itself, against the documented gotcha ----------------

check("A1 a bare '/features' resolves to the sibling features.html FILE, not features/index.html",
      resolve("/features") == REPO / "features.html", resolve("/features"))
check("A2 '/features/' (trailing slash) resolves to the directory's index.html",
      resolve("/features/") == REPO / "features" / "index.html", resolve("/features/"))
check("A3 a query string never changes what's resolved",
      resolve("/features/?sport=football") == resolve("/features/"))
check("A4 the bare-path trap only applies when a sibling .html actually exists",
      resolve("/football") == REPO / "football" / "index.html", resolve("/football"))
check("A5 root resolves to the top-level index.html", resolve("/") == REPO / "index.html")


# ---------------- B. every internal href on every page resolves to a real file ----------------

DYNAMIC_MARK = "${"   # a JS template literal (e.g. gamedayUrl(gamePk)) -- never an internal path here,
                       # but skipped explicitly rather than assumed so a real internal one isn't missed


def internal_hrefs(page):
    text = page.read_text(encoding="utf-8")
    out = []
    for href in HREF_RE.findall(text):
        if DYNAMIC_MARK in href:
            continue
        if not href.startswith("/"):
            continue   # external (fonts.googleapis.com, mlb.com, etc.) or a bare fragment/mailto
        out.append(href)
    return out


graph = {}   # page path (relative) -> set of resolved target files
broken = []
for page in PAGES:
    check(f"{page.relative_to(REPO)}: exists", page.exists())
    if not page.exists():
        continue
    hrefs = internal_hrefs(page)
    targets = set()
    for href in hrefs:
        target = resolve(href, REPO)
        targets.add(target)
        if not target.exists():
            broken.append(f"{page.relative_to(REPO)} -> {href!r} -> missing {target.relative_to(REPO)}")
    graph[page] = targets

check("B1 every internal href on every page resolves to a file that actually exists",
      not broken, "; ".join(broken[:5]))

# every page must link back to somewhere real -- a page with zero internal
# links at all would mean nobody can navigate away from it
no_links = [p.relative_to(REPO) for p, targets in graph.items() if not targets and p.name != "features.html"]
check("B2 every page (other than the redirect stub) has at least one internal link off it",
      not no_links, str(no_links))


# ---------------- C. the link graph, informationally ----------------

linked_to = set()
for targets in graph.values():
    linked_to |= targets
orphans = [p.relative_to(REPO) for p in PAGES if p.exists() and p not in linked_to]
if orphans:
    print(f"NOTE (informational, not a failure): nothing in this graph links to {[str(o) for o in orphans]} "
          f"-- fine if that's an entry point (like a page reached only from Discord/bookmarks), worth a look otherwise.")


print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all site-link checks passed")
