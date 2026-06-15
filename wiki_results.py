"""
Wikipedia results scraper - MediaWiki Action API (no browser/Playwright needed)
https://en.wikipedia.org/w/api.php?action=parse&prop=wikitext

Content is CC-BY-SA licensed, fully compliant for reuse (with attribution
if redistributed publicly). This module fetches the wikitext of World Cup
2026 group-stage pages and parses the {{Football box}} /
{{Football box collapsible}} templates that Wikipedia editors use to record
match results.

Nothing about teams/players is hardcoded - this just returns whatever
finished matches it finds on the requested Wikipedia page(s). Matching
those to the fixtures from the prediction sheet is handled the same way
as live_results.py (fuzzy name matching via NAME_ALIASES).
"""
import re
import requests

from live_results import _normalize, merge_into_actual_results  # reuse normalization

WIKI_API = "https://en.wikipedia.org/w/api.php"

# 3-letter FIFA country codes -> full names, as used in {{fb|XXX}} templates
# on Wikipedia World Cup pages. Extend as needed.
FIFA_CODE_TO_NAME = {
    "MEX": "Mexico", "RSA": "South Africa", "KOR": "South Korea", "CZE": "Czechia",
    "CAN": "Canada", "BIH": "Bosnia and Herzegovina", "QAT": "Qatar", "SUI": "Switzerland",
    "USA": "USA", "PAR": "Paraguay", "BRA": "Brazil", "MAR": "Morocco",
    "HAI": "Haiti", "SCO": "Scotland", "AUS": "Australia", "TUR": "Turkey",
    "GER": "Germany", "CUW": "Curacao", "CIV": "Ivory Coast", "ECU": "Ecuador",
    "NED": "Netherlands", "JPN": "Japan", "SWE": "Sweden", "TUN": "Tunisia",
    "ESP": "Spain", "CPV": "Cape Verde", "BEL": "Belgium", "EGY": "Egypt",
    "KSA": "Saudi Arabia", "URU": "Uruguay", "IRN": "Iran", "NZL": "New Zealand",
    "FRA": "France", "SEN": "Senegal", "IRQ": "Iraq", "NOR": "Norway",
    "ARG": "Argentina", "ALG": "Algeria", "AUT": "Austria", "JOR": "Jordan",
    "POR": "Portugal", "COD": "DR Congo", "UZB": "Uzbekistan", "COL": "Colombia",
    "ENG": "England", "CRO": "Croatia", "GHA": "Ghana", "PAN": "Panama",
}


def _wikitext_to_name(token):
    """Resolve a team token from a {{Football box}} template (e.g.
    '{{fb|MEX}}', 'Mexico', '{{flagicon|MEX}} Mexico') to a plain team name.
    """
    token = token.strip()

    # {{fb|XXX}} or {{flagg|XXX}} style -> look up code
    m = re.search(r"\{\{\s*(?:fb|flagicon|flag|flagg|fbicon)\s*\|\s*([A-Za-z]{2,3})", token)
    if m:
        code = m.group(1).upper()
        if code in FIFA_CODE_TO_NAME:
            return FIFA_CODE_TO_NAME[code]

    # Strip any remaining templates/links/markup, keep plain text
    token = re.sub(r"\{\{.*?\}\}", "", token)
    token = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", token)
    token = token.strip(" '\n\t")
    return token


def fetch_page_wikitext(page_title, lang="en"):
    """Fetch raw wikitext for a Wikipedia page via the MediaWiki Action API.

    page_title: e.g. "2026_FIFA_World_Cup_Group_A"
    Returns the wikitext as a string.
    """
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "wikitext",
        "format": "json",
        "formatversion": 2,
    }
    resp = requests.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params=params,
        headers={"User-Agent": "WC26PredictionLeague/1.0 (educational use)"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise ValueError(f"Wikipedia API error: {data['error']}")
    return data["parse"]["wikitext"]


# Matches {{Football box ...}} or {{Football box collapsible ...}} blocks
_FOOTBALLBOX_RE = re.compile(
    r"\{\{\s*Football\s*box(?:\s*collapsible)?\s*(.*?)\n\}\}",
    re.IGNORECASE | re.DOTALL,
)

# Extracts |key = value pairs from a template body. Use [ \t]* (not \s*)
# after '=' so it doesn't swallow the newline for empty fields like
# "|report =\n", which would otherwise merge into the next field.
_FIELD_RE = re.compile(r"\|\s*([A-Za-z0-9_]+)\s*=[ \t]*(.*?)(?=\n\s*\||\Z)", re.DOTALL)

# Score like "2–0", "2-0", "2:0"
_SCORE_RE = re.compile(r"(\d+)\s*[–\-:]\s*(\d+)")


def parse_footballbox_matches(wikitext):
    """Parse all {{Football box}}/{{Football box collapsible}} templates in
    `wikitext` into a list of {team1, team2, score1, score2, date} dicts.

    Only matches with a parseable numeric score are returned (i.e. matches
    that have been played).
    """
    matches = []
    for block_match in _FOOTBALLBOX_RE.finditer(wikitext):
        body = block_match.group(1)
        fields = {}
        for fm in _FIELD_RE.finditer(body):
            key = fm.group(1).strip().lower()
            val = fm.group(2).strip()
            fields[key] = val

        team1_raw = fields.get("team1") or fields.get("home")
        team2_raw = fields.get("team2") or fields.get("away")
        score_raw = fields.get("score") or fields.get("result")
        date_raw = fields.get("date", "")

        if not team1_raw or not team2_raw or not score_raw:
            continue

        score_m = _SCORE_RE.search(score_raw)
        if not score_m:
            continue  # not yet played (e.g. score = "v" or blank)

        matches.append({
            "team1": _wikitext_to_name(team1_raw),
            "team2": _wikitext_to_name(team2_raw),
            "score1": int(score_m.group(1)),
            "score2": int(score_m.group(2)),
            "date": date_raw,
        })

    return matches


def fetch_group_results(group_pages, lang="en"):
    """Fetch and parse multiple Wikipedia group pages.

    group_pages: list of page titles, e.g.
        ["2026_FIFA_World_Cup_Group_A", "2026_FIFA_World_Cup_Group_B", ...]

    Returns a flat list of match dicts (see parse_footballbox_matches).
    """
    all_matches = []
    for page in group_pages:
        wikitext = fetch_page_wikitext(page, lang=lang)
        all_matches.extend(parse_footballbox_matches(wikitext))
    return all_matches


def merge_wiki_into_actual_results(actual_results, wiki_matches):
    """Same fuzzy-matching merge as live_results.merge_into_actual_results,
    reused here for Wikipedia-sourced matches.
    """
    # wiki_matches already have the same shape (team1/team2/score1/score2)
    # expected by merge_into_actual_results, plus an extra "date" key which
    # is harmless there.
    return merge_into_actual_results(actual_results, wiki_matches)
