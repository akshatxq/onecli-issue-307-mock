"""Externally meaningful state projection, without routing or expectation fields."""
def calendar_state(calendar) -> dict:
    return {'events': [{k: event[k] for k in ('id', 'title', 'day', 'time', 'attendees')}
                       for event in calendar.snapshot()['events']]}
