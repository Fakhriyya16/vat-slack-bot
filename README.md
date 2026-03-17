# EU VAT Checker — Slack Bot

A Slack bot that automates EU VAT number verification using the official European Commission VIES API. For valid numbers, it generates a PDF proof and saves it automatically to the correct client folder in Google Drive.

## What it does

Type `/vat` followed by any EU VAT number in Slack:

```
/vat BE0456810810
/vat SE663000013801
```

The bot will:
1. Query the EU VIES API in real time
2. Post the result (company name, address, status) to Slack
3. Generate a PDF verification report for VALID results
4. Save the PDF to the correct client folder in **two Google Drive locations** using smart name matching
5. Post folder links back to Slack

## Result types

| Status | Meaning | What happens |
|---|---|---|
| ✅ VALID | VAT is registered and active | Result + PDF posted in Slack, PDF saved to Google Drive |
| ❌ INVALID | VAT does not exist or is inactive | Result posted in Slack only |
| ⚠️ UNAVAILABLE | VIES is temporarily down | Result posted in Slack, try again later |

## Smart folder matching

The bot matches client folders in Google Drive intelligently:
1. **Exact match** — saves automatically
2. **Fuzzy match** — strips legal suffixes (SA, NV, BV, GmbH, Ltd…) and matches on meaningful words. `SA ORANGE BELGIUM` → finds `Orange` folder automatically
3. **Ambiguous match** — if multiple folders score equally (e.g. `NU Bank` vs `NIBC Bank`), posts interactive buttons in Slack asking which folder to use
4. **No match** — creates a new folder automatically using the VIES company name

## Google Drive structure

**Location 1 — NV Customer Documents:**
```
Soda - Finance - Private
  └── Accounts Receivable
        └── Sales Contracts
              └── NV - Customer Documents
                    └── [Client Folder]
                          └── VAT_ClientName_BEXXXXXXXXX_YYYY-MM-DD.pdf
```

**Location 2 — Secondary location:**
```
[Second Drive Root]
  └── [Client Folder]
        └── Vies Check
              └── VAT_ClientName_BEXXXXXXXXX_YYYY-MM-DD.pdf
```

## Tech stack

| Component | Technology |
|---|---|
| Bot server | Python + Flask |
| Hosting | Render.com (free tier) |
| VAT validation | EU VIES SOAP API |
| PDF generation | ReportLab |
| Slack integration | slack-sdk |
| Google Drive | google-api-python-client |
| Code storage | GitHub |

## Deployment

### Prerequisites
- A Render.com account
- A Slack workspace with admin access (to install apps)
- A Google Cloud project with Drive API enabled and a service account

### Environment variables (set in Render)

| Variable | Description |
|---|---|
| `SLACK_BOT_TOKEN` | Bot token from Slack app (`xoxb-...`) |

### Secret files (set in Render)

| File | Description |
|---|---|
| `google_credentials.json` | Google service account credentials JSON |

### Google Drive folder IDs (set in `app.py`)

| Variable | Description |
|---|---|
| `DRIVE_FOLDER_ID` | ID of the NV - Customer Documents folder |
| `DRIVE_FOLDER_ID_2` | ID of the second Drive location |

### Slack app configuration

1. Create a Slack app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add bot scopes: `chat:write`, `files:write`, `commands`, `channels:history`
3. Create a slash command `/vat` pointing to `https://your-render-url.onrender.com/slack/vat`
4. Enable **Interactivity & Shortcuts** pointing to `https://your-render-url.onrender.com/slack/actions`
5. Install the app to your workspace
6. Invite the bot to your target channel: `/invite @VAT Checker`

### Google Drive setup

1. Create a service account in Google Cloud Console
2. Enable the Google Drive API
3. Share target folders with the service account as **Editor**
4. For Shared Drives: also add the service account as **Content Manager** at the Shared Drive level
5. Download the credentials JSON and add it as a Secret File in Render

## Running locally

```bash
pip install -r requirements.txt
export SLACK_BOT_TOKEN=xoxb-your-token
export GOOGLE_CREDENTIALS=$(cat google_credentials.json)
python app.py
```

## Notes

- The Render free tier spins down after 15 minutes of inactivity. The first request of the day may take 20–30 seconds to respond while the server wakes up. The bot handles this gracefully by responding with `⚙️ Agent is starting up, please wait...` immediately.
- All credentials are loaded from environment variables and secret files — no secrets are stored in the code.
- Cost: **€0/month** on the free tier.

## Repository structure

```
vat-slack-bot/
├── app.py              # Main bot application
├── requirements.txt    # Python dependencies
└── render.yaml         # Render deployment config
```
