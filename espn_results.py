"""
Live results fetcher - ESPN Hidden API
https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard

ESPN provides an open JSON endpoint for live sports scores. It requires no API key,
has virtually no rate limits, and does not block IP addresses from Cloud providers.
"""
import requests
from live_results import _normalize, merge_into_actual_results  # reuse normalization

ESPN_API_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"

def fetch_espn_matches():
    """Call GET on ESPN's scoreboard API to fetch live/finished World Cup matches."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}
    resp = requests.get(ESPN_API_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("events", [])

def espn_matches_to_results(events):
    """Convert raw ESPN event dicts into a normalized list of
    {team1, team2, score1, score2, status} entries.
    """
    out = []
    for event in events:
        # ESPN typically embeds match data inside 'competitions' array
        competitions = event.get("competitions", [])
        if not competitions:
            continue
            
        comp = competitions[0]
        status = comp.get("status", {}).get("type", {}).get("name", "")
        
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue
            
        # Extract home and away teams
        home = {}
        away = {}
        for c in competitors:
            if c.get("homeAway") == "home":
                home = c
            else:
                away = c
                
        # If home/away is not explicitly marked, just take first and second
        if not home or not away:
            home = competitors[0]
            away = competitors[1]

        t1_name = home.get("team", {}).get("name", "")
        t2_name = away.get("team", {}).get("name", "")
        
        # Parse scores
        s1_raw = home.get("score")
        s2_raw = away.get("score")
        
        s1 = None
        s2 = None
        if s1_raw is not None and s2_raw is not None:
            try:
                s1 = int(s1_raw)
                s2 = int(s2_raw)
            except (ValueError, TypeError):
                pass
                
        out.append({
            "team1": t1_name,
            "team2": t2_name,
            "score1": s1,
            "score2": s2,
            "status": status,
        })
    return out
