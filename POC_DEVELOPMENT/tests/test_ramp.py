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
