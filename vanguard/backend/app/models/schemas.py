from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class SignalType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    WATCH = "watch"
    NEUTRAL = "neutral"


class Candle(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class TokenInfo(BaseModel):
    address: str
    name: str
    symbol: str


class TxnPeriod(BaseModel):
    buys: int = 0
    sells: int = 0


class PairTxns(BaseModel):
    m5: TxnPeriod | None = None
    h1: TxnPeriod | None = None
    h6: TxnPeriod | None = None
    h24: TxnPeriod | None = None


class PairSocial(BaseModel):
    platform: str = ""
    handle: str | None = None
    url: str | None = None


class PairSummary(BaseModel):
    chain_id: str
    dex_id: str
    pair_address: str
    url: str
    base_token: TokenInfo
    quote_token: TokenInfo
    price_usd: float | None = None
    price_native: str | None = None
    price_change_m5: float | None = None
    price_change_h1: float | None = None
    price_change_h6: float | None = None
    price_change_h24: float | None = None
    volume_m5: float | None = None
    volume_h1: float | None = None
    volume_h6: float | None = None
    volume_h24: float | None = None
    liquidity_usd: float | None = None
    liquidity_base: float | None = None
    liquidity_quote: float | None = None
    market_cap: float | None = None
    fdv: float | None = None
    pair_created_at: int | None = None
    labels: list[str] = Field(default_factory=list)
    txns: PairTxns | None = None
    boosts_active: int | None = None
    image_url: str | None = None
    websites: list[str] = Field(default_factory=list)
    socials: list[PairSocial] = Field(default_factory=list)


class DivergenceSignal(BaseModel):
    type: str
    indicator: str
    strength: float = Field(ge=0, le=1)
    description: str


class FibonacciLevel(BaseModel):
    level: float
    price: float
    label: str


class FibonacciAnalysis(BaseModel):
    swing_high: float
    swing_low: float
    trend: str
    levels: list[FibonacciLevel]


class IndicatorSnapshot(BaseModel):
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    ema_9: float | None = None
    ema_21: float | None = None
    ema_50: float | None = None
    sma_200: float | None = None
    atr: float | None = None
    volume_sma: float | None = None


class BuySignal(BaseModel):
    score: float = Field(ge=0, le=100)
    signal_type: SignalType
    reasons: list[str]
    entry_zone: tuple[float, float] | None = None
    stop_loss: float | None = None
    take_profit: list[float] = Field(default_factory=list)


class TimeframeAnalysis(BaseModel):
    timeframe: Timeframe
    indicators: IndicatorSnapshot
    divergences: list[DivergenceSignal]
    fibonacci: FibonacciAnalysis | None = None
    buy_signal: BuySignal
    support_levels: list[float] = Field(default_factory=list)
    resistance_levels: list[float] = Field(default_factory=list)


class PairAnalysis(BaseModel):
    pair: PairSummary
    analyzed_at: datetime
    timeframes: list[TimeframeAnalysis]
    global_score: float = Field(ge=0, le=100)
    trend_index: float = Field(default=0, ge=-100, le=100)
    consensus: SignalType
    summary: str


class Alert(BaseModel):
    id: str
    pair_address: str
    chain_id: str
    symbol: str
    signal_type: SignalType
    score: float
    timeframe: Timeframe
    message: str
    created_at: datetime


class WatchlistItem(BaseModel):
    id: str
    chain_id: str
    pair_address: str
    symbol: str
    added_at: datetime


class SearchResponse(BaseModel):
    query: str
    pairs: list[PairSummary]


class ChainDiscoverGroup(BaseModel):
    chain_id: str
    label: str
    pairs: list[PairSummary]


class DiscoverResponse(BaseModel):
    chains: list[ChainDiscoverGroup]


class MarketFeedResponse(BaseModel):
    feed: str
    chain_id: str | None = None
    pairs: list[PairSummary]
    total: int
    source: str = "live"


class PairIndexStats(BaseModel):
    total_pairs: int
    by_feed: dict[str, int]
    last_indexed_at: str | None = None
    chains: list[str]


class WebSocketMessage(BaseModel):
    type: str
    payload: dict[str, Any]