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