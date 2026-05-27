import smtplib
import ssl
import csv
import json
import time
import os
import re
import sys
import random
import logging
import subprocess
import tempfile
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from getpass import getpass

try:
    from colorama import init, Fore, Style
    init(autoreset=True)
except ImportError:
    print("colorama not found. Install it: pip install colorama")
    sys.exit(1)


CONFIG_FILE = "sender_config.json"

DEFAULT_TEMPLATE = (
    "<p>Hi {{.FirstName}},</p>\n"
    "<p>This email is for {{.Email}}.</p>"
)

PROVIDERS = [
    ("Gmail",               "smtp.gmail.com",                     465, "SSL",      "your Gmail address"),
    ("Gmail (STARTTLS)",    "smtp.gmail.com",                     587, "STARTTLS", "your Gmail address"),
    ("Outlook / Office365", "smtp.office365.com",                 587, "STARTTLS", "your full Outlook/O365 email"),
    ("Yahoo",               "smtp.mail.yahoo.com",                465, "SSL",      "your Yahoo email address"),
    ("Yahoo (STARTTLS)",    "smtp.mail.yahoo.com",                587, "STARTTLS", "your Yahoo email address"),
    ("SendGrid",            "smtp.sendgrid.net",                  587, "STARTTLS", "apikey  (literal string)"),
    ("Mailgun",             "smtp.mailgun.org",                   587, "STARTTLS", "your Mailgun SMTP username"),
    ("SMTP2GO",             "mail.smtp2go.com",                   587, "STARTTLS", "your SMTP2GO username"),
    ("SMTP2GO (SSL)",       "mail.smtp2go.com",                   465, "SSL",      "your SMTP2GO username"),
    ("Amazon SES",          "email-smtp.us-east-1.amazonaws.com", 587, "STARTTLS", "your SES SMTP Access Key ID"),
    ("Amazon SES (SSL)",    "email-smtp.us-east-1.amazonaws.com", 465, "SSL",      "your SES SMTP Access Key ID"),
    ("ProtonMail",          "smtp.proton.me",                     587, "STARTTLS", "your ProtonMail address"),
    ("Zoho",                "smtp.zoho.com",                      587, "STARTTLS", "your Zoho email address"),
    ("Zoho (SSL)",          "smtp.zoho.com",                      465, "SSL",      "your Zoho email address"),
    ("Custom SMTP",         None,                                 None, None,      None),
]


# ── Config persistence ────────────────────────────────────────────────────────

def load_config():
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"profiles": {}, "last_profile": None, "last_campaign": None}


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def save_profile(config, profile_name, settings):
    config["profiles"][profile_name] = settings
    config["last_profile"] = profile_name
    save_config(config)


def save_campaign_summary(config, profile_name, subject, csv_path, sent, failed):
    config["last_campaign"] = {
        "profile":   profile_name,
        "subject":   subject,
        "csv_path":  csv_path,
        "sent":      sent,
        "failed":    failed,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_config(config)


# ── CSV helpers ───────────────────────────────────────────────────────────────

def extract_name_from_email(email):
    """stellaeden62@gmail.com → ('Stellaeden62', '')"""
    local = email.split("@")[0]
    return local.capitalize(), ""


def write_temp_csv(rows):
    """Write rows to a temp CSV file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="smtp_tmp_")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["FirstName", "LastName", "Email"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_clipboard():
    """Return clipboard text using tkinter (stdlib). Returns None on failure."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        return text
    except Exception:
        return None


def parse_emails_from_text(text):
    """Extract valid email addresses from free-form text (newlines or commas)."""
    tokens = re.split(r"[\n\r,]+", text)
    emails = []
    for token in tokens:
        token = token.strip().strip('"\'<> ')
        if token and "@" in token and "." in token.split("@")[-1]:
            emails.append(token)
    return emails


def parse_delay(val):
    """Accept '1', '2.5', or '2-5'. Returns (min_secs, max_secs) floats."""
    val = str(val).strip()
    if "-" in val:
        parts = val.split("-", 1)
        try:
            lo, hi = float(parts[0]), float(parts[1])
            return (min(lo, hi), max(lo, hi))
        except ValueError:
            pass
    try:
        n = float(val)
        return (n, n)
    except ValueError:
        return (1.0, 1.0)


def deduplicate_recipients(recipients):
    """Remove rows with duplicate emails (case-insensitive). Returns (deduped, dupe_count)."""
    seen  = set()
    deduped = []
    dupes   = 0
    for r in recipients:
        key = r["Email"].lower()
        if key in seen:
            dupes += 1
        else:
            seen.add(key)
            deduped.append(r)
    return deduped, dupes


def export_results(results, stamp):
    """Write results_YYYYMMDD_HHMMSS.csv. results = list of dicts."""
    filename = f"results_{stamp}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Email", "FirstName", "LastName",
                                                "Status", "Error", "Timestamp"])
        writer.writeheader()
        writer.writerows(results)
    return filename


def prompt_attachment():
    """Ask if user wants to attach a file. Returns path string or None."""
    if not ask_yn("Attach a file to all emails?"):
        return None
    while True:
        path = input("  Attachment file path: ").strip()
        if os.path.isfile(path):
            print(Fore.GREEN + f"  Attachment: {os.path.basename(path)} "
                  f"({os.path.getsize(path):,} bytes)")
            return path
        print(Fore.RED + f"  [ERROR] File not found: {path}")
        if not ask_yn("  Try a different path?"):
            return None


def prompt_csv_path():
    """
    Prompt for a CSV path with smart handling:
      - Detects accidental email input and re-prompts
      - 'single' → prompt for one email, build temp CSV
      - 'quick'  → prompt for comma-separated emails, build temp CSV
    Returns (resolved_path, is_temp_file).
    """
    print(Fore.CYAN + "  Hint: enter a file path, 'single' (one email), 'quick' (comma-separated), or 'clipboard'")

    while True:
        raw = input("Recipients CSV file path: ").strip()

        if not raw:
            print(Fore.RED + "  Please enter a path, 'single', 'quick', or 'clipboard'.")
            continue

        keyword = raw.lower()

        # ── 'clipboard' mode ──
        if keyword == "clipboard":
            text = read_clipboard()
            if not text:
                print(Fore.RED + "  Could not read clipboard (empty or access denied).")
                continue
            emails = parse_emails_from_text(text)
            if not emails:
                print(Fore.RED + "  No valid email addresses found in clipboard.")
                print(Fore.YELLOW + "  Expected emails separated by newlines or commas.")
                continue
            rows = []
            for email in emails:
                first, last = extract_name_from_email(email)
                rows.append({"FirstName": first, "LastName": last, "Email": email})
            path = write_temp_csv(rows)
            print(Fore.GREEN + f"  Read {len(rows)} email(s) from clipboard.")
            for r in rows:
                print(Fore.WHITE + f"    {r['FirstName']} <{r['Email']}>")
            return path, True

        # ── 'single' mode ──
        if keyword == "single":
            email = input("  Email address: ").strip()
            if not email or "@" not in email:
                print(Fore.RED + "  Invalid email address.")
                continue
            first, last = extract_name_from_email(email)
            path = write_temp_csv([{"FirstName": first, "LastName": last, "Email": email}])
            print(Fore.GREEN + f"  Temp CSV created for 1 recipient.")
            return path, True

        # ── 'quick' mode ──
        if keyword == "quick":
            raw_emails = input("  Emails (comma-separated): ").strip()
            emails = [e.strip() for e in raw_emails.split(",") if e.strip() and "@" in e]
            if not emails:
                print(Fore.RED + "  No valid email addresses found.")
                continue
            rows = []
            for email in emails:
                first, last = extract_name_from_email(email)
                rows.append({"FirstName": first, "LastName": last, "Email": email})
            path = write_temp_csv(rows)
            print(Fore.GREEN + f"  Temp CSV created for {len(rows)} recipient(s).")
            return path, True

        # ── Accidental email address input ──
        if "@" in raw and not os.path.isfile(raw):
            print(Fore.YELLOW + f"  '{raw}' looks like an email address, not a file path.")
            corrected = input("  Did you mean to type a CSV file path? Enter path: ").strip()
            if corrected:
                raw = corrected
            else:
                print(Fore.RED + "  No path entered. Try again, or type 'single' / 'quick'.")
                continue

        # ── Normal file path ──
        if not os.path.isfile(raw):
            print(Fore.RED + f"  [ERROR] File not found: {raw}")
            continue

        return raw, False


def prompt_multiline(instruction):
    """Multi-line body input: paste/stdin, file, or Notepad editor."""
    print(Fore.CYAN + instruction)
    print(Fore.CYAN + "  How do you want to enter the body?\n")
    print(f"  {Fore.YELLOW}1{Style.RESET_ALL}. Type / paste  (press Enter twice on a blank line to finish)")
    print(f"  {Fore.YELLOW}2{Style.RESET_ALL}. Load from a text file")
    print(f"  {Fore.YELLOW}3{Style.RESET_ALL}. Open in Notepad (write, save, close)\n")

    while True:
        choice = input(Fore.CYAN + "  Enter number: " + Style.RESET_ALL).strip()
        if choice in ("1", "2", "3"):
            break
        print(Fore.RED + "  Please enter 1, 2, or 3.")

    if choice == "2":
        while True:
            path = input("  Text file path: ").strip()
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    body = f.read()
                print(Fore.GREEN + f"  Loaded {len(body.splitlines())} line(s) from {path}")
                return body
            print(Fore.RED + f"  File not found: {path}")

    elif choice == "3":
        fd, tmp = tempfile.mkstemp(suffix=".txt", prefix="smtp_body_")
        os.close(fd)
        print(Fore.GREEN + "  Opening Notepad — write your message, save, then close it.")
        subprocess.run(["notepad.exe", tmp])
        with open(tmp, encoding="utf-8", errors="replace") as f:
            body = f.read()
        try:
            os.remove(tmp)
        except OSError:
            pass
        print(Fore.GREEN + f"  Read {len(body.splitlines())} line(s) from Notepad.")
        return body

    else:
        print(Fore.YELLOW + "\n  Paste or type your message.")
        print(Fore.YELLOW + "  Press Enter twice on a blank line when done:\n")
        lines = []
        consecutive_blanks = 0
        while True:
            line = input()
            if line == "":
                consecutive_blanks += 1
                if consecutive_blanks >= 2:
                    break
                lines.append("")        # keep single blank lines as paragraph breaks
            else:
                consecutive_blanks = 0
                lines.append(line)
        while lines and lines[-1] == "":   # strip trailing blank
            lines.pop()
        return "\n".join(lines)


def prompt_email_format(current=None):
    """
    Ask whether to use an HTML file or type plain text now.
    current: dict with keys 'email_format', 'html_path', 'body_text' (for edit mode).
    Returns dict: {email_format, html_path, body_text}
    """
    if current:
        fmt  = current.get("email_format", "html")
        desc = ("HTML file: " + (current.get("html_path") or "(default)")) if fmt == "html" \
               else "Plain text (saved in config)"
        print(Fore.CYAN + f"  Current format: {fmt.upper()} — {desc}")

    print(Fore.CYAN + "\n  Email format:")
    print(f"  {Fore.YELLOW}1{Style.RESET_ALL}. HTML template file")
    print(f"  {Fore.YELLOW}2{Style.RESET_ALL}. Plain text (type now)\n")

    while True:
        choice = input(Fore.CYAN + "  Enter number: " + Style.RESET_ALL).strip()
        if choice in ("1", "2"):
            break
        print(Fore.RED + "  Please enter 1 or 2.")

    if choice == "1":
        # HTML file path
        while True:
            print(Fore.CYAN + "  Leave blank to use the built-in default HTML template.")
            raw = input("  HTML template file path: ").strip()
            if not raw:
                print(Fore.GREEN + "  Using default template.")
                return {"email_format": "html", "html_path": "", "body_text": ""}
            if os.path.isfile(raw):
                return {"email_format": "html", "html_path": raw, "body_text": ""}
            print(Fore.RED + f"  [ERROR] File not found: {raw}")
            if input("  Use default template instead? (y/n): ").strip().lower() == "y":
                return {"email_format": "html", "html_path": "", "body_text": ""}

    else:
        # Plain text typed inline
        body = prompt_multiline(
            "  Type your message below. Use {{.FirstName}}, {{.LastName}}, {{.Email}} as placeholders.\n"
            "  Press Enter on an empty line when done:\n"
        )
        return {"email_format": "plain", "html_path": "", "body_text": body}


# ── Display helpers ───────────────────────────────────────────────────────────



def print_provider_menu():
    print(Fore.CYAN + Style.BRIGHT + "Select SMTP provider:\n")
    for i, (name, host, port, sec, _) in enumerate(PROVIDERS, start=1):
        detail = Fore.WHITE + (f"  {host}:{port} [{sec}]" if host else "  enter manually")
        print(f"  {Fore.YELLOW}{i:>2}{Style.RESET_ALL}. {Fore.GREEN}{name:<25}{Style.RESET_ALL}{detail}")
    print()


# ── Input helpers ─────────────────────────────────────────────────────────────

def prompt(label, default=None, secret=False):
    display = f"{label} [{default}]: " if default is not None else f"{label}: "
    value = getpass(display) if secret else input(display).strip()
    return str(default) if (not value and default is not None) else value


def ask_yn(question):
    return input(Fore.CYAN + question + " (y/n): " + Style.RESET_ALL).strip().lower() == "y"


def choose_provider():
    print_provider_menu()
    while True:
        raw = input(Fore.CYAN + "Enter number: " + Style.RESET_ALL).strip()
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(PROVIDERS):
                return PROVIDERS[idx]
        print(Fore.RED + f"  Please enter a number between 1 and {len(PROVIDERS)}.")


# ── Build settings via interactive prompts ────────────────────────────────────

def build_settings_interactive(default_creds=None):
    prov_name, host, port, security, username_hint = choose_provider()

    if host is None:
        host          = prompt("SMTP host")
        port          = int(prompt("SMTP port", default=587))
        sec_raw       = prompt("Security (SSL / STARTTLS)", default="STARTTLS").upper()
        security      = "SSL" if sec_raw == "SSL" else "STARTTLS"
        username_hint = "your SMTP username"
        prov_name     = f"Custom ({host}:{port})"

    print(Fore.GREEN + f"\n  Provider : {prov_name}")
    print(Fore.GREEN + f"  Host     : {host}:{port} [{security}]\n")

    dc_u = (default_creds or {}).get("username", "")
    dc_p = (default_creds or {}).get("password", "")
    if "gmail" in host.lower() and dc_u and dc_p:
        raw = input(Fore.CYAN + f"Use default credentials: {dc_u}? (Y/n): "
                    + Style.RESET_ALL).strip().lower()
        if raw != "n":
            username = dc_u
            password = dc_p
        else:
            username = prompt(f"SMTP username ({username_hint})")
            password = prompt("SMTP password / API key", secret=True)
    else:
        username = prompt(f"SMTP username ({username_hint})")
        password = prompt("SMTP password / API key", secret=True)

    raw = input(Fore.CYAN + "Save credentials in config? (Y/n): " + Style.RESET_ALL).strip().lower()
    if raw == "n":
        username = ""
        password = ""
    sender_name  = prompt("Sender name  (e.g. PayPal Support)")
    sender_email = prompt("Sender email (e.g. support@paypal.com)")
    subject      = prompt("Email subject")
    fmt          = prompt_email_format()
    csv_path, _  = prompt_csv_path()
    delay        = prompt("Delay between emails — seconds or range (e.g. 1  or  2-5)", default="1")

    return {
        "provider_name":   prov_name,
        "host":            host,
        "port":            port,
        "security":        security,
        "username":        username,
        "password":        password,
        "sender_name":     sender_name,
        "sender_email":    sender_email,
        "subject":         subject,
        "email_format":    fmt["email_format"],
        "html_path":       fmt["html_path"],
        "body_text":       fmt["body_text"],
        "csv_path":        csv_path,
        "delay":           delay,
    }


# ── Startup dashboard ─────────────────────────────────────────────────────────

def startup_dashboard(config):
    """
    Single-screen dashboard. Returns (action, profile_name_or_None).
    action: 'profile', 'new', 'edit', 'rename', 'delete', 'view', 'set_creds', 'quit'.
    """
    profiles = config.get("profiles", {})
    names    = list(profiles.keys())
    dc       = config.get("default_credentials", {})
    dc_user  = dc.get("username", "")

    print(Fore.CYAN + Style.BRIGHT + "\n=== SMTP Email Sender ===\n")

    if not names:
        print(Fore.YELLOW + "  No profiles saved yet. Starting new config setup.\n")
        return "new", None

    lc = config.get("last_campaign")
    if lc:
        failed_part = (Fore.RED + f"  {lc['failed']} failed" + Fore.WHITE) if lc["failed"] else ""
        print(Fore.WHITE + "Last campaign: " +
              Fore.GREEN + Style.BRIGHT + lc["profile"] + Style.RESET_ALL +
              Fore.WHITE + f"  |  {lc['sent']} sent" + failed_part +
              Fore.WHITE + f"  |  {lc['timestamp']}")

    if dc_user:
        print(Fore.WHITE + "Default credentials: " +
              Fore.GREEN + Style.BRIGHT + dc_user + Style.RESET_ALL +
              Fore.GREEN + "  [set]")
    else:
        print(Fore.YELLOW + "Default credentials: not set")
    print()

    print(Fore.CYAN + Style.BRIGHT + "Saved profiles:\n")
    for i, name in enumerate(names, start=1):
        s      = profiles[name]
        prov   = f"{s.get('provider_name', '')} ({s.get('port', '')} {s.get('security', '')})"
        email  = s.get("sender_email", "")
        csv_bn = os.path.basename(s.get("csv_path", "")) or "(none)"
        print(f"  {Fore.YELLOW}{i:>2}{Style.RESET_ALL}. "
              f"{Fore.GREEN}{name:<20}{Style.RESET_ALL}"
              f"  {prov:<22}"
              f"  |  {email:<28}"
              f"  |  {csv_bn}")

    creds_label = (f"Set default credentials  [{dc_user}]" if dc_user
                   else "Set default credentials")
    print(Fore.WHITE + "\n" + "─" * 45)
    print(f"  {Fore.YELLOW}N{Style.RESET_ALL}. New config")
    print(f"  {Fore.YELLOW}C{Style.RESET_ALL}. {creds_label}")
    print(f"  {Fore.YELLOW}E{Style.RESET_ALL}. Edit a profile")
    print(f"  {Fore.YELLOW}R{Style.RESET_ALL}. Rename a profile")
    print(f"  {Fore.YELLOW}D{Style.RESET_ALL}. Delete a profile")
    print(f"  {Fore.YELLOW}V{Style.RESET_ALL}. View a profile")
    print(f"  {Fore.YELLOW}Q{Style.RESET_ALL}. Quit")
    print()

    letter_map = {
        "n": "new", "c": "set_creds", "e": "edit", "r": "rename",
        "d": "delete", "v": "view", "q": "quit",
    }

    while True:
        raw = input(Fore.CYAN + "Enter number or letter: " + Style.RESET_ALL).strip().lower()
        if not raw:
            continue
        if raw in letter_map:
            return letter_map[raw], None
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(names):
                return "profile", names[idx]
        print(Fore.RED + f"  Enter 1–{len(names)} to load a profile, or N / C / E / R / D / V / Q.")


def pick_profile(config, prompt_label="\nSaved profiles:"):
    profiles = config["profiles"]
    names    = list(profiles.keys())
    print(Fore.CYAN + Style.BRIGHT + prompt_label + "\n")
    for i, name in enumerate(names, start=1):
        p = profiles[name]
        print(f"  {Fore.YELLOW}{i:>2}{Style.RESET_ALL}. {Fore.GREEN}{name:<30}{Style.RESET_ALL}"
              f"  {p['provider_name']} | {p['sender_email']}")
    print()
    while True:
        raw = input(Fore.CYAN + "Enter number: " + Style.RESET_ALL).strip()
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(names):
                return names[idx], profiles[names[idx]]
        print(Fore.RED + f"  Please enter a number between 1 and {len(names)}.")


def view_profile(name, s):
    fmt = s.get("email_format", "html")
    if fmt == "plain":
        body_preview = (s.get("body_text", "") or "")[:80].replace("\n", " ")
        fmt_detail   = f"Plain text — {body_preview!r}"
    else:
        fmt_detail   = f"HTML — {s.get('html_path', '') or '(default template)'}"

    print(Fore.CYAN + Style.BRIGHT + f"\n── Profile: {name} ──")
    print(Fore.WHITE  + f"  Provider    : {s.get('provider_name', '')}")
    print(Fore.WHITE  + f"  Host        : {s.get('host', '')}:{s.get('port', '')} [{s.get('security', '')}]")
    print(Fore.WHITE  + f"  Username    : {s.get('username', '')}")
    print(Fore.YELLOW + f"  Password    : {'*' * len(s.get('password', ''))}")
    print(Fore.WHITE  + f"  Sender name : {s.get('sender_name', '')}")
    print(Fore.WHITE  + f"  Sender email: {s.get('sender_email', '')}")
    print(Fore.WHITE  + f"  Subject     : {s.get('subject', '')}")
    print(Fore.WHITE  + f"  Format      : {fmt_detail}")
    print(Fore.WHITE  + f"  Delay       : {s.get('delay', 1)}s")
    print()


def edit_profile(name, s):
    """Walk through each editable field, showing current value. Return (new_name, updated_dict)."""
    print(Fore.CYAN + Style.BRIGHT + f"\nEditing profile: {name}")
    print(Fore.WHITE + "  Press Enter to keep the current value.\n")

    new_name = input(f"  Rename profile? Current name: {Fore.GREEN}{name}{Style.RESET_ALL}. "
                     f"New name (or Enter to keep): ").strip()
    if not new_name:
        new_name = name
    print()

    updated = dict(s)

    # ── SMTP provider ──
    print(Fore.CYAN + f"  Current provider: {s.get('provider_name', '')}  "
          f"({s.get('host', '')}:{s.get('port', '')} [{s.get('security', '')}])")
    if ask_yn("  Change SMTP provider?"):
        prov_name, host, port, security, username_hint = choose_provider()
        if host is None:
            host          = prompt("  SMTP host")
            port          = int(prompt("  SMTP port", default=587))
            sec_raw       = prompt("  Security (SSL / STARTTLS)", default="STARTTLS").upper()
            security      = "SSL" if sec_raw == "SSL" else "STARTTLS"
            prov_name     = f"Custom ({host}:{port})"
        updated["provider_name"] = prov_name
        updated["host"]          = host
        updated["port"]          = port
        updated["security"]      = security
    print()

    # ── Simple text fields ──
    fields = [
        ("username",     "SMTP username",                        False),
        ("password",     "SMTP password",                        True),
        ("sender_name",  "Sender name",                          False),
        ("sender_email", "Sender email",                         False),
        ("subject",      "Subject",                              False),
        ("delay",        "Delay — seconds or range (e.g. 2-5)", False),
    ]
    for key, label, secret in fields:
        current = s.get(key, "")
        if secret:
            display_current = "*" * len(str(current))
            new_val = getpass(f"  {label} [{display_current}]: ")
            if new_val:
                updated[key] = new_val
        else:
            updated[key] = prompt(f"  {label}", default=current)

    # ── Email format ──
    print()
    if ask_yn("  Change email format?"):
        fmt = prompt_email_format(current=s)
        updated["email_format"] = fmt["email_format"]
        updated["html_path"]    = fmt["html_path"]
        updated["body_text"]    = fmt["body_text"]

    return new_name, updated


# ── Email utilities ───────────────────────────────────────────────────────────

def load_recipients(csv_path):
    recipients = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            recipients.append({
                "FirstName": row.get("FirstName", "").strip(),
                "LastName":  row.get("LastName",  "").strip(),
                "Email":     row.get("Email",     "").strip(),
            })
    return recipients


def preview_csv(csv_path):
    """Print first 5 rows plus file stats; return True if user confirms."""
    file_size = os.path.getsize(csv_path)
    with open(csv_path, newline="", encoding="utf-8") as f:
        content = f.read()

    raw_lines = content.splitlines()
    raw_count = len(raw_lines)

    rows  = list(csv.DictReader(raw_lines))
    total = len(rows)

    print(Fore.CYAN + Style.BRIGHT + f"\n── CSV Preview: {os.path.basename(csv_path)} ──")
    print(Fore.WHITE + f"  Path      : {csv_path}")
    print(Fore.WHITE + f"  File size : {file_size:,} bytes  |  {raw_count} raw lines")
    print()

    if not rows:
        print(Fore.RED + "  [WARNING] No data rows found in this file.")
    else:
        print(Fore.WHITE + f"  Showing first {min(5, total)} of {total} row(s):\n")
        print(Fore.WHITE + f"  {'#':<4}  {'FirstName':<15}  {'LastName':<15}  Email")
        print(Fore.WHITE + "  " + "─" * 58)
        for i, row in enumerate(rows[:5], start=1):
            fn    = row.get("FirstName", "").strip()
            ln    = row.get("LastName",  "").strip()
            email = row.get("Email",     "").strip()
            print(Fore.WHITE + f"  {i:<4}  {fn:<15}  {ln:<15}  {email}")
        if total > 5:
            print(Fore.WHITE + f"  ... and {total - 5} more row(s)")
    print()

    return ask_yn("This looks correct?")


def load_template(html_path):
    with open(html_path, encoding="utf-8") as f:
        return f.read()


def render(template, recipient):
    return (
        template
        .replace("{{.FirstName}}", recipient["FirstName"])
        .replace("{{.LastName}}",  recipient["LastName"])
        .replace("{{.Email}}",     recipient["Email"])
    )


def setup_logger(log_path):
    logger = logging.getLogger("smtp_sender")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)
    return logger


def build_message(sender_name, sender_email, recipient, subject, body,
                  subtype="html", attachment_path=None):
    outer = MIMEMultipart("mixed" if attachment_path else "alternative")
    outer["Subject"] = subject
    outer["From"]    = formataddr((sender_name, sender_email))
    outer["To"]      = formataddr((f"{recipient['FirstName']} {recipient['LastName']}".strip(),
                                   recipient["Email"]))
    outer.attach(MIMEText(body, subtype, "utf-8"))
    if attachment_path:
        with open(attachment_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment",
                        filename=os.path.basename(attachment_path))
        outer.attach(part)
    return outer


def connect(host, port, security):
    context = ssl.create_default_context()
    if security == "STARTTLS":
        server = smtplib.SMTP(host, port)
        server.starttls(context=context)
        return server
    return smtplib.SMTP_SSL(host, port, context=context)


# ── Campaign runner ───────────────────────────────────────────────────────────

def _send_batch(server, recipients, sender_name, sender_email, subject,
                template, subtype, attachment_path, delay_range, logger, total_label):
    """
    Send to a list of recipients using an already-authenticated server.
    Returns (results_list, failed_recipients).
    results_list entries: {Email, FirstName, LastName, Status, Error, Timestamp}
    """
    results  = []
    failed_r = []
    n        = len(recipients)

    for i, recipient in enumerate(recipients, start=1):
        email  = recipient["Email"]
        rname  = f"{recipient['FirstName']} {recipient['LastName']}".strip()
        prefix = f"[{total_label}{i}/{n}]"
        ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not email:
            print(Fore.RED + f"{prefix} SKIP — missing email")
            logger.warning(f"Skipped — missing email | row {i}")
            results.append({"Email": "", "FirstName": recipient["FirstName"],
                            "LastName": recipient["LastName"],
                            "Status": "skipped", "Error": "missing email", "Timestamp": ts})
            continue

        try:
            body = render(template, recipient)
            msg  = build_message(sender_name, sender_email, recipient, subject,
                                 body, subtype, attachment_path)
            server.sendmail(sender_email, email, msg.as_string())
            print(Fore.GREEN + f"{prefix} SENT  → {rname} <{email}>")
            logger.info(f"SENT | {email} | {rname}")
            results.append({"Email": email, "FirstName": recipient["FirstName"],
                            "LastName": recipient["LastName"],
                            "Status": "sent", "Error": "", "Timestamp": ts})
        except Exception as e:
            err = str(e)
            print(Fore.RED + f"{prefix} FAIL  → {rname} <{email}> | {err}")
            logger.error(f"FAIL | {email} | {rname} | {err}")
            results.append({"Email": email, "FirstName": recipient["FirstName"],
                            "LastName": recipient["LastName"],
                            "Status": "failed", "Error": err, "Timestamp": ts})
            failed_r.append(recipient)

        if i < n:
            lo, hi = delay_range
            time.sleep(random.uniform(lo, hi) if lo != hi else lo)

    return results, failed_r


def _resolve_creds(username, password, host, profile_name, config):
    """
    Return (username, password) to use for a campaign, in priority order:
      1. Global default credentials (Gmail profiles only)
      2. Credentials saved in the profile
      3. Interactive prompt
    """
    dc   = config.get("default_credentials", {})
    dc_u = dc.get("username", "")
    dc_p = dc.get("password", "")

    if "gmail" in host.lower() and dc_u and dc_p:
        raw = input(Fore.CYAN + f"Use default credentials: {dc_u}? (Y/n): "
                    + Style.RESET_ALL).strip().lower()
        if raw != "n":
            return dc_u, dc_p

    if username and password:
        raw = input(Fore.CYAN + f"Using saved credentials for {username}. Use these? (Y/n): "
                    + Style.RESET_ALL).strip().lower()
        if raw != "n":
            return username, password
        username = prompt("SMTP username", default=username)
        password = prompt("SMTP password / API key", secret=True)
        raw2 = input(Fore.CYAN + "Update saved credentials? (y/N): "
                     + Style.RESET_ALL).strip().lower()
        if raw2 == "y" and profile_name and profile_name in config.get("profiles", {}):
            config["profiles"][profile_name]["username"] = username
            config["profiles"][profile_name]["password"] = password
            save_config(config)
            print(Fore.GREEN + "  Credentials updated.")
        return username, password

    if username or password:
        print(Fore.YELLOW + "  [!] Credentials incomplete in profile — re-entering.")
    username = prompt("SMTP username", default=username or None)
    password = prompt("SMTP password / API key", secret=True)
    return username, password


def run_campaign(s, profile_name, config, temp_csv=None):
    host         = s["host"]
    port         = s["port"]
    security     = s["security"]
    username, password = _resolve_creds(
        s.get("username") or "", s.get("password") or "",
        host, profile_name, config,
    )
    sender_name  = s["sender_name"]
    sender_email = s["sender_email"]
    subject      = s["subject"]
    csv_path     = s["csv_path"]
    email_format = s.get("email_format", "html")
    html_path    = s.get("html_path", "")
    body_text    = s.get("body_text", "")
    delay_range  = parse_delay(s.get("delay", "1"))

    # ── Resolve template / body ───────────────────────────────────────────────
    if email_format == "plain":
        template = body_text or "Hi {{.FirstName}},\n\nThis email is for {{.Email}}."
        subtype  = "plain"
    elif html_path and os.path.isfile(html_path):
        template = load_template(html_path)
        subtype  = "html"
    elif html_path and not os.path.isfile(html_path):
        print(Fore.RED + f"[ERROR] HTML template not found: {html_path}")
        print(Fore.YELLOW + "  Falling back to default template.")
        template = DEFAULT_TEMPLATE
        subtype  = "html"
    else:
        template = DEFAULT_TEMPLATE
        subtype  = "html"

    # ── Validate CSV ──────────────────────────────────────────────────────────
    if not os.path.isfile(csv_path):
        print(Fore.RED + f"[ERROR] CSV file not found: {csv_path}")
        return 0, 0

    recipients = load_recipients(csv_path)

    _fsize = os.path.getsize(csv_path)
    with open(csv_path, newline="", encoding="utf-8") as _f:
        _raw_lines = len(_f.read().splitlines())
    _data_lines = _raw_lines - 1  # subtract header row
    print(Fore.GREEN + f"Loaded {len(recipients)} recipient(s) from {os.path.basename(csv_path)}")
    print(Fore.WHITE + f"  [debug] {_fsize:,} bytes  |  {_raw_lines} raw lines  |  {_data_lines} data rows  |  {len(recipients)} parsed")
    if _data_lines > 0 and len(recipients) < _data_lines:
        print(Fore.RED + f"  [WARNING] {_data_lines - len(recipients)} row(s) not parsed — "
              "check for blank Email fields, BOM, or mixed line endings.")

    if not recipients:
        print(Fore.RED + "[ERROR] CSV file is empty or has no valid rows.")
        return 0, 0

    # ── Deduplicate ───────────────────────────────────────────────────────────
    recipients, dupes = deduplicate_recipients(recipients)
    if dupes:
        print(Fore.YELLOW + f"  Skipped {dupes} duplicate email(s).")

    # ── Attachment ────────────────────────────────────────────────────────────
    attachment_path = prompt_attachment()

    # ── Preview first email ───────────────────────────────────────────────────
    if recipients and ask_yn("\nPreview first email before sending?"):
        preview_body = render(template, recipients[0])
        print(Fore.CYAN + Style.BRIGHT + "\n── Preview ──────────────────────────────")
        print(Fore.WHITE + f"  To      : {recipients[0]['Email']}")
        print(Fore.WHITE + f"  Subject : {subject}")
        print(Fore.WHITE + "  Body:\n")
        for line in preview_body.splitlines():
            print("    " + line)
        if attachment_path:
            print(Fore.WHITE + f"\n  Attachment: {os.path.basename(attachment_path)}")
        print(Fore.CYAN + "─────────────────────────────────────────\n")
        if not ask_yn("Looks good? Send to all recipients?"):
            print(Fore.YELLOW + "  Cancelled.")
            return 0, 0

    stamp        = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"send_log_{stamp}.log"
    logger       = setup_logger(log_filename)

    delay_desc = (f"{delay_range[0]}-{delay_range[1]}s"
                  if delay_range[0] != delay_range[1] else f"{delay_range[0]}s")
    print(Fore.YELLOW + f"\n{len(recipients)} recipient(s) ready  |  delay: {delay_desc}")
    print(Fore.YELLOW + f"Log file : {log_filename}\n")
    logger.info(f"Campaign started — {len(recipients)} recipients | subject: {subject}")
    logger.info(f"Profile: {profile_name} | SMTP: {host}:{port} [{security}] | "
                f"from: {sender_name} <{sender_email}>")

    all_results  = []
    failed_recip = []

    try:
        server = connect(host, port, security)
        server.login(username, password)
        print(Fore.GREEN + f"[AUTH OK] Connected to {host}:{port} [{security}]\n")
        logger.info("SMTP authentication successful")

        with server:
            # ── First pass ───────────────────────────────────────────────────
            results, failed_recip = _send_batch(
                server, recipients, sender_name, sender_email,
                subject, template, subtype, attachment_path,
                delay_range, logger, total_label=""
            )
            all_results.extend(results)

            # ── Retry loop (up to 2 attempts) ────────────────────────────────
            attempt = 0
            while failed_recip and attempt < 2:
                print(Fore.CYAN + Style.BRIGHT + f"\n=== Summary so far ===")
                sent_n   = sum(1 for r in all_results if r["Status"] == "sent")
                failed_n = len(failed_recip)
                print(Fore.GREEN + f"  Sent:   {sent_n}")
                print(Fore.RED   + f"  Failed: {failed_n}")
                if not ask_yn(f"\nRetry {failed_n} failed email(s)?"):
                    break
                attempt += 1
                print(Fore.YELLOW + f"\nRetry attempt {attempt}/2...\n")
                logger.info(f"Retry attempt {attempt} — {len(failed_recip)} recipients")
                retry_results, failed_recip = _send_batch(
                    server, failed_recip, sender_name, sender_email,
                    subject, template, subtype, attachment_path,
                    delay_range, logger, total_label=f"retry{attempt}:"
                )
                # Update all_results: replace prior failed entries with retry outcomes
                retried_emails = {r["Email"] for r in retry_results}
                all_results = [r for r in all_results if r["Email"] not in retried_emails]
                all_results.extend(retry_results)

    except smtplib.SMTPAuthenticationError:
        print(Fore.RED + "[ERROR] Authentication failed. Check your username and password/API key.")
        logger.error("SMTP authentication failed")
        return 0, 0
    except Exception as e:
        print(Fore.RED + f"[ERROR] Connection error: {e}")
        logger.error(f"Connection error: {e}")
        return 0, 0
    finally:
        if temp_csv and os.path.isfile(temp_csv):
            try:
                os.remove(temp_csv)
            except OSError:
                pass

    # ── Final summary ─────────────────────────────────────────────────────────
    sent   = sum(1 for r in all_results if r["Status"] == "sent")
    failed = sum(1 for r in all_results if r["Status"] == "failed")

    results_file = export_results(all_results, stamp)

    print(Fore.CYAN + Style.BRIGHT + "\n=== Final Summary ===")
    print(Fore.GREEN + f"  Sent:    {sent}")
    print((Fore.RED if failed else Fore.WHITE) + f"  Failed:  {failed}")
    print(Fore.YELLOW + f"  Log:     {log_filename}")
    print(Fore.YELLOW + f"  Results: {results_file}\n")
    logger.info(f"Campaign finished — sent: {sent} | failed: {failed} | results: {results_file}")

    save_campaign_summary(config, profile_name, subject, csv_path, sent, failed)
    return sent, failed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    config = load_config()
    while True:
        choice, selected_profile = startup_dashboard(config)
        if choice != "set_creds":
            break
        print(Fore.CYAN + Style.BRIGHT + "\n── Set Default Credentials ──\n")
        dc_user = prompt("Gmail username (e.g. norereplyio@gmail.com)")
        dc_pass = prompt("Gmail app password", secret=True)
        config["default_credentials"] = {"username": dc_user, "password": dc_pass}
        save_config(config)
        print(Fore.GREEN + f"\n  Default credentials saved: {dc_user}\n")

    if choice == "quit":
        print(Fore.CYAN + "Done. Goodbye.\n")
        return

    profile_name = None
    settings     = None

    # ── View ──────────────────────────────────────────────────────────────────
    if choice == "view":
        name, s = pick_profile(config, prompt_label="\nSelect a profile to view:")
        view_profile(name, s)
        print(Fore.CYAN + "Done. Goodbye.\n")
        return

    # ── Delete ────────────────────────────────────────────────────────────────
    if choice == "delete":
        name, _ = pick_profile(config, prompt_label="\nSelect a profile to delete:")
        if ask_yn(Fore.RED + f"  Delete '{name}'? This cannot be undone."):
            del config["profiles"][name]
            if config.get("last_profile") == name:
                config["last_profile"] = None
            save_config(config)
            print(Fore.GREEN + f"  Deleted: {name}")
        else:
            print(Fore.YELLOW + "  Cancelled.")
        print(Fore.CYAN + "Done. Goodbye.\n")
        return

    # ── Rename ────────────────────────────────────────────────────────────────
    if choice == "rename":
        old_name, s = pick_profile(config, prompt_label="\nSelect a profile to rename:")
        new_name = input(f"  New name for {Fore.GREEN}{old_name}{Style.RESET_ALL} "
                         f"(or Enter to cancel): ").strip()
        if not new_name or new_name == old_name:
            print(Fore.YELLOW + "  No change.")
        else:
            config["profiles"][new_name] = config["profiles"].pop(old_name)
            if config.get("last_profile") == old_name:
                config["last_profile"] = new_name
            save_config(config)
            print(Fore.GREEN + f"  Renamed: {old_name}  →  {new_name}")
        print(Fore.CYAN + "Done. Goodbye.\n")
        return

    # ── Edit ──────────────────────────────────────────────────────────────────
    if choice == "edit":
        name, s       = pick_profile(config, prompt_label="\nSelect a profile to edit:")
        new_name, updated = edit_profile(name, s)
        print(Fore.CYAN + Style.BRIGHT + "\nWhat would you like to do with the changes?\n")
        print(f"  {Fore.YELLOW}1{Style.RESET_ALL}. Save changes and send now")
        print(f"  {Fore.YELLOW}2{Style.RESET_ALL}. Save changes and exit")
        print(f"  {Fore.YELLOW}3{Style.RESET_ALL}. Discard changes and exit")
        print()
        while True:
            raw = input(Fore.CYAN + "Enter number: " + Style.RESET_ALL).strip()
            if raw in ("1", "2", "3"):
                break
            print(Fore.RED + "  Please enter 1, 2, or 3.")

        if raw == "3":
            print(Fore.YELLOW + "  Changes discarded.")
            print(Fore.CYAN + "Done. Goodbye.\n")
            return

        if new_name != name:
            del config["profiles"][name]
        config["profiles"][new_name] = updated
        config["last_profile"]       = new_name
        save_config(config)
        print(Fore.GREEN + f"  Saved: {new_name}")

        if raw == "2":
            print(Fore.CYAN + "Done. Goodbye.\n")
            return

        # raw == "1" — fall through to send
        profile_name = new_name
        settings     = updated

    # ── Profile selected from dashboard ──────────────────────────────────────
    elif choice == "profile":
        profile_name = selected_profile
        settings     = config["profiles"][profile_name]
        print(Fore.GREEN + f"\nLoaded profile: {profile_name}\n")

    # ── New ───────────────────────────────────────────────────────────────────
    elif choice == "new":
        settings = build_settings_interactive(default_creds=config.get("default_credentials"))
        profile_name = prompt("\nSave this config as (profile name)").strip()
        if not profile_name:
            profile_name = f"profile_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        save_profile(config, profile_name, dict(settings))
        print(Fore.GREEN + f"  Saved as: {profile_name}\n")

    # ── Campaign loop ─────────────────────────────────────────────────────────
    show_preview = (choice == "profile")
    while True:
        is_temp  = False
        csv_path = settings.get("csv_path", "")

        if csv_path and not os.path.isfile(csv_path):
            print(Fore.RED + f"\n[ERROR] Default recipients file not found: {csv_path}")
            print(Fore.YELLOW + "  Falling back to recipient prompt.")
            csv_path, is_temp = prompt_csv_path()
            settings = dict(settings)
            settings["csv_path"] = csv_path
        elif not csv_path:
            csv_path, is_temp = prompt_csv_path()
            settings = dict(settings)
            settings["csv_path"] = csv_path
        elif show_preview:
            if not preview_csv(csv_path):
                print(Fore.YELLOW + "  Select a different recipients file.")
                csv_path, is_temp = prompt_csv_path()
                settings = dict(settings)
                settings["csv_path"] = csv_path
        show_preview = False  # only on first iteration

        run_campaign(settings, profile_name, config,
                     temp_csv=settings["csv_path"] if is_temp else None)

        if not ask_yn("\nSend to a different CSV with the same settings?"):
            break

        new_csv, is_temp = prompt_csv_path()
        new_subject      = prompt("Email subject", default=settings["subject"])
        settings         = dict(settings)
        settings["csv_path"] = new_csv
        settings["subject"]  = new_subject

    print(Fore.CYAN + "Done. Goodbye.\n")


if __name__ == "__main__":
    main()
