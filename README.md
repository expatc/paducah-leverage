# Paducah market leverage

A buyer-vs-seller tracker for Paducah, KY that rebuilds itself every month
with no input from you.

## What it does

`build_paducah.py` streams Redfin's city-level Data Center files, pulls the
Paducah, KY rows, scores four leverage signals, and writes `index.html` and
`data.json`. GitHub Actions runs it on the 18th of each month and publishes
the result to GitHub Pages.

## Setup (about five minutes, once)

1. Create a new GitHub repository.
2. Add `build_paducah.py` and `.github/workflows/monthly.yml` to it.
3. **Settings → Pages → Source: GitHub Actions**.
4. **Actions → Rebuild Paducah leverage → Run workflow** to build it now.

Your dashboard lives at `https://<username>.github.io/<repo>/`. Actions
minutes are free on public repos; the job takes a few minutes.

## Sources, and why these and not scraping

| Source | What it gives | Access |
|---|---|---|
| Redfin Data Center | City-level inventory, closings, months of supply, sale-to-list, DOM, price drops | Public S3 bucket, no key, citation requested |
| Zillow Research | Metro home-value index, metro inventory and pending counts | Public CDN, no key, attribution required |

Both are the companies' own bulk exports, published for exactly this use.
Scraping their consumer pages would get you the same numbers through a
worse door: Zillow's and Redfin's terms of use prohibit automated
collection, the listing data underneath is licensed from the Western
Kentucky Regional MLS with redistribution limits, and both sites sit behind
bot detection that breaks scrapers without warning. The bulk files are more
complete, reach back to 2014, and will not change out from under you.

## The four signals

| Signal | Neutral | Favors buyers when |
|---|---|---|
| Months of supply | 5.0 | higher |
| Average sale-to-list ratio | 0.990 | lower |
| Median days on market | 45 | higher |
| Share of listings cutting price | 20% | higher |

Each maps to a −100 (sellers) to +100 (buyers) scale, then averages. The
baselines are constants at the top of the script. If six months of watching
convinces you Paducah's normal sits elsewhere, change them there.

## Three limits worth knowing

**Redfin's city figures are rolling three-month windows.** Each reading is
labelled by the month its window ends (the October 2026 reading covers
August through October). That smooths single-month noise in a small market,
and it means the page moves slowly: fine for trend-watching, useless for
reacting to this week. Before November 2026 this page used Redfin's older
single-month city file, which Redfin retired.

**The average hides disagreement.** In May 2026 the four signals spanned
148 points: months of supply said sellers, price cuts said buyers. The page
flags a spread over 90 points and tells you to read the rows instead of the
beam. Do that.

**Redfin does not see every sale.** Its coverage in small markets is good
but not complete, so treat levels as approximate and direction as the real
signal. Cross-check the level against Kentucky REALTORS® monthly statewide
statistics.
