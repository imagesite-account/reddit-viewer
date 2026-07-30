#!/usr/bin/env python3
"""
Fetches Reddit listings + comment trees and writes slim JSON into site/data/.

Runs on a GitHub Actions runner, not on your machine. The static site reads
only the JSON this produces, so the browser never talks to reddit.com.

Usage:
    python fetch.py            # normal build
    python fetch.py --probe    # check whether reddit answers this runner at all
"""

import html
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "site" / "data"
CONFIG = json.loads((ROOT / "config.json").read_text())

UA = CONFIG.get("user_agent", "github-pages-reader/0.1")
DELAY = CONFIG.get("delay_seconds", 2.0)
RETRIES = CONFIG.get("retries", 3)

TAG_RE = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------- http


def _request(url, accept):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def fetch(url, accept="application/json", retries=RETRIES):
    """GET with backoff. Raises on final failure."""
    last = None
    for attempt in range(retries):
        try:
            return _request(url, accept)
        except urllib.error.HTTPError as e:
            last = e
            # 429/503 are worth waiting out; 403/404 usually are not.
            if e.code in (429, 500, 502, 503, 504):
                wait = DELAY * (2 ** attempt) + 1
                print(f"  {e.code} on {url} — retrying in {wait:.0f}s", flush=True)
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(DELAY * (2 ** attempt))
    raise last


def get_json(url):
    return json.loads(fetch(url))


# ---------------------------------------------------------------- parsing


def clean(text):
    """Reddit markdown arrives HTML-escaped in places. Normalise it."""
    return html.unescape(text or "").replace("\r\n", "\n").strip()


def strip_html(markup):
    return html.unescape(TAG_RE.sub("", markup or "")).strip()


def flatten(children, depth=0, acc=None, max_depth=8):
    """Depth-first walk of a comment listing into a flat, ordered list."""
    acc = [] if acc is None else acc
    for child in children:
        if child.get("kind") != "t1":
            continue  # skip "more comments" stubs and deleted branches
        d = child["data"]
        body = clean(d.get("body"))
        if not body or body in ("[deleted]", "[removed]"):
            continue
        acc.append(
            {
                "author": d.get("author") or "[unknown]",
                "body": body,
                "score": d.get("score", 0),
                "created": d.get("created_utc", 0),
                "depth": min(depth, max_depth),
                "op": bool(d.get("is_submitter")),
            }
        )
        replies = d.get("replies")
        if isinstance(replies, dict) and depth < max_depth:
            flatten(replies["data"]["children"], depth + 1, acc, max_depth)
    return acc


# ---------------------------------------------------------------- sources


def listing_json(sub, sort, limit):
    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={limit}&raw_json=1"
    data = get_json(url)
    out = []
    for child in data["data"]["children"]:
        p = child["data"]
        if p.get("stickied"):
            continue
        out.append(
            {
                "id": p["id"],
                "sub": p.get("subreddit", sub),
                "title": clean(p.get("title")),
                "author": p.get("author") or "[unknown]",
                "score": p.get("score", 0),
                "num_comments": p.get("num_comments", 0),
                "created": p.get("created_utc", 0),
                "selftext": clean(p.get("selftext")),
                "url": p.get("url", ""),
                "permalink": "https://www.reddit.com" + p.get("permalink", ""),
                "flair": p.get("link_flair_text") or "",
            }
        )
    return out


def listing_rss(sub, limit):
    """Fallback when the .json endpoint 403s. Titles only, no scores."""
    raw = fetch(f"https://www.reddit.com/r/{sub}/hot/.rss", accept="application/rss+xml")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(raw)
    out = []
    for entry in root.findall("a:entry", ns)[:limit]:
        link = entry.find("a:link", ns)
        href = link.get("href") if link is not None else ""
        m = re.search(r"/comments/([a-z0-9]+)/", href)
        if not m:
            continue
        title = entry.find("a:title", ns)
        author = entry.find("a:author/a:name", ns)
        updated = entry.find("a:updated", ns)
        created = 0
        if updated is not None and updated.text:
            try:
                created = datetime.fromisoformat(
                    updated.text.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                pass
        out.append(
            {
                "id": m.group(1),
                "sub": sub,
                "title": (title.text if title is not None else "").strip(),
                "author": (author.text if author is not None else "[unknown]"),
                "score": 0,
                "num_comments": 0,
                "created": created,
                "selftext": "",
                "url": href,
                "permalink": href,
                "flair": "",
                "degraded": True,
            }
        )
    return out


def thread_json(post_id, limit, depth):
    url = (
        f"https://www.reddit.com/comments/{post_id}.json"
        f"?limit={limit}&depth={depth}&sort=top&raw_json=1"
    )
    listing = get_json(url)
    post = listing[0]["data"]["children"][0]["data"]
    return {
        "selftext": clean(post.get("selftext")),
        "score": post.get("score", 0),
        "num_comments": post.get("num_comments", 0),
        "comments": flatten(listing[1]["data"]["children"], max_depth=depth),
    }


def thread_rss(post_id):
    """Fallback comment source. Flat, no scores, no nesting."""
    raw = fetch(
        f"https://www.reddit.com/comments/{post_id}/.rss", accept="application/rss+xml"
    )
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(raw)
    comments = []
    for entry in root.findall("a:entry", ns):
        content = entry.find("a:content", ns)
        author = entry.find("a:author/a:name", ns)
        body = strip_html(content.text if content is not None else "")
        if not body:
            continue
        comments.append(
            {
                "author": author.text if author is not None else "[unknown]",
                "body": body,
                "score": 0,
                "created": 0,
                "depth": 0,
                "op": False,
            }
        )
    return {"selftext": "", "score": 0, "num_comments": len(comments),
            "comments": comments, "degraded": True}


# ---------------------------------------------------------------- probe


def probe():
    checks = [
        ("listing json", "https://www.reddit.com/r/programming/hot.json?limit=1",
         "application/json"),
        ("listing rss", "https://www.reddit.com/r/programming/hot/.rss",
         "application/rss+xml"),
    ]
    ok = False
    for name, url, accept in checks:
        try:
            body = fetch(url, accept=accept, retries=1)
            print(f"  OK    {name:14s} {len(body):,} bytes")
            ok = True
        except urllib.error.HTTPError as e:
            print(f"  FAIL  {name:14s} HTTP {e.code}")
        except Exception as e:
            print(f"  FAIL  {name:14s} {type(e).__name__}: {e}")
    print("\nreachable" if ok else "\nreddit is refusing this runner entirely")
    return 0 if ok else 1


# ---------------------------------------------------------------- build


def build():
    subs = CONFIG["subreddits"]
    sort = CONFIG.get("sort", "hot")
    per_sub = CONFIG.get("posts_per_sub", 15)
    c_limit = CONFIG.get("comment_limit", 200)
    c_depth = CONFIG.get("comment_depth", 6)

    OUT.mkdir(parents=True, exist_ok=True)
    index, degraded = [], False

    for sub in subs:
        print(f"r/{sub}", flush=True)
        try:
            posts = listing_json(sub, sort, per_sub)
        except Exception as e:
            print(f"  json listing failed ({e}) — trying rss", flush=True)
            try:
                posts = listing_rss(sub, per_sub)
                degraded = True
            except Exception as e2:
                print(f"  rss listing failed too ({e2}) — skipping sub", flush=True)
                continue

        for post in posts:
            time.sleep(DELAY)
            pid = post["id"]
            try:
                thread = thread_json(pid, c_limit, c_depth)
            except Exception as e:
                print(f"  {pid} json failed ({e}) — trying rss", flush=True)
                try:
                    thread = thread_rss(pid)
                    degraded = True
                except Exception as e2:
                    print(f"  {pid} skipped ({e2})", flush=True)
                    continue

            record = {**post, **thread}
            (OUT / f"{pid}.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
            index.append(
                {
                    "id": pid,
                    "sub": post["sub"],
                    "title": post["title"],
                    "author": post["author"],
                    "score": record.get("score", 0),
                    "num_comments": len(record["comments"]),
                    "created": post["created"],
                    "flair": post["flair"],
                    "degraded": record.get("degraded", False),
                }
            )
            print(f"  {pid}  {len(record['comments']):4d} comments  {post['title'][:60]}",
                  flush=True)

    if not index:
        print("\nNothing fetched. Failing the job so the previous deploy stays live.")
        return 1

    index.sort(key=lambda p: (-p["score"], -p["created"]))
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False),
                                    encoding="utf-8")
    (OUT / "meta.json").write_text(
        json.dumps(
            {
                "captured": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "threads": len(index),
                "subreddits": subs,
                "sort": sort,
                "degraded": degraded,
            }
        ),
        encoding="utf-8",
    )

    # Drop thread files that fell out of this run.
    keep = {p["id"] for p in index} | {"index", "meta"}
    for f in OUT.glob("*.json"):
        if f.stem not in keep:
            f.unlink()

    print(f"\nWrote {len(index)} threads to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(probe() if "--probe" in sys.argv else build())
