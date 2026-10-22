"""paper_trade: deterministic paper-trading matching engine (pt-v1.1 → pt-v1.3).

Research-only engine that matches injected orders (or a strategy's per-day
orders) against bars under each market's frozen rules and returns one in-band
JSON document.  It NEVER places a real order and touches no broker /
trading connector (REQUIREMENTS R1).

Module map — matching and wire (pt-v1.1 core): ``schema.py`` (wire contract +
boundary parse), ``state_hash.py`` (JCS ``received_state_hash`` and the D3
engine fingerprint), ``calendar.py`` (cn_a CH truth / hk-us zero calendar),
``limits_cn_a.py`` (cn_a limit/ST/suspension), ``data.py`` (market data supply +
AM-9 federation), ``rules.py`` (three-market rule matrix + ``RULES_VERSION``),
``engine.py`` (day-loop matching), ``corporate_actions.py`` (split detector),
``wire.py`` (success/error serialization), ``tool.py`` (the ``paper_trade_step``
entry), ``strategy_exec.py`` (Step 7 strategy_path execution).

pt-v1.2 additive surface (cn_a 5m post-close step, P0): ``data_5m.py`` (stk_mins
fetch + continuous-session assembly), ``trading_days.py`` (cn_a session clock and
completed-day resolution), ``engine_adapter.py`` (wire document ↔
:class:`StepOutcome`), ``account.py`` / ``store.py`` / ``storage_io.py``
(incremental ledger, atomic account writes, append-only step directories),
``runner.py`` (single-day ``step`` + serial ``catch_up``), ``recovery.py``
(crash-window adoption).  All of it is opt-in: a call that omits every v1.2
request field is byte-identical to pt-v1.1.

pt-v1.3 additive surface (cn_a 5-minute intraday, P1): ``schema_v13.py`` (bar
request/result shapes, order lifecycle and phase vocabularies),
``bar_clock.py`` (the pure 48-bar lattice and phase classifier), ``bar_layout``
/ ``bar_store.py`` (immutable per-bar ledger steps), ``orders.py`` (the order
state machine), ``slippage.py`` (adverse fill pricing), ``fill_rules.py``
(limit and price-band decisions), ``bar_engine.py`` (one bar of matching),
``session_runner.py`` (the single writer and its crash contract),
``session_replay.py`` (a whole day), ``scheduler.py`` (one tick of a forward
loop), ``session_summary.py`` (the day summary) and ``feeds.py`` (the 5m frame
adapters).  Model:

* **Grid.** 48 bars per cn_a session, labelled by bar END (the label is the
  instant the bar closes, never its start): ``09:35``..``11:30`` (24 morning
  bars) and ``13:05``..``15:00`` (24 afternoon bars).  The 95-minute lunch
  break, the ``09:30`` opening-auction print and the ``14:57``-``15:00``
  closing-auction window are not continuous bars.  All times are
  Asia/Shanghai (UTC+08:00, no DST).
* **Phases.** ``session_phase`` is one of ``continuous`` (the only phase that
  matches orders), ``opening_auction_print``, ``auction_window`` or
  ``outside``; an empty value means "the caller did not classify this bar"
  and the clock derives it from ``bar_time``.
* **Order lifecycle.** ``new`` → ``filled`` | ``canceled`` | ``rejected`` |
  ``expired``.  ``replace`` keeps the same ``order_id``/``seq`` and leaves the
  order ``new``.  There are NO partial fills: a fill consumes the whole
  remaining quantity, so IOC and FOK differ only in the terminal reason token
  (``ioc_unfilled`` / ``fok_unfilled``).  ``day`` orders rest until the 15:00
  session end and then ``expire``; nothing rests overnight (no GTD).  A bar
  with no tradable print (a missing frame or a zero-volume bar) is a NO-TRADE
  outcome: resting orders stay ``new`` and the bar reports a ``data_missing``
  warning — only an explicit ``suspended_symbols`` input rejects
  ``suspended``.  The wire spelling is ``canceled``, one ``l``.
* **Fill price.** Every fill reference is moved against the trader first
  (``slippage.model``: ``none``, ``fixed_bps`` with a 10 bps default, or
  ``fixed_amount``), then capped by the order's own limit and clamped by the
  cn_a price band WHEN the session has one.  The band comes from an injected
  limits table or the request's explicit ``prev_closes`` (previous daily
  close per symbol); with neither it is UNKNOWN and the band slots are
  skipped, never fabricated.  A resulting price that is non-positive or beyond
  a known band is rejected with the frozen ``limit_up`` / ``limit_down``
  token, an unmatchable limit keeps ``limit_not_marketable``, and nothing is
  silently corrected into a fill.  In ``live`` mode the stored account's
  ``slippage_model`` is the default execution model (``none`` means zero
  slippage end to end); a bar request that carries a different explicit spec
  overrides it for that bar.
* **Feeds.** The live 5m adapter reads Tushare ``stk_mins`` one symbol and one
  day per call, and that provider's daily quota is small.  A missing 5m row is
  carried as an honest gap (no invented OHLC); a provider outage is
  ``feed_unavailable``; an adapter without a configured token skips instead of
  calling.  A single missing row does not fail a day, a day with no frames
  does (``partial``, never a fabricated close).
* **Opt-in.** The v1.3 surface is selected by the PRESENCE of a v1.3-only
  request field (``bar_time``, ``order_actions``, ``cancel_order_ids``,
  ``replace_orders``, ``slippage`` and the flat ``slippage_model`` /
  ``slippage_bps`` / ``slippage_amount`` spellings, ``session_phase``,
  ``open_orders``, ``order_seq``, ``prev_closes``).  A request that omits them keeps the
  pt-v1.1/pt-v1.2 document byte for byte, and ``schema.parse_request``
  refuses a v1.3 field rather than ignoring it.
* **Tools.** ``paper_trade_account_create`` opens an account from
  caller-injected ``today`` / ``next_trading_day``; ``paper_trade_bar_step``
  applies exactly one bar (place / cancel / replace) and returns the bar
  result; ``paper_trade_account_status`` reads cash, positions, open orders and
  the applied-bar watermark.  ``paper_trade_step`` keeps its pt-v1.1/v1.2
  daily parameter surface; it takes no v1.3-only argument.
* **Idempotency.** ``session_runner.bar_step`` is the ONLY writer of a bar.  A
  duplicate application is refused with ``bar_already_applied`` and writes not
  one byte; a crash between the step write and the account write is adopted
  from the matching request/result hashes, so one bar yields one set of fills.
  ``session_replay.replay_session`` drives all 48 labels (with a
  ``dry_run`` that is verified zero-write); ``scheduler.SessionScheduler.tick``
  applies at most one bar per call and never fills a missed beat.
* **Limits.** cn_a 5m only; hk/us keep the pt-v1.1/v1.2 daily path and a bar
  step for another market is refused.  No wall clock anywhere: the caller
  injects ``bar_time`` / ``today`` / ``as_of``, so a replay is reproducible on
  any host.  No broker, no order placement, no partial fills, no overnight
  orders.

The tool registry's auto-discovery (``src/tools/__init__.py``) imports this
package at agent startup and collects :class:`PaperTradeStepTool` via
``BaseTool.__subclasses__()`` (VD-12).  Consumers import submodules explicitly
(``from src.tools.paper_trade import engine``); this ``__init__`` re-exports
only the tool class the registry discovers.
"""

from __future__ import annotations

from .tool import PaperTradeStepTool

__all__ = ["PaperTradeStepTool"]
