"""
BindIQ — Gmail Monitor
Sends and monitors warantheyanesh@gmail.com for vendor contract emails.

Requires a Gmail App Password (not the regular account password):
  1. Enable 2FA at myaccount.google.com/security
  2. Create App Password: Security -> App Passwords -> Mail
  3. Set GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx in KnowledgeGraph/.env

Without an App Password the module runs in simulation mode.
"""

import imaplib
import smtplib
import email as emaillib
import os
import time
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header

logger = logging.getLogger("email_watcher")

GMAIL_IMAP = "imap.gmail.com"
GMAIL_SMTP = "smtp.gmail.com"
GMAIL_PORT = 587

BINDIQ_EMAIL = "warantheyanesh@gmail.com"


def _app_password() -> str:
    return os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")


def is_configured() -> bool:
    return bool(_app_password())


# ── SEND ──────────────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, html_body: str, plain_body: str = "") -> bool:
    """Send email from warantheyanesh@gmail.com via SMTP."""
    if not is_configured():
        logger.info(f"[SIMULATION] Would send to {to}: {subject}")
        return True

    msg = MIMEMultipart("alternative")
    msg["From"]    = BINDIQ_EMAIL
    msg["To"]      = to
    msg["Subject"] = subject

    if plain_body:
        msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(GMAIL_SMTP, GMAIL_PORT) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(BINDIQ_EMAIL, _app_password())
            srv.sendmail(BINDIQ_EMAIL, to, msg.as_string())
        logger.info(f"Email sent to {to}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Send failed: {e}")
        return False


def send_whole_foods_trigger(to: str = BINDIQ_EMAIL) -> bool:
    """Send the simulated Whole Foods vendor contract email."""
    subject = "Congratulations! Whole Foods Vendor Contract - Action Required"
    html = """
<p>Hi Maria,</p>
<p>Congratulations! We're excited to bring your artisan breads to our Midwest stores.</p>
<p>Before your first delivery on <strong>March 14</strong>, you must upload a Certificate
of Insurance to EXIGIS.</p>
<h3>Insurance Requirements:</h3>
<ul>
  <li>General Liability: <strong>$2,000,000</strong> per occurrence / $4,000,000 aggregate</li>
  <li>Additional Insured: <strong>Whole Foods Market Inc.</strong>
      (CG 20 15 endorsement required)</li>
  <li>Carrier Rating: AM Best <strong>A-</strong> or better</li>
  <li>Primary &amp; Non-Contributory language required</li>
  <li>30-day cancellation notice to certificate holder</li>
</ul>
<p>Register at: https://exigis.com/wholefoods</p>
<p>First delivery: <strong>Saturday, March 14</strong> (8 days!)</p>
<br>
<p>Jordan Smith<br>
Regional Vendor Coordinator<br>
Whole Foods Market — Midwest Region<br>
512-555-FOOD</p>
"""
    plain = (
        "Hi Maria,\n\nCongratulations on your Whole Foods contract!\n\n"
        "Insurance Requirements:\n"
        "- General Liability: $2,000,000 per occurrence\n"
        "- Additional Insured: Whole Foods Market Inc. (CG 2015)\n"
        "- AM Best A- or better\n"
        "- Deadline: March 14 (8 days)\n\n"
        "Jordan Smith, Whole Foods Market"
    )
    return send_email(to, subject, html, plain)


BINDIQ_BASE_URL = os.environ.get("BINDIQ_BASE_URL", "http://localhost:8000")


def build_alert_html(analysis: dict) -> tuple[str, str]:
    """
    Build the full HTML + plain-text alert email from an analysis dict.

    analysis keys (all optional, sensible defaults used):
        customer_name     str   -- "Maria"
        customer_id       str   -- used in CTA links
        current_carrier   str   -- "Simply Business"
        current_carrier_id str  -- "simply_business"
        current_limit     str   -- "$1,000,000"
        required_limit    str   -- "$2,000,000"
        deadline          str   -- "Mar 14, 2026"
        days_left         int   -- 8
        retailer          str   -- "Whole Foods"
        top_carriers      list  -- scored carrier dicts
    """
    name           = analysis.get("customer_name",      "Maria")
    cust_id        = analysis.get("customer_id",        "demo")
    cur_carrier    = analysis.get("current_carrier",    "Simply Business")
    cur_carrier_id = analysis.get("current_carrier_id", "simply_business")
    cur_limit      = analysis.get("current_limit",      "$1,000,000")
    req_limit      = analysis.get("required_limit",     "$2,000,000")
    deadline       = analysis.get("deadline",           "Mar 14, 2026")
    days_left      = analysis.get("days_left",          8)
    retailer       = analysis.get("retailer",           "Whole Foods")
    carriers       = analysis.get("top_carriers",       [])

    top3_names = ", ".join(c["name"] for c in carriers[:3]) if carriers else "NEXT Insurance, Hartford, Travelers"
    top        = carriers[0] if carriers else {"name": "NEXT Insurance", "score": 93, "quote_speed": "15 min"}

    upgrade_link = f"{BINDIQ_BASE_URL}/review/{cust_id}?action=upgrade&carrier={cur_carrier_id}"
    quotes_link  = (
        f"{BINDIQ_BASE_URL}/review/{cust_id}?gl=2000000"
        f"&endorsement=cg2015&retailer={retailer.replace(' ', '_')}"
    )
    # Primary CTA → BindIQ FastAPI interactive review page
    review_link = analysis.get("review_url") or f"{BINDIQ_BASE_URL}/review/{cust_id}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f0f2f5;color:#1a1a1a}}
.wrap{{max-width:600px;margin:24px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.12)}}
.hdr{{background:linear-gradient(135deg,#1565C0 0%,#0d47a1 100%);color:#fff;padding:28px 32px}}
.hdr h1{{font-size:18px;font-weight:700;line-height:1.3}}
.badge{{display:inline-block;background:#ff5252;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700;letter-spacing:.5px;margin-left:8px;vertical-align:middle}}
.hdr p{{margin-top:8px;opacity:.88;font-size:14px}}
.body{{padding:28px 32px}}
.deadline{{background:#fff3e0;border:1px solid #ffcc80;border-radius:8px;padding:14px 18px;text-align:center;margin-bottom:24px;font-size:15px;font-weight:600;color:#e65100}}
.gap-item{{display:flex;align-items:flex-start;gap:10px;padding:11px 14px;background:#fff8f0;border-left:4px solid #f57c00;border-radius:0 6px 6px 0;margin-bottom:7px;font-size:14px}}
.ok-item{{display:flex;align-items:flex-start;gap:10px;padding:11px 14px;background:#f1f8e9;border-left:4px solid #66bb6a;border-radius:0 6px 6px 0;margin-bottom:7px;font-size:14px}}
h3{{font-size:15px;font-weight:700;margin:22px 0 10px;color:#1a1a1a}}
.opt{{border:2px solid #e0e0e0;border-radius:10px;padding:18px 20px;margin-bottom:14px}}
.opt.rec{{border-color:#1565C0;background:#fafcff}}
.opt h4{{font-size:15px;font-weight:700;margin-bottom:6px}}
.opt p{{font-size:13px;color:#555;margin:4px 0}}
.opt .meta{{font-size:13px;color:#333;margin:8px 0}}
.btn{{display:inline-block;padding:11px 22px;border-radius:6px;text-decoration:none;font-weight:600;font-size:14px;margin-top:10px}}
.btn-blue{{background:#1565C0;color:#fff !important}}
.btn-grey{{background:#f5f5f5;color:#333 !important;border:1px solid #ddd}}
.rec-badge{{display:inline-block;background:#1565C0;color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;margin-left:8px;vertical-align:middle}}
.cta{{text-align:center;padding:24px 0 8px;border-top:1px solid #eee;margin-top:20px}}
.cta-btn{{display:inline-block;padding:14px 32px;background:#1565C0;color:#fff !important;border-radius:8px;text-decoration:none;font-weight:700;font-size:16px}}
.footer{{padding:16px 32px 24px;font-size:12px;color:#999;border-top:1px solid #f0f0f0}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <h1>Action Needed: Insurance Gap Detected <span class="badge">URGENT</span></h1>
    <p>BindIQ detected a vendor contract requiring coverage upgrades</p>
  </div>
  <div class="body">
    <p style="font-size:15px;margin-bottom:18px">Hi {name},<br><br>
    Congratulations on your <strong>{retailer} vendor contract!</strong>
    I noticed it requires insurance upgrades before your first delivery.</p>

    <div class="deadline">Deadline: {deadline} &nbsp;&middot;&nbsp; {days_left} days away</div>

    <h3>What {retailer} requires:</h3>
    <div class="gap-item">&#9888;&nbsp;<div><strong>GL Limit: {req_limit}</strong> &mdash; you currently have {cur_limit}</div></div>
    <div class="gap-item">&#9888;&nbsp;<div><strong>CG 2015 Broad Form Vendor endorsement</strong> &mdash; not on your current policy</div></div>
    <div class="gap-item">&#9888;&nbsp;<div><strong>{retailer} Market Inc. as Additional Insured</strong> &mdash; missing</div></div>
    <div class="ok-item">&#10003;&nbsp;<div><strong>AM Best Rating</strong> &mdash; your current carrier qualifies (A rated)</div></div>

    <h3>Your options:</h3>

    <div class="opt">
      <h4>1. Quick Fix &mdash; Upgrade current policy</h4>
      <p>Endorse your existing <strong>{cur_carrier}</strong> policy</p>
      <div class="meta"><strong>Cost:</strong> ~$213 extra/year &nbsp;&middot;&nbsp; <strong>Timeline:</strong> 2&ndash;3 days</div>
      <a href="{upgrade_link}" class="btn btn-grey">Upgrade Current Policy</a>
    </div>

    <div class="opt rec">
      <h4>2. Shop &amp; Save <span class="rec-badge">RECOMMENDED</span></h4>
      <p>Get competitive quotes with {req_limit} coverage</p>
      <div class="meta">
        <strong>Top carriers:</strong> {top3_names}<br>
        <strong>Potential savings:</strong> up to $300/year &nbsp;&middot;&nbsp; <strong>Timeline:</strong> 48&ndash;72 hours
      </div>
      <a href="{quotes_link}" class="btn btn-blue">Get Competitive Quotes</a>
    </div>

    <div class="cta">
      <a href="{review_link}" class="cta-btn">View Full Analysis &amp; Quote Carriers &#8594;</a>
      <p style="margin-top:12px;font-size:13px;color:#666">
        See all 5 carrier matches with Knowledge Graph reasoning paths.<br>
        One click to submit a quote &mdash; BindIQ delivers your certificate to {retailer}.
      </p>
    </div>
  </div>
  <div class="footer">
    Questions? Reply to this email or call 888-BIND-NOW<br>
    Top match: <strong>{top['name']}</strong> &mdash; {top.get('score', 93):.0f}/100 &middot; Quote in {top.get('quote_speed', '15 min')}<br>
    <a href="{review_link}" style="color:#90caf9">Open BindIQ Dashboard</a> &nbsp;&middot;&nbsp;
    <a href="{BINDIQ_BASE_URL}/unsubscribe?customer={cust_id}" style="color:#bbb">Unsubscribe</a>
  </div>
</div>
</body>
</html>"""

    plain = (
        f"Hi {name},\n\n"
        f"Congratulations on your {retailer} vendor contract!\n\n"
        f"ACTION REQUIRED -- {days_left} days until deadline ({deadline})\n\n"
        f"What {retailer} requires:\n"
        f"  ! GL Limit: {req_limit}  (you have {cur_limit})\n"
        f"  ! CG 2015 Broad Form Vendor endorsement  (MISSING)\n"
        f"  ! {retailer} Market Inc. as Additional Insured  (MISSING)\n"
        f"  OK AM Best Rating  (your carrier qualifies)\n\n"
        f"Option 1 -- Upgrade {cur_carrier}: ~$213/yr extra, 2-3 days\n"
        f"  {upgrade_link}\n\n"
        f"Option 2 -- Shop & Save (RECOMMENDED): up to $300/yr savings, 48-72 hours\n"
        f"  {quotes_link}\n\n"
        f"View full carrier analysis + quote: {review_link}\n\n"
        f"-- Your BindIQ Team | 888-BIND-NOW"
    )
    return html, plain


def send_bindiq_alert(to: str, analysis: dict) -> bool:
    """Send BindIQ's coverage gap alert email."""
    retailer  = analysis.get("retailer",   "Whole Foods")
    days_left = analysis.get("days_left",  8)
    subject   = (
        f"Action Needed: {retailer} Requires Higher Insurance "
        f"({days_left} days to comply)"
    )
    html, plain = build_alert_html(analysis)
    return send_email(to, subject, html, plain)


# ── RECEIVE / MONITOR ──────────────────────────────────────────────────────────

def check_inbox_for_trigger(since_minutes: int = 10) -> list[dict]:
    """
    Poll IMAP inbox for recent emails that look like vendor contracts.
    Uses the 3-stage email_agent pipeline (embedding → LLM → extraction).
    Falls back to keyword matching if email_agent is unavailable.
    Returns list of detected trigger emails.
    """
    if not is_configured():
        return []

    # Try to load the 3-stage agent
    try:
        import email_agent
        _use_agent = True
    except Exception:
        _use_agent = False

    KEYWORDS = ["certificate of insurance", "coi", "additional insured",
                "whole foods", "vendor contract", "$2,000,000", "cg 2015"]

    triggers = []
    try:
        mail = imaplib.IMAP4_SSL(GMAIL_IMAP)
        mail.login(BINDIQ_EMAIL, _app_password())
        mail.select("INBOX")

        status, data = mail.search(None, "UNSEEN")
        if status != "OK":
            return []

        all_unseen = data[0].split()
        if not all_unseen:
            return []

        # Only process the latest (most recent) unseen email
        nums_to_check = all_unseen[-1:]

        for num in nums_to_check:
            status, msg_data = mail.fetch(num, "(RFC822)")
            if status != "OK":
                continue

            msg     = emaillib.message_from_bytes(msg_data[0][1])
            subject = _decode_header(msg.get("Subject", ""))
            sender  = msg.get("From", "")
            body    = _get_body(msg)

            if _use_agent:
                result = email_agent.run(subject, body)
                if result.stage2_passed:
                    triggers.append({
                        "subject":      subject,
                        "from":         sender,
                        "body":         body[:500],
                        "confidence":   int(result.overall_confidence * 100),
                        "agent_result": result,
                    })
                    mail.store(num, "+FLAGS", "\\Seen")
            else:
                # Keyword fallback
                body_lower = body.lower()
                hits = sum(1 for kw in KEYWORDS if kw in body_lower or kw in subject.lower())
                if hits >= 2:
                    triggers.append({
                        "subject":      subject,
                        "from":         sender,
                        "body":         body[:500],
                        "keyword_hits": hits,
                        "confidence":   min(98, 60 + hits * 10),
                    })
                    mail.store(num, "+FLAGS", "\\Seen")

        mail.close()
        mail.logout()
    except Exception as e:
        logger.debug(f"IMAP check error: {e}")

    return triggers


def _decode_header(raw: str) -> str:
    parts = decode_header(raw)
    out = []
    for part, enc in parts:
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(part))
    return " ".join(out)


def _get_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                return part.get_payload(decode=True).decode("utf-8", errors="replace")
    else:
        return msg.get_payload(decode=True).decode("utf-8", errors="replace")
    return ""


def get_status() -> dict:
    """Return current Gmail connection status."""
    if not is_configured():
        return {"connected": False, "mode": "simulation",
                "message": "No App Password configured — running in simulation mode"}
    try:
        mail = imaplib.IMAP4_SSL(GMAIL_IMAP)
        mail.login(BINDIQ_EMAIL, _app_password())
        mail.logout()
        return {"connected": True, "mode": "live",
                "message": f"Connected: {BINDIQ_EMAIL}"}
    except Exception as e:
        return {"connected": False, "mode": "error", "message": str(e)}
