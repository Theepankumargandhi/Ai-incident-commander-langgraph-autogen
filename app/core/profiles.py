from __future__ import annotations

from app.models import PromptProfile


DEFAULT_PROFILES = {
    "balanced-v1": PromptProfile(
        id="balanced-v1",
        label="Balanced",
        description="Default profile with balanced confidence and remediation aggressiveness.",
    ),
    "conservative-v1": PromptProfile(
        id="conservative-v1",
        label="Conservative",
        description="Lower-confidence profile that prefers safer remediations.",
        confidence_bias=-0.08,
        aggressiveness_bias=-0.2,
    ),
    "aggressive-v1": PromptProfile(
        id="aggressive-v1",
        label="Aggressive",
        description="Higher-confidence profile that proposes stronger operational actions.",
        confidence_bias=0.06,
        aggressiveness_bias=0.25,
    ),
}


def get_profile(profile_id: str) -> PromptProfile:
    return DEFAULT_PROFILES.get(profile_id, DEFAULT_PROFILES["balanced-v1"])


def list_profiles() -> list[PromptProfile]:
    return list(DEFAULT_PROFILES.values())
