from __future__ import annotations

from app.models import ModelRoute, PromptProfile


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

DEFAULT_MODEL_ROUTES = {
    "balanced-route": ModelRoute(
        id="balanced-route",
        label="Balanced Route",
        description="Uses the default model across all investigation roles.",
        planner_model="gpt-4.1-mini",
        investigator_model="gpt-4.1-mini",
        critic_model="gpt-4.1-mini",
        commander_model="gpt-4.1-mini",
    ),
    "quality-route": ModelRoute(
        id="quality-route",
        label="Quality Route",
        description="Uses larger models for planning and final synthesis.",
        planner_model="gpt-4.1",
        investigator_model="gpt-4.1-mini",
        critic_model="gpt-4.1-mini",
        commander_model="gpt-4.1",
    ),
    "cost-aware-route": ModelRoute(
        id="cost-aware-route",
        label="Cost Aware Route",
        description="Prefers smaller models for supporting roles to reduce spend.",
        planner_model="gpt-4.1-mini",
        investigator_model="gpt-4.1-mini",
        critic_model="gpt-4.1-nano",
        commander_model="gpt-4.1-mini",
    ),
}


def get_profile(profile_id: str) -> PromptProfile:
    return DEFAULT_PROFILES.get(profile_id, DEFAULT_PROFILES["balanced-v1"])


def list_profiles() -> list[PromptProfile]:
    return list(DEFAULT_PROFILES.values())


def get_model_route(route_id: str) -> ModelRoute:
    return DEFAULT_MODEL_ROUTES.get(route_id, DEFAULT_MODEL_ROUTES["balanced-route"])


def list_model_routes() -> list[ModelRoute]:
    return list(DEFAULT_MODEL_ROUTES.values())
