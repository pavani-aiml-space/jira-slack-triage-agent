"""
Confidence Router — pure function mapping a confidence score to a routing tier.

No I/O, no settings import — thresholds are passed in explicitly so this stays
trivially testable and reusable outside the triage pipeline if needed.
"""
from __future__ import annotations

from typing import Literal

RouteDecision = Literal["auto_act", "flag", "escalate"]


def route_confidence(
    confidence: float,
    auto_act_threshold: float,
    ask_human_threshold: float,
) -> RouteDecision:
    """
    Map a confidence score to a routing tier.

    - confidence >= auto_act_threshold        → "auto_act"  (file directly)
    - ask_human_threshold <= confidence        → "flag"      (file + flag for review)
    - confidence < ask_human_threshold          → "escalate"  (don't file yet — confirm with a human)

    Both threshold comparisons are inclusive on their lower bound, so a
    confidence exactly equal to a threshold takes the higher tier.
    """
    if confidence >= auto_act_threshold:
        return "auto_act"
    if confidence >= ask_human_threshold:
        return "flag"
    return "escalate"
