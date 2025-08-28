"""
VFV.TO vs SU.TO Switching Strategy (Robust & Fixed)
---------------------------------------------------
- Rule: Compare *overnight returns* r_i(t) = (Open_t - Close_{t-1}) / Close_{t-1}.
- If the other asset beats the current by > hysteresis (bps), SWITCH at the OPEN.
- Mark portfolio to CLOSE each day.

Key fixes:
- Duplicate-safe index handling (drop duplicate timestamps).
- Passive equity is computed *vectorized* as a 1-D Series (no per-iteration lookup).
- Safe scalar getter for active loop to avoid Series->float errors.
"""

import argparse
from dataclasses import dataclass
import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt


# ============================== Defaults ======================================

DEFAULTS = dict(
    start_date="2024-01-01",
    end_date="2025-08-01",
    ticker1="VEQT.TO",          # 
    ticker2="SU.TO",            #
    shares1_init=500,          # start long asset 1
    shares2_init=0,
    cash_init=0.0,
    hysteresis_bps=7.0,        # require at least 7 bps edge to switch
    cooldown_days=7,           # min calendar days between switches
    fee_bps=0.0,               # commission bps per leg
    slippage_bps=0.0,          # slippage bps on execution price
    export_csv="",             # path to export daily results ("" to skip)
    plot=True
)

# ============================== Data helpers ==================================

def col1d(frame: pd.DataFrame, col: str) -> pd.Series:
    """
    Return a 1-D float Series for frame[col] even if it's (n,1) or already a Series.
    Keeps the original index.
    """
    arr = np.asarray(frame[col])   # handles Series or DataFrame column
    arr = arr.ravel()              # force 1-D
    return pd.Series(arr, index=frame.index, name=col, dtype="float64")

def fetch_data(ticker: str, label: str, start: str, end: str) -> pd.DataFrame:
    """
    Download OHLC from Yahoo and rename Open/Close to Open_<label>, Close_<label>.
    """
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(f"No data for {ticker}. Check symbol/date range/network.")
    df = df.rename(columns={"Open": f"Open_{label}", "Close": f"Close_{label}"})
    # keep only the columns we need
    df = df[[f"Open_{label}", f"Close_{label}"]].sort_index()
    return df


def align_two(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """
    Inner-join by date, sort, drop NaNs, and remove duplicate timestamps (keep first).
    This avoids Series->float conversion errors later.
    """
    combined = left.join(right, how="inner").sort_index().dropna()
    if not combined.index.is_unique:
        combined = combined[~combined.index.duplicated(keep="first")]
    return combined


def sget(series: pd.Series, dt) -> float:
    """
    Safe scalar getter: returns a float even if the index has duplicates.
    If multiple rows exist at dt, take the first (or replace with .mean()).
    """
    val = series.loc[dt]
    if isinstance(val, pd.Series):
        val = val.iloc[0]
    return float(val)


# ============================== Execution model ================================

@dataclass
class TradeParams:
    hysteresis_bps: float = DEFAULTS["hysteresis_bps"]
    cooldown_days: int = DEFAULTS["cooldown_days"]
    fee_bps: float = DEFAULTS["fee_bps"]
    slippage_bps: float = DEFAULTS["slippage_bps"]


def exec_price(open_px: float, slippage_bps: float, side: str) -> float:
    """
    Apply slippage to execution price:
      buy @ open * (1 + s), sell @ open * (1 - s)
    """
    s = slippage_bps / 1e4
    if side.lower() == "buy":
        return open_px * (1 + s)
    return open_px * (1 - s)


def fee_on_notional(notional: float, fee_bps: float) -> float:
    """
    Commission/fee as bps of notional (per leg).
    """
    return abs(notional) * (fee_bps / 1e4)


# ============================== Analytics ======================================

def annualize_factor(freq="D"):
    if freq == "D":
        return np.sqrt(252)
    if freq == "W":
        return np.sqrt(52)
    if freq == "M":
        return np.sqrt(12)
    return np.sqrt(252)


def compute_metrics(equity_curve: pd.Series, rf=0.0):
    """
    Basic performance metrics from an equity curve marked to close:
    - CAGR, annualized volatility, Sharpe (excess, rf≈0), Max drawdown
    """
    px = equity_curve.dropna()
    rets = px.pct_change().dropna()

    # CAGR
    if len(px) >= 2:
        n_years = (px.index[-1] - px.index[0]).days / 365.25
        cagr = (px.iloc[-1] / px.iloc[0]) ** (1 / n_years) - 1 if n_years > 0 else np.nan
    else:
        cagr = np.nan

    # Vol (ann)
    ann = annualize_factor("D")
    vol = rets.std() * ann if len(rets) else np.nan

    # Sharpe
    sharpe = (rets.mean() * 252 - rf) / vol if (vol and vol > 0) else np.nan

    # Max Drawdown
    rolling_max = px.cummax()
    dd = px / rolling_max - 1.0
    max_dd = dd.min() if len(dd) else np.nan

    return {"CAGR": cagr, "Volatility_ann": vol, "Sharpe": sharpe, "MaxDrawdown": max_dd}


def pretty_metrics(m):
    try:
        return (f"CAGR: {m['CAGR']*100:6.2f}%  |  "
                f"Vol (ann): {m['Volatility_ann']*100:6.2f}%  |  "
                f"Sharpe: {m['Sharpe']:5.2f}  |  "
                f"Max DD: {m['MaxDrawdown']*100:6.2f}%")
    except Exception:
        return "Insufficient data for metrics."


# ============================== Strategy core ==================================

def backtest_switching(
    data: pd.DataFrame,
    shares1_init: int,
    shares2_init: int,
    cash_init: float,
    params: TradeParams,
    label1="Stock1",
    label2="Stock2",
):
    """
    Compare overnight returns:
      r_i(t) = [Open_i(t) - Close_i(t-1)] / Close_i(t-1)
    If r_other - r_current > hysteresis_bps -> switch at OPEN.

    Mark-to-close equity each day. Track switches, hit rate, turnover.
    """

    idx = data.index

    # Pull open/close as 1-D Series (guard against accidental 2-D)
    o1 = col1d(data, f"Open_{label1}")
    c1 = col1d(data, f"Close_{label1}")
    o2 = col1d(data, f"Open_{label2}")
    c2 = col1d(data, f"Close_{label2}")


    # ---- PASSIVE EQUITY (vectorized, 1-D Series) ----
    passive_equity_series = (shares1_init * c1 + shares2_init * c2 + float(cash_init))
    # ensure unique index
    if not passive_equity_series.index.is_unique:
        passive_equity_series = passive_equity_series[~passive_equity_series.index.duplicated(keep="first")]
    # align to idx (active dates)
    passive_equity_series = passive_equity_series.reindex(idx).astype(float)
    passive_equity_series.name = "Equity_Passive"

    # ---- ACTIVE LOOP ----
    shares1, shares2 = shares1_init, shares2_init
    cash = float(cash_init)
    holding1 = shares1_init > 0
    last_switch_date = None

    eq_active = []
    pos_flag = []
    switches = 0
    turnover_notional = 0.0
    switch_hits = []

    for i, dt in enumerate(idx):
        open1 = sget(o1, dt)
        close1 = sget(c1, dt)
        open2 = sget(o2, dt)
        close2 = sget(c2, dt)

        if i == 0:
            r1 = r2 = 0.0
            edge_bps = 0.0
        else:
            prev_dt = idx[i - 1]
            prev_close1 = sget(c1, prev_dt)
            prev_close2 = sget(c2, prev_dt)
            r1 = (open1 - prev_close1) / prev_close1
            r2 = (open2 - prev_close2) / prev_close2
            edge_bps = (r2 - r1) * 1e4  # positive -> favor asset 2

        # Decide if we switch (hysteresis + cooldown)
        do_switch = False
        target_is_2 = False
        cooldown_ok = (last_switch_date is None) or ((dt - last_switch_date).days >= params.cooldown_days)

        if i > 0 and cooldown_ok:
            if holding1 and (edge_bps > params.hysteresis_bps):
                do_switch, target_is_2 = True, True
            elif (not holding1) and (edge_bps < -params.hysteresis_bps):
                do_switch, target_is_2 = True, False

        if do_switch:
            # SELL current holding at open with slippage; pay fee on notional
            if holding1:
                sell_px = exec_price(open1, params.slippage_bps, side="sell")
                notional = shares1 * sell_px
                cash += notional
                cash -= fee_on_notional(notional, params.fee_bps)
                turnover_notional += abs(notional)
                shares1 = 0
            else:
                sell_px = exec_price(open2, params.slippage_bps, side="sell")
                notional = shares2 * sell_px
                cash += notional
                cash -= fee_on_notional(notional, params.fee_bps)
                turnover_notional += abs(notional)
                shares2 = 0

            # BUY target with all cash (integer shares); pay fee
            if target_is_2:
                buy_px = exec_price(open2, params.slippage_bps, side="buy")
                new_shares = int(cash // buy_px)
                notional = new_shares * buy_px
                shares2 += new_shares
                cash -= notional
                cash -= fee_on_notional(notional, params.fee_bps)
                turnover_notional += abs(notional)
                holding1 = False
            else:
                buy_px = exec_price(open1, params.slippage_bps, side="buy")
                new_shares = int(cash // buy_px)
                notional = new_shares * buy_px
                shares1 += new_shares
                cash -= notional
                cash -= fee_on_notional(notional, params.fee_bps)
                turnover_notional += abs(notional)
                holding1 = True

            last_switch_date = dt
            switches += 1

            # Simple "hit" proxy by close
            if target_is_2 and i > 0:
                switch_hits.append((close2 / close1) > 1.0)
            elif (not target_is_2) and i > 0:
                switch_hits.append((close1 / close2) > 1.0)

        # Mark-to-close equity
        equity_today = shares1 * close1 + shares2 * close2 + cash
        eq_active.append(equity_today)
        pos_flag.append(1 if holding1 else -1)

    # Build results DataFrame (all 1-D, aligned)
    results = pd.DataFrame({
        "Equity_Active": pd.Series(eq_active, index=idx, dtype="float64"),
        "Equity_Passive": passive_equity_series,
        "PositionFlag": pd.Series(pos_flag, index=idx, dtype="int8"),
    })

    stats = {
        "Switches": switches,
        "Turnover_Notional": turnover_notional,
        "HitRate": float(np.mean(switch_hits)) if len(switch_hits) else np.nan,
    }
    return results, stats


# ============================== Plotting =======================================

def plot_results(df: pd.DataFrame, label1="Stock1", label2="Stock2"):
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1]})

    ax = axes[0]
    df["Equity_Active"].plot(ax=ax, label="Active (switching)", lw=2)
    df["Equity_Passive"].plot(ax=ax, label="Passive (buy&hold)", lw=2, alpha=0.85)
    ax.set_title("Equity Curves (Marked to Close)")
    ax.set_ylabel("Portfolio Value (CAD)")
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax2 = axes[1]
    df["PositionFlag"].plot(ax=ax2, drawstyle="steps-post")
    ax2.set_title(f"Position: +1 = {label1}, -1 = {label2}")
    ax2.set_ylim(-1.5, 1.5)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()


# ============================== Main ===========================================

def main():
    parser = argparse.ArgumentParser(description="VFV vs SU Switching Backtest (Robust & Fixed)")
    parser.add_argument("--start", default=DEFAULTS["start_date"])
    parser.add_argument("--end", default=DEFAULTS["end_date"])
    parser.add_argument("--ticker1", default=DEFAULTS["ticker1"])
    parser.add_argument("--ticker2", default=DEFAULTS["ticker2"])
    parser.add_argument("--shares1", type=int, default=DEFAULTS["shares1_init"])
    parser.add_argument("--shares2", type=int, default=DEFAULTS["shares2_init"])
    parser.add_argument("--cash", type=float, default=DEFAULTS["cash_init"])
    parser.add_argument("--hysteresis_bps", type=float, default=DEFAULTS["hysteresis_bps"])
    parser.add_argument("--cooldown", type=int, default=DEFAULTS["cooldown_days"])
    parser.add_argument("--fee_bps", type=float, default=DEFAULTS["fee_bps"])
    parser.add_argument("--slippage_bps", type=float, default=DEFAULTS["slippage_bps"])
    parser.add_argument("--export_csv", default=DEFAULTS["export_csv"])
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()  # in notebooks, use parser.parse_args([])

    label1, label2 = "Stock1", "Stock2"

    # Load & align data (duplicate-safe)
    d1 = fetch_data(args.ticker1, label1, args.start, args.end)
    d2 = fetch_data(args.ticker2, label2, args.start, args.end)
    data = align_two(d1, d2)

    # Prepare execution & risk control parameters
    params = TradeParams(
        hysteresis_bps=args.hysteresis_bps,
        cooldown_days=args.cooldown,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
    )

    # Run backtest
    results, stats = backtest_switching(
        data=data,
        shares1_init=args.shares1,
        shares2_init=args.shares2,
        cash_init=args.cash,
        params=params,
        label1=label1,
        label2=label2,
    )

    # Metrics
    m_active = compute_metrics(results["Equity_Active"])
    m_passive = compute_metrics(results["Equity_Passive"])

    # Summary
    print("\n=== Parameters ===")
    print(f"Dates: {args.start} → {args.end}")
    print(f"Tickers: {args.ticker1} vs {args.ticker2}")
    print(f"Hysteresis: {args.hysteresis_bps} bps | Cooldown: {args.cooldown} days | "
          f"Fees: {args.fee_bps} bps | Slippage: {args.slippage_bps} bps")

    print("\n=== Performance (Active) ===")
    print(pretty_metrics(m_active))
    print("=== Performance (Passive) ===")
    print(pretty_metrics(m_passive))

    print("\n=== Trading Stats ===")
    print(f"Switches: {stats['Switches']}")
    if pd.notna(stats["HitRate"]):
        print(f"Hit Rate (close-after-switch): {stats['HitRate']*100:.1f}%")
    else:
        print("Hit Rate: n/a")
    print(f"Turnover Notional (approx): ${stats['Turnover_Notional']:,.0f}")

    # Export (optional)
    if args.export_csv:
        results.to_csv(args.export_csv, index=True)
        print(f"\nSaved daily results → {args.export_csv}")

    # Plot
    if DEFAULTS["plot"] and not args.no_plot:
        plot_results(results, label1=label1, label2=label2)


if __name__ == "__main__":
    main()
