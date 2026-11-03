"""Profile files: safe names, listing, and a summary of what a profile will do."""
import re
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")

# Mirrors ramp.py's CONFIRMATION_THRESHOLD: a run with more simultaneous
# JMeter instances than this needs an explicit confirmation before it starts.
LARGE_RUN_INSTANCES = 4


def safe_profile_path(profiles_dir, name):
    """Path for a profile name, or None if the name could escape the profiles folder."""
    stem = name[:-5] if name.endswith(".yaml") else name
    if not NAME_RE.match(stem) or stem.endswith("."):
        return None
    return Path(profiles_dir) / f"{stem}.yaml"


def summarize(profile, baselines_dir=None):
    """What a parsed profile will do: steps, total time, peaks, target. Never raises on odd input."""
    if not isinstance(profile, dict) or not isinstance(profile.get("steps"), list):
        return {"error": "profile has no steps list"}
    default_duration = profile.get("step_duration_sec")
    steps = []
    total = 0
    for i, step in enumerate(profile["steps"]):
        if not isinstance(step, dict):
            return {"error": f"step {i + 1} is not a mapping"}
        duration = step.get("duration_sec", default_duration)
        if not isinstance(duration, (int, float)):
            return {"error": f"step {i + 1} has no duration (set duration_sec or step_duration_sec)"}
        total += duration
        steps.append({
            "step_id": f"step-{i + 1:02d}",
            "instances": step.get("instances"),
            "threads": step.get("threads"),
            "tps": step.get("tps"),
            "rps": step.get("rps"),
            "duration_sec": duration,
        })
    target = profile.get("target") if isinstance(profile.get("target"), dict) else {}
    regression = profile.get("regression") if isinstance(profile.get("regression"), dict) else {}
    baseline = regression.get("baseline")
    baseline_exists = bool(baseline and baselines_dir and (Path(baselines_dir) / f"{baseline}.json").exists())
    instances = [s["instances"] for s in steps if isinstance(s["instances"], int)]
    return {
        "steps": steps,
        "total_sec": total,
        "peak_instances": max(instances) if instances else 0,
        "peak_threads": max((s["threads"] for s in steps if isinstance(s["threads"], int)), default=0),
        "peak_tps": max((s["tps"] for s in steps if isinstance(s["tps"], (int, float))), default=None),
        "peak_rps": max((s["rps"] for s in steps if isinstance(s["rps"], (int, float))), default=None),
        "warmup_sec": profile.get("warmup_sec", 0),
        "test_plan": profile.get("test_plan"),
        "target": {k: target.get(k) for k in ("protocol", "host", "port", "auth")} if target else None,
        "requests": [r.get("name") or f"{r.get('method', 'GET')} {r.get('path', '')}"
                     for r in profile.get("requests", []) if isinstance(r, dict)],
        "baseline": baseline,
        "baseline_exists": baseline_exists,
        "needs_confirmation": bool(instances) and max(instances) > LARGE_RUN_INSTANCES,
    }


def load_summary(path, baselines_dir=None):
    try:
        profile = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as e:
        return {"error": f"could not read profile: {e}"}
    return summarize(profile, baselines_dir)


def list_profiles(profiles_dir, baselines_dir=None):
    out = []
    for path in sorted(Path(profiles_dir).glob("*.yaml")):
        out.append({"name": path.stem, **load_summary(path, baselines_dir)})
    return out
