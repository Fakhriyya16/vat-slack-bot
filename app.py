"""
VAT Checker Slack Bot
---------------------
Slack slash command: /vat SE663000013801
Returns VALID/INVALID/UNAVAILABLE + uploads a PDF proof.
"""

import os
import io
import datetime
import threading
from flask import Flask, request, jsonify
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

app = Flask(__name__)
slack_client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])


# ── VAT Parsing ───────────────────────────────────────────────────────────────

def parse_vat(raw: str):
    raw = raw.strip().upper().replace(" ", "").replace("-", "")
    if len(raw) < 4:
        raise ValueError("VAT number too short.")
    return raw[:2], raw[2:]


# ── VIES Query ────────────────────────────────────────────────────────────────

def check_vies(country_code: str, vat_number: str) -> dict:
    import requests as req
    import xml.etree.ElementTree as ET

    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:urn="urn:ec.europa.eu:taxud:vies:services:checkVat:types">
  <soapenv:Body>
    <urn:checkVat>
      <urn:countryCode>{country_code}</urn:countryCode>
      <urn:vatNumber>{vat_number}</urn:vatNumber>
    </urn:checkVat>
  </soapenv:Body>
</soapenv:Envelope>"""

    base = {"country_code": country_code, "vat_number": vat_number,
            "name": "—", "address": "—",
            "request_date": str(datetime.date.today())}
    try:
        response = req.post(
            "https://ec.europa.eu/taxation_customs/vies/services/checkVatService",
            data=envelope,
            headers={"Content-Type": "text/xml; charset=UTF-8"},
            timeout=10
        )
        ns = {"ns": "urn:ec.europa.eu:taxud:vies:services:checkVat:types"}
        root = ET.fromstring(response.text)

        # Check for fault
        fault = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Fault")
        if fault is not None:
            faultstring = fault.findtext("faultstring", "").upper()
            if "INVALID" in faultstring:
                return {**base, "status": "INVALID"}
            return {**base, "status": "UNAVAILABLE"}

        valid_el = root.find(".//ns:valid", ns)
        if valid_el is None:
            return {**base, "status": "UNAVAILABLE"}

        valid = valid_el.text.strip().lower() == "true"
        name = (root.findtext(".//ns:name", "—", ns) or "—").strip()
        address = (root.findtext(".//ns:address", "—", ns) or "—").strip().replace("\n", ", ")
        req_date = root.findtext(".//ns:requestDate", str(datetime.date.today()), ns)

        return {
            "status":       "VALID" if valid else "INVALID",
            "country_code": country_code,
            "vat_number":   vat_number,
            "name":         name,
            "address":      address,
            "request_date": str(req_date),
        }
    except Exception as e:
        err = str(e).upper()
        if "INVALID" in err:
            return {**base, "status": "INVALID"}
        return {**base, "status": "UNAVAILABLE"}


# ── PDF Generation ────────────────────────────────────────────────────────────

STATUS_COLORS = {
    "VALID":       (0.07, 0.53, 0.32),
    "INVALID":     (0.80, 0.15, 0.15),
    "UNAVAILABLE": (0.90, 0.55, 0.00),
}

def generate_pdf_bytes(data: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    W, H = A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    status = data["status"]
    r, g, b = STATUS_COLORS.get(status, (0.3, 0.3, 0.3))

    # Header
    c.setFillColorRGB(0.10, 0.14, 0.24)
    c.rect(0, H - 55*mm, W, 55*mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(20*mm, H - 28*mm, "EU VAT Verification Report")
    c.setFont("Helvetica", 11)
    c.drawString(20*mm, H - 40*mm, "Source: European Commission — VIES System")
    c.drawString(20*mm, H - 48*mm,
        f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Status badge
    badge_y = H - 80*mm
    c.setFillColorRGB(r, g, b)
    c.roundRect(20*mm, badge_y, 60*mm, 16*mm, 4*mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(50*mm, badge_y + 5*mm, status)

    # Details
    fields = [
        ("VAT Number",   f"{data['country_code']}{data['vat_number']}"),
        ("Country Code", data["country_code"]),
        ("Company Name", data["name"]),
        ("Address",      data["address"]),
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
        c.setFillColorRGB(0.10, 0.10, 0.10)
        c.setFont("Helvetica", 10)
        c.drawString(col2, y + 5*mm, str(value)[:80])
        y -= row_h

    # Footer
    c.setFillColorRGB(0.10, 0.14, 0.24)
    c.rect(0, 0, W, 18*mm, fill=1, stroke=0)
    c.setFillColorRGB(0.7, 0.7, 0.7)
    c.setFont("Helvetica", 8)
    c.drawString(20*mm, 7*mm,
        "Automatically generated using the EU VIES VAT validation service.")
    c.drawRightString(W - 20*mm, 7*mm, "Page 1 of 1")

    c.save()
    buf.seek(0)
    return buf.read()


# ── Slack Response (runs in background thread) ────────────────────────────────

STATUS_EMOJI = {
    "VALID": "✅",
    "INVALID": "❌",
    "UNAVAILABLE": "⚠️",
}

def process_vat(response_url: str, channel_id: str, vat_raw: str):
    import requests

    try:
        country_code, vat_number = parse_vat(vat_raw)
    except ValueError as e:
        requests.post(response_url, json={"text": f"❌ Error: {e}"})
        return

    # Post "checking..." message immediately
    requests.post(response_url, json={
        "text": f"🔍 Checking `{vat_raw}` against EU VIES..."
    })

    try:
        data = check_vies(country_code, vat_number)
    except Exception as e:
        requests.post(response_url, json={"text": f"❌ VIES query failed: {e}"})
        return

    emoji = STATUS_EMOJI.get(data["status"], "❓")
    status = data["status"]

    # Post result as a rich Slack block
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                     "text": f"{emoji} VAT Check Result: {status}"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*VAT Number*\n`{country_code}{vat_number}`"},
                {"type": "mrkdwn", "text": f"*Status*\n{status}"},
                {"type": "mrkdwn", "text": f"*Company Name*\n{data['name']}"},
                {"type": "mrkdwn", "text": f"*Address*\n{data['address']}"},
                {"type": "mrkdwn", "text": f"*Country*\n{data['country_code']}"},
                {"type": "mrkdwn", "text": f"*Checked On*\n{data['request_date']}"},
            ]
        },
        {"type": "divider"}
    ]

    requests.post(response_url, json={"blocks": blocks, "response_type": "in_channel"})

    # Generate and upload PDF
    try:
        pdf_bytes = generate_pdf_bytes(data)
        filename = f"VAT_{country_code}{vat_number}_{datetime.date.today()}.pdf"
        slack_client.files_upload_v2(
            channel=channel_id,
            filename=filename,
            content=pdf_bytes,
            title=f"VAT Verification — {country_code}{vat_number}",
            initial_comment=f"📄 PDF proof for `{country_code}{vat_number}`"
        )
    except Exception as e:
        requests.post(response_url, json={"text": f"⚠️ PDF upload failed: {e}"})


# ── Flask Routes ──────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/slack/vat", methods=["POST"])
def slack_vat():
    # Slack expects a response within 3 seconds — process in background
    vat_raw      = request.form.get("text", "").strip()
    response_url = request.form.get("response_url")
    channel_id   = request.form.get("channel_id")

    if not vat_raw:
        return jsonify({
            "response_type": "ephemeral",
            "text": "Usage: `/vat SE663000013801`\nPaste any EU VAT number after the command."
        })

    thread = threading.Thread(
        target=process_vat,
        args=(response_url, channel_id, vat_raw)
    )
    thread.start()

    return jsonify({
        "response_type": "ephemeral",
        "text": f"⏳ Looking up `{vat_raw}`..."
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
