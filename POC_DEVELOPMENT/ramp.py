#!/usr/bin/env python3
"""Distributed ramp/spike load orchestrator for the Boomi performance testing stack."""
import csv
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent
TARGETS_DIR = REPO_ROOT / "prometheus" / "targets"
RESULTS_DIR = REPO_ROOT / "test-plans" / "results"
TEST_PLANS_DIR = REPO_ROOT / "test-plans"
BASELINES_DIR = REPO_ROOT / "baselines"
GENERATED_DIR = TEST_PLANS_DIR / "generated"
PROMETHEUS_URL = "http://localhost:9090"
# Prometheus writes a staleness marker within a few seconds of a scrape
# target disappearing (observed empirically: ~3-4s after the target's last
# successful scrape, once its file_sd entry is removed). step_end is
# captured right as the container dies, which is right on that edge --
# querying at step_end itself is a race. Backing the query instant off by
# one full scrape_interval reliably lands before the staleness cutoff.
QUERY_SETTLE_SEC = 5
# A step needs at least this much measured time (after warmup) beyond the settle margin,
# so Prometheus gets a few scrapes inside the reporting window.
MIN_MEASURED_SEC = 10
GRAFANA_URL = "http://localhost:3004"
GRAFANA_AUTH = ("admin", "admin")
CONFIRMATION_THRESHOLD = 4


def post_grafana_annotation(text, tags, at_unix_time=None):
    if at_unix_time is None:
        at_unix_time = time.time()
    payload = json.dumps({
        "time": int(at_unix_time * 1000),
        "tags": tags,
        "text": text,
    }).encode()
    req = urllib.request.Request(f"{GRAFANA_URL}/api/annotations", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    credentials = f"{GRAFANA_AUTH[0]}:{GRAFANA_AUTH[1]}"
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(credentials.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:
        print(f"Warning: failed to post Grafana annotation: {e}", file=sys.stderr)

_current_step_state = {"run_id": None, "step_id": None, "instances": 0}


def _cleanup_current_step():
    run_id = _current_step_state["run_id"]
    step_id = _current_step_state["step_id"]
    instances = _current_step_state["instances"]
    if not run_id:
        return
    print(f"\nCleaning up {step_id}: stopping {instances} instance(s)...", file=sys.stderr)
    for idx in range(1, instances + 1):
        name = derive_instance_name(run_id, step_id, idx)
        subprocess.run(["docker", "stop", name], capture_output=True)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        delete_target_file(run_id, step_id, idx)


def _handle_interrupt(signum, frame):
    _cleanup_current_step()
    print("Interrupted. Prior steps' results remain in Prometheus; no summary report is printed for an interrupted run.", file=sys.stderr)
    sys.exit(130)


signal.signal(signal.SIGINT, _handle_interrupt)
signal.signal(signal.SIGTERM, _handle_interrupt)


def prom_instant_query(promql, at_unix_time):
    """Instant query evaluated AT a specific past timestamp (not 'now') --
    this avoids Prometheus staleness marking affecting results for targets
    that have since disappeared, which plain 'now' queries are vulnerable to.
    Degrades to an empty result vector (never raises) if Prometheus is
    unreachable or errors -- a report-time query failure must not crash a
    run whose actual load generation already completed successfully."""
    params = urllib.parse.urlencode({"query": promql, "time": at_unix_time})
    url = f"{PROMETHEUS_URL}/api/v1/query?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"Warning: Prometheus query failed ({e}): {promql!r}", file=sys.stderr)
        return {"status": "error", "data": {"resultType": "vector", "result": []}}


def _first_value(prom_response, default=0.0):
    """Pulls the first result's numeric value out of a Prometheus instant
    query response, or `default` if the result vector is empty (no data --
    this must never raise, an empty vector is a normal, expected outcome)."""
    results = prom_response.get("data", {}).get("result", [])
    if not results:
        return default
    return float(results[0]["value"][1])


def evaluate_stop_rule(profile, run_id, step_id, step_start, step_end, warmup_sec):
    stop_rule = profile.get("stop_rule", {})
    if not stop_rule:
        return False, None

    window_start = step_start + warmup_sec
    query_time = step_end - QUERY_SETTLE_SEC
    window_sec = max(query_time - window_start, 1)

    error_rate_pct = 0.0
    total_resp = prom_instant_query(
        f'increase(jmeter_requests_total{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s])',
        query_time,
    )
    total_requests = sum(float(r["value"][1]) for r in total_resp.get("data", {}).get("result", []))
    if total_requests > 0:
        fail_resp = prom_instant_query(
            f'increase(jmeter_failures_total{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s])',
            query_time,
        )
        total_failures = sum(float(r["value"][1]) for r in fail_resp.get("data", {}).get("result", []))
        error_rate_pct = 100.0 * total_failures / total_requests

    avg_resp = prom_instant_query(
        f'sum(increase(jmeter_response_time_ms_sum{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s])) '
        f'/ sum(increase(jmeter_response_time_ms_count{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s]))',
        query_time,
    )
    avg_response_ms = _first_value(avg_resp, default=0.0)

    if "max_error_rate_pct" in stop_rule and error_rate_pct > stop_rule["max_error_rate_pct"]:
        return True, f"error rate {error_rate_pct:.1f}% exceeded max_error_rate_pct={stop_rule['max_error_rate_pct']}"
    if "max_avg_response_ms" in stop_rule and avg_response_ms > stop_rule["max_avg_response_ms"]:
        return True, f"avg response {avg_response_ms:.0f}ms exceeded max_avg_response_ms={stop_rule['max_avg_response_ms']}"
    return False, None


def query_report_rows(run_id, step_id, step_start, step_end, warmup_sec):
    """One row per sampler label seen in this step, in JMeter's own
    Aggregate Report column convention."""
    window_start = step_start + warmup_sec
    query_time = step_end - QUERY_SETTLE_SEC
    window_sec = max(query_time - window_start, 1)

    labels_resp = prom_instant_query(
        f'group by (label) (jmeter_requests_total{{run_id="{run_id}", step_id="{step_id}"}})',
        query_time,
    )
    labels = sorted(r["metric"]["label"] for r in labels_resp.get("data", {}).get("result", []))

    if not labels:
        print(f"[{step_id}] WARNING: no Prometheus data found for this step in the window "
              f"ending at {query_time} -- reporting as NO DATA rather than omitting it.", file=sys.stderr)
        return [{
            "step_id": step_id, "label": "(no data)", "samples": 0,
            "avg_ms": 0.0, "median_ms": 0.0, "p90_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0,
            "error_pct": 0.0, "throughput": 0.0,
        }]

    rows = []
    for label in labels:
        sel = f'run_id="{run_id}", step_id="{step_id}", label="{label}"'

        samples = _first_value(prom_instant_query(f'sum(increase(jmeter_requests_total{{{sel}}}[{window_sec}s]))', query_time))
        failures = _first_value(prom_instant_query(f'sum(increase(jmeter_failures_total{{{sel}}}[{window_sec}s]))', query_time))
        error_pct = (100.0 * failures / samples) if samples > 0 else 0.0
        throughput = samples / window_sec if window_sec > 0 else 0.0

        avg_ms = _first_value(prom_instant_query(
            f'sum(increase(jmeter_response_time_ms_sum{{{sel}}}[{window_sec}s])) '
            f'/ sum(increase(jmeter_response_time_ms_count{{{sel}}}[{window_sec}s]))',
            query_time,
        ))
        median_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.50, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', query_time))
        p90_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.90, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', query_time))
        p95_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.95, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', query_time))
        p99_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.99, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', query_time))

        rows.append({
            "step_id": step_id,
            "label": label,
            "samples": int(samples),
            "avg_ms": avg_ms,
            "median_ms": median_ms,
            "p90_ms": p90_ms,
            "p95_ms": p95_ms,
            "p99_ms": p99_ms,
            "error_pct": error_pct,
            "throughput": throughput,
        })
    return rows


def print_summary_report(all_rows):
    print()
    print("=== Summary Report ===")
    header = f"{'Step':<10} {'Label':<28} {'Samples':>8} {'Avg':>8} {'Median':>8} {'90%':>8} {'95%':>8} {'99%':>8} {'Error%':>8} {'Thrpt':>8}"
    print(header)
    print("-" * len(header))
    for row in all_rows:
        flag = " ⚠ possible generator saturation" if row.get("saturated") else ""
        print(
            f"{row['step_id']:<10} {row['label']:<28} {row['samples']:>8} "
            f"{row['avg_ms']:>8.0f} {row['median_ms']:>8.0f} {row['p90_ms']:>8.0f} "
            f"{row['p95_ms']:>8.0f} {row['p99_ms']:>8.0f} {row['error_pct']:>7.1f}% {row['throughput']:>7.2f}/s{flag}"
        )


def scan_slow_requests(run_id, step_id, instances, threshold_ms):
    slow = []
    for idx in range(1, instances + 1):
        jtl_path = RESULTS_DIR / f"{run_id}-{step_id}-{idx}.jtl"
        if not jtl_path.exists():
            continue
        with open(jtl_path, newline="") as f:
            for row in csv.DictReader(f):
                elapsed = int(row["elapsed"])
                if elapsed > threshold_ms:
                    slow.append({
                        "step_id": step_id,
                        "timestamp": row["timeStamp"],
                        "label": row["label"],
                        "elapsed_ms": elapsed,
                        "response_code": row["responseCode"],
                    })
    return slow


def scan_jtl_failures(run_id, step_id, instances, top_n=3):
    """Failures straight from JMeter's own results files, which don't depend on Prometheus scrape timing.
    Returns {"step_id", "total", "failed", "top": [(code, message, count), ...]}."""
    total = failed = 0
    counts = {}
    for idx in range(1, instances + 1):
        jtl_path = RESULTS_DIR / f"{run_id}-{step_id}-{idx}.jtl"
        if not jtl_path.exists():
            continue
        with open(jtl_path, newline="") as f:
            for row in csv.DictReader(f):
                total += 1
                if row.get("success", "true").lower() != "true":
                    failed += 1
                    key = (row.get("responseCode", ""), (row.get("responseMessage", "") or "")[:160])
                    counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return {"step_id": step_id, "total": total, "failed": failed,
            "top": [(code, msg, n) for (code, msg), n in top]}


def print_jtl_failures(summaries):
    """Prints steps that had failed samples, with the most common error messages."""
    bad = [x for x in summaries if x["failed"] > 0]
    if not bad:
        return
    print()
    print("=== Failures seen in JMeter's raw results ===")
    for x in bad:
        pct = 100.0 * x["failed"] / x["total"] if x["total"] else 0.0
        print(f"{x['step_id']}: {x['failed']} of {x['total']} samples failed ({pct:.1f}%)")
        for code, msg, n in x["top"]:
            print(f"    {n} x [{code}] {msg}")


def short_step_warnings(profile):
    """Steps too short for Prometheus (scraped every few seconds) to measure reliably."""
    warnings = []
    warmup = profile.get("warmup_sec", 0)
    for i, step in enumerate(profile["steps"]):
        duration = step.get("duration_sec", profile.get("step_duration_sec"))
        if isinstance(duration, (int, float)) and duration - warmup < MIN_MEASURED_SEC + QUERY_SETTLE_SEC:
            warnings.append(f"step-{i + 1:02d}: only {duration - warmup:g}s of measured time after warmup; "
                            f"results from steps shorter than about {MIN_MEASURED_SEC + QUERY_SETTLE_SEC + warmup:g}s "
                            f"can be incomplete or empty. Use a longer duration_sec.")
    return warnings


def print_slow_requests_table(rows, total_requests):
    print()
    print(f"=== Slow Requests (total requests: {total_requests}, slow: {len(rows)}) ===")
    if not rows:
        print("(none)")
        return
    header = f"{'Step':<10} {'Timestamp':<15} {'Label':<28} {'Elapsed (ms)':>14} {'Code':>6}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row['step_id']:<10} {row['timestamp']:<15} {row['label']:<28} {row['elapsed_ms']:>14} {row['response_code']:>6}")


def load_profile(path):
    with open(path) as f:
        return yaml.safe_load(f)


def validate_profile(profile):
    """Returns a list of human-readable problems. Empty list means valid."""
    errors = []
    if not isinstance(profile, dict):
        return [f"profile must be a YAML mapping (top-level keys like test_plan/steps), got {type(profile).__name__}"]
    if not profile.get("test_plan"):
        errors.append("missing required key: test_plan")
    has_profile_duration = "step_duration_sec" in profile and isinstance(profile["step_duration_sec"], (int, float)) and profile["step_duration_sec"] >= 1
    if not profile.get("steps"):
        errors.append("steps list is missing or empty")
    else:
        for i, step in enumerate(profile["steps"]):
            label = f"steps[{i}]"
            if not isinstance(step, dict):
                errors.append(f"{label}: must be a mapping (instances/threads/...), got {type(step).__name__}")
                continue
            if "instances" not in step:
                errors.append(f"{label}: missing 'instances'")
            elif not isinstance(step["instances"], int) or step["instances"] < 1:
                errors.append(f"{label}: 'instances' must be a positive integer, got {step.get('instances')!r}")
            if "threads" not in step:
                errors.append(f"{label}: missing 'threads'")
            elif not isinstance(step["threads"], int) or step["threads"] < 1:
                errors.append(f"{label}: 'threads' must be a positive integer, got {step.get('threads')!r}")
            has_step_duration = "duration_sec" in step and isinstance(step["duration_sec"], (int, float)) and step["duration_sec"] >= 1
            if "duration_sec" in step and not has_step_duration:
                errors.append(f"{label}: 'duration_sec' must be a positive integer, got {step['duration_sec']!r}")
            elif not has_step_duration and not has_profile_duration:
                errors.append(f"{label}: no duration -- set 'duration_sec' on this step or a valid "
                               f"'step_duration_sec' at the top level")
    if "step_duration_sec" in profile and not has_profile_duration:
        errors.append(f"step_duration_sec must be a positive integer, got {profile['step_duration_sec']!r}")
    if "warmup_sec" in profile and (not isinstance(profile["warmup_sec"], (int, float)) or profile["warmup_sec"] < 0):
        errors.append(f"warmup_sec must be a non-negative number, got {profile['warmup_sec']!r}")
    if "slow_request_threshold_ms" in profile and (
        not isinstance(profile["slow_request_threshold_ms"], (int, float)) or profile["slow_request_threshold_ms"] <= 0
    ):
        errors.append(f"slow_request_threshold_ms must be a positive number, got {profile['slow_request_threshold_ms']!r}")
    stop_rule = profile.get("stop_rule", {})
    if not isinstance(stop_rule, dict):
        errors.append(f"stop_rule must be a mapping, got {type(stop_rule).__name__}")
        stop_rule = {}
    if "max_error_rate_pct" in stop_rule:
        v = stop_rule["max_error_rate_pct"]
        if not isinstance(v, (int, float)) or not (0 <= v <= 100):
            errors.append(f"stop_rule.max_error_rate_pct must be between 0 and 100, got {v!r}")
    if "max_avg_response_ms" in stop_rule:
        v = stop_rule["max_avg_response_ms"]
        if not isinstance(v, (int, float)) or v <= 0:
            errors.append(f"stop_rule.max_avg_response_ms must be a positive number, got {v!r}")
    errors.extend(_validate_endpoints(profile))
    errors.extend(_validate_pacing(profile))
    errors.extend(_validate_regression(profile))
    return errors


VALID_AUTH = ("none", "basic", "api_key")
# Database load tests (target.protocol: jdbc). The driver jar must be in the jmeter image (jmeter/Dockerfile).
JDBC_DRIVERS = {
    "postgresql": {"class": "org.postgresql.Driver", "url": "jdbc:postgresql://{host}:{port}/{database}", "port": 5432},
}
JDBC_PARAM_TYPES = ("INTEGER", "BIGINT", "SMALLINT", "VARCHAR", "DECIMAL", "DOUBLE", "BOOLEAN", "TIMESTAMP")
JDBC_QUERY_TYPES = ("select", "update")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
DEFAULT_DB_USER_ENV = "BOOMI_TEST_DB_USER"
DEFAULT_DB_PASSWORD_ENV = "BOOMI_TEST_DB_PASSWORD"
VALID_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
EXTRACT_RE = re.compile(r"^[A-Za-z_]\w*=\S.*$")


def _validate_endpoints(profile):
    """Validates the optional target/requests blocks. They go together: either
    both are present (ramp.py generates the JMeter plan) or neither is (the
    hand-built test_plan runs as-is)."""
    errors = []
    has_target = "target" in profile
    has_requests = "requests" in profile
    if not has_target and not has_requests:
        return errors
    if has_requests and not has_target:
        errors.append("'requests' needs a 'target' block (protocol/host/port)")
    if has_target and not has_requests:
        errors.append("'target' needs a 'requests' list")

    target = profile.get("target")
    if isinstance(target, dict) and target.get("protocol") == "jdbc":
        return errors + _validate_jdbc(profile)
    if has_target:
        if not isinstance(target, dict):
            errors.append(f"target must be a mapping, got {type(target).__name__}")
        else:
            if not target.get("host") or not isinstance(target["host"], str):
                errors.append("target.host is required")
            if target.get("protocol", "http") not in ("http", "https"):
                errors.append(f"target.protocol must be http or https, got {target.get('protocol')!r}")
            port = target.get("port")
            if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535):
                errors.append(f"target.port must be an integer 1-65535, got {port!r}")
            if target.get("auth", "none") not in VALID_AUTH:
                errors.append(f"target.auth must be one of {list(VALID_AUTH)}, got {target.get('auth')!r}")

    requests = profile.get("requests")
    if has_requests:
        if not isinstance(requests, list) or not requests:
            errors.append("requests must be a non-empty list")
        else:
            seen = set()
            for i, req in enumerate(requests):
                label = f"requests[{i}]"
                if not isinstance(req, dict):
                    errors.append(f"{label}: must be a mapping, got {type(req).__name__}")
                    continue
                name = req.get("name")
                if not name or not isinstance(name, str):
                    errors.append(f"{label}: missing 'name'")
                elif name in seen:
                    errors.append(f"{label}: duplicate name {name!r} (names become the Grafana labels, so must be unique)")
                else:
                    seen.add(name)
                if req.get("method", "GET") not in VALID_METHODS:
                    errors.append(f"{label}: 'method' must be one of {list(VALID_METHODS)}, got {req.get('method')!r}")
                path = req.get("path")
                if not isinstance(path, str) or not path.startswith("/"):
                    errors.append(f"{label}: 'path' must be a string starting with '/', got {path!r}")
                if "extract" in req and (not isinstance(req["extract"], str) or not EXTRACT_RE.match(req["extract"])):
                    errors.append(f"{label}: 'extract' must look like 'var=$.json.path', got {req['extract']!r}")
                if "body" in req and not isinstance(req["body"], str):
                    errors.append(f"{label}: 'body' must be a string, got {type(req['body']).__name__}")
    return errors


def _validate_jdbc(profile):
    """Validates a database target: target.protocol is jdbc and each request is a SQL query."""
    errors = []
    target = profile["target"]
    driver = target.get("driver")
    if driver not in JDBC_DRIVERS:
        errors.append(f"target.driver must be one of {list(JDBC_DRIVERS)} for protocol jdbc, got {driver!r}")
    if not target.get("host") or not isinstance(target["host"], str):
        errors.append("target.host is required")
    if not target.get("database") or not isinstance(target["database"], str):
        errors.append("target.database is required for protocol jdbc")
    port = target.get("port")
    if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535):
        errors.append(f"target.port must be an integer 1-65535, got {port!r}")
    if target.get("auth", "none") != "none":
        errors.append("target.auth does not apply to jdbc; the login comes from BOOMI_TEST_DB_USER and "
                      "BOOMI_TEST_DB_PASSWORD in .env, never from the profile")
    for key in ("user_env", "password_env"):
        if key in target and (not isinstance(target[key], str) or not ENV_NAME_RE.match(target[key])):
            errors.append(f"target.{key} must be the NAME of an environment variable in .env "
                          f"(capital letters, digits, underscores), not the value itself; got {target[key]!r}")
    allow_writes = target.get("allow_writes", False)
    if not isinstance(allow_writes, bool):
        errors.append(f"target.allow_writes must be true or false, got {allow_writes!r}")

    requests = profile.get("requests")
    if "requests" not in profile:
        return errors
    if not isinstance(requests, list) or not requests:
        return errors + ["requests must be a non-empty list of queries"]
    seen = set()
    for i, req in enumerate(requests):
        label = f"requests[{i}]"
        if not isinstance(req, dict):
            errors.append(f"{label}: must be a mapping, got {type(req).__name__}")
            continue
        name = req.get("name")
        if not name or not isinstance(name, str):
            errors.append(f"{label}: missing 'name'")
        elif name in seen:
            errors.append(f"{label}: duplicate name {name!r} (names become the Grafana labels, so must be unique)")
        else:
            seen.add(name)
        for key in ("method", "path", "body", "extract"):
            if key in req:
                errors.append(f"{label}: '{key}' is an HTTP field and does not apply to a jdbc query")
        sql = req.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            errors.append(f"{label}: 'sql' must be a non-empty string")
            continue
        query_type = req.get("query_type", "select")
        if query_type not in JDBC_QUERY_TYPES:
            errors.append(f"{label}: 'query_type' must be one of {list(JDBC_QUERY_TYPES)}, got {query_type!r}")
        elif query_type == "update" and allow_writes is not True:
            errors.append(f"{label}: this query writes to the database; set target.allow_writes: true to run it")
        params = req.get("params", [])
        if not isinstance(params, list):
            errors.append(f"{label}: 'params' must be a list of {{value, type}} entries")
            continue
        for j, param in enumerate(params):
            if not isinstance(param, dict) or "value" not in param:
                errors.append(f"{label}.params[{j}]: must be a mapping with 'value' and 'type'")
            elif param.get("type") not in JDBC_PARAM_TYPES:
                errors.append(f"{label}.params[{j}]: 'type' must be one of {list(JDBC_PARAM_TYPES)}, got {param.get('type')!r}")
            elif "," in str(param["value"]) and "${" not in str(param["value"]):
                errors.append(f"{label}.params[{j}]: a value cannot contain a comma")
        if sql.count("?") != len(params):
            errors.append(f"{label}: the sql has {sql.count('?')} '?' placeholder(s) but params has {len(params)} entr{'y' if len(params) == 1 else 'ies'}")
    return errors


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_pacing(profile):
    """think_time_ms / rps only exist in generated plans: a hand-built
    test_plan has its own timers, which ramp.py must not guess at."""
    errors = []
    has_requests = "requests" in profile
    if "think_time_ms" in profile:
        v = profile["think_time_ms"]
        if not _is_number(v) or v < 0:
            errors.append(f"think_time_ms must be a non-negative number, got {v!r}")
        elif not has_requests:
            errors.append("think_time_ms needs target/requests (it is applied to generated plans only)")
    for i, req in enumerate(profile.get("requests") or []):
        if isinstance(req, dict) and "think_time_ms" in req:
            v = req["think_time_ms"]
            if not _is_number(v) or v < 0:
                errors.append(f"requests[{i}]: 'think_time_ms' must be a non-negative number, got {v!r}")
    for i, step in enumerate(profile.get("steps") or []):
        if isinstance(step, dict) and "rps" in step:
            v = step["rps"]
            if not _is_number(v) or v <= 0:
                errors.append(f"steps[{i}]: 'rps' must be a positive number, got {v!r}")
            elif not has_requests:
                errors.append(f"steps[{i}]: 'rps' needs target/requests (it is applied to generated plans only)")
        if isinstance(step, dict) and "tps" in step:
            v = step["tps"]
            if not _is_number(v) or v <= 0:
                errors.append(f"steps[{i}]: 'tps' must be a positive number, got {v!r}")
            elif not has_requests:
                errors.append(f"steps[{i}]: 'tps' needs target/requests (it is applied to generated plans only)")
            if "rps" in step:
                errors.append(f"steps[{i}]: set either 'tps' (iterations/sec) or 'rps' (requests/sec), not both")
    return errors


def _validate_regression(profile):
    errors = []
    if "regression" not in profile:
        return errors
    reg = profile["regression"]
    if not isinstance(reg, dict):
        return [f"regression must be a mapping, got {type(reg).__name__}"]
    name = reg.get("baseline")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        errors.append(f"regression.baseline must be a name made of letters, digits, '.', '_' or '-', got {name!r}")
    for key in ("max_p95_increase_pct", "max_error_rate_increase_pct", "min_p95_delta_ms"):
        if key in reg and (not _is_number(reg[key]) or reg[key] < 0):
            errors.append(f"regression.{key} must be a non-negative number, got {reg[key]!r}")
    return errors


def _sp(parent, name, text, tag="stringProp"):
    el = ET.SubElement(parent, tag, name=name)
    el.text = text
    return el


def _element(parent, tag, testname, **attrs):
    el = ET.SubElement(parent, tag, testname=testname, enabled="true", **attrs)
    ET.SubElement(parent, "hashTree")
    return el


def _header_manager(parent, headers):
    hm = _element(parent, "HeaderManager", "HTTP Header Manager",
                  guiclass="HeaderPanel", testclass="HeaderManager")
    coll = ET.SubElement(hm, "collectionProp", name="HeaderManager.headers")
    for name, value in headers:
        h = ET.SubElement(coll, "elementProp", name="", elementType="Header")
        _sp(h, "Header.name", name)
        _sp(h, "Header.value", value)


def _http_sampler(parent, req, think_default=0):
    sampler = _element(parent, "HTTPSamplerProxy", req["name"],
                       guiclass="HttpTestSampleGui", testclass="HTTPSamplerProxy")
    body = req.get("body")
    if body is not None:
        args = ET.SubElement(sampler, "elementProp", name="HTTPsampler.Arguments", elementType="Arguments",
                             guiclass="HTTPArgumentsPanel", testclass="Arguments", enabled="true")
        coll = ET.SubElement(args, "collectionProp", name="Arguments.arguments")
        arg = ET.SubElement(coll, "elementProp", name="", elementType="HTTPArgument")
        _sp(arg, "HTTPArgument.always_encode", "false", tag="boolProp")
        _sp(arg, "Argument.value", body)
        _sp(arg, "Argument.metadata", "=")
        _sp(arg, "HTTPArgument.use_equals", "true", tag="boolProp")
        _sp(sampler, "HTTPSampler.postBodyRaw", "true", tag="boolProp")
    else:
        args = ET.SubElement(sampler, "elementProp", name="HTTPsampler.Arguments", elementType="Arguments")
        ET.SubElement(args, "collectionProp", name="Arguments.arguments")
    _sp(sampler, "HTTPSampler.path", req["path"])
    _sp(sampler, "HTTPSampler.method", req.get("method", "GET"))
    _sp(sampler, "HTTPSampler.follow_redirects", "true", tag="boolProp")
    _sp(sampler, "HTTPSampler.use_keepalive", "true", tag="boolProp")
    # _element appended the sampler's (empty) hashTree right after it; fill it.
    children = parent.find("hashTree[last()]")
    think = req.get("think_time_ms", think_default)
    if think:
        timer = _element(children, "ConstantTimer", "Think time",
                         guiclass="ConstantTimerGui", testclass="ConstantTimer")
        _sp(timer, "ConstantTimer.delay", str(int(think)))
    if "extract" in req:
        var, path = req["extract"].split("=", 1)
        pp = _element(children, "JSONPostProcessor", f"Extract {var}",
                      guiclass="JSONPostProcessorGui", testclass="JSONPostProcessor")
        _sp(pp, "JSONPostProcessor.referenceNames", var)
        _sp(pp, "JSONPostProcessor.jsonPathExprs", path)
        _sp(pp, "JSONPostProcessor.match_numbers", "1")
        _sp(pp, "JSONPostProcessor.defaultValues", "NOT_FOUND")


def _add_throughput_timer(thread_group_tree, profile):
    if any("rps" in step or "tps" in step for step in profile["steps"]):
        # Per-instance cap; the launcher sets RPS_PER_MIN per step. Steps without an
        # rps leave it at the huge default, i.e. effectively unthrottled.
        tt = _element(thread_group_tree, "ConstantThroughputTimer", "Target throughput",
                      guiclass="TestBeanGUI", testclass="ConstantThroughputTimer")
        _sp(tt, "calcMode", "2", tag="intProp")
        _sp(tt, "throughput", "${__P(RPS_PER_MIN,6000000)}")


def _jdbc_sampler(parent, req, think_default=0):
    params = req.get("params", [])
    write = req.get("query_type", "select") == "update"
    kind = ("Prepared " if params else "") + ("Update" if write else "Select") + " Statement"
    sampler = _element(parent, "JDBCSampler", req["name"], guiclass="TestBeanGUI", testclass="JDBCSampler")
    _sp(sampler, "dataSource", "db")
    _sp(sampler, "query", req["sql"].strip())
    _sp(sampler, "queryArguments", ",".join(str(p["value"]) for p in params))
    _sp(sampler, "queryArgumentsTypes", ",".join(p["type"] for p in params))
    _sp(sampler, "queryTimeout", "")
    _sp(sampler, "queryType", kind)
    # Count rows instead of materialising the result set: the load generator's own memory and CPU
    # should not be what limits the test.
    _sp(sampler, "resultSetHandler", "Count Records")
    _sp(sampler, "resultSetMaxRows", "")
    _sp(sampler, "resultVariable", "")
    _sp(sampler, "variableNames", "")
    children = parent.find("hashTree[last()]")
    think = req.get("think_time_ms", think_default)
    if think:
        timer = _element(children, "ConstantTimer", "Think time",
                         guiclass="ConstantTimerGui", testclass="ConstantTimer")
        _sp(timer, "ConstantTimer.delay", str(int(think)))


def _add_jdbc_elements(thread_group_tree, profile):
    target = profile["target"]
    driver = JDBC_DRIVERS[target["driver"]]
    url = driver["url"].format(host=target["host"], port=target.get("port", driver["port"]), database=target["database"])
    ds = _element(thread_group_tree, "JDBCDataSource", "JDBC Connection Configuration",
                  guiclass="TestBeanGUI", testclass="JDBCDataSource")
    _sp(ds, "dataSource", "db")
    _sp(ds, "dbUrl", url)
    _sp(ds, "driver", driver["class"])
    user_env = target.get("user_env", DEFAULT_DB_USER_ENV)
    password_env = target.get("password_env", DEFAULT_DB_PASSWORD_ENV)
    _sp(ds, "username", '${__groovy(System.getenv("%s"))}' % user_env)
    _sp(ds, "password", '${__groovy(System.getenv("%s"))}' % password_env)
    # 0 = each thread keeps its own connection: no pool contention muddying the measurement.
    _sp(ds, "poolMax", "0")
    _sp(ds, "timeout", "10000")
    _sp(ds, "autocommit", "true", tag="boolProp")
    _sp(ds, "keepAlive", "true", tag="boolProp")
    _sp(ds, "checkQuery", "SELECT 1")
    _sp(ds, "connectionAge", "5000")
    _sp(ds, "connectionProperties", "")
    _sp(ds, "initQuery", "")
    _sp(ds, "transactionIsolation", "DEFAULT")
    _sp(ds, "trimInterval", "60000")
    _sp(ds, "preinit", "false", tag="boolProp")
    _add_throughput_timer(thread_group_tree, profile)
    for req in profile["requests"]:
        _jdbc_sampler(thread_group_tree, req, profile.get("think_time_ms", 0))


def build_plan_from_profile(profile):
    """Returns JMeter plan XML (str) for the profile's target/requests blocks.
    The profile's test_plan acts as the template: its thread group settings
    and Prometheus Listener are kept as-is, everything between them is
    replaced by the generated defaults/auth/samplers."""
    tree = ET.parse(TEST_PLANS_DIR / profile["test_plan"])
    plan_children = tree.getroot().find("hashTree").find("hashTree")
    thread_group_tree = plan_children.findall("hashTree")[0]
    listener = next(c for c in thread_group_tree if c.tag.endswith("PrometheusListener"))
    listener_tree = thread_group_tree[list(thread_group_tree).index(listener) + 1]
    for child in list(thread_group_tree):
        thread_group_tree.remove(child)

    target = profile["target"]
    protocol = target.get("protocol", "http")
    if protocol == "jdbc":
        _add_jdbc_elements(thread_group_tree, profile)
        thread_group_tree.append(listener)
        thread_group_tree.append(listener_tree)
        ET.indent(tree, space="  ")
        return ET.tostring(tree.getroot(), encoding="unicode", xml_declaration=False)
    host = target["host"]
    port = target.get("port")
    base_url = f"{protocol}://{host}" + (f":{port}" if port else "")

    defaults = _element(thread_group_tree, "ConfigTestElement", "HTTP Request Defaults",
                        guiclass="HttpDefaultsGui", testclass="ConfigTestElement")
    args = ET.SubElement(defaults, "elementProp", name="HTTPsampler.Arguments", elementType="Arguments")
    ET.SubElement(args, "collectionProp", name="Arguments.arguments")
    _sp(defaults, "HTTPSampler.domain", host)
    _sp(defaults, "HTTPSampler.port", str(port) if port else "")
    _sp(defaults, "HTTPSampler.protocol", protocol)
    _sp(defaults, "HTTPSampler.path", "")

    auth = target.get("auth", "none")
    if auth == "basic":
        am = _element(thread_group_tree, "AuthManager", "HTTP Authorization Manager",
                      guiclass="AuthPanelGui", testclass="AuthManager")
        coll = ET.SubElement(am, "collectionProp", name="AuthManager.auth_list")
        a = ET.SubElement(coll, "elementProp", name="", elementType="Authorization")
        _sp(a, "Authorization.url", base_url)
        _sp(a, "Authorization.username", '${__groovy(System.getenv("BOOMI_TEST_TARGET_BASIC_AUTH_USER"))}')
        _sp(a, "Authorization.password", '${__groovy(System.getenv("BOOMI_TEST_TARGET_BASIC_AUTH_PASS"))}')
        _sp(a, "Authorization.mechanism", "BASIC_DIGEST")
        _sp(am, "AuthManager.controlledByThreadGroup", "false", tag="boolProp")

    headers = []
    if auth == "api_key":
        headers.append((target.get("api_key_header", "x-api-key"),
                        '${__groovy(System.getenv("BOOMI_TEST_TARGET_API_KEY"))}'))
    if any("body" in r for r in profile["requests"]):
        headers.append(("Content-Type", "application/json"))
    if headers:
        _header_manager(thread_group_tree, headers)

    _add_throughput_timer(thread_group_tree, profile)

    for req in profile["requests"]:
        _http_sampler(thread_group_tree, req, profile.get("think_time_ms", 0))

    thread_group_tree.append(listener)
    thread_group_tree.append(listener_tree)
    ET.indent(tree, space="  ")
    return ET.tostring(tree.getroot(), encoding="unicode", xml_declaration=False)


def generate_test_plan(profile, run_id):
    """Writes the generated plan under test-plans/generated/ and returns its
    path relative to test-plans/ (what launch_instance expects)."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    rel = f"generated/{run_id}.jmx"
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + build_plan_from_profile(profile) + "\n"
    (TEST_PLANS_DIR / rel).write_text(xml, encoding="utf-8")
    return rel


def step_request_rate(profile, step):
    """Total requests/sec cap for a step, or None for unthrottled. A TPS target
    (flow iterations/sec) becomes tps x requests-per-iteration: every request in
    the generated flow runs on every iteration, so the conversion is exact."""
    if "tps" in step:
        return step["tps"] * len(profile["requests"])
    return step.get("rps")


def resolve_step_duration(profile, step):
    return step.get("duration_sec", profile["step_duration_sec"])


def derive_instance_name(run_id, step_id, idx):
    return f"jmeter-ramp-{run_id}-{step_id}-{idx}"


def write_target_file(run_id, step_id, idx, container_name):
    TARGETS_DIR.mkdir(parents=True, exist_ok=True)
    target_path = TARGETS_DIR / f"{run_id}-{step_id}-{idx}.json"
    content = [{
        "targets": [f"{container_name}:9270"],
        "labels": {"run_id": run_id, "step_id": step_id},
    }]
    target_path.write_text(json.dumps(content))
    return target_path


def delete_target_file(run_id, step_id, idx):
    target_path = TARGETS_DIR / f"{run_id}-{step_id}-{idx}.json"
    target_path.unlink(missing_ok=True)


def compose_command():
    """The standalone docker-compose binary if present, else the Compose v2 plugin (`docker compose`)."""
    return ["docker-compose"] if shutil.which("docker-compose") else ["docker", "compose"]


def launch_instance(test_plan, run_id, step_id, idx, threads, duration, rps_per_min=None):
    """Launches one JMeter instance in the background. Returns the Popen handle."""
    container_name = derive_instance_name(run_id, step_id, idx)
    # Defensive: a stale container from a prior interrupted run can occupy
    # this name and make --name fail with a conflict error.
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    write_target_file(run_id, step_id, idx, container_name)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = f"/test-plans/results/{run_id}-{step_id}-{idx}.jtl"
    cmd = [
        *compose_command(), "run", "--rm", "--use-aliases",
        "--name", container_name,
        "jmeter-ramp", "-n", "-t", f"/test-plans/{test_plan}",
        "-l", result_file,
        f"-JTHREADS={threads}",
        f"-JDURATION={duration}",
    ]
    if rps_per_min is not None:
        cmd.append(f"-JRPS_PER_MIN={rps_per_min}")
    return subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_step(profile, run_id, step_idx, step):
    step_id = f"step-{step_idx + 1:02d}"
    duration = resolve_step_duration(profile, step)
    instances = step["instances"]
    threads = step["threads"]
    rps = step_request_rate(profile, step)
    # rps is the step's total; each instance caps itself at its share.
    rps_per_min = round(rps * 60 / instances, 3) if rps else None
    if "tps" in step:
        rps_note = f", target {step['tps']} TPS ({rps:g} req/s: {len(profile['requests'])} requests per iteration)"
    else:
        rps_note = f", target {rps} req/s total" if rps else ""
    print(f"[{step_id}] launching {instances} instance(s) x {threads} thread(s), duration={duration}s{rps_note}")

    _current_step_state["run_id"] = run_id
    _current_step_state["step_id"] = step_id
    _current_step_state["instances"] = instances

    post_grafana_annotation(f"{step_id} started: {instances} instance(s) x {threads} thread(s)", ["ramp", run_id, step_id])

    procs = []
    names = []
    for idx in range(1, instances + 1):
        names.append(derive_instance_name(run_id, step_id, idx))
        procs.append(launch_instance(profile["test_plan"], run_id, step_id, idx, threads, duration, rps_per_min))

    time.sleep(min(duration / 2, 10))
    cpu_pct = sample_host_cpu(names)
    cores = host_cpu_cores()
    saturated = cpu_pct > (cores * 100 * 0.85)
    if saturated:
        print(f"[{step_id}] WARNING: load generator CPU usage ({cpu_pct:.0f}% across {len(names)} instance(s), "
              f"host has {cores} cores) looks saturated -- these results may reflect the generator's own "
              f"limits, not the target's.", file=sys.stderr)

    exit_codes = [p.wait() for p in procs]
    post_grafana_annotation(f"{step_id} ended", ["ramp", run_id, step_id])
    _current_step_state["run_id"] = None
    for idx in range(1, instances + 1):
        delete_target_file(run_id, step_id, idx)

    failed = [i + 1 for i, code in enumerate(exit_codes) if code != 0]
    if failed:
        print(f"[{step_id}] WARNING: instance(s) {failed} exited non-zero: {exit_codes}", file=sys.stderr)

    return {"step_id": step_id, "instances_launched": instances, "exit_codes": exit_codes, "saturated": saturated}


def confirm_large_steps(profile, skip_confirmation):
    """Pre-scans every step before anything launches, so an oversized step
    buried later in the profile (e.g. step 4 of 5, as in
    rest-spike-example.yaml) still blocks before step 1 ever runs --
    checking per-step inside the run loop would let earlier steps generate
    real load first and defeat the point of the gate."""
    large_steps = [
        (i + 1, step["instances"])
        for i, step in enumerate(profile["steps"])
        if step["instances"] > CONFIRMATION_THRESHOLD
    ]
    if not large_steps:
        return
    lines = "\n".join(f"  step-{idx:02d}: {instances} instances" for idx, instances in large_steps)
    message = (
        f"\nThis profile launches more than {CONFIRMATION_THRESHOLD} simultaneous instances "
        f"in at least one step, against test plan '{profile['test_plan']}':\n{lines}\n"
        f"This generates real load -- make sure that's intentional.\n"
    )
    if skip_confirmation:
        print(message + "Proceeding without prompting (--yes was passed).")
        return
    print(message, end="")
    if not sys.stdin.isatty():
        print("No terminal attached to confirm interactively. Re-run with --yes to proceed unattended.", file=sys.stderr)
        sys.exit(1)
    answer = input("Type 'yes' to proceed: ")
    if answer.strip().lower() != "yes":
        print("Aborted.", file=sys.stderr)
        sys.exit(1)


def baseline_path(name):
    return BASELINES_DIR / f"{name}.json"


def _step_shapes(profile):
    return [{"instances": s["instances"], "threads": s["threads"]} for s in profile["steps"]]


def build_baseline(profile, run_id, rows):
    return {
        "run_id": run_id,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "steps": _step_shapes(profile),
        "rows": {
            f"{r['step_id']}|{r['label']}": {
                "samples": r["samples"], "p95_ms": r["p95_ms"],
                "error_pct": r["error_pct"], "throughput": r["throughput"],
            }
            for r in rows if r["label"] != "(no data)"
        },
    }


def compare_to_baseline(rows, baseline, regression):
    """One result per current row plus one per baseline row that vanished.
    status: ok | REGRESSION | NO DATA | new | missing. REGRESSION and NO DATA fail the run.
    A p95 increase must clear BOTH the percentage and the absolute-ms bar --
    3ms -> 6ms is +100% but meaningless noise."""
    max_pct = regression.get("max_p95_increase_pct", 25)
    max_err = regression.get("max_error_rate_increase_pct", 1)
    min_delta = regression.get("min_p95_delta_ms", 10)
    results = []
    seen = set()
    for row in rows:
        if row["label"] == "(no data)":
            continue
        key = f"{row['step_id']}|{row['label']}"
        seen.add(key)
        base = baseline["rows"].get(key)
        if base is None:
            results.append({"key": key, "status": "new", "reasons": []})
            continue
        if row["samples"] == 0:
            # No data can't confirm "no regression" -- fail loudly rather than
            # comparing a 0ms p95 against the baseline and passing.
            results.append({"key": key, "status": "NO DATA", "reasons": ["no samples in the measurement window"]})
            continue
        delta_ms = row["p95_ms"] - base["p95_ms"]
        delta_pct = (100.0 * delta_ms / base["p95_ms"]) if base["p95_ms"] > 0 else 0.0
        err_delta = row["error_pct"] - base["error_pct"]
        reasons = []
        if delta_ms > min_delta and delta_pct > max_pct:
            reasons.append(f"p95 {base['p95_ms']:.0f}ms -> {row['p95_ms']:.0f}ms (+{delta_pct:.0f}%, limit +{max_pct}%)")
        if err_delta > max_err:
            reasons.append(f"error rate {base['error_pct']:.1f}% -> {row['error_pct']:.1f}% (limit +{max_err} points)")
        results.append({
            "key": key, "status": "REGRESSION" if reasons else "ok", "reasons": reasons,
            "base_p95": base["p95_ms"], "p95": row["p95_ms"], "delta_pct": delta_pct,
            "base_err": base["error_pct"], "err": row["error_pct"],
            "base_thr": base["throughput"], "thr": row["throughput"],
        })
    for key in baseline["rows"]:
        if key not in seen:
            results.append({"key": key, "status": "missing", "reasons": []})
    return results


def print_regression_report(name, baseline, results):
    print()
    print(f"=== Regression check vs baseline '{name}' (run {baseline.get('run_id')}, saved {baseline.get('saved_at')}) ===")
    header = f"{'Step|Label':<40} {'p95 base':>9} {'p95 now':>8} {'Δp95':>7} {'Err base':>9} {'Err now':>8}  Status"
    print(header)
    print("-" * len(header))
    for r in results:
        if r["status"] == "NO DATA":
            print(f"{r['key']:<40} {'':>9} {'':>8} {'':>7} {'':>9} {'':>8}  NO DATA (cannot verify; counts as failure)")
            continue
        if r["status"] in ("new", "missing"):
            note = "not in baseline" if r["status"] == "new" else "in baseline but not in this run"
            print(f"{r['key']:<40} {'':>9} {'':>8} {'':>7} {'':>9} {'':>8}  {r['status']} ({note})")
            continue
        print(f"{r['key']:<40} {r['base_p95']:>8.0f}ms {r['p95']:>7.0f}ms {r['delta_pct']:>+6.0f}% "
              f"{r['base_err']:>8.1f}% {r['err']:>7.1f}%  {r['status']}")
        for reason in r["reasons"]:
            print(f"{'':<40} -> {reason}")


def host_cpu_cores():
    return os.cpu_count() or 1


def sample_host_cpu(container_names):
    """Sums docker stats CPU% across the given containers. Returns 0.0 for
    any container not currently running (already exited) rather than erroring."""
    total = 0.0
    for name in container_names:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", name],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            continue
        pct_str = result.stdout.strip().rstrip("%")
        try:
            total += float(pct_str)
        except ValueError:
            continue
    return total


def write_summary_json(path, run_id, exit_code, rows, slow_requests, regression, failures=None, no_data_steps=None):
    """Machine-readable run result, for the web console (same data the text report prints)."""
    payload = {
        "run_id": run_id,
        "exit_code": exit_code,
        "rows": rows,
        # capped so a very slow target can't produce a huge file; the count says how many there were
        "slow_requests": sorted(slow_requests, key=lambda r: r["elapsed_ms"], reverse=True)[:200],
        "slow_request_count": len(slow_requests),
        "regression": regression,
        "failures": failures or [],
        "no_data_steps": no_data_steps or [],
    }
    Path(path).write_text(json.dumps(payload, indent=2, default=str) + "\n")


def main():
    args = sys.argv[1:]
    skip_confirmation = "--yes" in args
    validate_only = "--validate" in args
    save_baseline = "--save-baseline" in args
    summary_json_path = None
    if "--summary-json" in args:
        i = args.index("--summary-json")
        if i + 1 >= len(args):
            print("--summary-json needs a file path.", file=sys.stderr)
            sys.exit(1)
        summary_json_path = args[i + 1]
        del args[i:i + 2]
    args = [a for a in args if a not in ("--yes", "--validate", "--save-baseline")]
    if len(args) < 1:
        print("Usage: ramp.py [--yes] [--validate] [--save-baseline] [--summary-json <path>] <profile.yaml>", file=sys.stderr)
        sys.exit(1)

    profile = load_profile(args[0])
    errors = validate_profile(profile)
    if errors:
        print("Profile is invalid:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    if save_baseline and "regression" not in profile:
        print("--save-baseline needs a 'regression: {baseline: <name>}' block in the profile.", file=sys.stderr)
        sys.exit(1)

    if validate_only:
        print(f"Profile is valid: {len(profile['steps'])} step(s), test_plan={profile['test_plan']!r}")
        sys.exit(0)

    run_id = time.strftime("%Y%m%d-%H%M%S")
    print(f"Run ID: {run_id}", flush=True)
    if "requests" in profile:
        generated = generate_test_plan(profile, run_id)
        print(f"Generated test plan from profile target/requests: test-plans/{generated}")
        profile = {**profile, "test_plan": generated}

    for warning in short_step_warnings(profile):
        print(f"WARNING: {warning}", file=sys.stderr)

    confirm_large_steps(profile, skip_confirmation)

    warmup_sec = profile.get("warmup_sec", 0)
    step_results = []
    stop_rule_triggered = False
    for step_idx, step in enumerate(profile["steps"]):
        step_start = int(time.time())
        result = run_step(profile, run_id, step_idx, step)
        step_end = int(time.time())
        result["step_start"] = step_start
        result["step_end"] = step_end
        step_results.append(result)

        should_stop, reason = evaluate_stop_rule(profile, run_id, result["step_id"], step_start, step_end, warmup_sec)
        if should_stop:
            print(f"[{result['step_id']}] STOP RULE TRIGGERED: {reason}", file=sys.stderr)
            stop_rule_triggered = True
            break

    print(f"Done. run_id={run_id}")
    for r in step_results:
        status = "OK" if all(c == 0 for c in r["exit_codes"]) else "HAD FAILURES"
        print(f"  {r['step_id']}: {r['instances_launched']} instance(s), exit codes {r['exit_codes']} [{status}]")

    all_rows = []
    all_slow = []
    slow_threshold = profile.get("slow_request_threshold_ms")
    for r in step_results:
        step_rows = query_report_rows(run_id, r["step_id"], r["step_start"], r["step_end"], warmup_sec)
        for row in step_rows:
            row["saturated"] = r["saturated"]
        all_rows.extend(step_rows)
        if slow_threshold:
            all_slow.extend(scan_slow_requests(run_id, r["step_id"], r["instances_launched"], slow_threshold))
    print_summary_report(all_rows)
    jtl_failures = [scan_jtl_failures(run_id, r["step_id"], r["instances_launched"]) for r in step_results]
    print_jtl_failures(jtl_failures)
    no_data_steps = sorted({row["step_id"] for row in all_rows}
                           - {row["step_id"] for row in all_rows if row["samples"] > 0})
    if no_data_steps:
        print(f"\nWARNING: no measurable samples for {', '.join(no_data_steps)}. The run did not produce usable "
              f"numbers (the step may be too short, the target unreachable, or every request failing -- see the "
              f"raw-results section above and the log).", file=sys.stderr)
    if slow_threshold:
        total_requests = sum(row["samples"] for row in all_rows)
        print_slow_requests_table(all_slow, total_requests)

    any_instance_failed = any(code != 0 for r in step_results for code in r["exit_codes"])
    regressed = False
    regression_info = None
    if "regression" in profile:
        regression = profile["regression"]
        name = regression["baseline"]
        regression_info = {"baseline": name, "status": "none", "results": []}
        if save_baseline:
            if stop_rule_triggered or any_instance_failed:
                print("Not saving baseline: this run had a stop-rule trigger or failed instances, "
                      "so it isn't a trustworthy reference.", file=sys.stderr)
                regression_info["status"] = "not_saved"
            else:
                BASELINES_DIR.mkdir(exist_ok=True)
                baseline_path(name).write_text(json.dumps(build_baseline(profile, run_id, all_rows), indent=2) + "\n")
                print(f"\nBaseline '{name}' saved to {baseline_path(name).relative_to(REPO_ROOT)}")
                regression_info["status"] = "saved"
        elif not baseline_path(name).exists():
            print(f"\nNo baseline '{name}' yet -- skipping regression check. "
                  f"Re-run with --save-baseline to record this run as the baseline.")
            regression_info["status"] = "no_baseline"
        else:
            baseline = json.loads(baseline_path(name).read_text())
            if baseline.get("steps") != _step_shapes(profile):
                print("\nWARNING: baseline was recorded with different steps (instances/threads) -- "
                      "the comparison below may not be like-for-like.", file=sys.stderr)
            results = compare_to_baseline(all_rows, baseline, regression)
            print_regression_report(name, baseline, results)
            regressed = any(r["status"] in ("REGRESSION", "NO DATA") for r in results)
            print("\nRegression check: " + ("FAILED" if regressed else "passed"))
            regression_info["status"] = "failed" if regressed else "passed"
            regression_info["results"] = results

    exit_code = 0
    if stop_rule_triggered:
        exit_code = 2
    elif any_instance_failed:
        exit_code = 3
    elif regressed:
        exit_code = 4
    elif no_data_steps:
        exit_code = 5

    if summary_json_path:
        write_summary_json(summary_json_path, run_id, exit_code, all_rows, all_slow, regression_info,
                           jtl_failures, no_data_steps)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
