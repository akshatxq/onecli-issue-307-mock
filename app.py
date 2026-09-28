"""Five HTTP listeners; only public/verification/admin are published by Docker."""
from __future__ import annotations
import argparse
import json
import logging
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from common import PROVIDERS, Result, error
from agent.environment import Environment
from agent.service import Agent
from gateway.service import Gateway
from google_services.service import GoogleServices
from verification.service import calendar_state

LOG = logging.getLogger('reference')
PROFILES = ('expire_after_lookup', 'valid', 'expired')

class Runtime:
    def __init__(self, mode: str = 'buggy', host: str = '127.0.0.1', ports=(8000, 8001, 8002, 8003, 8004)):
        if mode not in ('buggy', 'fixed'):
            raise ValueError('Invalid gateway mode')
        self.mode, self.host = mode, host
        self.scenario_lock = threading.RLock()
        self.gateway_lock = threading.RLock()
        self.service_lock = threading.RLock()
        self.audit_lock = threading.RLock()
        self.audit: list[dict] = []
        self.sequence = 0
        self.servers = []
        self.threads = []
        self.urls = {}
        try:
            for role, port in zip(('agent', 'verification', 'admin', 'gateway', 'services'), ports):
                # Internal interfaces always bind loopback, even in Docker.
                bind = '127.0.0.1' if role in ('gateway', 'services') else host
                server = ThreadingHTTPServer((bind, port), self.handler(role))
                server.daemon_threads = True
                self.servers.append(server)
                self.urls[role] = f'http://127.0.0.1:{server.server_port}'
            self.reset({})
        except Exception:
            for server in self.servers:
                server.server_close()
            raise

    def append_audit(self, record: dict):
        with self.audit_lock:
            item = {'sequence': len(self.audit) + 1, **record}
            self.audit.append(item)
            LOG.info(json.dumps(item, sort_keys=True))

    def reset(self, options: dict) -> Result:
        if set(options) - {'token_profile', 'connected'}:
            return error(400, 'invalid_reset_option')
        profile = options.get('token_profile', 'expire_after_lookup')
        connected = options.get('connected', list(PROVIDERS[:3]))
        if (profile not in PROFILES or not isinstance(connected, list)
                or any(p not in PROVIDERS for p in connected)):
            return error(400, 'invalid_reset_options')
        # Lock order matches scenario -> gateway -> service request flow.
        with self.scenario_lock, self.gateway_lock, self.service_lock, self.audit_lock:
            self.audit = []
            self.sequence = 0
            self.manual_sequence = 0
            self.services = GoogleServices(profile)
            self.gateway = Gateway(self.mode, self.urls['services'], connected, self.append_audit)
            self.agent = Agent(Environment(self.urls['gateway']))
        return Result(200, {'reset': True})

    def dispatch(self, role: str, method: str, path: str, body: dict, headers) -> Result:
        route = urlsplit(path).path
        if role == 'agent':
            if route != '/agent/chat' or method != 'POST':
                return error(404, 'not_found')
            if (set(body) != {'session_id', 'message'} or
                    any(not isinstance(body[k], str) or not body[k].strip() for k in ('session_id', 'message')) or
                    len(body['session_id']) > 128 or len(body['message']) > 4096):
                return error(400, 'invalid_chat_request')
            with self.scenario_lock:
                self.sequence += 1
                context = {'request_id': f'req-{self.sequence:06d}', 'session_id': body['session_id']}
                self.append_audit({**context, 'source': 'agent', 'result': 'started'})
                reply = self.agent.chat(body['session_id'], body['message'], context)
                self.append_audit({**context, 'source': 'agent', 'result': 'completed'})
                return Result(200, {'session_id': body['session_id'], 'reply': reply})
        if role == 'verification':
            if method == 'GET' and route == '/verification/calendar':
                with self.scenario_lock, self.service_lock:
                    return Result(200, calendar_state(self.services.calendar))
            return error(404, 'not_found')
        if role == 'admin':
            if method == 'POST' and route == '/_debug/reset':
                return self.reset(body)
            if method == 'GET':
                if route == '/_debug/health':
                    return Result(200, {'status': 'ok', 'components': list(self.urls)})
                if route == '/_debug/mode':
                    return Result(200, {'mode': self.mode, 'version': 'reference-1.23.0-' + self.mode})
                if route == '/_debug/audit':
                    with self.scenario_lock, self.audit_lock:
                        return Result(200, {'records': list(self.audit)})
                if route == '/_debug/calendar/state':
                    with self.scenario_lock, self.service_lock:
                        return Result(200, self.services.calendar.snapshot())
            # Developer curl reproduction enters the SAME gateway.
            if route == '/oauth2.googleapis.com/token' and method == 'POST':
                return self.gateway_dispatch({'target': 'https://oauth2.googleapis.com/token', 'method': 'POST', 'data': body}, headers)
            if route == '/_debug/proxy' and method == 'POST':
                return self.gateway_dispatch(body, headers)
            return error(404, 'not_found')
        if role == 'gateway':
            if route == '/proxy' and method == 'POST':
                return self.gateway_dispatch(body, headers)
            return error(404, 'not_found')
        if role == 'services':
            context = {'request_id': headers.get('X-Request-ID', 'manual'),
                       'session_id': headers.get('X-Session-ID', 'manual')}
            with self.service_lock:
                result = self.services.handle(headers.get('X-Provider', ''), method, path, body,
                                               headers.get('Authorization', '').removeprefix('Bearer '))
                self.append_audit({**context, 'source': 'oauth' if route == '/oauth2.googleapis.com/token' else 'provider',
                                   'target': route, 'provider_requested': headers.get('X-Provider'), 'status': result.status})
                return result
        return error(404, 'not_found')

    def gateway_dispatch(self, body, headers):
        with self.gateway_lock:
            self.manual_sequence += 1
            context = {'request_id': headers.get('X-Request-ID', f'manual-{self.manual_sequence:06d}'),
                       'session_id': headers.get('X-Session-ID', 'manual')}
            return self.gateway.handle(body, context)

    def handler(self, role):
        runtime = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self): self.respond()
            def do_POST(self): self.respond()
            def do_PATCH(self): self.respond()
            def do_DELETE(self): self.respond()
            def log_message(self, *args): pass
            def respond(self):
                try:
                    self.connection.settimeout(10)
                    length = int(self.headers.get('Content-Length', '0'))
                    if length < 0 or length > 65536:
                        result = error(413, 'body_too_large')
                    else:
                        raw = self.rfile.read(length)
                        if self.headers.get_content_type() == 'application/x-www-form-urlencoded':
                            fields = parse_qs(raw.decode(), keep_blank_values=True)
                            if any(len(v) != 1 for v in fields.values()):
                                raise ValueError('Duplicate form fields')
                            body = {k: v[0] for k, v in fields.items()}
                        else:
                            body = json.loads(raw) if raw else {}
                        if not isinstance(body, dict):
                            raise ValueError('Object required')
                        result = runtime.dispatch(role, self.command, self.path, body, self.headers)
                except (ValueError, UnicodeDecodeError):
                    result = error(400, 'invalid_request')
                except Exception:
                    LOG.exception('Unhandled request failure')
                    result = error(500, 'internal_error')
                encoded = json.dumps(result.body, sort_keys=True).encode()
                self.send_response(result.status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(encoded)
        return Handler

    def start(self):
        for server in self.servers:
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
            thread.start()
            self.threads.append(thread)
        return self

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)

    def __enter__(self): return self.start()
    def __exit__(self, *args): self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('buggy', 'fixed'), default=os.getenv('GATEWAY_MODE', 'buggy'))
    parser.add_argument('--host', default=os.getenv('BIND_HOST', '127.0.0.1'))
    parser.add_argument('--base-port', type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with Runtime(args.mode, args.host, tuple(range(args.base_port, args.base_port + 5))) as runtime:
        LOG.info(json.dumps({'event': 'ready', 'listeners': runtime.urls}))
        stop.wait()

if __name__ == '__main__':
    main()
