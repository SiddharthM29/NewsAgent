"""
app.py — Streamlit UI for NewsAI Agent
Run: streamlit run app.py
"""
import asyncio
import sys
import os

import streamlit as st

# ── Path setup ─────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from news_agent.agent import run_news_agent

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NewsAI",
    page_icon="🗞️",
    layout="centered",
)

# ── CSS ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

* { font-family: 'Inter', sans-serif; }
.stApp { background: #0f1117; color: #e8eaf0; }
#MainMenu, footer, header { visibility: hidden; }

/* Inputs */
.stTextInput > div > div > input,
.stSelectbox > div > div {
    background: #1a1d27 !important;
    border: 1px solid #2a2d3e !important;
    border-radius: 10px !important;
    color: #e8eaf0 !important;
}

/* Button */
.stButton > button {
    width: 100%;
    background: linear-gradient(135deg, #7c6af7, #5b8dee) !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 14px !important;
    font-weight: 600 !important;
    font-size: 15px !important;
    transition: all 0.2s ease !important;
}
.stButton > button:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 20px rgba(124,106,247,0.35) !important;
}

/* Digest card */
.digest-card {
    background: #1a1d27;
    border: 1px solid #2a2d3e;
    border-radius: 16px;
    padding: 28px 30px;
    margin-top: 24px;
    line-height: 1.75;
}
.digest-card h3 { color: #7c6af7; margin-bottom: 6px; font-size: 13px;
                  text-transform: uppercase; letter-spacing: 1.2px; }

/* Stats chips */
.stat-row { display: flex; gap: 12px; margin-top: 16px; flex-wrap: wrap; }
.chip {
    background: #1a1d27; border: 1px solid #2a2d3e;
    border-radius: 8px; padding: 6px 14px;
    font-size: 12px; color: #8b90a7;
}
.chip b { color: #e8eaf0; }

/* Step indicator */
.step {
    display: flex; align-items: center; gap: 10px;
    background: #13151c; border: 1px solid #1e2130;
    border-radius: 10px; padding: 10px 16px;
    margin-bottom: 8px; font-size: 13px; color: #8b90a7;
}
.step .icon { font-size: 18px; }
</style>
""", unsafe_allow_html=True)


# ── Header ─────────────────────────────────────────────────────────────────
st.markdown("""
<div style="text-align:center; padding: 32px 0 24px;">
  <div style="font-size:44px;">🗞️</div>
  <div style="font-size:28px; font-weight:700;
              background: linear-gradient(135deg, #7c6af7, #5b8dee);
              -webkit-background-clip: text; -webkit-text-fill-color: transparent;
              background-clip: text;">NewsAI</div>
  <div style="color:#5a5f7a; font-size:13px; margin-top:4px;">
      Powered by Google ADK + Gemini
  </div>
</div>
""", unsafe_allow_html=True)

# ── Inputs ─────────────────────────────────────────────────────────────────
col1, col2 = st.columns([2, 1])
with col1:
    topic = st.text_input(
        "Topic",
        placeholder='e.g. "AI", "cricket", "economy" — leave blank for top headlines',
        label_visibility="visible",
    )
with col2:
    date_filter = st.selectbox(
        "Date Range",
        options=["today", "yesterday", "last_3_days"],
        format_func=lambda x: {"today": "Today", "yesterday": "Yesterday",
                                "last_3_days": "Last 3 Days"}[x],
    )

run_btn = st.button("🔍 Get News", type="primary")

# ── Pipeline diagram (always visible) ──────────────────────────────────────
with st.expander("ℹ️ How it works", expanded=False):
    st.markdown("""
    <div class="step"><span class="icon">📡</span> <b>FetcherAgent</b> — calls RSS feeds, fetches raw articles</div>
    <div class="step"><span class="icon">🔎</span> <b>FilterAgent</b> — removes duplicates, keeps only topic-relevant stories</div>
    <div class="step"><span class="icon">✨</span> <b>SummarizerAgent</b> — writes your digest using Gemini</div>
    """, unsafe_allow_html=True)

# ── Run ─────────────────────────────────────────────────────────────────────
if run_btn:
    with st.spinner("🤖 Agents running… Fetch → Filter → Summarize"):
        try:
            result = asyncio.run(
                run_news_agent(
                    topic=topic.strip() or "all",
                    date_filter=date_filter,
                )
            )

            # Stats row
            st.markdown(f"""
            <div class="stat-row">
              <div class="chip">📰 <b>{result['raw_count']}</b> articles fetched</div>
              <div class="chip">🔎 <b>{result['filtered_count']}</b> after filter</div>
              <div class="chip">📅 {date_filter.replace('_', ' ').title()}</div>
              {"<div class='chip'>🏷️ " + topic + "</div>" if topic else ""}
            </div>
            """, unsafe_allow_html=True)

            # Digest
            st.markdown(f"""
            <div class="digest-card">
              <h3>✨ AI Digest</h3>
              {result['digest'].replace(chr(10), '<br>')}
            </div>
            """, unsafe_allow_html=True)

        except Exception as e:
            st.error(f"Something went wrong: {e}")
            st.info("Make sure your GOOGLE_API_KEY is set in the .env file.")
