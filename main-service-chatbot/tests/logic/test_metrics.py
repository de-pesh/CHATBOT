from app.logic.metrics import metrics_report


def test_rates_are_computed_over_the_right_denominators():
    row = {"turns": 100, "errors": 5, "answered": 60, "no_evidence": 20, "small_talk": 20,
           "feedback_positive": 9, "feedback_negative": 3}
    report = metrics_report(7, row)
    assert report["days"] == 7 and report["turns"] == 100
    assert report["error_rate"] == 0.05
    assert report["no_evidence_rate"] == 0.25       # 20 of the 80 that reached the search, not of all 100
    assert report["negative_feedback_rate"] == 0.25


def test_a_rate_with_nothing_to_divide_by_is_none():
    report = metrics_report(1, {})
    assert report["error_rate"] is None and report["no_evidence_rate"] is None and report["negative_feedback_rate"] is None
