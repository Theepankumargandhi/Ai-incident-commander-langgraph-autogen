from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any

from app.config import Settings


DEFAULT_FEATURES = (
    "p95_latency_ms",
    "error_rate",
    "up",
    "queue_depth",
    "cpu_utilization",
    "memory_usage_pct",
    "heap_usage_pct",
    "worker_throughput",
)


class IsolationForestMetricAnomalyDetector:
    """Score current service metrics against a recent baseline before LLM handoff."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.contamination = max(0.01, min(settings.anomaly_contamination, 0.5))
        self.min_samples = max(8, settings.anomaly_min_samples)

    def detect(self, current_metrics: dict[str, float], historical_samples: list[dict[str, float]]) -> dict[str, Any]:
        features = self._select_features(current_metrics, historical_samples)
        if not features:
            return self._empty_result("No numeric metric features were available for anomaly detection.")

        baseline = [
            self._vectorize(sample, features)
            for sample in historical_samples
            if self._has_any_feature(sample, features)
        ]
        if len(baseline) < self.min_samples:
            baseline.extend(self._synthetic_baseline(current_metrics, features, self.min_samples - len(baseline)))
        if len(baseline) < self.min_samples:
            return self._empty_result("Not enough baseline samples were available for Isolation Forest.")

        current_vector = self._vectorize(current_metrics, features)

        try:
            from sklearn.ensemble import IsolationForest
        except Exception as exc:  # pragma: no cover - dependency availability is environment-specific
            result = self._empty_result(f"scikit-learn is not installed: {exc}")
            result["model"] = "sklearn.ensemble.IsolationForest"
            result["enabled"] = False
            return result

        model = IsolationForest(
            n_estimators=100,
            contamination=self.contamination,
            random_state=42,
        )
        model.fit(baseline)
        decision = float(model.decision_function([current_vector])[0])
        prediction = int(model.predict([current_vector])[0])
        anomaly_score = round(max(0.0, -decision), 6)
        top_features = self._top_features(current_metrics, historical_samples, features)
        max_z = max((abs(item["z_score"]) for item in top_features), default=0.0)
        is_anomaly = prediction == -1 or max_z >= 3.0

        return {
            "enabled": True,
            "model": "sklearn.ensemble.IsolationForest",
            "is_anomaly": is_anomaly,
            "severity": self._severity(anomaly_score, max_z, is_anomaly),
            "anomaly_score": anomaly_score,
            "decision_function": round(decision, 6),
            "sample_count": len(baseline),
            "contamination": self.contamination,
            "feature_names": list(features),
            "current_vector": [round(value, 6) for value in current_vector],
            "top_features": top_features[:5],
            "summary": self._summary(is_anomaly, top_features, anomaly_score),
        }

    @staticmethod
    def _select_features(current_metrics: dict[str, float], historical_samples: list[dict[str, float]]) -> tuple[str, ...]:
        available = set(current_metrics)
        for sample in historical_samples:
            available.update(sample)
        preferred = [feature for feature in DEFAULT_FEATURES if feature in available]
        extra = sorted(feature for feature in available if feature not in DEFAULT_FEATURES)
        return tuple(preferred + extra)

    @staticmethod
    def _has_any_feature(sample: dict[str, float], features: tuple[str, ...]) -> bool:
        return any(feature in sample and isinstance(sample[feature], (int, float)) for feature in features)

    @staticmethod
    def _vectorize(metrics: dict[str, float], features: tuple[str, ...]) -> list[float]:
        values: list[float] = []
        for feature in features:
            raw = metrics.get(feature, 0.0)
            value = float(raw) if isinstance(raw, (int, float)) and math.isfinite(float(raw)) else 0.0
            values.append(value)
        return values

    @staticmethod
    def _synthetic_baseline(current_metrics: dict[str, float], features: tuple[str, ...], count: int) -> list[list[float]]:
        if count <= 0:
            return []
        normal_defaults = {
            "p95_latency_ms": 450.0,
            "error_rate": 0.01,
            "up": 1.0,
            "queue_depth": 40.0,
            "cpu_utilization": 0.45,
            "memory_usage_pct": 55.0,
            "heap_usage_pct": 55.0,
            "worker_throughput": max(float(current_metrics.get("worker_throughput", 80.0)), 80.0),
        }
        samples: list[list[float]] = []
        for index in range(count):
            scale = 1.0 + (((index % 7) - 3) * 0.035)
            sample = {
                feature: max(0.0, normal_defaults.get(feature, float(current_metrics.get(feature, 1.0)) or 1.0) * scale)
                for feature in features
            }
            samples.append(IsolationForestMetricAnomalyDetector._vectorize(sample, features))
        return samples

    @staticmethod
    def _top_features(
        current_metrics: dict[str, float],
        historical_samples: list[dict[str, float]],
        features: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for feature in features:
            values = [
                float(sample[feature])
                for sample in historical_samples
                if isinstance(sample.get(feature), (int, float)) and math.isfinite(float(sample[feature]))
            ]
            if not values:
                continue
            baseline_mean = mean(values)
            baseline_std = pstdev(values) or 1.0
            current_value = float(current_metrics.get(feature, 0.0) or 0.0)
            z_score = (current_value - baseline_mean) / baseline_std
            rows.append(
                {
                    "metric": feature,
                    "value": round(current_value, 6),
                    "baseline_mean": round(baseline_mean, 6),
                    "baseline_std": round(baseline_std, 6),
                    "z_score": round(z_score, 4),
                    "direction": "high" if z_score >= 0 else "low",
                }
            )
        return sorted(rows, key=lambda item: abs(item["z_score"]), reverse=True)

    @staticmethod
    def _severity(anomaly_score: float, max_z: float, is_anomaly: bool) -> str:
        if not is_anomaly:
            return "normal"
        if anomaly_score >= 0.08 or max_z >= 5.0:
            return "critical"
        if anomaly_score >= 0.03 or max_z >= 3.0:
            return "high"
        return "medium"

    @staticmethod
    def _summary(is_anomaly: bool, top_features: list[dict[str, Any]], anomaly_score: float) -> str:
        if not is_anomaly:
            return "Isolation Forest did not flag the current metric vector as anomalous."
        if not top_features:
            return f"Isolation Forest flagged an anomalous metric vector with score {anomaly_score:.4f}."
        strongest = top_features[0]
        return (
            "Isolation Forest flagged anomalous service telemetry before LLM investigation; "
            f"strongest driver is {strongest['metric']} ({strongest['direction']}, "
            f"z={strongest['z_score']})."
        )

    @staticmethod
    def _empty_result(reason: str) -> dict[str, Any]:
        return {
            "enabled": False,
            "model": "sklearn.ensemble.IsolationForest",
            "is_anomaly": False,
            "severity": "unknown",
            "anomaly_score": 0.0,
            "decision_function": None,
            "sample_count": 0,
            "feature_names": [],
            "current_vector": [],
            "top_features": [],
            "summary": reason,
        }
