# Crypto tax in Norway (short guide)

This is a plain-language summary to help understand what the bot's tax export does.
**It is not tax advice. Rules and rates change: always check with Skatteetaten (skatteetaten.no).**

## The main rules

| Topic | Rule |
|---|---|
| What is taxed | Gains when you sell (or swap) crypto. Holding without selling is not a gain. |
| Rate | Gains are ordinary income, taxed at 22 % (check the current year's rate). |
| Losses | Losses can be deducted from other income. |
| What counts as a sale | Selling for EUR/NOK, swapping one coin for another, paying with crypto. |
| Order of coins sold | **FIFO**: the coins you bought first are counted as sold first, per coin. |
| Fees | Buy fees add to the cost; sell fees reduce the sale value. |
| Currency | Everything is reported in NOK. The export uses Norges Bank's official EUR/NOK rate for each trade date. |
| Wealth tax | Crypto and EUR on the exchange count as wealth, valued at 31 December. The wealth tax only applies above a high threshold, so at this project's size it is almost certainly zero, but the holdings must still be reported. |
| Paper trades | Not real, not taxable. The export ignores them unless `--include-paper` is used for testing. |

## Where to report

In the tax return (skattemeldingen), under **virtuell valuta / kryptovaluta**:
one entry per coin and exchange, with gains, losses and the holding at 31 December.
The deadline is normally 30 April the year after.

## How to make the export

```bash
.\deploy\remote.ps1 download                                    # copy trades from the server
python -m cryptobot.tax --year 2027 --trades data\server\trades.csv
```

This writes, in `reports/tax/` (never committed):
- `tax_<year>_transactions.csv`: every trade with NOK rate, NOK value, cost basis and gain.
- `tax_<year>_summary.txt`: total gains, losses, net result, a 22 % estimate and holdings.

Keep these files and Bitvavo's own year-end statement together. If the numbers disagree,
Bitvavo's statement and Skatteetaten's guidance win.
