"""
FIFA World Cup 2026 - Prediction League Dashboard
Run: streamlit run app.py

Nothing is hardcoded:
- Player names come from the sheet names of the uploaded prediction workbook.
- Fixtures (Team1 vs Team2 per match) are derived from that same workbook.
- Actual match results are entered/edited live in the UI (or uploaded as a
  small results file) and can be saved/loaded as JSON so they persist
  between sessions/matchdays.
"""
import hashlib
import json
from io import BytesIO
import os
from dotenv import load_dotenv
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

load_dotenv()

from scoring import (
    load_predictions,
    extract_fixtures,
    build_leaderboard,
    detailed_breakdown,
    CORRECT_RESULT_POINTS,
    EXACT_SCORE_POINTS,
)
from live_results import (
    fetch_world_cup_matches,
    matches_to_results,
    merge_into_actual_results,
)

st.set_page_config(page_title="WC26 Prediction League", page_icon="⚽", layout="wide")

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("⚽ WC26 Prediction League")

st.sidebar.markdown("### 1️⃣ Upload predictions")
uploaded = st.sidebar.file_uploader(
    "Combined prediction workbook (.xlsx)", type=["xlsx"], key="pred_upload"
)
uploaded_ind = st.sidebar.file_uploader(
    "Individual prediction files (CSV, Excel)", 
    type=["xlsx", "csv"], 
    accept_multiple_files=True, 
    key="pred_upload_ind"
)

individual_names = {}
if uploaded_ind:
    st.sidebar.markdown("**Assign names to uploaded files:**")
    for f in uploaded_ind:
        default_name = f.name.rsplit('.', 1)[0]
        individual_names[f.name] = st.sidebar.text_input(
            f"Name for '{f.name}'", 
            value=default_name, 
            key=f"name_{f.name}"
        )

all_uploads = []
if uploaded is not None:
    all_uploads.append(uploaded)
if uploaded_ind:
    all_uploads.extend(uploaded_ind)

if all_uploads:
    if st.sidebar.button("🔄 Re-parse file(s)", help="Clear cache and re-parse the uploaded files"):
        # Remove all cached player dicts from session state
        for k in list(st.session_state.keys()):
            if k.startswith("players_"):
                del st.session_state[k]
        st.rerun()


st.sidebar.markdown("### 2️⃣ Live Match Results")

# Load football-data.org API token from environment
api_token = os.getenv("FOOTBALL_DATA_API_TOKEN", "")

if st.sidebar.button("🔄 Refresh Live Scores", help="Fetch the latest match results from football-data.org"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### Scoring Rules")
st.sidebar.markdown(
    f"- Correct winner/draw: **{CORRECT_RESULT_POINTS} pts**\n"
    f"- Exact score: **{EXACT_SCORE_POINTS} pts** (instead of correct winner/draw points, not in addition)\n"
    "- Tie-break: more exact-score predictions wins; "
    "still tied → prize split equally"
)

st.title("🏆 FIFA World Cup 2026 — Prediction League")

if not all_uploads:
    st.info("👈 Upload prediction workbook or individual files to get started.")
    st.stop()

# ---------------------------------------------------------------------------
# Load predictions + derive fixtures (nothing hardcoded)
# Cache by combined file content hash so files are not re-parsed unnecessarily.
# ---------------------------------------------------------------------------
file_hash_components = []
for f in all_uploads:
    f.seek(0)
    file_hash_components.append(f.name)
    if f.name in individual_names:
        file_hash_components.append(individual_names[f.name])
    file_hash_components.append(hashlib.md5(f.read()).hexdigest())
    f.seek(0)
file_hash = hashlib.md5("".join(file_hash_components).encode()).hexdigest()
cache_key = f"players_{file_hash}"

if cache_key not in st.session_state:
    total_sheets_placeholder = st.empty()
    progress_bar = st.progress(0, text="Parsing predictions...")

    def _on_progress(done, total):
        pct = int(done / total * 100)
        progress_bar.progress(pct, text=f"Parsing sheets... {done}/{total}")

    with st.spinner("Parsing prediction files..."):
        all_players = {}
        for f in all_uploads:
            f.seek(0)
            players_dict = load_predictions(
                f,
                progress_callback=_on_progress,
            )
            
            # Apply custom names for individual files
            if uploaded_ind and f in uploaded_ind and f.name in individual_names:
                custom_name = individual_names[f.name]
                # If there's exactly one key (which happens for individual files), rename it
                if len(players_dict) == 1:
                    old_key = list(players_dict.keys())[0]
                    players_dict[custom_name] = players_dict.pop(old_key)
                    
            all_players.update(players_dict)

        st.session_state[cache_key] = all_players

    progress_bar.empty()
    total_sheets_placeholder.empty()

players = st.session_state[cache_key]

if players:
    with st.sidebar.expander("💾 Export Combined Predictions"):
        st.markdown("Download all predictions merged into a single workbook.")
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            for p_name, p_data in players.items():
                safe_name = str(p_name)[:31]
                rows = []
                for m_no, p_pred in p_data.get("predictions", {}).items():
                    rows.append({
                        "Match No.": m_no,
                        "Team 1": p_pred["team1"],
                        "Team 1 Score": p_pred["score1"],
                        "Team 2 Score": p_pred["score2"],
                        "Team 2": p_pred["team2"]
                    })
                df = pd.DataFrame(rows)
                if p_data.get("winning_team_pick"):
                    df = pd.concat([df, pd.DataFrame([{"Team 1": "Winning Team", "Team 1 Score": p_data["winning_team_pick"]}])], ignore_index=True)
                
                if not df.empty:
                    df.to_excel(writer, index=False, sheet_name=safe_name)
                else:
                    # Create empty sheet if no valid predictions
                    pd.DataFrame([{"Message": "No predictions parsed"}]).to_excel(writer, index=False, sheet_name=safe_name)
        
        excel_data = output.getvalue()
        
        st.download_button(
            label="⬇️ Download Combined.xlsx",
            data=excel_data,
            file_name="Combined_Predictions.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
# ---------------------------------------------------------------------------
# Debug panel (shows raw markdown + parsed predictions)
# ---------------------------------------------------------------------------
with st.expander("🔧 Debug: raw markdown & parsed predictions", expanded=False):
    try:
        from llm_parser import file_to_markdown, _split_sheets
        for f in all_uploads:
            f.seek(0)
            st.markdown(f"**File: {f.name}**")
            raw_md = file_to_markdown(f, filename=f.name)
            f.seek(0)
            st.markdown("**Raw MarkItDown output:**")
            st.code(raw_md[:5000], language="markdown")
            st.markdown("**Sheet splits detected:**")
            
            # Inject filename as sheet name if missing
            if "## " not in raw_md:
                import os
                base = os.path.splitext(f.name)[0] if f.name else "Player"
                raw_md = f"## {base}\n\n{raw_md}"
                
            sheets = _split_sheets(raw_md)
            for name, body in sheets:
                st.markdown(f"- `{name}` — {len(body)} chars")

    except Exception as e:
        st.error(f"Debug error: {e}")
    st.markdown("**Parsed players dict:**")
    st.json({k: {"predictions_count": len(v.get("predictions", {})),
                 "winning_team_pick": v.get("winning_team_pick"),
                 "error": v.get("_llm_error")}
             for k, v in players.items()})

if not players:
    st.error("Couldn't find any prediction tables in this workbook. "
             "Check that each sheet has a 'Match No.' header row.")
    st.stop()

fixtures, fixture_duplicates = extract_fixtures(players)

if fixture_duplicates:
    dup_msgs = [
        f"Match **{dup_no}** (same as Match {kept_no}): {t1} vs {t2}"
        for dup_no, t1, t2, kept_no in fixture_duplicates
    ]
    st.warning(
        "⚠️ **Duplicate fixtures detected and removed:**\n\n"
        + "\n\n".join(f"- {m}" for m in dup_msgs)
        + "\n\nPlease fix the match numbers in your Excel file."
    )


# ---------------------------------------------------------------------------
# Fetch and merge live results (Auto-fetched)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300) # 5 min cache
def fetch_and_merge_live_results(token, _fixtures):
    ar = {
        m: {"team1": f["team1"], "team2": f["team2"], "score1": None, "score2": None}
        for m, f in _fixtures.items()
    }
    if not token:
        return ar, 0, 0, "No API token provided."
    
    try:
        from live_results import fetch_world_cup_matches, matches_to_results, merge_into_actual_results
        raw_matches = fetch_world_cup_matches(token)
        api_results = matches_to_results(raw_matches)
        ar, unmatched = merge_into_actual_results(ar, api_results)
        return ar, len(raw_matches), unmatched, None
    except Exception as e:
        return ar, 0, 0, str(e)

# Only initialize actual_results when fixtures change (e.g. new file uploaded).
# Otherwise preserve existing session state so manual edits and web-fetched
# scores are not wiped out on every Streamlit rerun.
import hashlib as _hl
_fixtures_hash = _hl.md5(str(sorted(fixtures.items())).encode()).hexdigest()

if (
    "actual_results" not in st.session_state
    or st.session_state.get("_fixtures_hash") != _fixtures_hash
):
    _ar, fetched_count, unmatched_count, api_error = fetch_and_merge_live_results(
        api_token, fixtures
    )
    st.session_state.actual_results = _ar
    st.session_state._fixtures_hash = _fixtures_hash
else:
    fetched_count, unmatched_count, api_error = 0, 0, None

if api_error and api_token:
    st.sidebar.error(f"API fetch failed: {api_error}")
elif fetched_count > 0:
    filled = sum(
        1 for v in st.session_state.actual_results.values()
        if v["score1"] is not None and v["score2"] is not None
    )
    st.sidebar.success(
        f"Live results updated! "
        f"({filled} fixtures have scores)."
    )


# ---------------------------------------------------------------------------
# Section: Show Actual Results
# ---------------------------------------------------------------------------
st.subheader("📋 Enter / Edit Actual Results")
st.caption("Fixtures below are auto-fetched from football-data.org but can be manually edited. "
           "Any edits you make will instantly update the leaderboard.")

col1, col2 = st.columns(2)
with col1:
    actual_img = st.file_uploader("📸 Auto-Fill from Screenshot (OCR)", type=["png", "jpg", "jpeg"], help="Drag and drop or paste (Ctrl+V) an image.")
    st.caption("💡 **Tip**: You can take a screenshot, click the box above, and press **Ctrl+V** (or Cmd+V) to paste it directly!")

if actual_img and st.button("Extract Scores"):
    with st.spinner("Extracting scores using AI Vision..."):
        try:
            from llm_parser import file_to_markdown, _parse_sheet
            from live_results import _normalize
            NV_API_KEY = os.getenv("NVIDIA_API_KEY", "")
            actual_img.seek(0)
            md_text = file_to_markdown(actual_img, filename=actual_img.name, api_key=NV_API_KEY)
            
            res = _parse_sheet("actuals", md_text)
            new_preds = res.get("predictions", {})
            
            updates = 0
            for mn, data in new_preds.items():
                t1 = _normalize(data["team1"])
                t2 = _normalize(data["team2"])
                
                for am, ad in st.session_state.actual_results.items():
                    at1 = _normalize(ad["team1"])
                    at2 = _normalize(ad["team2"])
                    
                    if (t1 == at1 and t2 == at2) or (t1 in at1 and t2 in at2) or (at1 in t1 and at2 in t2):
                        st.session_state.actual_results[am]["score1"] = data["score1"]
                        st.session_state.actual_results[am]["score2"] = data["score2"]
                        updates += 1
                        break
            if updates > 0:
                st.success(f"Successfully extracted {updates} match scores from the screenshot!")
            else:
                st.warning("Found a table but couldn't match any teams to the fixtures.")
        except Exception as e:
            st.error(f"OCR failed: {e}")

with col2:
    st.markdown("**🌐 Fetch Missing Scores (Web Search)**")
    st.caption("Automatically find missing scores using a free sports API.")
    if st.button("Fetch Missing Scores"):
        with st.spinner("Searching the web for missing scores..."):
            from live_results import fetch_score_from_web
            updates = 0
            for am, ad in st.session_state.actual_results.items():
                if ad.get("score1") is None or ad.get("score2") is None:
                    s1, s2 = fetch_score_from_web(ad["team1"], ad["team2"])
                    if s1 is not None and s2 is not None:
                        st.session_state.actual_results[am]["score1"] = s1
                        st.session_state.actual_results[am]["score2"] = s2
                        updates += 1
            if updates > 0:
                st.success(f"Successfully fetched {updates} match scores from the web!")
                st.rerun()
            else:
                st.warning("Couldn't find any missing scores on the web.")

results_table = pd.DataFrame([
    {
        "Match No": m,
        "Team 1": d["team1"],
        "Score 1": d["score1"],
        "Score 2": d["score2"],
        "Team 2": d["team2"],
    }
    for m, d in sorted(st.session_state.actual_results.items())
])

edited_df = st.data_editor(
    results_table,
    use_container_width=True,
    hide_index=True,
    disabled=["Match No", "Team 1", "Team 2"],
)

# Update session state with manual edits
for _, row in edited_df.iterrows():
    m = row["Match No"]
    s1 = row["Score 1"]
    s2 = row["Score 2"]
    if pd.notna(s1) and pd.notna(s2):
        st.session_state.actual_results[m]["score1"] = int(s1)
        st.session_state.actual_results[m]["score2"] = int(s2)

played_count = sum(
    1 for v in st.session_state.actual_results.values()
    if v["score1"] is not None and v["score2"] is not None
)
total_fixtures = len(st.session_state.actual_results)

if played_count == 0:
    st.warning("No results entered yet — leaderboard will show 0 points for everyone "
                "until at least one match result is filled in above.")

st.caption(f"{played_count} / {total_fixtures} match results entered.")

# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
leaderboard = build_leaderboard(players, st.session_state.actual_results)
breakdowns = detailed_breakdown(players, st.session_state.actual_results)

st.subheader("🥇 Leaderboard")

def highlight_top3(row):
    if row["Rank"] == 1:
        return ["background-color:#FFD70033"] * len(row)
    elif row["Rank"] == 2:
        return ["background-color:#C0C0C033"] * len(row)
    elif row["Rank"] == 3:
        return ["background-color:#CD7F3233"] * len(row)
    return [""] * len(row)

if leaderboard.empty:
    st.info("No players found in the uploaded sheet.")
else:
    st.dataframe(
        leaderboard.style.apply(highlight_top3, axis=1),
        hide_index=True,
        use_container_width=True,
    )

# ---------------------------------------------------------------------------
# Final Output Image (shareable table)
# ---------------------------------------------------------------------------
st.subheader("🖼️ Shareable Points Table (Image)")

title_input = st.text_input("Title for the table image", value="Updated Points Table")

def render_table_image(df, title, columns, figsize=(7, None)):
    n = len(df)
    fig_height = 0.5 * n + 1.2
    if figsize[1] is None:
        figsize = (figsize[0], fig_height)
    fig, ax = plt.subplots(figsize=figsize)
    ax.axis("off")

    existing_cols = [c for c in columns if c in df.columns]
    display_df = df[existing_cols].copy()

    table = ax.table(
        cellText=display_df.values,
        colLabels=existing_cols,
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(13)
    table.scale(1, 1.6)

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#cccccc")
        if row == 0:
            cell.set_facecolor("#1f4e79")
            cell.set_text_props(color="white", weight="bold")
        else:
            cell.set_facecolor("#ffffff" if row % 2 else "#f2f2f2")
            if col == 0:
                cell.set_text_props(ha="left")

    ax.set_title(title, fontsize=15, weight="bold", pad=20)
    fig.tight_layout()
    return fig

if not leaderboard.empty:
    fig = render_table_image(leaderboard, title_input, ["Player", "Points", "Exact Scores", "Correct Outcomes"], figsize=(8, None))
    st.pyplot(fig)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    buf.seek(0)
    st.download_button(
        "📥 Download Points Table Image",
        data=buf,
        file_name="Points_Table.png",
        mime="image/png",
    )

# ---------------------------------------------------------------------------
# Per-player breakdown
# ---------------------------------------------------------------------------
if not leaderboard.empty:
    st.subheader("🔍 Per-Player Breakdown")
    selected = st.selectbox("Select player", leaderboard["Player"].tolist())

    bd = pd.DataFrame(breakdowns[selected])
    if not bd.empty:
        bd_display = bd[["match_no", "fixture", "predicted", "actual", "points"]]
        bd_display.columns = ["Match", "Fixture", "Predicted", "Actual", "Points"]
        st.dataframe(bd_display, hide_index=True, use_container_width=True)
        
        st.subheader("🖼️ Shareable Breakdown Image")
        breakdown_title = f"{selected} - Predictions Breakdown"
        fig_bd = render_table_image(bd_display, breakdown_title, ["Match", "Fixture", "Predicted", "Actual", "Points"], figsize=(9, None))
        st.pyplot(fig_bd)
        
        buf_bd = BytesIO()
        fig_bd.savefig(buf_bd, format="png", dpi=200, bbox_inches="tight")
        buf_bd.seek(0)
        st.download_button(
            "📥 Download Breakdown Image",
            data=buf_bd,
            file_name=f"{selected}_Breakdown.png",
            mime="image/png",
        )
    else:
        st.info("No scored matches yet for this player.")

    total = leaderboard.loc[leaderboard["Player"] == selected, "Points"].values[0]
    exact = leaderboard.loc[leaderboard["Player"] == selected, "Exact Scores"].values[0]
    st.metric("Total Points", int(total), f"{int(exact)} exact score(s)")

    # -------------------------------------------------------------------
    # Charts
    # -------------------------------------------------------------------
    st.subheader("📊 Points Distribution")
    chart_df = leaderboard.set_index("Player")["Points"]
    st.bar_chart(chart_df)
