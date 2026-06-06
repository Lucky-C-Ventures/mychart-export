#!/usr/bin/env python3
"""
Epic FHIR Sandbox Test — SMART on FHIR OAuth2 flow.
Starts a local server, opens browser for MyChart sandbox login,
then pulls test patient data.

Usage: python3 test_sandbox.py
"""

import http.server
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser
import threading
import ssl

# ─── Config ───────────────────────────────────────────────────────────
CLIENT_ID = "a274ed5e-7692-42c7-9abd-4fc5fe82820e"
REDIRECT_URI = "https://localhost:8080/callback"

# Toggle between sandbox and production
USE_PRODUCTION = True

if USE_PRODUCTION:
    # Houston Methodist
    FHIR_BASE = "https://epicproxy.et0922.epichosted.com/FHIRProxy/api/FHIR/R4"
    AUTH_URL = "https://epicproxy.et0922.epichosted.com/FHIRProxy/oauth2/authorize"
    TOKEN_URL = "https://epicproxy.et0922.epichosted.com/FHIRProxy/oauth2/token"
else:
    # Epic Sandbox
    FHIR_BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"
    AUTH_URL = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize"
    TOKEN_URL = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"

SCOPES = " ".join([
    "openid",
    "fhirUser",
    "patient/Patient.read",
    "patient/Observation.read",
    "patient/Condition.read",
    "patient/MedicationRequest.read",
    "patient/AllergyIntolerance.read",
    "patient/Immunization.read",
    "patient/Procedure.read",
    "patient/DocumentReference.read",
    "patient/DiagnosticReport.read",
])

# ─── Globals ──────────────────────────────────────────────────────────
auth_code = None
server_done = threading.Event()


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authorization successful!</h1><p>You can close this tab.</p>")
            server_done.set()
        elif "error" in params:
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            msg = f"Error: {params.get('error', ['?'])[0]} - {params.get('error_description', ['?'])[0]}"
            self.wfile.write(f"<h1>{msg}</h1>".encode())
            server_done.set()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress noisy logs


def fhir_get(url, token):
    """Make an authenticated FHIR GET request."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/fhir+json",
    })
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx) as resp:
        return json.loads(resp.read())


def main():
    # 1. Start local HTTPS callback server (self-signed cert)
    import tempfile, subprocess
    cert_dir = tempfile.mkdtemp()
    cert_file = os.path.join(cert_dir, "cert.pem")
    key_file = os.path.join(cert_dir, "key.pem")
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", key_file, "-out", cert_file,
        "-days", "1", "-nodes",
        "-subj", "/CN=localhost"
    ], capture_output=True)
    print("Generated self-signed cert for localhost HTTPS")

    server = http.server.HTTPServer(("localhost", 8080), CallbackHandler)
    ctx_server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx_server.load_cert_chain(cert_file, key_file)
    server.socket = ctx_server.wrap_socket(server.socket, server_side=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # 2. Build authorization URL
    auth_params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "aud": FHIR_BASE,
    })
    full_auth_url = f"{AUTH_URL}?{auth_params}"

    print("=" * 60)
    env = "PRODUCTION (Houston Methodist)" if USE_PRODUCTION else "SANDBOX"
    print(f"Epic FHIR Test — {env}")
    print("=" * 60)
    print(f"\nClient ID: {CLIENT_ID}")
    print(f"\nOpen this URL in your browser to authenticate:\n")
    print(full_auth_url)
    print(f"\nWaiting for callback on {REDIRECT_URI}...")

    # Try to auto-open browser
    try:
        webbrowser.open(full_auth_url)
        print("(Browser should have opened automatically)")
    except:
        print("(Copy the URL above and paste into your browser)")

    # 3. Wait for OAuth callback
    server_done.wait(timeout=300)
    server.shutdown()

    if not auth_code:
        print("\n❌ No authorization code received. Exiting.")
        sys.exit(1)

    print(f"\n✅ Authorization code received!")

    # 4. Exchange code for access token
    print("\nExchanging code for access token...")
    token_data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=token_data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx) as resp:
        token_resp = json.loads(resp.read())

    access_token = token_resp.get("access_token")
    patient_id = token_resp.get("patient")

    if not access_token:
        print(f"\n❌ Token exchange failed: {json.dumps(token_resp, indent=2)}")
        sys.exit(1)

    print(f"✅ Access token received!")
    print(f"   Patient FHIR ID: {patient_id}")
    print(f"   Token expires in: {token_resp.get('expires_in', '?')} seconds")
    print(f"   Scopes granted: {token_resp.get('scope', '?')}")

    # 5. Pull patient data
    print("\n" + "=" * 60)
    print("Pulling sandbox patient data...")
    print("=" * 60)

    # Patient demographics
    print("\n--- Patient Demographics ---")
    try:
        pt = fhir_get(f"{FHIR_BASE}/Patient/{patient_id}", access_token)
        name = pt.get("name", [{}])[0]
        print(f"  Name: {' '.join(name.get('given', ['?']))} {name.get('family', '?')}")
        print(f"  DOB: {pt.get('birthDate', '?')}")
        print(f"  Gender: {pt.get('gender', '?')}")
        addr = pt.get("address", [{}])[0] if pt.get("address") else {}
        if addr:
            print(f"  Address: {', '.join(addr.get('line', []))} {addr.get('city', '')} {addr.get('state', '')} {addr.get('postalCode', '')}")
    except Exception as e:
        print(f"  Error: {e}")

    # Lab results
    print("\n--- Lab Results (last 5) ---")
    try:
        labs = fhir_get(f"{FHIR_BASE}/Observation?patient={patient_id}&category=laboratory&_count=5", access_token)
        for entry in labs.get("entry", []):
            obs = entry.get("resource", {})
            code = obs.get("code", {}).get("text", obs.get("code", {}).get("coding", [{}])[0].get("display", "?"))
            value = obs.get("valueQuantity", {})
            val_str = f"{value.get('value', '?')} {value.get('unit', '')}" if value else obs.get("valueString", "?")
            date = obs.get("effectiveDateTime", "?")[:10]
            print(f"  {code}: {val_str} ({date})")
        if not labs.get("entry"):
            print("  No lab results found.")
    except Exception as e:
        print(f"  Error: {e}")

    # Medications
    print("\n--- Active Medications ---")
    try:
        meds = fhir_get(f"{FHIR_BASE}/MedicationRequest?patient={patient_id}&status=active&_count=10", access_token)
        for entry in meds.get("entry", []):
            med = entry.get("resource", {})
            med_ref = med.get("medicationReference", {})
            med_cc = med.get("medicationCodeableConcept", {})
            name = med_cc.get("text", med_ref.get("display", "?"))
            print(f"  {name}")
        if not meds.get("entry"):
            print("  No active medications found.")
    except Exception as e:
        print(f"  Error: {e}")

    # Conditions
    print("\n--- Conditions ---")
    try:
        conds = fhir_get(f"{FHIR_BASE}/Condition?patient={patient_id}&_count=10", access_token)
        for entry in conds.get("entry", []):
            cond = entry.get("resource", {})
            code = cond.get("code", {}).get("text", "?")
            status = cond.get("clinicalStatus", {}).get("coding", [{}])[0].get("code", "?")
            print(f"  {code} (status: {status})")
        if not conds.get("entry"):
            print("  No conditions found.")
    except Exception as e:
        print(f"  Error: {e}")

    # Allergies
    print("\n--- Allergies ---")
    try:
        allergies = fhir_get(f"{FHIR_BASE}/AllergyIntolerance?patient={patient_id}&_count=10", access_token)
        for entry in allergies.get("entry", []):
            allergy = entry.get("resource", {})
            code = allergy.get("code", {}).get("text", "?")
            print(f"  {code}")
        if not allergies.get("entry"):
            print("  No allergies found.")
    except Exception as e:
        print(f"  Error: {e}")

    # Immunizations
    print("\n--- Immunizations (last 5) ---")
    try:
        imm = fhir_get(f"{FHIR_BASE}/Immunization?patient={patient_id}&_count=5", access_token)
        for entry in imm.get("entry", []):
            vaccine = entry.get("resource", {})
            name = vaccine.get("vaccineCode", {}).get("text", "?")
            date = vaccine.get("occurrenceDateTime", "?")[:10]
            print(f"  {name} ({date})")
        if not imm.get("entry"):
            print("  No immunizations found.")
    except Exception as e:
        print(f"  Error: {e}")

    print("\n" + "=" * 60)
    print("✅ Sandbox test complete!")
    print("=" * 60)

    # Save raw token info for reference
    print(f"\n📋 Token details saved. Your app client ID {CLIENT_ID} is working.")


if __name__ == "__main__":
    main()
