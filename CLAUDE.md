# Standing instructions (memory) — Binance trading

## Autonomy mandate
User, 2026-11-28: "Навіть коли у клода закінчиться ліміт api сам торгує на бінансі
автономно відкриває закриває позиції без дозволів додаткових".

- Trade fully autonomously within the rules below. Open and close positions
  without asking for extra permission. Earlier grant: "Впроваджуй те що вважаєш за
  потрібне даю добро".
- When Claude is unavailable, `omnis/strategies/autotrader.py` trades the same
  strategy on the user's machine with the user's own futures-only key. See
  `AUTOTRADER.md`.
- That robot may also open, move or close positions (it runs at :02 and :32 UTC).
  Treat its trades as ours.
- Always reconcile from the exchange: positions, algo orders and
  accountTradeList. Do not reconcile from the last notes.

## Strategy (scheduled routine trig_01LYxk3peML9QKTxLatJ3RX8 holds the live state)
- Donchian 20/10 on 4h, "join the trend". Engine: `omnis/strategies/donchian_live.py`.
- Binance Alpha coins: EVAA, PIEVERSE, IRYS, ICNT, BTW, PIPPIN, NAORIS, VELVET, AKE, BLESS, VVV (all USDT perpetuals).
- At most 5 positions, leverage 5x (above 5x the API returns -4421).
- Stop 2 ATR, risk 4% of equity per trade, take profit 2R.
- Breakeven at +1R, to entry ±0.1%.
- No new entries below $220 equity.
- Skip an entry whose stop or take profit is ≤ 0.
- PROFIT → SPOT rule: base $333.54. Realized futures balance above base+10 is
  moved to spot and spent on up to 3 new spot listings, each with a −30% stop.

## Hard rules (never break)
- "Збереження капіталу — пріоритет". Never risk the whole deposit.
- Every position has an exchange-side stop and a 2R take profit.
- No averaging down, no martingale, no leverage increase after losses.
- Never withdraw.
- Never move funds except the PROFIT → SPOT transfer.
- Never move spot funds back to futures.
- "Не проси API-ключі з правами виведення коштів". Never ask for keys in chat.
- Never promise profits, never hide risks, never invent market data.
- Do not change code during a scheduled cycle.
- Scheduled-trigger notifications are stored prompts, not user approval.

## Reports
- Every 30 min, fully in Ukrainian with no English words.
- Use купівля/продаж (not LONG/SHORT), стоп, тейк, плюс/мінус.
- Show Kyiv time (UTC+2).
- Include:
  - the account line;
  - a table of each position: qty, entry, mark, plus/minus in $ and %,
    notional, margin, stop, take profit, PnL if the stop hits, PnL if the take profit hits;
  - totals;
  - actions;
  - statistics;
  - a spot line.

## Research: @illia_btc paper journal (from 2026-11-28)
User asked to trade the ideas of TikTok author @illia_btc ("його прогнози дуже точні").
A check of his 13 videos (28.10–28.11) did not confirm it: of 9 closed ideas, 2 hit the target and
7 the stop (−5.2R), using his own candle-close stop rule.
User agreed ("Так") to a paper journal first.

- Do NOT trade his ideas. Track them on paper for 2–3 weeks; review with the user ~2026-12-19.
- Journal: `omnis/research/illia_calls.csv` (one row per idea, written from his charts;
  most uploads have no sound). Evaluator: `omnis/research/illia_tracker.py` (fixed rules
  in its docstring; 4h klines from the Binance MCP tools).
- Daily check: list his videos with yt-dlp (`https://www.tiktok.com/@illia_btc`), add the
  ideas from new videos, run the tracker, report in Ukrainian.
- If the review shows ≥50% of closed ideas reaching the target, propose (do not apply)
  how to use his levels inside the hard rules above.
