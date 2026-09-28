"""The sole behavioral difference between baseline and candidate lives here."""
from urllib.parse import urlsplit
from common import PREFIXES

RESOURCE_RULES = (
    ('calendar.googleapis.com', '/calendar/v3/', 'google-calendar'),
    ('www.googleapis.com', '/drive/v3/', 'google-drive'),
    ('gmail.googleapis.com', '/gmail/v1/', 'gmail'),
    ('aiplatform.googleapis.com', '/vertex-ai/v1/', 'vertex-ai'),
)

def owner(refresh_token: str) -> str | None:
    namespace = refresh_token.split(':', 1)[0]
    return next((p for p, prefix in PREFIXES.items() if prefix == namespace), None)

class Router:
    def __init__(self, mode: str):
        if mode not in ('buggy', 'fixed'):
            raise ValueError('GATEWAY_MODE must be buggy or fixed')
        self.mode = mode

    def route(self, target: str, refresh_token: str = '') -> str | None:
        url = urlsplit(target)
        if url.scheme != 'https' or url.port is not None or url.username or url.password or url.fragment:
            return None
        if url.hostname == 'oauth2.googleapis.com' and url.path == '/token':
            return 'vertex-ai' if self.mode == 'buggy' else owner(refresh_token)
        return next((provider for host, prefix, provider in RESOURCE_RULES
                     if url.hostname == host and url.path.startswith(prefix)), None)
