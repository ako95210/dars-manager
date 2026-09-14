#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Result:
    status: int
    duration_ms: float


def request_once(url: str, timeout: float) -> Result:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception:
        status = 0
    return Result(status=status, duration_ms=(time.perf_counter() - started) * 1000)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Sonde de charge HTTP non destructive")
    parser.add_argument("base_url")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit("base_url doit être une URL HTTP(S) complète")
    if args.requests < 1 or args.concurrency < 1:
        raise SystemExit("requests et concurrency doivent être positifs")
    url = args.base_url.rstrip("/") + "/api/health/ready"
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(
            pool.map(lambda _: request_once(url, args.timeout), range(args.requests))
        )
    elapsed = time.perf_counter() - started
    durations = [result.duration_ms for result in results]
    statuses: dict[str, int] = {}
    for result in results:
        key = str(result.status)
        statuses[key] = statuses.get(key, 0) + 1
    report = {
        "target": url,
        "requests": len(results),
        "concurrency": args.concurrency,
        "requests_per_second": round(len(results) / elapsed, 2),
        "latency_ms": {
            "average": round(statistics.fmean(durations), 2),
            "p50": round(percentile(durations, 0.50), 2),
            "p95": round(percentile(durations, 0.95), 2),
            "p99": round(percentile(durations, 0.99), 2),
        },
        "statuses": statuses,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if statuses.get("200", 0) != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
