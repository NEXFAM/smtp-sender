# smtp-sender

An interactive command-line SMTP email sender. Supports multiple providers, saved profiles, HTML/plain-text templates, CSV recipient lists, attachments, retry logic, and per-send result exports.

## Requirements

- Python 3.7+
- [colorama](https://pypi.org/project/colorama/)

```bash
pip install -r requirements.txt
```

## Usage

```bash
python smtp_sender.py
```

The tool starts an interactive dashboard. On first run it walks you through creating a new sending profile.

## Supported SMTP Providers

| Provider | Host | Port | Security |
|---|---|---|---|
| Gmail | smtp.gmail.com | 465 | SSL |
| Gmail | smtp.gmail.com | 587 | STARTTLS |
| Outlook / Office365 | smtp.office365.com | 587 | STARTTLS |
| Yahoo | smtp.mail.yahoo.com | 465 / 587 | SSL / STARTTLS |
| SendGrid | smtp.sendgrid.net | 587 | STARTTLS |
| Mailgun | smtp.mailgun.org | 587 | STARTTLS |
| SMTP2GO | mail.smtp2go.com | 465 / 587 | SSL / STARTTLS |
| Amazon SES | email-smtp.us-east-1.amazonaws.com | 465 / 587 | SSL / STARTTLS |
| ProtonMail | smtp.proton.me | 587 | STARTTLS |
| Zoho | smtp.zoho.com | 465 / 587 | SSL / STARTTLS |
| Custom | configurable | configurable | SSL / STARTTLS |

## Recipients CSV Format

The CSV must have these columns (with header row):

```
FirstName,LastName,Email
Jane,Doe,jane@example.com
John,Smith,john@example.com
```

### Quick input modes

Instead of a file path, you can type:

- `single` — enter one email address interactively
- `quick` — enter multiple emails as a comma-separated list
- `clipboard` — paste emails from the clipboard (newline- or comma-separated)

## Email Templates

Templates support these placeholders, replaced per recipient:

| Placeholder | Value |
|---|---|
| `{{.FirstName}}` | Recipient's first name |
| `{{.LastName}}` | Recipient's last name |
| `{{.Email}}` | Recipient's email address |

You can supply an HTML file or type plain text inline. Leaving the HTML path blank uses a built-in default template.

## Configuration Profiles

Profiles (SMTP credentials, sender name, subject, template, recipients file, delay) are saved to `sender_config.json` in the working directory. **Do not commit this file** — it is listed in `.gitignore`.

### Dashboard options

| Key | Action |
|---|---|
| Number | Load and run that profile |
| N | Create a new profile |
| E | Edit an existing profile |
| R | Rename a profile |
| D | Delete a profile |
| V | View profile details |
| C | Set default Gmail credentials |
| Q | Quit |

## Sending Options

- **Delay** — fixed seconds (`1`) or a random range (`2-5`) between emails
- **Attachment** — optionally attach a file to every email
- **Preview** — preview the first rendered email before sending
- **Retry** — automatically offers to retry failed recipients (up to 2 attempts)

## Output Files

After each campaign two files are written to the working directory:

| File | Contents |
|---|---|
| `send_log_YYYYMMDD_HHMMSS.log` | Timestamped log of every send attempt |
| `results_YYYYMMDD_HHMMSS.csv` | Per-recipient status: `sent`, `failed`, or `skipped` |

## Gmail Setup

Gmail requires an [App Password](https://support.google.com/accounts/answer/185833) (not your regular password). Enable 2-Step Verification on your Google Account, then generate an App Password for "Mail".

## License

MIT
