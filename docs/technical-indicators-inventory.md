# Technical indicators inventory (DexScreener/TradingView native library)

Source: full "Indicators" picker on DexScreener's chart widget (screenshots, 06/08),
~105 entries, TradingView's standard public indicator library. Cross-checked
against the earlier web-research pass (arXiv 2507.01963 wash-trading paper,
volume-filter/wick-gate evidence already validated on v8's own 32-trade sample --
see CLAUDE.md "Active state -- scalping_v8" and `docs/HANDOFF_PIPELINE_MOMENTUM.md`
2026.08.05 entry).

Every entry: what it measures, typical use, and a verdict on fit for v8's own
shape (5-15min candles, wick-confirmed RSI-divergence REVERSAL entry, flat
trailing-stop exit, no fixed TP). `already-in-v8` = literally already used by
`evaluate_v8_wick_reversal`. `candidate` = plausible next test, reasoning
given. `not-a-fit` = real reason it doesn't suit a short-horizon reversal
scalper, not just "unfamiliar".

## Trend-following / moving averages

| Indicator | Measures | Typical use | v8 fit |
|---|---|---|---|
| Moving Average (SMA) | Simple average price over N periods | Trend direction, dynamic support/resistance | not-a-fit -- v8 is a reversal engine, a trend filter fights its own premise unless used as a REGIME gate (see Choppiness Index below) |
| Moving Average Exponential (EMA) | Weighted average, recent bars count more | Faster trend-following than SMA | same as SMA |
| Double EMA (DEMA), Triple EMA (TEMA) | Reduced-lag EMA variants | Faster trend confirmation | not-a-fit, same lag-vs-reversal tension |
| Moving Average Weighted (WMA), Hamming, Adaptive (KAMA), Hull (HMA) | Alternative MA smoothing/weighting schemes | Trend-following with different lag/noise tradeoffs | not-a-fit for entry; KAMA's volatility-adaptive smoothing could inform the trailing-stop width later (separate question from entry) |
| Moving Average Channel, Double, Multiple, Triple, MA Cross, MA with EMA Cross | MA-crossover systems | Trend change signals | not-a-fit -- crossover systems are trend-continuation tools, opposite thesis to v8 |
| Guppy Multiple Moving Average | Bundle of short+long EMAs | Visualizes trend strength via ribbon spread/compression | not-a-fit, same reason |
| Arnaud Legoux Moving Average (ALMA) | Gaussian-weighted MA, low lag + low noise | Cleaner trend line than SMA/EMA | not-a-fit |
| Least Squares Moving Average (LSMA), Linear Regression Curve, Linear Regression Slope | Regression-fitted trend line/slope | Trend direction + rate of change | not-a-fit for entry |
| McGinley Dynamic | Self-adjusting MA (speeds up/slows with volatility) | Reduces MA whipsaw in choppy markets | not-a-fit |
| Smoothed Moving Average (SMMA/RMA) | Wilder's smoothing (same base as RSI/ATR) | Slower trend line | not-a-fit standalone -- already implicitly inside RSI/ATR, which v8 uses |
| Ichimoku Cloud | Multi-line trend/support-resistance/momentum system | All-in-one trend regime read | candidate -- the cloud itself (kumo) could serve as v8's regime filter (price above/below cloud = trending, inside = choppy), distinct question from Choppiness Index below, worth comparing |
| SuperTrend | ATR-based trailing trend line | Trend-following stop/entry | not-a-fit for entry (trend tool); SuperTrend's ATR-multiplier logic is conceptually close to v8's OWN trailing-stop mechanism already, no new info |
| Parabolic SAR | Trailing stop-and-reverse dots | Trend-following exit/reversal marker | not-a-fit -- designed for trend-following exits, v8 already has its own flat trailing-stop discipline (CLAUDE.md: never touch without reason) |
| Average Directional Index (ADX) + Directional Movement (+DI/-DI) | Trend STRENGTH (not direction) | Filter: only trade when a trend is strong/weak enough | **candidate, HIGH PRIORITY** -- directly testable on v8's existing 32 trades with zero new data collection: does v8 (a reversal strategy) systematically lose when ADX shows a strong trend? Already flagged as the #1 pick from the earlier research pass. |
| Choppiness Index | Trendiness vs. choppiness (0-100 range) | Regime filter, similar role to ADX | **candidate, HIGH PRIORITY**, same reasoning as ADX -- test both, they're not redundant (CHOP uses true range sum, ADX uses directional movement) |
| Aroon | Time since last N-period high/low | Trend presence/absence, early trend-change signal | candidate, lower priority -- overlaps conceptually with ADX/Choppiness, test only if those two prove inconclusive |
| Vortex Indicator | Positive/negative trend movement comparison | Trend change confirmation | not-a-fit, redundant with ADX for v8's purpose |
| Chande Kroll Stop | ATR-based stop-loss bands | Trailing stop placement | not-a-fit -- v8's own ATR-derived trail (`MIN/MAX_ATR_TRAIL_PCT_SCALPING`) already covers this ground, changing it needs a dedicated data-driven review, not a bolt-on indicator |
| Know Sure Thing (KST) | Smoothed ROC composite, momentum+trend hybrid | Long-horizon trend-momentum signal | not-a-fit -- built for swing/position timeframes, mismatched to v8's 5-15min horizon |
| Mass Index | Range expansion/contraction (reversal-adjacent) | Flags potential trend reversal via range widening | candidate, lower priority -- conceptually closest trend-family indicator to v8's own reversal thesis, worth a look if ADX/Choppiness/volume filters are exhausted |

## Momentum / oscillators

| Indicator | Measures | Typical use | v8 fit |
|---|---|---|---|
| Relative Strength Index (RSI) | Momentum, overbought/oversold | Reversal signal | already-in-v8 (core signal, divergence-based) |
| Money Flow Index (MFI) | Volume-weighted RSI | Reversal signal with volume confirmation | already-in-v8 (v9's core signal, not v8's -- worth testing whether MFI divergence, not just RSI, sharpens v8 too, since v9's synchronized-oversold design already validates the RSI+MFI combo empirically on a DIFFERENT pocket) |
| Stochastic, Stochastic RSI | Close position within recent range | Overbought/oversold, reversal timing | candidate -- v8's own 03/08 fix (truncating the unclosed candle) was validated by finding Stochastic %K correlated with the worst losses; a dedicated Stochastic confirmation gate (not just as a diagnostic) is untested |
| Connors RSI | Composite of RSI + streak + rank | Sharper short-term mean-reversion signal than plain RSI | candidate, direct thesis match -- built specifically for short-horizon mean-reversion, same family as v8's premise |
| Williams %R | Inverse-scaled Stochastic | Overbought/oversold | not-a-fit -- mathematically near-identical info to Stochastic, redundant to add both |
| Awesome Oscillator | Momentum via MA-of-MA difference | Momentum shift/zero-cross signals | not-a-fit -- built for swing timeframes (Bill Williams system), documented poor short-horizon performance |
| Accelerator Oscillator | Momentum's rate of change | Early momentum-shift warning | not-a-fit, same Bill Williams family, same horizon mismatch |
| Chande Momentum Oscillator (CMO) | Bounded momentum (-100/+100) | Similar role to RSI, different smoothing | not-a-fit -- redundant with RSI already in use |
| Rate Of Change (ROC), Momentum | Raw price change over N periods | Momentum magnitude | not-a-fit standalone -- too noisy unsmoothed for a 5-15min reversal signal |
| Ultimate Oscillator | Multi-timeframe weighted momentum | Reduces single-timeframe divergence false signals | candidate, lower priority -- the multi-timeframe blending is conceptually interesting against v8's own single-timeframe divergence, but adds real complexity for an unproven gain |
| Relative Vigor Index (RVI) | Close-vs-open strength relative to range | Momentum/trend confirmation | not-a-fit, weaker evidence base than RSI/MFI already in use |
| Balance of Power | Buying vs selling pressure within the bar | Momentum confirmation | not-a-fit, redundant with existing wick-ratio gate (which already measures intra-candle pressure more directly) |
| True Strength Index (TSI) | Double-smoothed momentum | Trend-momentum hybrid, less noisy than raw momentum | not-a-fit, smoothing lag mismatched to 5-15min reversal timing |
| TRIX | Triple-smoothed ROC of an EMA | Very smooth momentum/trend signal | not-a-fit, too much lag for v8's horizon |
| Klinger Oscillator | Volume-based trend/momentum hybrid | Confirms trend via volume flow | see Volume section -- the VOLUME half of this is more relevant than its momentum half |
| Fisher Transform | Normalizes price into a Gaussian-like distribution | Sharpens turning-point signals | candidate, direct thesis match -- explicitly designed to make reversal turning points sharper/clearer, exactly v8's use case |
| Detrended Price Oscillator (DPO) | Price minus a lagged MA, removes trend | Cycle/turning-point identification | candidate, lower priority -- same reversal-oriented family as Fisher, less commonly validated in literature |
| SMI Ergodic Indicator/Oscillator | Stochastic Momentum Index variant | Smoother overbought/oversold signal | not-a-fit, redundant with Stochastic RSI already candidate above |
| Majority Rule | Binary up/down vote across multiple MAs | Simple trend-consensus signal | not-a-fit, redundant with ADX/trend family above |

## Volatility

| Indicator | Measures | Typical use | v8 fit |
|---|---|---|---|
| Average True Range (ATR) | Average bar range (volatility magnitude) | Position sizing, stop width | already-in-v8 (trailing-stop width, `entry_atr_pct`) |
| Bollinger Bands, %B, Width | Price bands at N std-dev from a MA | Volatility regime, mean-reversion zone | candidate -- Bollinger Band Width (squeeze detection) already flagged in the earlier research pass; %B could double-confirm RSI oversold at the band's lower edge |
| Keltner Channels | ATR-based bands (vs Bollinger's std-dev) | Similar to Bollinger, different volatility measure | not-a-fit, redundant with Bollinger Band Width if that's adopted |
| Standard Deviation, Standard Error, Standard Error Bands | Statistical price dispersion | Volatility/regression-confidence measures | not-a-fit, ATR already covers volatility sizing; std-dev bands redundant with Bollinger |
| Historical Volatility | Annualized realized volatility | Longer-horizon volatility comparison | not-a-fit, wrong horizon (built for options/multi-day), ATR already serves the short-horizon equivalent |
| Chaikin Volatility | Rate of change of the high-low range | Volatility expansion/contraction | not-a-fit, redundant with ATR |
| Volatility Close-to-Close, O-H-L-C, Zero Trend Close-to-Close | Alternative realized-volatility estimators | Statistical volatility measurement | not-a-fit, same redundancy as Historical Volatility |
| Relative Volatility Index (RVI, volatility variant) | RSI formula applied to std-dev instead of price | Volatility-trend hybrid | not-a-fit, niche/rarely validated |
| Envelopes | Fixed % bands around an MA | Simple overbought/oversold bands | not-a-fit, strictly weaker than Bollinger (fixed % vs adaptive std-dev) |
| Donchian Channels | Highest-high/lowest-low channel | Breakout/range identification | not-a-fit -- breakout tool, opposite thesis to reversal |
| Price Channel | Similar to Donchian | Range/breakout levels | not-a-fit, same reason |

## Volume

| Indicator | Measures | Typical use | v8 fit |
|---|---|---|---|
| Volume | Raw traded volume per bar | Activity/liquidity confirmation | **candidate, HIGH PRIORITY** -- already the #1 concrete pick from the earlier research pass: 2x avg volume on the wick-confirmation candle documented at 71% WR vs the already-validated 60% baseline. Cheapest, most direct next test. |
| On Balance Volume (OBV) | Cumulative volume weighted by price direction | Volume-price divergence confirmation | candidate -- OBV divergence could serve as a THIRD confirmation leg alongside RSI+wick, worth testing after the raw volume filter above |
| Volume Oscillator | Fast/slow volume MA difference | Volume trend shift | not-a-fit, redundant with raw volume filter above |
| Price Volume Trend (PVT) | Cumulative volume weighted by % price change | Similar role to OBV, different weighting | not-a-fit, redundant with OBV if adopted |
| Chaikin Money Flow (CMF) | Volume-weighted accumulation/distribution over N bars | Buying/selling pressure via volume | candidate, lower priority -- conceptually close to MFI (already in v9), less validated for v8's specific wick-reversal shape |
| Chaikin Oscillator | MACD of the Accumulation/Distribution line | Volume-momentum hybrid | not-a-fit, redundant with CMF |
| Accumulation/Distribution | Cumulative volume-weighted close-position line | Long-term accumulation/distribution trend | not-a-fit, wrong horizon (built for swing/position analysis) |
| Klinger Oscillator (volume half) | Volume force over trend | Confirms trend reversals via volume | candidate, lower priority -- see Momentum section, its volume-flow logic overlaps OBV |
| Ease Of Movement | Price change relative to volume ("how easily" price moves | Low-volume price move = weak signal flag | candidate, lower priority -- could flag thin-liquidity wick spikes, a known risk already partially handled by v8's existing liquidity gates |
| Money Flow Index (MFI) | (listed under Momentum above -- volume+price hybrid) | -- | see Momentum section |
| VWAP | Volume-weighted average price (session-anchored) | Intraday fair-value reference | not-a-fit for a token market open 24/7 with no clean session anchor -- VWAP's core assumption (a session open) doesn't hold |
| VWMA | Volume-weighted moving average | Volume-weighted trend line | not-a-fit, same trend-family exclusion as other MAs |
| Volume Profile Fixed Range, Visible Range | Volume distribution by price level | Support/resistance via volume concentration | not-a-fit for automated entry logic -- these are visual/manual-analysis tools, not naturally reducible to a single testable signal |
| Net Volume | Buy volume minus sell volume | Directional volume pressure | not-a-fit, redundant with OBV |
| Price Oscillator | (MA-based, listed for volume-adjacent context) | Momentum via % MA difference | not-a-fit, redundant with existing momentum indicators |

## Candlestick / price-action & pattern tools

| Indicator | Measures | Typical use | v8 fit |
|---|---|---|---|
| (wick ratio gate) | Candle wick-to-body ratio | Reversal-candle confirmation | already-in-v8 (the CORE gate, ≥0.3 wick ratio, empirically validated 60% WR vs 25.6% p=0.026) |
| Zig Zag | Filters small price moves, highlights swing points | Visual swing-high/low identification | not-a-fit for live signals -- repaints (redraws past points as new data arrives), unusable as a real-time entry trigger |
| Williams Fractal | 5-bar local high/low pattern | Swing point identification | candidate, lower priority -- non-repainting unlike Zig Zag, could serve as an alternative/complementary swing-point confirmation to the existing wick gate |
| Williams Alligator | 3 smoothed MAs (jaw/teeth/lips) | Trend presence/absence via MA spread | not-a-fit, same trend-family exclusion, Bill Williams system with documented short-horizon weakness |
| Pivot Points Standard | Prior-period-derived support/resistance levels | Intraday level reference | candidate, lower priority -- could sharpen the invalidation/target price levels (not the entry signal itself), a distinct question from entry-signal selection |

## Statistical / composite / other

| Indicator | Measures | Typical use | v8 fit |
|---|---|---|---|
| Correlation Coefficient, Correlation - Log | Co-movement between two series (e.g. token vs BTC) | Diversification/regime context | candidate, lower priority -- could flag when a "reversal" is really just BTC beta, not token-specific signal; needs a reference series (BTC/ETH) wired in, real added complexity |
| 52 Week High/Low | Long-horizon price extremes | Position/valuation context | not-a-fit, wrong horizon entirely for a young/micro-cap token universe |
| Advance/Decline | Breadth across a basket of assets | Market-wide breadth | not-a-fit -- v8 trades single tokens, no natural basket to compute breadth over |
| Average Price, Median Price, Typical Price | Simple OHLC-derived price summaries | Building blocks for other indicators | not-a-fit standalone, these are inputs to other indicators, not signals themselves |
| Ratio | Price ratio between two series | Relative-strength comparison | not-a-fit, same missing-reference-series issue as Correlation |
| Spread | Difference between two series | Pairs-trading style comparison | not-a-fit, v8 isn't a pairs strategy |

## Explicitly out of reach (flagged in the earlier research pass, unchanged)

- **Wash-trading / liquidity-pool-inflation detection** (arXiv 2507.01963) -- strongest academic evidence found, directly on-theme with the "beat human psychology and detect bots" strategic axis, but requires NEW on-chain transaction-classification logic, not a TradingView indicator. Separate, bigger chantier than anything in this list.
- **Cumulative Volume Delta (CVD)** -- needs trade-by-trade buy/sell classification, not confirmed available in the current GeckoTerminal OHLCV pipeline. Blocked on a data-availability check, not a priority decision.
- **Pump-and-dump exclusion gate** (RSI>80 + volume+500%) -- not a new indicator, a new GATE composed from RSI+Volume already in this list; folds naturally into the Volume filter test above rather than standing alone.

## My picks, in test order (v8 is my own pocket, testing autonomously per the 05/08 mandate)

1. **Volume filter on the existing wick gate** (2x avg volume) -- cheapest, most direct, strongest documented evidence (71% vs 60% WR).
2. **ADX / Choppiness Index as a regime filter** -- zero new data collection, retroactively testable on the existing 32 trades right now.
3. **Fisher Transform** as an alternative/confirming turning-point signal alongside RSI divergence -- same reversal thesis, different math, worth a direct A/B.
4. **Connors RSI** as a sharper short-horizon mean-reversion signal, potential RSI replacement or confirmation layer.

Everything else above is banked (POTENTIAL doctrine, CLAUDE.md "Generative research") -- a real branch, not dug into now unless one of the top 4 proves inconclusive.
