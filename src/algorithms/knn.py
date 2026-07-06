from typing import List, Any
import math
from importlib import util

# optional numpy acceleration
_HAS_NUMPY = util.find_spec('numpy') is not None
if _HAS_NUMPY:
    import numpy as _np


def _validate_vector(v: List[float]) -> None:
    for x in v:
        if not math.isfinite(x):
            raise ValueError('Vector contains non-finite value')


def cosine(a: List[float], b: List[float]) -> float:
    # explicit dimension check
    if len(a) != len(b):
        raise ValueError('Dimension mismatch between vectors')
    _validate_vector(a)
    _validate_vector(b)
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        # if one vector is zero-vector, define cosine as 0.0
        return 0.0
    return dot / (na * nb)

class KNNAlgorithm:
    def __init__(self, k: int = 3, metric: str = 'cosine', weighting: str = 'uniform', confidence_threshold: float = 0.0):
        if k < 1:
            raise ValueError('k must be >= 1')
        self.k = int(k)
        self.metric = metric
        self.weighting = weighting
        self.confidence_threshold = float(confidence_threshold)
        self.X = []
        self.y = []
        self._dim = None
        self._npX = None
        self._np_norms = None

    def fit(self, X: List[List[float]], y: List[Any]) -> None:
        if len(X) != len(y):
            raise ValueError('X and y must have the same length')
        # check vectors
        if X:
            dim = len(X[0])
            for v in X:
                if len(v) != dim:
                    raise ValueError('All feature vectors must have the same length')
                _validate_vector(v)
            self._dim = dim
        else:
            self._dim = None
        self.X = list(X)
        self.y = list(y)
        # numpy cache
        if _HAS_NUMPY and self.X:
            try:
                self._npX = _np.array(self.X, dtype=float)
                self._np_norms = _np.linalg.norm(self._npX, axis=1)
            except Exception:
                self._npX = None
                self._np_norms = None
        else:
            self._npX = None
            self._np_norms = None

    def _distance(self, a,b):
        if self.metric == 'cosine':
            return 1.0 - cosine(a, b)
        raise ValueError('Unknown metric')

    def predict(self, Xq: List[List[float]]) -> List[Any]:
        preds = []
        if not self.X:
            raise ValueError('Model has no training data. Call fit() with non-empty data first.')

        # numpy accelerated batch path
        if _HAS_NUMPY and self._npX is not None:
            q_arr = _np.array(Xq, dtype=float)
            if self._dim is not None and q_arr.ndim == 2 and q_arr.shape[1] != self._dim:
                raise ValueError('Query vector dimension does not match training data')
            # ensure 2D
            if q_arr.ndim == 1:
                q_arr = q_arr.reshape(1, -1)
            q_norms = _np.linalg.norm(q_arr, axis=1)
            q_norms_safe = _np.where(q_norms == 0.0, 1.0, q_norms)
            denom = q_norms_safe[:, None] * _np.where(self._np_norms == 0.0, 1.0, self._np_norms)[None, :]
            dots = _np.dot(q_arr, self._npX.T)
            sims = dots / denom
            dists_mat = 1.0 - sims
            for i in range(dists_mat.shape[0]):
                dists = dists_mat[i]
                idx_sorted = _np.argsort(dists)
                k_eff = min(self.k, len(idx_sorted))
                k_idx = idx_sorted[:k_eff]
                votes = {}
                for j in k_idx:
                    label = self.y[int(j)]
                    dist = float(dists[int(j)])
                    w = 1.0 if self.weighting == 'uniform' else 1.0/(dist+1e-6)
                    votes[label] = votes.get(label, 0.0) + w
                total = sum(votes.values()) or 1.0
                top_label, top_weight = max(votes.items(), key=lambda t: (t[1], str(t[0])))
                confidence = top_weight / total
                preds.append('기타' if confidence < self.confidence_threshold else top_label)
            return preds

        # fallback pure-python per-sample path
        for q in Xq:
            if self._dim is not None and len(q) != self._dim:
                raise ValueError('Query vector dimension does not match training data')
            dists = [(self._distance(q, x), label) for x, label in zip(self.X, self.y)]
            dists.sort(key=lambda t: t[0])
            k_eff = min(self.k, len(dists))
            k_neigh = dists[:k_eff]
            votes = {}
            for dist, label in k_neigh:
                w = 1.0 if self.weighting == 'uniform' else 1.0/(dist + 1e-6)
                votes[label] = votes.get(label, 0.0) + w
            total = sum(votes.values()) or 1.0
            top_label, top_weight = max(votes.items(), key=lambda t: (t[1], str(t[0])))
            confidence = top_weight / total
            preds.append('기타' if confidence < self.confidence_threshold else top_label)
        return preds

    def predict_proba(self, Xq: List[List[float]]) -> List[List[float]]:
        out = []
        # classes fixed order for all outputs
        classes = sorted(set(self.y), key=lambda x: str(x))
        for q in Xq:
            if not self.X:
                raise ValueError('Model has no training data. Call fit() with non-empty data first.')
            if self._dim is not None and len(q) != self._dim:
                raise ValueError('Query vector dimension does not match training data')
            dists = [(self._distance(q,x), label) for x,label in zip(self.X,self.y)]
            dists.sort(key=lambda t: t[0])
            k_eff = min(self.k, len(dists))
            k_neigh = dists[:k_eff]
            votes = {c: 0.0 for c in classes}
            for dist,label in k_neigh:
                w = 1.0 if self.weighting == 'uniform' else 1.0/(dist+1e-6)
                votes[label] = votes.get(label, 0.0) + w
            total = sum(votes.values()) or 1.0
            out.append([votes.get(c,0.0)/total for c in classes])
        return out
