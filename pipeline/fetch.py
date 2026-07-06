#!/usr/bin/env python3
"""Ground Truth data pipeline.

Fetches every indicator in CATALOG from its primary source, applies
transforms, and writes compact JSON to data/ plus a freshness manifest.
Each chart fails independently; a failure never blocks the rest.

Run:  python pipeline/fetch.py            (full refresh)
      python pipeline/fetch.py --only cpi_yoy
      python pipeline/fetch.py --local-only   (manual series + catalog only)
"""
import csv, io, json, os, sys, time, traceback
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MANUAL = os.path.join(ROOT, "pipeline", "manual")
UA = {"User-Agent": "Mozilla/5.0 (GroundTruth dashboard; github.com/lkbell/sit-rep)"}
SIGNALS_ENABLED = True  # kill switch: set False and push to remove signals from the site entirely

def get(url, params=None, timeout=60):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r

# ---------------- source adapters ----------------

def src_fred(series_id):
    txt = get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": series_id}).text
    rows = list(csv.reader(io.StringIO(txt)))
    if not rows or len(rows[0]) < 2:
        raise ValueError("unexpected FRED csv shape")
    out = []
    for row in rows[1:]:
        if len(row) < 2 or row[1] in (".", "", "NA"):
            continue
        out.append((row[0], float(row[1])))
    if not out:
        raise ValueError("no observations")
    return out

def src_treasury_debt():
    base = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny"
    out, page = [], 1
    while True:
        j = get(base, {"fields": "record_date,tot_pub_debt_out_amt", "page[size]": 10000, "page[number]": page, "sort": "record_date"}).json()
        for d in j["data"]:
            out.append((d["record_date"], float(d["tot_pub_debt_out_amt"])))
        if not j["links"].get("next"):
            break
        page += 1
        if page > 5:
            break
    # keep last observation of each month
    bym = {}
    for dt, v in out:
        bym[dt[:7]] = (dt, v)
    return [bym[k] for k in sorted(bym)]

def src_cdc_overdose():
    j = get("https://data.cdc.gov/resource/xkb8-kh2a.json",
            {"$limit": 5000, "$where": "state='US' AND indicator='Number of Drug Overdose Deaths'"}).json()
    months = {m: i for i, m in enumerate(
        ["January","February","March","April","May","June","July","August","September","October","November","December"], 1)}
    out = []
    for d in j:
        val = d.get("predicted_value") or d.get("data_value")  # predicted corrects underreporting in recent months
        if not val:
            continue
        m = months.get(d["month"])
        if not m:
            continue
        out.append(("%s-%02d-01" % (d["year"], m), float(str(val).replace(",", ""))))
    return sorted(set(out))

def src_zillow(kind):
    urls = {
        "zhvi": "https://files.zillowstatic.com/research/public_csvs/zhvi/Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv",
        "zori": "https://files.zillowstatic.com/research/public_csvs/zori/Metro_zori_uc_sfrcondomfr_sm_month.csv",
    }
    txt = get(urls[kind], timeout=180).text
    rdr = csv.reader(io.StringIO(txt))
    header = next(rdr)
    di = [i for i, h in enumerate(header) if len(h) == 10 and h[4] == "-" and h[7] == "-"]
    for row in rdr:
        if "United States" in row[:6]:
            return [(header[i], float(row[i])) for i in di if row[i]]
    raise ValueError("US row not found")

def src_yahoo(symbol):
    j = get("https://query1.finance.yahoo.com/v8/finance/chart/" + symbol,
            {"interval": "1d", "range": "max"}).json()
    res = j["chart"]["result"][0]
    ts, closes = res["timestamp"], res["indicators"]["quote"][0]["close"]
    out = []
    for t, c in zip(ts, closes):
        if c is not None:
            out.append((datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"), round(float(c), 4)))
    return out

def src_epoch_scatter():
    txt = get("https://epoch.ai/data/notable_ai_models.csv", timeout=120).text
    rows = list(csv.DictReader(io.StringIO(txt)))
    dcol = ccol = mcol = None
    for k in rows[0]:
        lk = k.lower()
        if "publication date" in lk: dcol = k
        if "training compute" in lk and "flop" in lk and "cost" not in lk: ccol = k
        if lk in ("model", "system", "model name"): mcol = k
    if not dcol or not ccol:
        raise ValueError("epoch columns not found: %s" % list(rows[0])[:12])
    pts = []
    for r in rows:
        d, c = r.get(dcol, ""), r.get(ccol, "")
        if not d or not c:
            continue
        try:
            fl = float(c)
        except ValueError:
            continue
        if len(d) == 10 and d >= "2010-01-01" and fl > 0:
            pts.append((d, fl, (r.get(mcol) or "")[:40] if mcol else ""))
    pts.sort()
    return pts

def src_epoch_releases():
    pts = src_epoch_scatter()
    byq = {}
    for d, _fl, _m in pts:
        if d < "2015-01-01":
            continue
        q = d[:4] + "-%02d-01" % ((int(d[5:7]) - 1) // 3 * 3 + 1)
        byq[q] = byq.get(q, 0) + 1
    now = datetime.now(timezone.utc)
    curq = "%d-%02d-01" % (now.year, (now.month - 1) // 3 * 3 + 1)
    return [(k, byq[k]) for k in sorted(byq) if k != curq]  # drop only the genuinely incomplete current quarter

def src_rtci_snapshot():
    txt = get("https://raw.githubusercontent.com/AH-Datalytics/rtci/main/docs/app_data/full_table_data.csv", timeout=120).text
    rows = list(csv.DictReader(io.StringIO(txt)))
    nat = [r for r in rows if "nationwide" in (r.get("type", "") + r.get("agency_full", "")).lower()]
    if not nat:
        raise ValueError("no nationwide rows")
    dt = (nat[0].get("Date_Through") or "").strip()
    seen, bars = set(), {"labels": [], "pct": [], "through": nat[0].get("Month_Through", ""),
                         "date_through": dt if len(dt) == 10 else ""}
    order = ["Murders", "Rapes", "Robberies", "Aggravated Assaults", "Violent Crimes", "Burglaries", "Thefts", "Motor Vehicle Thefts", "Property Crimes"]
    nat.sort(key=lambda r: order.index(r["crime_type"]) if r["crime_type"] in order else 99)
    for r in nat:
        ct = r["crime_type"]
        if ct in seen:
            continue
        seen.add(ct)
        try:
            bars["labels"].append(ct)
            bars["pct"].append(round(float(r["Percent_Change"]), 1))
        except (ValueError, KeyError):
            continue
    return bars

def src_eia_crude():
    import xlrd  # .xls
    raw = get("https://www.eia.gov/dnav/pet/hist_xls/WCRFPUS2w.xls", timeout=120).content
    bk = xlrd.open_workbook(file_contents=raw)
    sh = bk.sheet_by_name("Data 1")
    out = []
    for i in range(sh.nrows):
        try:
            d = xlrd.xldate_as_datetime(sh.cell_value(i, 0), bk.datemode)
            v = float(sh.cell_value(i, 1))
            out.append((d.strftime("%Y-%m-%d"), v / 1000.0))  # kb/d -> mb/d
        except Exception:
            continue
    if not out:
        raise ValueError("no rows parsed")
    return out

def src_manual(name):
    with open(os.path.join(MANUAL, name + ".json")) as f:
        return json.load(f)

def src_stooq(symbol):
    txt = get("https://stooq.com/q/d/l/", {"s": symbol, "i": "d"}, timeout=120).text
    rows = list(csv.reader(io.StringIO(txt)))
    out = []
    for row in rows[1:]:
        if len(row) >= 5 and len(row[0]) == 10 and row[4]:
            out.append((row[0], round(float(row[4]), 4)))
    if not out:
        raise ValueError("no stooq rows")
    return out

def src_gpr():
    import xlrd
    raw = get("https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls", timeout=120).content
    bk = xlrd.open_workbook(file_contents=raw)
    sh = bk.sheet_by_index(0)
    hdr = [str(sh.cell_value(0, c)).strip().upper() for c in range(sh.ncols)]
    vcol = hdr.index("GPR")
    out = []
    for i in range(1, sh.nrows):
        try:
            d = xlrd.xldate_as_datetime(sh.cell_value(i, 0), bk.datemode)
            out.append((d.strftime("%Y-%m-%d"), round(float(sh.cell_value(i, vcol)), 2)))
        except Exception:
            continue
    if not out:
        raise ValueError("no GPR rows")
    return out

import re as _re

def _cell_date(cell, datemode):
    """Best-effort date from an xls cell: xldate float or common text formats.
    Returns 'YYYY-MM-DD' or None."""
    import xlrd
    if isinstance(cell, (int, float)) and 10000 < cell < 80000:
        try:
            return xlrd.xldate_as_datetime(cell, datemode).strftime("%Y-%m-%d")
        except Exception:
            return None
    if isinstance(cell, str):
        s = cell.strip()
        m = _re.match(r"^(\d{4})[-/\.](\d{1,2})([-/\.](\d{1,2}))?$", s)
        if m:
            return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(4) or 1))
        m = _re.match(r"^(\d{1,2})[-/\.](\d{4})$", s)
        if m:
            return "%04d-%02d-01" % (int(m.group(2)), int(m.group(1)))
        months = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
        m = _re.match(r"^(\d{1,2})[- ]([A-Za-z]{3,9})[- ](\d{4})$", s)
        if m:
            mm = months.get(m.group(2)[:3].lower())
            if mm:
                return "%04d-%02d-%02d" % (int(m.group(3)), mm, int(m.group(1)))
        m = _re.match(r"^([A-Za-z]{3,9})[- ](\d{2,4})$", s)
        if m:
            mm = months.get(m.group(1)[:3].lower())
            if mm:
                yy = int(m.group(2))
                if yy < 100:
                    yy += 2000 if yy < 50 else 1900
                return "%04d-%02d-01" % (yy, mm)
    return None

def src_gscpi():
    """NY Fed GSCPI: legacy .xls served under an .xlsx name, layout not guaranteed.
    Scans every sheet/row/column for a (date, number) pair; on failure raises with
    diagnostics (sheet names + first rows) so the manifest error explains the layout."""
    import xlrd
    raw = get("https://www.newyorkfed.org/medialibrary/research/interactives/gscpi/downloads/gscpi_data.xlsx", timeout=120).content
    bk = xlrd.open_workbook(file_contents=raw)
    out = []
    # Prefer sheets whose name mentions "data" (the file has a branded cover sheet first).
    order = sorted(range(bk.nsheets), key=lambda i: 0 if "data" in bk.sheet_by_index(i).name.lower() else 1)
    for si in order:
        sh = bk.sheet_by_index(si)
        for i in range(sh.nrows):
            row = sh.row_values(i)
            d = None
            di = -1
            for ci, cell in enumerate(row):
                d = _cell_date(cell, bk.datemode)
                if d:
                    di = ci
                    break
            if not d:
                continue
            for cell in row[di + 1:]:
                if isinstance(cell, (int, float)) and -50 < cell < 50:
                    out.append((d, round(float(cell), 3)))
                    break
        if out:
            break
    if not out:
        diag = "sheets=%s" % [bk.sheet_by_index(i).name for i in range(bk.nsheets)]
        try:
            shd = bk.sheet_by_index(order[0])
            diag += " datasheet_rows=%s" % [shd.row_values(i)[:8] for i in range(min(8, shd.nrows))]
        except Exception:
            pass
        raise ValueError("no GSCPI rows; " + diag)
    return sorted(out)

def src_owid(slug):
    txt = get("https://ourworldindata.org/grapher/" + slug + ".csv", timeout=120).text
    rows = list(csv.reader(io.StringIO(txt)))
    hdr = rows[0]
    ti = hdr.index("Day") if "Day" in hdr else hdr.index("Year")
    out = []
    for row in rows[1:]:
        if row and row[0] == "United States" and row[-1]:
            d = row[ti] if len(row[ti]) == 10 else row[ti] + "-12-31"
            out.append((d, float(row[-1])))
    if not out:
        raise ValueError("no US rows in OWID csv")
    return sorted(out)

SOURCES = {
    "fred": src_fred, "treasury_debt": src_treasury_debt, "cdc_overdose": src_cdc_overdose,
    "zillow": src_zillow, "yahoo": src_yahoo, "epoch_scatter": src_epoch_scatter,
    "epoch_releases": src_epoch_releases, "rtci_snapshot": src_rtci_snapshot,
    "eia_crude": src_eia_crude, "manual": src_manual, "owid": src_owid,
    "stooq": src_stooq, "gpr": src_gpr, "gscpi": src_gscpi,
}

# ---------------- transforms ----------------

def _mk(d):
    return (int(d[:4]), int(d[5:7]))

def t_yoy(pts, periods):
    """Calendar-aware YoY: compare to the observation in the same month one
    year earlier (works for monthly and quarterly grids). Skips the point if
    the base month is missing (e.g., the Oct-2025 CPI shutdown gap) rather
    than silently comparing across 13 months."""
    idx = {_mk(d): v for d, v in pts}
    out = []
    for d, v in pts:
        y, m = _mk(d)
        prev = idx.get((y - 1, m))
        if prev:
            out.append((d, round((v / prev - 1) * 100, 2)))
    return out

def t_diff(pts):
    return [(pts[i][0], round(pts[i][1] - pts[i - 1][1], 2)) for i in range(1, len(pts))]

def t_ma(pts, n):
    return [(pts[i][0], round(sum(v for _, v in pts[i - n + 1:i + 1]) / n, 2)) for i in range(n - 1, len(pts))]

def t_rollsum(pts, n):
    """Calendar-aware trailing n-month sum; emits a point only when all n
    calendar months are present."""
    idx = {_mk(d): v for d, v in pts}
    out = []
    for d, v in pts:
        y, m = _mk(d)
        total, ok = 0.0, True
        for k in range(n):
            mm, yy = m - k, y
            while mm < 1:
                mm += 12
                yy -= 1
            vv = idx.get((yy, mm))
            if vv is None:
                ok = False
                break
            total += vv
        if ok:
            out.append((d, round(total, 2)))
    return out

def t_scale(pts, k):
    return [(d, round(v * k, 4)) for d, v in pts]

def apply_transforms(pts, tfs):
    for tf in tfs or []:
        name, arg = (tf if isinstance(tf, list) else [tf, None])[:2]
        if name == "yoy_m": pts = t_yoy(pts, 12)
        elif name == "yoy_q": pts = t_yoy(pts, 4)
        elif name == "diff": pts = t_diff(pts)
        elif name == "ma": pts = t_ma(pts, arg)
        elif name == "rollsum": pts = t_rollsum(pts, arg)
        elif name == "scale": pts = t_scale(pts, arg)
        elif name == "neg": pts = t_scale(pts, -1)
        elif name == "clip": pts = [p for p in pts if p[0] >= arg]
    return pts

def thin(pts):
    """Daily data: keep daily for ~2y, weekly to ~10y, monthly beyond."""
    if len(pts) < 1200:
        return pts
    last = datetime.strptime(pts[-1][0], "%Y-%m-%d")
    out, seen = [], set()
    for d, v in pts:
        dt = datetime.strptime(d, "%Y-%m-%d")
        age = (last - dt).days
        if age <= 750:
            out.append((d, v))
        elif age <= 3700:
            k = "w" + dt.strftime("%G%V")
            if k not in seen:
                seen.add(k); out.append((d, v))
        else:
            k = "m" + d[:7]
            if k not in seen:
                seen.add(k); out.append((d, v))
    return out

def _sig_series(cid):
    try:
        with open(os.path.join(DATA, cid + ".json")) as f:
            s = json.load(f)["series"][0]
        return s["t"], s["v"]
    except Exception:
        return [], []

def _sig_since(t, v, cond):
    since = None
    for i in range(len(v) - 1, -1, -1):
        if v[i] is not None and cond(v[i]):
            since = t[i]
        else:
            break
    return since

def compute_signals():
    """Seven calibrated early-warning rules over the just-written data files.
    Thresholds chosen for low false-alarm rates; see MAINTENANCE.md before tuning."""
    sigs = []
    def add(sid, label, tripped, detail, since=None):
        sigs.append({"id": sid, "label": label, "tripped": bool(tripped), "detail": detail, "since": since})
    t, v = _sig_series("sahm")
    if v:
        add("sahm", "Sahm rule ≥ 0.50", v[-1] >= 0.5,
            "latest %.2fpp (trigger 0.50); has marked every recession start since 1970" % v[-1],
            _sig_since(t, v, lambda x: x >= 0.5))
    t, v = _sig_series("t10y2y")
    if v:
        cross = None
        last = datetime.strptime(t[-1], "%Y-%m-%d")
        for i in range(len(v) - 1, 0, -1):
            if (last - datetime.strptime(t[i], "%Y-%m-%d")).days > 60:
                break
            if v[i] is not None and v[i - 1] is not None and (v[i] > 0) != (v[i - 1] > 0):
                cross = t[i]
                break
        add("curve", "Yield curve crossed zero (past 60 days)", cross is not None,
            "latest %+.2fpp%s" % (v[-1], ("; crossed " + cross) if cross else ""), cross)
    t, v = _sig_series("hy_oas")
    if v:
        add("hy", "High-yield spread above 5pp", v[-1] > 5.0,
            "latest %.2fpp (calm <3, stress >5, crisis >8)" % v[-1], _sig_since(t, v, lambda x: x > 5.0))
    t, v = _sig_series("icsa")
    if v:
        lo = min([x for x in v[-26:] if x is not None] or [0])
        add("claims", "Jobless claims 15%+ off 26-week low", lo and v[-1] >= lo * 1.15,
            "4-wk avg %dK vs 26-wk low %dK (%+.0f%%)" % (v[-1], lo, (v[-1] / lo - 1) * 100 if lo else 0), None)
    t, v = _sig_series("nfci")
    if v:
        add("nfci", "Financial conditions tighter than average", v[-1] > 0,
            "NFCI %+.2f (0 = long-run average)" % v[-1], _sig_since(t, v, lambda x: x > 0))
    t, v = _sig_series("cpi_yoy")
    if v:
        add("cpi", "Inflation breakout (≥4% or ≤0%)", v[-1] >= 4.0 or v[-1] <= 0.0,
            "headline %.1f%% y/y" % v[-1], None)
    t, v = _sig_series("gpr")
    if v:
        base = [x for x in v[-60:] if x is not None]
        avg = sum(base) / len(base) if base else 0
        add("gpr", "Geopolitical risk 2x its 5-year average", bool(avg) and v[-1] >= 2 * avg,
            "GPR %.0f vs 5-yr avg %.0f" % (v[-1], avg), None)
    return {"enabled": True,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "tripped": sum(1 for s in sigs if s["tripped"]),
            "signals": sigs}

# ---------------- catalog ----------------
# chart: title, unit(label), fmt(pct|usd|num|sci), dec, freq(d/w/m/q/a/manual/snap),
#        kind(line|scatter|bars), log, rng(default years or 'max'), exp(days override),
#        series: [{name, src:[adapter, arg], tf:[...]}], source_name, source_url, note

SECTIONS = [
    ("markets", "Markets & finance", "Equities, rates, credit, the dollar, and the Fed."),
    ("economy", "Economy", "Inflation, jobs, growth, and the consumer."),
    ("fiscal", "Debt & deficit", "What Washington owes, borrows, and pays in interest."),
    ("housing", "Housing", "Prices, rents, rates, supply, and stress."),
    ("energy", "Energy", "Oil, gas, electricity, and U.S. production."),
    ("immigration", "Immigration & border", "Border enforcement volumes over time."),
    ("crime", "Crime", "Long-run murder rate and a real-time city sample."),
    ("society", "Society & politics", "Fertility, life expectancy, overdoses, marriage, trust, religion."),
    ("tech", "Tech & AI", "AI progress, chips, and the buildout."),
    ("world", "World", "China, geopolitics, and the external picture."),
]

# Charts surfaced on the Overview landing page (order matters).
# Composition: markets pulse -> the three best early-warning series (curve, credit,
# claims + Sahm) -> inflation/jobs -> fiscal -> housing -> the slow structural trends.
OVERVIEW = ["sp500", "dgs10", "t10y2y", "hy_oas", "icsa", "sahm", "cpi_yoy", "unrate",
            "deficit12", "debt", "cs_yoy", "swb_fy", "murder_rate", "fertility",
            "trust_media", "ai_compute"]

F = "https://fred.stlouisfed.org/series/"

CATALOG = {
    # ---- markets ----
    "sp500": dict(sec="markets", title="S&P 500", unit="index", fmt="num", dec=0, freq="d", rng=10,
                  series=[dict(name="S&P 500", src=["yahoo", "^GSPC"])],
                  fallback=[dict(name="S&P 500", src=["fred", "SP500"])],
                  source_name="Yahoo Finance (fallback: FRED)", source_url="https://finance.yahoo.com/quote/%5EGSPC"),
    "nasdaq": dict(sec="markets", title="Nasdaq Composite", unit="index", fmt="num", dec=0, freq="d", rng=10,
                   series=[dict(name="Nasdaq", src=["fred", "NASDAQCOM"])],
                   source_name="NASDAQ OMX via FRED", source_url=F + "NASDAQCOM"),
    "dgs10": dict(sec="markets", title="10-year Treasury yield", unit="%", fmt="pct", dec=2, freq="d", rng=25,
                  series=[dict(name="10Y", src=["fred", "DGS10"])],
                  source_name="U.S. Treasury via FRED", source_url=F + "DGS10"),
    "t10y2y": dict(sec="markets", title="Yield curve (10Y minus 2Y)", unit="pp", fmt="pct", dec=2, freq="d", rng=25,
                   series=[dict(name="10Y−2Y", src=["fred", "T10Y2Y"])], zero_line=True,
                   source_name="FRED", source_url=F + "T10Y2Y",
                   note="Inversions (below zero) have preceded every modern recession."),
    "hy_oas": dict(sec="markets", title="High-yield credit spread (OAS)", unit="pp", fmt="pct", dec=2, freq="d", rng=15,
                   series=[dict(name="HY OAS", src=["fred", "BAMLH0A0HYM2"])],
                   source_name="ICE BofA via FRED", source_url=F + "BAMLH0A0HYM2",
                   note="Stress gauge: widening spreads = markets pricing default risk."),
    "vix": dict(sec="markets", title="VIX (implied volatility)", unit="index", fmt="num", dec=1, freq="d", rng=10,
                series=[dict(name="VIX", src=["fred", "VIXCLS"])],
                source_name="Cboe via FRED", source_url=F + "VIXCLS"),
    "dollar": dict(sec="markets", title="U.S. dollar index (broad, trade-weighted)", unit="index", fmt="num", dec=1, freq="d", rng=15,
                   series=[dict(name="Broad dollar", src=["fred", "DTWEXBGS"])],
                   source_name="Federal Reserve via FRED", source_url=F + "DTWEXBGS"),
    "gold": dict(sec="markets", title="Gold", unit="$/oz", fmt="usd", dec=0, freq="d", rng=15,
                 series=[dict(name="Gold", src=["yahoo", "GC=F"])],
                 fallback=[dict(name="Gold", src=["stooq", "xauusd"])],
                 source_name="COMEX via Yahoo Finance (fallback: Stooq)", source_url="https://finance.yahoo.com/quote/GC=F"),
    "btc": dict(sec="markets", title="Bitcoin", unit="$", fmt="usd", dec=0, freq="d", rng="max", log=True,
                series=[dict(name="BTC", src=["fred", "CBBTCUSD"])],
                source_name="Coinbase via FRED", source_url=F + "CBBTCUSD"),
    "fedfunds": dict(sec="markets", title="Federal funds rate", unit="%", fmt="pct", dec=2, freq="m", rng="max",
                     series=[dict(name="Fed funds", src=["fred", "FEDFUNDS"])],
                     source_name="Federal Reserve via FRED", source_url=F + "FEDFUNDS"),
    "walcl": dict(sec="markets", title="Federal Reserve balance sheet", unit="$T", fmt="usd", dec=2, freq="w", rng="max",
                  series=[dict(name="Total assets", src=["fred", "WALCL"], tf=[["scale", 1e-6]])],
                  source_name="Federal Reserve via FRED", source_url=F + "WALCL"),
    "m2_yoy": dict(sec="markets", title="M2 money supply, YoY", unit="%", fmt="pct", dec=1, freq="m", rng=25, zero_line=True,
                   series=[dict(name="M2 YoY", src=["fred", "M2SL"], tf=["yoy_m"])],
                   source_name="Federal Reserve via FRED", source_url=F + "M2SL"),
    # ---- economy ----
    "cpi_yoy": dict(sec="economy", title="Inflation: CPI, YoY", unit="%", fmt="pct", dec=1, freq="m", rng=25, zero_line=True,
                    series=[dict(name="Headline", src=["fred", "CPIAUCSL"], tf=["yoy_m"]),
                            dict(name="Core (ex food & energy)", src=["fred", "CPILFESL"], tf=["yoy_m"])],
                    source_name="BLS via FRED", source_url=F + "CPIAUCSL"),
    "unrate": dict(sec="economy", title="Unemployment rate", unit="%", fmt="pct", dec=1, freq="m", rng=25,
                   series=[dict(name="U-3", src=["fred", "UNRATE"])],
                   source_name="BLS via FRED", source_url=F + "UNRATE"),
    "payems_chg": dict(sec="economy", title="Payrolls: monthly job growth", unit="thousands", fmt="num", dec=0, freq="m", rng=5, zero_line=True, no_delta=True,
                       series=[dict(name="Nonfarm payrolls, m/m", src=["fred", "PAYEMS"], tf=["diff"])],
                       source_name="BLS via FRED", source_url=F + "PAYEMS"),
    "lfpr_prime": dict(sec="economy", title="Prime-age (25–54) labor force participation", unit="%", fmt="pct", dec=1, freq="m", rng=25,
                       series=[dict(name="LFPR 25–54", src=["fred", "LNS11300060"])],
                       source_name="BLS via FRED", source_url=F + "LNS11300060"),
    "jtsjol": dict(sec="economy", title="Job openings", unit="millions", fmt="num", dec=1, freq="m", rng=15,
                   series=[dict(name="Openings", src=["fred", "JTSJOL"], tf=[["scale", 1e-3]])],
                   source_name="BLS JOLTS via FRED", source_url=F + "JTSJOL"),
    "icsa": dict(sec="economy", title="Initial jobless claims (4-week avg)", unit="thousands", fmt="num", dec=0, freq="w", rng=5,
                 series=[dict(name="Claims", src=["fred", "ICSA"], tf=[["ma", 4], ["scale", 1e-3]])],
                 source_name="DOL via FRED", source_url=F + "ICSA"),
    "gdp_growth": dict(sec="economy", title="Real GDP growth (annualized, q/q)", unit="%", fmt="pct", dec=1, freq="q", rng=15, zero_line=True, exp=270,
                       series=[dict(name="Real GDP", src=["fred", "A191RL1Q225SBEA"])],
                       source_name="BEA via FRED", source_url=F + "A191RL1Q225SBEA"),
    "wage_vs_cpi": dict(sec="economy", title="Wages vs. inflation, YoY", unit="%", fmt="pct", dec=1, freq="m", rng=10,
                        series=[dict(name="Avg hourly earnings", src=["fred", "CES0500000003"], tf=["yoy_m"]),
                                dict(name="CPI", src=["fred", "CPIAUCSL"], tf=["yoy_m", ["clip", "2007-01-01"]])],
                        source_name="BLS via FRED", source_url=F + "CES0500000003",
                        note="Wages above CPI = real wages rising."),
    "umcsent": dict(sec="economy", title="Consumer sentiment (U. Michigan)", unit="index", fmt="num", dec=1, freq="m", rng=25,
                    series=[dict(name="Sentiment", src=["fred", "UMCSENT"])],
                    source_name="U. Michigan via FRED", source_url=F + "UMCSENT"),
    "retail_yoy": dict(sec="economy", title="Real retail sales, YoY", unit="%", fmt="pct", dec=1, freq="m", rng=10, zero_line=True,
                       series=[dict(name="Real retail sales", src=["fred", "RRSFS"], tf=["yoy_m"])],
                       source_name="Census/BLS via FRED", source_url=F + "RRSFS",
                       note="Inflation-adjusted — nominal retail sales can grow while real volumes shrink."),
    "indpro_yoy": dict(sec="economy", title="Industrial production, YoY", unit="%", fmt="pct", dec=1, freq="m", rng=25, zero_line=True,
                       series=[dict(name="Industrial production", src=["fred", "INDPRO"], tf=["yoy_m"])],
                       source_name="Federal Reserve via FRED", source_url=F + "INDPRO"),
    "t5yie": dict(sec="economy", title="Market inflation expectations (5-year breakeven)", unit="%", fmt="pct", dec=2, freq="d", rng=15,
                  series=[dict(name="5Y breakeven", src=["fred", "T5YIE"])],
                  source_name="FRED", source_url=F + "T5YIE"),
    # ---- fiscal ----
    "debt": dict(sec="fiscal", title="Federal debt outstanding", unit="$T", fmt="usd", dec=2, freq="m", rng="max",
                 series=[dict(name="Total public debt", src=["treasury_debt"], tf=[["scale", 1e-12]])],
                 source_name="U.S. Treasury (Debt to the Penny)", source_url="https://fiscaldata.treasury.gov/datasets/debt-to-the-penny/"),
    "debt_gdp": dict(sec="fiscal", title="Federal debt as % of GDP", unit="%", fmt="pct", dec=0, freq="q", rng="max", exp=290,
                     series=[dict(name="Debt/GDP", src=["fred", "GFDEGDQ188S"])],
                     source_name="OMB/FRED", source_url=F + "GFDEGDQ188S"),
    "deficit12": dict(sec="fiscal", title="Federal deficit, trailing 12 months", unit="$B", fmt="usd", dec=0, freq="m", rng=25,
                      series=[dict(name="12-mo deficit", src=["fred", "MTSDS133FMS"], tf=[["rollsum", 12], "neg", ["scale", 1e-3]])],
                      source_name="U.S. Treasury via FRED", source_url=F + "MTSDS133FMS",
                      note="Positive = deficit. Monthly Treasury Statement, rolling 12-month sum."),
    "interest": dict(sec="fiscal", title="Federal interest payments (annualized)", unit="$B", fmt="usd", dec=0, freq="q", rng="max", exp=270,
                     series=[dict(name="Interest outlays", src=["fred", "A091RC1Q027SBEA"])],
                     source_name="BEA via FRED", source_url=F + "A091RC1Q027SBEA"),
    # ---- housing ----
    "cs_level": dict(sec="housing", title="Home prices: Case-Shiller national index", unit="index", fmt="num", dec=1, freq="m", rng="max", exp=135,
                     series=[dict(name="Case-Shiller US", src=["fred", "CSUSHPINSA"])],
                     source_name="S&P CoreLogic via FRED", source_url=F + "CSUSHPINSA"),
    "cs_yoy": dict(sec="housing", title="Home prices, YoY", unit="%", fmt="pct", dec=1, freq="m", rng=25, zero_line=True, exp=135,
                   series=[dict(name="Case-Shiller YoY", src=["fred", "CSUSHPINSA"], tf=["yoy_m"])],
                   source_name="S&P CoreLogic via FRED", source_url=F + "CSUSHPINSA"),
    "mspus": dict(sec="housing", title="Median home sale price", unit="$K", fmt="usd", dec=0, freq="q", rng="max", exp=270,
                  series=[dict(name="Median price", src=["fred", "MSPUS"], tf=[["scale", 1e-3]])],
                  source_name="Census/HUD via FRED", source_url=F + "MSPUS"),
    "mortgage30": dict(sec="housing", title="30-year mortgage rate", unit="%", fmt="pct", dec=2, freq="w", rng="max",
                       series=[dict(name="30Y fixed", src=["fred", "MORTGAGE30US"])],
                       source_name="Freddie Mac via FRED", source_url=F + "MORTGAGE30US"),
    "houst": dict(sec="housing", title="Housing starts", unit="M units (SAAR)", fmt="num", dec=2, freq="m", rng=25,
                  series=[dict(name="Starts", src=["fred", "HOUST"], tf=[["scale", 1e-3]])],
                  source_name="Census via FRED", source_url=F + "HOUST"),
    "permit": dict(sec="housing", title="Building permits", unit="M units (SAAR)", fmt="num", dec=2, freq="m", rng=25,
                   series=[dict(name="Permits", src=["fred", "PERMIT"], tf=[["scale", 1e-3]])],
                   source_name="Census via FRED", source_url=F + "PERMIT"),
    "msacsr": dict(sec="housing", title="Months' supply of new homes", unit="months", fmt="num", dec=1, freq="m", rng=25,
                   series=[dict(name="Supply", src=["fred", "MSACSR"])],
                   source_name="Census via FRED", source_url=F + "MSACSR"),
    "zhvi": dict(sec="housing", title="Zillow home value index (U.S.)", unit="$K", fmt="usd", dec=0, freq="m", rng="max",
                 series=[dict(name="ZHVI", src=["zillow", "zhvi"], tf=[["scale", 1e-3]])],
                 source_name="Zillow Research", source_url="https://www.zillow.com/research/data/"),
    "zori": dict(sec="housing", title="Zillow observed rent index (U.S.)", unit="$/mo", fmt="usd", dec=0, freq="m", rng="max",
                 series=[dict(name="ZORI", src=["zillow", "zori"])],
                 source_name="Zillow Research", source_url="https://www.zillow.com/research/data/"),
    "delinq": dict(sec="housing", title="Mortgage delinquency rate", unit="%", fmt="pct", dec=2, freq="q", rng="max", exp=290,
                   series=[dict(name="Single-family delinquency", src=["fred", "DRSFRMACBS"])],
                   source_name="Federal Reserve via FRED", source_url=F + "DRSFRMACBS"),
    "inventory": dict(sec="housing", title="Homes for sale: active listings", unit="count", fmt="num", dec=0, freq="m", rng="max",
                      series=[dict(name="Active listings", src=["fred", "ACTLISCOUUS"])],
                      source_name="Realtor.com via FRED", source_url=F + "ACTLISCOUUS",
                      note="Inventory turns before prices do. Series begins 2016."),
    # ---- energy ----
    "wti": dict(sec="energy", title="Crude oil (WTI)", unit="$/bbl", fmt="usd", dec=0, freq="d", rng=25,
                series=[dict(name="WTI", src=["fred", "DCOILWTICO"])],
                source_name="EIA via FRED", source_url=F + "DCOILWTICO"),
    "gas": dict(sec="energy", title="Retail gasoline (regular, U.S. avg)", unit="$/gal", fmt="usd", dec=2, freq="w", rng=25,
                series=[dict(name="Regular", src=["fred", "GASREGW"])],
                source_name="EIA via FRED", source_url=F + "GASREGW"),
    "natgas": dict(sec="energy", title="Natural gas (Henry Hub)", unit="$/MMBtu", fmt="usd", dec=2, freq="d", rng=25,
                   series=[dict(name="Henry Hub", src=["fred", "DHHNGSP"])],
                   source_name="EIA via FRED", source_url=F + "DHHNGSP"),
    "elec": dict(sec="energy", title="Residential electricity price (U.S. city avg)", unit="$/kWh", fmt="usd", dec=3, freq="m", rng=25,
                 series=[dict(name="Electricity", src=["fred", "APU000072610"])],
                 source_name="BLS via FRED", source_url=F + "APU000072610"),
    "crude_prod": dict(sec="energy", title="U.S. crude oil production", unit="M bbl/day", fmt="num", dec=1, freq="w", rng="max",
                       series=[dict(name="Field production", src=["eia_crude"])],
                       source_name="EIA weekly supply", source_url="https://www.eia.gov/dnav/pet/pet_sum_sndw_dcus_nus_w.htm"),
    # ---- immigration ----
    "swb_fy": dict(sec="immigration", title="Southwest border apprehensions by fiscal year (USBP)", unit="count", fmt="num", dec=0, freq="manual", rng="max",
                   series=[dict(name="SW apprehensions", src=["manual", "swb_fy"])],
                   source_name="CBP", source_url="https://www.cbp.gov/newsroom/stats/southwest-land-border-encounters", exp=550,
                   note="Curated from CBP published fiscal-year data; updated each fall when CBP closes the fiscal year."),
    # ---- crime ----
    "murder_rate": dict(sec="crime", title="Murder rate (per 100,000)", unit="per 100k", fmt="num", dec=1, freq="manual", rng="max",
                        series=[dict(name="Murder rate", src=["manual", "murder_rate"])],
                        source_name="FBI UCR/CDE", source_url="https://cde.ucr.cjis.gov/", exp=700,
                        note="Curated from FBI annual estimates; updated when the FBI publishes each fall."),
    "rtci_ytd": dict(sec="crime", title="Crime YTD vs. last year (real-time city sample)", unit="% change", fmt="pct", dec=1, freq="snap", kind="bars", exp=130,
                     series=[dict(name="RTCI", src=["rtci_snapshot"])],
                     source_name="Real-Time Crime Index (AH Datalytics)", source_url="https://realtimecrimeindex.com/",
                     note="Sample of 300+ agencies reporting monthly; provisional, not official FBI totals."),
    # ---- society & politics ----
    "overdoses": dict(sec="society", title="Drug overdose deaths (trailing 12 months)", unit="deaths", fmt="num", dec=0, freq="m", rng="max", exp=300,
                      series=[dict(name="12-mo overdose deaths", src=["cdc_overdose"])],
                      source_name="CDC VSRR (provisional)", source_url="https://www.cdc.gov/nchs/nvss/vsrr/drug-overdose-data.htm",
                      note="CDC publishes with about a 6-month lag; recent months use CDC's predicted (completeness-adjusted) counts."),
    "fertility": dict(sec="society", title="Total fertility rate", unit="births per woman", fmt="num", dec=2, freq="manual", rng="max",
                      series=[dict(name="TFR", src=["manual", "fertility"])], zero_line=False, exp=650,
                      source_name="CDC NCHS", source_url="https://www.cdc.gov/nchs/nvss/births.htm",
                      note="Replacement level ≈ 2.1. Curated annual data (CDC final)."),
    "life_exp": dict(sec="society", title="Life expectancy at birth", unit="years", fmt="num", dec=1, freq="manual", rng="max",
                     series=[dict(name="Life expectancy", src=["manual", "life_exp"])], exp=650,
                     source_name="CDC NCHS", source_url="https://www.cdc.gov/nchs/fastats/life-expectancy.htm",
                     note="Curated annual data."),
    "marriage": dict(sec="society", title="Marriage rate (per 1,000 population)", unit="per 1,000", fmt="num", dec=1, freq="manual", rng="max", exp=1400,
                     series=[dict(name="Marriage rate", src=["manual", "marriage"])],
                     source_name="CDC NCHS", source_url="https://www.cdc.gov/nchs/fastats/marriage-divorce.htm",
                     note="Curated annual data (provisional for recent years)."),
    "trust_media": dict(sec="society", title="Trust in mass media (Gallup)", unit="% great deal / fair amount", fmt="pct", dec=0, freq="manual", rng="max", exp=550,
                        series=[dict(name="Trust", src=["manual", "trust_media"])],
                        source_name="Gallup", source_url="https://news.gallup.com/poll/1663/media-use-evaluation.aspx",
                        note="Curated from Gallup's annual survey (published each fall)."),
    "church": dict(sec="society", title="Church/synagogue/mosque membership (Gallup)", unit="%", fmt="pct", dec=0, freq="manual", rng="max",
                   series=[dict(name="Membership", src=["manual", "church"])],
                   source_name="Gallup", source_url="https://news.gallup.com/poll/341963/church-membership-falls-below-majority-first-time.aspx",
                   note="Gallup series; last reading 2020. A living replacement series is on the backlog."),
    # ---- tech & ai ----
    "ai_compute": dict(sec="tech", title="AI training compute of notable models", unit="FLOP", fmt="sci", dec=0, freq="w", rng="max", kind="scatter", log=True, exp=60, no_delta=True,
                       series=[dict(name="Training FLOP", src=["epoch_scatter"])],
                       source_name="Epoch AI", source_url="https://epoch.ai/data/ai-models",
                       note="Each point is a notable model (log scale)."),
    "ai_releases": dict(sec="tech", title="Notable AI model releases per quarter", unit="models", fmt="num", dec=0, freq="q", rng="max", exp=200,
                        series=[dict(name="Releases", src=["epoch_releases"])],
                        source_name="Epoch AI", source_url="https://epoch.ai/data/ai-models"),
    "sox": dict(sec="tech", title="Semiconductor index (SOX)", unit="index", fmt="num", dec=0, freq="d", rng=10,
                series=[dict(name="PHLX Semiconductor", src=["yahoo", "^SOX"])],
                fallback=[dict(name="PHLX Semiconductor", src=["stooq", "^sox"])],
                source_name="Yahoo Finance (fallback: Stooq)", source_url="https://finance.yahoo.com/quote/%5ESOX"),
    "datacenter": dict(sec="tech", title="Data center construction spending (monthly)", unit="$B/mo", fmt="usd", dec=2, freq="m", rng="max", exp=220,
                       series=[dict(name="Construction spend", src=["owid", "monthly-spending-data-center-us"], tf=[["scale", 1e-9]])],
                       source_name="Census C30 via Our World in Data",
                       source_url="https://ourworldindata.org/grapher/monthly-spending-data-center-us"),
    # ---- world ----
    "usdcny": dict(sec="world", title="Dollar-yuan exchange rate", unit="CNY per USD", fmt="num", dec=3, freq="d", rng=15,
                   series=[dict(name="USD/CNY", src=["fred", "DEXCHUS"])],
                   source_name="Federal Reserve via FRED", source_url=F + "DEXCHUS"),
    "china_trade": dict(sec="world", title="U.S.–China goods trade (monthly)", unit="$B", fmt="usd", dec=1, freq="m", rng=15, exp=130,
                        series=[dict(name="Imports from China", src=["fred", "IMPCH"], tf=[["scale", 1e-3]]),
                                dict(name="Exports to China", src=["fred", "EXPCH"], tf=[["scale", 1e-3]])],
                        source_name="Census via FRED", source_url=F + "IMPCH"),
    # ---- v0.2 additions: leading / early-warning indicators (2026-07-03 review pass) ----
    "sahm": dict(sec="economy", title="Sahm rule recession indicator", unit="pp", fmt="pct", dec=2, freq="m", rng=25,
                 series=[dict(name="Sahm rule", src=["fred", "SAHMREALTIME"])],
                 source_name="Federal Reserve via FRED", source_url=F + "SAHMREALTIME",
                 note="Rise of 0.50 pp in the 3-month-avg unemployment rate off its 12-month low; a reading ≥ 0.50 has marked the start of every recession since 1970."),
    "ccsa": dict(sec="economy", title="Continued jobless claims", unit="millions", fmt="num", dec=2, freq="w", rng=5,
                 series=[dict(name="Continued claims", src=["fred", "CCSA"], tf=[["scale", 1e-6]])],
                 source_name="DOL via FRED", source_url=F + "CCSA",
                 note="Rising continued claims = laid-off workers not finding new jobs."),
    "quits": dict(sec="economy", title="Quits rate (JOLTS)", unit="%", fmt="pct", dec=1, freq="m", rng=15,
                  series=[dict(name="Quits rate", src=["fred", "JTSQUR"])],
                  source_name="BLS JOLTS via FRED", source_url=F + "JTSQUR",
                  note="Workers quit when confident; the quits rate turns before payrolls do."),
    "temphelp": dict(sec="economy", title="Temp-help employment, YoY", unit="%", fmt="pct", dec=1, freq="m", rng=25, zero_line=True,
                     series=[dict(name="Temp help YoY", src=["fred", "TEMPHELPS"], tf=["yoy_m"])],
                     source_name="BLS via FRED", source_url=F + "TEMPHELPS",
                     note="Temp staffing is cut first and hired first — it leads the broader labor market."),
    "ppi_yoy": dict(sec="economy", title="Producer prices (PPI final demand), YoY", unit="%", fmt="pct", dec=1, freq="m", rng=15, zero_line=True,
                    series=[dict(name="PPI YoY", src=["fred", "PPIFIS"], tf=["yoy_m"])],
                    source_name="BLS via FRED", source_url=F + "PPIFIS",
                    note="Pipeline inflation — producer prices tend to move before consumer prices."),
    "cons_delinq": dict(sec="economy", title="Consumer loan delinquencies", unit="%", fmt="pct", dec=2, freq="q", rng="max", exp=290,
                        series=[dict(name="Credit cards", src=["fred", "DRCCLACBS"]),
                                dict(name="All consumer loans", src=["fred", "DRCLACBS"])],
                        source_name="Federal Reserve via FRED", source_url=F + "DRCCLACBS"),
    "sloos": dict(sec="markets", title="Banks tightening lending standards (C&I loans)", unit="% of banks, net", fmt="pct", dec=1, freq="q", rng="max", zero_line=True, exp=200,
                  series=[dict(name="Net % tightening", src=["fred", "DRTSCILM"])],
                  source_name="Fed SLOOS via FRED", source_url=F + "DRTSCILM",
                  note="Senior Loan Officer Survey; credit tightening typically leads downturns by 2–3 quarters."),
    "nfci": dict(sec="markets", title="Financial conditions (Chicago Fed NFCI)", unit="index (0 = avg)", fmt="num", dec=2, freq="w", rng=25, zero_line=True,
                 series=[dict(name="NFCI", src=["fred", "NFCI"])],
                 source_name="Chicago Fed via FRED", source_url=F + "NFCI",
                 note="Positive = tighter than average financial conditions."),
    "gpr": dict(sec="world", title="Geopolitical risk index (GPR)", unit="index", fmt="num", dec=0, freq="m", rng="max", exp=120,
                series=[dict(name="GPR", src=["gpr"])],
                source_name="Caldara & Iacoviello", source_url="https://www.matteoiacoviello.com/gpr.htm",
                note="News-based index of geopolitical tensions, monthly since 1985."),
    "gscpi": dict(sec="world", title="Global supply chain pressure (GSCPI)", unit="std devs from avg", fmt="num", dec=2, freq="m", rng="max", zero_line=True, exp=120,
                  series=[dict(name="GSCPI", src=["gscpi"])],
                  source_name="NY Fed", source_url="https://www.newyorkfed.org/research/policy/gscpi",
                  note="Supply-chain stress shows up here before it shows up in goods prices."),
}

EXPECT_DAYS = {"d": 10, "w": 25, "m": 75, "q": 170, "a": 550, "snap": 75, "manual": None}

# ---------------- engine ----------------

def build_chart(cid, cfg):
    if cfg.get("kind") == "bars":
        bars = SOURCES[cfg["series"][0]["src"][0]](*cfg["series"][0]["src"][1:])
        return {"bars": bars}, bars.get("date_through") or bars.get("through", "")
    out = []
    for s in cfg["series"]:
        adapter = SOURCES[s["src"][0]]
        raw = adapter(*s["src"][1:])
        if s["src"][0] == "manual":
            out.append(raw)  # already {name,t,v}
            continue
        if s["src"][0] == "epoch_scatter":
            out.append({"name": s["name"], "t": [p[0] for p in raw], "v": [p[1] for p in raw],
                        "labels": [p[2] for p in raw]})
            continue
        pts = apply_transforms(raw, s.get("tf"))
        if cfg.get("freq") == "d":
            pts = thin(pts)
        out.append({"name": s["name"], "t": [p[0] for p in pts], "v": [p[1] for p in pts]})
    last_obs = max((s["t"][-1] for s in out if s.get("t")), default="")
    return {"series": out}, last_obs

def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    local_only = "--local-only" in sys.argv
    os.makedirs(DATA, exist_ok=True)

    old_manifest = {}
    mpath = os.path.join(DATA, "manifest.json")
    if os.path.exists(mpath):
        with open(mpath) as f:
            old_manifest = json.load(f).get("charts", {})

    manifest = {}
    for cid, cfg in CATALOG.items():
        if only and cid != only:
            manifest[cid] = old_manifest.get(cid, {"status": "skipped"})
            continue
        is_remote = cfg["series"][0]["src"][0] != "manual"
        if local_only and is_remote:
            manifest[cid] = old_manifest.get(cid, {"status": "pending"})
            continue
        try:
            payload, last_obs = build_chart(cid, cfg)
        except Exception as e:
            fb = cfg.get("fallback")
            if fb:
                try:
                    cfg2 = dict(cfg, series=fb)
                    payload, last_obs = build_chart(cid, cfg2)
                except Exception:
                    payload = None
            else:
                payload = None
            if payload is None:
                err = "%s: %s" % (type(e).__name__, str(e)[:400])
                print("FAIL  %-14s %s" % (cid, err))
                prev = old_manifest.get(cid, {})
                manifest[cid] = {"status": "failed", "error": err,
                                 "last_obs": prev.get("last_obs", ""),
                                 "fetched_at": prev.get("fetched_at", "")}
                traceback.print_exc(limit=1)
                continue
        payload["id"] = cid
        with open(os.path.join(DATA, cid + ".json"), "w") as f:
            json.dump(payload, f, separators=(",", ":"))
        status = "manual" if cfg["freq"] == "manual" else "ok"
        manifest[cid] = {"status": status, "last_obs": last_obs,
                         "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
        print("ok    %-14s last=%s" % (cid, last_obs))
        time.sleep(0.4)

    site_catalog = {"sections": [{"id": sid, "title": st, "desc": sd,
                                  "charts": [cid for cid, c in CATALOG.items() if c["sec"] == sid]}
                                 for sid, st, sd in SECTIONS],
                    "overview": OVERVIEW,
                    "charts": {cid: {k: v for k, v in c.items() if k not in ("series", "fallback", "sec")}
                               for cid, c in CATALOG.items()},
                    "expect_days": EXPECT_DAYS}
    with open(os.path.join(DATA, "catalog.json"), "w") as f:
        json.dump(site_catalog, f, separators=(",", ":"))
    if SIGNALS_ENABLED:
        try:
            sig = compute_signals()
            with open(os.path.join(DATA, "signals.json"), "w") as f:
                json.dump(sig, f, separators=(",", ":"))
            print("signals: %d/%d tripped" % (sig["tripped"], len(sig["signals"])))
        except Exception as e:
            print("signals computation failed (non-fatal): %s" % e)
    with open(mpath, "w") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                   "charts": manifest}, f, indent=1)
    fails = [c for c, m in manifest.items() if m.get("status") == "failed"]
    print("\n%d charts, %d failed%s" % (len(manifest), len(fails), (": " + ", ".join(fails)) if fails else ""))

if __name__ == "__main__":
    main()
