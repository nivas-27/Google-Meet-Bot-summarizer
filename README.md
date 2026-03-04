# 🤖 MeetBot — Google Meet Automation Bot

A Python bot that silently joins Google Meet calls, captures live captions, summarises the meeting using Google Gemini AI, and emails the summary to you — fully automated.

Built specifically for **daily standup meetings** with a clean per-person update format.

---

## ✨ Features

- **Auto-join** Google Meet calls with mic and camera off
- **Persistent login** — log in once, reused forever via Chrome profile
- **Live caption scraping** — captures who said what with timestamps
- **AI summarisation** — uses Google Gemini to generate a standup-style summary per speaker
- **Auto email** — sends the summary to your inbox the moment the meeting ends
- **Saves transcript + summary** locally as `.txt` and `.md` files

---

## 📁 Project Structure

```
meetbot/
├── meet_bot.py             # Main bot (Phase 1 + Phase 2)
├── send_summary_mail.py    # Email sender (Phase 3)
├── .env                    # Your secrets (never commit this)
├── .gitignore
├── requirements.txt
├── chrome-profile/         # Auto-created on first login (never commit this)
└── transcripts/            # Auto-created — stores transcripts and summaries
```

---

## ⚙️ Setup

### 1. Clone the repo

```bash
git clone https://github.com/nivas-27/Google-Meet-Bot-summarizer.git
cd meetbot
```

### 2. Install dependencies

```bash
pip install selenium python-dotenv webdriver-manager google-genai
```

### 3. Create your `.env` file

```env
GMAIL_ID="YOUR GMAIL ID"
GMAIL_APP_PASSWORD="GMAIL_APP_PASSWORD"
RECEIVER_GMAIL_ID="RECEIVER_GMAIL_ID"
MEET_LINK="MEET_LINK"
GEMINI_API_KEY="YOUR_GEMINI_API_KEY"
```

### 4. Get your API keys

**Gemini API Key (free)**
1. Go to [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Click **Create API key**
3. Paste it into `.env` as `GEMINI_API_KEY`

**Gmail App Password**
1. Make sure **2-Step Verification** is ON for your Google account
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Click **Create** → name it `MeetBot`
4. Copy the 16-character password into `.env` as `GMAIL_APP_PASSWORD`

> ⚠️ If your email is a Google Workspace account (e.g. `@yourcompany.com`), your admin may need to enable App Passwords.

---

## 🚀 Usage

```bash
python meet_bot.py
```

**First run only:** Chrome will open and ask you to log into your Google account manually. Once done, press `ENTER` in the terminal. The session is saved and you will never be asked again.

**Every run after that:** The bot will:
1. Open Chrome with your saved session
2. Navigate to the Meet link in your `.env`
3. Mute mic and camera
4. Join the meeting
5. Enable captions automatically
6. Silently record everything said

**To leave the meeting:**

Press `Ctrl+C` in the terminal. The bot will:
1. Stop recording captions
2. Save the full transcript to `./transcripts/`
3. Send the transcript to Gemini for summarisation
4. Save the summary to `./transcripts/`
5. Email the summary to your inbox
6. Close the browser

---

## 📋 Sample Summary Output

```
**Jawaharsrinivas S**
- ✅ Done   : Completed the API integration for the post signal module
- 🔄 Doing  : Currently fixing the edge case in the webhook handler
- 🔜 Next   : Will move on to writing unit tests once the fix is done

**Priya R**
- ✅ Done   : Reviewed and merged two pull requests from yesterday
- 🔄 Doing  : Working on the dashboard UI redesign
- 🔜 Next   : Will share designs for review by EOD
```

---

## 🗂️ Output Files

All files are saved in the `./transcripts/` folder with a datetime-stamped name:

| File | Contents |
|------|----------|
| `meet_YYYY-MM-DD_HH-MM_transcript.txt` | Full raw transcript with speaker names and timestamps |
| `meet_YYYY-MM-DD_HH-MM_summary.md` | AI-generated standup summary in markdown |

---

## 🔧 Configuration

All configuration lives in `.env`. Supported keys:

| Key | Required | Description |
|-----|----------|-------------|
| `MEET_LINK` | ✅ | Full Google Meet URL to join |
| `GEMINI_API_KEY` | ✅ | Google AI Studio API key for summarisation |
| `GMAIL_APP_PASSWORD` | ✅ | Gmail App Password for sending the summary email |

---

## 🤖 Gemini Model Fallback

The bot tries Gemini models in this order based on free-tier quota availability. If one model hits its rate limit, it automatically moves to the next:

1. `gemini-2.5-flash-lite` — primary (10 RPM)
2. `gemini-3-flash-preview` — fallback (5 RPM)
3. `gemini-2.5-flash` — last resort (5 RPM)

---

## 🛡️ Security

- **Never commit `.env`** — it contains your API keys and app password
- **Never commit `chrome-profile/`** — it contains your Google login session
- Both are excluded via `.gitignore`

---

## 📦 Requirements

- Python 3.8+
- Google Chrome (any recent version)
- Windows / macOS / Linux

```
selenium
python-dotenv
webdriver-manager
google-genai
```

---

## 🙋 FAQ

**The bot joined but captions are not being captured.**
Make sure captions are visible on screen. The bot sends `Shift+C` to enable them. If your Meet language is not English, captions may need to be manually enabled once.

**Authentication error when sending email.**
Double-check that 2-Step Verification is ON and the App Password in `.env` is exactly the one generated at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).

**The summary shows "Not mentioned" for most fields.**
This usually means the captions were inaccurate or very short. Google Meet's auto-captions can miss words, especially with accents or background noise. The raw transcript is always saved so you can review it.

**Chrome is not closing after Ctrl+C on Windows.**
The bot uses `taskkill` as a fallback. If Chrome still remains open, you can manually close it — it does not affect the saved transcript or summary.

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

## 🙌 Contributing

Pull requests are welcome! If you find a bug or want to add a feature (Slack integration, Teams support, etc.), feel free to open an issue.
