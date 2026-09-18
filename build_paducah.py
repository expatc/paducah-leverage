#!/usr/bin/env python3
"""
Paducah market leverage — automated monthly build.

PRIMARY SOURCE: Redfin Data Center city-level market tracker. Real MLS
closings, scoped to Paducah city limits, with months of supply already
computed. Redfin publishes these files openly and asks only for citation.

SECONDARY: Zillow Research metro CSVs, for the home-value index and as a
sanity check. Metro geography, so it covers more rural ground.

Neither requires a key, an account, or scraping. Both are the publishers'
own bulk exports.
"""

import csv
import gzip
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone

REDFIN_CITY = ("https://redfin-public-data.s3.us-west-2.amazonaws.com/"
               "redfin_market_tracker/city_market_tracker.tsv000.gz")
ZILLOW = "https://files.zillowstatic.com/research/public_csvs/"
CITY, STATE = "Paducah", "KY"
METRO = "Paducah, KY"
UA = {"User-Agent": "paducah-tracker/2.0"}

ZILLOW_SERIES = {
    "zhvi":       "zhvi/Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv",
    "metro_inv":  "invt_fs/Metro_invt_fs_uc_sfrcondo_sm_month.csv",
    "metro_pend": "new_pending/Metro_new_pending_uc_sfrcondo_sm_month.csv",
}

# Neutral baselines: where a signal favors neither side, and how far from it
# counts as a full +/-100. Edit these, not the scoring code.
BASELINES = {
    "supply": (5.0, 3.0),
    "stl":    (0.990, 0.020),
    "dom":    (45.0, 35.0),
    "drops":  (20.0, 15.0),
}


def fetch_redfin():
    """Stream the city file, keep only Paducah KY 'All Residential' rows."""
    print("Streaming Redfin city market tracker (large file, a few minutes)...",
          file=sys.stderr)
    req = urllib.request.Request(REDFIN_CITY, headers=UA)
    out = {}
    with urllib.request.urlopen(req, timeout=900) as resp:
        stream = io.TextIOWrapper(gzip.GzipFile(fileobj=resp),
                                  encoding="utf-8", errors="replace")
        reader = csv.reader(stream, delimiter="\t")
        header = [c.strip('"').lower() for c in next(reader)]
        idx = {c: i for i, c in enumerate(header)}
        for row in reader:
            if len(row) < len(header):
                continue
            if (row[idx["city"]].strip('"') == CITY
                    and row[idx["state_code"]].strip('"') == STATE
                    and row[idx["property_type"]].strip('"') == "All Residential"):
                def f(col):
                    v = row[idx[col]].strip('"')
                    if v in ("", "NA"):
                        return None
                    try:
                        return float(v)
                    except ValueError:
                        return None
                drops, above = f("price_drops"), f("sold_above_list")
                out[row[idx["period_begin"]].strip('"')[:7]] = {
                    "inventory":    f("inventory"),
                    "homes_sold":   f("homes_sold"),
                    "new_listings": f("new_listings"),
                    "supply":       f("months_of_supply"),
                    "dom":          f("median_dom"),
                    "stl":          f("avg_sale_to_list"),
                    "drops":        drops * 100 if drops is not None else None,
                    "above_list":   above * 100 if above is not None else None,
                    "median_price": f("median_sale_price"),
                }
    print(f"  Redfin: {len(out)} months for {CITY}, {STATE}", file=sys.stderr)
    return out


def fetch_zillow_row(path):
    req = urllib.request.Request(ZILLOW + path, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r:
        text = r.read().decode("utf-8", "replace")
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    col = header.index("RegionName")
    for row in rows[1:]:
        if row[col].strip() == METRO:
            out = {}
            for i, c in enumerate(header):
                if len(c) == 10 and c[4] == "-" and row[i].strip():
                    try:
                        out[c[:7]] = float(row[i])
                    except ValueError:
                        pass
            return out
    return {}


def clamp(v, lo=-100.0, hi=100.0):
    return max(lo, min(hi, v))


def score_month(m):
    """Each signal on -100 (sellers) .. +100 (buyers)."""
    parts = []
    if m.get("supply") is not None:
        b, s = BASELINES["supply"]
        parts.append(("Months of supply", f"{m['supply']:.1f} mo",
                      clamp((m["supply"] - b) / s * 100)))
    if m.get("stl") is not None:
        b, s = BASELINES["stl"]
        parts.append(("Sale-to-list ratio", f"{m['stl']:.3f}",
                      clamp((b - m["stl"]) / s * 100)))
    if m.get("dom") is not None:
        b, s = BASELINES["dom"]
        parts.append(("Days on market", f"{m['dom']:.0f} days",
                      clamp((m["dom"] - b) / s * 100)))
    if m.get("drops") is not None:
        b, s = BASELINES["drops"]
        parts.append(("Listings cutting price", f"{m['drops']:.1f}%",
                      clamp((m["drops"] - b) / s * 100)))
    comp = sum(p[2] for p in parts) / len(parts) if parts else None
    return parts, comp


def verdict(s):
    if s <= -50: return "Firmly a seller's market"
    if s <= -20: return "Leaning toward sellers"
    if s <   20: return "Roughly balanced"
    if s <   50: return "Leaning toward buyers"
    return "Firmly a buyer's market"


def build():
    redfin = fetch_redfin()
    if not redfin:
        raise SystemExit("No Paducah rows in the Redfin file — check CITY/STATE, "
                         "or whether Redfin changed the schema.")

    zillow = {}
    for key, path in ZILLOW_SERIES.items():
        try:
            zillow[key] = fetch_zillow_row(path)
            print(f"  Zillow {key}: {len(zillow[key])} months", file=sys.stderr)
        except Exception as e:
            print(f"  Zillow {key}: skipped ({e})", file=sys.stderr)
            zillow[key] = {}

    series = []
    for mo in sorted(redfin):
        rec = dict(redfin[mo])
        rec["month"] = mo
        rec["zhvi"] = zillow.get("zhvi", {}).get(mo)
        mi = zillow.get("metro_inv", {}).get(mo)
        mp = zillow.get("metro_pend", {}).get(mo)
        rec["metro_supply"] = (mi / mp) if (mi and mp) else None
        parts, comp = score_month(rec)
        rec["parts"] = [{"name": n, "value": v, "score": round(s, 1)}
                        for n, v, s in parts]
        rec["score"] = round(comp, 1) if comp is not None else None
        series.append(rec)

    full = [r for r in series if len(r["parts"]) == 4]
    if not full:
        raise SystemExit("No month has all four signals.")

    return {
        "city": f"{CITY}, {STATE}",
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "latest": full[-1],
        "series": full[-48:],
    }


# ---------------------------------------------------------------- render

HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paducah market leverage</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500&family=IBM+Plex+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{--ground:#F1F4F3;--surface:#fff;--ink:#16242F;--muted:#657572;--rule:#D4DBD9;
--seller:#A8761B;--buyer:#1C6B67;--even:#7C8A88;--shadow:0 1px 0 rgba(22,36,47,.06)}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--ground:#0E161A;--surface:#162126;
--ink:#E4EBE9;--muted:#93A3A0;--rule:#2A3A3F;--seller:#D9A247;--buyer:#4FB3AC;--even:#8C9C99;--shadow:none}}
:root[data-theme="dark"]{--ground:#0E161A;--surface:#162126;--ink:#E4EBE9;--muted:#93A3A0;
--rule:#2A3A3F;--seller:#D9A247;--buyer:#4FB3AC;--even:#8C9C99;--shadow:none}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-variant-numeric:tabular-nums;
font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,sans-serif;font-size:15px;line-height:1.5}
.wrap{max-width:620px;margin:0 auto;padding:28px 18px 60px}
h1,h2{font-family:"Newsreader",Georgia,serif;font-weight:400;margin:0}
h1{font-size:30px;line-height:1.15;letter-spacing:-.01em}
h2{font-size:21px;margin-bottom:10px}
.sub{color:var(--muted);font-size:13.5px;margin:6px 0 0}
.scale{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
padding:22px 16px 16px;margin:22px 0 14px;box-shadow:var(--shadow)}
svg.beam{display:block;width:100%;height:auto}
.verdict{font-family:"Newsreader",Georgia,serif;font-size:26px;text-align:center;line-height:1.2;margin-top:4px}
.verdict span{display:block;font-family:"IBM Plex Sans",sans-serif;font-size:15px;color:var(--muted);margin-top:7px}
.asof{text-align:center;color:var(--muted);font-size:13px;margin-top:2px}
section{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
padding:18px 16px;margin-bottom:14px;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-weight:500;color:var(--muted);font-size:12.5px;padding:0 0 7px;border-bottom:1px solid var(--rule)}
td{padding:9px 0;border-bottom:1px solid var(--rule);vertical-align:baseline}
td.n{text-align:right;white-space:nowrap}
tr:last-child td{border-bottom:none}
.bar{height:3px;border-radius:2px;margin-top:5px;background:var(--rule);position:relative}
.bar i{position:absolute;top:0;height:3px;border-radius:2px}
.lean{font-size:12.5px;padding-left:8px}
.s-sell{color:var(--seller)}.s-buy{color:var(--buyer)}.s-even{color:var(--even)}
.spark{display:block;width:100%;height:auto;margin-bottom:14px}
.note{font-size:13.5px;color:var(--muted);margin:14px 0 0}
footer{color:var(--muted);font-size:12.5px;line-height:1.55;margin-top:24px}
footer a{color:var(--muted)}
</style></head><body><div class="wrap">
<h1>Which way is Paducah tipping?</h1>
<p class="sub">Rebuilt every month from Redfin's own city-level exports.</p>
<div class="scale">
<svg class="beam" viewBox="0 0 320 128" role="img" aria-label="Market leverage beam">
<g transform="rotate(__ROT__ 160 46)">
<line x1="34" y1="46" x2="286" y2="46" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
<circle cx="34" cy="46" r="13" fill="var(--seller)"/><circle cx="286" cy="46" r="13" fill="var(--buyer)"/></g>
<path d="M160 46 L177 96 L143 96 Z" fill="currentColor" opacity=".9"/>
<line x1="120" y1="99" x2="200" y2="99" stroke="currentColor" stroke-width="2" opacity=".35" stroke-linecap="round"/>
<text x="34" y="122" text-anchor="middle" font-family="IBM Plex Sans" font-size="11.5" fill="var(--seller)">Sellers</text>
<text x="286" y="122" text-anchor="middle" font-family="IBM Plex Sans" font-size="11.5" fill="var(--buyer)">Buyers</text>
</svg>
<div class="verdict">__VERDICT__<span>__SCORELINE__</span></div>
<div class="asof">As of __ASOF__ &middot; __SOLD__ sold, __INV__ active</div></div>
<section><h2>What's driving it</h2><table><tbody>__PARTS__</tbody></table>
<p class="note">__SPREAD__</p></section>
<section><h2>Two years of readings</h2><svg class="spark" viewBox="0 0 320 70">__SPARK__</svg>
<table><thead><tr><th>Month</th><th class="n">Supply</th><th class="n">S/L</th><th class="n">DOM</th><th class="n">Score</th></tr></thead>
<tbody>__HIST__</tbody></table></section>
<footer>City figures from the <a href="https://www.redfin.com/news/data-center/">Redfin Data Center</a>, covering Paducah city limits and drawn from MLS closings. Home values from <a href="https://www.zillow.com/research/data/">Zillow Research</a>, which only reports transaction dynamics at metro level. Signals are scored against neutral baselines of 5.0 months of supply, 0.990 sale-to-list, 45 days on market, and 20% of listings cutting price &mdash; all four are constants at the top of the build script. Redfin's city file runs about two months behind. Built __BUILT__.</footer>
</div></body></html>"""


def cls(s):    return "s-sell" if s <= -20 else ("s-buy" if s >= 20 else "s-even")
def lean(s):   return "sellers" if s <= -20 else ("even" if s < 20 else "buyers")
def colour(s): return "var(--seller)" if s <= -20 else ("var(--buyer)" if s >= 20 else "var(--even)")

MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
FULLMON = ["January","February","March","April","May","June","July","August",
           "September","October","November","December"]


def render(d):
    latest = d["latest"]
    s = latest["score"]
    prev = d["series"][-2]["score"] if len(d["series"]) > 1 else None

    line = f"{s:+.0f} on a \u2212100 to +100 scale"
    if prev is not None:
        line += f" \u00b7 {s - prev:+.0f} since last month"

    parts = ""
    for p in latest["parts"]:
        w = abs(p["score"]) / 2
        left = 50 - w if p["score"] < 0 else 50
        parts += (f'<tr><td>{p["name"]}<div class="bar">'
                  f'<i style="left:{left:.1f}%;width:{w:.1f}%;background:{colour(p["score"])}"></i>'
                  f'</div></td><td class="n">{p["value"]}'
                  f'<span class="lean {cls(p["score"])}">{lean(p["score"])}</span></td></tr>')

    scores = [p["score"] for p in latest["parts"]]
    spread = max(scores) - min(scores)
    if spread > 90:
        note = (f"These four disagree sharply \u2014 {spread:.0f} points between the most "
                f"seller-friendly and the most buyer-friendly signal. Averaging signals that "
                f"pull opposite ways makes a weak summary, so read the rows, not the beam.")
    else:
        note = "The four signals point the same general direction this month."
    if latest.get("metro_supply"):
        note += (f" Zillow's wider metro reads {latest['metro_supply']:.1f} months of supply "
                 f"against the city's {latest['supply']:.1f}; rural McCracken County moves "
                 f"much slower than Paducah proper.")

    recent = d["series"][-24:]
    hist = ""
    for r in reversed(recent):
        y, m = r["month"].split("-")
        hist += (f'<tr><td>{MON[int(m)-1]} {y}</td><td class="n">{r["supply"]:.1f}</td>'
                 f'<td class="n">{r["stl"]:.3f}</td><td class="n">{r["dom"]:.0f}</td>'
                 f'<td class="n {cls(r["score"])}">{r["score"]:+.0f}</td></tr>')

    pts, path = [], ""
    for i, r in enumerate(recent):
        x = 6 + (308 * i / max(1, len(recent) - 1))
        y = 35 - (r["score"] / 100) * 29
        path += ("M" if i == 0 else " L") + f"{x:.1f} {y:.1f}"
        pts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="{colour(r["score"])}"/>')
    spark = ('<line x1="0" y1="35" x2="320" y2="35" stroke="var(--rule)" stroke-width="1"/>'
             f'<path d="{path}" fill="none" stroke="currentColor" stroke-width="1.6" '
             f'stroke-linejoin="round" opacity=".55"/>' + "".join(pts))

    y, m = latest["month"].split("-")
    return (HTML.replace("__ROT__", f"{s/10:.2f}")
                .replace("__VERDICT__", verdict(s))
                .replace("__SCORELINE__", line)
                .replace("__ASOF__", f"{FULLMON[int(m)-1]} {y}")
                .replace("__SOLD__", f"{latest['homes_sold']:.0f}" if latest.get("homes_sold") else "\u2014")
                .replace("__INV__", f"{latest['inventory']:.0f}" if latest.get("inventory") else "\u2014")
                .replace("__PARTS__", parts)
                .replace("__SPREAD__", note)
                .replace("__HIST__", hist)
                .replace("__SPARK__", spark)
                .replace("__BUILT__", d["built"]))


if __name__ == "__main__":
    data = build()
    with open("data.json", "w") as f:
        json.dump(data, f, indent=1)
    with open("index.html", "w") as f:
        f.write(render(data))
    L = data["latest"]
    print(f"\n{L['month']}  score {L['score']:+.0f}  {verdict(L['score'])}", file=sys.stderr)
    for p in L["parts"]:
        print(f"    {p['name']:<24} {p['value']:>10}  {p['score']:+6.1f}", file=sys.stderr)
