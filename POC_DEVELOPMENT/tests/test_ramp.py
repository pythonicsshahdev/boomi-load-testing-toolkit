import sys
from pathlib import Path

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
        profile = yaml.safe_load((Path(__file__).resolve().parent.parent / "ramp-profiles" / f"{name}.yaml").read_text())
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
