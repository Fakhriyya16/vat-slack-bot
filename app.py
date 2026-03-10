"""
VAT Checker Slack Bot — with interactive folder selection buttons
"""

import os
import io
import json
import datetime
import threading
from flask import Flask, request, jsonify
from slack_sdk import WebClient

app = Flask(__name__)
slack_client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
DRIVE_FOLDER_ID = "169XfIlnQfIg8ocQmeTkKeo9GKJrw-g-b"

# Temporary store for pending folder selections
# key: action_id, value: {pdf_bytes, filename, company_name, candidates}
pending_uploads = {}


# ── VAT Parsing ───────────────────────────────────────────────────────────────

def parse_vat(raw):
    raw = raw.strip().upper().replace(" ", "").replace("-", "")
    if len(raw) < 4:
        raise ValueError("VAT number too short.")
    return raw[:2], raw[2:]


# ── VIES Query ────────────────────────────────────────────────────────────────

def check_vies(country_code, vat_number):
    import requests as req
    import xml.etree.ElementTree as ET

    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:urn="urn:ec.europa.eu:taxud:vies:services:checkVat:types">'
        "<soapenv:Body><urn:checkVat>"
        f"<urn:countryCode>{country_code}</urn:countryCode>"
        f"<urn:vatNumber>{vat_number}</urn:vatNumber>"
        "</urn:checkVat></soapenv:Body></soapenv:Envelope>"
    )
    base = {"country_code": country_code, "vat_number": vat_number,
            "corrected_vat": None, "name": "—", "address": "—",
            "request_date": str(datetime.date.today())}
    try:
        response = req.post(
            "https://ec.europa.eu/taxation_customs/vies/services/checkVatService",
            data=envelope, headers={"Content-Type": "text/xml; charset=UTF-8"}, timeout=10
        )
        ns = {"ns": "urn:ec.europa.eu:taxud:vies:services:checkVat:types"}
        root = ET.fromstring(response.text)

        fault = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Fault")
        if fault is not None:
            faultstring = fault.findtext("faultstring", "").upper()
            return {**base, "status": "INVALID" if "INVALID" in faultstring else "UNAVAILABLE"}

        valid_el = root.find(".//ns:valid", ns)
        if valid_el is None:
            return {**base, "status": "UNAVAILABLE"}

        valid = valid_el.text.strip().lower() == "true"
        name = (root.findtext(".//ns:name", "—", ns) or "—").strip()
        address = (root.findtext(".//ns:address", "—", ns) or "—").strip().replace("\n", ", ")
        req_date = root.findtext(".//ns:requestDate", str(datetime.date.today()), ns)
        returned_vat = (root.findtext(".//ns:vatNumber", vat_number, ns) or vat_number).strip()
        corrected_vat = returned_vat if returned_vat != vat_number.strip() else None

        return {
            "status": "VALID" if valid else "INVALID",
            "country_code": country_code, "vat_number": vat_number,
            "corrected_vat": corrected_vat, "name": name,
            "address": address, "request_date": str(req_date),
        }
    except Exception as e:
        return {**base, "status": "INVALID" if "INVALID" in str(e).upper() else "UNAVAILABLE"}


# ── PDF Generation ────────────────────────────────────────────────────────────

STATUS_COLORS = {
    "VALID": (0.07, 0.53, 0.32),
    "INVALID": (0.80, 0.15, 0.15),
    "UNAVAILABLE": (0.90, 0.55, 0.00),
}

def generate_pdf_bytes(data):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    W, H = A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    status = data["status"]
    r, g, b = STATUS_COLORS.get(status, (0.3, 0.3, 0.3))

    c.setFillColorRGB(0.10, 0.14, 0.24)
    c.rect(0, H - 55*mm, W, 55*mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(20*mm, H - 28*mm, "EU VAT Verification Report")
    c.setFont("Helvetica", 11)
    c.drawString(20*mm, H - 40*mm, "Source: European Commission — VIES System")
    c.drawString(20*mm, H - 48*mm, f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    badge_y = H - 80*mm
    c.setFillColorRGB(r, g, b)
    c.roundRect(20*mm, badge_y, 60*mm, 16*mm, 4*mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(50*mm, badge_y + 5*mm, status)

    fields = [("Original VAT", f"{data['country_code']}{data['vat_number']}")]
    if data.get("corrected_vat"):
        fields.append(("Corrected VAT", f"{data['country_code']}{data['corrected_vat']}"))
    fields += [
        ("Country Code", data["country_code"]),
        ("Company Name", data["name"]),
        ("Address", data["address"]),
        ("Request Date", data["request_date"]),
        ("Verified Via", "EU VIES — checkVatService"),
    ]

    y = H - 105*mm
    row_h = 14*mm
    col1, col2 = 20*mm, 75*mm

    for i, (label, value) in enumerate(fields):
        bg = 0.96 if i % 2 == 0 else 1.0
        c.setFillColorRGB(bg, bg, bg)
        c.rect(col1, y - 3*mm, W - 40*mm, row_h, fill=1, stroke=0)
        c.setFillColorRGB(0.40, 0.40, 0.40)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(col1 + 3*mm, y + 5*mm, label)
        c.setFillColorRGB(*(0.90, 0.55, 0.00) if label == "Corrected VAT" else (0.10, 0.10, 0.10))
        c.setFont("Helvetica", 10)
        c.drawString(col2, y + 5*mm, str(value)[:80])
        y -= row_h

    c.setFillColorRGB(0.10, 0.14, 0.24)
    c.rect(0, 0, W, 18*mm, fill=1, stroke=0)
    c.setFillColorRGB(0.7, 0.7, 0.7)
    c.setFont("Helvetica", 8)
    c.drawString(20*mm, 7*mm, "Automatically generated using the EU VIES VAT validation service.")
    c.drawRightString(W - 20*mm, 7*mm, "Page 1 of 1")

    c.save()
    buf.seek(0)
    return buf.read()


# ── Google Drive ──────────────────────────────────────────────────────────────

def get_drive_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    secret_path = "/etc/secrets/google_credentials.json"
    if os.path.exists(secret_path):
        with open(secret_path) as f:
            creds_dict = json.load(f)
    else:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS")
        if not creds_json:
            raise ValueError("Google credentials not found")
        creds_dict = json.loads(creds_json)

    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)


def get_all_customer_folders(service):
    """Fetch all subfolders inside the customer documents folder."""
    query = (
        f"'{DRIVE_FOLDER_ID}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    results = service.files().list(
        q=query, fields="files(id, name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        pageSize=500
    ).execute()
    return results.get("files", [])


LEGAL_SUFFIXES = {
    "sa", "nv", "bv", "ab", "ag", "ltd", "llc", "inc", "gmbh", "srl",
    "sas", "spa", "oy", "as", "plc", "pte", "pty", "kft", "zrt",
    "momsgrupp", "holding", "group", "groep", "international", "intl", "bank"
}

def clean_name(name):
    words = name.lower().replace("-", " ").replace("_", " ").split()
    return [w for w in words if w not in LEGAL_SUFFIXES and len(w) > 2]


def score_match(company_name, folder_name):
    """
    Returns a match score between 0 and 1.
    1.0 = exact match, 0 = no meaningful overlap.
    """
    company_words = clean_name(company_name)
    folder_words = clean_name(folder_name)

    if not folder_words or not company_words:
        return 0.0

    matches = sum(
        1 for fw in folder_words
        if any(fw == cw or (len(fw) > 3 and (fw in cw or cw in fw))
               for cw in company_words)
    )
    return matches / len(folder_words)


def find_folder_candidates(all_folders, company_name):
    """
    Returns:
    - exact: single folder dict if exact name match
    - candidates: list of folder dicts with score >= 0.6 (fuzzy matches)
    - none: empty list (no matches)
    """
    # Exact match
    for folder in all_folders:
        if folder["name"].strip().lower() == company_name.strip().lower():
            return "exact", [folder]

    # Fuzzy candidates — score >= 0.6
    scored = [
        (score_match(company_name, f["name"]), f)
        for f in all_folders
    ]
    candidates = [f for score, f in scored if score >= 0.6]
    candidates.sort(key=lambda f: score_match(company_name, f["name"]), reverse=True)

    if len(candidates) == 1:
        return "single_fuzzy", candidates
    elif len(candidates) > 1:
        return "ambiguous", candidates
    else:
        return "none", []


def upload_pdf_to_folder(pdf_bytes, filename, folder_id):
    from googleapiclient.http import MediaIoBaseUpload
    service = get_drive_service()
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")
    uploaded = service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True
    ).execute()
    return uploaded.get("webViewLink", "")


def create_customer_folder(company_name):
    service = get_drive_service()
    folder = service.files().create(
        body={"name": company_name.strip(), "mimeType": "application/vnd.google-apps.folder", "parents": [DRIVE_FOLDER_ID]},
        fields="id", supportsAllDrives=True
    ).execute()
    return folder["id"]


# ── Slack Helpers ─────────────────────────────────────────────────────────────

STATUS_EMOJI = {"VALID": "✅", "INVALID": "❌", "UNAVAILABLE": "⚠️"}


def post_to_slack_and_drive(channel_id, response_url, pdf_bytes, filename, company_name, folder_id):
    """Upload PDF to Slack and Drive after folder is determined."""
    import requests

    # Upload to Slack
    slack_client.files_upload_v2(
        channel=channel_id,
        filename=filename,
        content=pdf_bytes,
        title=f"VAT Verification — {filename}",
        initial_comment=f"📄 PDF proof for {filename.replace('.pdf','')}"
    )

    # Upload to Drive
    try:
        drive_link = upload_pdf_to_folder(pdf_bytes, filename, folder_id)
        if drive_link:
            slack_client.chat_postMessage(
                channel=channel_id,
                text=f"📁 Saved to Google Drive: {drive_link}"
            )
    except Exception as e:
        slack_client.chat_postMessage(
            channel=channel_id,
            text=f"⚠️ Drive upload failed: {e}"
        )


def ask_user_to_pick_folder(channel_id, response_url, pdf_bytes, filename, company_name, candidates):
    """Post an interactive message asking user to pick the right folder."""
    import uuid

    pending_id = str(uuid.uuid4())[:8]
    pending_uploads[pending_id] = {
        "pdf_bytes": pdf_bytes,
        "filename": filename,
        "company_name": company_name,
        "channel_id": channel_id,
        "candidates": {f["id"]: f["name"] for f in candidates},
    }

    buttons = []
    for folder in candidates[:4]:  # Max 4 buttons
        buttons.append({
            "type": "button",
            "text": {"type": "plain_text", "text": folder["name"]},
            "value": json.dumps({"pending_id": pending_id, "folder_id": folder["id"]}),
            "action_id": f"folder_{folder['id'][:8]}"
        })

    # Add "Create new folder" button
    buttons.append({
        "type": "button",
        "text": {"type": "plain_text", "text": f"➕ Create new: {company_name[:30]}"},
        "value": json.dumps({"pending_id": pending_id, "folder_id": "NEW"}),
        "action_id": "folder_new",
        "style": "primary"
    })

    slack_client.chat_postMessage(
        channel=channel_id,
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"⚠️ *Multiple folders found for `{company_name}`*\nWhich folder should I save the PDF to?"}
            },
            {
                "type": "actions",
                "elements": buttons
            }
        ]
    )


# ── Main VAT processing ───────────────────────────────────────────────────────

def process_vat(response_url, channel_id, vat_raw):
    import requests

    try:
        country_code, vat_number = parse_vat(vat_raw)
    except ValueError as e:
        requests.post(response_url, json={"text": f"❌ Error: {e}"})
        return

    requests.post(response_url, json={"text": f"🔍 Checking {vat_raw} against EU VIES..."})

    try:
        data = check_vies(country_code, vat_number)
    except Exception as e:
        requests.post(response_url, json={"text": f"❌ VIES query failed: {e}"})
        return

    emoji = STATUS_EMOJI.get(data["status"], "❓")
    status = data["status"]

    fields = [
        {"type": "mrkdwn", "text": f"*Original VAT*\n{country_code}{vat_number}"},
        {"type": "mrkdwn", "text": f"*Status*\n{status}"},
    ]
    if data.get("corrected_vat"):
        fields.append({"type": "mrkdwn", "text": f"*⚠️ Corrected VAT*\n{country_code}{data['corrected_vat']}"})
    fields += [
        {"type": "mrkdwn", "text": f"*Company Name*\n{data['name']}"},
        {"type": "mrkdwn", "text": f"*Address*\n{data['address']}"},
        {"type": "mrkdwn", "text": f"*Country*\n{data['country_code']}"},
        {"type": "mrkdwn", "text": f"*Checked On*\n{data['request_date']}"},
    ]

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} VAT Check Result: {status}"}},
        {"type": "section", "fields": fields},
        {"type": "divider"},
    ]
    requests.post(response_url, json={"blocks": blocks, "response_type": "in_channel"})

    if status != "VALID":
        return

    try:
        pdf_bytes = generate_pdf_bytes(data)
        filename = f"VAT_{country_code}{vat_number}_{datetime.date.today()}.pdf"
        company_name = data["name"] if data["name"] != "—" else "_unmatched"

        # Find folder candidates
        try:
            service = get_drive_service()
            all_folders = get_all_customer_folders(service)
            match_type, candidates = find_folder_candidates(all_folders, company_name)

            if match_type in ("exact", "single_fuzzy"):
                # Single clear match — upload automatically
                folder_id = candidates[0]["id"]
                folder_name = candidates[0]["name"]
                post_to_slack_and_drive(channel_id, response_url, pdf_bytes, filename, company_name, folder_id)
                slack_client.chat_postMessage(
                    channel=channel_id,
                    text=f"✅ PDF saved to folder: *{folder_name}*"
                )

            elif match_type == "ambiguous":
                # Multiple matches — ask user to pick
                # First upload PDF to Slack so they can see it while deciding
                slack_client.files_upload_v2(
                    channel=channel_id,
                    filename=filename,
                    content=pdf_bytes,
                    title=f"VAT Verification — {filename}",
                    initial_comment=f"📄 PDF proof for {filename.replace('.pdf','')}"
                )
                ask_user_to_pick_folder(channel_id, response_url, pdf_bytes, filename, company_name, candidates)

            else:
                # No match — create new folder and upload
                folder_id = create_customer_folder(company_name)
                post_to_slack_and_drive(channel_id, response_url, pdf_bytes, filename, company_name, folder_id)
                slack_client.chat_postMessage(
                    channel=channel_id,
                    text=f"📁 New folder created and PDF saved: *{company_name}*"
                )

        except Exception as e:
            # Drive failed — still upload to Slack
            slack_client.files_upload_v2(
                channel=channel_id, filename=filename, content=pdf_bytes,
                title=f"VAT Verification — {filename}",
                initial_comment=f"📄 PDF proof for {filename.replace('.pdf','')}"
            )
            slack_client.chat_postMessage(channel=channel_id, text=f"⚠️ Drive upload failed: {e}")

    except Exception as e:
        requests.post(response_url, json={"text": f"⚠️ PDF generation failed: {e}"})


# ── Flask Routes ──────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/slack/vat", methods=["POST"])
def slack_vat():
    vat_raw = request.form.get("text", "").strip()
    response_url = request.form.get("response_url")
    channel_id = request.form.get("channel_id")

    if not vat_raw:
        return jsonify({
            "response_type": "ephemeral",
            "text": "Usage: `/vat SE663000013801`\nPaste any EU VAT number after the command."
        })

    threading.Thread(target=process_vat, args=(response_url, channel_id, vat_raw)).start()
    return jsonify({"response_type": "ephemeral", "text": f"⏳ Looking up {vat_raw}..."})


@app.route("/slack/actions", methods=["POST"])
def slack_actions():
    """Handle interactive button clicks for folder selection."""
    payload = json.loads(request.form.get("payload", "{}"))
    actions = payload.get("actions", [])
    channel_id = payload.get("channel", {}).get("id")

    for action in actions:
        value = json.loads(action.get("value", "{}"))
        pending_id = value.get("pending_id")
        folder_id = value.get("folder_id")

        upload_data = pending_uploads.pop(pending_id, None)
        if not upload_data:
            slack_client.chat_postMessage(channel=channel_id, text="⚠️ Session expired. Please run the /vat check again.")
            return jsonify({})

        pdf_bytes = upload_data["pdf_bytes"]
        filename = upload_data["filename"]
        company_name = upload_data["company_name"]

        if folder_id == "NEW":
            folder_id = create_customer_folder(company_name)
            folder_name = company_name
        else:
            folder_name = upload_data["candidates"].get(folder_id, "selected folder")

        try:
            drive_link = upload_pdf_to_folder(pdf_bytes, filename, folder_id)
            slack_client.chat_postMessage(
                channel=channel_id,
                text=f"✅ PDF saved to folder *{folder_name}*\n📁 {drive_link}"
            )
        except Exception as e:
            slack_client.chat_postMessage(channel=channel_id, text=f"⚠️ Drive upload failed: {e}")

    return jsonify({})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
