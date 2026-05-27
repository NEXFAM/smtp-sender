# smtp-sender

A fully interactive command-line SMTP email sender built in Python. Supports multiple providers, saved sending profiles, HTML/plain-text templates, CSV recipient lists, file attachments, retry logic, and timestamped result exports.

**Author:** [NEXFAM](https://github.com/NEXFAM)

---

## Features

- 11 pre-configured SMTP providers (Gmail, Outlook, SendGrid, SES, and more)
- Named profiles saved locally — no re-entering settings between sessions
- HTML template files or inline plain-text body
- Per-recipient placeholder substitution (`{{.FirstName}}`, `{{.Email}}`, etc.)
- CSV recipient list with preview before sending
- Quick input modes: single address, comma-separated list, or clipboard paste
- Optional file attachment on every email
- Configurable delay between sends (fixed or randomised range)
- Automatic retry for failed recipients (up to 2 attempts)
- Timestamped `.log` and `.csv` result files after every campaign
- Coloured terminal output via [colorama](https://pypi.org/project/colorama/)

---

## Requirements

- Python 3.7+
- colorama

```bash
pip install -r requirements.txt
```

---

## Usage

```bash
python smtp_sender.py
```

On first run the tool walks you through creating a sending profile. Subsequent runs open the dashboard where you can load a saved profile and send immediately.

---

## Supported SMTP Providers

| Provider | Host | Port | Security |
|---|---|---|---|
| Gmail | smtp.gmail.com | 465 | SSL |
| Gmail (STARTTLS) | smtp.gmail.com | 587 | STARTTLS |
| Outlook / Office365 | smtp.office365.com | 587 | STARTTLS |
| Yahoo | smtp.mail.yahoo.com | 465 / 587 | SSL / STARTTLS |
| SendGrid | smtp.sendgrid.net | 587 | STARTTLS |
| Mailgun | smtp.mailgun.org | 587 | STARTTLS |
| SMTP2GO | mail.smtp2go.com | 465 / 587 | SSL / STARTTLS |
| Amazon SES | email-smtp.us-east-1.amazonaws.com | 465 / 587 | SSL / STARTTLS |
| ProtonMail | smtp.proton.me | 587 | STARTTLS |
| Zoho | smtp.zoho.com | 465 / 587 | SSL / STARTTLS |
| Custom | configurable | configurable | SSL / STARTTLS |

---

## Recipients CSV Format

The CSV must include a header row with these exact columns:

```
FirstName,LastName,Email
Jane,Doe,jane@example.com
John,Smith,john@example.com
```

### Quick input modes

When prompted for a CSV path, you can also type a keyword:

| Keyword | Behaviour |
|---|---|
| `single` | Enter one email address interactively |
| `quick` | Enter multiple emails as a comma-separated list |
| `clipboard` | Read emails from clipboard (newline- or comma-separated) |

---

## Email Templates

Use these placeholders anywhere in your subject, HTML file, or plain-text body — they are replaced per recipient at send time:

| Placeholder | Replaced with |
|---|---|
| `{{.FirstName}}` | Recipient's first name |
| `{{.LastName}}` | Recipient's last name |
| `{{.Email}}` | Recipient's email address |

Supply an HTML file path when prompted, or leave it blank to use the built-in default template. Choose plain-text to type or paste the body inline.

---

## Configuration Profiles

All profile settings (SMTP host, credentials, sender name, subject, template, recipients file, delay) are stored in `sender_config.json` in the working directory.

**This file is excluded by `.gitignore` and should never be committed.**

### Dashboard options

| Key | Action |
|---|---|
| `1`–`n` | Load and run that profile |
| `N` | Create a new profile |
| `E` | Edit an existing profile |
| `R` | Rename a profile |
| `D` | Delete a profile |
| `V` | View profile details |
| `C` | Set default Gmail credentials |
| `Q` | Quit |

---

## Sending Options

| Option | Details |
|---|---|
| Delay | Fixed seconds (`1`) or a randomised range (`2-5`) between sends |
| Attachment | Attach any file to every email in the campaign |
| Preview | Render and review the first email before sending to all |
| Retry | Automatically prompts to retry failed recipients (up to 2 passes) |

---

## Output Files

Two files are written to the working directory after every campaign:

| File | Contents |
|---|---|
| `send_log_YYYYMMDD_HHMMSS.log` | Full timestamped log of every send attempt |
| `results_YYYYMMDD_HHMMSS.csv` | Per-recipient result: `sent`, `failed`, or `skipped` |

---

## Gmail Setup

Gmail requires an **App Password** — not your regular account password.

1. Enable 2-Step Verification on your Google Account
2. Go to **Google Account → Security → App Passwords**
3. Generate a password for "Mail"
4. Use that 16-character password in smtp-sender

More info: [support.google.com/accounts/answer/185833](https://support.google.com/accounts/answer/185833)

---

## License

MIT
