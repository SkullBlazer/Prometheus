#!/usr/bin/env python3
"""
Benchmark helper for comparing:
1) Prometheus-only
2) Prometheus + Thanos Query (+ optional object storage)

Usage:
  python benchmark_observability.py run --output prometheus_only.json
  python benchmark_observability.py run --output thanos_stack.json
  python benchmark_observability.py compare --left prometheus_only.json --right thanos_stack.json --output-md comparison.md
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


QUERY_SET = {
    "up": "up",
    "ingest_rate": "rate(prometheus_tsdb_head_samples_appended_total[1m])",
    "http_req_rate": "sum(rate(prometheus_http_requests_total[1m]))",
    "scrape_duration_avg": "avg(scrape_duration_seconds)",
}


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def ensure_no_trailing_slash(url: str) -> str:
    return url[:-1] if url.endswith("/") else url


def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (p / 100.0) * (len(ordered) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return ordered[lo]
    weight = rank - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def summarize(values: List[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "avg": None,
            "p50": None,
            "p95": None,
            "max": None,
            "stdev": None,
        }

    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {
        "count": len(values),
        "min": min(values),
        "avg": statistics.fmean(values),
        "p50": percentile(values, 50.0),
        "p95": percentile(values, 95.0),
        "max": max(values),
        "stdev": stdev,
    }


def parse_percent(text: str) -> Optional[float]:
    if not text:
        return None
    value = text.strip().replace("%", "")
    try:
        return float(value)
    except ValueError:
        return None


SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*$")
SIZE_FACTORS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000 ** 2,
    "GB": 1000 ** 3,
    "TB": 1000 ** 4,
    "KIB": 1024,
    "MIB": 1024 ** 2,
    "GIB": 1024 ** 3,
    "TIB": 1024 ** 4,
}


def parse_size_to_bytes(size_text: str) -> Optional[int]:
    match = SIZE_RE.match(size_text.strip())
    if not match:
        return None

    raw_value, raw_unit = match.groups()
    unit = raw_unit.upper()
    factor = SIZE_FACTORS.get(unit)
    if factor is None:
        return None

    try:
        return int(float(raw_value) * factor)
    except ValueError:
        return None


def parse_mem_usage_used_bytes(mem_usage: str) -> Optional[int]:
    if not mem_usage:
        return None
    used = mem_usage.split("/")[0].strip()
    return parse_size_to_bytes(used)


def bytes_to_mib(value: Optional[int]) -> Optional[float]:
    if value is None:
        return None
    return value / (1024.0 * 1024.0)


def http_get_status(url: str, timeout: float) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": "obs-bench/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def http_get_json(url: str, timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "obs-bench/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8")
    return json.loads(payload)


def endpoint_alive(base_url: str, timeout: float) -> bool:
    url = ensure_no_trailing_slash(base_url)
    return (
        http_get_status(url + "/-/healthy", timeout)
        or http_get_status(url + "/api/v1/status/buildinfo", timeout)
    )


def instant_query(base_url: str, query: str, timeout: float) -> List[Dict[str, Any]]:
    params = urllib.parse.urlencode({"query": query})
    url = ensure_no_trailing_slash(base_url) + "/api/v1/query?" + params
    data = http_get_json(url, timeout)
    if data.get("status") != "success":
        raise RuntimeError("query did not return success")
    return data.get("data", {}).get("result", [])


def range_query(
    base_url: str,
    query: str,
    start_ts: int,
    end_ts: int,
    step_seconds: int,
    timeout: float,
) -> List[Dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "query": query,
            "start": str(start_ts),
            "end": str(end_ts),
            "step": str(step_seconds),
        }
    )
    url = ensure_no_trailing_slash(base_url) + "/api/v1/query_range?" + params
    data = http_get_json(url, timeout)
    if data.get("status") != "success":
        raise RuntimeError("range query did not return success")
    return data.get("data", {}).get("result", [])


def benchmark_instant_query(
    base_url: str,
    query: str,
    samples: int,
    warmup: int,
    timeout: float,
) -> Dict[str, Any]:
    for _ in range(max(0, warmup)):
        try:
            instant_query(base_url, query, timeout)
        except Exception:
            # Warmup errors do not fail the run.
            pass

    lat_all: List[float] = []
    lat_success: List[float] = []
    result_sizes: List[float] = []
    errors: List[str] = []
    success_count = 0

    for _ in range(samples):
        start = time.perf_counter()
        try:
            result = instant_query(base_url, query, timeout)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            lat_all.append(elapsed_ms)
            lat_success.append(elapsed_ms)
            result_sizes.append(float(len(result)))
            success_count += 1
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            lat_all.append(elapsed_ms)
            if len(errors) < 5:
                errors.append(str(exc))

    requested = samples
    failed = requested - success_count
    success_rate = (success_count / requested * 100.0) if requested else 0.0

    return {
        "requested": requested,
        "successful": success_count,
        "failed": failed,
        "success_rate_pct": success_rate,
        "latency_ms_success": summarize(lat_success),
        "latency_ms_all": summarize(lat_all),
        "result_count": summarize(result_sizes),
        "sample_errors": errors,
    }


def benchmark_range_query(
    base_url: str,
    query: str,
    repeats: int,
    lookback_minutes: int,
    step_seconds: int,
    timeout: float,
) -> Dict[str, Any]:
    lat_all: List[float] = []
    lat_success: List[float] = []
    points_count: List[float] = []
    errors: List[str] = []
    success_count = 0

    for _ in range(repeats):
        end_ts = int(time.time())
        start_ts = end_ts - (lookback_minutes * 60)

        start = time.perf_counter()
        try:
            result = range_query(base_url, query, start_ts, end_ts, step_seconds, timeout)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            lat_all.append(elapsed_ms)
            lat_success.append(elapsed_ms)
            points = sum(len(series.get("values", [])) for series in result)
            points_count.append(float(points))
            success_count += 1
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            lat_all.append(elapsed_ms)
            if len(errors) < 5:
                errors.append(str(exc))

    requested = repeats
    failed = requested - success_count
    success_rate = (success_count / requested * 100.0) if requested else 0.0

    return {
        "requested": requested,
        "successful": success_count,
        "failed": failed,
        "success_rate_pct": success_rate,
        "latency_ms_success": summarize(lat_success),
        "latency_ms_all": summarize(lat_all),
        "returned_points": summarize(points_count),
        "lookback_minutes": lookback_minutes,
        "step_seconds": step_seconds,
        "sample_errors": errors,
    }


def benchmark_load(
    base_url: str,
    query: str,
    concurrency: int,
    duration_seconds: int,
    timeout: float,
) -> Dict[str, Any]:
    deadline = time.monotonic() + duration_seconds
    lock = threading.Lock()

    total = 0
    success = 0
    failures = 0
    lat_success: List[float] = []
    lat_all: List[float] = []
    errors: List[str] = []

    def worker() -> None:
        nonlocal total, success, failures
        while time.monotonic() < deadline:
            start = time.perf_counter()
            try:
                instant_query(base_url, query, timeout)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                with lock:
                    total += 1
                    success += 1
                    lat_all.append(elapsed_ms)
                    lat_success.append(elapsed_ms)
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                with lock:
                    total += 1
                    failures += 1
                    lat_all.append(elapsed_ms)
                    if len(errors) < 10:
                        errors.append(str(exc))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(worker) for _ in range(max(1, concurrency))]
        for future in futures:
            future.result()

    duration = float(duration_seconds) if duration_seconds > 0 else 1.0
    req_per_sec = total / duration
    success_rate = (success / total * 100.0) if total else 0.0

    return {
        "duration_seconds": duration_seconds,
        "concurrency": concurrency,
        "total_requests": total,
        "successful": success,
        "failed": failures,
        "success_rate_pct": success_rate,
        "throughput_req_per_sec": req_per_sec,
        "latency_ms_success": summarize(lat_success),
        "latency_ms_all": summarize(lat_all),
        "sample_errors": errors,
    }


def collect_target_health(base_url: str, timeout: float) -> Dict[str, Any]:
    url = ensure_no_trailing_slash(base_url) + "/api/v1/targets?state=active"
    try:
        data = http_get_json(url, timeout)
        if data.get("status") != "success":
            raise RuntimeError("targets API did not return success")

        active = data.get("data", {}).get("activeTargets", [])
        total = len(active)
        healthy = 0
        unhealthy = 0

        for target in active:
            if target.get("health") == "up":
                healthy += 1
            else:
                unhealthy += 1

        return {
            "total_active_targets": total,
            "healthy_targets": healthy,
            "unhealthy_targets": unhealthy,
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "total_active_targets": None,
            "healthy_targets": None,
            "unhealthy_targets": None,
        }


def collect_docker_stats(container_names: List[str]) -> Dict[str, Any]:
    cmd = ["docker", "stats", "--no-stream", "--format", "{{json .}}"]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "containers": {},
            "totals": {},
        }

    wanted = set(container_names)
    containers: Dict[str, Any] = {}

    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        name = row.get("Name") or row.get("Container")
        if not name:
            continue

        if wanted and name not in wanted:
            continue

        mem_usage_raw = row.get("MemUsage", "")
        mem_used = parse_mem_usage_used_bytes(mem_usage_raw)

        containers[name] = {
            "cpu_percent": parse_percent(row.get("CPUPerc", "")),
            "mem_percent": parse_percent(row.get("MemPerc", "")),
            "mem_usage_raw": mem_usage_raw,
            "mem_used_bytes": mem_used,
            "mem_used_mib": bytes_to_mib(mem_used),
            "net_io": row.get("NetIO", ""),
            "block_io": row.get("BlockIO", ""),
            "pids": row.get("PIDs", ""),
        }

    total_mem = sum(v.get("mem_used_bytes", 0) or 0 for v in containers.values())

    return {
        "available": True,
        "error": None,
        "containers": containers,
        "totals": {
            "containers_count": len(containers),
            "total_mem_used_bytes": total_mem,
            "total_mem_used_mib": bytes_to_mib(total_mem),
        },
    }


def benchmark_endpoint(
    name: str,
    base_url: str,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "name": name,
        "base_url": base_url,
        "alive": endpoint_alive(base_url, args.timeout),
        "instant_queries": {},
        "range_query": {},
        "load_test": {},
        "targets": {},
    }

    for query_name, promql in QUERY_SET.items():
        result["instant_queries"][query_name] = benchmark_instant_query(
            base_url=base_url,
            query=promql,
            samples=args.samples,
            warmup=args.warmup,
            timeout=args.timeout,
        )

    result["range_query"] = benchmark_range_query(
        base_url=base_url,
        query="up",
        repeats=args.range_repeats,
        lookback_minutes=args.range_lookback_minutes,
        step_seconds=args.range_step_seconds,
        timeout=args.timeout,
    )

    if args.load_duration_seconds > 0:
        result["load_test"] = benchmark_load(
            base_url=base_url,
            query="up",
            concurrency=args.load_concurrency,
            duration_seconds=args.load_duration_seconds,
            timeout=args.timeout,
        )
    else:
        result["load_test"] = {
            "skipped": True,
            "reason": "load_duration_seconds <= 0",
        }

    result["targets"] = collect_target_health(base_url, args.timeout)
    return result


def derive_thanos_overhead(run_result: Dict[str, Any]) -> Dict[str, Any]:
    endpoints = run_result.get("endpoints", {})
    prom = endpoints.get("prometheus")
    thanos = endpoints.get("thanos_query")
    if not prom or not thanos:
        return {}

    overhead: Dict[str, Any] = {"instant_queries": {}}

    prom_queries = prom.get("instant_queries", {})
    thanos_queries = thanos.get("instant_queries", {})

    for query_name in QUERY_SET:
        p_avg = (
            prom_queries.get(query_name, {})
            .get("latency_ms_success", {})
            .get("avg")
        )
        t_avg = (
            thanos_queries.get(query_name, {})
            .get("latency_ms_success", {})
            .get("avg")
        )
        if p_avg is None or t_avg is None:
            continue

        delta_ms = t_avg - p_avg
        delta_pct = (delta_ms / p_avg * 100.0) if p_avg else None
        overhead["instant_queries"][query_name] = {
            "prometheus_avg_ms": p_avg,
            "thanos_avg_ms": t_avg,
            "delta_ms": delta_ms,
            "delta_pct": delta_pct,
        }

    p_load = prom.get("load_test", {})
    t_load = thanos.get("load_test", {})
    p_qps = p_load.get("throughput_req_per_sec")
    t_qps = t_load.get("throughput_req_per_sec")
    if p_qps is not None and t_qps is not None:
        overhead["load_test"] = {
            "prometheus_qps": p_qps,
            "thanos_qps": t_qps,
            "delta_qps": t_qps - p_qps,
            "delta_pct": ((t_qps - p_qps) / p_qps * 100.0) if p_qps else None,
        }

    return overhead


def run_mode(args: argparse.Namespace) -> int:
    prom_url = ensure_no_trailing_slash(args.prom_url)
    thanos_url = ensure_no_trailing_slash(args.thanos_url)

    prom_up = endpoint_alive(prom_url, args.timeout)
    thanos_up = endpoint_alive(thanos_url, args.timeout)

    if not prom_up:
        print("ERROR: Prometheus endpoint is not reachable.", file=sys.stderr)
        print("Tried: " + prom_url, file=sys.stderr)
        return 2

    endpoints_to_benchmark = {"prometheus": prom_url}
    if thanos_up:
        endpoints_to_benchmark["thanos_query"] = thanos_url

    endpoint_results: Dict[str, Any] = {}
    for name, base_url in endpoints_to_benchmark.items():
        endpoint_results[name] = benchmark_endpoint(name, base_url, args)

    docker_stats = collect_docker_stats(
        ["prometheus", "thanos-sidecar", "thanos-query", "minio"]
    )

    mode = "prometheus+thanos" if thanos_up else "prometheus-only"
    run_result: Dict[str, Any] = {
        "generated_at_utc": utc_now_iso(),
        "mode_detected": mode,
        "detection": {
            "prometheus_url": prom_url,
            "prometheus_up": prom_up,
            "thanos_query_url": thanos_url,
            "thanos_query_up": thanos_up,
        },
        "config": {
            "samples": args.samples,
            "warmup": args.warmup,
            "timeout_seconds": args.timeout,
            "range_repeats": args.range_repeats,
            "range_lookback_minutes": args.range_lookback_minutes,
            "range_step_seconds": args.range_step_seconds,
            "load_concurrency": args.load_concurrency,
            "load_duration_seconds": args.load_duration_seconds,
        },
        "endpoints": endpoint_results,
        "docker_stats": docker_stats,
    }

    run_result["derived"] = {
        "thanos_overhead": derive_thanos_overhead(run_result),
    }

    output_path = args.output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(run_result, f, indent=2)

    print("Saved benchmark JSON: " + output_path)
    print("Detected mode: " + mode)

    if args.output_md:
        summary = render_run_markdown(run_result)
        with open(args.output_md, "w", encoding="utf-8") as f:
            f.write(summary)
        print("Saved benchmark markdown: " + args.output_md)

    return 0


def get_nested(data: Dict[str, Any], path: List[str]) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def render_run_markdown(run_result: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Observability Benchmark Run")
    lines.append("")
    lines.append("Generated at: " + str(run_result.get("generated_at_utc")))
    lines.append("Detected mode: " + str(run_result.get("mode_detected")))
    lines.append("")

    detection = run_result.get("detection", {})
    lines.append("## Endpoint Detection")
    lines.append("")
    lines.append("- Prometheus up: " + str(detection.get("prometheus_up")))
    lines.append("- Thanos query up: " + str(detection.get("thanos_query_up")))
    lines.append("")

    lines.append("## Instant Query Latency (avg ms)")
    lines.append("")
    lines.append("| Endpoint | Query | Avg ms | P95 ms | Success % |")
    lines.append("|---|---|---:|---:|---:|")

    for endpoint_name, endpoint_data in run_result.get("endpoints", {}).items():
        for query_name, query_data in endpoint_data.get("instant_queries", {}).items():
            lat = query_data.get("latency_ms_success", {})
            avg = lat.get("avg")
            p95 = lat.get("p95")
            success = query_data.get("success_rate_pct")

            avg_txt = f"{avg:.2f}" if isinstance(avg, (int, float)) else "n/a"
            p95_txt = f"{p95:.2f}" if isinstance(p95, (int, float)) else "n/a"
            succ_txt = f"{success:.1f}" if isinstance(success, (int, float)) else "n/a"

            lines.append(
                f"| {endpoint_name} | {query_name} | {avg_txt} | {p95_txt} | {succ_txt} |"
            )

    lines.append("")

    lines.append("## Load Test")
    lines.append("")
    lines.append("| Endpoint | Req/s | Avg ms | P95 ms | Success % |")
    lines.append("|---|---:|---:|---:|---:|")

    for endpoint_name, endpoint_data in run_result.get("endpoints", {}).items():
        load = endpoint_data.get("load_test", {})
        if load.get("skipped"):
            lines.append(f"| {endpoint_name} | skipped | skipped | skipped | skipped |")
            continue

        qps = load.get("throughput_req_per_sec")
        lat = load.get("latency_ms_success", {})
        avg = lat.get("avg")
        p95 = lat.get("p95")
        success = load.get("success_rate_pct")

        qps_txt = f"{qps:.2f}" if isinstance(qps, (int, float)) else "n/a"
        avg_txt = f"{avg:.2f}" if isinstance(avg, (int, float)) else "n/a"
        p95_txt = f"{p95:.2f}" if isinstance(p95, (int, float)) else "n/a"
        succ_txt = f"{success:.1f}" if isinstance(success, (int, float)) else "n/a"

        lines.append(f"| {endpoint_name} | {qps_txt} | {avg_txt} | {p95_txt} | {succ_txt} |")

    lines.append("")

    docker_stats = run_result.get("docker_stats", {})
    lines.append("## Container Resource Snapshot")
    lines.append("")

    if not docker_stats.get("available"):
        lines.append("Docker stats unavailable: " + str(docker_stats.get("error")))
    else:
        lines.append("| Container | CPU % | Mem MiB | Mem % |")
        lines.append("|---|---:|---:|---:|")
        for name, stats_data in docker_stats.get("containers", {}).items():
            cpu = stats_data.get("cpu_percent")
            mem_mib = stats_data.get("mem_used_mib")
            mem_pct = stats_data.get("mem_percent")

            cpu_txt = f"{cpu:.2f}" if isinstance(cpu, (int, float)) else "n/a"
            mem_txt = f"{mem_mib:.2f}" if isinstance(mem_mib, (int, float)) else "n/a"
            memp_txt = f"{mem_pct:.2f}" if isinstance(mem_pct, (int, float)) else "n/a"
            lines.append(f"| {name} | {cpu_txt} | {mem_txt} | {memp_txt} |")

    lines.append("")
    return "\n".join(lines)


def compare_runs(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    output: Dict[str, Any] = {
        "left_mode": left.get("mode_detected"),
        "right_mode": right.get("mode_detected"),
        "instant_query_comparison": {},
        "load_test_comparison": {},
        "resource_comparison": {},
        "interpretation": {
            "prometheus_only_pros": [],
            "prometheus_only_cons": [],
            "thanos_stack_pros": [],
            "thanos_stack_cons": [],
        },
    }

    left_prom = get_nested(left, ["endpoints", "prometheus", "instant_queries"]) or {}
    right_prom = get_nested(right, ["endpoints", "prometheus", "instant_queries"]) or {}
    right_thanos = get_nested(right, ["endpoints", "thanos_query", "instant_queries"]) or {}

    for query_name in QUERY_SET:
        left_avg = get_nested(left_prom.get(query_name, {}), ["latency_ms_success", "avg"])
        right_prom_avg = get_nested(right_prom.get(query_name, {}), ["latency_ms_success", "avg"])
        right_thanos_avg = get_nested(right_thanos.get(query_name, {}), ["latency_ms_success", "avg"])

        row: Dict[str, Any] = {
            "left_prometheus_avg_ms": left_avg,
            "right_prometheus_avg_ms": right_prom_avg,
            "right_thanos_query_avg_ms": right_thanos_avg,
        }

        if isinstance(right_prom_avg, (int, float)) and isinstance(right_thanos_avg, (int, float)) and right_prom_avg > 0:
            row["thanos_overhead_vs_right_prometheus_pct"] = (
                (right_thanos_avg - right_prom_avg) / right_prom_avg * 100.0
            )

        if isinstance(left_avg, (int, float)) and isinstance(right_thanos_avg, (int, float)) and left_avg > 0:
            row["right_thanos_vs_left_prometheus_pct"] = (
                (right_thanos_avg - left_avg) / left_avg * 100.0
            )

        output["instant_query_comparison"][query_name] = row

    left_load = get_nested(left, ["endpoints", "prometheus", "load_test"]) or {}
    right_prom_load = get_nested(right, ["endpoints", "prometheus", "load_test"]) or {}
    right_thanos_load = get_nested(right, ["endpoints", "thanos_query", "load_test"]) or {}

    output["load_test_comparison"] = {
        "left_prometheus_qps": left_load.get("throughput_req_per_sec"),
        "right_prometheus_qps": right_prom_load.get("throughput_req_per_sec"),
        "right_thanos_query_qps": right_thanos_load.get("throughput_req_per_sec"),
    }

    left_mem = get_nested(left, ["docker_stats", "totals", "total_mem_used_mib"])
    right_mem = get_nested(right, ["docker_stats", "totals", "total_mem_used_mib"])

    output["resource_comparison"] = {
        "left_total_mem_mib": left_mem,
        "right_total_mem_mib": right_mem,
        "delta_mem_mib": (right_mem - left_mem)
        if isinstance(left_mem, (int, float)) and isinstance(right_mem, (int, float))
        else None,
    }

    # Build report-ready interpretation hints.
    prompts = output["interpretation"]

    if isinstance(left_mem, (int, float)) and isinstance(right_mem, (int, float)):
        if right_mem > left_mem:
            prompts["prometheus_only_pros"].append(
                f"Lower memory footprint by about {right_mem - left_mem:.2f} MiB in this run."
            )
            prompts["thanos_stack_cons"].append(
                f"Higher memory footprint by about {right_mem - left_mem:.2f} MiB in this run."
            )
        elif left_mem > right_mem:
            prompts["thanos_stack_pros"].append(
                f"Lower memory footprint by about {left_mem - right_mem:.2f} MiB in this run."
            )

    has_right_thanos = bool(get_nested(right, ["endpoints", "thanos_query"]))
    if has_right_thanos:
        prompts["thanos_stack_pros"].append(
            "Provides an additional query layer that can aggregate across multiple Prometheus instances."
        )
        prompts["prometheus_only_cons"].append(
            "No dedicated global query layer for multi-cluster aggregation."
        )

    prompts["thanos_stack_pros"].append(
        "Can use object storage for long-term retention (if sidecar/store components are enabled)."
    )
    prompts["prometheus_only_cons"].append(
        "Local-only retention unless extra remote storage components are added."
    )

    prompts["prometheus_only_pros"].append(
        "Simpler deployment and fewer moving parts to operate."
    )
    prompts["thanos_stack_cons"].append(
        "Higher operational complexity due to extra components and object storage integration."
    )

    return output


def render_compare_markdown(
    left_label: str,
    right_label: str,
    compare_result: Dict[str, Any],
) -> str:
    lines: List[str] = []
    lines.append("# Benchmark Comparison")
    lines.append("")
    lines.append(f"Left run: {left_label}")
    lines.append(f"Right run: {right_label}")
    lines.append("")

    lines.append("## Instant Query Latency")
    lines.append("")
    lines.append("| Query | Left Prom avg ms | Right Prom avg ms | Right Thanos avg ms | Thanos overhead vs right Prom % |")
    lines.append("|---|---:|---:|---:|---:|")

    for query_name, row in compare_result.get("instant_query_comparison", {}).items():
        left_avg = row.get("left_prometheus_avg_ms")
        right_prom = row.get("right_prometheus_avg_ms")
        right_thanos = row.get("right_thanos_query_avg_ms")
        overhead = row.get("thanos_overhead_vs_right_prometheus_pct")

        left_txt = f"{left_avg:.2f}" if isinstance(left_avg, (int, float)) else "n/a"
        rprom_txt = f"{right_prom:.2f}" if isinstance(right_prom, (int, float)) else "n/a"
        rth_txt = f"{right_thanos:.2f}" if isinstance(right_thanos, (int, float)) else "n/a"
        oh_txt = f"{overhead:.2f}" if isinstance(overhead, (int, float)) else "n/a"

        lines.append(f"| {query_name} | {left_txt} | {rprom_txt} | {rth_txt} | {oh_txt} |")

    lines.append("")

    load = compare_result.get("load_test_comparison", {})
    lines.append("## Load Throughput")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")

    for key in ["left_prometheus_qps", "right_prometheus_qps", "right_thanos_query_qps"]:
        value = load.get(key)
        value_txt = f"{value:.2f}" if isinstance(value, (int, float)) else "n/a"
        lines.append(f"| {key} | {value_txt} |")

    lines.append("")

    resources = compare_result.get("resource_comparison", {})
    lines.append("## Memory Snapshot")
    lines.append("")
    lines.append("| Metric | MiB |")
    lines.append("|---|---:|")
    for key in ["left_total_mem_mib", "right_total_mem_mib", "delta_mem_mib"]:
        value = resources.get(key)
        value_txt = f"{value:.2f}" if isinstance(value, (int, float)) else "n/a"
        lines.append(f"| {key} | {value_txt} |")

    lines.append("")

    interp = compare_result.get("interpretation", {})
    lines.append("## Interpretation")
    lines.append("")

    lines.append("### Prometheus-only Pros")
    for item in interp.get("prometheus_only_pros", []):
        lines.append("- " + item)
    lines.append("")

    lines.append("### Prometheus-only Cons")
    for item in interp.get("prometheus_only_cons", []):
        lines.append("- " + item)
    lines.append("")

    lines.append("### Thanos Stack Pros")
    for item in interp.get("thanos_stack_pros", []):
        lines.append("- " + item)
    lines.append("")

    lines.append("### Thanos Stack Cons")
    for item in interp.get("thanos_stack_cons", []):
        lines.append("- " + item)

    lines.append("")
    return "\n".join(lines)


def compare_mode(args: argparse.Namespace) -> int:
    with open(args.left, "r", encoding="utf-8") as f:
        left = json.load(f)
    with open(args.right, "r", encoding="utf-8") as f:
        right = json.load(f)

    compare_result = compare_runs(left, right)

    output: Dict[str, Any] = {
        "generated_at_utc": utc_now_iso(),
        "left_file": os.path.abspath(args.left),
        "right_file": os.path.abspath(args.right),
        "left_mode": left.get("mode_detected"),
        "right_mode": right.get("mode_detected"),
        "comparison": compare_result,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print("Saved comparison JSON: " + args.output)

    md_path = args.output_md
    if md_path:
        md = render_compare_markdown(args.left, args.right, compare_result)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        print("Saved comparison markdown: " + md_path)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark and compare Prometheus-only vs Prometheus+Thanos setups"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a benchmark against local stack")
    run_parser.add_argument("--prom-url", default="http://localhost:9090", help="Prometheus base URL")
    run_parser.add_argument("--thanos-url", default="http://localhost:10903", help="Thanos Query base URL")
    run_parser.add_argument("--samples", type=int, default=20, help="Instant query samples per query")
    run_parser.add_argument("--warmup", type=int, default=3, help="Warmup query calls per query")
    run_parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds")
    run_parser.add_argument("--range-repeats", type=int, default=5, help="Range query repeats")
    run_parser.add_argument(
        "--range-lookback-minutes",
        type=int,
        default=15,
        help="Range query lookback window in minutes",
    )
    run_parser.add_argument(
        "--range-step-seconds",
        type=int,
        default=15,
        help="Range query step in seconds",
    )
    run_parser.add_argument(
        "--load-concurrency",
        type=int,
        default=8,
        help="Concurrent workers in load test",
    )
    run_parser.add_argument(
        "--load-duration-seconds",
        type=int,
        default=20,
        help="Load test duration in seconds (0 to skip)",
    )
    run_parser.add_argument(
        "--output",
        default="benchmark_run.json",
        help="Output JSON file",
    )
    run_parser.add_argument(
        "--output-md",
        default="",
        help="Optional markdown summary output file",
    )

    compare_parser = subparsers.add_parser(
        "compare", help="Compare two benchmark JSON outputs"
    )
    compare_parser.add_argument("--left", required=True, help="Left benchmark JSON")
    compare_parser.add_argument("--right", required=True, help="Right benchmark JSON")
    compare_parser.add_argument(
        "--output",
        default="benchmark_compare.json",
        help="Output comparison JSON",
    )
    compare_parser.add_argument(
        "--output-md",
        default="comparison_report.md",
        help="Optional markdown report output",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        return run_mode(args)
    if args.command == "compare":
        return compare_mode(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
