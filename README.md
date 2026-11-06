# NSE end-of-day prices (Nifty 500)

Feeds a **paper-trading** experiment (virtual money only). A GitHub Action downloads NSE's
official daily files after the market close and keeps the last 260 closing prices of every
Nifty 500 stock plus the Nifty 50 / Nifty 500 indices in `data/closes.json`.

- `data/closes.json` – `{"last_date": "...", "rows": {"SYMBOL": [["YYYY-MM-DD", close], ...]}}`
- `data/status.json` – last trading date fetched, log of the last run, split/bonus adjustments
- `data/nifty500.csv` – index members (refreshed in the first week of each month)
- `scripts/update_eod.py` – the updater; `.github/workflows/nse-eod.yml` – schedule (19:02 and 20:47 IST, Mon–Fri)

Seed history (to 23 Oct 2026): eod2_data by BennyThadikaran (split/bonus-adjusted NSE data).
Not investment advice.
