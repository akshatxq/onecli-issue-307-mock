"""Generate JSON-form YAML (JSON is valid YAML 1.2) without build dependencies."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def ref(name): return {'$ref': '#/components/schemas/' + name}
def obj(props, required=(), **extra):
    return {'type': 'object', 'properties': props, 'required': list(required), **extra}
def array(item): return {'type': 'array', 'items': item}
string = {'type': 'string'}
event_props = {'id': string, 'title': {'type': 'string', 'minLength': 1, 'maxLength': 200},
               'day': {'type': 'string', 'enum': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']},
               'time': {'type': 'string', 'pattern': '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'}, 'attendees': array(string)}
schemas = {
    'ChatRequest': obj({'session_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
                        'message': {'type': 'string', 'minLength': 1, 'maxLength': 4096}}, ('session_id', 'message'), additionalProperties=False),
    'ChatResponse': obj({'session_id': string, 'reply': string}, ('session_id', 'reply'), additionalProperties=False),
    'Event': obj(event_props, tuple(event_props), additionalProperties=False),
    'EventCreate': obj({k: v for k, v in event_props.items() if k != 'id'}, ('title', 'day', 'time'), additionalProperties=False),
    'EventPatch': obj({k: v for k, v in event_props.items() if k != 'id'}, additionalProperties=False),
    'CalendarState': obj({'events': array(ref('Event'))}, ('events',), additionalProperties=False),
    'Error': obj({'error': string, 'provider': string}, ('error',)),
    'Reset': obj({'token_profile': {'type': 'string', 'enum': ['expire_after_lookup', 'valid', 'expired'], 'default': 'expire_after_lookup'},
                  'connected': array({'type': 'string', 'enum': ['google-calendar', 'google-drive', 'gmail', 'vertex-ai']})}, additionalProperties=False),
    'ProxyRequest': obj({'target': {'type': 'string', 'description': 'Allowlisted HTTPS Google-shaped URL; never fetched from the Internet.'},
                         'method': {'enum': ['GET', 'POST', 'PATCH', 'DELETE'], 'type': 'string'}, 'data': {'type': 'object'}}, ('target', 'method')),
    'OAuthRequest': obj({k: string for k in ('client_id', 'client_secret', 'refresh_token', 'grant_type', 'scope')},
                         ('client_id', 'client_secret', 'refresh_token', 'grant_type')),
    'OAuthResponse': obj({'access_token': string, 'token_type': string, 'expires_in': {'type': 'integer'}, 'scope': string},
                         ('access_token', 'token_type', 'expires_in', 'scope')),
    'SendMessage': obj({k: {'type': 'string', 'minLength': 1} for k in ('to', 'subject', 'body')}, ('to', 'subject', 'body')),
    'GenerateRequest': obj({'prompt': string}, ('prompt',)),
}
ports = {'PUBLIC / AGENT-FACING': 8000, 'VERIFICATION / TEST CONFIGURATION': 8001, 'DEBUG / INTERNAL': 8002,
         'GATEWAY / INTERNAL': 8003, 'SERVICE / INTERNAL': 8004}
paths = {}
def add(path, method, tag, summary, response=None, body=None, params=(), status='200', form=False):
    op = {'tags': [tag], 'summary': summary, 'operationId': method + '_' + path.replace('/', '_').replace('{', '').replace('}', '').replace(':', '_'),
          'servers': [{'url': 'http://127.0.0.1:' + str(ports[tag])}],
          'responses': {status: {'description': 'Success', 'content': {'application/json': {'schema': response or {'type': 'object'}}}},
                        '400': {'description': 'Invalid request', 'content': {'application/json': {'schema': ref('Error')}}},
                        '401': {'description': 'Invalid credentials or disconnected provider', 'content': {'application/json': {'schema': ref('Error')}}},
                        '404': {'description': 'Not found', 'content': {'application/json': {'schema': ref('Error')}}},
                        '503': {'description': 'Local component unavailable', 'content': {'application/json': {'schema': ref('Error')}}}}}
    if params: op['parameters'] = list(params)
    if body:
        op['requestBody'] = {'required': True, 'content': {'application/json': {'schema': body}}}
        if form: op['requestBody']['content']['application/x-www-form-urlencoded'] = {'schema': body}
    if tag == 'SERVICE / INTERNAL':
        op['security'] = [{'bearerAuth': []}]
        op.setdefault('parameters', []).append({'name': 'X-Provider', 'in': 'header', 'required': True, 'schema': {'type': 'string'}})
    paths.setdefault(path, {})[method] = op
P, V, D, G, S = ports
add('/agent/chat', 'post', P, 'Send one turn in a deterministic calendar session', ref('ChatResponse'), ref('ChatRequest'))
add('/verification/calendar', 'get', V, 'Read independent externally meaningful Calendar state', ref('CalendarState'))
add('/_debug/reset', 'post', D, 'Reset all state, sessions, tokens, IDs, and audit; preserve gateway mode', body=ref('Reset'))
for path, summary, response in [('/_debug/audit', 'Read private correlated component audit', None),
                                ('/_debug/mode', 'Read private gateway version', None),
                                ('/_debug/health', 'Read readiness', None),
                                ('/_debug/calendar/state', 'Read developer Calendar state', ref('CalendarState'))]:
    add(path, 'get', D, summary, response)
add('/_debug/proxy', 'post', D, 'Developer gateway reproduction envelope', body=ref('ProxyRequest'))
add('/proxy', 'post', G, 'Agent execution environment gateway transport', body=ref('ProxyRequest'))
add('/oauth2.googleapis.com/token', 'post', D, 'Developer refresh through gateway; returns opaque access placeholder', ref('OAuthResponse'), ref('OAuthRequest'), form=True)
# The provider listener serves the same protocol path after gateway dispatch.
paths['/oauth2.googleapis.com/token']['post']['description'] = 'Port 8002 enters gateway routing. On internal port 8004 the issuer receives injected fake credentials and X-Provider. Never point an agent connector here.'
paths['/oauth2.googleapis.com/token']['post']['servers'].append({'url': 'http://127.0.0.1:8004'})
add('/calendar/v3/events', 'get', S, 'List/filter events', ref('CalendarState'), params=[{'name': key, 'in': 'query', 'schema': string} for key in ('day', 'title')])
add('/calendar/v3/events', 'post', S, 'Create validated event', ref('Event'), ref('EventCreate'), status='201')
ids = ({'name': 'event_id', 'in': 'path', 'required': True, 'schema': string},)
add('/calendar/v3/events/{event_id}', 'get', S, 'Get event', ref('Event'), params=ids)
add('/calendar/v3/events/{event_id}', 'patch', S, 'Atomically update event', ref('Event'), ref('EventPatch'), params=ids)
add('/calendar/v3/events/{event_id}', 'delete', S, 'Cancel event', params=ids)
add('/calendar/v3/freebusy', 'get', S, 'Return busy start times for one weekday', params=[{'name': 'day', 'in': 'query', 'required': True, 'schema': event_props['day']}])
for path in ('/drive/v3/about', '/drive/v3/files', '/drive/v3/files/{file_id}', '/gmail/v1/users/me/profile', '/gmail/v1/users/me/messages'):
    params = [{'name': 'file_id', 'in': 'path', 'required': True, 'schema': string}] if '{file_id}' in path else []
    add(path, 'get', S, 'Read deterministic provider metadata', params=params)
add('/gmail/v1/users/me/messages/send', 'post', S, 'Store a sent message', body=ref('SendMessage'))
add('/vertex-ai/v1/models/{model}:generateContent', 'post', S, 'Return deterministic text (no inference)', body=ref('GenerateRequest'),
    params=[{'name': 'model', 'in': 'path', 'required': True, 'schema': string}])
spec = {'openapi': '3.1.0', 'info': {'title': 'OneCLI Issue 307 controlled reference environment', 'version': '1.0.0',
        'description': 'Not OneCLI. Import only openapi-agent.json into an agent connector; this full document includes developer interfaces.'},
        'tags': [{'name': name} for name in ports], 'paths': paths,
        'components': {'schemas': schemas, 'securitySchemes': {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}}}}
(ROOT / 'openapi.yaml').write_text(json.dumps(spec, indent=2) + '\n')
public = {**spec, 'tags': [{'name': P}], 'paths': {'/agent/chat': paths['/agent/chat']},
          'components': {'schemas': {k: schemas[k] for k in ('ChatRequest', 'ChatResponse', 'Error')}}}
(ROOT / 'openapi-agent.json').write_text(json.dumps(public, indent=2) + '\n')
