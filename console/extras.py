"""Things the console shows around a run: live numbers from Prometheus, a pre-run checklist,
and the optional finished-run notification."""
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

PROMETHEUS_URL = os.environ.get("CONSOLE_PROMETHEUS_URL", "http://localhost:9090")
GRAFANA_HEALTH_URL = os.environ.get("CONSOLE_GRAFANA_HEALTH_URL", "http://localhost:3004/api/health")
RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}$")

# Variables docker-compose.yml sets itself for the bundled samples, so they are always present in the container.
COMPOSE_DEFAULTS = {"BOOMI_TEST_DB_USER", "BOOMI_TEST_DB_PASSWORD",
                    "BOOMI_TEST_OAUTH_STUB_CLIENT_ID", "BOOMI_TEST_OAUTH_STUB_CLIENT_SECRET"}


def _prom(promql):
    url = f"{PROMETHEUS_URL}/api/v1/query?" + urllib.parse.urlencode({"query": promql})
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            result = json.loads(resp.read())["data"]["result"]
    except Exception:
        return None
    if not result:
        return None
    value = float(result[0]["value"][1])
    return None if value != value else value  # NaN means "no samples in the window"


def live_metrics(ramp_run_id):
    """Current numbers for a running test, or None when the run id is not usable. Values are None until
    Prometheus has scraped them (a few seconds after a phase starts), so the UI shows a dash, not a zero."""
    if not ramp_run_id or not RUN_ID_RE.match(ramp_run_id):
        return None
    sel = f'run_id="{ramp_run_id}"'
    requests = f"sum(rate(jmeter_requests_total{{{sel}}}[15s]))"
    failures = f"(sum(rate(jmeter_failures_total{{{sel}}}[15s])) or vector(0))"
    return {
        "throughput": _prom(requests),
        "error_pct": _prom(f"100 * {failures} / {requests}"),
        "p95_ms": _prom(f"histogram_quantile(0.95, sum by (le) (rate(jmeter_response_time_ms_bucket{{{sel}}}[30s])))"),
        "avg_ms": _prom(f"sum(rate(jmeter_response_time_ms_sum{{{sel}}}[15s])) / sum(rate(jmeter_response_time_ms_count{{{sel}}}[15s]))"),
        "active_threads": _prom('sum(jmeter_threads{state="active"})'),
    }


def _http_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=3):
            return True
    except Exception:
        return False


def env_names_set(env_path):
    """Names of variables that have a non-empty value in .env or this process's environment. Never the values."""
    names = {k for k, v in os.environ.items() if v}
    try:
        for line in Path(env_path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                if value.strip().strip('"\''):
                    names.add(key.strip())
    except OSError:
        pass
    return names


def required_env(target):
    """The .env variable names a profile's auth needs: [(name, what it is for)]."""
    if not isinstance(target, dict):
        return []
    auth = target.get("auth", "none")
    oauth = target.get("oauth") if isinstance(target.get("oauth"), dict) else {}
    if target.get("protocol") == "jdbc":
        return [(target.get("user_env", "BOOMI_TEST_DB_USER"), "database user"),
                (target.get("password_env", "BOOMI_TEST_DB_PASSWORD"), "database password")]
    return {
        "basic": [("BOOMI_TEST_TARGET_BASIC_AUTH_USER", "basic auth user"), ("BOOMI_TEST_TARGET_BASIC_AUTH_PASS", "basic auth password")],
        "api_key": [("BOOMI_TEST_TARGET_API_KEY", "API key")],
        "bearer": [(target.get("token_env", "BOOMI_TEST_TARGET_BEARER_TOKEN"), "bearer token")],
        "oauth2_client_credentials": [(oauth.get("client_id_env", "BOOMI_TEST_OAUTH_CLIENT_ID"), "OAuth client id"),
                                      (oauth.get("client_secret_env", "BOOMI_TEST_OAUTH_CLIENT_SECRET"), "OAuth client secret")],
    }.get(auth, [])


def precheck(profile_path, env_path):
    """A checklist for the selected profile: [{id, status: ok|warn, label, detail}]."""
    items = []
    items.append({"id": "prometheus", "label": "Metrics store (Prometheus)",
                  "status": "ok" if _http_ok(f"{PROMETHEUS_URL}/-/healthy") else "warn",
                  "detail": "Reachable." if _http_ok(f"{PROMETHEUS_URL}/-/healthy")
                  else "Not reachable. Start the stack with: docker-compose up -d prometheus grafana"})
    grafana_ok = _http_ok(GRAFANA_HEALTH_URL)
    items.append({"id": "grafana", "label": "Dashboard (Grafana)", "status": "ok" if grafana_ok else "warn",
                  "detail": "Reachable." if grafana_ok else "Not reachable, so there will be no charts. The run itself still works."})
    try:
        profile = yaml.safe_load(Path(profile_path).read_text())
    except (OSError, yaml.YAMLError):
        return items
    target = profile.get("target") if isinstance(profile, dict) else None
    needed = required_env(target)
    if needed:
        have = env_names_set(env_path) | COMPOSE_DEFAULTS
        missing = [(n, what) for n, what in needed if n not in have]
        items.append({"id": "credentials", "label": "Credentials in .env",
                      "status": "warn" if missing else "ok",
                      "detail": ("Not set: " + ", ".join(f"{n} ({what})" for n, what in missing) + ". Add them to .env, then run again.")
                      if missing else "All set: " + ", ".join(n for n, _ in needed) + "."})
    if isinstance(target, dict) and target.get("host"):
        items.append({"id": "target", "label": "Target", "status": "ok",
                      "detail": f"{target.get('protocol', 'http')}://{target['host']}" + (f":{target['port']}" if target.get("port") else "")
                      + ". Use Test one request to check it is reachable and the credentials work."})
    return items


def notify(webhook_url, text):
    """POSTs {"text": ...} to an incoming webhook (Slack and Teams both accept this shape). Best effort."""
    try:
        req = urllib.request.Request(webhook_url, data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=8).read()
        return True
    except Exception:
        return False
