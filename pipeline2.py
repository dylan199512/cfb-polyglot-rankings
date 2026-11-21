import csv
import html
import json
import os
import re
import subprocess
import unicodedata
import urllib.request
from datetime import datetime
import cfbd

OVERRIDES = {
    "Miami (FL)": "Miami",
    "Miami (Fla.)": "Miami",
    "Pitt": "Pittsburgh",
    "App State": "Appalachian State",
    "Southern Miss": "Southern Mississippi",
    "UConn": "Connecticut",
    "UMass": "Massachusetts",
    "Sam Houston State": "Sam Houston",
    "UL Monroe": "Louisiana Monroe",
    "Louisiana-Monroe": "Louisiana Monroe",
    "Hawai'i": "Hawaii",
}

POWER_CONFS = {"sec", "big ten", "big 12", "acc"}
POWER_CONF_LABELS = {
    "sec": "SEC",
    "big ten": "Big Ten",
    "big 12": "Big 12",
    "acc": "ACC",
}
POWER_INDEPENDENTS = {"Notre Dame"}
MASSEY_MARGIN_CAP = 28.0


def clean(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").strip()
    s = re.sub(r"\bSt\.$", "State", s)
    return OVERRIDES.get(s, s)


def is_fbs(val) -> bool:
    return bool(val and "fbs" in str(getattr(val, "value", val)).lower())


def get_tier(team: str, conf: str | None, fbs_tier_map: dict[str, str], cls_val) -> str:
    """Returns 'P' (Power 4 + Notre Dame), 'G' (Group of 6 + UConn), or 'C' (FCS)."""
    if team in fbs_tier_map:
        return fbs_tier_map[team]
    if conf:
        c_low = conf.strip().lower()
        if c_low in POWER_CONFS or (c_low == "fbs independents" and team in POWER_INDEPENDENTS):
            return "P"
        if is_fbs(cls_val):
            return "G"
    if is_fbs(cls_val):
        return "P" if team in POWER_INDEPENDENTS else "G"
    return "C"


def fetch_espn_polls():
    """Fetches CFP, AP Top 25, and US LBM Coaches Poll from ESPN's rankings feed."""
    url = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.espn.com/college-football/rankings",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    cfp, ap, coaches = {}, {}, {}
    rankings = payload.get("rankings", [])
    label = rankings[0].get("occurrence", {}).get("displayValue", "ESPN Live") if rankings else "ESPN Live"

    for poll in rankings:
        p_id = str(poll.get("id", ""))
        p_type = poll.get("type", "").lower()
        p_name = poll.get("name", "").lower()
        if "fcs" in p_name or "fcs" in p_type:
            continue
        ranks = {
            clean(e["team"]["location"]): int(e["current"])
            for e in poll.get("ranks", [])
            if e.get("team", {}).get("location") and e.get("current") is not None
        }
        if not ranks:
            continue
        if p_type == "cfp" or "playoff" in p_name or "cfp" in p_name:
            cfp = ranks
        elif p_type == "ap" or "associated press" in p_name or "ap top 25" in p_name:
            ap = ranks
        elif p_id == "2" or "usa" in p_type or "lbm" in p_name or "coach" in p_name:
            coaches = ranks

    return cfp, ap, coaches, label


def fetch_espn_live_scoreboard():
    """Pulls real-time completed final scores and live game count from ESPN's FBS scoreboard."""
    url = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80&limit=200"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.espn.com/college-football/scoreboard",
        },
    )
    finals = {}
    in_progress = 0
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return finals, in_progress

    for ev in payload.get("events", []):
        comps = ev.get("competitions", [])
        if not comps:
            continue
        comp = comps[0]
        status = comp.get("status", {}).get("type", {})
        if status.get("state") == "in":
            in_progress += 1
        if not status.get("completed"):
            continue

        home_t, away_t = None, None
        home_s, away_s = None, None
        for c in comp.get("competitors", []):
            loc = c.get("team", {}).get("location")
            if not loc:
                continue
            name = clean(loc)
            try:
                pts = int(float(c.get("score", 0)))
            except (TypeError, ValueError):
                continue
            if c.get("homeAway") == "home":
                home_t, home_s = name, pts
            elif c.get("homeAway") == "away":
                away_t, away_s = name, pts

        if home_t and away_t and home_s is not None and away_s is not None:
            wk = ev.get("week", {}).get("number")
            neutral = bool(comp.get("neutralSite", False))
            finals[(home_t, away_t)] = {
                "week": wk,
                "home": home_t,
                "home_score": home_s,
                "away": away_t,
                "away_score": away_s,
                "neutral": neutral,
            }
    return finals, in_progress


def fetch_season_data(year: int = 2026):
    api_key = os.environ.get("CFBD_API_KEY")
    if not api_key:
        raise RuntimeError("CFBD_API_KEY is not set. Run: export CFBD_API_KEY='your_key'")

    cfp, ap, coaches, label = fetch_espn_polls()
    live_finals, in_progress = fetch_espn_live_scoreboard()

    with cfbd.ApiClient(cfbd.Configuration(access_token=api_key)) as client:
        fbs_teams = cfbd.TeamsApi(client).get_fbs_teams(year=year)
        fbs_tier_map: dict[str, str] = {}
        team_conf_map: dict[str, str] = {}
        for t in fbs_teams:
            name = clean(t.school)
            raw_conf = (getattr(t, "conference", "") or "").strip()
            conf_low = raw_conf.lower()
            team_conf_map[name] = raw_conf or "FBS Independents"
            if conf_low in POWER_CONFS or name in POWER_INDEPENDENTS:
                fbs_tier_map[name] = "P"
            else:
                fbs_tier_map[name] = "G"

        raw = cfbd.GamesApi(client).get_games(year=year, classification="fbs", season_type="both")
        ordered_raw = sorted(raw, key=lambda g: (g.week or 0, str(g.start_date or "")))

        games = []
        upcoming = []
        seen_matchups = set()
        live_merged_count = 0

        for g in ordered_raw:
            h, a = clean(g.home_team), clean(g.away_team)
            h_cls = getattr(g, "home_classification", getattr(g, "home_division", None))
            a_cls = getattr(g, "away_classification", getattr(g, "away_division", None))
            h_conf = getattr(g, "home_conference", None)
            a_conf = getattr(g, "away_conference", None)
            if h not in team_conf_map and h_conf:
                team_conf_map[h] = h_conf
            if a not in team_conf_map and a_conf:
                team_conf_map[a] = a_conf

            h_pts, a_pts = g.home_points, g.away_points
            if (h_pts is None or a_pts is None) and (h, a) in live_finals:
                lf = live_finals[(h, a)]
                h_pts, a_pts = lf["home_score"], lf["away_score"]
                live_merged_count += 1

            seen_matchups.add((h, a))
            if h_pts is not None and a_pts is not None:
                games.append({
                    "week": g.week,
                    "home": h,
                    "home_div": get_tier(h, h_conf, fbs_tier_map, h_cls),
                    "home_score": h_pts,
                    "away": a,
                    "away_div": get_tier(a, a_conf, fbs_tier_map, a_cls),
                    "away_score": a_pts,
                    "neutral": "Y" if g.neutral_site else "N",
                })
            else:
                upcoming.append({
                    "week": g.week,
                    "home": h,
                    "away": a,
                    "neutral": bool(g.neutral_site),
                })

        for (h, a), lf in live_finals.items():
            if (h, a) not in seen_matchups and (a, h) not in seen_matchups and lf["week"]:
                games.append({
                    "week": lf["week"],
                    "home": h,
                    "home_div": fbs_tier_map.get(h, "C"),
                    "home_score": lf["home_score"],
                    "away": a,
                    "away_div": fbs_tier_map.get(a, "C"),
                    "away_score": lf["away_score"],
                    "neutral": "Y" if lf["neutral"] else "N",
                })
                live_merged_count += 1

        games.sort(key=lambda x: x["week"] or 0)
        next_week = min((u["week"] for u in upcoming if u["week"] is not None), default=None)
        if next_week is not None:
            upcoming = [u for u in upcoming if u["week"] == next_week]

        if not (cfp and ap and coaches):
            pw = cfbd.RankingsApi(client).get_rankings(year=year)
            if pw:
                pw.sort(
                    key=lambda w: (
                        1 if str(getattr(w, "season_type", "")).lower() == "postseason" else 0,
                        getattr(w, "week", 0) or 0,
                    )
                )
                for week_obj in reversed(pw):
                    for poll in week_obj.polls or []:
                        pn = (poll.poll or "").lower()
                        rk = {
                            clean(r.school): int(r.rank)
                            for r in (poll.ranks or [])
                            if getattr(r, "school", None) and getattr(r, "rank", None) is not None
                        }
                        if not rk:
                            continue
                        if not cfp and ("playoff" in pn or "cfp" in pn):
                            cfp = rk
                        elif not ap and "ap" in pn:
                            ap = rk
                        elif not coaches and ("coach" in pn or "usa" in pn or "lbm" in pn):
                            coaches = rk
                    if cfp and ap and coaches:
                        break

    max_wk = max((g["week"] for g in games if g["week"] is not None), default=0)
    if live_merged_count > 0 or in_progress > 0:
        print(
            f"Live Sync: Merged {live_merged_count} completed Week {max_wk} final(s) from ESPN "
            f"({in_progress} games currently in progress)."
        )

    return games, upcoming, next_week, max_wk, in_progress, cfp, ap, coaches, label, team_conf_map


def write_games(games, path="games.dat"):
    with open(path, "w", encoding="ascii", errors="replace") as f:
        for i, g in enumerate(games, 1):
            f.write(
                f"{i:04d}{g['week']:02d}{g['home']:<25.25}{g['home_div']:1.1}"
                f"{g['home_score']:03d}{g['away']:<25.25}{g['away_div']:1.1}"
                f"{g['away_score']:03d}{g['neutral']:1.1}\n"
            )


def read_elo(path="elo_ratings.dat"):
    out = {}
    with open(path, "r", encoding="ascii") as f:
        for line in f:
            if len(line.strip()) >= 41:
                out[line[0:25].strip()] = {
                    "div": line[25:26],
                    "wins": int(line[29:32]),
                    "losses": int(line[32:35]),
                    "elo": float(line[35:44]),
                }
    return out


def parse_cobol_ledger(games, path="rankings_report.txt"):
    """Parses COBOL's 132-column report into global top swings AND per-team game-by-game Elo logs."""
    swings = []
    team_logs: dict[str, list[dict]] = {}
    if not os.path.exists(path):
        return swings, team_logs

    with open(path, "r", encoding="ascii") as f:
        for line in f:
            if len(line) < 123 or not line[0:4].isdigit():
                continue
            try:
                g_idx = int(line[0:4]) - 1
                wk = int(line[5:7])
                home = line[9:34].strip()
                h_score = int(line[40:43])
                away = line[48:73].strip()
                a_score = int(line[79:82])
                mode = line[84:87].strip()
                pre_h = float(line[89:96])
                pre_a = float(line[98:105])
                delta = float(line[107:114])
                post_h = float(line[116:123])
                post_a = round(pre_a - delta, 2)
                neutral = (games[g_idx]["neutral"] == "Y") if 0 <= g_idx < len(games) else False

                winner = home if h_score > a_score else away
                loser = away if h_score > a_score else home
                swings.append({
                    "week": wk,
                    "winner": winner,
                    "loser": loser,
                    "score": f"{max(h_score, a_score)}-{min(h_score, a_score)}",
                    "mode": mode,
                    "shift": abs(delta),
                })

                team_logs.setdefault(home, []).append({
                    "week": wk,
                    "opp": away,
                    "loc": "N" if neutral else "vs",
                    "result": "W" if h_score > a_score else "L",
                    "score": f"{h_score}-{a_score}",
                    "pre_elo": round(pre_h, 2),
                    "post_elo": round(post_h, 2),
                    "delta": round(delta, 2),
                    "mode": mode,
                })
                team_logs.setdefault(away, []).append({
                    "week": wk,
                    "opp": home,
                    "loc": "N" if neutral else "@",
                    "result": "W" if a_score > h_score else "L",
                    "score": f"{a_score}-{h_score}",
                    "pre_elo": round(pre_a, 2),
                    "post_elo": round(post_a, 2),
                    "delta": round(-delta, 2),
                    "mode": mode,
                })
            except ValueError:
                continue

    swings.sort(key=lambda x: x["shift"], reverse=True)
    return swings, team_logs


def elo_win_prob(elo_a: float, elo_b: float, hfa_a: float = 0.0) -> float:
    diff = (elo_a + hfa_a) - elo_b
    return 1.0 / (1.0 + (10.0 ** (-diff / 400.0)))


def build_projected_cfp_bracket(board, elo):
    """Applies 2026-27 12-team CFP rules (4 P4 champs + 1 G6 + 7 at-large, straight seeding)."""
    selected: dict[str, str] = {}

    for conf_key, conf_label in POWER_CONF_LABELS.items():
        for r in board:
            if r["conf"].strip().lower() == conf_key and r["team"] not in selected:
                selected[r["team"]] = f"{conf_label} Auto"
                break

    for r in board:
        if r["tier"] == "G6" and r["team"] not in selected:
            selected[r["team"]] = "G6 Auto"
            break

    for r in board[:12]:
        if r["team"] == "Notre Dame" and "Notre Dame" not in selected:
            selected["Notre Dame"] = "ND Top-12"
            break

    for r in board:
        if len(selected) >= 12:
            break
        if r["team"] not in selected:
            selected[r["team"]] = "At-Large"

    seeded = []
    for seed_idx, r in enumerate([row for row in board if row["team"] in selected], 1):
        seeded.append({
            **r,
            "seed": seed_idx,
            "bid": selected[r["team"]],
        })

    bubble_out = [r for r in board if r["team"] not in selected][:2]

    by_seed = {s["seed"]: s for s in seeded}
    r1_pairs = [(12, 5, 4), (11, 6, 3), (10, 7, 2), (9, 8, 1)]
    first_round = []
    for dog_s, fav_s, bye_s in r1_pairs:
        if dog_s not in by_seed or fav_s not in by_seed:
            continue
        dog = by_seed[dog_s]
        fav = by_seed[fav_s]
        p_fav = elo_win_prob(elo[fav["team"]]["elo"], elo[dog["team"]]["elo"], hfa_a=55.0)
        spread = ((elo[fav["team"]]["elo"] + 55.0) - elo[dog["team"]]["elo"]) / 25.0
        first_round.append({
            "dog": dog,
            "fav": fav,
            "bye_seed": bye_s,
            "bye_team": by_seed[bye_s]["team"] if bye_s in by_seed else "",
            "fav_prob": p_fav * 100.0,
            "dog_prob": (1.0 - p_fav) * 100.0,
            "spread": spread,
        })

    return seeded[:4], first_round, bubble_out


def predict_upcoming_upsets(upcoming, elo, comp_rk, top_n=10):
    """Computes COBOL Elo win probabilities and ranks upcoming games by upset threat."""
    predictions = []
    for g in upcoming:
        h, a = g["home"], g["away"]
        if h not in elo or a not in elo:
            continue
        h_rk = comp_rk.get(h)
        a_rk = comp_rk.get(a)
        if (h_rk is None or h_rk > 25) and (a_rk is None or a_rk > 25):
            continue

        h_elo = elo[h]["elo"]
        a_elo = elo[a]["elo"]
        hfa = 0.0 if g["neutral"] else 55.0
        diff = (h_elo + hfa) - a_elo
        p_home = 1.0 / (1.0 + (10.0 ** (-diff / 400.0)))
        p_away = 1.0 - p_home

        if p_home >= 0.5:
            fav, dog = h, a
            fav_rk, dog_rk = h_rk, a_rk
            fav_prob, dog_prob = p_home * 100.0, p_away * 100.0
            venue = "vs" if g["neutral"] else "@"
        else:
            fav, dog = a, h
            fav_rk, dog_rk = a_rk, h_rk
            fav_prob, dog_prob = p_away * 100.0, p_home * 100.0
            venue = "vs"

        spread = abs(diff) / 25.0
        if dog_prob >= 40.0:
            alert = "TOSS-UP"
        elif dog_prob >= 28.0:
            alert = "HIGH ALERT"
        elif dog_prob >= 18.0:
            alert = "WATCH"
        else:
            alert = "SAFE"

        predictions.append({
            "week": g["week"],
            "home": h,
            "away": a,
            "fav": fav,
            "dog": dog,
            "fav_rk": fav_rk,
            "dog_rk": dog_rk,
            "fav_prob": fav_prob,
            "dog_prob": dog_prob,
            "spread": spread,
            "venue": venue,
            "neutral": g["neutral"],
            "alert": alert,
        })

    predictions.sort(
        key=lambda x: (
            1 if (x["fav_rk"] is not None and x["fav_rk"] <= 25) else 0,
            x["dog_prob"],
        ),
        reverse=True,
    )
    return predictions[:top_n]


def solve_linear_models_fortran(games):
    """Builds Colley (C*r = b) and Massey (M*m = p) systems and solves both in Fortran."""
    teams = sorted({g["home"] for g in games} | {g["away"] for g in games})
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    C = [[2.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    M = [[0.0 for _ in range(n)] for _ in range(n)]
    w, l = [0.0] * n, [0.0] * n
    p = [0.0] * n

    for g in games:
        h, a = idx[g["home"]], idx[g["away"]]
        C[h][h] += 1.0
        C[a][a] += 1.0
        C[h][a] -= 1.0
        C[a][h] -= 1.0

        M[h][h] += 1.0
        M[a][a] += 1.0
        M[h][a] -= 1.0
        M[a][h] -= 1.0

        raw_margin = float(g["home_score"] - g["away_score"])
        capped_margin = max(-MASSEY_MARGIN_CAP, min(MASSEY_MARGIN_CAP, raw_margin))
        p[h] += capped_margin
        p[a] -= capped_margin

        if g["home_score"] > g["away_score"]:
            w[h] += 1.0
            l[a] += 1.0
        else:
            w[a] += 1.0
            l[h] += 1.0

    for i in range(n - 1):
        M[i][i] += 1e-9
    M[n - 1] = [1.0] * n
    p[n - 1] = 0.0

    with open("matrix.dat", "w", encoding="ascii") as f:
        f.write(f"{n}\n")
        for row in C:
            f.write(" ".join(f"{v:.8f}" for v in row) + "\n")
        for i in range(n):
            f.write(f"{1.0 + 0.5 * (w[i] - l[i]):.8f}\n")
        for row in M:
            f.write(" ".join(f"{v:.8f}" for v in row) + "\n")
        for val in p:
            f.write(f"{val:.8f}\n")

    subprocess.run(["./colley_solver"], check=True)

    colley_map, massey_map = {}, {}
    with open("colley_ratings.dat", "r", encoding="ascii") as f:
        lines = [line.strip() for line in f if line.strip()]
    for i, line in enumerate(lines):
        parts = line.split()
        colley_map[teams[i]] = float(parts[0])
        massey_map[teams[i]] = float(parts[1])

    return colley_map, massey_map


def norm(vals):
    lo, hi = min(vals), max(vals)
    return [50.0] * len(vals) if hi == lo else [((v - lo) / (hi - lo)) * 100.0 for v in vals]


def determine_driver(e_rk: int, c_rk: int, m_rk: int) -> tuple[str, str]:
    """Identifies which of the 3 engines pulls a team's composite rank highest."""
    ranks = {"Elo-Led": e_rk, "Colley-Led": c_rk, "Massey-Led": m_rk}
    best_label, best_rk = min(ranks.items(), key=lambda x: x[1])
    worst_rk = max(ranks.values())
    if worst_rk - best_rk <= 2:
        return "Balanced", "drv-bal"
    cls_map = {"Elo-Led": "drv-elo", "Colley-Led": "drv-col", "Massey-Led": "drv-mas"}
    return best_label, cls_map[best_label]


def build_plain_english_why(r: dict, logs: list[dict], comp_rk: dict[str, int]) -> str:
    """Generates a concise natural-language explanation for why a team holds its exact rank."""
    e_rk, c_rk, m_rk = r["elo_rk"], r["colley_rk"], r["massey_rk"]
    tier_note = (
        "Power 4 baseline (1560 Elo)"
        if r["tier"] == "P4"
        else "Group of 6 baseline (1440 Elo)"
    )

    if r["driver"] == "Balanced":
        model_note = f"All three engines agree closely (Elo #{e_rk}, Colley #{c_rk}, Massey #{m_rk})."
    elif r["driver"] == "Elo-Led":
        model_note = f"COBOL Elo (#{e_rk}) leads their composite over Colley (#{c_rk}) and Massey (#{m_rk}) due to recency-weighted wins."
    elif r["driver"] == "Colley-Led":
        model_note = f"Fortran Colley (#{c_rk}, win/loss matrix) lifts them above Elo (#{e_rk}) and Massey (#{m_rk})."
    else:
        model_note = f"Fortran Massey (#{m_rk}, {r['massey']:+.2f} margin rating) boosts them ahead of Elo (#{e_rk}) and Colley (#{c_rk})."

    losses = [g for g in logs if g["result"] == "L"]
    if not losses:
        loss_note = "Undefeated ledger with zero Elo penalties."
    else:
        loss_strs = []
        for g in losses:
            ork = comp_rk.get(g["opp"])
            o_lbl = f"#{ork} {g['opp']}" if ork else g["opp"]
            loss_strs.append(f"{o_lbl} ({g['score']}, {g['delta']:+.1f} Elo)")
        loss_note = "Losses: " + "; ".join(loss_strs) + "."

    return (
        f"Starts from {tier_note} with #{r['sos_rk']} Strength of Schedule and marquee win over {r['best_win']}. "
        f"{model_note} {loss_note}"
    )


def log_history_and_get_trends(board, label, csv_path="rankings_history.csv"):
    """Logs current week's rankings to CSV and computes rank change vs. the prior saved snapshot."""
    prev_ranks: dict[str, int] = {}
    existing_rows: list[dict] = []

    if os.path.exists(csv_path):
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            existing_rows = list(csv.DictReader(f))
        distinct_labels = []
        for row in existing_rows:
            lbl = row.get("poll_label", "")
            if lbl and lbl not in distinct_labels:
                distinct_labels.append(lbl)
        prior_candidates = [lbl for lbl in distinct_labels if lbl != label]
        if prior_candidates:
            prev_label = prior_candidates[-1]
            for row in existing_rows:
                if row.get("poll_label") == prev_label:
                    prev_ranks[row["team"]] = int(row["comp_rank"])

    kept_rows = [r for r in existing_rows if r.get("poll_label") != label]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    for idx, r in enumerate(board, 1):
        kept_rows.append({
            "timestamp": stamp,
            "poll_label": label,
            "comp_rank": idx,
            "team": r["team"],
            "tier": r["tier"],
            "record": r["rec"],
            "cobol_elo": f"{r['elo']:.4f}",
            "elo_rank": r["elo_rk"],
            "fortran_colley": f"{r['colley']:.4f}",
            "colley_rank": r["colley_rk"],
            "fortran_massey": f"{r['massey']:.4f}",
            "massey_rank": r["massey_rk"],
            "driver": r["driver"],
            "composite": f"{r['comp']:.2f}",
            "sos_rank": r["sos_rk"],
            "best_win": r["best_win"],
            "cfp_rank": r["cfp"] or "",
            "ap_rank": r["ap"] or "",
            "coaches_rank": r["coach"] or "",
        })

    fieldnames = [
        "timestamp", "poll_label", "comp_rank", "team", "tier", "record",
        "cobol_elo", "elo_rank", "fortran_colley", "colley_rank",
        "fortran_massey", "massey_rank", "driver", "composite",
        "sos_rank", "best_win", "cfp_rank", "ap_rank", "coaches_rank",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    trends: dict[str, str] = {}
    for idx, r in enumerate(board, 1):
        old_rk = prev_ranks.get(r["team"])
        if old_rk is None:
            trends[r["team"]] = "--"
        elif old_rk == idx:
            trends[r["team"]] = "0"
        else:
            trends[r["team"]] = f"{old_rk - idx:+d}"
    return trends


def fmt_rk_name(team: str, rk: int | None) -> str:
    return f"#{rk} {team}" if (rk is not None and rk <= 25) else team


def generate_html_dashboard(
    board,
    trends,
    swings,
    team_logs,
    comp_rk,
    predictions,
    next_week,
    max_wk,
    in_progress,
    bracket_data,
    snubs,
    label,
    ref,
    html_path="dashboard.html",
):
    """Writes a self-contained HTML scoreboard with live Saturday score sync and clickable team audits."""
    now_str = datetime.now().strftime("%b %d, %Y %I:%M %p")
    byes, first_round, bubble_out = bracket_data
    live_pill = f"Through Week {max_wk}" + (f" ({in_progress} Live)" if in_progress else "")

    rows_html = []
    for rk, r in enumerate(board[:25], 1):
        hrk = r["cfp"] if ref == "CFP" else r["ap"]
        rk_s = f"#{hrk}" if hrk else "NR"
        if hrk:
            d_val = hrk - rk
            d_s = f"{d_val:+d}" if d_val != 0 else "0"
            d_cls = "pos" if d_val > 0 else ("neg" if d_val < 0 else "neu")
        else:
            d_s = "NEW"
            d_cls = "new-tag"

        tr_s = trends.get(r["team"], "--")
        tr_cls = "pos" if tr_s.startswith("+") else ("neg" if tr_s.startswith("-") else "neu")
        ap_s = f"#{r['ap']}" if r["ap"] else "NR"
        co_s = f"#{r['coach']}" if r["coach"] else "NR"
        tier_cls = "p4" if r["tier"] == "P4" else "g6"
        sos_cls = "pos" if r["sos_rk"] <= 15 else ("neg" if r["sos_rk"] >= 75 else "mono")
        sub_ranks = f"#{r['elo_rk']} · #{r['colley_rk']} · #{r['massey_rk']}"

        rows_html.append(f"""
        <tr class="clickable-row" onclick="openAudit({rk - 1})" title="Click for {html.escape(r['team'])}'s game-by-game Elo & model breakdown">
          <td class="num rk">{rk}</td>
          <td class="team">{html.escape(r['team'])} <span class="inspect-hint">&#9432;</span></td>
          <td><span class="badge {tier_cls}">{r['tier']}</span></td>
          <td class="mono">{r['rec']}</td>
          <td class="num mono">{r['elo']:.2f}</td>
          <td class="num mono">{r['colley']:.4f}</td>
          <td class="num mono">{r['massey']:+.2f}</td>
          <td class="num score">{r['comp']:.2f}</td>
          <td class="num mono sub-rk">{sub_ranks}</td>
          <td><span class="badge {r['driver_cls']}">{r['driver']}</span></td>
          <td class="num {sos_cls}">#{r['sos_rk']}</td>
          <td class="best-win">{html.escape(r['best_win'])}</td>
          <td class="num {tr_cls}">{tr_s}</td>
          <td class="num mono">{rk_s}</td>
          <td class="num {d_cls}">{d_s}</td>
          <td class="num mono">{ap_s}</td>
          <td class="num mono">{co_s}</td>
        </tr>""")

    bye_cards = []
    qf_labels = {1: "vs 8/9 Winner", 2: "vs 7/10 Winner", 3: "vs 6/11 Winner", 4: "vs 5/12 Winner"}
    for b in byes:
        bye_cards.append(f"""
        <div class="cfp-bye">
          <div class="cfp-seed-row">
            <span class="seed-pill">#{b['seed']} Seed</span>
            <span class="bid-tag">{html.escape(b['bid'])}</span>
          </div>
          <div class="cfp-team">{html.escape(b['team'])} <span class="muted">({b['rec']})</span></div>
          <div class="cfp-sub">Quarterfinal: {qf_labels.get(b['seed'], '')}</div>
        </div>""")

    r1_cards = []
    for m in first_round:
        dog = m["dog"]
        fav = m["fav"]
        r1_cards.append(f"""
        <div class="cfp-game">
          <div class="cfp-seed-row">
            <span class="wk">Campus R1 &bull; Winner plays #{m['bye_seed']} {html.escape(m['bye_team'])}</span>
            <span class="shift">{html.escape(fav['team'])} -{m['spread']:.1f}</span>
          </div>
          <div class="cfp-line">
            <span><strong>#{dog['seed']} {html.escape(dog['team'])}</strong> <span class="bid-tag">{html.escape(dog['bid'])}</span></span>
            <span class="mono muted">{m['dog_prob']:.1f}%</span>
          </div>
          <div class="cfp-line">
            <span>@ <strong>#{fav['seed']} {html.escape(fav['team'])}</strong> <span class="bid-tag">{html.escape(fav['bid'])}</span></span>
            <span class="mono pos">{m['fav_prob']:.1f}%</span>
          </div>
        </div>""")

    bubble_txt = ", ".join(f"#{b['comp_rk']} {b['team']} ({b['rec']})" for b in bubble_out)
    bubble_html = f'<div class="snubs"><strong>First Two Out:</strong> {html.escape(bubble_txt)}</div>' if bubble_txt else ""

    pred_cards = []
    for p in predictions[:6]:
        fav_label = fmt_rk_name(p["fav"], p["fav_rk"])
        dog_label = fmt_rk_name(p["dog"], p["dog_rk"])
        badge_cls = {
            "TOSS-UP": "tossup",
            "HIGH ALERT": "high-alert",
            "WATCH": "watch",
            "SAFE": "safe",
        }.get(p["alert"], "safe")
        pred_cards.append(f"""
        <div class="swing-item">
          <div class="swing-top">
            <span class="badge {badge_cls}">{p['alert']}</span>
            <span class="wk">Line: {html.escape(p['fav'])} -{p['spread']:.1f}</span>
            <span class="upset-pct">{p['dog_prob']:.1f}% Upset</span>
          </div>
          <div class="matchup">
            <strong>{html.escape(dog_label)}</strong> {p['venue']} <strong>{html.escape(fav_label)}</strong>
          </div>
          <div class="prob-bar">
            <div class="prob-dog" style="width:{p['dog_prob']:.1f}%"></div>
            <div class="prob-fav" style="width:{p['fav_prob']:.1f}%"></div>
          </div>
        </div>""")

    if not pred_cards:
        pred_cards.append('<div class="sub">No upcoming FBS games scheduled.</div>')

    swing_cards = []
    for s in swings[:5]:
        mode_badge = '<span class="badge nov">NOV 1.25x</span>' if s["mode"] == "NOV" else '<span class="badge reg">REG</span>'
        swing_cards.append(f"""
        <div class="swing-item">
          <div class="swing-top">
            <span class="wk">Week {s['week']}</span>
            {mode_badge}
            <span class="shift">+{s['shift']:.2f} Elo</span>
          </div>
          <div class="matchup"><strong>{html.escape(s['winner'])}</strong> def. {html.escape(s['loser'])} ({s['score']})</div>
        </div>""")

    snub_banner = ""
    if snubs:
        snub_banner = f'<div class="snubs"><strong>{ref} Top 25 Outside Computer Top 25:</strong> {html.escape(", ".join(snubs))}</div>'

    wk_title = f"Week {next_week}" if next_week else "Upcoming"

    calc_teams = []
    for i, r in enumerate(board, 1):
        enriched_games = []
        for g in team_logs.get(r["team"], []):
            o_rk = comp_rk.get(g["opp"])
            enriched_games.append({
                **g,
                "opp_rk": f"#{o_rk}" if o_rk else "FCS",
            })
        calc_teams.append({
            "team": r["team"],
            "rk": i,
            "tier": r["tier"],
            "conf": r["conf"],
            "rec": r["rec"],
            "elo": round(r["elo"], 4),
            "elo_rk": r["elo_rk"],
            "colley": round(r["colley"], 4),
            "colley_rk": r["colley_rk"],
            "massey": round(r["massey"], 2),
            "massey_rk": r["massey_rk"],
            "comp": round(r["comp"], 2),
            "sos_rk": r["sos_rk"],
            "best_win": r["best_win"],
            "driver": r["driver"],
            "why": r["why"],
            "games": enriched_games,
        })

    teams_json = json.dumps(calc_teams)
    options_html = "".join(
        f'<option value="{i}">#{t["rk"]} {html.escape(t["team"])} ({t["rec"]} &bull; {t["elo"]:.1f} Elo)</option>'
        for i, t in enumerate(calc_teams)
    )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>2026 Polyglot CFB Rankings — {html.escape(live_pill)}</title>
  <style>
    :root {{ --bg:#0f172a; --card:#1e293b; --border:#334155; --text:#f8fafc; --muted:#94a3b8; --accent:#38bdf8; --green:#4ade80; --red:#f87171; --amber:#fbbf24; --purple:#c084fc; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; padding:22px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--text); }}
    .wrap {{ max-width:1540px; margin:0 auto; }}
    header {{ display:flex; flex-wrap:wrap; justify-content:space-between; align-items:flex-end; gap:12px; margin-bottom:20px; border-bottom:1px solid var(--border); padding-bottom:14px; }}
    h1 {{ margin:0; font-size:1.5rem; letter-spacing:-0.02em; }}
    .sub {{ color:var(--muted); font-size:0.88rem; margin-top:4px; }}
    .muted {{ color:var(--muted); font-weight:400; }}
    .pills {{ display:flex; gap:8px; flex-wrap:wrap; }}
    .pill {{ background:var(--card); border:1px solid var(--border); padding:5px 10px; border-radius:999px; font-size:0.78rem; color:var(--accent); font-weight:600; }}
    .grid {{ display:grid; grid-template-columns: 2.8fr 1fr; gap:20px; align-items:start; }}
    @media (max-width: 1200px) {{ .grid {{ grid-template-columns: 1fr; }} }}
    .main-col, .side-col {{ display:flex; flex-direction:column; gap:20px; }}
    .card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; overflow-x:auto; }}
    .card-hdr {{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:12px; }}
    .card-hdr h2 {{ margin:0; }}
    h2 {{ margin:0 0 12px 0; font-size:1.05rem; color:#e2e8f0; }}
    table {{ width:100%; border-collapse:collapse; font-size:0.83rem; }}
    th {{ text-align:left; color:var(--muted); font-weight:600; font-size:0.7rem; text-transform:uppercase; padding:8px 6px; border-bottom:1px solid var(--border); }}
    th.num, td.num {{ text-align:right; }}
    td {{ padding:8px 6px; border-bottom:1px solid rgba(51,65,85,0.55); white-space:nowrap; }}
    tr.clickable-row {{ cursor:pointer; transition:background 0.12s ease; }}
    tr.clickable-row:hover {{ background:rgba(56,189,248,0.11); }}
    .inspect-hint {{ color:var(--muted); font-size:0.75rem; opacity:0.7; }}
    .rk {{ font-weight:700; color:var(--accent); }}
    .team {{ font-weight:600; }}
    .sub-rk {{ color:#cbd5e1; font-size:0.77rem; }}
    .best-win {{ font-size:0.79rem; color:#cbd5e1; }}
    .mono {{ font-variant-numeric: tabular-nums; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:0.79rem; }}
    .score {{ font-weight:700; color:#fff; }}
    .pos {{ color:var(--green); font-weight:700; }}
    .neg {{ color:var(--red); font-weight:700; }}
    .neu {{ color:var(--muted); }}
    .new-tag {{ color:var(--amber); font-weight:700; font-size:0.73rem; }}
    .badge {{ display:inline-block; padding:2px 6px; border-radius:5px; font-size:0.67rem; font-weight:700; letter-spacing:0.02em; }}
    .p4 {{ background:rgba(56,189,248,0.16); color:#7dd3fc; }}
    .g6 {{ background:rgba(148,163,184,0.18); color:#cbd5e1; }}
    .drv-elo {{ background:rgba(56,189,248,0.18); color:#7dd3fc; }}
    .drv-col {{ background:rgba(74,222,128,0.18); color:#86efac; }}
    .drv-mas {{ background:rgba(192,132,252,0.2); color:#d8b4fe; }}
    .drv-bal {{ background:rgba(148,163,184,0.16); color:#cbd5e1; }}
    .nov {{ background:rgba(251,191,36,0.16); color:var(--amber); }}
    .reg {{ background:rgba(148,163,184,0.16); color:var(--muted); }}
    .tossup {{ background:rgba(248,113,113,0.2); color:#fca5a5; }}
    .high-alert {{ background:rgba(251,191,36,0.2); color:var(--amber); }}
    .watch {{ background:rgba(56,189,248,0.18); color:#7dd3fc; }}
    .safe {{ background:rgba(148,163,184,0.16); color:var(--muted); }}
    .cfp-grid {{ display:grid; grid-template-columns: 1fr 1.35fr; gap:14px; }}
    @media (max-width: 780px) {{ .cfp-grid {{ grid-template-columns: 1fr; }} }}
    .cfp-col {{ display:flex; flex-direction:column; gap:10px; }}
    .cfp-bye, .cfp-game {{ background:rgba(15,23,42,0.55); border:1px solid var(--border); border-radius:9px; padding:10px 12px; }}
    .cfp-seed-row {{ display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:5px; font-size:0.75rem; }}
    .seed-pill {{ background:rgba(56,189,248,0.18); color:var(--accent); font-weight:700; padding:2px 7px; border-radius:999px; }}
    .bid-tag {{ background:rgba(148,163,184,0.16); color:#cbd5e1; font-size:0.68rem; font-weight:600; padding:1px 6px; border-radius:4px; }}
    .cfp-team {{ font-weight:700; font-size:0.92rem; }}
    .cfp-sub {{ font-size:0.76rem; color:var(--muted); margin-top:3px; }}
    .cfp-line {{ display:flex; justify-content:space-between; align-items:center; font-size:0.84rem; padding:2px 0; }}
    .swing-item {{ padding:10px 0; border-bottom:1px solid rgba(51,65,85,0.6); }}
    .swing-item:last-child {{ border-bottom:none; padding-bottom:2px; }}
    .swing-top {{ display:flex; align-items:center; gap:8px; font-size:0.77rem; margin-bottom:5px; }}
    .wk {{ color:var(--muted); font-weight:600; }}
    .shift {{ margin-left:auto; color:var(--green); font-weight:700; font-family:ui-monospace,Menlo,monospace; }}
    .upset-pct {{ margin-left:auto; color:var(--amber); font-weight:700; font-family:ui-monospace,Menlo,monospace; }}
    .matchup {{ font-size:0.86rem; color:#e2e8f0; }}
    .prob-bar {{ display:flex; height:6px; border-radius:999px; overflow:hidden; margin-top:7px; background:#0f172a; }}
    .prob-dog {{ background:var(--amber); }}
    .prob-fav {{ background:var(--accent); }}
    .snubs {{ margin-top:12px; padding:10px 12px; background:rgba(15,23,42,0.6); border:1px solid var(--border); border-radius:8px; font-size:0.82rem; color:#cbd5e1; }}
    .calc-form {{ display:flex; flex-direction:column; gap:9px; }}
    select {{ width:100%; background:#0f172a; color:var(--text); border:1px solid var(--border); border-radius:7px; padding:8px 10px; font-size:0.84rem; }}
    .calc-out {{ margin-top:6px; padding:11px; background:rgba(15,23,42,0.65); border:1px solid var(--border); border-radius:8px; }}
    .calc-head {{ display:flex; justify-content:space-between; align-items:center; font-size:0.86rem; font-weight:700; }}
    .calc-meta {{ display:flex; justify-content:space-between; font-size:0.77rem; color:var(--muted); margin-top:6px; }}
    .modal-bg {{ display:none; position:fixed; inset:0; background:rgba(2,6,23,0.78); z-index:50; align-items:center; justify-content:center; padding:20px; }}
    .modal-bg.open {{ display:flex; }}
    .modal {{ background:var(--card); border:1px solid var(--border); border-radius:14px; max-width:820px; width:100%; max-height:88vh; overflow-y:auto; padding:20px; box-shadow:0 24px 48px rgba(0,0,0,0.55); }}
    .modal-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; border-bottom:1px solid var(--border); padding-bottom:12px; margin-bottom:14px; }}
    .close-btn {{ background:#0f172a; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:6px 12px; cursor:pointer; font-weight:700; }}
    .why-box {{ background:rgba(56,189,248,0.09); border:1px solid rgba(56,189,248,0.3); border-radius:9px; padding:12px; font-size:0.88rem; line-height:1.45; color:#e2e8f0; margin-bottom:14px; }}
    .stat-strip {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:14px; }}
    @media (max-width:640px) {{ .stat-strip {{ grid-template-columns:repeat(2,1fr); }} }}
    .stat-box {{ background:rgba(15,23,42,0.6); border:1px solid var(--border); border-radius:8px; padding:9px 11px; }}
    .stat-lbl {{ font-size:0.7rem; color:var(--muted); text-transform:uppercase; font-weight:600; }}
    .stat-val {{ font-size:0.98rem; font-weight:700; margin-top:2px; font-variant-numeric:tabular-nums; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>2026 Polyglot College Football Computer Poll</h1>
        <div class="sub">3-Model BCS Ensemble &bull; Generated {now_str} &bull; Click any team row to inspect its COBOL Elo game log</div>
      </div>
      <div class="pills">
        <span class="pill">{html.escape(live_pill)}</span>
        <span class="pill">COBOL COMP-3 Elo</span>
        <span class="pill">Fortran Colley + Massey</span>
        <span class="pill">ESPN {html.escape(label)}</span>
      </div>
    </header>
    <div class="grid">
      <div class="main-col">
        <div class="card">
          <div class="card-hdr">
            <h2>Composite Top 25 Leaderboard (Model Sub-Ranks, Driver &amp; Resume)</h2>
            <span class="sub">Sub-Ranks: Elo &middot; Colley &middot; Massey</span>
          </div>
          <table>
            <thead>
              <tr>
                <th class="num">RK</th>
                <th>FBS Team</th>
                <th>Tier</th>
                <th>Rec</th>
                <th class="num">COBOL Elo</th>
                <th class="num">FTN Colley</th>
                <th class="num">FTN Massey</th>
                <th class="num">Comp</th>
                <th class="num">E·C·M Rks</th>
                <th>Driver</th>
                <th class="num">SOS</th>
                <th>Best Win</th>
                <th class="num">Move</th>
                <th class="num">{ref}</th>
                <th class="num">Delta</th>
                <th class="num">AP</th>
                <th class="num">Coach</th>
              </tr>
            </thead>
            <tbody>{''.join(rows_html)}</tbody>
          </table>
          {snub_banner}
        </div>
        <div class="card">
          <h2>Projected 12-Team CFP Bracket (2026 Format: 4 P4 Champs + 1 G6 + 7 At-Large)</h2>
          <div class="cfp-grid">
            <div class="cfp-col">
              {''.join(bye_cards)}
            </div>
            <div class="cfp-col">
              {''.join(r1_cards)}
            </div>
          </div>
          {bubble_html}
        </div>
      </div>
      <div class="side-col">
        <div class="card">
          <h2>Interactive COBOL Elo Matchup Calculator</h2>
          <div class="calc-form">
            <select id="teamA" onchange="calcMatchup()">{options_html}</select>
            <select id="venue" onchange="calcMatchup()">
              <option value="neutral">Neutral Site (CFP Bowl / Championship)</option>
              <option value="homeA">Team A Home Field (+55 Elo)</option>
              <option value="homeB">Team B Home Field (+55 Elo)</option>
            </select>
            <select id="teamB" onchange="calcMatchup()">{options_html}</select>
            <div class="calc-out" id="calcOut"></div>
          </div>
        </div>
        <div class="card">
          <h2>{html.escape(wk_title)} Upset Watch (COBOL Elo)</h2>
          {''.join(pred_cards)}
        </div>
        <div class="card">
          <h2>COBOL Audit: Top Elo Swings</h2>
          {''.join(swing_cards)}
        </div>
      </div>
    </div>
  </div>

  <div class="modal-bg" id="auditModal" onclick="if(event.target===this) closeAudit()">
    <div class="modal">
      <div class="modal-top">
        <div>
          <h2 id="mTitle" style="margin:0;font-size:1.25rem;"></h2>
          <div class="sub" id="mSub"></div>
        </div>
        <button class="close-btn" onclick="closeAudit()">Close &times;</button>
      </div>
      <div class="why-box" id="mWhy"></div>
      <div class="stat-strip" id="mStats"></div>
      <table>
        <thead>
          <tr>
            <th>Wk</th>
            <th>Loc</th>
            <th>Opponent</th>
            <th>Opp Rk</th>
            <th>Result</th>
            <th>Mode</th>
            <th class="num">Pre-Elo</th>
            <th class="num">Shift</th>
            <th class="num">Post-Elo</th>
          </tr>
        </thead>
        <tbody id="mGames"></tbody>
      </table>
    </div>
  </div>

  <script>
    const TEAMS = {teams_json};
    document.getElementById("teamB").selectedIndex = Math.min(1, TEAMS.length - 1);

    function calcMatchup() {{
      const a = TEAMS[document.getElementById("teamA").value];
      const b = TEAMS[document.getElementById("teamB").value];
      const v = document.getElementById("venue").value;
      const hfa = v === "homeA" ? 55.0 : (v === "homeB" ? -55.0 : 0.0);
      const diff = (a.elo + hfa) - b.elo;
      const pA = 1.0 / (1.0 + Math.pow(10.0, -diff / 400.0));
      const pB = 1.0 - pA;
      const fav = diff >= 0 ? a.team : b.team;
      const spread = (Math.abs(diff) / 25.0).toFixed(1);
      const shiftA = (40.0 * (1.0 - pA)).toFixed(2);
      const shiftB = (40.0 * (1.0 - pB)).toFixed(2);
      document.getElementById("calcOut").innerHTML = `
        <div class="calc-head">
          <span>${{a.team}}: ${{(pA * 100).toFixed(1)}}%</span>
          <span>${{b.team}}: ${{(pB * 100).toFixed(1)}}%</span>
        </div>
        <div class="prob-bar">
          <div class="prob-fav" style="width:${{(pA * 100).toFixed(1)}}%"></div>
          <div class="prob-dog" style="width:${{(pB * 100).toFixed(1)}}%"></div>
        </div>
        <div class="calc-meta">
          <span>Line: <strong>${{fav}} -${{spread}}</strong></span>
          <span>Win Shift: +${{shiftA}} / +${{shiftB}} Elo</span>
        </div>
      `;
    }}

    function openAudit(idx) {{
      const t = TEAMS[idx];
      document.getElementById("mTitle").textContent = `#${{t.rk}} ${{t.team}} (${{t.rec}})`;
      document.getElementById("mSub").textContent = `${{t.conf}} (${{t.tier}}) • Composite Score: ${{t.comp.toFixed(2)}} • Driver: ${{t.driver}}`;
      document.getElementById("mWhy").textContent = t.why;
      document.getElementById("mStats").innerHTML = `
        <div class="stat-box"><div class="stat-lbl">COBOL Elo</div><div class="stat-val">#${{t.elo_rk}} (${{t.elo.toFixed(2)}})</div></div>
        <div class="stat-box"><div class="stat-lbl">Fortran Colley</div><div class="stat-val">#${{t.colley_rk}} (${{t.colley.toFixed(4)}})</div></div>
        <div class="stat-box"><div class="stat-lbl">Fortran Massey</div><div class="stat-val">#${{t.massey_rk}} (${{t.massey >= 0 ? '+' : ''}}${{t.massey.toFixed(2)}})</div></div>
        <div class="stat-box"><div class="stat-lbl">Schedule (SOS)</div><div class="stat-val">#${{t.sos_rk}} FBS</div></div>
      `;
      document.getElementById("mGames").innerHTML = t.games.map(g => {{
        const resCls = g.result === "W" ? "pos" : "neg";
        const dCls = g.delta >= 0 ? "pos" : "neg";
        const dStr = (g.delta >= 0 ? "+" : "") + g.delta.toFixed(2);
        return `<tr>
          <td class="mono">Wk ${{g.week}}</td>
          <td class="muted">${{g.loc}}</td>
          <td class="team">${{g.opp}}</td>
          <td class="mono muted">${{g.opp_rk}}</td>
          <td class="${{resCls}}">${{g.result}} ${{g.score}}</td>
          <td><span class="badge ${{g.mode === 'NOV' ? 'nov' : 'reg'}}">${{g.mode}}</span></td>
          <td class="num mono">${{g.pre_elo.toFixed(2)}}</td>
          <td class="num mono ${{dCls}}">${{dStr}}</td>
          <td class="num mono">${{g.post_elo.toFixed(2)}}</td>
        </tr>`;
      }}).join("");
      document.getElementById("auditModal").classList.add("open");
    }}

    function closeAudit() {{
      document.getElementById("auditModal").classList.remove("open");
    }}
    document.addEventListener("keydown", e => {{ if (e.key === "Escape") closeAudit(); }});
    calcMatchup();
  </script>
</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    games, upcoming, next_week, max_wk, in_progress, cfp, ap, coaches, label, team_conf_map = fetch_season_data(2026)
    write_games(games)
    subprocess.run(["./elo_engine"], check=True)
    colley, massey = solve_linear_models_fortran(games)
    elo = read_elo()

    all_teams = sorted(elo.keys())
    all_e = norm([elo[t]["elo"] for t in all_teams])
    all_c = norm([colley[t] for t in all_teams])
    all_m = norm([massey[t] for t in all_teams])
    all_comp = {t: (en + cn + mn) / 3.0 for t, en, cn, mn in zip(all_teams, all_e, all_c, all_m)}

    opponents_by_team: dict[str, list[str]] = {t: [] for t in all_teams}
    wins_by_team: dict[str, list[str]] = {t: [] for t in all_teams}
    for g in games:
        h, a = g["home"], g["away"]
        opponents_by_team[h].append(a)
        opponents_by_team[a].append(h)
        if g["home_score"] > g["away_score"]:
            wins_by_team[h].append(a)
        else:
            wins_by_team[a].append(h)

    fbs = [t for t, d in elo.items() if d["div"] in ("P", "G")]
    e_norm = norm([elo[t]["elo"] for t in fbs])
    c_norm = norm([colley[t] for t in fbs])
    m_norm = norm([massey[t] for t in fbs])

    elo_rk_map = {t: i for i, t in enumerate(sorted(fbs, key=lambda x: elo[x]["elo"], reverse=True), 1)}
    colley_rk_map = {t: i for i, t in enumerate(sorted(fbs, key=lambda x: colley[x], reverse=True), 1)}
    massey_rk_map = {t: i for i, t in enumerate(sorted(fbs, key=lambda x: massey[x], reverse=True), 1)}

    raw_sos = {
        t: (sum(all_comp[opp] for opp in opponents_by_team[t]) / len(opponents_by_team[t]))
        if opponents_by_team[t]
        else 0.0
        for t in fbs
    }
    sos_rk_map = {t: i for i, t in enumerate(sorted(fbs, key=lambda x: raw_sos[x], reverse=True), 1)}

    board = sorted(
        [
            {
                "team": t,
                "tier": "P4" if elo[t]["div"] == "P" else "G6",
                "conf": team_conf_map.get(t, "Unknown"),
                "rec": f"{elo[t]['wins']}-{elo[t]['losses']}",
                "elo": elo[t]["elo"],
                "elo_rk": elo_rk_map[t],
                "colley": colley[t],
                "colley_rk": colley_rk_map[t],
                "massey": massey[t],
                "massey_rk": massey_rk_map[t],
                "comp": (en + cn + mn) / 3.0,
                "sos_rk": sos_rk_map[t],
                "cfp": cfp.get(t),
                "ap": ap.get(t),
                "coach": coaches.get(t),
            }
            for t, en, cn, mn in zip(fbs, e_norm, c_norm, m_norm)
        ],
        key=lambda x: x["comp"],
        reverse=True,
    )

    swings, team_logs = parse_cobol_ledger(games, "rankings_report.txt")
    comp_rk = {r["team"]: i for i, r in enumerate(board, 1)}

    for i, r in enumerate(board, 1):
        r["comp_rk"] = i
        drv, drv_cls = determine_driver(r["elo_rk"], r["colley_rk"], r["massey_rk"])
        r["driver"] = drv
        r["driver_cls"] = drv_cls
        defeated = wins_by_team.get(r["team"], [])
        if defeated:
            best_opp = min(defeated, key=lambda opp: comp_rk.get(opp, 999))
            b_rk = comp_rk.get(best_opp)
            r["best_win"] = f"#{b_rk} {best_opp}" if b_rk else best_opp
        else:
            r["best_win"] = "--"
        r["why"] = build_plain_english_why(r, team_logs.get(r["team"], []), comp_rk)

    ref = "CFP" if cfp else "AP"
    trends = log_history_and_get_trends(board, f"Through Wk {max_wk}")
    predictions = predict_upcoming_upsets(upcoming, elo, comp_rk)
    bracket_data = build_projected_cfp_bracket(board, elo)

    print(f"\n2026 POLYGLOT COMPUTER POLL (THROUGH WEEK {max_wk}) vs. ESPN {label}")
    print(f"{'RK':<4} {'FBS TEAM':<18} {'TIER':<5} {'REC':<6} {'ELO':>9} {'COLLEY':>8} {'MASSEY':>8} {'COMP':>6} {'E·C·M RKS':<12} {'DRIVER':<11} {'SOS':>4} {'BEST WIN':<18} {ref:>4} {'DLT':>4}")
    print("-" * 129)
    for rk, r in enumerate(board[:25], 1):
        hrk = r["cfp"] if cfp else r["ap"]
        rk_s = f"#{hrk}" if hrk else "NR"
        d_s = f"{hrk - rk:+d}" if (hrk and hrk != rk) else ("0" if hrk else "NEW")
        ecm_s = f"#{r['elo_rk']}·#{r['colley_rk']}·#{r['massey_rk']}"
        sos_s = f"#{r['sos_rk']}"
        print(
            f"{rk:<4} {r['team']:<18} {r['tier']:<5} {r['rec']:<6} {r['elo']:>9.2f} {r['colley']:>8.4f} "
            f"{r['massey']:>+8.2f} {r['comp']:>6.2f} {ecm_s:<12} {r['driver']:<11} {sos_s:>4} {r['best_win']:<18.18} {rk_s:>4} {d_s:>4}"
        )

    byes, first_round, bubble_out = bracket_data
    print("\nPROJECTED 12-TEAM CFP BRACKET (2026 FORMAT)")
    print("First-Round Byes: " + ", ".join(f"#{b['seed']} {b['team']} ({b['bid']})" for b in byes))
    for m in first_round:
        print(
            f"  R1: #{m['dog']['seed']} {m['dog']['team']} ({m['dog']['bid']}) @ "
            f"#{m['fav']['seed']} {m['fav']['team']} ({m['fav']['bid']}) "
            f"[Line: {m['fav']['team']} -{m['spread']:.1f} | {m['fav']['team']} {m['fav_prob']:.1f}%]"
        )
    if bubble_out:
        print("First Two Out: " + ", ".join(f"#{b['comp_rk']} {b['team']} ({b['rec']})" for b in bubble_out))

    active = cfp if cfp else ap
    top25 = {r["team"] for r in board[:25]}
    snubs = [
        f"#{rk} {t} (Comp #{comp_rk.get(t, '?')})"
        for t, rk in sorted(active.items(), key=lambda x: x[1])
        if t not in top25
    ]
    if snubs:
        print(f"\n{ref} Top 25 Teams Outside Computer Top 25: {', '.join(snubs)}")

    if predictions:
        print(f"\nWEEK {next_week} UPSET WATCH (COBOL ELO WIN PROBABILITY)")
        print(f"{'ALERT':<11} {'UNDERDOG':<24} {'':<2} {'FAVORITE':<24} {'LINE':>8} {'UPSET %':>9}")
        print("-" * 83)
        for p in predictions[:8]:
            dog_s = fmt_rk_name(p["dog"], p["dog_rk"])
            fav_s = fmt_rk_name(p["fav"], p["fav_rk"])
            line_s = f"-{p['spread']:.1f}"
            print(f"{p['alert']:<11} {dog_s:<24} {p['venue']:<2} {fav_s:<24} {line_s:>8} {p['dog_prob']:>8.1f}%")

    generate_html_dashboard(
        board, trends, swings, team_logs, comp_rk, predictions, next_week, max_wk, in_progress, bracket_data, snubs, label, ref
    )
    print("\nSaved rankings_history.csv and updated dashboard.html (run: open dashboard.html)")


if __name__ == "__main__":
    main()