#!/usr/bin/env python3
"""Distributed ramp/spike load orchestrator for the Boomi performance testing stack."""
import csv
import json
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent
TARGETS_DIR = REPO_ROOT / "prometheus" / "targets"
RESULTS_DIR = REPO_ROOT / "test-plans" / "results"
PROMETHEUS_URL = "http://localhost:9090"
CONFIRMATION_THRESHOLD = 4

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
    that have since disappeared, which plain 'now' queries are vulnerable to."""
    params = urllib.parse.urlencode({"query": promql, "time": at_unix_time})
    url = f"{PROMETHEUS_URL}/api/v1/query?{params}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


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
    window_sec = max(step_end - window_start, 1)

    error_rate_pct = 0.0
    total_resp = prom_instant_query(
        f'increase(jmeter_requests_total{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s])',
        step_end,
    )
    total_requests = sum(float(r["value"][1]) for r in total_resp.get("data", {}).get("result", []))
    if total_requests > 0:
        fail_resp = prom_instant_query(
            f'increase(jmeter_failures_total{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s])',
            step_end,
        )
        total_failures = sum(float(r["value"][1]) for r in fail_resp.get("data", {}).get("result", []))
        error_rate_pct = 100.0 * total_failures / total_requests

    avg_resp = prom_instant_query(
        f'sum(increase(jmeter_response_time_ms_sum{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s])) '
        f'/ sum(increase(jmeter_response_time_ms_count{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s]))',
        step_end,
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
    window_sec = max(step_end - window_start, 1)

    labels_resp = prom_instant_query(
        f'group by (label) (jmeter_requests_total{{run_id="{run_id}", step_id="{step_id}"}})',
        step_end,
    )
    labels = sorted(r["metric"]["label"] for r in labels_resp.get("data", {}).get("result", []))

    rows = []
    for label in labels:
        sel = f'run_id="{run_id}", step_id="{step_id}", label="{label}"'

        samples = _first_value(prom_instant_query(f'increase(jmeter_requests_total{{{sel}}}[{window_sec}s])', step_end))
        failures = _first_value(prom_instant_query(f'increase(jmeter_failures_total{{{sel}}}[{window_sec}s])', step_end))
        error_pct = (100.0 * failures / samples) if samples > 0 else 0.0
        throughput = samples / window_sec if window_sec > 0 else 0.0

        avg_ms = _first_value(prom_instant_query(
            f'increase(jmeter_response_time_ms_sum{{{sel}}}[{window_sec}s]) / increase(jmeter_response_time_ms_count{{{sel}}}[{window_sec}s])',
            step_end,
        ))
        median_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.50, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', step_end))
        p90_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.90, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', step_end))
        p95_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.95, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', step_end))
        p99_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.99, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', step_end))

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
    if not profile.get("test_plan"):
        errors.append("missing required key: test_plan")
    if not profile.get("steps"):
        errors.append("steps list is missing or empty")
    else:
        for i, step in enumerate(profile["steps"]):
            label = f"steps[{i}]"
            if "instances" not in step:
                errors.append(f"{label}: missing 'instances'")
            elif not isinstance(step["instances"], int) or step["instances"] < 1:
                errors.append(f"{label}: 'instances' must be a positive integer, got {step.get('instances')!r}")
            if "threads" not in step:
                errors.append(f"{label}: missing 'threads'")
            elif not isinstance(step["threads"], int) or step["threads"] < 1:
                errors.append(f"{label}: 'threads' must be a positive integer, got {step.get('threads')!r}")
            if "duration_sec" in step and (not isinstance(step["duration_sec"], int) or step["duration_sec"] < 1):
                errors.append(f"{label}: 'duration_sec' must be a positive integer, got {step['duration_sec']!r}")
    if "step_duration_sec" in profile and (not isinstance(profile["step_duration_sec"], int) or profile["step_duration_sec"] < 1):
        errors.append(f"step_duration_sec must be a positive integer, got {profile['step_duration_sec']!r}")
    stop_rule = profile.get("stop_rule", {})
    if "max_error_rate_pct" in stop_rule:
        v = stop_rule["max_error_rate_pct"]
        if not isinstance(v, (int, float)) or not (0 <= v <= 100):
            errors.append(f"stop_rule.max_error_rate_pct must be between 0 and 100, got {v!r}")
    if "max_avg_response_ms" in stop_rule:
        v = stop_rule["max_avg_response_ms"]
        if not isinstance(v, (int, float)) or v <= 0:
            errors.append(f"stop_rule.max_avg_response_ms must be a positive number, got {v!r}")
    return errors


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


def launch_instance(test_plan, run_id, step_id, idx, threads, duration):
    """Launches one JMeter instance in the background. Returns the Popen handle."""
    container_name = derive_instance_name(run_id, step_id, idx)
    # Defensive: a stale container from a prior interrupted run can occupy
    # this name and make --name fail with a conflict error.
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    write_target_file(run_id, step_id, idx, container_name)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = f"/test-plans/results/{run_id}-{step_id}-{idx}.jtl"
    cmd = [
        "docker-compose", "run", "--rm", "--use-aliases",
        "--name", container_name,
        "jmeter", "-n", "-t", f"/test-plans/{test_plan}",
        "-l", result_file,
        f"-JTHREADS={threads}",
        f"-JDURATION={duration}",
    ]
    return subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_step(profile, run_id, step_idx, step):
    step_id = f"step-{step_idx + 1:02d}"
    duration = resolve_step_duration(profile, step)
    instances = step["instances"]
    threads = step["threads"]
    print(f"[{step_id}] launching {instances} instance(s) x {threads} thread(s), duration={duration}s")

    _current_step_state["run_id"] = run_id
    _current_step_state["step_id"] = step_id
    _current_step_state["instances"] = instances

    procs = []
    names = []
    for idx in range(1, instances + 1):
        names.append(derive_instance_name(run_id, step_id, idx))
        procs.append(launch_instance(profile["test_plan"], run_id, step_id, idx, threads, duration))

    time.sleep(min(duration / 2, 10))
    cpu_pct = sample_host_cpu(names)
    cores = host_cpu_cores()
    saturated = cpu_pct > (cores * 100 * 0.85)
    if saturated:
        print(f"[{step_id}] WARNING: load generator CPU usage ({cpu_pct:.0f}% across {len(names)} instance(s), "
              f"host has {cores} cores) looks saturated -- these results may reflect the generator's own "
              f"limits, not the target's.", file=sys.stderr)

    exit_codes = [p.wait() for p in procs]
    _current_step_state["run_id"] = None
    for idx in range(1, instances + 1):
        delete_target_file(run_id, step_id, idx)

    failed = [i + 1 for i, code in enumerate(exit_codes) if code != 0]
    if failed:
        print(f"[{step_id}] WARNING: instance(s) {failed} exited non-zero: {exit_codes}", file=sys.stderr)

    return {"step_id": step_id, "instances_launched": instances, "exit_codes": exit_codes, "saturated": saturated}


def confirm_large_step(step, step_id, test_plan, skip_confirmation):
    if step["instances"] <= CONFIRMATION_THRESHOLD:
        return
    message = (
        f"\n{step_id} launches {step['instances']} simultaneous instances "
        f"(> {CONFIRMATION_THRESHOLD}) against test plan '{test_plan}'. "
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


def host_cpu_cores():
    result = subprocess.run(["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True)
    return int(result.stdout.strip())


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


def main():
    args = sys.argv[1:]
    skip_confirmation = "--yes" in args
    validate_only = "--validate" in args
    args = [a for a in args if a not in ("--yes", "--validate")]
    if len(args) < 1:
        print("Usage: ramp.py [--yes] [--validate] <profile.yaml>", file=sys.stderr)
        sys.exit(1)

    profile = load_profile(args[0])
    errors = validate_profile(profile)
    if errors:
        print("Profile is invalid:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    if validate_only:
        print(f"Profile is valid: {len(profile['steps'])} step(s), test_plan={profile['test_plan']!r}")
        sys.exit(0)

    run_id = time.strftime("%Y%m%d-%H%M%S")
    warmup_sec = profile.get("warmup_sec", 0)
    step_results = []
    for step_idx, step in enumerate(profile["steps"]):
        step_id_preview = f"step-{step_idx + 1:02d}"
        confirm_large_step(step, step_id_preview, profile["test_plan"], skip_confirmation)
        step_start = int(time.time())
        result = run_step(profile, run_id, step_idx, step)
        step_end = int(time.time())
        result["step_start"] = step_start
        result["step_end"] = step_end
        step_results.append(result)

        should_stop, reason = evaluate_stop_rule(profile, run_id, result["step_id"], step_start, step_end, warmup_sec)
        if should_stop:
            print(f"[{result['step_id']}] STOP RULE TRIGGERED: {reason}", file=sys.stderr)
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
    if slow_threshold:
        total_requests = sum(row["samples"] for row in all_rows)
        print_slow_requests_table(all_slow, total_requests)


if __name__ == "__main__":
    main()
