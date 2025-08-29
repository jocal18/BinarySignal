# Binary Switch Signal Bot (Discord Webhook)

Automated daily **binary switch signal** between two tickers with alerts to **Discord** via webhook.  
Designed for Canadian market hours (America/Toronto). Runs after the TSX/TSX-V open, compares **overnight returns**, and tells you whether to **switch** or **hold** based on a hysteresis threshold (in **basis points**) to avoid flip-flopping.

---

## 🔍 What it does (strategy)

At each trading day *t*:

- Compute **overnight return** for each asset $i \in \lbrace A, B \rbrace$: 


$$
r^{i}_t=\frac{\text{Open}^{i} _t-\text{Close}^{i} _{t-1}}{\text{Close}^{i} _{t-1}}
$$

- Define the **edge** as:

$$
\text{edge}_t = r^{(B)}_t - r^{(A)}_t
$$

(in **bps** when multiplied by $10^{4}$).

- If you **currently hold A**, **switch to B** only if:

$$
\text{edge}_t > \Delta
$$

- If you **currently hold B**, **switch to A** only if:

$$
-r^{(B)}_t + r^{(A)}_t > \Delta
$$

(equivalently $r^{(A)}_t-r^{(B)}_t > \Delta$

- Otherwise: **Hold** (inside hysteresis).

- You can backtest the strategy using StockBinaryComparison.py
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

---

## ⚙️ Configuration

### Environment file (`.env`)

Create `.env` next to `signal2.py`:

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
```
To have it run on a schedule on a Mac: 
- Create folder: ~/binary-signal-bot, add signal.py, .env, and a venv.
- copy the  LaunchAgent in ~/Library/LaunchAgents/
- Make sure your Mac doesn't go to sleep
- Modify the .env accordingly to what you want


## ⏰ The Time Guard

The **time guard** is a safety check inside the bot.  

- Even if the LaunchAgent runs at a certain time, the bot will only **act** if the local time in `America/Toronto` is within the guard window (default: **09:40–09:50**).  
- If the current time is outside that window, the script exits silently.

### Why it matters
- **Correct data**: ensures the "open" price is actually available before computing signals.  
- **Avoid spam**: if you schedule multiple runs (e.g. 09:45 and 09:50), only the valid one posts.  
- **DST-proof**: you don’t need to change your cron job when Toronto switches between EST/EDT. The guard enforces local time.

### Why it’s in `.env`
The guard is optional and configurable. In `.env`:

```ini
TIME_GUARD=1   # enforce 09:40–09:50 Toronto
TIME_GUARD=0   # disable guard (bot runs whenever cron triggers)
```
