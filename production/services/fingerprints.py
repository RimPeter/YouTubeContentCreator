import hashlib
import json

from .access import ProductionValidationError


def fingerprint_json(value):
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProductionValidationError("Job snapshots must contain valid JSON values.") from exc
    return hashlib.sha256(encoded).hexdigest()
