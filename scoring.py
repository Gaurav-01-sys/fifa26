"""
FIFA World Cup 2026 Prediction League - Scoring Engine
Fully generic: no player names, fixtures, or results are hardcoded.
Everything is derived from the uploaded prediction workbook and the
actual-results input supplied at runtime (via the Streamlit UI).
"""
import pandas as pd

# Scoring rule constants (only the RULES are fixed, not the data)
CORRECT_RESULT_POINTS = 2   # winner correctly predicted
CORRECT_METHOD_POINTS = 2   # correct method prediction
EXACT_SCORE_POINTS = 5      # exact score correctly predicted


def _outcome(s1, s2):
    """Return 'team1', 'team2', or 'draw'."""
    if s1 > s2:
        return "team1"
    elif s2 > s1:
        return "team2"
    return "draw"


def _normalize_method(m):
    """Normalize method string to handle abbreviations like ET or Pens."""
    m = str(m).strip().lower()
    if m in ("et", "extra time", "aet"):
        return "extra time"
    if m in ("pens", "penalties", "p", "pen"):
        return "penalties"
    if m in ("90 mins", "90", "90 m", "regular", "rt", "ft"):
        return "90 mins"
    return m


def _seek0(f):
    """Seek a file-like stream back to the start (no-op for plain paths)."""
    if hasattr(f, "seek"):
        try:
            f.seek(0)
        except Exception:
            pass


def load_predictions(xlsx_path, nvidia_api_key: str = "", progress_callback=None):
    """Load every player's sheet from the combined predictions workbook.

    Player names are taken from the sheet names of the uploaded file.

    Parsing priority
    ----------------
    1. LLM parser (llm_parser.parse_xlsx_with_llm) — when *nvidia_api_key*
       is provided.  Handles any layout without brittle column-detection.
    2. MarkItDown — converts xlsx → markdown tables, then regex-parses them.
    3. Pandas fallback — direct cell-scan with openpyxl via pandas.ExcelFile.
    """
    if xlsx_path is None:
        return {}

    # 1. llm_parser (most robust — handles multi-table sheets, any layout)
    # -------------------------------------------------------------------
    try:
        from llm_parser import parse_predictions
        _seek0(xlsx_path)
        filename = getattr(xlsx_path, "name", "")
        result = parse_predictions(
            xlsx_path,
            filename=filename,
            api_key=nvidia_api_key.strip() if nvidia_api_key else "",
            progress_callback=progress_callback,
        )
        # Only accept if at least one player has actual predictions
        if result and any(v["predictions"] for v in result.values()):
            return result
    except Exception:
        pass  # fall through to next method


    _seek0(xlsx_path)
    try:
        from markitdown import MarkItDown
        import io
        import re

        md = MarkItDown()
        
        # Check if xlsx_path is a path string or a file-like stream
        if isinstance(xlsx_path, str):
            result = md.convert(xlsx_path)
        else:
            # It's a file-like stream (e.g. Streamlit UploadedFile)
            # Make sure we read it as a binary stream
            if hasattr(xlsx_path, "seek"):
                xlsx_path.seek(0)
            stream_data = io.BytesIO(xlsx_path.read())
            # restore stream position just in case pandas fallback needs it
            if hasattr(xlsx_path, "seek"):
                xlsx_path.seek(0)
            result = md.convert_stream(stream_data, mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            
        text = result.text_content
        
        # Parse markitdown output
        # Split by sheet header '## '
        sheets = text.split("\n## ")
        # if the first sheet starts at the beginning of the file, it might start with '## ' without '\n'
        if len(sheets) == 1 and text.startswith("## "):
            sheets = text.split("## ")
        elif len(sheets) > 1 and text.startswith("## "):
            first_parts = sheets[0].split("## ")
            if len(first_parts) > 1:
                sheets = first_parts[1:] + sheets[1:]

        players = {}
        for sheet in sheets:
            if not sheet.strip():
                continue
            lines = [line.strip() for line in sheet.split("\n") if line.strip()]
            if not lines:
                continue
            
            player_name = lines[0].replace("## ", "").strip()
            preds = {}
            winner = None
            
            # Locate all markdown table rows
            table_rows = []
            for line in lines[1:]:
                if line.startswith("|"):
                    table_rows.append(line)
                    
            header_index = -1
            headers = []
            
            # Find header row containing "Match No." or similar
            for i, row in enumerate(table_rows):
                cells = [c.strip() for c in row.split("|")[1:-1]]
                if any("match" in c.lower() and "no" in c.lower() for c in cells):
                    header_index = i
                    headers = cells
                    break
                    
            if header_index == -1:
                # Fallback: find by "team 1" and "team 2"
                for i, row in enumerate(table_rows):
                    cells = [c.strip() for c in row.split("|")[1:-1]]
                    has_t1 = any("team 1" in c.lower() or "team1" in c.lower() for c in cells)
                    has_t2 = any("team 2" in c.lower() or "team2" in c.lower() for c in cells)
                    if has_t1 and has_t2:
                        header_index = i
                        headers = cells
                        break
                        
            if header_index != -1:
                # Map column indices
                col_match = -1
                col_t1 = -1
                col_t2 = -1
                col_s1 = -1
                col_s2 = -1
                col_winner = -1
                col_method = -1
                
                for idx, h in enumerate(headers):
                    h_lower = h.lower()
                    if "match" in h_lower and "no" in h_lower:
                        col_match = idx
                    elif ("team 1" in h_lower or "team1" in h_lower) and "score" not in h_lower:
                        col_t1 = idx
                    elif ("team 2" in h_lower or "team2" in h_lower) and "score" not in h_lower:
                        col_t2 = idx
                    elif "score 1" in h_lower or "score1" in h_lower or (("team 1" in h_lower or "team1" in h_lower) and "score" in h_lower):
                        col_s1 = idx
                    elif "score 2" in h_lower or "score2" in h_lower or (("team 2" in h_lower or "team2" in h_lower) and "score" in h_lower):
                        col_s2 = idx
                    elif "winner" in h_lower or "winning team" in h_lower or "match winner" in h_lower:
                        col_winner = idx
                    elif "method" in h_lower or "win method" in h_lower:
                        col_method = idx
                        
                # Determine data start offset
                start_row_offset = 1
                if header_index + 1 < len(table_rows):
                    next_row = table_rows[header_index + 1]
                    if all(c in "-:| " for c in next_row.replace("|", "").strip()):
                        start_row_offset = 2
                        
                auto_mn = 0
                for row in table_rows[header_index + start_row_offset:]:
                    cells = [c.strip() for c in row.split("|")[1:-1]]
                    if len(cells) <= max(col_t1, col_t2, col_s1, col_s2):
                        continue
                    try:
                        t1 = cells[col_t1].strip()
                        t2 = cells[col_t2].strip()
                        s1 = int(cells[col_s1].strip()) if cells[col_s1].strip().isdigit() else None
                        s2 = int(cells[col_s2].strip()) if cells[col_s2].strip().isdigit() else None
                        w_val = cells[col_winner].strip() if col_winner != -1 and col_winner < len(cells) else ""
                        m_val = cells[col_method].strip() if col_method != -1 and col_method < len(cells) else ""
                        
                        if s1 is not None and s2 is not None:
                            auto_mn += 1
                            match_no = auto_mn
                            preds[match_no] = {
                                "team1": t1,
                                "team2": t2,
                                "score1": s1,
                                "score2": s2,
                                "winner": w_val,
                                "method": m_val
                            }
                    except Exception:
                        pass
                        
            # Look for winning team prediction
            for line in lines[1:]:
                line_lower = line.lower()
                if "winning team" in line_lower or "champion" in line_lower:
                    parts = [p.strip() for p in line.split("|") if p.strip()]
                    for idx, part in enumerate(parts):
                        if "winning team" in part.lower() or "champion" in part.lower():
                            if idx + 1 < len(parts):
                                winner = parts[idx + 1]
                                winner = re.sub(r'[\*\`\_]', '', winner).strip()
                                if winner.lower() in ("nan", ""):
                                    winner = None
                                break
                    if winner:
                        break
                        
            players[player_name] = {"predictions": preds, "winning_team_pick": winner}
            
        if players:
            return players
            
    except Exception:
        pass

    # Fallback to search-based pandas parsing
    _seek0(xlsx_path)
    try:
        xl = pd.ExcelFile(xlsx_path)
        players = {}
        for sheet in xl.sheet_names:
            try:
                raw = pd.read_excel(xl, sheet_name=sheet, header=None)
                
                # Scan ALL rows to find ALL header rows (handles multi-table sheets:
                # e.g. group-stage table + RO32 table in the same sheet).
                def _is_table_header(row_vals):
                    """True if this row looks like a match prediction header."""
                    # Strip whitespace from all values before checking
                    lv = [str(v).strip().lower() for v in row_vals if str(v).strip() not in ("", "nan")]
                    has_team = any(
                        v.strip() in ("team 1", "team1", "team 2", "team2") or
                        (("team" in v) and ("1" in v or "2" in v))
                        for v in lv
                    )
                    has_score = any("score" in v for v in lv)
                    has_match = any("match" in v for v in lv)
                    has_winner = any(v.strip() == "winner" for v in lv)
                    has_method = any("method" in v for v in lv)
                    # A valid header needs teams + (scores OR match# OR winner/method)
                    return has_team and (has_score or has_match or has_winner or has_method)

                header_candidates = [
                    r for r in range(len(raw))
                    if _is_table_header(raw.iloc[r].tolist())
                ]

                if not header_candidates:
                    continue

                preds = {}

                for h_idx in header_candidates:
                    hdr = [str(v).strip() for v in raw.iloc[h_idx].tolist()]
                    sub = raw.iloc[h_idx + 1:].copy()
                    sub.columns = hdr

                    c_t1 = c_t2 = c_s1 = c_s2 = c_winner = c_method = None
                    for col in hdr:
                        cl = col.strip().lower()
                        if ("team 1" in cl or "team1" in cl) and "scor" not in cl:
                            c_t1 = col
                        elif ("team 2" in cl or "team2" in cl) and "scor" not in cl:
                            c_t2 = col
                        elif "score 1" in cl or "score1" in cl or \
                             (("team 1" in cl or "team1" in cl) and "score" in cl):
                            c_s1 = col
                        elif "score 2" in cl or "score2" in cl or \
                             (("team 2" in cl or "team2" in cl) and "score" in cl):
                            c_s2 = col
                        elif "winner" in cl:
                            c_winner = col
                        elif "method" in cl:
                            c_method = col

                    if not (c_t1 and c_t2):
                        continue  # not a match table
                    if not (c_s1 and c_s2) and not c_winner:
                        continue

                    sub = sub.dropna(subset=[c_t1], how='all')
                    for _, row in sub.iterrows():
                        try:
                            t1 = str(row[c_t1]).strip()
                            t2 = str(row[c_t2]).strip()
                            if not t1 or t1.lower() in ("nan", "none", ""):
                                continue
                            if not t2 or t2.lower() in ("nan", "none", ""):
                                continue

                            s1_raw = row[c_s1] if c_s1 and pd.notna(row.get(c_s1)) else None
                            s2_raw = row[c_s2] if c_s2 and pd.notna(row.get(c_s2)) else None
                            if s1_raw is None or s2_raw is None:
                                continue

                            w_val = str(row[c_winner]).strip() if c_winner and pd.notna(row.get(c_winner)) else ""
                            m_val = str(row[c_method]).strip() if c_method and pd.notna(row.get(c_method)) else ""
                            if w_val.lower() in ("nan", "none"):
                                w_val = ""
                            if m_val.lower() in ("nan", "none"):
                                m_val = ""

                            match_key = len(preds) + 1
                            preds[match_key] = {
                                "team1": t1,
                                "team2": t2,
                                "score1": int(float(str(s1_raw))),
                                "score2": int(float(str(s2_raw))),
                                "winner": w_val,
                                "method": m_val,
                            }
                        except Exception:
                            pass

                # Predicted overall winner search
                winner = None
                for r_idx in range(len(raw)):
                    for c_idx in range(len(raw.columns)):
                        val = str(raw.iloc[r_idx, c_idx]).strip().lower()
                        if "winning team" in val or "champion" in val:
                            w_val = raw.iloc[r_idx, c_idx + 1] if c_idx + 1 < len(raw.columns) else None
                            if w_val is not None and pd.notna(w_val) and str(w_val).strip() not in ("", "nan"):
                                winner = str(w_val).strip()
                                break
                    if winner:
                        break

                players[sheet] = {"predictions": preds, "winning_team_pick": winner}
            except Exception:
                pass

        return players
    except Exception:
        return {}


def extract_fixtures(players):
    """Derive the canonical fixture list (match_no -> team1/team2) by
    scanning all players' predictions. Uses the most common team1/team2
    pairing seen for each match number, so it still works if one sheet
    has a typo or a missing row.

    Deduplicates: if the same (team1, team2) pair appears under multiple
    match numbers, only the lowest match number is kept. The app can display
    a warning for the removed duplicates.

    Returns (fixtures_dict, duplicate_warnings_list).
    For backwards compatibility a plain call to extract_fixtures() still works;
    callers that want duplicate info should unpack two values.
    """
    from collections import Counter

    fixture_votes = {}
    for data in players.values():
        for match_no, pred in data["predictions"].items():
            key = (pred["team1"], pred["team2"])
            fixture_votes.setdefault(match_no, Counter())[key] += 1

    # Pick the most-common pairing for each match number
    raw = {}
    for match_no, counter in fixture_votes.items():
        (team1, team2), _ = counter.most_common(1)[0]
        raw[match_no] = {"team1": team1, "team2": team2}

    # Deduplicate: collapse identical (team1, team2) pairs to the first match_no
    seen_pairs = {}   # (team1, team2) -> first match_no seen
    duplicates = []   # list of (removed_match_no, team1, team2, kept_match_no)
    fixtures = {}
    for match_no in sorted(raw.keys()):
        entry = raw[match_no]
        pair = (entry["team1"], entry["team2"])
        if pair in seen_pairs:
            duplicates.append((match_no, entry["team1"], entry["team2"], seen_pairs[pair]))
        else:
            seen_pairs[pair] = match_no
            fixtures[match_no] = entry

    return fixtures, duplicates



def score_player(predictions, actual_results):
    """Score a single player's predictions against actual_results.

    actual_results: dict {match_no: {"team1":..., "team2":...,
                                       "score1": int|None, "score2": int|None}}
                     Only matches with non-None scores are scored.

    Returns dict with total_points, exact_scores (count), and per-match breakdown.
    """
    total = 0
    exact_count = 0
    correct_result_count = 0
    breakdown = []

    for match_no, actual in actual_results.items():
        if actual.get("score1") is None or actual.get("score2") is None:
            continue  # match not yet played / result not entered

        from live_results import _normalize
        act_t1 = _normalize(actual.get("team1", ""))
        act_t2 = _normalize(actual.get("team2", ""))

        pred_found = False
        pred_s1, pred_s2 = None, None
        pred_w, pred_m = None, None

        # 1. Try looking up by match_no first, but VERIFY the teams match
        pred = predictions.get(match_no)
        if pred is not None:
            pred_t1 = _normalize(pred.get("team1", ""))
            pred_t2 = _normalize(pred.get("team2", ""))
            
            if (pred_t1 == act_t1 or act_t1 in pred_t1 or pred_t1 in act_t1) and \
               (pred_t2 == act_t2 or act_t2 in pred_t2 or pred_t2 in act_t2):
                pred_s1, pred_s2 = pred["score1"], pred["score2"]
                pred_w, pred_m = pred.get("winner", ""), pred.get("method", "")
                pred_found = True
            elif (pred_t1 == act_t2 or act_t2 in pred_t1 or pred_t1 in act_t2) and \
                 (pred_t2 == act_t1 or act_t1 in pred_t2 or pred_t2 in act_t1):
                # Swapped order
                pred_s1, pred_s2 = pred["score2"], pred["score1"]
                pred_w, pred_m = pred.get("winner", ""), pred.get("method", "")
                pred_found = True

        # 2. If match_no had different teams (or was missing), search ALL predictions
        if not pred_found:
            for p_match_no, p_pred in predictions.items():
                p_t1 = _normalize(p_pred.get("team1", ""))
                p_t2 = _normalize(p_pred.get("team2", ""))
                
                if (p_t1 == act_t1 or act_t1 in p_t1 or p_t1 in act_t1) and \
                   (p_t2 == act_t2 or act_t2 in p_t2 or p_t2 in act_t2):
                    pred_s1, pred_s2 = p_pred["score1"], p_pred["score2"]
                    pred_w, pred_m = p_pred.get("winner", ""), p_pred.get("method", "")
                    pred_found = True
                    break
                elif (p_t1 == act_t2 or act_t2 in p_t1 or p_t1 in act_t2) and \
                     (p_t2 == act_t1 or act_t1 in p_t2 or p_t2 in act_t1):
                    # Swapped order
                    pred_s1, pred_s2 = p_pred["score2"], p_pred["score1"]
                    pred_w, pred_m = p_pred.get("winner", ""), p_pred.get("method", "")
                    pred_found = True
                    break

        if not pred_found:
            breakdown.append({
                "match_no": match_no,
                "fixture": f"{actual['team1']} vs {actual['team2']}",
                "predicted": "—",
                "actual": f"{actual['score1']}-{actual['score2']}",
                "points": 0,
                "exact": False,
                "result_correct": False,
            })
            continue

        points = 0
        exact = False
        result_correct = False

        if pred_s1 == actual["score1"] and pred_s2 == actual["score2"]:
            points += EXACT_SCORE_POINTS
            exact = True
            exact_count += 1

        # Check Winner
        actual_winner = actual.get("winner", "")
        act_w = _normalize(actual_winner)
        pr_w = _normalize(pred_w) if pred_w else ""
        
        # If user didn't enter actual winner in the table but scores are unequal, infer it for convenience
        if not act_w and actual["score1"] != actual["score2"]:
            act_w = act_t1 if actual["score1"] > actual["score2"] else act_t2
            
        if pr_w and act_w and (pr_w == act_w or act_w in pr_w or pr_w in act_w):
            points += CORRECT_RESULT_POINTS
            result_correct = True
            correct_result_count += 1

        # Check Method
        act_m = _normalize_method(actual.get("method", ""))
        pr_m = _normalize_method(pred_m) if pred_m else ""
        
        if pr_m and act_m and (pr_m == act_m or pr_m in act_m or act_m in pr_m):
            points += CORRECT_METHOD_POINTS

        total += points

        breakdown.append({
            "match_no": match_no,
            "fixture": f"{actual['team1']} vs {actual['team2']}",
            "predicted": f"{pred_s1}-{pred_s2} {pred_w} {pred_m}".strip(),
            "actual": f"{actual['score1']}-{actual['score2']} {actual_winner} {actual.get('method', '')}".strip(),
            "points": points,
            "exact": exact,
            "result_correct": result_correct,
        })

    return {"total_points": total, "exact_scores": exact_count, "correct_results": correct_result_count, "breakdown": breakdown}


def build_leaderboard(players, actual_results):
    """Return a ranked leaderboard DataFrame with tie-break applied.

    players: output of load_predictions()
    actual_results: dict of match_no -> {team1, team2, score1, score2}
    """
    rows = []
    for name, data in players.items():
        result = score_player(data["predictions"], actual_results)
        rows.append({
            "Player": name,
            "Points": result["total_points"],
            "Exact Scores": result["exact_scores"],
            "Correct Outcomes": result["correct_results"],
            "Predicted Champion": data["winning_team_pick"] or "-",
        })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    # Sort: Points desc, then Exact Scores desc (tie-breaker)
    df = df.sort_values(by=["Points", "Exact Scores"], ascending=[False, False]).reset_index(drop=True)

    # Rank with shared-rank for ties on (Points, Exact Scores)
    ranks = []
    rank = 1
    for i in range(len(df)):
        if i > 0:
            prev = df.iloc[i - 1]
            curr = df.iloc[i]
            if not (curr["Points"] == prev["Points"] and curr["Exact Scores"] == prev["Exact Scores"]):
                rank = i + 1
        ranks.append(rank)
    df.insert(0, "Rank", ranks)

    return df


def detailed_breakdown(players, actual_results):
    """Return dict: player -> breakdown list (per-match)."""
    out = {}
    for name, data in players.items():
        out[name] = score_player(data["predictions"], actual_results)["breakdown"]
    return out
