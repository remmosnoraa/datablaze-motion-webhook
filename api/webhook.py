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
                        motion_status = 'SUCCESS'
                        motion_detail = response_data[:200]
                    except urllib.error.HTTPError as e:
                        motion_status = 'FAILED'
                        motion_detail = f'{e.code}: {e.read().decode("utf-8")[:200]}'

                    # Fan out to the local case management Flask app.
                    # Additive and non-blocking: if Flask is down or the
                    # tunnel is broken, Motion still receives its data.
                    flask_status = self._forward_to_flask(motion_payload)

                    http_code = 200 if motion_status == 'SUCCESS' else 500
                    self.send_response(http_code)
                    self.end_headers()
                    response_msg = (
                        f'Motion: {motion_status} ({motion_detail})\n'
                        f'Flask:  {flask_status}\n\n'
                        f'Payload sent:\n{json.dumps(motion_payload, indent=2)}'
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

        Returns a short human-readable status string. Never raises — Flask
        is a secondary destination and must never break Motion forwarding.
        """
        flask_url = os.environ.get('FLASK_WEBHOOK_URL')
        if not flask_url:
            return 'SKIPPED (FLASK_WEBHOOK_URL not set)'

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
            return f'SUCCESS ({body})'
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8')[:200]
            return f'FAILED {e.code}: {body}'
        except Exception as e:
            return f'FAILED: {type(e).__name__}: {str(e)[:200]}'

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
