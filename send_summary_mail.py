"""
Phase 3 — Send standup summary via email using Gmail App Password.

SETUP (one-time):
  1. Go to https://myaccount.google.com/apppasswords
  2. Create an app password (name it anything e.g. "MeetBot")
  3. Copy the 16-character password into your .env file:

     GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

  Note: App passwords only work if 2-Step Verification is ON for your account.

INTEGRATION:
  Import and call send_summary_email(summary_text, date_str) from meet_bot.py
  after summarise_with_gemini() returns — see integration instructions below.
"""

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
SENDER_EMAIL   = os.getenv("GMAIL_ID")
RECEIVER_EMAIL = os.getenv("RECEIVER_GMAIL_ID")
SMTP_HOST      = "smtp.gmail.com"
SMTP_PORT      = 587


def send_summary_email(summary_text: str, date_str: str) -> bool:
    """
    Sends the standup summary as plain text email.

    Args:
        summary_text : The summary string returned by summarise_with_gemini()
        date_str     : Human-readable date/time string for the subject line
                       e.g. "2026-03-04 09:30"

    Returns:
        True  — email sent successfully
        False — sending failed (reason printed to console)
    """
    app_password = os.getenv("GMAIL_APP_PASSWORD")
    if not app_password:
        print("⚠️  GMAIL_APP_PASSWORD not set in .env — email skipped.")
        return False

    # Guard: do not send if summary is empty or only whitespace
    if not summary_text or not summary_text.strip():
        print("⚠️  Summary is empty — email not sent.")
        return False

    # ── Build email ───────────────────────────────────────────────────────────
    subject = f"📋 Daily Standup Summary — {date_str}"

    body = (
        f"Hi Nivas,\n\n"
        f"Here is the automated standup summary for {date_str}:\n\n"
        f"{'=' * 60}\n\n"
        f"{summary_text.strip()}\n\n"
        f"{'=' * 60}\n\n"
        f"— MeetBot 🤖"
    )

    msg = MIMEMultipart()
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECEIVER_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # ── Send ──────────────────────────────────────────────────────────────────
    try:
        print(f"📧 Sending summary email to {RECEIVER_EMAIL}...")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()          # encrypt the connection
            server.ehlo()
            server.login(SENDER_EMAIL, app_password)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print(f"✅ Email sent successfully to {RECEIVER_EMAIL}")
        return True

    except smtplib.SMTPAuthenticationError:
        print("❌ Email failed: Authentication error.")
        print("   → Make sure GMAIL_APP_PASSWORD in .env is correct.")
        print("   → App password must be 16 chars (spaces are fine, they are stripped).")
        return False

    except smtplib.SMTPException as e:
        print(f"❌ Email failed: SMTP error — {e}")
        return False

    except Exception as e:
        print(f"❌ Email failed: Unexpected error — {e}")
        return False