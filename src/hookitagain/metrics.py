import json


def emit_count(name: str, value: int = 1) -> None:
    # names from docs/metrics.md, one JSON line on stdout for CloudWatch Logs
    print(json.dumps({"metric": name, "unit": "count", "value": value}), flush=True)


def emit_ms(name: str, value: int) -> None:
    print(
        json.dumps({"metric": name, "unit": "milliseconds", "value": value}),
        flush=True,
    )
