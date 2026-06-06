#!/usr/bin/env python3
"""
People Registry HTTP API — stdlib only, no external dependencies.
Port: 5100
Run: PYTHONPATH=/home/billyomes/watson python3 jobs/people/server.py
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from jobs.people.api import (
    people_list, people_get, people_create, people_update, people_delete,
    congregation_list, congregation_get, congregation_create,
    congregation_update, congregation_delete, congregation_search,
)

PORT = 5100

_CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
}


class Handler(BaseHTTPRequestHandler):

    # ── Response helpers ───────────────────────────────────────

    def _send(self, status: int, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        for k, v in _CORS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get('Content-Length', 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    def _parse(self):
        """Return (path_parts, query_string_dict)."""
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split('/') if p]
        qs = parse_qs(parsed.query)
        return parts, qs

    def _result(self, result, created=False):
        """Dispatch an api.py result to the right status code."""
        if isinstance(result, dict) and 'error' in result:
            status = 404 if result['error'] == 'Not found' else 500
            self._send(status, result)
        else:
            self._send(201 if created else 200, result)

    # ── CORS preflight ─────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in _CORS.items():
            self.send_header(k, v)
        self.end_headers()

    # ── GET ────────────────────────────────────────────────────

    def do_GET(self):
        parts, qs = self._parse()
        try:
            if parts == ['health']:
                self._send(200, {'status': 'ok'})

            elif parts == ['people']:
                self._send(200, people_list())

            elif len(parts) == 2 and parts[0] == 'people':
                self._result(people_get(int(parts[1])))

            elif parts == ['congregation']:
                self._send(200, congregation_list())

            # /congregation/search must be checked before /congregation/<id>
            elif len(parts) == 2 and parts[0] == 'congregation' and parts[1] == 'search':
                name = qs.get('name', [''])[0]
                self._send(200, congregation_search(name))

            elif len(parts) == 2 and parts[0] == 'congregation':
                self._result(congregation_get(int(parts[1])))

            else:
                self._send(404, {'error': 'Not found'})

        except (ValueError, IndexError):
            self._send(400, {'error': 'Invalid ID'})
        except Exception as e:
            self._send(500, {'error': str(e)})

    # ── POST ───────────────────────────────────────────────────

    def do_POST(self):
        parts, _ = self._parse()
        try:
            data = self._read_json()

            if parts == ['people']:
                if not data.get('name', '').strip():
                    self._send(400, {'error': 'name is required'})
                    return
                self._result(people_create(data), created=True)

            elif parts == ['congregation']:
                if not data.get('name', '').strip():
                    self._send(400, {'error': 'name is required'})
                    return
                self._result(congregation_create(data), created=True)

            else:
                self._send(404, {'error': 'Not found'})

        except json.JSONDecodeError:
            self._send(400, {'error': 'Invalid JSON'})
        except Exception as e:
            self._send(500, {'error': str(e)})

    # ── PUT ────────────────────────────────────────────────────

    def do_PUT(self):
        parts, _ = self._parse()
        try:
            data = self._read_json()

            if len(parts) == 2 and parts[0] == 'people':
                self._result(people_update(int(parts[1]), data))

            elif len(parts) == 2 and parts[0] == 'congregation':
                self._result(congregation_update(int(parts[1]), data))

            else:
                self._send(404, {'error': 'Not found'})

        except (ValueError, IndexError):
            self._send(400, {'error': 'Invalid ID'})
        except json.JSONDecodeError:
            self._send(400, {'error': 'Invalid JSON'})
        except Exception as e:
            self._send(500, {'error': str(e)})

    # ── DELETE ─────────────────────────────────────────────────

    def do_DELETE(self):
        parts, _ = self._parse()
        try:
            if len(parts) == 2 and parts[0] == 'people':
                self._result(people_delete(int(parts[1])))

            elif len(parts) == 2 and parts[0] == 'congregation':
                self._result(congregation_delete(int(parts[1])))

            else:
                self._send(404, {'error': 'Not found'})

        except (ValueError, IndexError):
            self._send(400, {'error': 'Invalid ID'})
        except Exception as e:
            self._send(500, {'error': str(e)})

    # ── Logging ────────────────────────────────────────────────

    def log_message(self, fmt, *args):
        print(f'[people-api] {self.address_string()} {fmt % args}', file=sys.stdout)


def run() -> str:
    """Return a summary of the People Registry contact count."""
    try:
        from jobs.people.api import people_list
        contacts = people_list()
        return f"People Registry: {len(contacts)} contacts on file."
    except Exception as exc:
        return f"People Registry unavailable: {exc}"


if __name__ == '__main__':
    server = HTTPServer(('', PORT), Handler)
    print(f'[people-api] Listening on port {PORT}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('[people-api] Shutting down.')
        server.server_close()
