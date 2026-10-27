# Experiment log

Every strategy change is written down here *before* it is run, with the rule for success.
Results are added afterwards, good or bad. This keeps us from quietly tuning the model until
history looks good (overfitting by trial and error).

Development period: 2022-01-01 to 2024-12-31 (walk-forward, 6-month folds).
Final test period 2025-01-01 onwards: untouched until all choices are frozen.

---

## Exp 0 - first ML model (4h candles)

Setup: 17 features, label = beats 0.7 % round-trip cost after 24 h, LightGBM
(200 trees, 15 leaves, min 200 rows per leaf), enter at p > 0.55, exit at p < 0.50.

| | Baseline 4h | ML 4h |
|---|---|---|
| End value (from 2,000 kr) | 1,936 kr | 1,530 kr |
| Worst drop | -10.4 % | -25.2 % |
| Trades / week | 1.5 | 2.4 |
| Fees | 229 kr | 371 kr |
| Test AUC (avg of 6 folds) | - | 0.565 |
| Train AUC (avg) | - | 0.757 |

Conclusion: weak real signal, clear overfitting gap, fees ate most of it. No edge.

---

## Exp 1 - one planned round of fixes (registered 2026-10-27, before running)

**Changes (decided in advance, no further tuning):**
1. Minimum holding time = the prediction horizon. The model predicts a move over H candles,
   so it must hold for H candles (stop-loss still applies). Fewer trades, fewer fees.
2. Stronger regularisation: 150 trees, max 7 leaves, depth 3, min 300 rows per leaf
   (100 on daily data), 60 % of features per tree, L2 penalty 5.
3. Two runs:
   - **1a:** 4h candles, horizon 6 (24 h), min hold 6.
   - **1b:** daily candles, horizon 5 (5 days), min hold 5.
   Baselines are run on the same timeframe without changes.

**Success rule (ML must pass all three on 2022-2024):**
- A. Total return higher than the baseline on the same timeframe.
- B. Total return higher than holding BTC with the same average exposure.
- C. Average test AUC at least 0.53.

**If neither run passes:** the ML approach has no edge at this size. We stop tuning it and go
forward with the baseline as a cautious learning strategy.

**Results (run 2026-10-27):**

| | Baseline 4h | ML 1a (4h) | Baseline 1d | ML 1b (1d) |
|---|---|---|---|---|
| End value (from 2,000 kr) | 1,936 kr | 1,929 kr | 1,910 kr | 1,933 kr |
| Total return | -3.2 % | -3.6 % | -4.5 % | -3.3 % |
| Hold BTC, same exposure | +7.1 % | +1.1 % | +3.5 % | +6.4 % |
| Worst drop | -10.4 % | -8.0 % | -8.2 % | -8.8 % |
| Trades / week | 1.5 | 0.9 | 0.3 | 0.8 |
| Fees | 229 kr | 145 kr | 40 kr | 118 kr |
| Test AUC (avg) | - | 0.568 | - | 0.538 |
| Train AUC (avg) | - | 0.661 | - | 0.786 |

| Rule | 1a (4h) | 1b (1d) |
|---|---|---|
| A. Beats baseline | No (-3.6 % vs -3.2 %) | Yes (-3.3 % vs -4.5 %) |
| B. Beats hold BTC, same exposure | No | No |
| C. Test AUC >= 0.53 | Yes | Yes (barely) |
| **Pass** | **No** | **No** |

**What changed:** minimum holding time cut ML fees by 60 % (371 -> 145 kr) and the loss from
-23.5 % to -3.6 %. Stronger regularisation shrank the overfitting gap on 4h (0.76 -> 0.66 train AUC)
while test AUC stayed about the same. Daily candles had too little data; the model still memorised.

**Conclusion:** neither run passed. The model finds a small, steady signal (AUC about 0.56), but it is
not large enough to make money after costs at this size. As decided in advance, we stop tuning the
ML model. The baseline goes forward as a cautious learning strategy. The ML model may run in
*shadow mode* (predicting and logging, never trading) to collect real out-of-sample evidence.
