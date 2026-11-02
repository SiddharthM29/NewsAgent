"""
news_agent/rss_tool.py
───────────────────────
Fetches articles via NewsAPI (topic searches) with RSS direct-keyword fallback.

Strategy:
  - topic != "all"  →  NewsAPI first (server-side search)
                        If no key / quota exceeded → RSS + direct keyword match
  - topic == "all"  →  RSS feeds, no topic filter (general headlines)
"""
from __future__ import annotations

import logging
import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone

import feedparser

logger = logging.getLogger("news_agent.rss_tool")

# ── RSS feeds ──────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # International
    ("BBC News",       "https://feeds.bbci.co.uk/news/rss.xml"),
    ("Reuters",        "https://feeds.reuters.com/reuters/topNews"),
    ("CNN",            "https://rss.cnn.com/rss/edition.rss"),
    ("Al Jazeera",     "https://www.aljazeera.com/xml/rss/all.xml"),
    
    # Technology / General
    ("TechCrunch",     "https://techcrunch.com/feed/"),
    ("The Verge",      "https://www.theverge.com/rss/index.xml"),
    ("Hacker News",    "https://hnrss.org/frontpage"),
    
    # India - English
    ("NDTV",           "https://feeds.feedburner.com/ndtvnews-top-stories"),
    ("The Hindu",      "https://www.thehindu.com/feeder/default.rss"),
    ("Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
    
    # India - Hindi
    ("NDTV India",     "https://ndtv.in/rss/top-stories"),
    ("Zee News Hindi", "https://zeenews.india.com/hindi/india.xml"),
    
    # India - Marathi
    ("ABP Majha",      "https://marathi.abplive.com/home/feed"),
    ("Loksatta",       "https://www.loksatta.com/feed/"),
]

MAX_PER_FEED = 20
MAX_TOTAL    = 60

# ── NewsAPI config ─────────────────────────────────────────────────────────
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
NEWSAPI_URL = "https://newsapi.org/v2/everything"


# ── Public tool function ───────────────────────────────────────────────────

def fetch_rss_articles(topic: str, date_filter: str) -> str:
    """
    Fetch recent news articles, filtered by topic and date.

    Args:
        topic: Topic or keyword, e.g. "AI", "cricket", "economy".
               Pass "all" for general top headlines.
        date_filter: One of "today", "yesterday", "last_3_days".

    Returns:
        Plain-text blocks (SOURCE / TITLE / DESC), separated by "---".
        First line is a metadata header describing the source used.
    """
    t0     = time.time()
    cutoff = _get_cutoff(date_filter)
    topic  = (topic or "all").strip()

    logger.info("[FETCH] topic=%r  date_filter=%r  cutoff=%s",
                topic, date_filter, cutoff.strftime("%Y-%m-%d %H:%M UTC"))

    if topic.lower() == "all":
        articles   = _fetch_rss_all(cutoff)
        source_tag = "RSS_ALL"
    else:
        articles = _fetch_from_newsapi(topic, cutoff)
        if articles is not None:
            source_tag = "NEWSAPI"
        else:
            articles   = _fetch_rss_keyword(topic, cutoff)
            source_tag = "RSS_FALLBACK"

    elapsed = time.time() - t0
    logger.info("[FETCH] done  source=%s  articles=%d  elapsed=%.2fs",
                source_tag, len(articles), elapsed)

    if not articles:
        return (
            f"FETCH_META: source={source_tag} | topic={topic} | count=0\n\n"
            f"No articles found for topic='{topic}' date_filter='{date_filter}'. "
            "The feeds may be slow or the date range too narrow."
        )

    header = f"FETCH_META: source={source_tag} | topic={topic} | count={len(articles)}\n\n"
    return header + "\n---\n".join(articles[:MAX_TOTAL])


# ── Fetch strategies ───────────────────────────────────────────────────────

def _fetch_from_newsapi(topic: str, cutoff: datetime) -> list[str] | None:
    """
    Query NewsAPI for topic-specific articles.
    Returns list of formatted article strings, or None if unavailable/quota exceeded.
    """
    if not NEWSAPI_KEY:
        logger.info("[NEWSAPI] No NEWSAPI_KEY set — will use RSS fallback")
        return None

    t0        = time.time()
    from_date = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info("[NEWSAPI] Requesting topic=%r  from=%s", topic, from_date)

    try:
        resp = requests.get(
            NEWSAPI_URL,
            params={
                "q":        topic,
                "from":     from_date,
                "sortBy":   "publishedAt",
                "language": "en",
                "pageSize": 30,
                "apiKey":   NEWSAPI_KEY,
            },
            timeout=10,
        )
        elapsed = time.time() - t0
        logger.info("[NEWSAPI] Response  status=%d  elapsed=%.2fs",
                    resp.status_code, elapsed)

        if resp.status_code == 401:
            logger.warning("[NEWSAPI] 401 Unauthorized — check NEWSAPI_KEY in .env")
            return None
        if resp.status_code == 429:
            logger.warning("[NEWSAPI] 429 Quota exceeded — falling back to RSS")
            return None
        if resp.status_code != 200:
            logger.warning("[NEWSAPI] Unexpected status %d — body: %s",
                           resp.status_code, resp.text[:200])
            return None

        data          = resp.json()
        total_results = data.get("totalResults", 0)
        logger.info("[NEWSAPI] totalResults=%d  page_size=%d",
                    total_results, len(data.get("articles", [])))

        articles = []
        removed  = 0
        for item in data.get("articles", []):
            title  = (item.get("title") or "").strip()
            source = item.get("source", {}).get("name", "NewsAPI")
            desc   = (item.get("description") or item.get("content") or "").strip()
            desc   = _strip_html(desc)[:300]
            if title and "[Removed]" not in title:
                articles.append(
                    f"SOURCE: {source}\n"
                    f"TITLE: {title}\n"
                    f"DESC: {desc}"
                )
            else:
                removed += 1

        logger.info("[NEWSAPI] Kept %d articles  (removed %d [Removed] entries)",
                    len(articles), removed)
        return articles if articles else None

    except requests.exceptions.Timeout:
        logger.error("[NEWSAPI] Timeout after %.2fs — falling back to RSS", time.time() - t0)
        return None
    except Exception as exc:
        logger.error("[NEWSAPI] Error after %.2fs: %s", time.time() - t0, exc)
        return None


def _fetch_rss_keyword(topic: str, cutoff: datetime) -> list[str]:
    """
    RSS fallback: direct keyword search only (no synonyms).
    Keeps articles whose title or description contain the topic word.
    """
    logger.info("[RSS_FALLBACK] Starting  topic=%r  (direct keyword match)", topic)
    articles:    list[str] = []
    topic_lower: str       = topic.lower()
    total_t0               = time.time()

    for source_name, feed_url in RSS_FEEDS:
        t0 = time.time()
        try:
            feed    = feedparser.parse(feed_url)
            elapsed = time.time() - t0
            entries = feed.entries[:MAX_PER_FEED]
            matched       = 0
            skipped_date  = 0
            skipped_topic = 0

            for entry in entries:
                pub = _parse_date(entry)
                if pub and pub < cutoff:
                    skipped_date += 1
                    continue

                title = (entry.get("title") or "").strip()
                desc  = _strip_html(
                    entry.get("summary") or entry.get("description") or ""
                )[:300]

                # Direct keyword match — no synonyms
                if topic_lower not in (title + " " + desc).lower():
                    skipped_topic += 1
                    continue

                if title:
                    articles.append(
                        f"SOURCE: {source_name}\n"
                        f"TITLE: {title}\n"
                        f"DESC: {desc}"
                    )
                    matched += 1

            logger.info("[RSS_FALLBACK] %-14s  total=%2d  matched=%2d  "
                        "skip_date=%2d  skip_topic=%2d  elapsed=%.2fs",
                        source_name, len(entries), matched,
                        skipped_date, skipped_topic, elapsed)

        except Exception as exc:
            logger.warning("[RSS_FALLBACK] %-14s  FAILED: %s", source_name, exc)
            continue

        if len(articles) >= MAX_TOTAL:
            break

    logger.info("[RSS_FALLBACK] Done  total_articles=%d  total_elapsed=%.2fs",
                len(articles), time.time() - total_t0)
    return articles[:MAX_TOTAL]


def _fetch_rss_all(cutoff: datetime) -> list[str]:
    """
    RSS fetch with date filter only — no topic filter.
    Used for topic == "all" (general headlines).
    """
    logger.info("[RSS_ALL] Starting general fetch (no topic filter)")
    articles: list[str] = []
    total_t0 = time.time()

    for source_name, feed_url in RSS_FEEDS:
        t0 = time.time()
        try:
            feed    = feedparser.parse(feed_url)
            elapsed = time.time() - t0
            added        = 0
            skipped_date = 0

            for entry in feed.entries[:MAX_PER_FEED]:
                pub = _parse_date(entry)
                if pub and pub < cutoff:
                    skipped_date += 1
                    continue
                title = (entry.get("title") or "").strip()
                desc  = _strip_html(
                    entry.get("summary") or entry.get("description") or ""
                )[:300]
                if title:
                    articles.append(
                        f"SOURCE: {source_name}\n"
                        f"TITLE: {title}\n"
                        f"DESC: {desc}"
                    )
                    added += 1

            logger.info("[RSS_ALL] %-14s  added=%2d  skip_date=%2d  elapsed=%.2fs",
                        source_name, added, skipped_date, elapsed)

        except Exception as exc:
            logger.warning("[RSS_ALL] %-14s  FAILED: %s", source_name, exc)
            continue

        if len(articles) >= MAX_TOTAL:
            break

    logger.info("[RSS_ALL] Done  total_articles=%d  total_elapsed=%.2fs",
                len(articles), time.time() - total_t0)
    return articles[:MAX_TOTAL]


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_cutoff(date_filter: str) -> datetime:
    now = datetime.now(tz=timezone.utc)
    if date_filter == "yesterday":
        base = now - timedelta(days=1)
        return base.replace(hour=0, minute=0, second=0, microsecond=0)
    if date_filter == "last_3_days":
        return now - timedelta(days=3)
    # Default: today
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_date(entry) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        val = getattr(entry, field, None) or entry.get(field)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()
