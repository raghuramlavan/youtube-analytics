#!/usr/bin/env python3
"""
generate_dashboard.py
Generates a self-contained HTML dashboard from an analyzed YouTube JSON file.

Handles ALL formats:
  FORMAT A — Single video:  { "video_metadata": {...}, "comments": [...] }
  FORMAT B — Multi video:   { "videos": [ { "comments": [...], ... } ] }

For single video:  daily axis = comment published_at  (comments spread over days)
For multi video:   daily axis = video published_at    (videos uploaded over days)

Handles both old prompt fields (player_name, country, theme)
and new prompt fields (players_or_personalities, nike_products_mentioned, etc.)

Usage:
    python generate_dashboard.py <json_file> [-o output.html]
"""

import json, argparse, sys
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

# ── Normalise helpers ──────────────────────────────────────────────────────────

def norm_sentiment(s):
    if not s: return "neutral"
    s = str(s).strip().lower()
    if s.startswith("pos"): return "positive"
    if s.startswith("neg"): return "negative"
    return "neutral"

def date_only(ts):
    if not ts: return "Unknown"
    return str(ts)[:10]

def safe_int(v):
    try: return int(v or 0)
    except: return 0

def js(obj):
    return json.dumps(obj, ensure_ascii=False)


# ── Format detection ───────────────────────────────────────────────────────────

def detect_format(data):
    if "videos" in data and isinstance(data["videos"], list) and data["videos"]:
        return "multi"
    if "video_metadata" in data or "comments" in data:
        return "single"
    return "unknown"


# ── Comment field extraction (old + new prompt) ────────────────────────────────

def extract_comment_fields(c):
    """
    Returns normalised fields from a comment dict.
    Supports old format (player_name/country/theme) and new format (players_or_personalities/...).
    """
    sentiment = norm_sentiment(c.get("sentiment") or c.get("Sentiment"))

    # New prompt format
    players = c.get("players_or_personalities") or []
    players_sent = c.get("players_sentiment") or []
    products = c.get("nike_products_mentioned") or []
    comp_cmp  = c.get("competitor_comparison") or "none"
    brands    = c.get("competitor_brands_mentioned") or []
    themes    = []
    countries = []

    # Old prompt format fallback
    if not players and c.get("player_name") and c["player_name"] not in (None, "null", ""):
        players = [c["player_name"]]
        players_sent = [sentiment]
    if c.get("country") and c["country"] not in (None, "null", ""):
        countries = [c["country"]]
    if c.get("theme") and c["theme"] not in (None, "null", ""):
        themes = [c["theme"]]

    return {
        "sentiment":   sentiment,
        "players":     [p for p in players if p] if isinstance(players, list) else [],
        "players_sent":[s for s in players_sent if s] if isinstance(players_sent, list) else [],
        "products":    [p for p in products if p] if isinstance(products, list) else [],
        "comp_cmp":    comp_cmp,
        "brands":      [b for b in brands if b] if isinstance(brands, list) else [],
        "themes":      themes,
        "countries":   countries,
        "text":        (c.get("text") or "")[:250],
        "likes":       safe_int(c.get("like_count")),
        "author":      c.get("author", ""),
        "date":        date_only(c.get("published_at")),
        "is_analyzed": bool(c.get("sentiment")),
    }


# ── Data extraction ────────────────────────────────────────────────────────────

def extract_single(data):
    """Single-video format. Returns (mode, video_meta, flat_comment_list)."""
    vm = data.get("video_metadata", {})
    top_comments = data.get("comments", [])
    flat = []
    for c in top_comments:
        flat.append(extract_comment_fields(c))
        for r in c.get("replies", []):
            flat.append(extract_comment_fields(r))
    return "single", [vm], flat


def extract_multi(data):
    """Multi-video format. Returns (mode, video_list_with_stats, flat_comment_list)."""
    videos_out = []
    flat_all   = []

    for v in data.get("videos", []):
        v_comments = []
        for c in v.get("comments", []):
            cf = extract_comment_fields(c)
            v_comments.append(cf)
            flat_all.append(cf)
            for r in c.get("replies", []):
                rf = extract_comment_fields(r)
                v_comments.append(rf)
                flat_all.append(rf)

        # per-video aggregates
        sentiments = Counter(cf["sentiment"] for cf in v_comments)
        players    = Counter(p for cf in v_comments for p in cf["players"])
        products   = Counter(p for cf in v_comments for p in cf["products"])
        brands     = Counter(b for cf in v_comments for b in cf["brands"])
        themes     = Counter(t for cf in v_comments for t in cf["themes"])
        top_cmts   = sorted(v_comments, key=lambda x: x["likes"], reverse=True)[:5]
        dom        = sentiments.most_common(1)[0][0] if sentiments else "neutral"

        videos_out.append({
            "video_id":      v.get("video_id", ""),
            "title":         (v.get("title") or "")[:70],
            "channel":       v.get("channel", ""),
            "published_at":  date_only(v.get("published_at")),
            "view_count":    safe_int(v.get("view_count")),
            "like_count":    safe_int(v.get("like_count")),
            "comment_count": safe_int(v.get("comment_count_reported") or len(v_comments)),
            "sentiments":    dict(sentiments),
            "dom_sentiment": dom,
            "is_analyzed":   any(cf["is_analyzed"] for cf in v_comments),
            "players":       players.most_common(5),
            "products":      products.most_common(5),
            "brands":        brands.most_common(5),
            "themes":        themes.most_common(5),
            "top_comments":  top_cmts,
            "total_comments_fetched": len(v_comments),
        })

    return "multi", videos_out, flat_all


# ── Stats builder ──────────────────────────────────────────────────────────────

def build_stats(mode, videos, flat_comments, data):
    """
    mode     : 'single' | 'multi'
    videos   : list of video dicts
    flat_comments: list of extracted comment field dicts
    """
    total_videos   = len(videos)
    total_analyzed = sum(1 for c in flat_comments if c["is_analyzed"])
    total_comments = len(flat_comments)

    # ── Global comment aggregates ──
    all_sentiments = Counter(c["sentiment"] for c in flat_comments if c["is_analyzed"])
    all_players    = Counter(p for c in flat_comments for p in c["players"])
    all_products   = Counter(p for c in flat_comments for p in c["products"])
    all_brands     = Counter(b for c in flat_comments for b in c["brands"])
    all_themes     = Counter(t for c in flat_comments for t in c["themes"])
    all_countries  = Counter(co for c in flat_comments for co in c["countries"])

    # per-player sentiment breakdown
    player_pos = Counter(); player_neg = Counter(); player_neu = Counter()
    for c in flat_comments:
        for i, p in enumerate(c["players"]):
            ps = c["players_sent"][i] if i < len(c["players_sent"]) else c["sentiment"]
            ps = norm_sentiment(ps)
            if ps == "positive":  player_pos[p] += 1
            elif ps == "negative": player_neg[p] += 1
            else:                  player_neu[p] += 1

    # competitor comparison counts
    comp_pos = sum(1 for c in flat_comments if c["comp_cmp"] == "positive")
    comp_neg = sum(1 for c in flat_comments if c["comp_cmp"] == "negative")
    comp_neu = sum(1 for c in flat_comments if c["comp_cmp"] == "neutral")

    # Top liked comments
    top_liked = sorted(flat_comments, key=lambda x: x["likes"], reverse=True)[:50]

    # ── DAILY breakdown ──
    # For single video → daily axis = comment date
    # For multi video  → daily axis = video published_at
    if mode == "single":
        vm = data.get("video_metadata", {})
        # Comments per day
        day_cmts        = Counter()
        day_pos_cmts    = Counter()
        day_neg_cmts    = Counter()
        day_neu_cmts    = Counter()
        for c in flat_comments:
            d = c["date"]
            day_cmts[d] += 1
            if c["is_analyzed"]:
                s = c["sentiment"]
                if s == "positive":  day_pos_cmts[d] += 1
                elif s == "negative": day_neg_cmts[d] += 1
                else:                 day_neu_cmts[d] += 1
        all_days = sorted(d for d in day_cmts if d != "Unknown")

        # single video details
        single_video_meta = {
            "title":    vm.get("title", "Video"),
            "channel":  vm.get("channel", ""),
            "published": date_only(vm.get("published_at")),
            "views":    safe_int(vm.get("view_count")),
            "likes":    safe_int(vm.get("like_count")),
            "comments": safe_int(vm.get("comment_count")),
            "video_id": vm.get("video_id", ""),
        }

        return {
            "mode": "single",
            "total_videos":   1,
            "total_comments": total_comments,
            "total_analyzed": total_analyzed,
            "all_sentiments": dict(all_sentiments),
            "all_players":    all_players.most_common(20),
            "all_products":   all_products.most_common(20),
            "all_brands":     all_brands.most_common(20),
            "all_themes":     all_themes.most_common(20),
            "all_countries":  all_countries.most_common(20),
            "player_pos": dict(player_pos), "player_neg": dict(player_neg), "player_neu": dict(player_neu),
            "comp_pos": comp_pos, "comp_neg": comp_neg, "comp_neu": comp_neu,
            "top_liked": top_liked,
            "all_days":        all_days,
            "day_cmts":        dict(day_cmts),
            "day_pos_cmts":    dict(day_pos_cmts),
            "day_neg_cmts":    dict(day_neg_cmts),
            "day_neu_cmts":    dict(day_neu_cmts),
            "day_vid_total":   {},
            "day_vid_pos":     {},
            "day_vid_neg":     {},
            "day_vid_neu":     {},
            "video_summaries": [],
            "single_video_meta": single_video_meta,
        }

    else:  # multi
        day_vid_total = Counter()
        day_vid_pos   = Counter()
        day_vid_neg   = Counter()
        day_vid_neu   = Counter()
        day_cmts      = Counter()
        day_pos_cmts  = Counter()
        day_neg_cmts  = Counter()
        day_neu_cmts  = Counter()

        for v in videos:
            d = v["published_at"]
            day_vid_total[d] += 1
            dom = v["dom_sentiment"]
            if dom == "positive":   day_vid_pos[d] += 1
            elif dom == "negative": day_vid_neg[d] += 1
            else:                   day_vid_neu[d] += 1

        for c in flat_comments:
            d = c["date"]
            day_cmts[d] += 1
            if c["is_analyzed"]:
                s = c["sentiment"]
                if s == "positive":   day_pos_cmts[d] += 1
                elif s == "negative": day_neg_cmts[d] += 1
                else:                 day_neu_cmts[d] += 1

        all_days = sorted(d for d in day_vid_total if d != "Unknown")

        return {
            "mode": "multi",
            "total_videos":   total_videos,
            "total_comments": total_comments,
            "total_analyzed": total_analyzed,
            "all_sentiments": dict(all_sentiments),
            "all_players":    all_players.most_common(20),
            "all_products":   all_products.most_common(20),
            "all_brands":     all_brands.most_common(20),
            "all_themes":     all_themes.most_common(20),
            "all_countries":  all_countries.most_common(20),
            "player_pos": dict(player_pos), "player_neg": dict(player_neg), "player_neu": dict(player_neu),
            "comp_pos": comp_pos, "comp_neg": comp_neg, "comp_neu": comp_neu,
            "top_liked": top_liked,
            "all_days":       all_days,
            "day_vid_total":  dict(day_vid_total),
            "day_vid_pos":    dict(day_vid_pos),
            "day_vid_neg":    dict(day_vid_neg),
            "day_vid_neu":    dict(day_vid_neu),
            "day_cmts":       dict(day_cmts),
            "day_pos_cmts":   dict(day_pos_cmts),
            "day_neg_cmts":   dict(day_neg_cmts),
            "day_neu_cmts":   dict(day_neu_cmts),
            "video_summaries": videos,
            "single_video_meta": {},
        }


# ── HTML builder ───────────────────────────────────────────────────────────────

def generate_html(stats, query, source_file):
    mode = stats["mode"]
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    tot_v   = stats["total_videos"]
    tot_c   = stats["total_comments"]
    tot_a   = stats["total_analyzed"]
    pos_c   = stats["all_sentiments"].get("positive", 0)
    neg_c   = stats["all_sentiments"].get("negative", 0)
    neu_c   = stats["all_sentiments"].get("neutral", 0)
    base    = tot_a or 1
    mode_label = "Single Video" if mode == "single" else f"{tot_v} Videos"

    # ── Single video banner ──
    sv = stats.get("single_video_meta", {})
    single_banner = ""
    if mode == "single":
        yt_url = f"https://youtube.com/watch?v={sv.get('video_id','')}" if sv.get('video_id') else "#"
        single_banner = f"""
    <div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px 28px;margin:24px 32px 0">
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
        <div style="font-size:28px">🎬</div>
        <div style="flex:1">
          <a href="{yt_url}" target="_blank" style="color:#60a5fa;font-size:18px;font-weight:700;text-decoration:none">{sv.get('title','')}</a>
          <div style="color:#94a3b8;font-size:13px;margin-top:4px">
            📺 {sv.get('channel','')} &nbsp;|&nbsp; 📅 Published {sv.get('published','')}
          </div>
        </div>
        <div style="display:flex;gap:24px;flex-wrap:wrap">
          <div style="text-align:center"><div style="font-size:22px;font-weight:700;color:#fff">{sv.get('views',0):,}</div><div style="font-size:11px;color:#64748b">Views</div></div>
          <div style="text-align:center"><div style="font-size:22px;font-weight:700;color:#f43f5e">{sv.get('likes',0):,}</div><div style="font-size:11px;color:#64748b">Likes</div></div>
          <div style="text-align:center"><div style="font-size:22px;font-weight:700;color:#60a5fa">{sv.get('comments',0):,}</div><div style="font-size:11px;color:#64748b">Comments</div></div>
        </div>
      </div>
    </div>"""

    # ── KPI analysis warning ──
    analysis_warn = ""
    if tot_a == 0:
        analysis_warn = """<div style="background:#422006;border:1px solid #92400e;border-radius:8px;padding:12px 20px;margin-bottom:20px;color:#fbbf24">
          ⚠️ <strong>No analysed comments found.</strong> Run <code>analyze_comments_deepseek.py</code> first to see sentiment, players, products, and brand data.
        </div>"""

    # ── Video table rows ──
    # IMPORTANT: pass video index not raw JSON in onclick to avoid quote-breaking HTML attributes
    video_rows = ""
    sorted_videos = sorted(stats["video_summaries"], key=lambda x: x["published_at"], reverse=True)[:500]
    for idx, v in enumerate(sorted_videos):
        s = v["sentiments"]
        pos = s.get("positive", 0); neg = s.get("negative", 0); neu = s.get("neutral", 0)
        tot_s = pos + neg + neu or 1
        dom = v["dom_sentiment"]
        dc = {"positive": "#22c55e", "negative": "#ef4444", "neutral": "#f59e0b"}.get(dom, "#94a3b8")
        badge = f'<span style="background:{dc}22;color:{dc};padding:2px 8px;border-radius:12px;font-size:11px;border:1px solid {dc}55">{dom}</span>'
        top_player  = v["players"][0][0]  if v["players"]  else "—"
        top_product = v["products"][0][0] if v["products"] else "—"
        top_brand   = v["brands"][0][0]   if v["brands"]   else "—"
        yt_link = f"https://youtube.com/watch?v={v['video_id']}" if v.get("video_id") else "#"
        analyzed_icon = "✅" if v.get("is_analyzed") else "⏳"
        # Escape title for safe display in HTML (not in JS)
        safe_title = v["title"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        bar = (f'<div style="display:flex;height:6px;border-radius:3px;overflow:hidden;min-width:80px">'
               f'<div style="width:{pos*100//tot_s}%;background:#22c55e"></div>'
               f'<div style="width:{neu*100//tot_s}%;background:#f59e0b"></div>'
               f'<div style="width:{neg*100//tot_s}%;background:#ef4444"></div></div>'
               f'<div style="font-size:10px;color:#64748b;margin-top:2px">+{pos} /{neu} -{neg}</div>')
        # Use index reference — never embed raw JSON in HTML attributes
        video_rows += (
            f'<tr onclick="showVideoModal({idx})" style="cursor:pointer">'
            f'<td>{v["published_at"]}</td>'
            f'<td><a href="{yt_link}" target="_blank" onclick="event.stopPropagation()" '
            f'style="color:#60a5fa;text-decoration:none">{safe_title}</a></td>'
            f'<td style="color:#94a3b8">{v["channel"]}</td>'
            f'<td style="text-align:right">{v["view_count"]:,}</td>'
            f'<td style="text-align:right">{v["total_comments_fetched"]:,}</td>'
            f'<td>{analyzed_icon}</td>'
            f'<td>{badge}</td><td>{bar}</td>'
            f'<td>{top_player}</td><td>{top_product}</td><td>{top_brand}</td></tr>\n'
        )

    # ── Player table rows ──
    player_rows = ""
    for player, cnt in stats["all_players"]:
        pos = stats["player_pos"].get(player, 0)
        neg = stats["player_neg"].get(player, 0)
        neu = stats["player_neu"].get(player, 0)
        tot = pos + neg + neu or 1
        player_rows += (
            f'<tr><td style="font-weight:600;color:#e2e8f0">{player}</td>'
            f'<td style="text-align:right">{cnt}</td>'
            f'<td style="text-align:right;color:#22c55e">{pos}</td>'
            f'<td style="text-align:right;color:#f59e0b">{neu}</td>'
            f'<td style="text-align:right;color:#ef4444">{neg}</td>'
            f'<td><div style="display:flex;height:6px;border-radius:3px;overflow:hidden;min-width:120px">'
            f'<div style="width:{pos*100//tot}%;background:#22c55e"></div>'
            f'<div style="width:{neu*100//tot}%;background:#f59e0b"></div>'
            f'<div style="width:{neg*100//tot}%;background:#ef4444"></div>'
            f'</div></td></tr>\n'
        )

    # ── Daily table rows ──
    daily_rows = ""
    for d in sorted(stats["all_days"], reverse=True):
        vt = stats["day_vid_total"].get(d, 0)
        vp = stats["day_vid_pos"].get(d, 0)
        vn = stats["day_vid_neg"].get(d, 0)
        vz = stats["day_vid_neu"].get(d, 0)
        ct = stats["day_cmts"].get(d, 0)
        cp = stats["day_pos_cmts"].get(d, 0)
        cn = stats["day_neg_cmts"].get(d, 0)
        cz = stats["day_neu_cmts"].get(d, 0)
        if mode == "single":
            daily_rows += (f'<tr><td>{d}</td><td style="text-align:right">{ct:,}</td>'
                           f'<td style="text-align:right;color:#22c55e">{cp:,}</td>'
                           f'<td style="text-align:right;color:#f59e0b">{cz:,}</td>'
                           f'<td style="text-align:right;color:#ef4444">{cn:,}</td></tr>\n')
        else:
            daily_rows += (f'<tr><td>{d}</td>'
                           f'<td style="text-align:right;font-weight:600">{vt}</td>'
                           f'<td style="text-align:right;color:#22c55e">{vp}</td>'
                           f'<td style="text-align:right;color:#f59e0b">{vz}</td>'
                           f'<td style="text-align:right;color:#ef4444">{vn}</td>'
                           f'<td style="text-align:right">{ct:,}</td>'
                           f'<td style="text-align:right;color:#22c55e">{cp:,}</td>'
                           f'<td style="text-align:right;color:#f59e0b">{cz:,}</td>'
                           f'<td style="text-align:right;color:#ef4444">{cn:,}</td></tr>\n')

    daily_thead = (
        '<tr><th>Date</th><th>Comments</th>'
        '<th style="color:#22c55e">+ve</th><th style="color:#f59e0b">Neu</th><th style="color:#ef4444">-ve</th></tr>'
        if mode == "single" else
        '<tr><th>Date</th><th>Videos</th>'
        '<th style="color:#22c55e">+ve Videos</th><th style="color:#f59e0b">Neu Videos</th><th style="color:#ef4444">-ve Videos</th>'
        '<th>Comments</th><th style="color:#22c55e">+ve Cmts</th><th style="color:#f59e0b">Neu Cmts</th><th style="color:#ef4444">-ve Cmts</th></tr>'
    )

    # daily chart section changes by mode
    daily_video_chart = "" if mode == "single" else """
    <div class="card">
      <h3>📦 Videos Uploaded Per Day</h3>
      <canvas id="videosPerDayBar" style="max-height:260px"></canvas>
    </div>
    <div class="card">
      <h3>📊 Videos Per Day — Sentiment Breakdown (dominant sentiment per video)</h3>
      <canvas id="vidSentPerDay" style="max-height:260px"></canvas>
    </div>"""

    comment_day_label = "Comments Posted Per Day" if mode == "single" else "Comments Per Day — Sentiment Breakdown"

    # Pre-compute conditionals that contain quotes — can't use backslash inside f-string expressions (Python < 3.12)
    videos_nav_btn = '<button onclick="showPage(\'videos\',this)">🎬 All Videos</button>' if mode == "multi" else ""

    return f"""<!DOCTYPE html>
  <html lang="en">
  <head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Nike YouTube Analytics — {mode_label}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
  .header{{background:linear-gradient(135deg,#1e3a5f 0%,#0f172a 100%);padding:20px 32px;border-bottom:1px solid #1e293b}}
  .header h1{{font-size:22px;font-weight:700;color:#fff;display:flex;align-items:center;gap:10px}}
  .header .sub{{color:#94a3b8;font-size:12px;margin-top:5px}}
  .nav{{display:flex;gap:4px;padding:10px 32px;background:#0f172a;border-bottom:1px solid #1e293b;flex-wrap:wrap;position:sticky;top:0;z-index:10}}
  .nav button{{padding:7px 14px;border:none;border-radius:8px;cursor:pointer;font-size:12px;font-weight:600;
    background:#1e293b;color:#64748b;transition:all .15s}}
  .nav button:hover{{background:#2d3f55;color:#e2e8f0}}
  .nav button.active{{background:#3b82f6;color:#fff}}
  .page{{display:none;padding:20px 32px;max-width:1700px;margin:0 auto}}
  .page.active{{display:block}}
  .kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:20px}}
  .kpi{{background:#1e293b;border-radius:12px;padding:18px 20px;border:1px solid #334155;position:relative;overflow:hidden}}
  .kpi::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--kpi-color,#3b82f6)}}
  .kpi .val{{font-size:28px;font-weight:800;color:#fff;line-height:1}}
  .kpi .lbl{{font-size:11px;color:#64748b;margin-top:6px;text-transform:uppercase;letter-spacing:.5px}}
  .kpi .pct{{font-size:12px;margin-top:4px;font-weight:600}}
  .card{{background:#1e293b;border-radius:12px;padding:18px 20px;border:1px solid #334155;margin-bottom:18px}}
  .card h3{{font-size:13px;font-weight:700;color:#94a3b8;margin-bottom:14px;text-transform:uppercase;letter-spacing:.5px}}
  .g2{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
  .g3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px}}
  .g4{{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:14px}}
  @media(max-width:1100px){{.g4{{grid-template-columns:1fr 1fr}}.g3{{grid-template-columns:1fr 1fr}}}}
  @media(max-width:700px){{.g2,.g3,.g4{{grid-template-columns:1fr}}}}
  canvas{{max-height:300px}}
  table{{width:100%;border-collapse:collapse;font-size:12px}}
  th{{padding:9px 12px;text-align:left;color:#64748b;font-weight:600;border-bottom:1px solid #334155;font-size:11px;text-transform:uppercase;letter-spacing:.3px;white-space:nowrap}}
  td{{padding:8px 12px;border-bottom:1px solid #1a2535;color:#cbd5e1;vertical-align:middle}}
  tbody tr:hover td{{background:#243448}}
  .search-bar{{width:100%;padding:9px 16px;background:#0f172a;border:1px solid #334155;border-radius:8px;
    color:#e2e8f0;font-size:13px;margin-bottom:14px}}
  .search-bar:focus{{outline:none;border-color:#3b82f6}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}}
  .chip{{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;margin:2px}}
  .modal-bg{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:200;overflow-y:auto;padding:32px 16px}}
  .modal{{background:#1e293b;border-radius:16px;max-width:680px;margin:0 auto;padding:24px;border:1px solid #334155}}
  .modal-close{{float:right;background:none;border:none;color:#64748b;font-size:22px;cursor:pointer;line-height:1}}
  .modal-close:hover{{color:#fff}}
  ::-webkit-scrollbar{{width:5px;height:5px}}
  ::-webkit-scrollbar-track{{background:#0f172a}}
  ::-webkit-scrollbar-thumb{{background:#334155;border-radius:3px}}
  .no-data{{color:#475569;text-align:center;padding:24px;font-style:italic}}
  </style>
  </head>
  <body>

  <div class="header">
    <h1>🎯 Nike YouTube Analytics Dashboard <span style="font-size:13px;background:#1e293b;padding:3px 10px;border-radius:8px;font-weight:500;color:#60a5fa">{mode_label}</span></h1>
    <div class="sub">Query: <strong>{query}</strong> &nbsp;·&nbsp; Source: {source_file} &nbsp;·&nbsp; Generated: {generated_at}
      &nbsp;·&nbsp; {tot_c:,} comments ({tot_a:,} analysed)
    </div>
  </div>

  {single_banner}

  <div class="nav">
    <button class="active" onclick="showPage('overview',this)">📊 Overview</button>
    <button onclick="showPage('daily',this)">📅 Daily Trends</button>
    <button onclick="showPage('players',this)">👤 Players</button>
    <button onclick="showPage('products',this)">👟 Products & Brands</button>
    {videos_nav_btn}
    <button onclick="showPage('comments',this)">💬 Top Comments</button>
  </div>

  <!-- ══════════════════════════════════════════ OVERVIEW ══ -->
  <div id="page-overview" class="page active">
  {analysis_warn}
    <div class="kpi-grid">
      <div class="kpi" style="--kpi-color:#3b82f6">
        <div class="val">{tot_v:,}</div>
        <div class="lbl">{'Video' if mode=='single' else 'Videos'}</div>
      </div>
      <div class="kpi" style="--kpi-color:#8b5cf6">
        <div class="val">{tot_c:,}</div>
        <div class="lbl">Comments Fetched</div>
      </div>
      <div class="kpi" style="--kpi-color:#06b6d4">
        <div class="val">{tot_a:,}</div>
        <div class="lbl">Comments Analysed</div>
      </div>
      <div class="kpi" style="--kpi-color:#22c55e">
        <div class="val" style="color:#22c55e">{pos_c:,}</div>
        <div class="lbl">Positive Comments</div>
        <div class="pct" style="color:#22c55e">{pos_c*100//base}%</div>
      </div>
      <div class="kpi" style="--kpi-color:#f59e0b">
        <div class="val" style="color:#f59e0b">{neu_c:,}</div>
        <div class="lbl">Neutral Comments</div>
        <div class="pct" style="color:#f59e0b">{neu_c*100//base}%</div>
      </div>
      <div class="kpi" style="--kpi-color:#ef4444">
        <div class="val" style="color:#ef4444">{neg_c:,}</div>
        <div class="lbl">Negative Comments</div>
        <div class="pct" style="color:#ef4444">{neg_c*100//base}%</div>
      </div>
      <div class="kpi" style="--kpi-color:#a78bfa">
        <div class="val">{len(stats['all_players'])}</div>
        <div class="lbl">Players Mentioned</div>
      </div>
      <div class="kpi" style="--kpi-color:#34d399">
        <div class="val">{len(stats['all_products'])}</div>
        <div class="lbl">Nike Products</div>
      </div>
      <div class="kpi" style="--kpi-color:#f87171">
        <div class="val">{len(stats['all_brands'])}</div>
        <div class="lbl">Competitor Brands</div>
      </div>
      <div class="kpi" style="--kpi-color:#fb923c">
        <div class="val">{len(stats['all_days'])}</div>
        <div class="lbl">Days Covered</div>
      </div>
    </div>

    <div class="g2">
      <div class="card"><h3>🥧 Overall Sentiment Split</h3><canvas id="sentDonut"></canvas></div>
      <div class="card"><h3>⚔️ Competitor Comparison Sentiment</h3><canvas id="compDonut"></canvas></div>
    </div>

    <div class="g2">
      <div class="card"><h3>📅 {'Comments Per Day' if mode=='single' else 'Videos Uploaded Per Day'}</h3><canvas id="overviewDailyBar"></canvas></div>
      <div class="card"><h3>👤 Top Players / Personalities</h3><canvas id="overviewPlayers"></canvas></div>
    </div>

    <div class="g3">
      <div class="card"><h3>👟 Top Nike Products</h3>
        <table><thead><tr><th>Product</th><th>Mentions</th></tr></thead><tbody>
        {"".join(f'<tr><td>{p}</td><td style="text-align:right;color:#60a5fa;font-weight:600">{n}</td></tr>' for p,n in stats['all_products'][:12]) or '<tr><td colspan=2 class=no-data>No product data yet</td></tr>'}
        </tbody></table></div>
      <div class="card"><h3>🏢 Competitor Brands</h3>
        <table><thead><tr><th>Brand</th><th>Mentions</th></tr></thead><tbody>
        {"".join(f'<tr><td>{b}</td><td style="text-align:right;color:#f87171;font-weight:600">{n}</td></tr>' for b,n in stats['all_brands'][:12]) or '<tr><td colspan=2 class=no-data>No competitor data yet</td></tr>'}
        </tbody></table></div>
      <div class="card"><h3>🌍 Top Countries / Themes</h3>
        <table><thead><tr><th>{'Country' if stats['all_countries'] else 'Theme'}</th><th>Count</th></tr></thead><tbody>
        {"".join(f'<tr><td>{x}</td><td style="text-align:right;color:#a78bfa;font-weight:600">{n}</td></tr>' for x,n in (stats['all_countries'] or stats['all_themes'])[:12]) or '<tr><td colspan=2 class=no-data>No data yet</td></tr>'}
        </tbody></table></div>
    </div>
  </div>

  <!-- ══════════════════════════════════════════ DAILY ══ -->
  <div id="page-daily" class="page">
  {daily_video_chart}
    <div class="card">
      <h3>💬 {comment_day_label}</h3>
      <canvas id="cmtSentPerDay" style="max-height:280px"></canvas>
    </div>
    <div class="card">
      <h3>📈 Sentiment % Ratio Over Time</h3>
      <canvas id="sentRatioLine" style="max-height:240px"></canvas>
    </div>
    <div class="card">
      <h3>📋 Daily Summary Table</h3>
      <div style="overflow-x:auto">
      <table><thead>{daily_thead}</thead><tbody>{daily_rows}</tbody></table>
      </div>
    </div>
  </div>

  <!-- ══════════════════════════════════════════ PLAYERS ══ -->
  <div id="page-players" class="page">
    <div class="card"><h3>🏆 Player Sentiment Stacked Bar</h3>
      <canvas id="playerStackBar" style="max-height:360px"></canvas></div>
    <div class="card"><h3>👤 Player Detail Table</h3>
      <table><thead><tr>
        <th>Player / Personality</th><th>Total Mentions</th>
        <th style="color:#22c55e">Positive</th>
        <th style="color:#f59e0b">Neutral</th>
        <th style="color:#ef4444">Negative</th>
        <th>Sentiment Bar</th>
      </tr></thead><tbody>
      {player_rows or '<tr><td colspan=6 class=no-data>No player data — run DeepSeek analysis first</td></tr>'}
      </tbody></table></div>
  </div>

  <!-- ══════════════════════════════════════════ PRODUCTS ══ -->
  <div id="page-products" class="page">
    <div class="g2">
      <div class="card"><h3>👟 Nike Products</h3><canvas id="productsBar"></canvas></div>
      <div class="card"><h3>🏢 Competitor Brands</h3><canvas id="brandsBar"></canvas></div>
    </div>
    <div class="g2">
      <div class="card"><h3>🥧 Nike Products Share</h3><canvas id="productsPie"></canvas></div>
      <div class="card"><h3>🥧 Competitor Brand Share</h3><canvas id="brandsPie"></canvas></div>
    </div>
  </div>

  <!-- ══════════════════════════════════════════ VIDEOS (multi only) ══ -->
  {"" if mode == "single" else '''
  <div id="page-videos" class="page">
    <div class="card">
      <h3>🎬 All Videos</h3>
      <input type="text" class="search-bar" placeholder="🔍 Search by title, channel, player, product…" oninput="filterVids(this.value)">
      <div style="overflow-x:auto">
      <table id="vidTable">
        <thead><tr>
          <th>Date</th><th>Title</th><th>Channel</th><th style="text-align:right">Views</th>
          <th style="text-align:right">Comments</th><th>Analysed</th><th>Dominant</th>
          <th>Sentiment</th><th>Top Player</th><th>Top Product</th><th>Top Brand</th>
        </tr></thead>
        <tbody id="vidTableBody">''' + video_rows + '''</tbody>
      </table></div>
    </div>
  </div>'''}

  <!-- ══════════════════════════════════════════ COMMENTS ══ -->
  <div id="page-comments" class="page">
    <div class="card">
      <h3>💬 Top 50 Most-Liked Comments</h3>
      <div id="topCmtList"></div>
    </div>
  </div>

  <!-- ══ Modal ══ -->
  <div class="modal-bg" id="modalBg" onclick="if(event.target===this)closeModal()">
    <div class="modal">
      <button class="modal-close" onclick="closeModal()">✕</button>
      <h2 id="modalTitle" style="font-size:16px;color:#fff;margin-bottom:16px;padding-right:30px"></h2>
      <div id="modalBody"></div>
    </div>
  </div>

  <script>
  // ── Raw data ───────────────────────────────────────────────────────────────────
  const MODE       = {js(mode)};
  const DAYS       = {js(stats['all_days'])};
  const DAY_VT     = {js(stats['day_vid_total'])};
  const DAY_VP     = {js(stats['day_vid_pos'])};
  const DAY_VN     = {js(stats['day_vid_neg'])};
  const DAY_VZ     = {js(stats['day_vid_neu'])};
  const DAY_CT     = {js(stats['day_cmts'])};
  const DAY_CP     = {js(stats['day_pos_cmts'])};
  const DAY_CN     = {js(stats['day_neg_cmts'])};
  const DAY_CZ     = {js(stats['day_neu_cmts'])};
  const PLAYERS    = {js(stats['all_players'])};
  const PPOS       = {js(stats['player_pos'])};
  const PNEG       = {js(stats['player_neg'])};
  const PNEU       = {js(stats['player_neu'])};
  const PRODUCTS   = {js(stats['all_products'])};
  const BRANDS     = {js(stats['all_brands'])};
  const SENTS      = {js(stats['all_sentiments'])};
  const COMP_POS   = {stats['comp_pos']};
  const COMP_NEG   = {stats['comp_neg']};
  const COMP_NEU   = {stats['comp_neu']};
  const TOP_LIKED  = {js(stats['top_liked'])};
  const VIDEO_DATA = {js(sorted_videos)};

  // ── HTML escape helper (prevents comment text breaking innerHTML) ─────────────
  function esc(str) {{
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }}

  // ── Chart colours ─────────────────────────────────────────────────────────────
  Chart.defaults.color = '#64748b';
  Chart.defaults.borderColor = '#1e293b';
  const C = {{
    pos:'#22c55e', neg:'#ef4444', neu:'#f59e0b',
    blue:'#3b82f6', purple:'#a78bfa', cyan:'#22d3ee',
    pal:['#3b82f6','#8b5cf6','#06b6d4','#f59e0b','#10b981','#f43f5e',
        '#a78bfa','#34d399','#fb923c','#60a5fa','#c084fc','#4ade80',
        '#fbbf24','#38bdf8','#e879f9','#f472b6','#a3e635','#2dd4bf']
  }};
  const built = {{}};

  function mkChart(id, cfg) {{
    const el = document.getElementById(id);
    if (!el) return null;
    if (el._chart) el._chart.destroy();
    const c = new Chart(el, cfg);
    el._chart = c;
    return c;
  }}

  // ── Navigation ────────────────────────────────────────────────────────────────
  function showPage(name, btn) {{
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
    document.getElementById('page-' + name).classList.add('active');
    if (btn) btn.classList.add('active');
    if (!built[name]) {{ built[name] = true; buildPage(name); }}
  }}

  function buildPage(p) {{
    if (p==='overview') buildOverview();
    if (p==='daily')    buildDaily();
    if (p==='players')  buildPlayers();
    if (p==='products') buildProducts();
    if (p==='comments') buildComments();
  }}

  // ── Overview ──────────────────────────────────────────────────────────────────
  function buildOverview() {{
    const pos = SENTS.positive||0, neg = SENTS.negative||0, neu = SENTS.neutral||0;
    // Sentiment donut
    mkChart('sentDonut', {{ type:'doughnut', data:{{
      labels:['Positive','Neutral','Negative'],
      datasets:[{{ data:[pos,neu,neg], backgroundColor:[C.pos,C.neu,C.neg], borderWidth:2, borderColor:'#1e293b' }}]
    }}, options:{{ cutout:'62%', plugins:{{ legend:{{position:'bottom', labels:{{boxWidth:12}} }} }} }} }});

    // Competitor comparison donut
    const cpTotal = COMP_POS + COMP_NEG + COMP_NEU;
    if (cpTotal > 0) {{
      mkChart('compDonut', {{ type:'doughnut', data:{{
        labels:['Nike Better (pos)','Competitor Better (neg)','Neutral'],
        datasets:[{{ data:[COMP_POS,COMP_NEG,COMP_NEU], backgroundColor:[C.pos,C.neg,C.neu], borderWidth:2, borderColor:'#1e293b' }}]
      }}, options:{{ cutout:'62%', plugins:{{ legend:{{position:'bottom', labels:{{boxWidth:12}} }} }} }} }});
    }} else {{
      const el = document.getElementById('compDonut');
      if(el) el.parentElement.innerHTML += '<p class="no-data">No competitor comparison data yet</p>';
    }}

    // Daily overview bar
    const dailyData = MODE==='single'
      ? DAYS.map(d => DAY_CT[d]||0)
      : DAYS.map(d => DAY_VT[d]||0);
    const dailyLabel = MODE==='single' ? 'Comments' : 'Videos';
    mkChart('overviewDailyBar', {{ type:'bar', data:{{
      labels: DAYS,
      datasets:[{{ label:dailyLabel, data:dailyData, backgroundColor:C.blue, borderRadius:4 }}]
    }}, options:{{ plugins:{{legend:{{display:false}}}}, scales:{{y:{{beginAtZero:true}}}} }} }});

    // Top players
    const pNames  = PLAYERS.slice(0,10).map(x=>x[0]);
    const pCounts = PLAYERS.slice(0,10).map(x=>x[1]);
    mkChart('overviewPlayers', {{ type:'bar', data:{{
      labels: pNames,
      datasets:[{{ label:'Mentions', data:pCounts, backgroundColor:C.pal, borderRadius:4 }}]
    }}, options:{{ indexAxis:'y', plugins:{{legend:{{display:false}}}}, scales:{{x:{{beginAtZero:true}}}} }} }});
  }}

  // ── Daily ─────────────────────────────────────────────────────────────────────
  function buildDaily() {{
    const stackOpts = {{
      scales:{{ x:{{stacked:true}}, y:{{stacked:true, beginAtZero:true}} }},
      plugins:{{ legend:{{position:'bottom', labels:{{boxWidth:12}} }} }}
    }};

    if (MODE === 'multi') {{
      // Videos per day plain bar
      mkChart('videosPerDayBar', {{ type:'bar', data:{{
        labels: DAYS,
        datasets:[{{ label:'Videos Uploaded', data:DAYS.map(d=>DAY_VT[d]||0),
          backgroundColor:C.blue, borderRadius:3 }}]
      }}, options:{{ plugins:{{legend:{{display:false}}}}, scales:{{y:{{beginAtZero:true}}}} }} }});

      // Videos sentiment stacked
      mkChart('vidSentPerDay', {{ type:'bar', data:{{
        labels: DAYS,
        datasets:[
          {{ label:'Positive', data:DAYS.map(d=>DAY_VP[d]||0), backgroundColor:C.pos, borderRadius:2 }},
          {{ label:'Neutral',  data:DAYS.map(d=>DAY_VZ[d]||0), backgroundColor:C.neu, borderRadius:2 }},
          {{ label:'Negative', data:DAYS.map(d=>DAY_VN[d]||0), backgroundColor:C.neg, borderRadius:2 }},
        ]
      }}, options:stackOpts }});
    }}

    // Comments sentiment stacked
    mkChart('cmtSentPerDay', {{ type:'bar', data:{{
      labels: DAYS,
      datasets:[
        {{ label:'Positive', data:DAYS.map(d=>DAY_CP[d]||0), backgroundColor:C.pos, borderRadius:2 }},
        {{ label:'Neutral',  data:DAYS.map(d=>DAY_CZ[d]||0), backgroundColor:C.neu, borderRadius:2 }},
        {{ label:'Negative', data:DAYS.map(d=>DAY_CN[d]||0), backgroundColor:C.neg, borderRadius:2 }},
      ]
    }}, options:stackOpts }});

    // Ratio line
    mkChart('sentRatioLine', {{ type:'line', data:{{
      labels: DAYS,
      datasets:[
        {{ label:'% Positive', tension:.3, fill:true,
          borderColor:C.pos, backgroundColor:'rgba(34,197,94,.1)',
          data: DAYS.map(d=>{{ const t=(DAY_CP[d]||0)+(DAY_CN[d]||0)+(DAY_CZ[d]||0); return t?Math.round((DAY_CP[d]||0)*100/t):0; }}) }},
        {{ label:'% Negative', tension:.3, fill:true,
          borderColor:C.neg, backgroundColor:'rgba(239,68,68,.1)',
          data: DAYS.map(d=>{{ const t=(DAY_CP[d]||0)+(DAY_CN[d]||0)+(DAY_CZ[d]||0); return t?Math.round((DAY_CN[d]||0)*100/t):0; }}) }},
      ]
    }}, options:{{
      scales:{{ y:{{ min:0, max:100, ticks:{{callback:v=>v+'%'}} }} }},
      plugins:{{ legend:{{position:'bottom', labels:{{boxWidth:12}} }} }}
    }} }});
  }}

  // ── Players ───────────────────────────────────────────────────────────────────
  function buildPlayers() {{
    const names = PLAYERS.slice(0,15).map(x=>x[0]);
    mkChart('playerStackBar', {{ type:'bar', data:{{
      labels: names,
      datasets:[
        {{ label:'Positive', data:names.map(n=>PPOS[n]||0), backgroundColor:C.pos, borderRadius:2 }},
        {{ label:'Neutral',  data:names.map(n=>PNEU[n]||0), backgroundColor:C.neu, borderRadius:2 }},
        {{ label:'Negative', data:names.map(n=>PNEG[n]||0), backgroundColor:C.neg, borderRadius:2 }},
      ]
    }}, options:{{
      scales:{{ x:{{stacked:true}}, y:{{stacked:true, beginAtZero:true}} }},
      plugins:{{ legend:{{position:'bottom', labels:{{boxWidth:12}} }} }}
    }} }});
  }}

  // ── Products & Brands ─────────────────────────────────────────────────────────
  function buildProducts() {{
    function hbar(id, items, color) {{
      if (!items.length) {{ const el=document.getElementById(id); if(el) el.parentElement.innerHTML+='<p class=no-data>No data yet — run DeepSeek analysis first</p>'; return; }}
      mkChart(id, {{ type:'bar', data:{{
        labels: items.slice(0,12).map(x=>x[0]),
        datasets:[{{ label:'Mentions', data:items.slice(0,12).map(x=>x[1]), backgroundColor:color, borderRadius:4 }}]
      }}, options:{{ indexAxis:'y', plugins:{{legend:{{display:false}}}}, scales:{{x:{{beginAtZero:true}}}} }} }});
    }}
    function pie(id, items, pal) {{
      if (!items.length) {{ const el=document.getElementById(id); if(el) el.parentElement.innerHTML+='<p class=no-data>No data yet</p>'; return; }}
      mkChart(id, {{ type:'doughnut', data:{{
        labels: items.slice(0,10).map(x=>x[0]),
        datasets:[{{ data:items.slice(0,10).map(x=>x[1]), backgroundColor:pal, borderWidth:2, borderColor:'#1e293b' }}]
      }}, options:{{ cutout:'55%', plugins:{{ legend:{{position:'right', labels:{{boxWidth:10, font:{{size:11}} }} }} }} }} }});
    }}
    hbar('productsBar', PRODUCTS, C.blue);
    hbar('brandsBar',   BRANDS,   C.neg);
    pie('productsPie',  PRODUCTS, C.pal);
    pie('brandsPie',    BRANDS,   C.pal.slice(5));
  }}

  // ── Comments ──────────────────────────────────────────────────────────────────
  function buildComments() {{
    const smap = {{positive:'#22c55e22',negative:'#ef444422',neutral:'#f59e0b22'}};
    const scol = {{positive:'#22c55e',  negative:'#ef4444',  neutral:'#f59e0b'}};
    const semi = {{positive:'😊', negative:'😠', neutral:'😐'}};
    document.getElementById('topCmtList').innerHTML = TOP_LIKED.slice(0,50).map(c => `
      <div style="padding:12px 4px;border-bottom:1px solid #1e293b">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;flex-wrap:wrap">
          <span style="background:${{smap[c.sentiment]}};color:${{scol[c.sentiment]}};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">
            ${{semi[c.sentiment]||'💬'}} ${{c.sentiment||'—'}}
          </span>
          <span style="color:#64748b;font-size:11px">${{esc(c.author)}}</span>
          <span style="margin-left:auto;color:#f59e0b;font-size:11px">❤ ${{(c.likes||0).toLocaleString()}}</span>
          <span style="color:#475569;font-size:11px">📅 ${{c.date}}</span>
        </div>
        <div style="font-size:13px;color:#e2e8f0;line-height:1.5">"${{esc(c.text)}}"</div>
      </div>`).join('');
  }}

  // ── Video modal ───────────────────────────────────────────────────────────────
  function showVideoModal(idx) {{
    const v = VIDEO_DATA[idx];
    document.getElementById('modalTitle').textContent = v.title;
    const s = v.sentiments||{{}};
    const pos=s.positive||0, neg=s.negative||0, neu=s.neutral||0, tot=pos+neg+neu||1;
    const scol={{positive:'#22c55e',negative:'#ef4444',neutral:'#f59e0b'}};
    const smap={{positive:'pos',negative:'neg',neutral:'neu'}};

    document.getElementById('modalBody').innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:14px">
        <div style="background:#0f172a;border-radius:8px;padding:10px;text-align:center">
          <div style="font-size:18px;font-weight:700">${{(v.view_count||0).toLocaleString()}}</div>
          <div style="font-size:10px;color:#64748b">Views</div></div>
        <div style="background:#0f172a;border-radius:8px;padding:10px;text-align:center">
          <div style="font-size:18px;font-weight:700">${{(v.total_comments_fetched||0).toLocaleString()}}</div>
          <div style="font-size:10px;color:#64748b">Comments</div></div>
        <div style="background:#0f172a;border-radius:8px;padding:10px;text-align:center">
          <div style="font-size:18px;font-weight:700;color:${{scol[v.dom_sentiment]||'#fff'}}">${{v.dom_sentiment}}</div>
          <div style="font-size:10px;color:#64748b">Dominant</div></div>
      </div>
      <div style="margin-bottom:14px">
        <div style="font-size:11px;color:#64748b;margin-bottom:5px">SENTIMENT SPLIT</div>
        <div style="display:flex;height:10px;border-radius:5px;overflow:hidden">
          <div style="width:${{pos*100/tot}}%;background:#22c55e"></div>
          <div style="width:${{neu*100/tot}}%;background:#f59e0b"></div>
          <div style="width:${{neg*100/tot}}%;background:#ef4444"></div>
        </div>
        <div style="display:flex;gap:14px;margin-top:5px;font-size:11px">
          <span style="color:#22c55e">+ve: ${{pos}}</span>
          <span style="color:#f59e0b">neu: ${{neu}}</span>
          <span style="color:#ef4444">-ve: ${{neg}}</span>
        </div>
      </div>
      ${{v.players.length?`<div style="margin-bottom:12px"><div style="font-size:11px;color:#64748b;margin-bottom:5px">👤 PLAYERS</div>${{v.players.map(p=>`<span class="chip" style="background:#1e3a5f;color:#93c5fd">${{p[0]}} ×${{p[1]}}</span>`).join('')}}</div>`:''}}
      ${{v.products.length?`<div style="margin-bottom:12px"><div style="font-size:11px;color:#64748b;margin-bottom:5px">👟 NIKE PRODUCTS</div>${{v.products.map(p=>`<span class="chip" style="background:#064e3b;color:#34d399">${{p[0]}} ×${{p[1]}}</span>`).join('')}}</div>`:''}}
      ${{v.brands.length?`<div style="margin-bottom:12px"><div style="font-size:11px;color:#64748b;margin-bottom:5px">🏢 COMPETITOR BRANDS</div>${{v.brands.map(b=>`<span class="chip" style="background:#3b0764;color:#e879f9">${{b[0]}} ×${{b[1]}}</span>`).join('')}}</div>`:''}}
      ${{v.themes.length?`<div style="margin-bottom:12px"><div style="font-size:11px;color:#64748b;margin-bottom:5px">💡 THEMES</div>${{v.themes.map(t=>`<span class="chip" style="background:#0f2d2d;color:#2dd4bf">${{t[0]}} ×${{t[1]}}</span>`).join('')}}</div>`:''}}
      ${{(v.top_comments||[]).length?`<div><div style="font-size:11px;color:#64748b;margin-bottom:8px">💬 TOP COMMENTS</div>
        ${{v.top_comments.map(c=>`<div style="padding:9px;background:#0f172a;border-radius:8px;margin-bottom:7px">
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:4px;flex-wrap:wrap">
            <span style="color:${{scol[c.sentiment]||'#94a3b8'}};font-size:11px;font-weight:600">${{c.sentiment||'—'}}</span>
            <span style="color:#475569;font-size:11px">${{esc(c.author)}}</span>
            <span style="margin-left:auto;color:#f59e0b;font-size:11px">❤ ${{c.likes}}</span>
          </div>
          <div style="font-size:12px;color:#cbd5e1;line-height:1.5">${{esc(c.text)}}</div>
        </div>`).join('')}}</div>`:''}}
      <div style="margin-top:14px">
        <a href="https://youtube.com/watch?v=${{v.video_id}}" target="_blank"
          style="display:inline-block;padding:8px 18px;background:#1e3a5f;color:#60a5fa;border-radius:8px;font-size:12px;text-decoration:none">
          ▶ Watch on YouTube
        </a>
      </div>`;
    document.getElementById('modalBg').style.display = 'block';
  }}
  function closeModal() {{ document.getElementById('modalBg').style.display='none'; }}

  // ── Table search ──────────────────────────────────────────────────────────────
  function filterVids(q) {{
    q = q.toLowerCase();
    document.querySelectorAll('#vidTableBody tr').forEach(r => {{
      r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
    }});
  }}

  // ── Init ──────────────────────────────────────────────────────────────────────
  window.addEventListener('load', () => {{ built['overview']=true; buildOverview(); }});
  </script>
  </body>
  </html>"""


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Generate HTML dashboard from YouTube analytics JSON")
    ap.add_argument("input", help="JSON file (single or multi-video, analyzed or raw)")
    ap.add_argument("-o", "--output", default=None, help="Output HTML file")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        print(f"❌ File not found: {inp}"); sys.exit(1)

    out = Path(args.output) if args.output else inp.with_stem(inp.stem + "_dashboard").with_suffix(".html")

    print(f"📁 Loading  : {inp}")
    data = json.loads(inp.read_text(encoding="utf-8"))

    fmt = detect_format(data)
    print(f"📐 Format   : {fmt}")
    if fmt == "unknown":
        print("❌ Unrecognised JSON format."); sys.exit(1)

    if fmt == "single":
        mode, videos, flat = extract_single(data)
    else:
        mode, videos, flat = extract_multi(data)

    print(f"🎬 Videos   : {len(videos)}")
    print(f"💬 Comments : {len(flat):,} (incl. replies)")
    analyzed = sum(1 for c in flat if c["is_analyzed"])
    print(f"🤖 Analysed : {analyzed:,}")

    stats = build_stats(mode, videos, flat, data)
    query = data.get("query", data.get("video_metadata", {}).get("title", "Nike"))
    html  = generate_html(stats, query, inp.name)

    out.write_text(html, encoding="utf-8")
    print(f"\n✅ Dashboard : {out}")
    print(f"   Open in browser → file://{out.resolve()}")


if __name__ == "__main__":
    main()
