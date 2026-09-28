"""State-based black-box evaluator; local comparison also proves conformance."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Runtime
from common import http

MESSAGE = 'Move my Team Meeting from Monday at 10 AM to Wednesday at 3 PM.'
EXPECTED = {'title': 'Team Meeting', 'day': 'Wednesday', 'time': '15:00'}

def require(result):
    if not result.ok:
        raise RuntimeError(f'HTTP {result.status}: {result.body}')
    return result.body

def evaluate(agent_url, verification_url):
    """This evaluator has no mode, audit, or internal-state access."""
    response = require(http(agent_url, '/agent/chat', 'POST', {'session_id': 'benchmark-001', 'message': MESSAGE}))
    state = require(http(verification_url, '/verification/calendar'))
    matches = [e for e in state['events'] if e['title'] == EXPECTED['title']]
    passed = len(matches) == 1 and all(matches[0].get(k) == v for k, v in EXPECTED.items())
    return {'reply': response['reply'], 'state': state, 'verdict': 'PASS' if passed else 'FAIL'}

def repetitions(agent_url, verification_url, admin_url, repeats):
    results = []
    for _ in range(repeats):
        require(http(admin_url, '/_debug/reset', 'POST', {}))
        results.append(evaluate(agent_url, verification_url))
    require(http(admin_url, '/_debug/reset', 'POST', {}))
    return results

def run_local(repeats=5):
    modes = {}
    for mode in ('buggy', 'fixed'):
        with Runtime(mode, ports=(0, 0, 0, 0, 0)) as runtime:
            modes[mode] = repetitions(runtime.urls['agent'], runtime.urls['verification'], runtime.urls['admin'], repeats)
    conforms = (all(r['verdict'] == 'FAIL' for r in modes['buggy']) and
                all(r['verdict'] == 'PASS' for r in modes['fixed']) and
                all(r == modes[m][0] for m in modes for r in modes[m]) and
                modes['buggy'][0]['reply'] == modes['fixed'][0]['reply'])
    return {'reference_conforms': conforms, 'repetitions': repeats, 'modes': modes}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--agent-url')
    parser.add_argument('--verification-url', default='http://127.0.0.1:8001')
    parser.add_argument('--admin-url', default='http://127.0.0.1:8002')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.repeats < 1: parser.error('--repeats must be positive')
    if args.agent_url:
        runs = repetitions(args.agent_url, args.verification_url, args.admin_url, args.repeats)
        report = {'runs': runs}
        success = all(r['verdict'] == 'PASS' for r in runs)
    else:
        report = run_local(args.repeats)
        success = report['reference_conforms']
    encoded = json.dumps(report, indent=2)
    if args.output: Path(args.output).write_text(encoded + '\n')
    print(encoded)
    return 0 if success else 1

if __name__ == '__main__': raise SystemExit(main())
