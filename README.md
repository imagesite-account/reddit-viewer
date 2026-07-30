# reddit-mirror

A read-only, text-only Reddit mirror that builds on GitHub's infrastructure and
serves from GitHub Pages.

The point: your browser is on a network that blocks Reddit. A GitHub Actions
runner isn't. So the runner does the fetching, writes plain JSON, and Pages
serves that JSON as a static site. **Your browser never makes a request to
reddit.com** — there is nothing for the filter to see except `github.io`.

```
Actions runner (Azure IP, not on your network)
   │  fetch.py  →  reddit .json endpoints
   │  strips to title / selftext / comment tree
   ▼
site/data/*.json  →  Pages artifact  →  yourname.github.io/reddit-mirror
   ▼
your browser: loads static JSON only
```

## Set up

1. **Push this folder to a new GitHub repo.** Public repo = unlimited Actions
   minutes. Private = it eats your quota fast at a 30-minute cadence.
2. **Settings → Pages → Source → GitHub Actions.** Not "Deploy from a branch".
3. **Run the `probe` workflow first.** Actions tab → probe → Run workflow.
   This answers the only question that matters before you invest any time —
   see *Will this actually work* below.
4. If the probe passes, edit `config.json` and push. The `mirror` workflow runs
   on push and every 30 minutes after.

## Will this actually work

Reddit has been progressively throttling unauthenticated access from datacenter
IP ranges, and Actions runners are datacenter IPs. The probe tells you where you
stand today:

| Probe result | What it means |
|---|---|
| both OK | Full fidelity: scores, nested comments, everything. |
| json FAIL, rss OK | `fetch.py` falls back automatically. Flat comments, no scores, fewer of them. The UI marks these threads `rss`. |
| both FAIL | Reddit won't talk to Actions runners. See *Fallback* below. |

## Config

```jsonc
{
  "subreddits": ["LocalLLama", "MachineLearning", "Claude", "ChatGPT", "ClaudeCode", "Anthropic"],
  "sort": "hot",          // hot | new | top | rising
  "posts_per_sub": 15,
  "comment_limit": 200,   // comments requested per thread
  "comment_depth": 6,     // nesting levels kept
  "delay_seconds": 2.0    // don't lower this
}
```

Rough cost: `posts_per_sub × subreddits × delay_seconds` is your runtime floor.
15 × 3 × 2s ≈ 90 seconds per build, ~48 builds/day.

## Reading it

Two panes on desktop, single column on mobile.

| Key | |
|---|---|
| `j` / `k` | move through the list |
| `enter` / `o` | open |
| `esc` | back to the list |
| `/` | filter titles |

The strip along the top is the important bit: it shows exactly when the capture
happened and how old it is. It turns amber past 90 minutes. Everything on the
page is a snapshot — votes and replies after that moment don't exist here.

## Gotchas

- **Scheduled workflows get disabled after 60 days of repo inactivity.** GitHub
  emails you first. Any commit or manual dispatch resets the clock.
- **Cron is best-effort.** GitHub queues scheduled jobs; `*/30` drifts, sometimes
  by a lot during peak hours.
- **A failed fetch doesn't blank the site.** `fetch.py` exits non-zero if it got
  nothing, which stops the job before the deploy step, so Pages keeps serving the
  last good build.
- **No on-demand threads.** You read what the cron pulled. Triggering a fetch
  from the page would need a token in client-side JS, which would be public.
  Trigger `mirror` manually from the Actions tab instead.
- **State is not preserved between runs.** Each build starts from a fresh
  checkout, so threads that fall out of `hot` disappear from your mirror.
- **A public repo makes your mirror world-readable.** It's public Reddit content,
  so this is mostly fine — but the subreddit list is a real signal about you, and
  it's attached to your GitHub identity.

## Fallback if runners are blocked

Self-host [Redlib](https://github.com/redlib-org/redlib) on a residential
connection (a home box, a Pi, a cheap VPS) and point `fetch.py` at that instead
of `www.reddit.com`. Same static-generation pattern, different origin — you only
have to change the URL builders in `listing_json` / `thread_json`.

## Security note

Comment bodies are arbitrary user-controlled strings. `app.js` puts every one of
them into the DOM via `textContent`, never `innerHTML`. If you add markdown
rendering later, run it through a sanitizer such as DOMPurify — otherwise you've
built yourself an XSS delivery mechanism aimed at your own browser.

## One thing worth naming

If the blocking network is a workplace or school, routing around the filter
probably violates its acceptable-use policy regardless of how cleanly it's done.
That's a real consequence and it's independent of whether the technique works.
Your call.

## Local preview

`site/data/` ships with sample output so you can open the reader without a
network round trip. It's gitignored, so it won't be committed.

```powershell
cd site
python -m http.server 8000
# open http://localhost:8000
```
