"""
xlsx prediction parser.

Pipeline
--------
xlsx → MarkItDown → markdown tables → direct column-detection parser

No LLM required. MarkItDown produces clean, structured markdown tables
that can be parsed directly by detecting column names.
"""
import io
import re

# ---------------------------------------------------------------------------
# Step 1: xlsx → markdown (via MarkItDown)
# ---------------------------------------------------------------------------

def file_to_markdown(file_data, filename: str = "", api_key: str = "") -> str:
    """
    Convert a file (xlsx, csv, image) to a markdown string using plain MarkItDown.
    """
    from markitdown import MarkItDown
    
    if api_key and api_key.strip():
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=api_key.strip()
            )
            md = MarkItDown(llm_client=client, llm_model="meta/llama-3.2-90b-vision-instruct")
        except Exception:
            md = MarkItDown()
    else:
        md = MarkItDown()

    ext = ""
    if filename and "." in filename:
        ext = "." + filename.split(".")[-1].lower()

    if isinstance(file_data, str):
        result = md.convert(file_data)
    elif isinstance(file_data, (bytes, bytearray)):
        result = md.convert_stream(
            io.BytesIO(file_data),
            file_extension=ext
        )
    else:
        if hasattr(file_data, "seek"):
            file_data.seek(0)
        raw = file_data.read()
        if hasattr(file_data, "seek"):
            file_data.seek(0)
        result = md.convert_stream(
            io.BytesIO(raw),
            file_extension=ext
        )

    return result.text_content


def _split_sheets(markdown: str) -> list:
    """
    Split a MarkItDown xlsx output into (sheet_name, markdown_chunk) pairs.
    Each sheet starts with a '## SheetName' heading.
    """
    if markdown.startswith("## "):
        markdown = "\n" + markdown

    parts = re.split(r"\n## ", markdown)
    sheets = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        lines = part.splitlines()
        name = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        sheets.append((name, body))
    return sheets


# ---------------------------------------------------------------------------
# Step 2: parse markdown tables directly (no LLM)
# ---------------------------------------------------------------------------

# Sheets to skip — summary/admin tabs, not player prediction sheets
_SKIP_SHEET_NAMES = {
    "points table", "summary", "leaderboard", "standings",
    "scoreboard", "results", "sheet1", "sheet2", "sheet3",
}

# Column name aliases → canonical name
_COL_ALIASES = {
    # match number
    "match no.": "match_no", "match no": "match_no", "match": "match_no",
    "match number": "match_no", "#": "match_no",
    # team 1
    "team 1": "team1", "team1": "team1", "home": "team1", "eam 1": "team1",
    # team 2
    "team 2": "team2", "team2": "team2", "away": "team2", "eam 2": "team2",
    # score 1  (both "Score 1" with space and "Score1" without)
    "team 1 score": "score1", "score 1": "score1", "score1": "score1",
    "goals 1": "score1", "team1 score": "score1", "eam 1 scor": "score1",
    "eam 1 score": "score1", "eam 1 scor ": "score1",
    # score 2
    "team 2 score": "score2", "score 2": "score2", "score2": "score2",
    "goals 2": "score2", "team2 score": "score2", "eam 2 scor": "score2",
    "eam 2 score": "score2", "eam 2 scor ": "score2",
    # winner
    "winner": "winner", "winning team": "winner", "match winner": "winner",
    # method
    "method": "method", "win method": "method", "winning method": "method",
    "method ": "method", " method": "method", "match victory method": "method",
}


def _parse_md_rows(md_body: str) -> list:
    """
    Parse all pipe-delimited table rows from a markdown body.
    Returns a list of lists (one per row), skipping separator lines (--- rows).
    """
    rows = []
    for line in md_body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        # Skip separator lines like | --- | --- |
        if re.match(r"^\|[\s\-|]+\|$", line):
            continue
        cells = [c.strip() for c in line.split("|")]
        # remove leading/trailing empty strings from split
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        rows.append(cells)
    return rows


def _is_player_sheet(name: str, body: str) -> bool:
    """Return True if this sheet looks like a player prediction sheet."""
    if name.strip().lower() in _SKIP_SHEET_NAMES:
        return False
    if not re.search(r"\d", body):
        return False
    return True


def _parse_sheet(sheet_name: str, sheet_body: str) -> dict:
    """
    Parse one sheet's markdown into predictions + winning_team_pick.

    Strategy
    --------
    1. Walk rows to find the "Winning Team" cell -> extract winner.
    2. Find ALL header rows (rows containing >= 2 recognised column names).
    3. For each header, parse data rows until the next header or end.
    4. Merge all predictions (supports multi-table sheets: group stage + RO32).
    """
    rows = _parse_md_rows(sheet_body)

    winner = None
    header_blocks = []  # list of (header_idx, col_map)

    # --- Pass 1: find winner row and ALL header rows ---
    for i, row in enumerate(rows):
        row_lower = [c.strip().lower() for c in row]

        # Winning team row: first cell is "winning team"
        if row_lower and row_lower[0] == "winning team":
            for cell in row[1:]:
                cl = cell.strip().lower()
                if cl and cl not in ("nan", "none", "") and not cl.startswith("unnamed"):
                    winner = cell.strip()
                    break

        # Header row: contains at least two recognised column names
        matches = sum(1 for c in row_lower if c in _COL_ALIASES)
        if matches >= 2:
            col_map = {}
            for j, cell in enumerate(row_lower):
                canonical = _COL_ALIASES.get(cell)
                if canonical and canonical not in col_map:
                    col_map[canonical] = j
            has_required = (
                {"team1", "team2", "score1", "score2"}.issubset(col_map) or
                ({"team1", "team2", "winner"}.issubset(col_map) and "score1" in col_map)
            )
            if has_required:
                header_blocks.append((i, col_map))

    if not header_blocks:
        return {"predictions": {}, "winning_team_pick": winner}

    # --- Pass 2: for each header block, parse its data rows ---
    preds = {}
    auto_match_no = 0

    for b_idx, (header_idx, col_map) in enumerate(header_blocks):
        next_header = header_blocks[b_idx + 1][0] if b_idx + 1 < len(header_blocks) else len(rows)
        data_rows = rows[header_idx + 1: next_header]

        for row in data_rows:
            max_col = max(col_map.values())
            if len(row) <= max_col:
                continue

            def cell(key, _row=row, _cm=col_map):
                idx = _cm.get(key)
                return _row[idx].strip() if idx is not None and idx < len(_row) else ""

            team1 = cell("team1")
            team2 = cell("team2")
            s1_raw = cell("score1")
            s2_raw = cell("score2")
            winner_val = cell("winner")
            method_val = cell("method")

            if not team1 or team1.lower() in ("nan", "none", ""):
                continue
            if not s1_raw or s1_raw.lower() in ("nan", "none", ""):
                continue
            if not s2_raw or s2_raw.lower() in ("nan", "none", ""):
                continue
            if winner_val.lower() in ("nan", "none"):
                winner_val = ""
            if method_val.lower() in ("nan", "none"):
                method_val = ""

            try:
                s1 = int(float(s1_raw))
                s2 = int(float(s2_raw))
            except (ValueError, TypeError):
                continue

            auto_match_no += 1
            preds[auto_match_no] = {
                "team1": team1,
                "team2": team2,
                "score1": s1,
                "score2": s2,
                "winner": winner_val,
                "method": method_val,
            }

    return {"predictions": preds, "winning_team_pick": winner}


# ---------------------------------------------------------------------------
# Step 3: main entry point
# ---------------------------------------------------------------------------

def parse_predictions(file_data, filename: str = "", api_key: str = "",
                      progress_callback=None) -> dict:
    """
    Parse a file workbook or individual file via MarkItDown → direct table parsing.

    Parameters
    ----------
    file_data : str | bytes | file-like
        Raw file data.
    filename : str
        Filename (used to determine extension and player name for individual files).
    api_key : str
        Used for NVIDIA Vision API OCR for image files.
    progress_callback : callable(done: int, total: int) | None
        Called after each sheet finishes.

    Returns
    -------
    dict
        {player_name: {"predictions": {match_no: {...}}, "winning_team_pick": str|None}}
    """
    # Step 1: file → markdown
    markdown = file_to_markdown(file_data, filename=filename, api_key=api_key)

    # Step 2: split into per-sheet chunks
    # If there are no ## headings, treat the entire file as one player's sheet,
    # naming it after the file.
    if "## " not in markdown:
        import os
        base = os.path.splitext(filename)[0] if filename else "Player"
        markdown = f"## {base}\n\n{markdown}"

    all_sheets = _split_sheets(markdown)
    sheets = [(name, body) for name, body in all_sheets
              if _is_player_sheet(name, body)]

    total = len(sheets)
    players = {}

    for i, (sheet_name, sheet_body) in enumerate(sheets, 1):
        players[sheet_name] = _parse_sheet(sheet_name, sheet_body)
        if progress_callback:
            progress_callback(i, total)

    return players
