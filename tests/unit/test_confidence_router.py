"""
Unit tests for confidence_router.py — pure function, no mocking needed.
"""
from pipeline.confidence_router import route_confidence


def test_at_or_above_auto_act_threshold_is_auto_act():
    assert route_confidence(0.90, 0.90, 0.65) == "auto_act"
    assert route_confidence(0.95, 0.90, 0.65) == "auto_act"
    assert route_confidence(1.0, 0.90, 0.65) == "auto_act"


def test_between_thresholds_is_flag():
    assert route_confidence(0.65, 0.90, 0.65) == "flag"
    assert route_confidence(0.78, 0.90, 0.65) == "flag"
    assert route_confidence(0.8999, 0.90, 0.65) == "flag"


def test_below_ask_human_threshold_is_escalate():
    assert route_confidence(0.6499, 0.90, 0.65) == "escalate"
    assert route_confidence(0.5, 0.90, 0.65) == "escalate"
    assert route_confidence(0.0, 0.90, 0.65) == "escalate"


def test_custom_thresholds_respected():
    # Wider "auto act" band
    assert route_confidence(0.80, 0.80, 0.50) == "auto_act"
    assert route_confidence(0.79, 0.80, 0.50) == "flag"
    assert route_confidence(0.49, 0.80, 0.50) == "escalate"


def test_boundary_is_inclusive_on_upper_tier():
    # Exactly at ask_human_threshold takes "flag", not "escalate"
    assert route_confidence(0.65, 0.90, 0.65) == "flag"
