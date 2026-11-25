#!/usr/bin/env python3
"""Offline HTTPS/HTTP fake for api.telegram.org and api.deepseek.com.

Routes are chosen from the URL path so the wizard's real code paths run
unchanged: it just points its base URLs at this server.

  /bot<token>/getMe   -> 200 {"ok":true,"result":{"username":...}} for good tokens
                         401 {"ok":false} for tokens containing "bad"
  /user/balance       -> 200 for keys starting with "sk-good", 401 otherwise

A control port lets tests flip modes (e.g. force connection refusal) without
restarting, and records every request path so tests can assert the token
never leaks into output.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

STATE = {
    'mode': 'normal',      # normal | closed
    'requests': [],        # (path, headers.get('Authorization'))
    'lock': threading.Lock(),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):  # keep test output clean
        pass

    def _record(self):
        with STATE['lock']:
            STATE['requests'].append(
                (self.path, self.headers.get('Authorization') or '')
            )

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._record()
        path = self.path.split('?')[0]

        if path == '/__state':
            with STATE['lock']:
                self._send(200, {'mode': STATE['mode'], 'count': len(STATE['requests'])})
            return
        if path == '/__reset':
            with STATE['lock']:
                STATE['requests'] = []
            self._send(200, {'ok': True})
            return
        if path == '/__requests':
            with STATE['lock']:
                self._send(200, {'requests': STATE['requests']})
            return
        if path == '/__mode':
            # mode is carried in the query string: /__mode?m=closed
            query = self.path.split('?', 1)[1] if '?' in self.path else ''
            for part in query.split('&'):
                if part.startswith('m='):
                    STATE['mode'] = part[2:]
            self._send(200, {'mode': STATE['mode']})
            return

        if path.startswith('/bot') and path.endswith('/getMe'):
            token = path[4:-len('/getMe')]
            if 'bad' in token or 'revoked' in token:
                self._send(401, {'ok': False, 'description': 'Unauthorized'})
                return
            if 'boom' in token:
                self._send(500, {'ok': False, 'description': 'Internal'})
                return
            self._send(200, {
                'ok': True,
                'result': {'id': 123456, 'is_bot': True, 'username': 'blade_test_bot'},
            })
            return

        if path == '/user/balance':
            auth = self.headers.get('Authorization') or ''
            key = auth.replace('Bearer ', '').strip()
            if key.startswith('sk-good'):
                self._send(200, {'is_available': True, 'balance_infos': []})
                return
            self._send(401, {'error': {'message': 'invalid key'}})
            return

        self._send(404, {'ok': False, 'description': 'not found'})


def serve(port: int = 0):
    server = HTTPServer(('127.0.0.1', port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


if __name__ == '__main__':
    import sys

    listen_port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    srv = serve(listen_port)
    print(f'listening on 127.0.0.1:{srv.server_address[1]}', flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
