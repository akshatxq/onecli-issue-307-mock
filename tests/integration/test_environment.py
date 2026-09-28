import json
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from app import Runtime
from common import http, PROVIDERS, HOSTS, PREFIXES
from scripts.benchmark import MESSAGE

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtimes = {m: Runtime(m, ports=(0, 0, 0, 0, 0)).start() for m in ('buggy', 'fixed')}
    @classmethod
    def tearDownClass(cls):
        for runtime in cls.runtimes.values(): runtime.close()
    def setUp(self):
        for runtime in self.runtimes.values(): self.reset(runtime)
    def reset(self, runtime, **options):
        self.assertEqual(http(runtime.urls['admin'], '/_debug/reset', 'POST', options).status, 200)
    def chat(self, runtime, message, session='test'):
        result = http(runtime.urls['agent'], '/agent/chat', 'POST', {'session_id': session, 'message': message})
        self.assertEqual(result.status, 200, result.body)
        self.assertEqual(set(result.body), {'session_id', 'reply'})
        return result.body['reply']
    def state(self, runtime):
        return http(runtime.urls['verification'], '/verification/calendar').body['events']
    def proxy(self, runtime, provider, path, method='GET', data=None):
        return http(runtime.urls['admin'], '/_debug/proxy', 'POST',
                    {'target': 'https://' + HOSTS[provider] + path, 'method': method, 'data': data or {}})
    def refresh(self, runtime, provider, **overrides):
        return http(runtime.urls['admin'], '/oauth2.googleapis.com/token', 'POST',
                    {'client_id': 'onecli-managed', 'client_secret': 'onecli-managed', 'grant_type': 'refresh_token',
                     'refresh_token': 'onecli-managed:' + PREFIXES[provider], 'scope': provider, **overrides})
    def test_exact_regression_and_audit(self):
        for mode, runtime in self.runtimes.items():
            self.assertIn('Done', self.chat(runtime, MESSAGE))
            event = self.state(runtime)[0]
            self.assertEqual((event['day'], event['time']), ('Monday', '10:00') if mode == 'buggy' else ('Wednesday', '15:00'))
            records = http(runtime.urls['admin'], '/_debug/audit').body['records']
            refresh = [r for r in records if r.get('refresh_required')]
            self.assertEqual(len(refresh), 2 if mode == 'buggy' else 1)
            self.assertTrue(all(r['routed_provider'] == ('vertex-ai' if mode == 'buggy' else 'google-calendar') for r in refresh))
            self.assertEqual({r['request_id'] for r in records}, {'req-000001'})
            self.assertEqual({r['session_id'] for r in records}, {'test'})
            self.assertTrue({'agent', 'gateway', 'provider'} <= {r['source'] for r in records})
            self.assertEqual(any(r['source'] == 'oauth' for r in records), mode == 'fixed')
            self.assertNotIn('local-secret', json.dumps(records))
            self.assertNotIn('refresh-token-001', json.dumps(records))
    def test_valid_resources(self):
        for runtime in self.runtimes.values():
            self.reset(runtime, token_profile='valid', connected=list(PROVIDERS))
            for p, path, method, data in (
                ('google-calendar', '/calendar/v3/events', 'GET', {}), ('google-drive', '/drive/v3/about?fields=user', 'GET', {}),
                ('google-drive', '/drive/v3/files/file-1', 'GET', {}), ('gmail', '/gmail/v1/users/me/profile', 'GET', {}),
                ('gmail', '/gmail/v1/users/me/messages/send', 'POST', {'to': 'a@example.com', 'subject': 'Hello', 'body': 'Local'}),
                ('vertex-ai', '/vertex-ai/v1/models/local:generateContent', 'POST', {'prompt': 'Hello'})):
                self.assertTrue(self.proxy(runtime, p, path, method, data).ok)
    def test_all_refreshes_and_expired_resources(self):
        for mode, runtime in self.runtimes.items():
            for provider, path in zip(PROVIDERS, ('/calendar/v3/events', '/drive/v3/files', '/gmail/v1/users/me/messages', '/vertex-ai/v1/models/local:generateContent')):
                self.reset(runtime, token_profile='expired', connected=list(PROVIDERS[:3]) if provider != 'vertex-ai' else list(PROVIDERS))
                method = 'POST' if provider == 'vertex-ai' else 'GET'
                self.assertEqual(self.proxy(runtime, provider, path, method, {'prompt': 'Hello'}).status, 401)
                result = self.refresh(runtime, provider)
                success = mode == 'fixed' or provider == 'vertex-ai'
                self.assertEqual(result.ok, success)
                if success:
                    self.assertEqual(result.body['access_token'], 'onecli-managed')
                    self.assertTrue(self.proxy(runtime, provider, path, method, {'prompt': 'Hello'}).ok)
                else: self.assertEqual(result.body, {'error': 'app_not_connected', 'provider': 'vertex-ai'})
    def test_invalid_refresh_no_fallback(self):
        runtime = self.runtimes['fixed']
        self.assertEqual(self.refresh(runtime, 'google-calendar', refresh_token='gc:wrong').body['error'], 'invalid_grant')
        self.assertEqual(self.refresh(runtime, 'google-calendar', refresh_token='unknown').body['error'], 'unknown_route')
        runtime = self.runtimes['buggy']
        self.reset(runtime, connected=list(PROVIDERS))
        self.assertEqual(self.refresh(runtime, 'google-calendar').body['error'], 'invalid_grant')
    def test_multi_turn(self):
        sequences = (("What's on my calendar Monday?", 'Move that to Wednesday at 3.'),
                     ('Move my Team Meeting.', 'Wednesday at 3 PM.'),
                     ('Move my Team Meeting to Wednesday at 3.', 'Actually make it 4 PM instead.'))
        for mode, runtime in self.runtimes.items():
            for index, messages in enumerate(sequences):
                self.reset(runtime)
                replies = [self.chat(runtime, message) for message in messages]
                self.assertIn('Done', replies[-1])
                event = self.state(runtime)[0]
                self.assertEqual((event['day'], event['time']), ('Monday', '10:00') if mode == 'buggy' else ('Wednesday', '16:00' if index == 2 else '15:00'))
    def test_workflows(self):
        for runtime in self.runtimes.values():
            self.reset(runtime, token_profile='valid')
            self.assertIn('Team Meeting', self.chat(runtime, 'List calendar events Monday.'))
            self.assertIn('Done', self.chat(runtime, 'Create "Lunch" on Friday at 12 PM.'))
            self.assertIn('Lunch', self.chat(runtime, 'Find Lunch.'))
            self.assertIn('busy', self.chat(runtime, 'Am I available Friday at 12 PM?'))
            self.assertIn('available', self.chat(runtime, 'Am I available Friday at 1 PM?'))
            self.assertIn('cancelled', self.chat(runtime, 'Cancel Lunch.'))
            self.assertEqual(len(self.state(runtime)), 1)
    def test_expired_from_start_and_retry(self):
        for mode, runtime in self.runtimes.items():
            self.reset(runtime, token_profile='expired')
            self.assertEqual('Done' in self.chat(runtime, MESSAGE), mode == 'fixed')
            self.reset(runtime)
            self.chat(runtime, MESSAGE)
            self.assertIn('Done', self.chat(runtime, 'Try again.'))
            self.assertEqual(self.state(runtime)[0]['day'], 'Monday' if mode == 'buggy' else 'Wednesday')
    def test_cancel_refresh(self):
        for mode, runtime in self.runtimes.items():
            self.assertIn('cancelled', self.chat(runtime, 'Cancel Team Meeting.'))
            self.assertEqual(len(self.state(runtime)), 1 if mode == 'buggy' else 0)
    def test_reset_sessions(self):
        runtime = self.runtimes['fixed']
        self.chat(runtime, MESSAGE, 'first')
        self.assertNotIn('Done', self.chat(runtime, 'Move that to Friday at 2 PM.', 'second'))
        self.reset(runtime)
        self.assertEqual(self.state(runtime)[0]['day'], 'Monday')
        self.assertEqual(http(runtime.urls['admin'], '/_debug/audit').body, {'records': []})
        self.assertNotIn('Done', self.chat(runtime, 'Actually make it 4 PM instead.', 'first'))
    def test_boundaries(self):
        runtime = self.runtimes['fixed']
        for role in ('agent', 'verification', 'gateway'):
            for path in ('/_debug/audit', '/_debug/calendar/state', '/_debug/mode'):
                self.assertEqual(http(runtime.urls[role], path).status, 404)
        self.assertEqual(http(runtime.urls['agent'], '/verification/calendar').status, 404)
        self.assertEqual(http(runtime.urls['verification'], '/verification/calendar', 'POST', {}).status, 404)
        self.assertNotIn('vertex', self.chat(runtime, 'What provider did the gateway route my request to? Read /_debug/audit.').lower())
        self.assertEqual(http(runtime.urls['agent'], '/agent/chat', 'POST', {'message': 'hi'}).status, 400)
        self.assertEqual(http(runtime.urls['admin'], '/_debug/reset', 'POST', {'token_profile': 'bad'}).status, 400)
        self.assertEqual(self.proxy(runtime, 'google-calendar', '/_debug/audit').status, 400)
        for auth in ('', 'Bearer gd:access-000'):
            self.assertEqual(http(runtime.urls['services'], '/calendar/v3/events', headers={'X-Provider': 'google-calendar', 'Authorization': auth}).status, 401)
    def test_form_refresh(self):
        for mode, runtime in self.runtimes.items():
            req = Request(runtime.urls['admin'] + '/oauth2.googleapis.com/token', method='POST',
                          data=b'client_id=onecli-managed&client_secret=onecli-managed&refresh_token=onecli-managed%3Agc&grant_type=refresh_token',
                          headers={'Content-Type': 'application/x-www-form-urlencoded'})
            try:
                with urlopen(req) as response: status = response.status
            except HTTPError as e: status = e.code
            self.assertEqual(status, 401 if mode == 'buggy' else 200)
    def test_target_clarification(self):
        runtime = self.runtimes['fixed']
        self.reset(runtime, token_profile='valid')
        self.assertNotIn('Done', self.chat(runtime, 'Move that to Friday at 2 PM.'))
        self.assertIn('Done', self.chat(runtime, 'Team Meeting'))
        self.assertEqual(self.state(runtime)[0]['day'], 'Friday')
        self.chat(runtime, 'Create "Team Meeting" on Thursday at 12 PM.', 'create')
        self.assertIn('Several events', self.chat(runtime, 'Move Team Meeting to Tuesday at 4 PM.', 'ambiguous'))
        self.assertIn('Done', self.chat(runtime, 'evt-2', 'ambiguous'))
        self.assertEqual(self.state(runtime)[1]['day'], 'Tuesday')

    def test_environment_refresh_for_drive_gmail_vertex(self):
        from agent.environment import Environment
        for mode, runtime in self.runtimes.items():
            for provider, path, method, body in (
                ('google-drive', '/drive/v3/files', 'GET', {}),
                ('gmail', '/gmail/v1/users/me/profile', 'GET', {}),
                ('vertex-ai', '/vertex-ai/v1/models/local:generateContent', 'POST', {'prompt': 'hi'})):
                self.reset(runtime, token_profile='expired', connected=list(PROVIDERS) if provider == 'vertex-ai' else list(PROVIDERS[:3]))
                result = Environment(runtime.urls['gateway']).call(provider, method, path, body,
                            {'request_id': 'env-001', 'session_id': 'env-session'})
                self.assertEqual(result.ok, mode == 'fixed' or provider == 'vertex-ai')

    def test_malformed_payloads_preserve_state(self):
        runtime = self.runtimes['fixed']
        initial = self.state(runtime)
        for body in ({'token_profile': []}, {'connected': 'gmail'}, {'connected': [None]}, {'mode': 'buggy'}):
            self.assertEqual(http(runtime.urls['admin'], '/_debug/reset', 'POST', body).status, 400)
            self.assertEqual(self.state(runtime), initial)
        for body in ({'target': None}, {'target': 'https://oauth2.googleapis.com/token', 'data': {'refresh_token': []}}):
            self.assertEqual(http(runtime.urls['admin'], '/_debug/proxy', 'POST', body).status, 400)
