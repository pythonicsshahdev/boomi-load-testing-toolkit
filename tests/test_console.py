import http.client
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from console import profiles, server
from console.runs import RunError, RunManager

REPO_ROOT = Path(__file__).resolve().parent.parent

FAKE_RAMP = """
import json, sys, time
summary, mode = sys.argv[1], sys.argv[2]
print("Run ID: 20260101-000000", flush=True)
print("[step-01] launching 1 instance(s) x 2 thread(s), duration=1s", flush=True)
if mode == "hang":
    time.sleep(30)
code = int(mode) if mode.isdigit() else 0
json.dump({"run_id": "20260101-000000", "exit_code": code, "rows": [], "regression": None}, open(summary, "w"))
sys.exit(code)
"""

VALID_YAML = """test_plan: rest/ramp.jmx
step_duration_sec: 10
target:
  protocol: http
  host: host.docker.internal
  port: 8078
  auth: none
requests:
- name: List customers
  method: GET
  path: /customers
steps:
- instances: 1
  threads: 2
  tps: 5
- instances: 1
  threads: 4
  tps: 10
"""


# --- profiles ---------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["../etc/passwd", "a/b", "", ".hidden", "name with space", "x" * 200, "..", "a\\b"])
def test_safe_profile_path_rejects_unsafe_names(tmp_path, name):
    assert profiles.safe_profile_path(tmp_path, name) is None


def test_safe_profile_path_accepts_plain_names_with_or_without_extension(tmp_path):
    assert profiles.safe_profile_path(tmp_path, "my-profile") == tmp_path / "my-profile.yaml"
    assert profiles.safe_profile_path(tmp_path, "my-profile.yaml") == tmp_path / "my-profile.yaml"
    assert profiles.safe_profile_path(tmp_path, "rest.example") == tmp_path / "rest.example.yaml"


def test_summarize_totals_and_peaks():
    s = profiles.summarize({"step_duration_sec": 200, "warmup_sec": 15, "steps": [
        {"instances": 1, "threads": 8, "tps": 10},
        {"instances": 1, "threads": 50, "tps": 80, "duration_sec": 400},
    ]})
    assert s["total_sec"] == 600
    assert s["peak_tps"] == 80 and s["peak_threads"] == 50 and s["peak_instances"] == 1
    assert s["steps"][0]["step_id"] == "step-01" and s["steps"][1]["duration_sec"] == 400
    assert not s["needs_confirmation"]


def test_summarize_flags_large_runs():
    s = profiles.summarize({"step_duration_sec": 10, "steps": [{"instances": 5, "threads": 1}]})
    assert s["needs_confirmation"]


def test_summarize_reports_errors_instead_of_raising():
    assert "error" in profiles.summarize(None)
    assert "error" in profiles.summarize({"steps": ["nope"]})
    assert "error" in profiles.summarize({"steps": [{"instances": 1, "threads": 1}]})  # no duration anywhere


def test_summarize_checks_baseline_file(tmp_path):
    (tmp_path / "mine.json").write_text("{}")
    p = {"step_duration_sec": 5, "regression": {"baseline": "mine"}, "steps": [{"instances": 1, "threads": 1}]}
    assert profiles.summarize(p, tmp_path)["baseline_exists"] is True
    p["regression"]["baseline"] = "other"
    assert profiles.summarize(p, tmp_path)["baseline_exists"] is False


def test_real_phased_profile_summary():
    s = profiles.load_summary(REPO_ROOT / "profiles" / "rest-boomi-phased-tps-20min.yaml")
    assert s["total_sec"] == 1200 and len(s["steps"]) == 6 and s["peak_tps"] == 80


# --- access rules -------------------------------------------------------------------------------

def test_is_loopback():
    assert all(server.is_loopback(h) for h in ("127.0.0.1", "localhost", "::1", "127.5.5.5"))
    assert not any(server.is_loopback(h) for h in ("0.0.0.0", "10.0.0.5", "example.com", ""))


def test_startup_requires_token_off_loopback():
    assert server.startup_problem("127.0.0.1", None) is None
    assert server.startup_problem("0.0.0.0", None) is not None
    assert server.startup_problem("0.0.0.0", "short") is not None
    assert server.startup_problem("0.0.0.0", "x" * 16) is None
    assert server.startup_problem("127.0.0.1", "short") is not None


def test_host_allowed_blocks_rebinding_on_loopback():
    assert server.host_allowed("localhost:8800", "127.0.0.1")
    assert server.host_allowed("127.0.0.1:8800", "127.0.0.1")
    assert server.host_allowed("[::1]:8800", "127.0.0.1")
    assert not server.host_allowed("evil.example.com", "127.0.0.1")
    assert not server.host_allowed("evil.example.com:8800", "127.0.0.1")
    assert server.host_allowed("loadtest.corp.example:8800", "127.0.0.1", extra_hosts=["loadtest.corp.example"])
    assert server.host_allowed("anything.example", "0.0.0.0")


def test_origin_must_match_host_when_present():
    assert server.origin_ok(None, "localhost:8800")
    assert server.origin_ok("http://localhost:8800", "localhost:8800")
    assert not server.origin_ok("http://evil.example.com", "localhost:8800")


def test_token_ok():
    assert server.token_ok(None, None)
    assert server.token_ok("secret-secret-secret", "secret-secret-secret")
    assert not server.token_ok("wrong", "secret-secret-secret")
    assert not server.token_ok(None, "secret-secret-secret")


# --- run manager ---------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "small.yaml").write_text(VALID_YAML)
    (profiles_dir / "big.yaml").write_text(VALID_YAML.replace("instances: 1\n  threads: 4", "instances: 6\n  threads: 4"))
    (profiles_dir / "broken.yaml").write_text("test_plan: rest/ramp.jmx\nsteps: []\n")
    mode = {"value": "0"}

    def command(snapshot, summary, save_baseline):
        return [sys.executable, "-u", "-c", FAKE_RAMP, str(summary), mode["value"]]

    mgr = RunManager(tmp_path / "runs", REPO_ROOT, profiles_dir, tmp_path / "baselines", command=command)
    mgr.mode = mode
    mgr.tmp = tmp_path
    return mgr


def wait_done(mgr, run_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        state = mgr.get(run_id)
        if state["status"] != "running":
            return state
        time.sleep(0.1)
    raise AssertionError("run did not finish")


@pytest.mark.parametrize("code,status", [("0", "passed"), ("2", "stop_rule"), ("3", "instance_failed"), ("4", "regression"), ("5", "no_data")])
def test_run_outcome_maps_exit_code_to_status(env, code, status):
    env.mode["value"] = code
    state = wait_done(env, env.start("small")["id"])
    assert state["status"] == status and state["exit_code"] == int(code)
    assert state["ramp_run_id"] == "20260101-000000" and state["current_step"] == "step-01"
    assert state["summary"]["exit_code"] == int(code)


def test_run_log_is_readable_in_chunks(env):
    run = wait_done(env, env.start("small")["id"])
    first = env.log(run["id"], 0)
    assert "Run ID: 20260101-000000" in first["text"]
    assert env.log(run["id"], first["offset"])["text"] == ""


def test_only_one_run_at_a_time(env):
    env.mode["value"] = "hang"
    run = env.start("small")
    with pytest.raises(RunError) as e:
        env.start("small")
    assert e.value.status == 409
    stopped = env.stop(run["id"])
    assert wait_done(env, stopped["id"])["status"] == "stopped"
    env.mode["value"] = "0"
    assert wait_done(env, env.start("small")["id"])["status"] == "passed"


def test_large_run_needs_confirmation(env):
    with pytest.raises(RunError) as e:
        env.start("big")
    assert e.value.status == 428
    assert wait_done(env, env.start("big", confirmed=True)["id"])["status"] == "passed"


def test_invalid_or_missing_profiles_are_rejected(env):
    for name, status in (("broken", 400), ("nope", 404), ("../x", 404)):
        with pytest.raises(RunError) as e:
            env.start(name)
        assert e.value.status == status


def test_history_survives_a_new_manager_and_marks_dead_runs_lost(env):
    run = wait_done(env, env.start("small")["id"])
    again = RunManager(env.runs_dir, REPO_ROOT, env.profiles_dir, env.baselines_dir)
    assert again.get(run["id"])["status"] == "passed"
    # a run recorded as running whose process is gone and left no summary
    state_path = env.runs_dir / run["id"] / "state.json"
    state = json.loads(state_path.read_text())
    state.update(status="running", exit_code=None, pid=2 ** 22 + 7)
    state_path.write_text(json.dumps(state))
    (env.runs_dir / run["id"] / "summary.json").unlink()
    assert again.get(run["id"])["status"] == "lost"


def test_validate_uses_ramp_validator(env):
    ok, message = env.validate(VALID_YAML)
    assert ok and "valid" in message.lower()
    ok, message = env.validate("test_plan: rest/ramp.jmx\nsteps: []\n")
    assert not ok and message


# --- http -----------------------------------------------------------------------------------------

class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": f"localhost:{self.port}", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body)
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            return res.status, json.loads(raw), res
        except ValueError:
            return res.status, raw, res


def serve(env, token=None, bind="127.0.0.1"):
    app = server.App(env, token=token, bind_host=bind)
    httpd = server.build_server("127.0.0.1", 0, app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, Client(httpd.server_address[1])


def test_http_basics_and_profile_listing(env):
    httpd, c = serve(env)
    try:
        status, cfg, _ = c.request("GET", "/api/config")
        assert status == 200 and cfg["auth_required"] is False and cfg["dashboard_uid"] == "boomi-perf-jmeter"
        status, listing, _ = c.request("GET", "/api/profiles")
        assert status == 200 and {p["name"] for p in listing} == {"small", "big", "broken"}
        status, page, res = c.request("GET", "/")
        assert status == 200 and b"Load Test Console" in page and res.getheader("Content-Type").startswith("text/html")
        assert c.request("GET", "/nope")[0] == 404
        assert c.request("GET", "/../server.py")[0] == 404
        assert c.request("GET", "/api/profiles/..%2Fsecret")[0] == 400
    finally:
        httpd.shutdown()


def test_http_blocks_foreign_host_origin_and_non_json_posts(env):
    httpd, c = serve(env)
    try:
        assert c.request("GET", "/api/profiles", headers={"Host": "evil.example.com"})[0] == 403
        assert c.request("POST", "/api/validate", {"yaml": VALID_YAML}, {"Origin": "http://evil.example.com"})[0] == 403
        assert c.request("POST", "/api/validate", {"yaml": VALID_YAML}, {"Origin": f"http://localhost:{c.port}"})[0] == 200
        status, _, _ = c.request("POST", "/api/runs", None, {"Content-Type": "text/plain"})
        assert status == 415
    finally:
        httpd.shutdown()


def test_http_token_flow(env):
    token = "t" * 20
    httpd, c = serve(env, token=token, bind="0.0.0.0")
    try:
        assert c.request("GET", "/api/config")[1]["auth_required"] is True
        assert c.request("GET", "/api/profiles")[0] == 401
        assert c.request("GET", "/api/profiles", headers={"Authorization": "Bearer wrong"})[0] == 401
        assert c.request("GET", "/api/profiles", headers={"Authorization": f"Bearer {token}"})[0] == 200
        assert c.request("POST", "/api/login", {"token": "wrong"})[0] == 401
        status, _, res = c.request("POST", "/api/login", {"token": token})
        cookie = res.getheader("Set-Cookie")
        assert status == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie
        assert c.request("GET", "/api/profiles", headers={"Cookie": cookie.split(";")[0]})[0] == 200
    finally:
        httpd.shutdown()


def test_http_run_lifecycle_and_profile_save(env):
    httpd, c = serve(env)
    try:
        env.mode["value"] = "hang"
        status, held, _ = c.request("POST", "/api/runs", {"profile": "small"})
        assert status == 201
        assert c.request("POST", "/api/runs", {"profile": "small"})[0] == 409
        assert c.request("POST", f"/api/runs/{held['id']}/stop", {})[0] == 200
        assert wait_done(env, held["id"])["status"] == "stopped"
        env.mode["value"] = "0"
        status, run, _ = c.request("POST", "/api/runs", {"profile": "small"})
        assert status == 201 and run["id"] != held["id"]
        done = wait_done(env, run["id"])
        status, got, _ = c.request("GET", f"/api/runs/{run['id']}")
        assert status == 200 and got["status"] == "passed" and got["summary"]["run_id"] == "20260101-000000"
        status, log, _ = c.request("GET", f"/api/runs/{run['id']}/log?offset=0")
        assert status == 200 and "Run ID" in log["text"]
        assert c.request("GET", "/api/runs/run-1-nope")[0] == 404
        assert c.request("POST", "/api/runs", {"profile": "big"})[0] == 428

        status, saved, _ = c.request("PUT", "/api/profiles/fresh", {"yaml": VALID_YAML})
        assert status == 200 and saved["name"] == "fresh" and (env.profiles_dir / "fresh.yaml").exists()
        assert c.request("PUT", "/api/profiles/fresh", {"yaml": VALID_YAML})[0] == 409
        assert c.request("PUT", "/api/profiles/fresh", {"yaml": VALID_YAML, "overwrite": True})[0] == 200
        assert c.request("PUT", "/api/profiles/bad", {"yaml": "steps: []"})[0] == 400
        assert not (env.profiles_dir / "bad.yaml").exists()
    finally:
        httpd.shutdown()
