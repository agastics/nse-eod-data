#!/usr/bin/env python3
"""
Daily NSE end-of-day updater for the Nifty 500 paper-trading experiment.

Runs in GitHub Actions after the market close. For every trading day since the last
stored date it downloads NSE's official files:
  * CM bhavcopy (UDiFF format; falls back to sec_bhavdata_full) -> closing price of every stock
  * ind_close_all -> closing values of the Nifty 50 / Nifty 500 indices
and appends the closes to data/closes.json (last 260 sessions per symbol).

Splits/bonuses: NSE's "previous close" column is adjusted for corporate actions. If it
differs from our stored last close by more than 2%, the stored history of that stock is
rescaled by the same factor, so a 1:2 split does not look like a 50% crash.
"""
import csv, datetime as dt, io, json, os, sys, time, zipfile
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CLOSES = os.path.join(DATA, "closes.json")
STATUS = os.path.join(DATA, "status.json")
LIST = os.path.join(DATA, "nifty500.csv")
KEEP = 260
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
INDEX_MAP = {"Nifty 50": "NIFTY50", "Nifty 500": "NIFTY500",
             "Nifty Midcap 150": "NIFTYMIDCAP150", "Nifty Smallcap 250": "NIFTYSMALLCAP250"}

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/",
})

def get(url, tries=3):
    for i in range(tries):
        try:
            r = S.get(url, timeout=30)
            if r.status_code == 200 and r.content:
                return r.content
            if r.status_code == 404:
                return None
        except requests.RequestException as e:
            print("  retry", url, e)
        time.sleep(3 * (i + 1))
    return None

def warm_up():
    try:
        S.get("https://www.nseindia.com/", timeout=20)
    except requests.RequestException:
        pass

def stock_closes(day):
    """Return {SYMBOL: (close, prev_close)} for EQ/BE series, or None if no file (holiday / not yet published)."""
    ymd, dmy = day.strftime("%Y%m%d"), day.strftime("%d%m%Y")
    raw = get(f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip")
    out = {}
    if raw:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            text = z.read(z.namelist()[0]).decode("utf-8", "replace")
        for r in csv.DictReader(io.StringIO(text)):
            if r.get("SctySrs", "").strip() in ("EQ", "BE", "BZ"):
                try:
                    out[r["TckrSymb"].strip()] = (float(r["ClsPric"]), float(r.get("PrvsClsgPric") or 0))
                except (KeyError, ValueError):
                    pass
        if out:
            return out, "udiff"
    raw = get(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dmy}.csv")
    if raw:
        rows = csv.reader(io.StringIO(raw.decode("utf-8", "replace")))
        head = [h.strip() for h in next(rows)]
        ix = {h: i for i, h in enumerate(head)}
        for r in rows:
            r = [c.strip() for c in r]
            if len(r) == len(head) and r[ix["SERIES"]] in ("EQ", "BE", "BZ"):
                try:
                    out[r[ix["SYMBOL"]]] = (float(r[ix["CLOSE_PRICE"]]), float(r[ix["PREV_CLOSE"]]))
                except ValueError:
                    pass
        if out:
            return out, "sec_bhavdata_full"
    return None, None

def index_closes(day):
    raw = get(f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{day.strftime('%d%m%Y')}.csv")
    out = {}
    if raw:
        for r in csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))):
            name = (r.get("Index Name") or "").strip()
            if name in INDEX_MAP:
                try:
                    out[INDEX_MAP[name]] = float(r["Closing Index Value"])
                except (KeyError, ValueError):
                    pass
    return out

def refresh_list():
    raw = get("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv")
    if raw and b"Symbol" in raw[:400] and raw.count(b"\n") > 400:
        open(LIST, "wb").write(raw)
        print("Nifty 500 list refreshed")

NICE = [1/2, 1/3, 1/4, 1/5, 1/10, 2/3, 2/5, 2, 3, 4, 5, 10]

def repair_corporate_actions(rows, members, lookback=15):
    """Bonus/split/consolidation shows up as a one-day jump no price band allows (> ~25%)
    landing close to a simple ratio (1:1 bonus -> x0.5, 1:5 split -> x0.2, ...). Rescale the
    history before that day by the ratio. Idempotent: once fixed, the jump is gone."""
    fixed = []
    for sym in members:
        h = rows.get(sym)
        if not h or len(h) < 2:
            continue
        start = max(1, len(h) - lookback)
        for i in range(start, len(h)):
            prev, cur = h[i - 1][1], h[i][1]
            if prev <= 0:
                continue
            f = cur / prev
            if 0.75 <= f <= 1.4:
                continue
            ratio = min(NICE, key=lambda r: abs(f / r - 1))
            if abs(f / ratio - 1) <= 0.12:
                for j in range(i):
                    h[j][1] = round(h[j][1] * ratio, 2)
                fixed.append(f"{sym} x{ratio:.4f} before {h[i][0]} (raw jump {f - 1:+.1%})")
    return fixed

def main():
    now = dt.datetime.now(IST)
    store = json.load(open(CLOSES))
    rows = store["rows"]
    status = json.load(open(STATUS)) if os.path.exists(STATUS) else {}
    warm_up()
    if now.day <= 7 or not os.path.exists(LIST):
        refresh_list()
    members = {r["Symbol"].strip() for r in csv.DictReader(open(LIST))}
    last = dt.date.fromisoformat(store["last_date"])
    day, end = last + dt.timedelta(days=1), now.date()
    if now.hour < 18:
        end -= dt.timedelta(days=1)  # today's files are not out yet
    added, adjusted, log = [], [], []
    while day <= end:
        if day.weekday() < 5:
            eq, src = stock_closes(day)
            if eq:
                idx = index_closes(day)
                d = day.isoformat()
                for sym in members:
                    if sym not in eq:
                        continue
                    close, prev = eq[sym]
                    hist = rows.setdefault(sym, [])
                    if hist and prev > 0 and hist[-1][0] < d:
                        f = prev / hist[-1][1]
                        if abs(f - 1) > 0.02 and 0.05 < f < 20:
                            for h in hist:
                                h[1] = round(h[1] * f, 2)
                            adjusted.append(f"{sym} x{f:.4f} on {d}")
                    if not hist or hist[-1][0] < d:
                        hist.append([d, close])
                    rows[sym] = hist[-KEEP:]
                for sym, v in idx.items():
                    hist = rows.setdefault(sym, [])
                    if not hist or hist[-1][0] < d:
                        hist.append([d, v])
                    rows[sym] = hist[-KEEP:]
                store["last_date"] = d
                added.append(d)
                log.append(f"{d}: {sum(1 for s in members if s in eq)} stocks ({src}), indices {sorted(idx)}")
            else:
                log.append(f"{day.isoformat()}: no bhavcopy (holiday or not published)")
        day += dt.timedelta(days=1)
    adjusted += repair_corporate_actions(rows, members)
    store["updated_at"] = now.isoformat(timespec="minutes")
    json.dump(store, open(CLOSES, "w"), separators=(",", ":"))
    status.update({"last_trading_date": store["last_date"], "checked_at": store["updated_at"],
                   "days_added": added, "adjustments": adjusted[-50:], "log": log[-30:],
                   "symbols": len(rows)})
    json.dump(status, open(STATUS, "w"), indent=1)
    print("\n".join(log) or "nothing new")
    print("adjusted:", adjusted)

if __name__ == "__main__":
    main()
