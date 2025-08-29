# Binary Switch Signal Bot (Discord Webhook)

Automated daily **binary switch signal** between two tickers with alerts to **Discord** via webhook.  
Designed for Canadian market hours (America/Toronto). Runs after the TSX/TSX-V open, compares **overnight returns**, and tells you whether to **switch** or **hold** based on a hysteresis threshold (in **basis points**) to avoid flip-flopping.

---

## 🔍 What it does (strategy)

At each trading day *t*:

- Compute **overnight return** for each asset *i ∈ {A, B}*:
  
  \[ r^{(i)}_t = \frac{Open^{(i)}_t - Close^{(i)}_{t-1}}{Close^{(i)}_{t-1}} \]

- Define the **edge** as \( r^{(B)}_t - r^{(A)}_t \) (in **bps** when multiplied by \(10^4\)).
- If you **currently hold A**, **switch to B** only if edge \(>\ \Delta\) (hysteresis threshold).
- If you **currently hold B**, **switch to A** only if \(-\)edge \(>\ \Delta\) (equivalently \( r^{(A)}_t - r^{(B)}_t > \Delta\)).
- Otherwise: **Hold** (message will say “inside hysteresis”).

This is exactly what the bot computes and posts to your Discord channel.

---

## ✨ Features

- ✅ Any two tickers (e.g., `VFV.TO` vs `VEQT.TO`, or `SU.TO` vs `VEQT.TO`).
- ✅ Explicit **current holding** (`A` or `B`) so the bot only recommends switching **away** from what you hold.
- ✅ **Hysteresis** (`Δ` in bps) to reduce churn.
- ✅ **Time guard**: only act between **09:40–09:50 America/Toronto** (configurable).
- ✅ Robust open fetching with retries (minute bars can lag a bit at the open).
- ✅ **Discord Webhook** transport (simple and reliable).
- ✅ Works locally, on **Render** (cron), or a **24/7 Mac** with `launchd`.

---

## 📦 Repository contents

- `signal.py` – the signal generator & Discord notifier.
- `requirements.txt` – Python dependencies.
- `.env.example` – template for local environment variables (copy to `.env`).

> **Note:** Do **not** commit your real `.env` with secrets.

---

## ⚙️ Configuration

### Option A — Environment file (`.env`)

Create `.env` next to `signal.py`:

```ini
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/XXX/YYY
TICKER_A=VFV.TO
TICKER_B=VEQT.TO
LABEL_A=VFV
LABEL_B=VEQT
CURRENT_HOLDING=A
DELTA_BPS=7
TIME_GUARD=1
OPEN_RETRIES=8
OPEN_RETRY_DELAY=10
