"""Improved KNN implementation with data cleaning, normalization,
weighted distance, and K optimization.

Classes:
 - DataCleaner
 - Normalizer
 - KOptimizer
 - WeightedDistance
 - KNNClassifier

This module is pure-Python and includes type hints.
"""
from typing import List, Tuple, Dict, Any, Optional
import math
import json
import random


class DataCleaner:
    def __init__(self):
        pass

    def remove_duplicates(self, X: List[List[float]], y: List[str]) -> Tuple[List[List[float]], List[str]]:
        seen = set()
        newX, newy = [], []
        before = len(X)
        for xi, yi in zip(X, y):
            key = (tuple(float(v) for v in xi), str(yi))
            if key in seen:
                continue
            seen.add(key)
            newX.append(list(xi))
            newy.append(yi)
        after = len(newX)
        print(f"[DataCleaner] remove_duplicates: before={before} after={after}")
        return newX, newy

    def validate_labels(self, y: List[Any], allowed_labels: Optional[List[str]] = None) -> Tuple[List[int], List[int]]:
        """Return indices (keep, drop) based on label validation.

        - None, empty-string, non-string are dropped
        - if allowed_labels provided, labels not in that list are dropped
        """
        keep_idx, drop_idx = [], []
        for i, lab in enumerate(y):
            if lab is None:
                print(f"[DataCleaner] Warning: label None at index {i}")
                drop_idx.append(i)
                continue
            if not isinstance(lab, str):
                print(f"[DataCleaner] Warning: label not str at index {i}: {lab}")
                drop_idx.append(i)
                continue
            if lab.strip() == "":
                print(f"[DataCleaner] Warning: empty label at index {i}")
                drop_idx.append(i)
                continue
            if allowed_labels is not None and lab not in allowed_labels:
                print(f"[DataCleaner] Warning: undefined label '{lab}' at index {i}")
                drop_idx.append(i)
                continue
            keep_idx.append(i)
        return keep_idx, drop_idx


class Normalizer:
    def __init__(self):
        self.min_: Optional[List[float]] = None
        self.max_: Optional[List[float]] = None

    def fit(self, X: List[List[float]]) -> None:
        if not X:
            raise ValueError("Cannot fit Normalizer on empty data")
        dim = len(X[0])
        self.min_ = [float('inf')] * dim
        self.max_ = [float('-inf')] * dim
        for x in X:
            if len(x) != dim:
                raise ValueError("Inconsistent feature dimensions")
            for i, v in enumerate(x):
                if v < self.min_[i]:
                    self.min_[i] = v
                if v > self.max_[i]:
                    self.max_[i] = v

    def transform(self, x: List[float]) -> List[float]:
        if self.min_ is None or self.max_ is None:
            raise ValueError("Normalizer not fitted")
        if len(x) != len(self.min_):
            raise ValueError("Dimension mismatch in transform")
        out = []
        for i, v in enumerate(x):
            denom = self.max_[i] - self.min_[i]
            if denom == 0:
                # all values equal for this feature; map to 0.0
                out.append(0.0)
            else:
                out.append((v - self.min_[i]) / denom)
        return out

    def fit_transform(self, X: List[List[float]]) -> List[List[float]]:
        self.fit(X)
        return [self.transform(x) for x in X]


def _accuracy(y_true: List[str], y_pred: List[str]) -> float:
    if not y_true:
        return 0.0
    correct = sum(1 for a, b in zip(y_true, y_pred) if a == b)
    return correct / len(y_true)

def _precision_recall_f1(y_true: List[str], y_pred: List[str]) -> Tuple[float, float, float]:
    # macro average across classes
    labels = sorted(set(y_true) | set(y_pred))
    precisions, recalls = [], []
    for lab in labels:
        tp = sum(1 for a, b in zip(y_true, y_pred) if a == lab and b == lab)
        fp = sum(1 for a, b in zip(y_true, y_pred) if a != lab and b == lab)
        fn = sum(1 for a, b in zip(y_true, y_pred) if a == lab and b != lab)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        precisions.append(prec)
        recalls.append(rec)
    if not precisions:
        return 0.0, 0.0, 0.0
    p = sum(precisions) / len(precisions)
    r = sum(recalls) / len(recalls)
    f1 = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0
    return p, r, f1


class KOptimizer:
    def __init__(self, min_k: int = 1, max_k: Optional[int] = None, folds: int = 5):
        self.min_k = min_k
        self.max_k = max_k
        self.folds = folds

    def search_best_k(self, X: List[List[float]], y: List[str], max_k_manual: Optional[int] = None) -> Dict[str, Any]:
        n = len(X)
        if n == 0:
            raise ValueError("Empty training set for K optimization")
        sqrt_n = int(math.sqrt(n))
        max_k = self.max_k or max(15, sqrt_n)
        max_k = min(max_k, max(15, sqrt_n))
        if max_k_manual is not None:
            max_k = max_k_manual
        ks = list(range(self.min_k, min(max_k, n) + 1))
        random.seed(0)
        idx = list(range(n))
        random.shuffle(idx)
        folds = self.folds
        # partition indices
        parts = [idx[i::folds] for i in range(folds)]
        best = {'k': None, 'accuracy': -1.0, 'metrics': {}}
        results = {}
        for k in ks:
            accs, ps, rs, f1s = [], [], [], []
            for i in range(folds):
                test_idx = parts[i]
                train_idx = [j for j in idx if j not in test_idx]
                X_train = [X[j] for j in train_idx]
                y_train = [y[j] for j in train_idx]
                X_test = [X[j] for j in test_idx]
                y_test = [y[j] for j in test_idx]
                clf = KNNClassifier(k=k)
                clf.fit(X_train, y_train)
                preds = clf.predict(X_test)
                accs.append(_accuracy(y_test, preds))
                p, r, f1 = _precision_recall_f1(y_test, preds)
                ps.append(p); rs.append(r); f1s.append(f1)
            mean_acc = sum(accs)/len(accs)
            mean_p = sum(ps)/len(ps)
            mean_r = sum(rs)/len(rs)
            mean_f1 = sum(f1s)/len(f1s)
            results[k] = {'accuracy': mean_acc, 'precision': mean_p, 'recall': mean_r, 'f1': mean_f1}
            if mean_acc > best['accuracy']:
                best['k'] = k
                best['accuracy'] = mean_acc
                best['metrics'] = results[k]
        print("[KOptimizer] search results:")
        for k, v in results.items():
            print(f"K={k} Accuracy={v['accuracy']:.4f} Precision={v['precision']:.4f} Recall={v['recall']:.4f} F1={v['f1']:.4f}")
        print(f"Best K = {best['k']}")
        return {'best_k': best['k'], 'results': results}


class WeightedDistance:
    def __init__(self, weights: Optional[List[float]] = None):
        # weights list corresponds to feature indices
        self.weights = weights

    @classmethod
    def from_json(cls, path: str, feature_names: Optional[List[str]] = None) -> 'WeightedDistance':
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and feature_names is not None:
            # map feature names to vector indices
            w = [1.0] * len(feature_names)
            name_to_idx = {n: i for i, n in enumerate(feature_names)}
            for key, val in data.items():
                if key in name_to_idx:
                    w[name_to_idx[key]] = float(val)
            return cls(w)
        elif isinstance(data, list):
            return cls([float(x) for x in data])
        else:
            # fallback: uniform weights
            return cls(None)

    def calculate(self, a: List[float], b: List[float]) -> float:
        if len(a) != len(b):
            raise ValueError('Dimension mismatch in distance')
        if self.weights is None:
            # default Euclidean
            s = 0.0
            for x, y in zip(a, b):
                d = x - y
                s += d * d
            return math.sqrt(s)
        else:
            if len(self.weights) != len(a):
                raise ValueError('Weights length mismatch')
            s = 0.0
            for w, x, y in zip(self.weights, a, b):
                d = x - y
                s += float(w) * (d * d)
            return math.sqrt(s)


class KNNClassifier:
    def __init__(self, k: int = 3, distance: Optional[WeightedDistance] = None):
        self.k = max(1, int(k))
        self.distance = distance or WeightedDistance()
        self.X: List[List[float]] = []
        self.y: List[str] = []

    def fit(self, X: List[List[float]], y: List[str]) -> None:
        if not X:
            raise ValueError('Empty training set')
        self.X = [list(map(float, xi)) for xi in X]
        self.y = list(y)

    def _vote(self, neighbors: List[Tuple[float, str]]) -> str:
        # neighbors: list of (distance, label) sorted by distance asc
        counts: Dict[str, int] = {}
        for _, lab in neighbors:
            counts[lab] = counts.get(lab, 0) + 1
        # deterministic tie-break: highest count then smallest label string
        best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        return best

    def predict(self, X: List[List[float]]) -> List[str]:
        preds = []
        for xi in X:
            dists = []
            for xt, yt in zip(self.X, self.y):
                d = self.distance.calculate(xi, xt)
                dists.append((d, yt))
            dists.sort(key=lambda p: p[0])
            neighbors = dists[:self.k]
            preds.append(self._vote(neighbors))
        return preds

    def predict_proba(self, X: List[List[float]]) -> List[Dict[str, float]]:
        probs = []
        for xi in X:
            dists = []
            for xt, yt in zip(self.X, self.y):
                d = self.distance.calculate(xi, xt)
                dists.append((d, yt))
            dists.sort(key=lambda p: p[0])
            neighbors = dists[:self.k]
            counts: Dict[str, int] = {}
            for _, lab in neighbors:
                counts[lab] = counts.get(lab, 0) + 1
            total = sum(counts.values())
            probs.append({lab: cnt/total for lab, cnt in counts.items()})
        return probs
