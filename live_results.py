"""
Live results fetcher - football-data.org v4 API
https://api.football-data.org/v4/matches

Free tier covers the FIFA World Cup (competition code "WC") with an
X-Auth-Token header. 10 requests/minute.

Nothing about teams/players is hardcoded here either - this module just
fetches whatever matches the API returns for the World Cup competition
and exposes them as a flat list. Matching those matches to the fixtures
extracted from the prediction sheet (extract_fixtures) is done with
fuzzy name matching to handle naming differences such as:
  "USA" (prediction sheet) vs "United States" (API)
  "South Korea" vs "Korea Republic"
"""
import requests

API_BASE = "https://api.football-data.org/v4"
COMPETITION_CODE = "WC"  # FIFA World Cup

# Common alternate names -> canonical forms used for matching.
# Add to this map if your prediction sheets use other naming conventions.
NAME_ALIASES = {
    "usa": "united states",
    "us": "united states",
    "south korea": "korea republic",
    "korea": "korea republic",
    "bosnia": "bosnia and herzegovina",
    "bosnia & herzegovina": "bosnia and herzegovina",
    "czech republic": "czechia",
    "ivory coast": "cote d'ivoire",
    "côte d'ivoire": "cote d'ivoire",
    "cte d'ivoire": "cote d'ivoire",
    "cape verde": "cabo verde",
    "curaçao": "curacao",
    "curaao": "curacao",
    "türkiye": "turkey",
    "trkiye": "turkey",
    "turkiye": "turkey",
    "holland": "netherlands",
    "iran": "ir iran",
}


def _normalize(name):
    n = (name or "").strip().lower()
    n = n.replace(".", "").replace("'", "'")
    return NAME_ALIASES.get(n, n)


def fetch_world_cup_matches(api_token, matchday=None, status=None):
    """Call GET /v4/matches for the World Cup competition.

    api_token: your football-data.org API key (X-Auth-Token header)
    matchday: optional int to filter by group-stage matchday
    status: optional status filter, e.g. "FINISHED", "SCHEDULED", "LIVE"

    Returns the raw list of match dicts from the API response.
    Raises requests.HTTPError on failure (e.g. bad/missing token,
    rate limit exceeded).
    """
    url = f"{API_BASE}/competitions/{COMPETITION_CODE}/matches"
    params = {}
    if matchday is not None:
        params["matchday"] = matchday
    if status is not None:
        params["status"] = status

    resp = requests.get(
        url,
        headers={"X-Auth-Token": api_token},
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("matches", [])


def matches_to_results(matches):
    """Convert raw API match dicts into a normalized list of
    {team1, team2, score1, score2, status, utc_date} entries,
    for finished matches only (others have score1/score2 = None).
    """
    out = []
    for m in matches:
        home = m.get("homeTeam", {}).get("name", "")
        away = m.get("awayTeam", {}).get("name", "")
        score = m.get("score", {}).get("fullTime", {})
        s1 = score.get("home")
        s2 = score.get("away")
        out.append({
            "team1": home,
            "team2": away,
            "score1": s1,
            "score2": s2,
            "status": m.get("status"),
            "utc_date": m.get("utcDate"),
        })
    return out


def merge_into_actual_results(actual_results, api_matches):
    """Fill in score1/score2 in `actual_results` (keyed by match_no, with
    team1/team2 from the prediction sheet) by fuzzy-matching against
    `api_matches` (output of matches_to_results).

    Mutates and returns `actual_results`. Matches with no corresponding
    finished API result are left unchanged (score1/score2 stay as-is,
    typically None).

    Returns (actual_results, unmatched_count) where unmatched_count is
    the number of fixtures that could not be matched to any API match.
    """
    # Build lookup: (norm_team1, norm_team2) -> api match
    lookup = {}
    for am in api_matches:
        key = (_normalize(am["team1"]), _normalize(am["team2"]))
        lookup[key] = am
        # also index the reversed pairing, in case home/away differ
        lookup[(key[1], key[0])] = {
            **am,
            "team1": am["team2"], "team2": am["team1"],
            "score1": am["score2"], "score2": am["score1"],
        }

    unmatched = 0
    for match_no, fixture in actual_results.items():
        key = (_normalize(fixture["team1"]), _normalize(fixture["team2"]))
        api_match = lookup.get(key)
        if api_match is None:
            unmatched += 1
            continue
        if api_match["score1"] is not None and api_match["score2"] is not None:
            fixture["score1"] = api_match["score1"]
            fixture["score2"] = api_match["score2"]

    return actual_results, unmatched


# TheSportsDB uses different team names than the prediction sheets.
# Map our normalized names -> TheSportsDB names.
SPORTSDB_ALIASES = {
    "czechia": "Czech Republic",
    "czech republic": "Czech Republic",
    "bosnia and herzegovina": "Bosnia-Herzegovina",
    "bosnia & herzegovina": "Bosnia-Herzegovina",
    "bosnia": "Bosnia-Herzegovina",
    "côte d'ivoire": "Ivory Coast",
    "cote d'ivoire": "Ivory Coast",
    "ivory coast": "Ivory Coast",
    "türkiye": "Turkey",
    "turkiye": "Turkey",
    "trkiye": "Turkey",
    "south korea": "South Korea",
    "korea republic": "South Korea",
    "usa": "USA",
    "united states": "USA",
    "cape verde": "Cape Verde",
    "cabo verde": "Cape Verde",
    "curacao": "Curacao",
    "curaçao": "Curacao",
    "curaao": "Curacao",
}


def _to_sportsdb_name(name):
    """Translate a team name to its TheSportsDB equivalent."""
    norm = (name or "").strip().lower()
    norm = norm.replace(".", "").replace("\u2019", "'").replace("\u00e9", "e")
    return SPORTSDB_ALIASES.get(norm, name.strip())


def _search_sportsdb(t1, t2):
    """Single search attempt against TheSportsDB. Returns (score1, score2, winner, method) or (None, None, None, None)."""
    url = "https://www.thesportsdb.com/api/v1/json/3/searchevents.php"
    resp = requests.get(url, params={"e": f"{t1} vs {t2}"}, timeout=15)
    resp.raise_for_status()
    res = resp.json()
    if res.get("event"):
        event = res["event"][0]
        s1_raw = event.get("intHomeScore")
        s2_raw = event.get("intAwayScore")
        if s1_raw is not None and s2_raw is not None:
            s1_int, s2_int = int(s1_raw), int(s2_raw)
            db_home = _normalize(event.get("strHomeTeam", ""))
            t1_norm = _normalize(t1)
            t2_norm = _normalize(t2)
            
            # Determine winner & method
            winner = None
            method = "90 mins"
            if s1_int > s2_int:
                winner = event.get("strHomeTeam", t1)
            elif s2_int > s1_int:
                winner = event.get("strAwayTeam", t2)
            # if scores are equal, we can't reliably infer winner/method without more data

            # If the result is stored reversed (t1=away, t2=home), flip the scores and winner
            if t2_norm in db_home and t1_norm not in db_home:
                # If t2 was home, then the returned winner name is fine, but s1, s2 order needs flip
                return s2_int, s1_int, winner, method
            
            return s1_int, s2_int, winner, method
    return None, None, None, None


def _search_espn(t1, t2):
    """Secondary fallback: Search against ESPN's public API. No API key needed."""
    url = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
    try:
        # Fetch all matches in the tournament window
        resp = requests.get(url, params={"dates": "20260611-20260719"}, timeout=15)
        resp.raise_for_status()
        events = resp.json().get("events", [])
        
        t1_norm = _normalize(t1)
        t2_norm = _normalize(t2)
        
        for e in events:
            comps = e.get("competitions", [])
            if not comps: continue
            competitors = comps[0].get("competitors", [])
            if len(competitors) != 2: continue
            
            c1, c2 = competitors[0], competitors[1]
            n1 = _normalize(c1.get("team", {}).get("name", ""))
            n2 = _normalize(c2.get("team", {}).get("name", ""))
            
            match_found = False
            c1_is_t1 = False
            
            if (t1_norm in n1 or n1 in t1_norm) and (t2_norm in n2 or n2 in t2_norm):
                match_found = True
                c1_is_t1 = True
            elif (t1_norm in n2 or n2 in t1_norm) and (t2_norm in n1 or n1 in t2_norm):
                match_found = True
                c1_is_t1 = False
                
            if match_found:
                if not e.get("status", {}).get("type", {}).get("completed", False):
                    continue # Match is not finished yet
                
                if c1.get("score") is None or c2.get("score") is None:
                    continue
                    
                s1_int = int(c1["score"]) if c1_is_t1 else int(c2["score"])
                s2_int = int(c2["score"]) if c1_is_t1 else int(c1["score"])
                
                winner = None
                method = "90 mins"
                
                if c1.get("winner"):
                    winner = c1.get("team", {}).get("name", "")
                elif c2.get("winner"):
                    winner = c2.get("team", {}).get("name", "")
                
                status_detail = e.get("status", {}).get("type", {}).get("detail", "")
                if "AET" in status_detail or "ET" in status_detail or "Extra" in status_detail:
                    method = "Extra Time"
                elif "Pen" in status_detail:
                    method = "Penalties"
                
                return s1_int, s2_int, winner, method
                
    except Exception as e:
        print(f"ESPN Error for {t1} vs {t2}: {e}")
        
    return None, None, None, None


def fetch_score_from_web(team1, team2):
    """Fallback: Fetch score via free web APIs. No API key needed.
    Tries multiple name aliases and both team orderings.
    Returns (score1, score2, winner, method) or (None, None, None, None).
    """
    db_t1 = _to_sportsdb_name(team1)
    db_t2 = _to_sportsdb_name(team2)

    try:
        # Attempt 1: normal order (TheSportsDB)
        s1, s2, w, m = _search_sportsdb(db_t1, db_t2)
        if s1 is not None:
            return s1, s2, w, m

        # Attempt 2: swapped order (TheSportsDB)
        s1, s2, w, m = _search_sportsdb(db_t2, db_t1)
        if s1 is not None:
            return s2, s1, w, m
            
    except Exception as e:
        print(f"TheSportsDB Error for {team1} vs {team2}: {e}")

    # Attempt 3: ESPN Public API Fallback
    s1, s2, w, m = _search_espn(team1, team2)
    if s1 is not None:
        return s1, s2, w, m

    return None, None, None, None
