"""Google-like provider boundary: all resource calls require an issued access token."""
from __future__ import annotations
from copy import deepcopy
from urllib.parse import urlsplit, parse_qs
from common import PROVIDERS, Result, error
from google_services.calendar import Calendar
from google_services.oauth import OAuth

class GoogleServices:
    def __init__(self, profile: str):
        self.oauth = OAuth(profile)
        self.calendar = Calendar()
        self.messages = [{'id': 'msg-1', 'subject': 'Team agenda', 'to': 'me@example.com', 'body': 'Discuss roadmap.'}]
        self.files = [{'id': 'file-1', 'name': 'Roadmap.txt', 'mimeType': 'text/plain'}]

    def handle(self, provider: str, method: str, target: str, data: dict, token: str) -> Result:
        parsed = urlsplit(target)
        path = parsed.path
        if provider not in PROVIDERS:
            return error(400, 'unknown_provider')
        if path == '/oauth2.googleapis.com/token' and method == 'POST':
            return self.oauth.refresh(data, provider)
        if not self.oauth.authorize(provider, token):
            return error(401, 'invalid_credentials')
        if provider == 'google-calendar':
            return self.calendar.handle(method, path, data, parse_qs(parsed.query))
        if provider == 'google-drive' and method == 'GET':
            if path == '/drive/v3/about':
                return Result(200, {'user': {'displayName': 'Local User', 'emailAddress': 'me@example.com'}})
            if path == '/drive/v3/files':
                return Result(200, {'files': deepcopy(self.files)})
            if path == '/drive/v3/files/file-1':
                return Result(200, deepcopy(self.files[0]))
        if provider == 'gmail':
            if method == 'GET' and path == '/gmail/v1/users/me/profile':
                return Result(200, {'emailAddress': 'me@example.com', 'messagesTotal': len(self.messages)})
            if method == 'GET' and path == '/gmail/v1/users/me/messages':
                return Result(200, {'messages': deepcopy(self.messages)})
            if method == 'POST' and path == '/gmail/v1/users/me/messages/send':
                if not all(isinstance(data.get(k), str) and data[k] for k in ('to', 'subject', 'body')):
                    return error(400, 'invalid_message')
                message = {k: data[k] for k in ('to', 'subject', 'body')}
                message['id'] = f'msg-{len(self.messages) + 1}'
                self.messages.append(message)
                return Result(200, deepcopy(message))
        if (provider == 'vertex-ai' and method == 'POST'
                and path.startswith('/vertex-ai/v1/models/') and path.endswith(':generateContent')):
            if not isinstance(data.get('prompt'), str):
                return error(400, 'invalid_prompt')
            return Result(200, {'text': 'Local Vertex response: ' + data['prompt']})
        return error(404, 'unknown_resource')
