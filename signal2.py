import os, time, requests, pytz, argparse
from datetime import datetime
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()  # loads .env locally; ignored on Render (set env vars in dashboard)

# ---------------- Config & Defaults ----------------
TZ = pytz.timezone("America/Toronto")

def env_bool(name: str, default=False):
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "y"}

def get_args():
    p = argparse.ArgumentParser(description="Binary switch signal with Discord webhook")
    p.add_argument("--a", dest="ticker_a", default=os.getenv("TICKER_A", "VFV.TO"),
                   help="First ticker symbol (default from TICKER_A or VFV.TO)")
    p.add_argument("--b", dest="ticker_b", default=os.getenv("TICKER_B", "VEQT.TO"),
                   help="Second ticker symbol (default from TICKER_B or VEQT.TO)")
    p.add_argument("--label-a", default=os.getenv("LABEL_A", "A"),
                   help="Label for ticker A (for message readability)")
    p.add_argument("--label-b", default=os.getenv("LABEL_B", "B"),
                   help="Label for ticker B")
    p.add_argument("--holding", choices=["A", "B"],
                   default=os.getenv("CURRENT_HOLDING", "A").upper(),
                   help="Which asset you CURRENTLY hold (A or B). The rule will only switch away from this if the other wins.")
    p.add_argument("--delta-bps", type=float, default=float(os.getenv("DELTA_BPS", "7")),
                   help="Hysteresis threshold in basis points (default 7)")
    p.add_argument("--guard", action="store_true", default=env_bool("TIME_GUARD", False),
                   help="If set, only act near 09:31–09:40 America/Toronto")
    p.add_argument("--retries", type=int, default=int(os.getenv("OPEN_RETRIES", "8")),
                   help="Retries for fetching today's open (1m bars)")
    p.add_argument("--delay", type=int, default=int(os.getenv("OPEN_RETRY_DELAY", "10")),
                   help="Seconds between retries for open fetch")
    p.add_argument("--webhook", default=os.getenv("DISCORD_WEBHOOK_URL"),
                   help="Discord webhook URL (or set DISCORD_WEBHOOK_URL)")
    return p.parse_args()

# ---------------- Data helpers ----------------
def prev_close(ticker: str) -> float:
    """
    Get the most recent daily close (yesterday's close) for the ticker.
    """
    df = yf.download(ticker, period="5d", interval="1d", auto_adjust=False, progress=False)
    if df.empty or "Close" not in df.columns:
        raise RuntimeError(f"No daily close data for {ticker}")
    return float(df["Close"].iloc[-1])

def today_open(ticker: str, retries=8, delay=10) -> float:
    """
    Get today's 'open' using the first 1-minute bar's Open after the market opens.
    Retries a few times because the bar may not be available exactly at 09:30.
    """
    for _ in range(retries):
        df = yf.download(ticker, period="1d", interval="1m", auto_adjust=False, progress=False)
        if not df.empty and "Open" in df.columns:
            return float(df["Open"].iloc[0])   # first minute bar ~ session open
        time.sleep(delay)
    raise RuntimeError(f"Open not available yet for {ticker}")

# ---------------- Discord ----------------
def post_discord(webhook_url: str, content: str):
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL not set (or --webhook not provided)")
    r = requests.post(webhook_url, json={"content": content}, timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"Discord webhook error {r.status_code}: {r.text}")

# ---------------- Main logic ----------------
def main():
    args = get_args()
    now = datetime.now(TZ)

    # Optional time guard near TSX open to avoid noise outside the window
    if args.guard:
        if not (now.hour == 9 and 31 <= now.minute <= 40):
            # Quiet exit outside window
            return

    # Fetch yesterday's closes & today's opens
    cA, cB = prev_close(args.ticker_a), prev_close(args.ticker_b)
    oA = today_open(args.ticker_a, retries=args.retries, delay=args.delay)
    oB = today_open(args.ticker_b, retries=args.retries, delay=args.delay)

    # Overnight returns (prev close -> today's open)
    rA = (oA - cA) / cA
    rB = (oB - cB) / cB
    edge_bps = (rB - rA) * 1e4  # positive => B stronger than A

    # EXACT STRATEGY:
    # - If holding A, switch to B ONLY if rB - rA > DELTA_BPS.
    # - If holding B, switch to A ONLY if rA - rB > DELTA_BPS.
    delta = args.delta_bps
    holding = args.holding.upper()

    if holding == "A":
        if edge_bps > delta:
            action = f"Switch to **{args.label_b}** (Sell {args.label_a} @ open; Buy {args.label_b})"
        else:
            action = f"Hold **{args.label_a}** (inside hysteresis or not superior)"
    else:  # holding == "B"
        if (-edge_bps) > delta:  # rA - rB > delta
            action = f"Switch to **{args.label_a}** (Sell {args.label_b} @ open; Buy {args.label_a})"
        else:
            action = f"Hold **{args.label_b}** (inside hysteresis or not superior)"

    msg = (
        f"**Binary Switch Signal** — {now.strftime('%Y-%m-%d %H:%M %Z')}\n"
        f"{args.label_a} ({args.ticker_a}) close/open: {cA:.2f} / {oA:.2f}  rA={rA*100:.2f}%\n"
        f"{args.label_b} ({args.ticker_b}) close/open: {cB:.2f} / {oB:.2f}  rB={rB*100:.2f}%\n"
        f"Edge (rB - rA): {edge_bps:.1f} bps   |   Holding: {holding}\n"
        f"Δ threshold: {delta:.1f} bps\n"
        f"**Action**: {action}"
    )
    print(msg)
    post_discord(args.webhook, msg)

if __name__ == "__main__":
    main()
