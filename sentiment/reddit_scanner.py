#!/usr/bin/env python3
"""
Reddit sentiment scanner for BTC paper-trading test.

Read-only, public, unauthenticated.
Sources: Reddit public JSON for r/Bitcoin, r/CryptoCurrency, r/BitcoinMarkets
  https://www.reddit.com/r/<sub>/top.json?limit=20&t=day
No login, no posting, no API keys.

Functions:
  fetch_subreddit(subreddit, limit=20)
  score_sentiment(text) -> float in [-1, 1]
  extract_strategy_ideas(posts) -> list of candidate ideas

Output:
  ~/workspace/trading-bot/data/reddit_sentiment.json
"""

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Public JSON endpoints, no auth
SUBREDDITS = ["Bitcoin", "CryptoCurrency", "BitcoinMarkets"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Fallback hosts tried in order if the primary is blocked.
# Reddit frequently returns 403 Blocked to datacenter IPs; trying
# alternates is cheap and keeps the run read-only and unauthenticated.
URL_TEMPLATES = [
    "https://www.reddit.com/r/{sub}/top.json?limit={limit}&t=day",
    "https://old.reddit.com/r/{sub}/top.json?limit={limit}&t=day",
    "https://api.reddit.com/r/{sub}/top?limit={limit}&t=day",
]

# Small keyword lexicon for simple sentiment scoring.
POSITIVE_WORDS = {
    "bull", "bullish", "moon", "pump", "rally", "breakout", "surge",
    "surging", "gains", "gain", "profit", "profitable", "strong",
    "buy", "buying", "long", "hodl", "recovery", "ath", "green",
    "up", "uptrend", "optimistic", "confidence", "accumulate",
    "accumulation", "breakthrough", "record", "soar", "soaring",
    "rocket", "great", "good", "amazing", "excellent", "win",
}

NEGATIVE_WORDS = {
    "bear", "bearish", "crash", "dump", "dumping", "drop", "fall",
    "falling", "sell", "selling", "short", "loss", "losses", "fear",
    "panic", "down", "downtrend", "red", "correction", "collapse",
    "collapsing", "scam", "fraud", "hack", "bad", "terrible",
    "worried", "risky", "plunge", "plunging", "bleed", "bleeding",
    "rekt", "dead", "bubble",
}

# Strategy-related terms to surface as candidates for backtesting.
STRATEGY_TERMS = [
    "strategy", "strategies", "backtest", "backtesting", "back tested",
    "rsi", "sma", "ema", "macd", "momentum", "mean reversion",
    "mean-reversion", "dca", "dollar cost averaging", "dollar-cost averaging",
    "bollinger", "moving average", "breakout", "support", "resistance",
    "scalping", "swing trading", "swing trade", "trend following",
    "grid trading", "arbitrage", "hedge", "hedging", "position sizing",
    "stop loss", "take profit", "fibonacci", "volume profile",
    "on-chain", "onchain", "funding rate",
]


def score_sentiment(text):
    """Simple keyword sentiment in [-1, 1].

    (positive - negative) / (positive + negative), 0 if no keywords.
    """
    if not text:
        return 0.0
    words = re.findall(r"[a-z]+", text.lower())
    pos = sum(1 for w in words if w in POSITIVE_WORDS)
    neg = sum(1 for w in words if w in NEGATIVE_WORDS)
    # Also catch multi-word positive/negative phrases
    lowered = text.lower()
    if "all-time high" in lowered or "all time high" in lowered:
        pos += 1
    if "all-time low" in lowered:
        neg += 1
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 3)


def fetch_subreddit(subreddit, limit=20):
    """Fetch top posts (past day) from a public subreddit JSON feed.

    Returns a list of post dicts. Returns [] on any failure, with a
    warning printed, so one subreddit failing does not break the run.
    Respects rate limits: callers should sleep between subreddits.
    """
    last_error = None
    for url_template in URL_TEMPLATES:
        url = url_template.format(sub=subreddit, limit=limit)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    last_error = f"HTTP {resp.status} on {url}"
                    continue
                data = json.loads(resp.read().decode("utf-8"))
                break
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code} {e.reason} on {url}"
            if e.code == 429:
                print("[warn] rate limited (429) - back off and retry later")
                break
            continue
        except urllib.error.URLError as e:
            last_error = f"network error on {url}: {e}"
            continue
        except (json.JSONDecodeError, ValueError) as e:
            last_error = f"bad JSON on {url}: {e}"
            continue
    else:
        print(f"[warn] r/{subreddit}: all endpoints failed: {last_error}")
        return []
    if 'data' not in locals():
        print(f"[warn] r/{subreddit}: all endpoints failed: {last_error}")
        return []

    posts = []
    children = (data.get("data") or {}).get("children") or []
    for child in children:
        d = child.get("data") or {}
        title = d.get("title") or ""
        selftext = d.get("selftext") or ""
        combined = f"{title} {selftext}"
        posts.append({
            "subreddit": subreddit,
            "title": title,
            "selftext": selftext[:500],
            "url": d.get("url") or "",
            "permalink": "https://www.reddit.com" + (d.get("permalink") or ""),
            "score": d.get("score") or 0,
            "num_comments": d.get("num_comments") or 0,
            "created_utc": d.get("created_utc"),
            "sentiment": score_sentiment(combined),
        })
    return posts


def _term_in_text(term, text):
    """Match a strategy term, with word boundaries for short acronyms.

    Prevents e.g. 'rsi' matching inside 'reversion', or 'sma' inside
    other words. Longer phrases match as substrings.
    """
    if len(term) <= 4 and " " not in term and "-" not in term:
        return re.search(r"\b" + re.escape(term) + r"\b", text) is not None
    return term in text


def extract_strategy_ideas(posts):
    """Pull out posts mentioning strategy / indicator / trading terms.

    Returns candidates sorted by Reddit score, with matched terms listed.
    These are ideas to test in backtesting, not trading signals.
    """
    ideas = []
    for post in posts:
        text = f"{post.get('title', '')} {post.get('selftext', '')}".lower()
        matched = sorted({term for term in STRATEGY_TERMS if _term_in_text(term, text)})
        if not matched:
            continue
        ideas.append({
            "subreddit": post.get("subreddit"),
            "title": post.get("title"),
            "url": post.get("permalink") or post.get("url"),
            "matched_terms": matched,
            "sentiment": post.get("sentiment", 0.0),
            "score": post.get("score", 0),
            "num_comments": post.get("num_comments", 0),
        })
    # Most-discussed / highest-voted first
    ideas.sort(key=lambda x: (x["score"], x["num_comments"]), reverse=True)
    return ideas


def main():
    project_root = Path(__file__).resolve().parent.parent
    out_path = project_root / "data" / "reddit_sentiment.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_posts = []
    errors = []
    for i, sub in enumerate(SUBREDDITS):
        if i > 0:
            time.sleep(2)  # respect rate limits between requests
        posts = fetch_subreddit(sub, limit=20)
        if not posts:
            errors.append(f"r/{sub}: no posts fetched (see warnings above)")
        all_posts.extend(posts)

    if all_posts:
        overall = round(
            sum(p["sentiment"] for p in all_posts) / len(all_posts), 3
        )
    else:
        overall = 0.0

    # Top posts by Reddit score
    top_posts = sorted(
        all_posts, key=lambda p: (p["score"], p["num_comments"]), reverse=True
    )[:20]

    ideas = extract_strategy_ideas(all_posts)

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Reddit public JSON (unauthenticated, read-only)",
        "subreddits": SUBREDDITS,
        "total_posts": len(all_posts),
        "overall_sentiment_score": overall,
        "sentiment_scale": "-1 (very negative) to 1 (very positive), 0 = neutral / no keywords",
        "top_posts": top_posts,
        "candidate_strategy_ideas": ideas,
        "errors": errors,
    }

    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Wrote {out_path}")
    print(f"Total posts: {len(all_posts)}")
    print(f"Overall sentiment: {overall}")
    print(f"Strategy ideas found: {len(ideas)}")
    if errors:
        print(f"Errors: {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
