import unittest
from common import PROVIDERS, PREFIXES, HOSTS
from gateway.router import Router
from google_services.oauth import OAuth
from google_services.calendar import Calendar
from agent.service import slots

class RoutingTests(unittest.TestCase):
    def test_resources(self):
        paths = ('/calendar/v3/events', '/drive/v3/about', '/gmail/v1/users/me/profile', '/vertex-ai/v1/models/local:generateContent')
        for mode in ('buggy', 'fixed'):
            for p, path in zip(PROVIDERS, paths):
                self.assertEqual(Router(mode).route('https://' + HOSTS[p] + path), p)
    def test_refresh_routes(self):
        for p in PROVIDERS:
            token = PREFIXES[p] + ':refresh-token-001'
            self.assertEqual(Router('buggy').route('https://oauth2.googleapis.com/token', token), 'vertex-ai')
            self.assertEqual(Router('fixed').route('https://oauth2.googleapis.com/token', token), p)
        self.assertIsNone(Router('fixed').route('https://oauth2.googleapis.com/token', 'unknown'))
    def test_allowlist(self):
        for target in ('http://calendar.googleapis.com/calendar/v3/events', 'https://evil.test/calendar/v3/events',
                       'https://calendar.googleapis.com/_debug/audit', 'https://calendar.googleapis.com:8002/calendar/v3/events',
                       'https://user@calendar.googleapis.com/calendar/v3/events'):
            self.assertIsNone(Router('fixed').route(target))

class OAuthTests(unittest.TestCase):
    def form(self, p):
        return {'client_id': p + ':client', 'client_secret': p + ':local-secret', 'grant_type': 'refresh_token',
                'refresh_token': PREFIXES[p] + ':refresh-token-001', 'scope': p}
    def test_providers(self):
        oauth = OAuth('expired')
        for p in PROVIDERS:
            self.assertFalse(oauth.authorize(p, oauth.initial(p)))
            result = oauth.refresh(self.form(p), p)
            self.assertTrue(result.ok)
            self.assertTrue(oauth.authorize(p, result.body['access_token']))
            self.assertFalse(oauth.authorize(next(x for x in PROVIDERS if x != p), result.body['access_token']))
    def test_invalid_grants(self):
        oauth = OAuth('valid')
        for field, value, expected in (('client_secret', 'bad', 'invalid_client'), ('refresh_token', 'gc:wrong', 'invalid_grant'),
                                       ('grant_type', 'password', 'unsupported_grant_type'), ('scope', 'gmail', 'invalid_scope')):
            self.assertEqual(oauth.refresh({**self.form('google-calendar'), field: value}, 'google-calendar').body['error'], expected)
    def test_expiry(self):
        oauth = OAuth('expire_after_lookup')
        self.assertTrue(oauth.authorize('google-calendar', oauth.initial('google-calendar')))
        self.assertFalse(oauth.authorize('google-calendar', oauth.initial('google-calendar')))
        for _ in range(10): self.assertTrue(oauth.authorize('google-drive', oauth.initial('google-drive')))

class CalendarTests(unittest.TestCase):
    def test_atomic_validation(self):
        calendar = Calendar()
        before = calendar.snapshot()
        for data in ({'day': 'Never'}, {'time': '25:00'}, {'id': 'spoof'}, {'attendees': 'oops'}):
            self.assertEqual(calendar.handle('PATCH', '/calendar/v3/events/evt-1', data, {}).status, 400)
            self.assertEqual(before, calendar.snapshot())
        snapshot = calendar.snapshot()
        snapshot['events'].clear()
        self.assertEqual(before, calendar.snapshot())
    def test_slots(self):
        for text, expected in [('from Monday at 10 AM to Wednesday at 3 PM', ('Wednesday', '15:00')),
                               ('Wednesday at 3', ('Wednesday', '15:00')), ('Actually make it 4 PM instead.', (None, '16:00')),
                               ('Friday at 12 AM', ('Friday', '00:00')), ('Friday at 28 PM', ('Friday', None))]:
            self.assertEqual(slots(text), expected)

class CompletionPolicyTests(unittest.TestCase):
    def test_transport_and_validation_errors_do_not_claim_success(self):
        from agent.service import Agent
        from common import Result, error
        class BrokenTool:
            def __init__(self, failure): self.failure = failure
            def call(self, provider, method, path, data, context):
                if method == 'GET': return Result(200, Calendar().snapshot())
                return self.failure
        for failure in (error(503, 'transport_unavailable'), error(400, 'invalid_event'), error(404, 'event_not_found')):
            reply = Agent(BrokenTool(failure)).chat('test', 'Move Team Meeting to Wednesday at 3 PM.', {})
            self.assertNotIn('Done', reply)
