"""Deterministic OAuth issuer. Only the gateway holds client credentials."""
from __future__ import annotations
from dataclasses import dataclass
from common import PROVIDERS, PREFIXES, Result, error

@dataclass
class AccessToken:
    provider: str
    remaining_uses: int | None

class OAuth:
    def __init__(self, profile: str):
        self.tokens: dict[str, AccessToken] = {}
        self.generations = {p: 0 for p in PROVIDERS}
        for provider in PROVIDERS:
            uses = 0 if profile == 'expired' else None
            if profile == 'expire_after_lookup' and provider == 'google-calendar':
                uses = 1
            self.tokens[self.initial(provider)] = AccessToken(provider, uses)

    @staticmethod
    def initial(provider: str) -> str:
        return f'{PREFIXES[provider]}:access-000'

    def authorize(self, provider: str, token: str) -> bool:
        item = self.tokens.get(token)
        if item is None or item.provider != provider or item.remaining_uses == 0:
            return False
        if item.remaining_uses is not None:
            item.remaining_uses -= 1
        return True

    def refresh(self, form: dict, provider: str) -> Result:
        if form.get('grant_type') != 'refresh_token':
            return error(400, 'unsupported_grant_type')
        if form.get('client_id') != f'{provider}:client' or form.get('client_secret') != f'{provider}:local-secret':
            return error(401, 'invalid_client')
        if form.get('refresh_token') != f'{PREFIXES[provider]}:refresh-token-001':
            return error(400, 'invalid_grant')
        if form.get('scope') not in (None, '', provider):
            return error(400, 'invalid_scope')
        self.generations[provider] += 1
        token = f'{PREFIXES[provider]}:access-{self.generations[provider]:03d}'
        self.tokens[token] = AccessToken(provider, None)
        return Result(200, {'access_token': token, 'token_type': 'Bearer',
                            'expires_in': 3600, 'scope': provider})
