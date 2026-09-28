"""Credential vault, provider routing, bearer injection, and private audit."""
from __future__ import annotations
from urllib.parse import urlsplit
from common import PROVIDERS, PREFIXES, Result, error, http
from gateway.router import Router, owner
from google_services.oauth import OAuth

class Gateway:
    def __init__(self, mode: str, service_url: str, connected: list[str], audit):
        self.router = Router(mode)
        self.service_url = service_url
        self.connected = set(connected)
        self.audit = audit
        self.access = {p: OAuth.initial(p) for p in PROVIDERS}

    def handle(self, envelope: dict, context: dict) -> Result:
        target, method = envelope.get('target', ''), envelope.get('method', 'GET')
        data = envelope.get('data', {})
        if not isinstance(target, str) or method not in ('GET', 'POST', 'PATCH', 'DELETE') or not isinstance(data, dict):
            return error(400, 'invalid_gateway_request')
        refresh = target == 'https://oauth2.googleapis.com/token'
        token = data.get('refresh_token', '')
        if not isinstance(token, str):
            return error(400, 'invalid_refresh_token')
        # Agent/tool environment passes only a provider-scoped opaque placeholder.
        if token.startswith('onecli-managed:'):
            token = token.removeprefix('onecli-managed:') + ':refresh-token-001'
        try:
            routed = self.router.route(target, token)
        except ValueError:
            return error(400, 'invalid_target')
        intended = owner(token) if refresh else routed
        record = {**context, 'source': 'gateway', 'target': target,
                  'provider_requested': intended, 'routed_provider': routed,
                  'refresh_required': refresh, 'misrouted': bool(intended and routed != intended)}
        if routed is None:
            result = error(400, 'unknown_route')
        elif routed not in self.connected:
            result = error(401, 'app_not_connected', provider=routed)
        else:
            if refresh:
                if method != 'POST':
                    return error(405, 'method_not_allowed')
                body = {**data, 'refresh_token': token}
                # Explicit fake credentials are also accepted for protocol tests.
                for key, value in (('client_id', f'{routed}:client'), ('client_secret', f'{routed}:local-secret')):
                    if body.get(key) == 'onecli-managed':
                        body[key] = value
                path = '/oauth2.googleapis.com/token'
            else:
                body = data
                parsed = urlsplit(target)
                path = parsed.path + ('?' + parsed.query if parsed.query else '')
            result = http(self.service_url, path, method, body if method != 'GET' else None,
                          {'X-Provider': routed, 'Authorization': 'Bearer ' + self.access[routed],
                           'X-Request-ID': context['request_id'], 'X-Session-ID': context['session_id']})
            if refresh and result.ok:
                self.access[routed] = result.body['access_token']
                # Do not return vault material across the environment boundary.
                result = Result(200, {**result.body, 'access_token': 'onecli-managed'})
        record.update(status=result.status, result='accepted' if result.ok else 'rejected')
        if refresh:
            record['refresh_succeeded'] = result.ok
        self.audit(record)
        return result
