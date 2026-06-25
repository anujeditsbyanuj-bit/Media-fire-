# 🔥 Anuj Mediafire Downloader Bot

Powerful Telegram bot jo MediaFire files aur folders directly Telegram pe download karta hai.

---

## ⚙️ Environment Variables (Render / Replit Secrets)

| Key | Value |
|---|---|
| `BOT_TOKEN` | @BotFather se mila token |
| `API_ID` | my.telegram.org se |
| `API_HASH` | my.telegram.org se |
| `OWNER_ID` | Apna Telegram numeric ID |
| `MONGO_URI` | MongoDB Atlas connection URI |
| `MONGO_DB` | DB name (default: `mediafire_bot`) |
| `MONGO_COL` | Collection name (default: `users`) |
| `BOT_NAME` | Bot ka naam (optional) |
| `ADMIN_NAME` | Admin ka naam (optional) |
| `CHANNEL_USERNAME` | @YourChannel (optional) |
| `OWNER_USERNAME` | @YourUsername (optional) |

---

## 🚀 Deploy (Render)

1. GitHub pe push karo
2. Render → New Web Service → repo connect karo
3. **Start Command:** `python bot.py`
4. Environment variables upar wali table se set karo
5. Deploy!

---

## 📦 Features

| Feature | Free | Premium |
|---|---|---|
| Single file download | ✅ (max 500 MB) | ✅ (max 4 GB) |
| Folder download | ❌ | ✅ (all files) |
| Daily downloads | 7/day | Unlimited |
| Progress bar (speed + ETA) | ✅ | ✅ |
| File > 2GB split upload | ✅ | ✅ |
| Cancel anytime `/cancel` | ✅ | ✅ |
| Download history | ✅ | ✅ |

---

## 💬 User Commands

| Command | Description |
|---|---|
| `/start` | Welcome screen |
| `/help` | All commands |
| `/profile` | Your plan & stats |
| `/history` | Last 20 downloads |
| `/ping` | Bot latency |
| `/cancel` | Cancel active download |
| `/premium` | Premium plans |
| `/redeem KEY` | Activate premium key |

---

## 👑 Admin Commands (Owner only)

| Command | Description |
|---|---|
| `/stats` | Bot statistics |
| `/genkey 30` | 30-day key generate |
| `/genkey 7 5` | 5 keys of 7 days |
| `/listkeys` | Unused keys list |
| `/listkeys all` | All keys (used + unused) |
| `/delkey KEY` | Delete unused key |
| `/addpremium USER_ID` | Lifetime premium do |
| `/addpremium USER_ID 30` | 30-day premium do |
| `/revokepremium USER_ID` | Premium hatao |
| `/ban USER_ID` | User ban karo |
| `/unban USER_ID` | User unban karo |
| `/broadcast MESSAGE` | Sab users ko message |
| `/users` | User list (paginated) |

---

## 🔧 Config (config.py)

```python
FREE_DAILY_LIMIT    = 7        # Free user daily downloads
FREE_MAX_SIZE_MB    = 500      # Free user max file size
PREMIUM_MAX_SIZE_MB = 4096     # Premium max file size (4 GB)
MAX_CONCURRENT_DOWNLOADS = 3   # Simultaneous single-file downloads
SPLIT_SIZE_BYTES = 1.9 GB      # Split size for >2GB files
```

---

## ⚠️ Notes

- **Folder download** premium users ke liye hai — free users ko upgrade prompt milega
- **Double response fix** — har restart pe purani `.session` files auto-delete hoti hain
- **Render free tier** — 76+ files wale bade folders ke liye paid tier recommend hai (15 min timeout)
- **MongoDB Atlas** free tier kaam karta hai

---

## 📁 File Structure

```
xylon-bot/
├── bot.py                  # Main entry point
├── config.py               # Environment config
├── database.py             # MongoDB operations
├── mediafire_dl.py         # MediaFire API + downloader
├── utils.py                # Helper functions
├── requirements.txt        # Dependencies
└── handlers/
    ├── commands.py         # User commands
    ├── admin.py            # Admin commands
    └── downloader.py       # Download + upload logic
```

---

**Made with ❤️ — @anujedits76**
