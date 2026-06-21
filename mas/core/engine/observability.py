"""Observability helpers for runtime instrumentation.

This module provides lightweight operation timing, correlation-id propagation,
and SLO summary reporting without external telemetry dependencies.

Harvested from codex-mas (proj-YYYYMMDD-NNN). Metric samples are persisted
through ``core.utils.log_helpers.append_event`` (episodic.db) under the
``observability.metric`` action type, so ``list_metric_samples`` reads them back
from the same store the rest of the engine uses.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from time import perf_counter
from typing import Any, Iterator

from core.engine.audit_logger import get_logger
from core.utils import log_helpers


# Initial SLO thresholds; values are intentionally conservative.
SLO_THRESHOLDS: dict[str, dict[str, float]] = {
    "state.load": {"p95_ms": 250.0, "p99_ms": 500.0, "min_success_rate": 99.0},
    "state.save": {"p95_ms": 300.0, "p99_ms": 600.0, "min_success_rate": 99.0},
    "handoff.create": {"p95_ms": 300.0, "p99_ms": 700.0, "min_success_rate": 99.0},
    "handoff.accept": {"p95_ms": 200.0, "p99_ms": 500.0, "min_success_rate": 99.0},
    "handoff.reject": {"p95_ms": 200.0, "p99_ms": 500.0, "min_success_rate": 99.0},
    "lifecycle.check_phase_artifacts": {"p95_ms": 100.0, "p99_ms": 200.0, "min_success_rate": 99.0},
    "lifecycle.check_close": {"p95_ms": 150.0, "p99_ms": 300.0, "min_success_rate": 99.0},
    "lifecycle.check_spawn": {"p95_ms": 100.0, "p99_ms": 200.0, "min_success_rate": 99.0},
    "consultation.required_for": {"p95_ms": 150.0, "p99_ms": 300.0, "min_success_rate": 99.0},
    "event.record": {"p95_ms": 120.0, "p99_ms": 300.0, "min_success_rate": 99.0},
}


@dataclass
class MetricSample:
    operation: str
    project_id: str
    correlation_id: str
    actor: str
    success: bool
    duration_ms: float
    timestamp: str
    error: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def correlation_id_from_state(state: dict | None) -> str:
    if not isinstance(state, dict):
        return ""
    ci = state.get("core_identity", {})
    if not isinstance(ci, dict):
        return ""
    value = ci.get("request_id") or ci.get("correlation_id")
    return str(value) if value else ""


def emit_metric(sample: MetricSample, db_url: str | None = None) -> None:
    logger = get_logger()
    try:
        logger.log(
            "observability_metric",
            operation=sample.operation,
            project_id=sample.project_id,
            correlation_id=sample.correlation_id or None,
            actor=sample.actor,
            success=sample.success,
            duration_ms=sample.duration_ms,
            error=sample.error,
            details=sample.details or None,
        )
    except Exception:
        # Non-fatal by design; try DB-backed persistence even if file audit logging fails.
        pass

    payload = {"metric": asdict(sample)}
    try:
        log_helpers.append_event(
            project_id=sample.project_id or "system",
            agent_id=sample.actor or "system",
            action_type="observability.metric",
            intent=sample.operation,
            result_shape="metric",
            payload=payload,
            db_url=db_url,
        )
    except Exception:
        # Non-fatal by design; file-based audit log still captures the sample.
        pass


@contextmanager
def observe_operation(
    operation: str,
    *,
    project_id: str,
    correlation_id: str = "",
    actor: str = "system",
    details: dict[str, Any] | None = None,
    db_url: str | None = None,
) -> Iterator[None]:
    started = perf_counter()
    success = True
    error_message: str | None = None
    try:
        yield
    except Exception as exc:  # noqa: BLE001
        success = False
        error_message = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        duration_ms = round((perf_counter() - started) * 1000.0, 3)
        emit_metric(
            MetricSample(
                operation=operation,
                project_id=project_id,
                correlation_id=correlation_id,
                actor=actor,
                success=success,
                duration_ms=duration_ms,
                timestamp=_utc_now_iso(),
                error=error_message,
                details=details or {},
            ),
            db_url=db_url,
        )


def _extract_metric_dict(row_payload: Any) -> dict[str, Any] | None:
    payload_obj = row_payload
    if isinstance(payload_obj, str):
        try:
            payload_obj = json.loads(payload_obj)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload_obj, dict):
        return None

    # append_event stores a wire-shaped envelope; the real payload lives here.
    metric = (
        payload_obj.get("params", {})
        .get("inputs", {})
        .get("metric")
    )
    if isinstance(metric, dict):
        return metric
    return None


def list_metric_samples(
    *,
    project_id: str,
    operation: str | None = None,
    limit: int = 200,
    db_url: str | None = None,
) -> list[MetricSample]:
    events = log_helpers.query_events(
        project_id=project_id,
        action_type="observability.metric",
        limit=limit,
        db_url=db_url,
    )
    samples: list[MetricSample] = []
    for row in events:
        metric = _extract_metric_dict(row.get("payload"))
        if not metric:
            continue
        if operation and metric.get("operation") != operation:
            continue
        try:
            samples.append(
                MetricSample(
                    operation=str(metric.get("operation", "")),
                    project_id=str(metric.get("project_id", project_id)),
                    correlation_id=str(metric.get("correlation_id", "")),
                    actor=str(metric.get("actor", "system")),
                    success=bool(metric.get("success", False)),
                    duration_ms=float(metric.get("duration_ms", 0.0)),
                    timestamp=str(metric.get("timestamp", "")),
                    error=str(metric.get("error")) if metric.get("error") else None,
                    details=metric.get("details") if isinstance(metric.get("details"), dict) else {},
                )
            )
        except Exception:
            continue
    return samples


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight, 3)


def build_slo_report_from_samples(samples: list[MetricSample]) -> dict[str, Any]:
    grouped: dict[str, list[MetricSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.operation, []).append(sample)

    operations: dict[str, dict[str, Any]] = {}
    breaches: list[dict[str, Any]] = []

    for operation, op_samples in grouped.items():
        latencies = [s.duration_ms for s in op_samples]
        successes = [s for s in op_samples if s.success]
        success_rate = (len(successes) / len(op_samples) * 100.0) if op_samples else 0.0
        summary = {
            "count": len(op_samples),
            "success_rate": round(success_rate, 3),
            "avg_ms": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
            "p95_ms": _percentile(latencies, 0.95),
            "p99_ms": _percentile(latencies, 0.99),
        }

        threshold = SLO_THRESHOLDS.get(operation, {})
        p95_limit = float(threshold.get("p95_ms", 0.0))
        p99_limit = float(threshold.get("p99_ms", 0.0))
        min_success = float(threshold.get("min_success_rate", 0.0))

        checks = {
            "p95_ok": summary["p95_ms"] <= p95_limit if p95_limit else True,
            "p99_ok": summary["p99_ms"] <= p99_limit if p99_limit else True,
            "success_ok": summary["success_rate"] >= min_success if min_success else True,
        }
        summary["thresholds"] = {
            "p95_ms": p95_limit,
            "p99_ms": p99_limit,
            "min_success_rate": min_success,
        }
        summary["checks"] = checks
        operations[operation] = summary

        if not all(checks.values()):
            breaches.append({
                "operation": operation,
                "checks": checks,
                "observed": {
                    "p95_ms": summary["p95_ms"],
                    "p99_ms": summary["p99_ms"],
                    "success_rate": summary["success_rate"],
                },
                "thresholds": summary["thresholds"],
            })

    return {
        "sample_count": len(samples),
        "operation_count": len(operations),
        "operations": operations,
        "breaches": breaches,
    }


def build_slo_report(
    *,
    project_id: str,
    operation: str | None = None,
    limit: int = 500,
    db_url: str | None = None,
) -> dict[str, Any]:
    samples = list_metric_samples(
        project_id=project_id,
        operation=operation,
        limit=limit,
        db_url=db_url,
    )
    return {
        "project_id": project_id,
        "operation_filter": operation,
        "generated_at": _utc_now_iso(),
        "report": build_slo_report_from_samples(samples),
    }
