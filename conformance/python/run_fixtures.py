import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "core" / "python"))

from prompttrace import KeyNameRedactor, MemorySink, trace


def add(a, b):
    return a + b


def boom():
    raise TypeError("bad input")


def identity(value):
    return value


def double(x):
    return x * 2


FUNCTIONS = {"add": add, "boom": boom, "identity": identity, "double": double}


def normalize(record, scenario):
    artifacts = dict(record["artifacts"])
    if isinstance(artifacts.get("input"), dict):
        artifacts["input"] = {"args": artifacts["input"]["args"]}
    out = {
        "scenario": scenario["name"],
        "operation": record["operation"],
        "status": record["status"],
        "keys": record["keys"],
        "artifacts": artifacts,
        "metrics": record["metrics"],
        "sdkVersion": record["sdk"]["version"],
    }
    if "error" in record:
        out["error"] = {
            "type": record["error"].get("type"),
            "message": record["error"].get("message"),
        }
    return out


def run(scenario):
    sink = MemorySink()
    fn = FUNCTIONS[scenario["fn"]]
    options = {
        "sink": sink,
        "operation": scenario["operation"],
        "capture_input": scenario.get("captureInput", True),
        "capture_output": scenario.get("captureOutput", True),
    }
    if scenario.get("redactSensitive"):
        options["redactors"] = [KeyNameRedactor()]
    if "keys" in scenario:
        options["keys"] = scenario["keys"]
    if scenario.get("runtimeKeys"):
        expected = scenario["runtimeKeys"]
        options["keys"] = lambda args, kwargs: {
            key: value for key, value in expected.items()
        }
    if "artifacts" in scenario:
        fixed = scenario["artifacts"]
        options["artifacts"] = lambda ctx, fixed=fixed: dict(fixed)
    if "metrics" in scenario:
        fixed = scenario["metrics"]
        options["metrics"] = lambda ctx, fixed=fixed: dict(fixed)

    wrapped = trace(**options)(fn)
    args = scenario["args"]
    try:
        result = wrapped(*args)
        if "expectedOutput" in scenario:
            assert result == scenario["expectedOutput"], result
    except Exception as exc:
        expected = scenario.get("expectedError", {})
        if expected:
            assert type(exc).__name__ == expected["type"]
            assert str(exc) == expected["message"]
        else:
            raise
    return normalize(sink.records[0], scenario)


def main():
    root = Path(__file__).resolve().parents[1]
    scenarios = json.loads((root / "fixtures" / "scenarios.json").read_text())["scenarios"]
    records = [run(scenario) for scenario in scenarios]
    out = root / "expected"
    out.mkdir(exist_ok=True)
    for record in records:
        (out / f"{record['scenario']}.json").write_text(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()