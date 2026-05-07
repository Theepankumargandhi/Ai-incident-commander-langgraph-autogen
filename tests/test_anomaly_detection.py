from app.config import Settings
from app.core.causal import CausalReasoningEngine
from app.integrations.observability import PrometheusObservabilityClient
from app.models import Incident, IncidentSeverity, ObservabilitySnapshot


class FakeAnomalyDetector:
    def detect(self, _current_metrics, historical_samples):
        return {
            "enabled": True,
            "model": "sklearn.ensemble.IsolationForest",
            "is_anomaly": True,
            "severity": "high",
            "anomaly_score": 0.081,
            "sample_count": len(historical_samples),
            "top_features": [
                {
                    "metric": "p95_latency_ms",
                    "value": 2400.0,
                    "baseline_mean": 420.0,
                    "z_score": 4.7,
                    "direction": "high",
                }
            ],
            "summary": "Isolation Forest flagged anomalous service telemetry before LLM investigation.",
        }


def test_observability_adds_isolation_forest_anomaly_context_before_llm() -> None:
    settings = Settings(app_env="test", enable_anomaly_detection=True)
    client = PrometheusObservabilityClient(settings)
    client.anomaly_detector = FakeAnomalyDetector()
    incident = Incident(
        title="Checkout latency spike",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="p95 latency breached SLO.",
        source="prometheus",
        metrics={"p95_latency_ms": 2400.0, "error_rate": 0.03},
        context={
            "metric_history": [
                {"p95_latency_ms": 410.0, "error_rate": 0.01, "up": 1.0},
                {"p95_latency_ms": 430.0, "error_rate": 0.012, "up": 1.0},
            ]
        },
    )

    snapshot = client.collect(incident)

    assert snapshot.anomaly_detected is True
    assert snapshot.anomaly_score == 0.081
    assert snapshot.anomaly_detection["model"] == "sklearn.ensemble.IsolationForest"
    assert snapshot.query_details["anomaly_detection"]["severity"] == "high"
    assert "Isolation Forest" in snapshot.logs[0]


def test_causal_analysis_uses_anomaly_signal_as_pre_llm_evidence() -> None:
    incident = Incident(
        title="Checkout degradation",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.medium,
        description="Customer reports are increasing.",
        source="prometheus",
        metrics={"error_rate": 0.01},
    )
    snapshot = ObservabilitySnapshot(
        metrics={"p95_latency_ms": 900.0, "error_rate": 0.01},
        logs=["No dominant log signal."],
        anomaly_detected=True,
        anomaly_score=0.081,
        anomaly_detection={
            "summary": "Isolation Forest flagged anomalous service telemetry before LLM investigation.",
            "top_features": [{"metric": "p95_latency_ms"}],
        },
    )

    result = CausalReasoningEngine().analyze(incident, snapshot, [])

    assert "Anomaly detection isolated abnormal p95_latency_ms" in result.likely_cause
    assert any("Isolation Forest" in item for item in result.reasoning)
