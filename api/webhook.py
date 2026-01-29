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

                # Only forward if "Add to Motion" is checked
                if client.get('Add to Motion') == True:
                    # Clean and flatten the data for Motion
                    cleaned_data = self._clean_client_data(client)

                    # Wrap in items array to match Motion's filter expectation
                    motion_payload = {
                        "items": [cleaned_data]
                    }

                    # Forward to Motion's webhook
                    motion_url = os.environ.get('MOTION_WEBHOOK_URL')
                    if not motion_url:
                        raise ValueError("MOTION_WEBHOOK_URL environment variable not set")

                    req = urllib.request.Request(
                        motion_url,
                        data=json.dumps(motion_payload).encode('utf-8'),
                        headers={
                            'Content-Type': 'application/json',
                            'User-Agent': 'DataBlaze-Motion-Webhook/1.0',
                            'Accept': 'application/json'
                        }
                    )

                    try:
                        response = urllib.request.urlopen(req)
                        response_data = response.read().decode('utf-8')

                        self.send_response(200)
                        self.end_headers()
                        # Include the payload in the response for debugging
                        response_msg = f'SUCCESS: Forwarded to Motion - {cleaned_data.get("First Name")} {cleaned_data.get("Last Name")}\n\nPayload sent:\n{json.dumps(motion_payload, indent=2)}'
                        self.wfile.write(response_msg.encode())

                    except urllib.error.HTTPError as e:
                        error_body = e.read().decode('utf-8')
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(f'ERROR: Motion webhook failed - {e.code}: {error_body}'.encode())

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
