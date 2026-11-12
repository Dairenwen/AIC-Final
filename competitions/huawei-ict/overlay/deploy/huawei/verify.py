#!/usr/bin/env python3
"""Verify existing health responses. Makes no inference/SMS requests."""
import argparse
import json
import time
from urllib.parse import urlparse
from urllib.request import urlopen


def assess(live, ready, *, core_only=False):
    if not isinstance(live, dict) or not isinstance(ready, dict):
        return False
    live_data = live.get('data', {})
    ready_data = ready.get('data', {})
    if not isinstance(live_data, dict) or not isinstance(ready_data, dict):
        return False
    components = ready_data.get('components', {})
    if not isinstance(components, dict):
        return False
    acceptable = {'ready', 'degraded'} if core_only else {'ready'}
    return (live_data.get('status') == 'up' and ready_data.get('status') in acceptable
            and components.get('database') == 'up')


def fetch_health(base_url):
    def fetch(path):
        with urlopen(base_url.rstrip('/') + path, timeout=30) as response:
            return json.load(response)
    return fetch('/api/v1/health/live'), fetch('/api/v1/health/ready')


def verify_health(base_url, *, attempts=1, interval=2, core_only=False):
    result = {'passed': False}
    for attempt in range(1, attempts + 1):
        try:
            live, ready = fetch_health(base_url)
            result = {'passed': assess(live, ready, core_only=core_only), 'attempt': attempt,
                      'ready': ready.get('data', {}) if isinstance(ready, dict) else {}}
        except Exception as exc:
            result = {'passed': False, 'attempt': attempt, 'error_type': type(exc).__name__}
        if result['passed'] or attempt == attempts:
            return result
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:5000')
    parser.add_argument('--attempts', type=int, default=1, help='bounded retries during startup')
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--core-only', action='store_true', help='check API and database startup; do not assert feature readiness')
    args = parser.parse_args()
    if not 1 <= args.attempts <= 30 or not 0 <= args.interval <= 60:
        parser.error('attempts must be 1..30 and interval 0..60 seconds')
    parsed = urlparse(args.base_url)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        parser.error('Use an HTTP(S) application origin without credentials/query/fragment')
    result = verify_health(args.base_url, attempts=args.attempts, interval=args.interval, core_only=args.core_only)
    print(json.dumps({**result,
        'limits': 'Health-only; all advertised functions and original data still require acceptance.'}, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
