# Go-live checklist

Live trading uses real money. Work through every box in order, and only the owner switches it on.
If any box cannot be ticked, stay on paper trading.

## 1. Paper trading review (after at least 1-2 weeks)

- [ ] A daily report arrived every morning, with no gaps.
- [ ] `.\deploy\remote.ps1 decisions`: every decision has a sensible reason.
- [ ] `.\deploy\remote.ps1 logs`: no repeated errors or crashes.
- [ ] Paper trades (if any) match what the backtest would expect: few trades, small amounts.
- [ ] Memory and CPU on the server are fine (`.\deploy\remote.ps1 status`).

## 2. Final test on untouched data (run once, then never tune again)

```bash
python -m cryptobot.backtest --start 2025-01-01 --end <today>
```

- [ ] Result written into `docs/experiments.md`, good or bad.
- [ ] Worst drop is within what you can accept (the 1,000 kr total limit stops the bot anyway).
- [ ] You accept that the expected result is roughly break-even, not a profit.

## 3. Bitvavo account

- [ ] Account verified, 2-step verification on.
- [ ] 2,000 kr deposited as EUR. Check Bitvavo's deposit page for methods and fees from a
      Norwegian bank, and note the exchange rate you got.
- [ ] Nothing else on the account that you care about (the bot only uses its own budget, but keep it simple).

## 4. API key (on bitvavo.com, not the app)

- [ ] Permissions: **View** and **Trade** only. **Withdraw must be OFF.**
- [ ] IP whitelist: only the server's public IP (shown on the Oracle instance page).
- [ ] Key and secret pasted into your local `.env` by you, never into chat, email or the repo.
- [ ] `LIVE_TRADING=yes` added to `.env`.

## 5. Safety drills on the server (while still on paper)

- [ ] `.\deploy\remote.ps1 kill` -> "BOT STOPPED" email arrives, bot stays stopped.
- [ ] `.\deploy\remote.ps1 resume` -> "Bot started" email, trading continues.
- [ ] You know how to sell by hand in the Bitvavo app if the bot or server is down.

## 6. Switch on

```powershell
.\deploy\deploy.ps1 -WithEnv
```

- [ ] Email **"Bot started (LIVE)"** arrives, with a cash budget of about 2,000 kr.
- [ ] If instead you get **"Live trading refused to start"**: read the reason, fix it, deploy again.

## 7. First week live

- [ ] Every trade email checked against Bitvavo's order history (price, amount, fee).
- [ ] Daily report checked every morning.
- [ ] `.\deploy\remote.ps1 download` + `python -m cryptobot.tax --year <year>` works.

## Known limitations (accepted for this size)

- **Stop-losses live in the bot, not on the exchange.** If the server is down, open positions
  have no stop. With at most 3 positions of 200 kr, the worst case is limited, but check the
  daily report: a missing report means the bot is not watching.
- **Market orders** pay the taker fee and can fill slightly worse than the last price.
- **Uncertain orders halt the bot.** After a network error during an order, the bot stops and emails
  you. Check Bitvavo's order history, fix the state if needed, then run `remote.ps1 resume`.

## Going back to paper

Set `LIVE_TRADING=no` in `.env` and run `.\deploy\deploy.ps1 -WithEnv`. Sell any coins by hand
or with `remote.ps1 killsell` first if you want to be fully in cash.
