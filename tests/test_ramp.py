import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ramp


def test_first_value_returns_default_on_empty_result():
    empty_response = {"status": "success", "data": {"resultType": "vector", "result": []}}
    assert ramp._first_value(empty_response, default=0.0) == 0.0


def test_first_value_extracts_real_value():
    response = {"status": "success", "data": {"resultType": "vector", "result": [{"value": [12345, "42.5"]}]}}
    assert ramp._first_value(response) == 42.5


def _valid_profile():
    return {
        "test_plan": "rest/ramp.jmx",
        "step_duration_sec": 20,
        "steps": [{"instances": 1, "threads": 3}],
    }


def test_validate_profile_accepts_a_valid_profile():
    assert ramp.validate_profile(_valid_profile()) == []


def test_validate_profile_rejects_non_dict_profile():
    errors = ramp.validate_profile(None)
    assert len(errors) == 1
    assert "mapping" in errors[0]


def test_validate_profile_rejects_non_dict_step():
    profile = _valid_profile()
    profile["steps"] = ["not-a-mapping"]
    errors = ramp.validate_profile(profile)
    assert any("steps[0]" in e and "mapping" in e for e in errors)


def test_validate_profile_rejects_step_with_no_resolvable_duration():
    profile = {
        "test_plan": "rest/ramp.jmx",
        "steps": [{"instances": 1, "threads": 3}],
    }
    errors = ramp.validate_profile(profile)
    assert any("no duration" in e for e in errors)


def test_validate_profile_accepts_per_step_duration_without_top_level_default():
    profile = {
        "test_plan": "rest/ramp.jmx",
        "steps": [{"instances": 1, "threads": 3, "duration_sec": 15}],
    }
    assert ramp.validate_profile(profile) == []


def test_validate_profile_rejects_non_numeric_warmup_sec():
    profile = _valid_profile()
    profile["warmup_sec"] = "ten"
    errors = ramp.validate_profile(profile)
    assert any("warmup_sec" in e for e in errors)


def test_validate_profile_rejects_negative_warmup_sec():
    profile = _valid_profile()
    profile["warmup_sec"] = -5
    errors = ramp.validate_profile(profile)
    assert any("warmup_sec" in e for e in errors)


def test_validate_profile_rejects_non_numeric_slow_request_threshold():
    profile = _valid_profile()
    profile["slow_request_threshold_ms"] = "abc"
    errors = ramp.validate_profile(profile)
    assert any("slow_request_threshold_ms" in e for e in errors)


def test_validate_profile_rejects_non_dict_stop_rule():
    profile = _valid_profile()
    profile["stop_rule"] = "not-a-mapping"
    errors = ramp.validate_profile(profile)
    assert any("stop_rule" in e and "mapping" in e for e in errors)


def test_query_report_rows_returns_sentinel_row_when_no_data():
    rows = ramp.query_report_rows("no-such-run-id-ever", "step-01", 0, 60, 2)
    assert len(rows) == 1
    assert rows[0]["label"] == "(no data)"
    assert rows[0]["samples"] == 0

def _endpoint_profile():
    profile = _valid_profile()
    profile["target"] = {"protocol": "http", "host": "rest-target", "port": 4005, "auth": "none"}
    profile["requests"] = [
        {"name": "List customers", "method": "GET", "path": "/customers?limit=5", "extract": "id=$[0].customer_id"},
        {"name": "Get customer", "method": "GET", "path": "/customers/${id}"},
    ]
    return profile


def test_validate_accepts_target_and_requests():
    assert ramp.validate_profile(_endpoint_profile()) == []


def test_validate_requires_target_and_requests_together():
    profile = _endpoint_profile()
    del profile["target"]
    assert any("needs a 'target'" in e for e in ramp.validate_profile(profile))
    profile = _endpoint_profile()
    del profile["requests"]
    assert any("needs a 'requests'" in e for e in ramp.validate_profile(profile))


def test_validate_rejects_bad_request_fields():
    profile = _endpoint_profile()
    profile["requests"] = [
        {"name": "a", "path": "customers"},
        {"name": "a", "path": "/x", "method": "TRACE"},
        {"name": "b", "path": "/y", "extract": "no-equals"},
    ]
    errors = ramp.validate_profile(profile)
    assert any("'path' must" in e for e in errors)
    assert any("duplicate name" in e for e in errors)
    assert any("'method' must" in e for e in errors)
    assert any("'extract' must" in e for e in errors)


def test_validate_rejects_bad_target_fields():
    profile = _endpoint_profile()
    profile["target"] = {"protocol": "ftp", "port": 99999, "auth": "oauth"}
    errors = ramp.validate_profile(profile)
    assert any("target.host" in e for e in errors)
    assert any("target.protocol" in e for e in errors)
    assert any("target.port" in e for e in errors)
    assert any("target.auth" in e for e in errors)


def test_generated_plan_has_defaults_samplers_extractor_and_listener():
    import xml.etree.ElementTree as ET
    xml = ramp.build_plan_from_profile(_endpoint_profile())
    root = ET.fromstring(xml)
    names = [e.get("testname") for e in root.iter() if e.get("testname")]
    assert "HTTP Request Defaults" in names
    assert "List customers" in names and "Get customer" in names
    assert "Extract id" in names
    assert "Prometheus Listener" in names
    assert "GET Customers List" not in names  # template's own samplers are replaced
    assert "HTTP Authorization Manager" not in names  # auth: none
    texts = {e.get("name"): e.text for e in root.iter("stringProp")}
    assert texts["HTTPSampler.domain"] == "rest-target"
    assert texts["JSONPostProcessor.jsonPathExprs"] == "$[0].customer_id"


def test_generated_plan_adds_auth_and_body_headers():
    profile = _endpoint_profile()
    profile["target"]["auth"] = "api_key"
    profile["requests"].append({"name": "Create", "method": "POST", "path": "/customers", "body": '{"a": 1}'})
    xml = ramp.build_plan_from_profile(profile)
    assert "x-api-key" in xml and "BOOMI_TEST_TARGET_API_KEY" in xml
    assert "Content-Type" in xml and "postBodyRaw" in xml
    profile["target"]["auth"] = "basic"
    assert "HTTP Authorization Manager" in ramp.build_plan_from_profile(profile)


def test_validate_pacing_rules():
    profile = _endpoint_profile()
    profile["think_time_ms"] = 250
    profile["requests"][0]["think_time_ms"] = 0
    profile["steps"][0]["rps"] = 20
    assert ramp.validate_profile(profile) == []
    profile["think_time_ms"] = -1
    profile["steps"][0]["rps"] = 0
    errors = ramp.validate_profile(profile)
    assert any("think_time_ms must be" in e for e in errors)
    assert any("'rps' must be" in e for e in errors)


def test_pacing_without_requests_is_rejected():
    profile = _valid_profile()
    profile["think_time_ms"] = 100
    profile["steps"][0]["rps"] = 5
    errors = ramp.validate_profile(profile)
    assert any("think_time_ms needs" in e for e in errors)
    assert any("'rps' needs" in e for e in errors)


def test_generated_plan_includes_think_time_and_throughput_timer():
    profile = _endpoint_profile()
    profile["think_time_ms"] = 250
    profile["requests"][1]["think_time_ms"] = 0  # per-request override wins
    profile["steps"][0]["rps"] = 20
    xml = ramp.build_plan_from_profile(profile)
    assert xml.count('testclass="ConstantTimer"') == 1
    assert "ConstantThroughputTimer" in xml and "RPS_PER_MIN" in xml
    plain = ramp.build_plan_from_profile(_endpoint_profile())
    assert "ConstantTimer" not in plain and "ConstantThroughputTimer" not in plain


def test_validate_regression_block():
    profile = _valid_profile()
    profile["regression"] = {"baseline": "rest-endpoints", "max_p95_increase_pct": 30}
    assert ramp.validate_profile(profile) == []
    profile["regression"] = {"baseline": "../evil", "min_p95_delta_ms": -5}
    errors = ramp.validate_profile(profile)
    assert any("regression.baseline" in e for e in errors)
    assert any("min_p95_delta_ms" in e for e in errors)


def _row(label, p95, err=0.0, step="step-01"):
    return {"step_id": step, "label": label, "samples": 100, "p95_ms": p95, "error_pct": err, "throughput": 10.0}


def _baseline(rows):
    return ramp.build_baseline(_valid_profile(), "20260101-000000", rows)


def test_compare_flags_p95_regression_but_ignores_small_absolute_changes():
    base = _baseline([_row("a", 200), _row("tiny", 3)])
    results = {r["key"]: r for r in ramp.compare_to_baseline(
        [_row("a", 300), _row("tiny", 9)], base, {"max_p95_increase_pct": 25, "min_p95_delta_ms": 10})}
    assert results["step-01|a"]["status"] == "REGRESSION"      # +50% and +100ms
    assert results["step-01|tiny"]["status"] == "ok"            # +200% but only +6ms


def test_compare_flags_error_rate_and_reports_new_and_missing():
    base = _baseline([_row("a", 100, err=0.0), _row("gone", 100)])
    results = {r["key"]: r for r in ramp.compare_to_baseline(
        [_row("a", 100, err=3.0), _row("fresh", 100)], base, {"max_error_rate_increase_pct": 1})}
    assert results["step-01|a"]["status"] == "REGRESSION"
    assert results["step-01|fresh"]["status"] == "new"
    assert results["step-01|gone"]["status"] == "missing"


def test_compare_passes_when_within_limits():
    base = _baseline([_row("a", 200)])
    results = ramp.compare_to_baseline([_row("a", 220)], base, {})
    assert [r["status"] for r in results] == ["ok"]


def test_compare_treats_zero_sample_rows_as_no_data_not_a_pass():
    base = _baseline([_row("a", 200)])
    empty = dict(_row("a", 0), samples=0)
    results = ramp.compare_to_baseline([empty], base, {})
    assert results[0]["status"] == "NO DATA"


def test_tps_converts_to_request_rate_per_iteration():
    profile = _endpoint_profile()  # 2 requests per iteration
    assert ramp.step_request_rate(profile, {"tps": 10}) == 20
    assert ramp.step_request_rate(profile, {"rps": 7}) == 7
    assert ramp.step_request_rate(profile, {}) is None


def test_validate_tps_rules():
    profile = _endpoint_profile()
    profile["steps"][0]["tps"] = 10
    assert ramp.validate_profile(profile) == []
    profile["steps"][0]["rps"] = 5
    assert any("either 'tps'" in e for e in ramp.validate_profile(profile))
    del profile["steps"][0]["rps"]
    profile["steps"][0]["tps"] = 0
    assert any("'tps' must be" in e for e in ramp.validate_profile(profile))
    plain = _valid_profile()
    plain["steps"][0]["tps"] = 5
    assert any("'tps' needs" in e for e in ramp.validate_profile(plain))


def test_generated_plan_has_throughput_timer_for_tps_steps():
    profile = _endpoint_profile()
    profile["steps"][0]["tps"] = 10
    assert "ConstantThroughputTimer" in ramp.build_plan_from_profile(profile)


def test_host_cpu_cores_works_without_sysctl():
    assert ramp.host_cpu_cores() >= 1


def test_compose_command_prefers_standalone_binary_then_plugin(monkeypatch):
    monkeypatch.setattr(ramp.shutil, "which", lambda name: "/usr/local/bin/docker-compose")
    assert ramp.compose_command() == ["docker-compose"]
    monkeypatch.setattr(ramp.shutil, "which", lambda name: None)
    assert ramp.compose_command() == ["docker", "compose"]


# --- JDBC (database) targets ---------------------------------------------------------------------

def _jdbc_profile(**overrides):
    profile = {
        "test_plan": "rest/ramp.jmx",
        "step_duration_sec": 10,
        "target": {"protocol": "jdbc", "driver": "postgresql", "host": "db", "port": 5432, "database": "app"},
        "requests": [
            {"name": "By id", "sql": "SELECT * FROM t WHERE id = ?",
             "params": [{"value": "${__Random(1,100)}", "type": "INTEGER"}]},
            {"name": "Count", "sql": "SELECT count(*) FROM t"},
        ],
        "steps": [{"instances": 1, "threads": 2, "tps": 5}],
    }
    profile.update(overrides)
    return profile


def test_validate_accepts_a_jdbc_profile():
    assert ramp.validate_profile(_jdbc_profile()) == []


def test_validate_jdbc_target_problems():
    for target, fragment in (
        ({"protocol": "jdbc", "driver": "oracle", "host": "db", "database": "app"}, "target.driver"),
        ({"protocol": "jdbc", "driver": "postgresql", "database": "app"}, "target.host"),
        ({"protocol": "jdbc", "driver": "postgresql", "host": "db"}, "target.database"),
        ({"protocol": "jdbc", "driver": "postgresql", "host": "db", "database": "a", "port": 0}, "target.port"),
        ({"protocol": "jdbc", "driver": "postgresql", "host": "db", "database": "a", "auth": "basic"}, "target.auth"),
        ({"protocol": "jdbc", "driver": "postgresql", "host": "db", "database": "a", "allow_writes": "yes"}, "allow_writes"),
    ):
        errors = ramp.validate_profile(_jdbc_profile(target=target))
        assert any(fragment in e for e in errors), (target, errors)


def test_validate_jdbc_query_problems():
    cases = [
        ({"name": "q"}, "'sql'"),
        ({"name": "q", "sql": "  "}, "'sql'"),
        ({"name": "q", "sql": "SELECT 1", "path": "/x"}, "HTTP field"),
        ({"name": "q", "sql": "SELECT 1", "extract": "a=$.b"}, "HTTP field"),
        ({"name": "q", "sql": "SELECT ?"}, "1 '?' placeholder"),
        ({"name": "q", "sql": "SELECT 1", "params": [{"value": 1, "type": "INTEGER"}]}, "0 '?' placeholder"),
        ({"name": "q", "sql": "SELECT ?", "params": [{"value": 1, "type": "TEXTY"}]}, "'type'"),
        ({"name": "q", "sql": "SELECT ?", "params": [{"type": "INTEGER"}]}, "'value'"),
        ({"name": "q", "sql": "SELECT ?", "params": [{"value": "a,b", "type": "VARCHAR"}]}, "comma"),
        ({"name": "q", "sql": "SELECT 1", "query_type": "delete"}, "query_type"),
    ]
    for req, fragment in cases:
        errors = ramp.validate_profile(_jdbc_profile(requests=[req]))
        assert any(fragment in e for e in errors), (req, errors)


def test_validate_jdbc_duplicate_query_names():
    errors = ramp.validate_profile(_jdbc_profile(requests=[
        {"name": "same", "sql": "SELECT 1"}, {"name": "same", "sql": "SELECT 2"}]))
    assert any("duplicate" in e for e in errors)


def test_jdbc_writes_need_allow_writes():
    write = {"name": "ins", "sql": "INSERT INTO t (a) VALUES (?)", "query_type": "update",
             "params": [{"value": "x", "type": "VARCHAR"}]}
    errors = ramp.validate_profile(_jdbc_profile(requests=[write]))
    assert any("allow_writes" in e for e in errors)
    target = {"protocol": "jdbc", "driver": "postgresql", "host": "db", "database": "app", "allow_writes": True}
    assert ramp.validate_profile(_jdbc_profile(requests=[write], target=target)) == []


def test_http_protocol_check_still_rejects_unknown_protocols():
    profile = _jdbc_profile(target={"protocol": "ftp", "host": "x"},
                            requests=[{"name": "a", "method": "GET", "path": "/"}])
    assert any("target.protocol" in e for e in ramp.validate_profile(profile))


def _plan(profile):
    import xml.etree.ElementTree as ET
    return ET.fromstring(ramp.build_plan_from_profile(profile))


def test_jdbc_plan_has_datasource_samplers_timer_and_listener():
    root = _plan(_jdbc_profile())
    tags = [el.tag for el in root.iter()]
    assert "JDBCDataSource" in tags and tags.count("JDBCSampler") == 2
    assert "ConstantThroughputTimer" in tags
    assert any(t.endswith("PrometheusListener") for t in tags)
    assert "HTTPSamplerProxy" not in tags and "AuthManager" not in tags

    ds = next(root.iter("JDBCDataSource"))
    props = {e.get("name"): (e.text or "") for e in ds}
    assert props["dbUrl"] == "jdbc:postgresql://db:5432/app"
    assert props["driver"] == "org.postgresql.Driver"
    assert props["poolMax"] == "0"
    # the login is read from the environment, never written into the plan
    assert "System.getenv" in props["username"] and "System.getenv" in props["password"]

    samplers = {s.get("testname"): {e.get("name"): (e.text or "") for e in s} for s in root.iter("JDBCSampler")}
    assert samplers["By id"]["queryType"] == "Prepared Select Statement"
    assert samplers["By id"]["queryArguments"] == "${__Random(1,100)}"
    assert samplers["By id"]["queryArgumentsTypes"] == "INTEGER"
    assert samplers["Count"]["queryType"] == "Select Statement"
    assert samplers["Count"]["resultSetHandler"] == "Count Records"


def test_jdbc_plan_write_queries_and_think_time():
    write = {"name": "ins", "sql": "INSERT INTO t (a) VALUES (?)", "query_type": "update",
             "params": [{"value": "x", "type": "VARCHAR"}], "think_time_ms": 50}
    target = {"protocol": "jdbc", "driver": "postgresql", "host": "db", "database": "app", "allow_writes": True}
    root = _plan(_jdbc_profile(requests=[write], target=target))
    sampler = next(root.iter("JDBCSampler"))
    assert {e.get("name"): (e.text or "") for e in sampler}["queryType"] == "Prepared Update Statement"
    assert any(t.get("testname") == "Think time" for t in root.iter("ConstantTimer"))


def test_jdbc_plan_without_rate_has_no_throughput_timer():
    root = _plan(_jdbc_profile(steps=[{"instances": 1, "threads": 2}]))
    assert "ConstantThroughputTimer" not in [el.tag for el in root.iter()]


def test_sample_jdbc_profiles_are_valid():
    import yaml
    for name in ("jdbc-postgres-sample", "jdbc-postgres-writes-sample"):
        profile = yaml.safe_load((Path(__file__).resolve().parent.parent / "profiles" / f"{name}.yaml").read_text())
        assert ramp.validate_profile(profile) == [], name
        _plan(profile)


# --- failure visibility (raw results, short steps) -----------------------------------------------

def _write_jtl(path, rows):
    header = "timeStamp,elapsed,label,responseCode,responseMessage,threadName,dataType,success,failureMessage,bytes\n"
    path.write_text(header + "".join(
        f'1,5,{label},{code},"{msg}",t,text,{str(ok).lower()},,0\n' for label, code, msg, ok in rows))


def test_scan_jtl_failures_counts_and_ranks_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(ramp, "RESULTS_DIR", tmp_path)
    _write_jtl(tmp_path / "r1-step-01-1.jtl", [("a", 200, "OK", True)] * 3 + [("a", 401, "Unauthorized", False)] * 2
               + [("a", "null 0", "password authentication failed", False)])
    _write_jtl(tmp_path / "r1-step-01-2.jtl", [("a", 401, "Unauthorized", False)])
    out = ramp.scan_jtl_failures("r1", "step-01", 2)
    assert out["total"] == 7 and out["failed"] == 4
    assert out["top"][0] == ("401", "Unauthorized", 3)
    assert out["top"][1][2] == 1


def test_scan_jtl_failures_without_files_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ramp, "RESULTS_DIR", tmp_path)
    assert ramp.scan_jtl_failures("none", "step-01", 2) == {"step_id": "step-01", "total": 0, "failed": 0, "top": []}


def test_print_jtl_failures_is_silent_when_nothing_failed(capsys):
    ramp.print_jtl_failures([{"step_id": "step-01", "total": 10, "failed": 0, "top": []}])
    assert capsys.readouterr().out == ""
    ramp.print_jtl_failures([{"step_id": "step-02", "total": 10, "failed": 10, "top": [("401", "Unauthorized", 10)]}])
    out = capsys.readouterr().out
    assert "step-02: 10 of 10 samples failed (100.0%)" in out and "401" in out


def test_short_step_warnings():
    profile = {"warmup_sec": 2, "step_duration_sec": 12, "steps": [{"instances": 1, "threads": 1},
                                                                 {"instances": 1, "threads": 1, "duration_sec": 60}]}
    warnings = ramp.short_step_warnings(profile)
    assert len(warnings) == 1 and warnings[0].startswith("step-01")
    assert ramp.short_step_warnings({"step_duration_sec": 30, "steps": [{"instances": 1, "threads": 1}]}) == []


def test_jdbc_login_variable_names_can_be_set_per_profile():
    target = {"protocol": "jdbc", "driver": "postgresql", "host": "db", "database": "app",
              "user_env": "SEP_DB_USER", "password_env": "SEP_DB_PASSWORD"}
    profile = _jdbc_profile(target=target)
    assert ramp.validate_profile(profile) == []
    ds = next(_plan(profile).iter("JDBCDataSource"))
    props = {e.get("name"): (e.text or "") for e in ds}
    assert 'getenv("SEP_DB_USER")' in props["username"] and 'getenv("SEP_DB_PASSWORD")' in props["password"]


def test_jdbc_login_variable_names_default_and_are_validated():
    ds = next(_plan(_jdbc_profile()).iter("JDBCDataSource"))
    props = {e.get("name"): (e.text or "") for e in ds}
    assert 'getenv("BOOMI_TEST_DB_USER")' in props["username"]
    for bad in ("hunter2", "lower_case", "WITH SPACE", "", 5, "A;B"):
        target = {"protocol": "jdbc", "driver": "postgresql", "host": "db", "database": "app", "password_env": bad}
        errors = ramp.validate_profile(_jdbc_profile(target=target))
        assert any("password_env" in e for e in errors), bad


# --- bearer and OAuth2 client-credentials auth ---------------------------------------------------

def _oauth_profile(**oauth):
    profile = _endpoint_profile()
    profile["target"]["auth"] = "oauth2_client_credentials"
    profile["target"]["oauth"] = {"token_url": "https://idp.example.com/oauth2/v1/token", **oauth}
    return profile


def test_validate_oauth_accepts_a_full_block():
    profile = _oauth_profile(scope="loadtest.read", audience="api://x", client_auth="post", refresh_skew_sec=20,
                             client_id_env="MY_ID", client_secret_env="MY_SECRET")
    assert ramp.validate_profile(profile) == []


def test_validate_oauth_rejects_bad_settings():
    profile = _oauth_profile(client_auth="digest", refresh_skew_sec=-1, client_secret_env="not-an-env-name", bogus=1)
    profile["target"]["oauth"]["token_url"] = "idp.example.com/token"
    errors = "\n".join(ramp.validate_profile(profile))
    assert "token_url must be a full http(s) URL" in errors
    assert "client_auth must be one of" in errors
    assert "refresh_skew_sec must be a non-negative integer" in errors
    assert "client_secret_env must be the NAME of an environment variable" in errors
    assert "bogus is not a known setting" in errors


def test_validate_oauth_needs_its_block_and_only_with_that_auth():
    profile = _endpoint_profile()
    profile["target"]["auth"] = "oauth2_client_credentials"
    assert any("needs a target.oauth block" in e for e in ramp.validate_profile(profile))
    profile = _endpoint_profile()
    profile["target"]["oauth"] = {"token_url": "https://idp.example.com/token"}
    assert any("only applies with auth: oauth2_client_credentials" in e for e in ramp.validate_profile(profile))


def test_validate_bearer_token_env_must_be_a_name():
    profile = _endpoint_profile()
    profile["target"]["auth"] = "bearer"
    assert ramp.validate_profile(profile) == []
    profile["target"]["token_env"] = "eyJhbGciOi.real.token"
    assert any("token_env must be the NAME" in e for e in ramp.validate_profile(profile))


def test_generated_plan_bearer_reads_token_from_env():
    profile = _endpoint_profile()
    profile["target"]["auth"] = "bearer"
    xml = ramp.build_plan_from_profile(profile)
    assert 'Bearer ${__groovy(System.getenv("BOOMI_TEST_TARGET_BEARER_TOKEN"))}' in xml
    profile["target"]["token_env"] = "MY_TOKEN"
    assert 'System.getenv("MY_TOKEN")' in ramp.build_plan_from_profile(profile)


def test_generated_plan_oauth_adds_refreshing_preprocessor_and_bearer_header():
    xml = ramp.build_plan_from_profile(_oauth_profile(scope="a b", refresh_skew_sec=15))
    root = ET.fromstring(xml)
    pre = [e for e in root.iter("JSR223PreProcessor")]
    assert len(pre) == 1 and pre[0].get("testname") == "OAuth token"
    script = next(e.text for e in pre[0] if e.get("name") == "script")
    assert "final String TOKEN_URL = 'https://idp.example.com/oauth2/v1/token'" in script
    assert "final String SCOPE = 'a b'" in script
    assert "final long SKEW_MS = 15 * 1000L" in script
    assert "BOOMI_TEST_OAUTH_CLIENT_SECRET" in script and "__" not in script.replace("__P", "")
    assert "Bearer ${__P(oauth_token)}" in xml


def test_groovy_str_escapes_quotes_and_backslashes():
    assert ramp._groovy_str("it's") == "'it\\'s'"
    assert ramp._groovy_str("a\\b") == "'a\\\\b'"
    assert ramp._groovy_str("${x}") == "'${x}'"


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize("stdout,stderr,rc,ok,fragment", [
    ('HTTP 200\n{"access_token":"abc","expires_in":3600}', "", 0, True, "token lifetime 3600s"),
    ('HTTP 200\n{"access_token":"abc","expires_in":5}', "", 0, True, "WARNING: lifetime is not longer"),
    ('HTTP 401\n{"error":"invalid_client"}', "", 0, False, "returned HTTP 401"),
    ("HTTP 200\n{}", "", 0, False, "no access_token"),
    ("MISSING_ENV", "", 3, False, "client id/secret are not set"),
    ("", "curl: (7) Failed to connect", 4, False, "host.docker.internal"),
])
def test_preflight_oauth_messages(monkeypatch, stdout, stderr, rc, ok, fragment):
    monkeypatch.setattr(ramp.subprocess, "run", lambda *a, **k: _completed(stdout, stderr, rc))
    result_ok, message = ramp.preflight_oauth(_oauth_profile(refresh_skew_sec=30))
    assert result_ok is ok and fragment in message


# --- try one request -----------------------------------------------------------------------------

@pytest.mark.parametrize("stdout,stderr,ok,fragment", [
    ('STATUS 200 0.012\n---BODY\n[{"id": 1}]', "", True, "returned HTTP 200 in 12 ms"),
    ('STATUS 401 0.004\n---BODY\n{"error":"no"}', "", False, "returned HTTP 401"),
    ("UNREACHABLE", "curl: (7) Failed to connect", False, "host.docker.internal"),
    ("MISSING_ENV", "", False, "client id/secret are not set"),
    ("TOKEN_HTTP 401 {\"error\":\"invalid_client\"}", "", False, "token endpoint refused"),
])
def test_try_first_request_messages(monkeypatch, stdout, stderr, ok, fragment):
    monkeypatch.setattr(ramp.subprocess, "run", lambda *a, **k: _completed(stdout, stderr, 0))
    result = ramp.try_first_request(_endpoint_profile())
    assert result["ok"] is ok and fragment in result["message"]
    if ok:
        assert result["status"] == 200 and result["body"].startswith("[")


def test_try_first_request_declines_chained_and_database_profiles():
    profile = _endpoint_profile()
    profile["requests"] = [{"name": "Get", "method": "GET", "path": "/customers/${id}"}]
    assert "earlier response" in ramp.try_first_request(profile)["message"]
    profile["target"]["protocol"] = "jdbc"
    assert "HTTP profiles" in ramp.try_first_request(profile)["message"]


# --- API key in the query string, and signed requests (Boomi Cloud API Management) --------------------

def _cam_profile(auth, **target):
    profile = _endpoint_profile()
    profile["target"].update({"auth": auth, **target})
    return profile


def test_validate_api_key_location_and_signature_settings():
    assert ramp.validate_profile(_cam_profile("api_key", api_key_in="query", api_key_param="key")) == []
    assert ramp.validate_profile(_cam_profile("api_key_signature", signature_algorithm="md5", signature_param="signature",
                                              api_key_env="K", api_secret_env="S")) == []
    errors = "\n".join(ramp.validate_profile(_cam_profile("api_key", api_key_in="cookie")))
    assert "api_key_in must be one of" in errors
    errors = "\n".join(ramp.validate_profile(_cam_profile("api_key_signature", signature_algorithm="sha1", api_key_param="bad name",
                                                          api_secret_env="not-a-name")))
    assert "signature_algorithm must be one of" in errors and "plain parameter name" in errors and "NAME of an environment variable" in errors
    errors = "\n".join(ramp.validate_profile(_cam_profile("basic", api_key_in="query", signature_algorithm="md5", api_key_env="K")))
    assert "api_key_in only applies" in errors and "signature_algorithm only applies" in errors and "api_key_env only applies" in errors


def test_api_key_in_query_goes_on_every_request_and_not_in_a_header():
    profile = _cam_profile("api_key", api_key_in="query")
    profile["requests"][0]["path"] = "/customers?limit=5"
    xml = ramp.build_plan_from_profile(profile)
    paths = [e.text for e in ET.fromstring(xml).iter("stringProp") if e.get("name") == "HTTPSampler.path" and e.text]
    assert paths[0] == '/customers?limit=5&api_key=${__groovy(System.getenv("BOOMI_TEST_TARGET_API_KEY"))}'
    assert paths[1] == '/customers/${id}?api_key=${__groovy(System.getenv("BOOMI_TEST_TARGET_API_KEY"))}'
    assert "x-api-key" not in xml
    header = ramp.build_plan_from_profile(_cam_profile("api_key"))
    assert "x-api-key" in header and "api_key=" not in header


def test_signature_expression_digests_key_secret_and_epoch_seconds():
    target = _cam_profile("api_key_signature", signature_param="s", api_key_env="K", api_secret_env="S")["target"]
    suffix = ramp.auth_query_suffix(target)
    assert suffix.startswith('api_key=${__groovy(System.getenv("K"))}&s=${__groovy(')
    expr = ramp._signature_groovy(target)
    assert 'getInstance("SHA-256")' in expr and 'System.getenv("K")' in expr and 'System.getenv("S")' in expr
    assert "currentTimeMillis().intdiv(1000)" in expr and "," not in expr  # a comma would split JMeter function arguments
    target["signature_algorithm"] = "md5"
    assert 'getInstance("MD5")' in ramp._signature_groovy(target)
    assert ramp.auth_query_suffix({"auth": "none"}) == "" and ramp.with_auth_query("/x", {"auth": "none"}) == "/x"


def test_launch_keeps_request_urls_out_of_the_results_file(monkeypatch):
    seen = {}
    monkeypatch.setattr(ramp.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(ramp, "write_target_file", lambda *a, **k: None)
    monkeypatch.setattr(ramp.subprocess, "Popen", lambda cmd, **k: seen.setdefault("cmd", cmd))
    ramp.launch_instance("rest/ramp.jmx", "20260101-000000", "step-01", 1, 2, 10)
    assert "-Jjmeter.save.saveservice.url=false" in seen["cmd"]


# --- friendly errors for a missing or unreadable profile ----------------------------------------------

def test_load_profile_missing_file_exits_with_a_plain_message(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        ramp.load_profile(str(tmp_path / "my-api.yaml"))
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "Profile not found" in err and "profiles/" in err and "Traceback" not in err


def test_load_profile_rejects_a_folder_and_broken_yaml(tmp_path, capsys):
    with pytest.raises(SystemExit):
        ramp.load_profile(str(tmp_path))
    assert "is a folder" in capsys.readouterr().err
    bad = tmp_path / "bad.yaml"
    bad.write_text("steps: [unclosed\n  - nope: : :\n")
    with pytest.raises(SystemExit):
        ramp.load_profile(str(bad))
    assert "Could not read" in capsys.readouterr().err
