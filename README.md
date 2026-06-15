# WC26 Prediction League — Streamlit App

Fully dynamic — **nothing is hardcoded** except the scoring rules and team
name aliases used for matching.

## Files
- `app.py` — Streamlit dashboard
- `scoring.py` — generic scoring engine
- `live_results.py` — football-data.org API integration
- `requirements.txt` — dependencies

## How it works
1. **Upload the combined prediction workbook** (.xlsx). Player names = sheet
   names in the file. Fixtures (Team1 vs Team2 per match) are read directly
   from the predictions — auto-detected, not hardcoded.
2. **Get actual results** — three options:
   - **Fetch live (recommended)**: enter your free football-data.org API key
     in the sidebar's "Fetch live results" panel, optionally filter by
     matchday, and click **Fetch results from API**. This calls
     `GET https://api.football-data.org/v4/competitions/WC/matches` and
     auto-fills scores for any fixtures it can match (team names are
     normalized — e.g. "USA" ↔ "United States", "South Korea" ↔ "Korea
     Republic" — see `NAME_ALIASES` in `live_results.py`).
   - **Upload a results file**: `.json` (`{"1": {"score1": 2, "score2": 0}, ...}`)
     or `.csv` with columns `Match No, Score1, Score2`.
   - **Manual entry**: edit the results table directly in the UI.
3. Leaderboard, shareable PNG table, per-player breakdown, and chart update
   live as results are filled in.
4. Use **Save these results (JSON)** to download entered/fetched results for
   re-use next matchday.

## Getting a football-data.org API key
Sign up free at https://www.football-data.org/client/register — the free
tier includes the FIFA World Cup (`WC`) competition at 10 requests/minute.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud
1. Push these files to a GitHub repo.
2. share.streamlit.io → New app → point to `app.py`.
3. (Optional) store your API token as a Streamlit secret instead of typing
   it each session.

## Scoring Rules (configurable in `scoring.py`)
- Correct winner/draw prediction: **2 pts**
- Exact score prediction: **5 pts** (instead of correct winner/draw points, not in addition)
- Tie-break: most exact-score predictions wins; if still tied, prize split
  equally.

