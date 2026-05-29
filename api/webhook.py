from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.error
import os

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            # Read webhook data from DataBlaze
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            webhook_data = json.loads(post_data.decode('utf-8'))

            # Only process row updates
            if webhook_data.get('event_type') == 'rows.updated' and webhook_data.get('items'):
                client = webhook_data['items'][0]

                # "Add to Motion" is the DataBlaze intake-trigger checkbox. The
                # name is retained for backward-compat with the existing DataBlaze
                # column; Motion is no longer a destination. The Motion fan-out was
                # removed 2026-05-29: its agent-workflow webhook had been retired
                # during the migration off Motion, and the Motion call had no
                # timeout, so a hung Motion request was timing out the whole
                # function and dropping the intake before it reached Flask.
                if client.get('Add to Motion') == True:
                    # Clean and flatten the row, then wrap it in the items array
                    # the Flask intake expects (same shape Flask already ingests).
                    cleaned_data = self._clean_client_data(client)
                    payload = {"items": [cleaned_data]}

                    # The local Flask case manager is now the sole destination,
                    # so its result drives the status reported back to DataBlaze.
                    flask_ok, flask_detail = self._forward_to_flask(payload)

                    http_code = 200 if flask_ok else 500
                    self.send_response(http_code)
                    self.end_headers()
                    response_msg = (
                        f'Flask: {flask_detail}\n\n'
                        f'Payload sent:\n{json.dumps(payload, indent=2)}'
                    )
                    self.wfile.write(response_msg.encode())

                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'SKIPPED: Add to Motion not checked')
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'IGNORED: Not a row update event')

        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f'ERROR: {str(e)}'.encode())

    def _forward_to_flask(self, payload):
        """POST the cleaned payload to the local Flask case management app.

        Returns (ok, detail). ok is True only when Flask accepts the intake
        (HTTP 2xx); it drives the status the relay reports back to DataBlaze.
        Never raises.
        """
        flask_url = os.environ.get('FLASK_WEBHOOK_URL')
        if not flask_url:
            return (False, 'MISCONFIGURED (FLASK_WEBHOOK_URL not set)')

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'DataBlaze-Motion-Webhook/1.0',
            'Accept': 'application/json',
        }
        api_key = os.environ.get('FLASK_WEBHOOK_API_KEY')
        if api_key:
            headers['X-API-Key'] = api_key

        try:
            req = urllib.request.Request(
                flask_url,
                data=json.dumps(payload).encode('utf-8'),
                headers=headers,
            )
            response = urllib.request.urlopen(req, timeout=5)
            body = response.read().decode('utf-8')[:200]
            return (True, f'SUCCESS ({body})')
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8')[:200]
            return (False, f'FAILED {e.code}: {body}')
        except Exception as e:
            return (False, f'FAILED: {type(e).__name__}: {str(e)[:200]}')

    def _clean_client_data(self, client):
        """Extract and flatten relevant fields for Motion"""
        def get_value(field):
            """Extract value from nested objects or return as-is"""
            if field is None:
                return None
            if isinstance(field, dict) and 'value' in field:
                return field['value']
            return field

        # Build cleaned data with only relevant fields
        cleaned = {
            "id": client.get('id'),
            "Last Name": client.get('Last Name') or '',
            "First Name": client.get('First Name') or '',
            "LS File": client.get('LS File') or '',
            "LS Email": client.get('LS Email') or '',
            "Phone #": client.get('Phone #') or '',
            "Email": client.get('Email') or '',
            "Open Date": client.get('Open Date'),
            "Assignment Description": client.get('Assignment Description') or ''
        }

        # Add CASEWORK if it exists, flattening it
        casework_value = get_value(client.get('CASEWORK'))
        if casework_value:
            cleaned["CASEWORK"] = casework_value

        # Add optional fields only if they exist, flattening nested objects
        optional_fields = [
            'Appeal Progression',
            'Referee Name',
            'Docket No(s).',
            'Decision Date',
            'Hearing Date/Time',
            'RD Section(s) of Law',
            'SSN Last 4',
            'Web Sign'
        ]

        for field in optional_fields:
            value = get_value(client.get(field))
            if value is not None and value != '':
                cleaned[field] = value

        return cleaned
