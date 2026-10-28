# 🗞️ Telegram Real-Time & Daily News Bot

A free, reliable Telegram News Bot built in **Python 3.12** for **Windows 11**.

Collects news from 9 priority publisher RSS feeds, cleans and attributes summaries, classifies categories, and automatically publishes alerts to your Telegram channel as soon as they break.

---

## ⚡ Priority Sources Configured

| Priority | Source | Focus / Coverage | Tags |
|:---:|---|---|---|
| **P1** | **Reuters India** | National Breaking News & Economy | `#Breaking` `#National` `#India` |
| **P2** | **The Hindu** | Politics, Government, Education & Public Affairs | `#Politics` `#Government` `#National` |
| **P3** | **The Indian Express** | National News, Investigations & City News | `#National` `#Investigation` `#India` |
| **P4** | **Hindustan Times** | Mumbai & Regional News | `#Mumbai` `#Maharashtra` `#Regional` |
| **P5** | **Loksatta** | Marathi & Maharashtra News | `#Maharashtra` `#Marathi` `#Regional` |
| **P6** | **I Love Navi Mumbai** | Dedicated Local Reporting | `#NaviMumbai` `#LocalNews` |
| **P7** | **NMMC and CIDCO** | Official Civic & Infrastructure Notices | `#NaviMumbai` `#NMMC` `#CIDCO` `#Civic` |
| **P8** | **Akashvani News** | Public-Service & Regional Updates | `#Akashvani` `#National` `#PublicService` |

---

## 🚀 Quick Start (Windows 11)

### 1. Requirements & Virtual Environment
Open PowerShell or Command Prompt in the project folder:
```powershell
# Create virtual environment
python -m venv venv

# Activate virtual environment
.\venv\Scripts\activate

# Install free dependencies
pip install -r requirements.txt
```

### 2. Configure Credentials (`.env`)
Create a `.env` file from the template:
```powershell
cp .env.example .env
```
Open `.env` and fill in:
```ini
BOT_TOKEN=your_telegram_bot_token_here
CHANNEL_ID=@your_channel_username
```
> 🔒 **Security Notice:** `.env` and `logs/` are strictly excluded in `.gitignore`. The bot never logs or prints tokens in chat or terminal.

### 3. Verify Bot Setup
Run the setup diagnostic:
```powershell
python main.py setup
```
This validates:
1. Token validity via Telegram's `getMe` API.
2. Channel admin status & `can_post_messages` permission.
3. Interactive test message prompt.

### 4. Test All RSS Feeds
Test all 9 live priority sources:
```powershell
python main.py check-feeds
```

---

## 💻 Running the Bot

### Option A: Real-Time Watcher (Recommended for Live Desktop Use)
Polls all 9 priority sources every 60 seconds. New breaking news is posted to Telegram immediately:
```powershell
python main.py watch
```
*(Or double-click `run_watcher.bat`)*

### Option B: Single Poll Run
Performs a single check, publishes any new articles, and exits:
```powershell
python main.py run
```
*(Or double-click `run_bot.bat` — ideal for Windows Task Scheduler)*

### Option C: Health Diagnostics
Check polling statistics, cached article IDs, and live feed connectivity:
```powershell
python main.py health
```

---

## ⏰ Windows Task Scheduler Setup

To run the bot automatically in the background on Windows:

1. Press `Win + R`, type `taskschd.msc`, and press **Enter**.
2. Click **Create Task...** on the right side.
3. **General Tab:**
   * Name: `Telegram News Bot Poller`
   * Check: **Run whether user is logged on or not** (or Run only when user is logged on)
4. **Triggers Tab:**
   * Click **New...**
   * Begin the task: **On a schedule** $\to$ **Daily**
   * Under Advanced settings:
     * Check **Repeat task every:** `5 minutes` (or `1 minute` by typing `1 minute`)
     * For a duration of: **Indefinitely**
     * Check: **Stop task if it runs longer than:** `3 minutes`
5. **Actions Tab:**
   * Click **New...**
   * Action: **Start a program**
   * Program/script: Browse to `C:\Users\siddh\.gemini\antigravity\scratch\telegram-news-bot\run_bot.bat`
   * Start in: `C:\Users\siddh\.gemini\antigravity\scratch\telegram-news-bot`
6. **Conditions Tab:**
   * Check **Start only if the following network connection is available** $\to$ **Any connection**
   * Check **Wake the computer to run this task** (allows waking from Sleep if laptop is plugged in)
7. **Settings Tab:**
   * Check **Run task as soon as possible after a scheduled start is missed**
   * Check **If the running task does not end when requested, force it to stop**

> ⚠️ **Important Local Hardware Limitation:**  
> A local Windows script only executes when the laptop is **powered on** and connected to the internet. If the laptop is shut down, in deep hibernation, or battery-depleted, Windows cannot execute tasks.

---

## ☁️ Running 24/7 Even When Laptop Is Turned Off (100% Free)

If you need the bot to run **every 1 minute even when your laptop is completely powered off**, you can deploy this bot to **GitHub Actions** completely free with zero server costs:

1. Push this project folder to a private GitHub repository.
2. In your GitHub repository:
   * Go to **Settings** $\to$ **Secrets and variables** $\to$ **Actions**
   * Add Secret `BOT_TOKEN`: Your bot token
   * Add Secret `CHANNEL_ID`: `@news_8am`
3. A ready-to-use workflow is already provided in [`.github/workflows/poller.yml`](.github/workflows/poller.yml).
4. GitHub will automatically poll the feeds on cloud servers every 5 minutes 24/7, even when your laptop is off.

---

## 🛠️ Testing & Verification

Run the automated test suite with pytest:
```powershell
pytest -v
```
All 26 automated tests cover:
* UTC to IST conversion and midnight edge cases
* Headline Jaccard similarity deduplication
* Telegram MarkdownV2 special character escaping
* Message length limits and chunking
* State tracking and duplicate prevention
* Telegram retry logic with HTTP 429 rate limit backoff

---

## ❓ Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| `Chat not found` | The bot cannot locate the channel. | Ensure the channel is public or use numeric ID (e.g., `-100...`). Add bot as Administrator. |
| `Need administrator rights` | Bot lacks post permissions. | In Telegram channel: **Settings $\to$ Administrators $\to$ Add Bot** and check **Post Messages**. |
| `429 Too Many Requests` | Telegram rate limit hit. | The bot automatically handles 429 and sleeps for `retry_after` seconds. |
| No new articles posted | Articles already cached in `state.json`. | Run `python main.py health` to see cached IDs. On startup, existing articles are marked seen so only fresh articles post. |
