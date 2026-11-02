"""
news_agent/agent.py
────────────────────
Three ADK agents wired into a SequentialAgent pipeline.

Pipeline:
  FetcherAgent → FilterAgent → SummarizerAgent

Mirrors the exact pattern from /Agents/sequential_agent.ipynb and
/Agents/multi_agent.ipynb.
"""
import logging
import os
import time
from dotenv import load_dotenv

from google.adk.agents import Agent, SequentialAgent
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.adk.tools import FunctionTool
from google.genai import types

from news_agent.rss_tool import fetch_rss_articles

# Load GOOGLE_API_KEY from .env
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("news_agent.agent")

# ── Retry config (same as your multi_agent.ipynb) ─────────────────────────
retry_config = types.HttpRetryOptions(
    attempts=5,
    exp_base=7,
    initial_delay=1,
    http_status_codes=[429, 500, 503, 504]
)

# ── Shared Gemini model ────────────────────────────────────────────────────
# Using flash-lite to stay within free-tier quota
_gemini = Gemini(model="gemini-2.5-flash-lite", retry_options=retry_config)

# ── Tool ───────────────────────────────────────────────────────────────────
rss_tool = FunctionTool(fetch_rss_articles)

# ── Agent 1: Fetcher ───────────────────────────────────────────────────────
fetcher_agent = Agent(
    name="FetcherAgent",
    model=_gemini,
    instruction="""You are a news fetcher agent.

The user will tell you a topic and a date range.
Your ONLY job is to call the fetch_rss_articles tool once with:
  - topic: the topic from the user (use "all" if no specific topic given)
  - date_filter: one of "today", "yesterday", "last_3_days" based on what the user asked

After the tool returns, output the raw result exactly as-is. Do not add anything.
""",
    tools=[rss_tool],
    output_key="raw_articles",
)

# ── Agent 2: Filter ────────────────────────────────────────────────────────
filter_agent = Agent(
    name="FilterAgent",
    model=_gemini,
    instruction="""You are a strict news filter agent.

The user's original search request is in the conversation above — it contains "Topic: X".
Extract that topic (X) and use it as the filter target.

Here are the raw fetched articles:
{raw_articles?}

Your rules:
1. If topic is "all": keep all articles, just remove exact duplicates.
2. If topic is specific (e.g. "cricket", "AI", "economy"):
   - Keep ONLY articles that explicitly mention that topic or are directly related to it.
   - If an article does NOT clearly mention the topic or a close synonym, REMOVE IT. No exceptions.
   - When in doubt → REMOVE the article.
   - Fewer accurate results is far better than including off-topic stories.
3. Remove duplicate stories (same event, multiple sources → keep the clearest one).
4. Return the surviving articles in the same SOURCE/TITLE/DESC format, separated by "---".
5. Do not summarize. Do not add commentary. Just return the filtered article list.
""",
    output_key="filtered_articles",
)

# ── Agent 3: Summarizer ────────────────────────────────────────────────────
summarizer_agent = Agent(
    name="SummarizerAgent",
    model=_gemini,
    instruction="""You are a news summarizer agent.

Here are the filtered articles:
{filtered_articles?}

Create a clean news digest in this exact format:

**📋 Overview**
<Write 2-3 sentences giving the big picture of today's news on the topic>

**📰 Top Stories**
• **[Source] Title**
  <Provide a detailed, well-rounded paragraph (around 50-60 words) summarizing this story. Include the who, what, where, when, and why. Ensure it's engaging and informative, similar to an 'Inshorts' style summary. Do not just write one sentence.>
• **[Source] Title**
  <Provide a detailed, well-rounded paragraph...>
(continue for all articles, max 15)

Be concise, factual, and easy to read.
""",
    output_key="digest",
)

# ── Root: SequentialAgent ──────────────────────────────────────────────────
root_agent = SequentialAgent(
    name="NewsAgent",
    sub_agents=[fetcher_agent, filter_agent, summarizer_agent],
)


# ── Runner helper (used by app.py) ─────────────────────────────────────────
async def run_news_agent(topic: str, date_filter: str) -> dict:
    """
    Run the full pipeline and return a dict with keys:
      raw_count, filtered_count, digest, source_tag
    """
    import uuid
    pipeline_t0 = time.time()
    # Must match the directory name ADK 1.22 infers from agent.py's module path
    runner     = InMemoryRunner(agent=root_agent, app_name="news_agent")
    user_id    = "news_user"
    session_id = str(uuid.uuid4())
    topic      = topic or "all"

    logger.info("[PIPELINE] ── Starting  topic=%r  date_filter=%r  session=%s",
                topic, date_filter, session_id[:8])

    query = (
        f"Fetch and summarize news. "
        f"Topic: {topic}. "
        f"Date range: {date_filter}."
    )

    # Create session — no initial state (ADK 1.22 bug: state param prevents session storage)
    await runner.session_service.create_session(
        app_name=runner.app_name,
        user_id=user_id,
        session_id=session_id,
    )
    
    # Initialize state manually to prevent "Context variable not found"
    session = await runner.session_service.get_session(
        app_name=runner.app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if session:
        session.state["raw_articles"] = ""
        session.state["filtered_articles"] = ""

    logger.info("[PIPELINE] Session created  app_name=%r", runner.app_name)

    # Consume all events — log each agent's output event for timing
    agent_times: dict[str, float] = {}
    last_agent: str | None        = None
    agent_start: float            = time.time()

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text=query)]
        ),
    ):
        author = getattr(event, "author", None)
        if author and author != last_agent:
            if last_agent:
                agent_times[last_agent] = time.time() - agent_start
                logger.info("[PIPELINE] %-16s done in %.2fs",
                            last_agent, agent_times[last_agent])
            last_agent  = author
            agent_start = time.time()
            logger.info("[PIPELINE] %-16s started", author)

    # Record time for the last agent
    if last_agent:
        agent_times[last_agent] = time.time() - agent_start
        logger.info("[PIPELINE] %-16s done in %.2fs",
                    last_agent, agent_times[last_agent])

    logger.info("[PIPELINE] run_async complete  total_wall=%.2fs",
                time.time() - pipeline_t0)

    # Read output_key values from session state
    session = await runner.session_service.get_session(
        app_name=runner.app_name,
        user_id=user_id,
        session_id=session_id,
    )

    state = session.state if session else {}

    raw_articles      = state.get("raw_articles", "")
    filtered_articles = state.get("filtered_articles", "")

    logger.info("[PIPELINE] State keys: %s", list(state.keys()))
    logger.info("[PIPELINE] raw_articles      chars=%d", len(raw_articles))
    logger.info("[PIPELINE] filtered_articles chars=%d", len(filtered_articles))
    logger.info("[PIPELINE] digest            chars=%d", len(state.get("digest", "")))

    # Parse FETCH_META header to get source tag
    source_tag = "RSS"
    raw_first_line = raw_articles.split("\n")[0] if raw_articles else ""
    if "NEWSAPI" in raw_first_line:
        source_tag = "NewsAPI"
    elif "RSS_FALLBACK" in raw_first_line:
        source_tag = "RSS (keyword filtered)"
    elif "RSS_ALL" in raw_first_line:
        source_tag = "RSS (all feeds)"

    def _count_articles(text: str) -> int:
        return len([b for b in text.split("---") if b.strip() and "FETCH_META" not in b])

    raw_c    = _count_articles(raw_articles)
    filter_c = _count_articles(filtered_articles)
    logger.info("[PIPELINE] ── Complete  raw=%d  filtered=%d  source=%s  total_wall=%.2fs",
                raw_c, filter_c, source_tag, time.time() - pipeline_t0)

    return {
        "raw_count":      raw_c,
        "filtered_count": filter_c,
        "digest":         state.get("digest", "\u26a0\ufe0f No digest was generated."),
        "source_tag":     source_tag,
    }
