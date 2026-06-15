"""
Wire Protocol

Encodes, decodes, and validates MAS wire-format payloads.
Canonical location: core.utils.wire_protocol
"""

from __future__ import annotations

import sys
import json
import argparse
from typing import Any

# ---------------------------------------------------------------------------
# Protocol version
# ---------------------------------------------------------------------------

WIRE_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Status code vocabulary
# ---------------------------------------------------------------------------

STATUS_CODES: dict[str, str] = {
    "spec_analysis:ok":           "Spec analyzed — all fields present and clear",
    "spec_analysis:incomplete":   "Spec missing critical fields",
    "clarification:complete":     "All clarification rounds complete",
    "clarification:pending":      "Clarification rounds in progress",
    "product_plan:ready":         "Product plan produced and ready for approval",
    "product_plan:revision":      "Product plan revised per feedback",
    "spec:ready":                 "Specification written and complete",
    "capability:match":           "Required capability found in registry",
    "capability:gap":             "Gap found — gap certificate issued",
    "capability:no_gaps":         "No capability gaps — all covered",
    "exec_plan:ready":            "Execution plan compiled",
    "milestone:complete":         "Milestone all tasks completed",
    "task:complete":              "Task completed successfully",
    "task:blocked":               "Task blocked — dependency or resource issue",
    "task:failed":                "Task failed — requires intervention",
    "eval:report_ready":          "Evaluation report produced",
    "eval:pass":                  "Project passed evaluation",
    "eval:fail":                  "Project failed evaluation — issues found",
    "training:proposals_ready":   "Training proposals generated",
    "training:backlog_updated":   "Training backlog updated",
    "scribe:folder_ready":        "Project folder initialized",
    "scribe:recorded":            "Event recorded in project memory",
    "spawn:package_ready":        "Agent package built and ready for review",
    "spawn:rejected":             "Spawn request rejected",
    "consult:approve":            "Recommends approval",
    "consult:caution":            "Recommends approval with caveats",
    "consult:oppose":             "Recommends against",
    "ok":                         "Operation completed successfully",
    "error":                      "Operation failed",
    "pending":                    "Operation in progress",
    "escalate":                   "Human escalation required",
}

# ---------------------------------------------------------------------------
# Field mappings (expanded ↔ compact)
# ---------------------------------------------------------------------------

_PAYLOAD_MAP: dict[str, str] = {
    "summary":                    "s",
    "artifacts_produced":         "art",
    "decisions_made":             "dec",
    "open_questions":             "oq",
    "constraints_for_next":       "con",
    "shared_state_fields_modified": "mod",
    "findings":                   "f",
    "risk_level":                 "rl",
    "recommendation":             "rec",
    "key_concerns":               "kc",
    "reasoning":                  "rsn",
    "status":                     "st",
    "action":                     "act",
    "path":                       "path",
    "data":                       "d",
}

_COMPACT_TO_EXPANDED: dict[str, str] = {v: k for k, v in _PAYLOAD_MAP.items()}

FINDING_SCHEMA = {"path": "path", "status": "st", "action": "act"}
FINDING_EXPAND = {v: k for k, v in FINDING_SCHEMA.items()}


class WireEncoder:
    def encode(self, payload: dict) -> dict:
        wire: dict[str, Any] = {"_v": WIRE_VERSION}

        for full_key, compact_key in _PAYLOAD_MAP.items():
            val = payload.get(full_key)
            if val is None:
                continue
            if isinstance(val, (list, dict)) and not val:
                continue
            if isinstance(val, str) and not val:
                continue

            if full_key == "findings" and isinstance(val, list):
                wire[compact_key] = [self._encode_finding(f) for f in val]
            else:
                wire[compact_key] = val

        for k, v in payload.items():
            if k not in _PAYLOAD_MAP and k != "_v":
                if v is not None and v != [] and v != "":
                    wire[k] = v

        return wire

    def _encode_finding(self, f: dict) -> dict:
        if not isinstance(f, dict):
            return f
        result = {}
        for full_k, compact_k in FINDING_SCHEMA.items():
            if full_k in f:
                result[compact_k] = f[full_k]
        for k, v in f.items():
            if k not in FINDING_SCHEMA:
                result[k] = v
        return result


class WireDecoder:
    def decode(self, wire: dict) -> dict:
        expanded: dict[str, Any] = {}

        for k, v in wire.items():
            if k == "_v":
                continue

            full_key = _COMPACT_TO_EXPANDED.get(k)
            if full_key:
                if full_key == "findings" and isinstance(v, list):
                    expanded[full_key] = [self._decode_finding(f) for f in v]
                else:
                    expanded[full_key] = v
            else:
                expanded[k] = v

        return expanded

    def _decode_finding(self, f: dict) -> dict:
        if not isinstance(f, dict):
            return f
        result = {}
        for compact_k, full_k in FINDING_EXPAND.items():
            if compact_k in f:
                result[full_k] = f[compact_k]
        for k, v in f.items():
            if k not in FINDING_EXPAND:
                result[k] = v
        return result


class WireValidator:
    def validate(self, payload: dict) -> tuple[bool, list[str]]:
        warnings: list[str] = []
        if "_v" not in payload:
            warnings.append("Missing _v version field — payload is in legacy expanded format")
            return False, warnings

        v = payload.get("_v")
        if v != WIRE_VERSION:
            warnings.append(f"Unknown wire protocol version: {v!r} (expected {WIRE_VERSION!r})")

        s = payload.get("s")
        if s and isinstance(s, str):
            if s not in STATUS_CODES and ":" not in s:
                warnings.append(f"Unknown status code: {s!r}. Use 'section:status' format.")

        rsn = payload.get("rsn", "")
        if isinstance(rsn, str) and rsn:
            word_count = len(rsn.split())
            if word_count > 100:
                warnings.append(
                    f"reasoning (rsn) is {word_count} words — max 100 words per protocol spec"
                )

        return len(warnings) == 0, warnings

    def is_wire_format(self, payload: dict) -> bool:
        return isinstance(payload, dict) and "_v" in payload


_encoder = WireEncoder()
_decoder = WireDecoder()
_validator = WireValidator()


def encode(payload: dict) -> dict:
    return _encoder.encode(payload)


def decode(wire: dict) -> dict:
    return _decoder.decode(wire)


def validate(payload: dict) -> tuple[bool, list[str]]:
    return _validator.validate(payload)


def is_wire_format(payload: dict) -> bool:
    return _validator.is_wire_format(payload)


def encode_decode_roundtrip(payload: dict) -> dict:
    return decode(encode(payload))


def _read_json_arg(text: str | None) -> dict:
    if text:
        return json.loads(text)
    if not sys.stdin.isatty():
        return json.loads(sys.stdin.read())
    raise ValueError("Provide JSON as argument or pipe via stdin")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wire Protocol encoder/decoder/validator",
        epilog='echo \'{"summary":"task:complete"}\' | uv run python mas/core/wire_protocol.py encode',
    )
    sub = parser.add_subparsers(dest="command", required=True)

    enc = sub.add_parser("encode", help="Encode expanded payload to wire format")
    enc.add_argument("json", nargs="?", help="JSON payload (or pipe via stdin)")

    dec = sub.add_parser("decode", help="Decode wire format to expanded payload")
    dec.add_argument("json", nargs="?", help="JSON wire payload (or pipe via stdin)")

    val = sub.add_parser("validate", help="Validate a payload")
    val.add_argument("json", nargs="?", help="JSON payload (or pipe via stdin)")

    sub.add_parser("codes", help="List all status codes")

    ns = parser.parse_args()

    if ns.command == "codes":
        for code, desc in sorted(STATUS_CODES.items()):
            print(f"  {code:<35} {desc}")
        return 0

    try:
        data = _read_json_arg(getattr(ns, "json", None))
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    if ns.command == "encode":
        result = encode(data)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif ns.command == "decode":
        result = decode(data)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif ns.command == "validate":
        ok, warnings = validate(data)
        status = "COMPLIANT" if ok else "NON-COMPLIANT"
        print(f"[{status}]")
        for w in warnings:
            print(f"  warning: {w}")
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
