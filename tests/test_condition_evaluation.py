import pytest

from system.ML.Condition.evaluation import evaluate_predictions


def row(i, truth, prediction, **extra):
    return dict(
        sample_id=str(i),
        session_id="test",
        ground_truth=truth,
        prediction=prediction,
        **extra
    )


def test_metrics_and_failures_are_separate():
    report = evaluate_predictions(
        [
            row(1, "SEAL_LEAK", "ABNORMAL"),
            row(2, "NORMAL", "NORMAL"),
            row(3, "NORMAL", "SEAL_LEAK"),
            row(4, "SEAL_LEAK", "NORMAL"),
            row(5, "NORMAL", None, inference_status="ERROR"),
            row(6, "SEAL_LEAK", None, inference_status="ABSTAIN"),
            row(7, None, "NORMAL"),
            row(8, "NORMAL", "unknown"),
        ],
        training_session_ids=["train"],
    )
    assert report["confusion_matrix"] == dict(tp=1, fn=1, fp=1, tn=1)
    assert report["metrics"]["accuracy"] == 0.5
    assert report["metrics"]["abnormal_recall"] == 0.5
    assert report["metrics"]["false_alarm_rate"] == 0.5
    assert report["metrics"]["prediction_coverage"] == 4 / 7
    assert report["counts"]["inference_errors"] == 1
    assert report["counts"]["abstentions"] == 1
    assert report["counts"]["invalid_predictions"] == 1
    assert report["training_session_overlap"] == []


def test_no_predictions_is_not_zero_accuracy():
    report = evaluate_predictions([row(1, "NORMAL", None, inference_status="ERROR")])
    assert report["status"] == "NO_EVALUABLE_PREDICTIONS"
    assert report["metrics"]["accuracy"] is None
    assert report["metrics"]["prediction_coverage"] == 0
    assert evaluate_predictions([])["metrics"]["prediction_coverage"] is None


def test_single_class_and_overlap_are_reported():
    report = evaluate_predictions(
        [row(1, "NORMAL", "NORMAL")], training_session_ids=["test"]
    )
    assert report["metrics"]["abnormal_recall"] is None
    assert report["metrics"]["false_alarm_rate"] == 0
    assert "TRAINING_SESSION_OVERLAP" in report["warnings"]
    assert report["evaluation_scope"] == "EXPLORATORY"


def test_duplicates_and_invalid_status_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_predictions([row(1, "NORMAL", "NORMAL")] * 2)
    with pytest.raises(ValueError, match="inference_status"):
        evaluate_predictions([row(1, "NORMAL", "NORMAL", inference_status="failed")])
