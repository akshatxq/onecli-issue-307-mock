"""Calendar storage and validated operations; no dependency on agent or gateway."""
from copy import deepcopy
import re
from common import DAYS, Result, error

INITIAL = {'id': 'evt-1', 'title': 'Team Meeting', 'day': 'Monday', 'time': '10:00',
           'attendees': ['me', 'team@example.com']}

class Calendar:
    def __init__(self):
        self.events = {'evt-1': deepcopy(INITIAL)}
        self.sequence = 1

    def snapshot(self) -> dict:
        return {'events': deepcopy(list(self.events.values()))}

    @staticmethod
    def valid(event: dict) -> bool:
        return (isinstance(event.get('title'), str) and 0 < len(event['title']) <= 200
                and event.get('day') in DAYS and isinstance(event.get('time'), str)
                and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', event['time']) is not None
                and isinstance(event.get('attendees'), list)
                and all(isinstance(x, str) for x in event['attendees']))

    def handle(self, method: str, path: str, data: dict, query: dict) -> Result:
        root = '/calendar/v3/events'
        if path == '/calendar/v3/freebusy' and method == 'GET':
            day = query.get('day', [None])[0]
            if day not in DAYS:
                return error(400, 'invalid_day')
            return Result(200, {'day': day, 'busy': [e['time'] for e in self.events.values() if e['day'] == day]})
        if path == root:
            if method == 'GET':
                events = self.snapshot()['events']
                for key in ('day', 'title'):
                    if key in query:
                        events = [e for e in events if e[key].lower() == query[key][0].lower()]
                return Result(200, {'events': events})
            if method == 'POST':
                event = {'attendees': ['me'], **data}
                if not self.valid(event) or set(data) - {'title', 'day', 'time', 'attendees'}:
                    return error(400, 'invalid_event')
                self.sequence += 1
                event['id'] = f'evt-{self.sequence}'
                self.events[event['id']] = deepcopy(event)
                return Result(201, deepcopy(event))
        elif path.startswith(root + '/'):
            event_id = path[len(root) + 1:]
            if event_id not in self.events:
                return error(404, 'event_not_found')
            if method == 'GET':
                return Result(200, deepcopy(self.events[event_id]))
            if method == 'PATCH':
                event = {**self.events[event_id], **data}
                if set(data) - {'title', 'day', 'time', 'attendees'} or not self.valid(event):
                    return error(400, 'invalid_event')
                self.events[event_id] = event
                return Result(200, deepcopy(event))
            if method == 'DELETE':
                del self.events[event_id]
                return Result(200, {'deleted': event_id})
        return error(404, 'unknown_calendar_operation')
