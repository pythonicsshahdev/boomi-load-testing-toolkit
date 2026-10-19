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
