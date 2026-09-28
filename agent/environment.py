"""Restricted tool transport. No vault, service state, or debug interface is exposed."""
from common import HOSTS, PREFIXES, Result, error, http

class Environment:
    def __init__(self, gateway_url: str):
        self.gateway_url = gateway_url

    def call(self, provider: str, method: str, path: str, data: dict, context: dict) -> Result:
        if provider not in HOSTS or not path.startswith(('/calendar/v3/', '/drive/v3/', '/gmail/v1/', '/vertex-ai/v1/')):
            return error(403, 'tool_not_allowed')
        headers = {'X-Request-ID': context['request_id'], 'X-Session-ID': context['session_id']}
        envelope = {'target': f'https://{HOSTS[provider]}{path}', 'method': method, 'data': data}
        result = http(self.gateway_url, '/proxy', 'POST', envelope, headers)
        if result.status != 401:
            return result
        form = {'client_id': 'onecli-managed', 'client_secret': 'onecli-managed',
                'refresh_token': f'onecli-managed:{PREFIXES[provider]}',
                'grant_type': 'refresh_token', 'scope': provider}
        # Two bounded refresh attempts. Replay the resource only after successful refresh.
        for _ in range(2):
            refreshed = http(self.gateway_url, '/proxy', 'POST',
                             {'target': 'https://oauth2.googleapis.com/token', 'method': 'POST', 'data': form}, headers)
            if refreshed.ok:
                return http(self.gateway_url, '/proxy', 'POST', envelope, headers)
        return error(401, 'tool_credentials_unavailable')
