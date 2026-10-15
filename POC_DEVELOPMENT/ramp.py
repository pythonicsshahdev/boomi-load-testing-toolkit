#!/usr/bin/env python3
"""Distributed ramp/spike load orchestrator for the Boomi performance testing stack."""
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent
TARGETS_DIR = REPO_ROOT / "prometheus" / "targets"
RESULTS_DIR = REPO_ROOT / "test-plans" / "results"


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

    procs = []
    for idx in range(1, instances + 1):
        procs.append(launch_instance(profile["test_plan"], run_id, step_id, idx, threads, duration))

    exit_codes = [p.wait() for p in procs]
    for idx in range(1, instances + 1):
        delete_target_file(run_id, step_id, idx)

    failed = [i + 1 for i, code in enumerate(exit_codes) if code != 0]
    if failed:
        print(f"[{step_id}] WARNING: instance(s) {failed} exited non-zero: {exit_codes}", file=sys.stderr)

    return {"step_id": step_id, "instances_launched": instances, "exit_codes": exit_codes}


def main():
    if len(sys.argv) < 2:
        print("Usage: ramp.py <profile.yaml>", file=sys.stderr)
        sys.exit(1)

    profile = load_profile(sys.argv[1])
    errors = validate_profile(profile)
    if errors:
        print("Profile is invalid:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    run_id = time.strftime("%Y%m%d-%H%M%S")
    step_results = []
    for step_idx, step in enumerate(profile["steps"]):
        result = run_step(profile, run_id, step_idx, step)
        step_results.append(result)

    print(f"Done. run_id={run_id}")
    for r in step_results:
        status = "OK" if all(c == 0 for c in r["exit_codes"]) else "HAD FAILURES"
        print(f"  {r['step_id']}: {r['instances_launched']} instance(s), exit codes {r['exit_codes']} [{status}]")


if __name__ == "__main__":
    main()
