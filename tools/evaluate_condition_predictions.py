"""Evaluate a JSON file of existing predictions; never train or write to DB."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from system.ML.Condition.evaluation import evaluate_predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        parser.error("input must be an object containing a rows array")
    report = evaluate_predictions(
        data["rows"], training_session_ids=data.get("training_session_ids")
    )
    report["dataset_description"] = data.get("dataset_description")
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        # Preserve previous reports and input files rather than overwrite them.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
