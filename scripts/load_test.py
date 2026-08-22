#!/usr/bin/env python3
"""
Lightweight QA Load Testing Script for Druthers API.

Exercises traffic-relevant read endpoints:
  1. GET /v1/search/movies?q=dune
  2. GET /v1/search/tv?q=breaking
  3. GET /v1/search/books?q=hobbit
  4. GET /v1/search/games?q=zelda
  5. GET /v1/public/dadam
  6. GET /v1/summary

Usage:
  python scripts/load_test.py --url http://localhost:8000 --concurrency 5 --duration 10

Rate Limit Note:
  Druthers API enforces rate limits on unauthenticated/authenticated endpoints
  via `app/services/rate_limit.py`. Be mindful of concurrency levels when testing
  against production or QA environments to avoid tripping the rate limiters.
"""

import argparse
import concurrent.futures
import math
import time
import urllib.request
import urllib.error

ENDPOINTS = [
    '/v1/search/movies?q=dune',
    '/v1/search/tv?q=breaking',
    '/v1/search/books?q=hobbit',
    '/v1/search/games?q=zelda',
    '/v1/public/dadam',
    '/v1/summary',
]


def make_request(base_url: str, path: str) -> tuple[bool, float, int]:
    '''Issue one GET and return (success, elapsed_ms, status_code).'''
    url = f"{base_url.rstrip('/')}{path}"
    start = time.perf_counter()
    status_code = 0
    success = False
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Druthers-LoadTest/1.0'},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            status_code = response.status
            success = 200 <= status_code < 300
    except urllib.error.HTTPError as err:
        status_code = err.code
    except Exception:  # pylint: disable=broad-exception-caught
        # A load test must survive every transport failure a rate limiter or a
        # cold Cloud Run instance can produce; anything unhandled here would
        # kill the worker thread and quietly shrink the concurrency.
        status_code = 0

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return success, elapsed_ms, status_code


def percentile(data: list[float], p: float) -> float:
    '''Linear-interpolated percentile over unsorted samples.'''
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return d0 + d1


def run_load_test(base_url: str, concurrency: int, duration_sec: int):
    # pylint: disable=too-many-locals
    # The counters, timers and result buckets are all one flat measurement
    # loop; splitting them across helpers would hide the sequence rather than
    # clarify it.
    '''Drive the endpoint list for duration_sec and print a latency summary.'''
    print('\n🚀 Starting Druthers Load Test')
    print(f"   Target URL:  {base_url}")
    print(f"   Concurrency: {concurrency} workers")
    print(f"   Duration:    {duration_sec} seconds")
    print(f"   Endpoints:   {len(ENDPOINTS)} paths\n")

    latencies: list[float] = []
    status_counts: dict[int, int] = {}
    total_requests = 0
    success_requests = 0

    end_time = time.time() + duration_sec
    idx = 0

    def worker_task():
        nonlocal idx
        results = []
        while time.time() < end_time:
            path = ENDPOINTS[idx % len(ENDPOINTS)]
            idx += 1
            res = make_request(base_url, path)
            results.append(res)
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker_task) for _ in range(concurrency)]
        for future in concurrent.futures.as_completed(futures):
            for ok, elapsed_ms, code in future.result():
                total_requests += 1
                if ok:
                    success_requests += 1
                latencies.append(elapsed_ms)
                status_counts[code] = status_counts.get(code, 0) + 1

    if not total_requests:
        print('❌ No requests executed.')
        return

    error_rate = ((total_requests - success_requests) / total_requests) * 100.0
    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)

    print('📊 --- Load Test Results ---')
    print(f"Total Requests:     {total_requests}")
    print(f"Successful:         {success_requests}")
    print(f"Error Rate:         {error_rate:.2f}%")
    print(f"Status Code Breakdown: {dict(sorted(status_counts.items()))}")
    print('Latency Percentiles:')
    print(f"  p50 (median):     {p50:.2f} ms")
    print(f"  p95:              {p95:.2f} ms")
    print(f"  p99:              {p99:.2f} ms")
    print('----------------------------\n')


def main():
    '''Parse arguments and run one load test.'''
    parser = argparse.ArgumentParser(description='Druthers QA Load Test Script')
    parser.add_argument(
        '--url',
        default='http://localhost:8000',
        help='Base URL of target API (default: http://localhost:8000)',
    )
    parser.add_argument(
        '--concurrency',
        type=int,
        default=5,
        help='Number of concurrent workers (default: 5)',
    )
    parser.add_argument(
        '--duration',
        type=int,
        default=10,
        help='Duration of test in seconds (default: 10)',
    )
    args = parser.parse_args()
    run_load_test(args.url, args.concurrency, args.duration)


if __name__ == '__main__':
    main()
