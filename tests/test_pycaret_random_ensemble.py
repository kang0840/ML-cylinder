import pandas as pd
import pytest

from tools.pycaret_random_ensemble import (
    choose_random_models,
    ensemble_combinations,
    independent_scores,
    infer_task,
    prepare_frames,
    select_winner,
)


def test_random_three_models_are_reproducible_and_all_blends_are_built():
    available = ["lr", "knn", "nb", "dt", "rf"]
    first = choose_random_models(available, "classification", 3, seed=7)
    second = choose_random_models(available, "classification", 3, seed=7)
    assert first == second
    assert len(set(first)) == 3
    combinations = ensemble_combinations(first)
    assert len(combinations) == 4  # three pairs and one triple
    assert set(combinations[-1]) == set(first)


def test_frames_exclude_identifiers_and_keep_test_separate():
    train = pd.DataFrame({
        "measurement_id": [f"m{i}" for i in range(30)],
        "rms": range(30),
        "crest": range(30, 60),
        "target": ["normal", "fault"] * 15,
    })
    test = pd.DataFrame({
        "measurement_id": ["t1", "t2"],
        "rms": [1, 2], "crest": [3, 4], "target": ["normal", "fault"],
    })
    train_frame, test_frame, features = prepare_frames(train, test, "target")
    assert features == ["rms", "crest"]
    assert len(train_frame) == 30
    assert len(test_frame) == 2


def test_task_inference_and_independent_scores():
    assert infer_task(pd.Series(["normal", "fault"] * 20)) == "classification"
    assert infer_task(pd.Series([float(i) for i in range(100)])) == "regression"
    scores = independent_scores(
        "classification", pd.Series(["a", "b"]), pd.Series(["a", "b"])
    )
    assert scores["test_f1"] == 1.0


def test_missing_test_target_is_rejected():
    train = pd.DataFrame({"x": range(30), "target": range(30)})
    with pytest.raises(ValueError, match="test file is missing target"):
        prepare_frames(train, pd.DataFrame({"x": [1]}), "target")


def test_full_blend_scope_selects_exactly_all_three_models():
    rows = [
        {"kind": "single", "models": "rf", "cv_score": 1.0},
        {"kind": "blend", "models": "rf+dt", "cv_score": 0.99},
        {"kind": "blend", "models": "rf+dt+lda", "cv_score": 0.98},
    ]
    assert select_winner(rows, 3, "all")["models"] == "rf"
    assert select_winner(rows, 3, "full_blend")["models"] == "rf+dt+lda"
