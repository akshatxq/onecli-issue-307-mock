"""Small, dependency-free HTTP protocol shared by the local components."""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

PROVIDERS = ('google-calendar', 'google-drive', 'gmail', 'vertex-ai')
PREFIXES = dict(zip(PROVIDERS, ('gc', 'gd', 'gmail', 'vx')))
HOSTS = dict(zip(PROVIDERS, ('calendar.googleapis.com', 'www.googleapis.com',
                            'gmail.googleapis.com', 'aiplatform.googleapis.com')))
DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')

@dataclass(frozen=True)
class Result:
    status: int
    body: dict

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def http(base: str, path: str, method: str = 'GET', data: dict | None = None,
         headers: dict | None = None) -> Result:
    request_headers = {'Content-Type': 'application/json', **(headers or {})}
    req = Request(base + path, data=None if data is None else json.dumps(data).encode(),
                  method=method, headers=request_headers)
    # Never use host proxy configuration for local component traffic.
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=5) as response:
            return Result(response.status, json.load(response))
    except HTTPError as error:
        return Result(error.code, json.load(error))
    except (URLError, TimeoutError):
        return Result(503, {'error': 'transport_unavailable'})


def error(status: int, code: str, **details) -> Result:
    return Result(status, {'error': code, **details})
