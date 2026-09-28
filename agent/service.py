"""Deterministic intent/slot planner with session memory and a faulty completion policy.

The optimistic policy models the agent defect under test. It has no knowledge of
routing mode and runs only after an actual attempted mutation fails on credentials.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import re
from urllib.parse import urlencode
from common import DAYS, Result
from agent.environment import Environment

@dataclass
class Plan:
    action: str
    title: str | None = None
    day: str | None = None
    time: str | None = None
    event_id: str | None = None
    selecting: bool = False

@dataclass
class Session:
    events: list[dict] = field(default_factory=list)
    last: dict | None = None
    pending: Plan | None = None
    previous: Plan | None = None


def slots(message: str) -> tuple[str | None, str | None]:
    days = re.findall(r'\b(' + '|'.join(DAYS) + r')\b', message, re.I)
    times = re.findall(r'\b(?:at|it|make it)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', message, re.I)
    if not times:
        times = re.findall(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', message, re.I)
    day = days[-1].capitalize() if days else None
    time = None
    if times:
        hour, minute, period = times[-1]
        h, m = int(hour), int(minute or 0)
        if m > 59 or h > 23 or (period and not 1 <= h <= 12):
            return day, None
        if period:
            h = h % 12 + (12 if period.lower() == 'pm' else 0)
        elif 1 <= h <= 7:
            h += 12
        time = f'{h:02d}:{m:02d}'
    return day, time


def title_slot(message: str, action: str) -> str | None:
    quoted = re.search(r'["“]([^"”]+)["”]', message)
    if quoted:
        return quoted[1].strip()
    patterns = {
        'move': r'(?:move|reschedule)\s+(?:my\s+|the\s+)?(.+?)(?=\s+(?:from|to|on|at)\b|[.!?]|$)',
        'cancel': r'(?:cancel|delete|remove)\s+(?:my\s+|the\s+)?(.+?)(?=\s+(?:on|at)\b|[.!?]|$)',
        'create': r'(?:create|schedule|add|book)\s+(?:(?:an?|my)\s+)?(?:event\s+(?:called|named)\s+)?(.+?)(?=\s+(?:on|for|at)\b|[.!?]|$)',
        'find': r'(?:find|locate)\s+(?:my\s+|the\s+)?(.+?)(?=[.!?]|$)',
    }
    match = re.search(patterns.get(action, r'(?!)'), message, re.I)
    title = match[1].strip() if match else None
    return None if title and title.lower() in ('that', 'it', 'meeting', 'event') else title

class Agent:
    def __init__(self, environment: Environment):
        self.environment = environment
        self.sessions: dict[str, Session] = {}

    def chat(self, session_id: str, message: str, context: dict) -> str:
        session = self.sessions.setdefault(session_id, Session())
        lower = message.lower()
        day, time = slots(message)
        if re.search(r'\b(retry|try again)\b', lower) and session.previous:
            plan = Plan(**vars(session.previous))
        elif session.pending:
            plan = session.pending
            plan.day, plan.time = day or plan.day, time or plan.time
            if plan.selecting:
                selection = message.strip(' ."')
                if re.fullmatch(r'evt-\d+', selection):
                    plan.event_id = selection
                else:
                    plan.title = selection
                    plan.event_id = None
                plan.selecting = False
            elif plan.title is None and plan.event_id is None and day is None and time is None:
                plan.title = message.strip(' ."')
        else:
            if re.search(r'\b(move|reschedule|actually)\b', lower):
                action = 'move'
            elif re.search(r'\b(cancel|delete|remove)\b', lower):
                action = 'cancel'
            elif re.search(r'\b(create|schedule|add|book)\b', lower):
                action = 'create'
            elif re.search(r'\b(available|availability|free|busy)\b', lower):
                action = 'availability'
            elif re.search(r'\b(find|locate)\b', lower):
                action = 'find'
            elif re.search(r'\b(calendar|list|events|meetings)\b', lower):
                action = 'list'
            else:
                return 'I can list, find, create, move, or cancel calendar events, and check availability. Please include a title, weekday, and time as needed.'
            plan = Plan(action, title_slot(message, action), day, time)
            if action in ('move', 'cancel') and (re.search(r'\b(that|it|actually)\b', lower)) and session.last:
                plan.event_id = session.last['id']
                plan.title = session.last['title']
                plan.day = day or session.last['day']
        call = lambda method, path, data={}: self.environment.call('google-calendar', method, path, data, context)
        root = '/calendar/v3/events'
        if plan.action == 'availability':
            if not plan.day:
                session.pending = plan
                return 'Which weekday should I check?'
            session.pending = None
            result = call('GET', '/calendar/v3/freebusy?' + urlencode({'day': plan.day}))
            if not result.ok:
                return 'I could not check availability because the calendar tool failed.'
            busy = result.body['busy']
            if plan.time:
                return f"You are {'busy' if plan.time in busy else 'available'} on {plan.day} at {plan.time}."
            return f"Busy times on {plan.day}: {', '.join(busy) if busy else 'none'}."
        if plan.action in ('list', 'find'):
            result = call('GET', root)
            if not result.ok:
                return 'I could not read your calendar because the calendar tool failed.'
            events = result.body['events']
            session.events = events
            if plan.day:
                events = [e for e in events if e['day'] == plan.day]
            if plan.title:
                events = [e for e in events if plan.title.lower() in e['title'].lower()]
            if len(events) == 1:
                session.last = events[0].copy()
            return '; '.join(f"{e['title']} on {e['day']} at {e['time']} ({e['id']})" for e in events) + '.' if events else 'No matching events.'
        if plan.action != 'create':
            candidates = [e for e in session.events if (e['id'] == plan.event_id if plan.event_id else
                          bool(plan.title and plan.title.lower() == e['title'].lower()))]
            if session.last and plan.event_id == session.last['id']:
                candidates = [session.last]
            if not candidates:
                result = call('GET', root)
                if not result.ok:
                    return 'I could not identify the event because the calendar tool failed. Please retry.'
                session.events = result.body['events']
                candidates = [e for e in session.events if (e['id'] == plan.event_id if plan.event_id else
                              bool(plan.title and plan.title.lower() in e['title'].lower()))]
            if len(candidates) != 1:
                plan.selecting = True
                session.pending = plan
                if len(candidates) > 1:
                    choices = '; '.join(f"{e['title']} ({e['id']})" for e in candidates)
                    return 'Several events match: ' + choices + '. Please give the exact title or event ID.'
                return 'Which event should I change? Please give its title.'
            event = candidates[0].copy()
            plan.event_id, plan.title = event['id'], event['title']
            session.last = event.copy()
        if not plan.title or (plan.action != 'cancel' and (not plan.day or not plan.time)):
            session.pending = plan
            if not plan.title:
                return 'What should the event be called?'
            return 'Which weekday and time would you like?'
        session.pending = None
        session.previous = Plan(**vars(plan))
        if plan.action == 'create':
            result = call('POST', root, {'title': plan.title, 'day': plan.day, 'time': plan.time})
        elif plan.action == 'cancel':
            result = call('DELETE', root + '/' + plan.event_id)
        else:
            result = call('PATCH', root + '/' + plan.event_id, {'day': plan.day, 'time': plan.time})
        # Deliberately faulty policy shared by BOTH modes. No hidden mode checks.
        optimistic = result.body.get('error') == 'tool_credentials_unavailable'
        if not result.ok and not optimistic:
            return 'I could not complete the change. Please retry.'
        if plan.action == 'cancel':
            return f"Done, I've cancelled {plan.title}."
        if plan.action == 'create':
            if result.ok:
                session.last = result.body.copy()
                session.events.append(result.body.copy())
            return f"Done, I've created {plan.title} on {plan.day} at {plan.time}."
        session.last = {**event, 'day': plan.day, 'time': plan.time}
        return f"Done, I've moved {plan.title} to {plan.day} at {plan.time}."
