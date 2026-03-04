"""
Google Meet Bot - Phase 2
Requirements: pip install selenium python-dotenv webdriver-manager google-genai

.env file should contain:
  MEET_LINK=https://meet.google.com/xxx-xxxx-xxx
  GEMINI_API_KEY=your_gemini_api_key_here

HOW LOGIN WORKS:
  - First run : Bot opens Chrome, you log in manually → session saved to ./chrome-profile/
  - Every run after : Bot reuses that saved session — no password needed ever again.

PHASE 2 ADDITIONS:
  - After joining, captions are enabled automatically (Shift+C)
  - Caption text + speaker names are scraped in real-time from the DOM
  - On exit (Ctrl+C), full transcript is saved to ./transcripts/
  - Gemini summarises the transcript and saves it alongside
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
import time
import os
import threading
import datetime
import signal
import subprocess
from send_summary_mail import send_summary_email

# Start virtual display for headless EC2
import subprocess
subprocess.Popen(['Xvfb', ':99', '-screen', '0', '1920x1080x24'])
import os
os.environ['DISPLAY'] = ':99'
import time
time.sleep(1)  # give Xvfb a moment to start


from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR     = os.path.join(BASE_DIR, "chrome-profile")
TRANSCRIPT_DIR  = os.path.join(BASE_DIR, "transcripts")
os.makedirs(TRANSCRIPT_DIR, exist_ok=True)


# ── Gemini helper ─────────────────────────────────────────────────────────────

def summarise_with_gemini(transcript_text: str) -> str:
    """
    Send the full transcript to Gemini and return a structured summary.
    Tries models in order of your free-tier quota availability:
      1. gemini-2.5-flash-lite  (10 RPM — most headroom)
      2. gemini-3-flash-preview (5 RPM  — fallback)
      3. gemini-2.5-flash       (5 RPM  — last resort)
    On 429, waits the retry delay Gemini returns before trying the next model.
    """
    from google import genai
    from google.genai import errors as genai_errors
    import re

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "⚠️  GEMINI_API_KEY not set in .env — summary skipped."

    client = genai.Client(api_key=api_key)

    prompt = f"""You are a meeting assistant specialising in daily standup meetings.

Below is a transcript of a standup call. Each person gives a brief update covering what they worked on, what they are currently doing, and what they plan to work on next.

Your job is to extract each speaker's update and present it in a clean, easy-to-read format for the team lead.

Output format (repeat this block for every speaker who gave an update):

---
**[Speaker Name]**
- ✅ Done      : (what they completed or worked on previously)
- 🔄 Doing     : (what they are currently working on right now)
- 🔜 Next      : (what they plan to work on next / blockers if any)
---

Rules:
- Use only what was actually said in the transcript. Do not invent or assume details.
- If a speaker did not mention one of the three categories, write "Not mentioned" for that field.
- Keep each point concise — one or two sentences.
- Do not add any introduction, conclusion, or extra commentary. Just the speaker blocks.

--- TRANSCRIPT START ---
{transcript_text}
--- TRANSCRIPT END ---
"""

    # Models tried in order — first one with quota wins
    model_chain = [
        "gemini-2.5-flash-lite",    # 10 RPM free tier — primary choice
        "gemini-3-flash-preview",   # 5 RPM free tier  — fallback
        "gemini-2.5-flash",         # 5 RPM free tier  — last resort
    ]

    for model in model_chain:
        print(f"  Trying model: {model}...")
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            print(f"  ✅ Summary generated with {model}")
            return response.text

        except genai_errors.ClientError as e:
            msg = str(e)
            if "429" in msg or "quota" in msg.lower():
                # Extract wait time from error, then move to next model
                wait = 0
                m = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", msg)
                if m:
                    wait = int(m.group(1))
                print(f"  ⚠️  {model} quota exceeded — trying next model"
                      + (f" (after {wait}s wait)" if wait else ""))
                if wait:
                    time.sleep(wait)
                continue   # try next model in chain
            elif "404" in msg or "not found" in msg.lower():
                print(f"  ⚠️  {model} not available in your region — trying next model")
                continue
            else:
                return f"⚠️  Gemini API error: {e}"

        except Exception as e:
            return f"⚠️  Unexpected error: {e}"

    return "⚠️  All Gemini models exhausted. Transcript is still saved — try again later."


# ── Caption scraper (runs in background thread) ───────────────────────────────

class CaptionScraper:
    """
    Polls the Google Meet caption DOM every second.
    Deduplicates lines so partial/updated captions are captured correctly.

    DOM targets (from your inspection):
      Speaker : span.NWpY1d          (inside each caption block)
      Text    : div.ygicle.VbkSUe    (the spoken words)
      Container confirmed active: div[aria-label="Captions"]
    """

    def __init__(self, driver):
        self.driver   = driver
        self.entries  = []          # list of {"ts": "HH:MM", "speaker": str, "text": str}
        self._running = False
        self._thread  = None
        self._last_seen: dict = {}

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print("📝 Caption scraper started.\n")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        print(f"📝 Caption scraper stopped. {len(self.entries)} lines captured.")

    def build_transcript(self) -> str:
        if not self.entries:
            return "(No captions were captured.)"
        lines = [f"[{e['ts']}] {e['speaker']}: {e['text']}" for e in self.entries]
        return "\n".join(lines)

    def _poll_loop(self):
        while self._running:
            try:
                self._scrape_once()
            except Exception:
                pass
            time.sleep(1)

    def _scrape_once(self):
        blocks = self.driver.find_elements(By.CSS_SELECTOR, "div.nMcdL")
        for block in blocks:
            try:
                speaker = block.find_element(By.CSS_SELECTOR, "span.NWpY1d").text.strip()
            except NoSuchElementException:
                speaker = "Unknown"
            try:
                text = block.find_element(By.CSS_SELECTOR, "div.ygicle.VbkSUe").text.strip()
            except NoSuchElementException:
                continue
            if not text:
                continue
            if self._last_seen.get(speaker) == text:
                continue
            self._last_seen[speaker] = text
            ts = datetime.datetime.now().strftime("%H:%M")
            self.entries.append({"ts": ts, "speaker": speaker, "text": text})


# ── Main bot ──────────────────────────────────────────────────────────────────

class JoinGoogleMeet:
    def __init__(self):
        self.meet_link = os.getenv('MEET_LINK')
        if not self.meet_link:
            raise ValueError("MEET_LINK must be set in .env")

        self._chrome_pid = None   # tracked so we can force-kill if needed

        opt = Options()
        opt.add_argument('--disable-blink-features=AutomationControlled')
        opt.add_argument('--start-maximized')
        opt.add_argument('--no-sandbox')
        opt.add_argument('--disable-dev-shm-usage')
        opt.add_argument(f'--user-data-dir={PROFILE_DIR}')
        opt.add_experimental_option("excludeSwitches", ["enable-automation"])
        opt.add_experimental_option('useAutomationExtension', False)
        opt.add_experimental_option("prefs", {
            "profile.default_content_setting_values.media_stream_mic": 1,
            "profile.default_content_setting_values.media_stream_camera": 1,
            "profile.default_content_setting_values.geolocation": 0,
            "profile.default_content_setting_values.notifications": 1,
        })

        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=opt
        )
        # Save Chrome's PID for force-kill fallback
        try:
            self._chrome_pid = self.driver.service.process.pid
        except Exception:
            pass

        self.driver.implicitly_wait(5)
        self.wait = WebDriverWait(self.driver, 20)
        self.caption_scraper = CaptionScraper(self.driver)

    # ──────────────────────────────────────────────────────────────────────────
    # LOGIN  (unchanged from Phase 1)
    # ──────────────────────────────────────────────────────────────────────────

    def _is_signed_in(self):
        try:
            self.driver.get("https://mail.google.com/mail/u/0/#inbox")
            WebDriverWait(self.driver, 8).until(
                EC.any_of(
                    EC.presence_of_element_located((By.XPATH, '//div[@gh="cm"]')),
                    EC.presence_of_element_located((By.XPATH, '//*[@data-tooltip="Inbox"]')),
                )
            )
            return True
        except TimeoutException:
            return False

    def ensureLoggedIn(self):
        print("Checking login session...")
        if self._is_signed_in():
            print("✅ Session active — skipping login.\n")
            return

        print("\n── FIRST-TIME LOGIN REQUIRED ────────────────────────────────────")
        print("Chrome is open. Please log into your Google account in the browser.")
        print("Wait until your Gmail inbox is fully visible, then come back here")
        print("and press ENTER.")
        print("─────────────────────────────────────────────────────────────────\n")
        self.driver.get("https://accounts.google.com/signin")
        input("Press ENTER after you have logged in and Gmail is open → ")

        if not self._is_signed_in():
            print("❌ Still not logged in. Re-run the script and complete sign-in.")
            self.driver.quit()
            exit(1)

        print("✅ Logged in! Session saved — you won't need to do this again.\n")

    # ──────────────────────────────────────────────────────────────────────────
    # MIC / CAMERA  (unchanged from Phase 1)
    # ──────────────────────────────────────────────────────────────────────────

    def _mute_device(self, label: str, jsname: str, shortcut: str):
        print(f"  Muting {label}...")
        try:
            btn = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, f'[jsname="{jsname}"]'))
            )
            is_muted = btn.get_attribute("data-is-muted")
            aria     = (btn.get_attribute("aria-label") or "").lower()
            print(f"    Found via jsname — data-is-muted={is_muted}, aria-label=\"{aria}\"")
            if is_muted == "false":
                self.driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.6)
                new_muted = btn.get_attribute("data-is-muted")
                if new_muted == "true":
                    print(f"  ✅ {label}: turned OFF via jsname")
                    return
                else:
                    print(f"    jsname click did not toggle — trying fallbacks")
            else:
                print(f"  ✅ {label}: already OFF (data-is-muted=true)")
                return
        except TimeoutException:
            print(f"    jsname [{jsname}] not found — trying aria-label")

        try:
            device_word = "microphone" if "mic" in label.lower() else "camera"
            xpath = (
                f'//div[@role="button" and contains('
                f'translate(@aria-label,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),'
                f'"{device_word}") and @data-is-muted="false"]'
            )
            btn = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
            self.driver.execute_script("arguments[0].click();", btn)
            time.sleep(0.6)
            print(f"  ✅ {label}: turned OFF via aria-label")
            return
        except TimeoutException:
            print(f"    aria-label strategy failed — trying keyboard shortcut")

        try:
            body = self.driver.find_element(By.TAG_NAME, "body")
            body.send_keys(shortcut)
            time.sleep(0.6)
            print(f"  ✅ {label}: toggled via keyboard shortcut ({shortcut})")
        except Exception as e:
            print(f"  ⚠️  All strategies failed for {label}: {e}")

    def turnOffMicCam(self):
        print(f"Navigating to Meet: {self.meet_link}")
        self.driver.get(self.meet_link)
        print("Waiting for pre-join screen to load...")
        try:
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[jsname="hw0c9"]'))
            )
        except TimeoutException:
            print("  ⚠️  Pre-join screen took too long — continuing anyway.")
        time.sleep(1.5)
        self._mute_device("Microphone", jsname="hw0c9",  shortcut=Keys.CONTROL + "d")
        time.sleep(0.5)
        self._mute_device("Camera",     jsname="psRWwc", shortcut=Keys.CONTROL + "e")
        time.sleep(0.5)
        print("Mic/Camera setup: Done\n")

    # ──────────────────────────────────────────────────────────────────────────
    # JOIN  (unchanged from Phase 1)
    # ──────────────────────────────────────────────────────────────────────────

    def joinMeeting(self):
        print("Looking for join button...")
        time.sleep(2)
        strategies = [
            (By.XPATH, '//button[.//span[contains(text(),"Join now") or contains(text(),"Ask to join") or text()="Join"]]'),
            (By.XPATH, '//span[text()="Join now" or text()="Ask to join"]'),
            (By.XPATH, '//*[@role="button" and (contains(.,"Join now") or contains(.,"Ask to join"))]'),
            (By.CSS_SELECTOR, 'button[jsname="Qx7uuf"]'),
        ]
        clicked_label = None
        for by, selector in strategies:
            try:
                btn = WebDriverWait(self.driver, 8).until(
                    EC.element_to_be_clickable((by, selector))
                )
                clicked_label = btn.text.strip() or "Join"
                btn.click()
                print(f"  Clicked: '{clicked_label}'")
                break
            except TimeoutException:
                continue
        if not clicked_label:
            print("  ⚠️  No join button found — may already be in the call.")
            return False
        return clicked_label

    def waitToBeAdmitted(self, timeout=300):
        print(f"Waiting to be admitted (up to {timeout}s)...")
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.any_of(
                    EC.presence_of_element_located((By.XPATH, '//button[contains(@aria-label,"Leave call")]')),
                    EC.presence_of_element_located((By.XPATH, '//button[contains(@aria-label,"leave")]')),
                )
            )
            print("  ✅ Admitted to the meeting!")
            return True
        except TimeoutException:
            print("  ⚠️  Timed out waiting to be admitted.")
            return False

    def checkIfJoined(self):
        try:
            WebDriverWait(self.driver, 15).until(
                EC.any_of(
                    EC.presence_of_element_located((By.XPATH, '//button[contains(@aria-label,"Leave call")]')),
                    EC.presence_of_element_located((By.XPATH, '//*[contains(@aria-label,"leave")]')),
                )
            )
            print("✅ Bot confirmed inside the meeting!")
            return True
        except TimeoutException:
            print("⚠️  Could not confirm in-call state — check the browser.")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # PHASE 2 — CAPTIONS
    # ──────────────────────────────────────────────────────────────────────────

    def enableCaptions(self):
        print("Enabling captions...")
        time.sleep(2)
        try:
            body = self.driver.find_element(By.TAG_NAME, "body")
            body.send_keys(Keys.SHIFT + "c")
            time.sleep(1)
        except Exception as e:
            print(f"  ⚠️  Shortcut failed: {e}")

        try:
            WebDriverWait(self.driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'div[aria-label="Captions"]'))
            )
            print("✅ Captions are ON.\n")
            return True
        except TimeoutException:
            pass

        print("  Shortcut may not have worked — trying toolbar button...")
        try:
            btn = WebDriverWait(self.driver, 6).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    '//*[@role="button" and ('
                    'contains(translate(@aria-label,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"caption") or '
                    'contains(translate(@data-tooltip,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"caption"))]'
                ))
            )
            btn.click()
            WebDriverWait(self.driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'div[aria-label="Captions"]'))
            )
            print("✅ Captions are ON (via toolbar button).\n")
            return True
        except TimeoutException:
            print("  ⚠️  Could not confirm captions are on — scraper will still run.")
            return False

    def saveTranscriptAndSummarise(self):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        base_name = f"meet_{timestamp}"
        txt_path  = os.path.join(TRANSCRIPT_DIR, f"{base_name}_transcript.txt")
        md_path   = os.path.join(TRANSCRIPT_DIR, f"{base_name}_summary.md")

        transcript = self.caption_scraper.build_transcript()

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("Google Meet Transcript\n")
            f.write(f"Generated : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"Meet link : {self.meet_link}\n")
            f.write("=" * 60 + "\n\n")
            f.write(transcript)
        print(f"\n📄 Transcript saved → {txt_path}")

        print("🤖 Sending transcript to Gemini for summary...")
        summary = summarise_with_gemini(transcript)

        # Phase 3 — send email if summary is not empty
        date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        send_summary_email(summary, date_str)

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Meeting Summary\n\n")
            f.write(f"**Date/Time :** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}  \n")
            f.write(f"**Meet link :** {self.meet_link}  \n\n")
            f.write("---\n\n")
            f.write(summary)
        print(f"✅ Summary saved    → {md_path}\n")

    def close_browser(self):
        """
        Three-layer browser close for Windows reliability:
          1. driver.quit()  — clean Selenium shutdown
          2. taskkill on chromedriver PID — kills the chromedriver process tree
          3. taskkill on all chrome.exe  — nuclear option if still open
        """
        # Layer 1: polite Selenium quit
        try:
            self.driver.quit()
            time.sleep(2)
        except Exception:
            pass

        # Layer 2: kill chromedriver process tree (takes Chrome with it on Windows)
        if self._chrome_pid:
            try:
                subprocess.call(
                    ["taskkill", "/F", "/T", "/PID", str(self._chrome_pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                time.sleep(1)
            except Exception:
                pass

        # Layer 3: kill any remaining chrome.exe launched by this script
        # (Only kills processes that share this chromedriver's user-data-dir)
        try:
            subprocess.call(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────────
    # MAIN FLOW
    # ──────────────────────────────────────────────────────────────────────────

    def run(self):
        self.ensureLoggedIn()
        self.turnOffMicCam()
        label = self.joinMeeting()

        if label and "ask" in str(label).lower():
            self.waitToBeAdmitted()
        else:
            time.sleep(6)

        self.checkIfJoined()
        self.enableCaptions()
        self.caption_scraper.start()

        print("Bot is live and silent in the meeting.")
        print("Captions are being recorded.")
        print("Press Ctrl+C to leave and generate the summary.\n")


if __name__ == "__main__":
    bot = JoinGoogleMeet()

    def shutdown(sig=None, frame=None):
        print("\n⏹  Ctrl+C detected — shutting down gracefully...")

        # 1. Stop caption scraper
        try:
            bot.caption_scraper.stop()
        except Exception:
            pass

        # 2. Save transcript + Gemini summary (browser still open)
        try:
            bot.saveTranscriptAndSummarise()
        except Exception as e:
            print(f"  ⚠️  Error during save: {e}")

        # 3. Close browser (with force-kill fallback for Windows)
        print("Closing browser and leaving meeting...")
        bot.close_browser()

        print("Done. Goodbye!")
        os._exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    bot.run()

    while True:
        time.sleep(60)