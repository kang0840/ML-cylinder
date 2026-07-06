from typing import List, Any, Dict
import math
from collections import defaultdict

class MultinomialNB:
    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self.class_count = defaultdict(int)
        self.feature_count = {}  # class -> list counts
        self.class_log_prior = {}
        self.feature_log_prob = {}
        self.classes = []

    def fit(self, X: List[List[float]], y: List[Any]) -> None:
        self.classes = sorted(set(y))
        n_features = len(X[0]) if X else 0
        self.feature_count = {c: [0.0]*n_features for c in self.classes}
        for xi, yi in zip(X,y):
            self.class_count[yi] += 1
            for i,v in enumerate(xi):
                self.feature_count[yi][i] += v
        total_count = sum(self.class_count.values())
        self.class_log_prior = {c: math.log(self.class_count[c]/total_count) for c in self.classes}
        self.feature_log_prob = {}
        for c in self.classes:
            sm = sum(self.feature_count[c]) + self.alpha * n_features
            self.feature_log_prob[c] = [math.log((self.feature_count[c][i] + self.alpha)/sm) for i in range(n_features)]

    def _joint_log_likelihood(self, x):
        res = {}
        for c in self.classes:
            s = self.class_log_prior[c]
            flp = self.feature_log_prob[c]
            s += sum(xi * flp_i for xi, flp_i in zip(x, flp))
            res[c] = s
        return res

    def predict(self, X: List[List[float]]) -> List[Any]:
        preds = []
        for x in X:
            j = self._joint_log_likelihood(x)
            preds.append(max(j.items(), key=lambda t:t[1])[0])
        return preds

    def predict_proba(self, X: List[List[float]]):
        out = []
        for x in X:
            j = self._joint_log_likelihood(x)
            maxv = max(j.values())
            exps = {c: math.exp(v-maxv) for c,v in j.items()}
            s = sum(exps.values()) or 1.0
            out.append([exps[c]/s for c in self.classes])
        return out
