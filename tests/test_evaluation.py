from evaluation import score


def test_metrics_account_for_missed_bugs_and_false_positives():
    a = {"file": "app.py", "line": 1, "source": "logic", "rule_id": "LOGIC"}
    b = {**a, "line": 2}
    c = {**a, "line": 3}
    assert score([a, b], [a, c, c]) == {"tp": 1, "fp": 1, "fn": 1, "precision": 0.5, "recall": 0.5}
