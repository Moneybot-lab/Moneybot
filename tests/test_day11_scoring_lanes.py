from scripts.day11_compare_candidate_vs_production import _ranking_lane_decide


def _metrics(best):
    return {"best_ranking_backtest": best}


def test_ranking_lane_requires_top_k_return_objective_drawdown_and_big_loss_rate():
    candidate = _metrics(
        {
            "top_k": 3,
            "total_return": 0.08,
            "objective_score": 0.03,
            "max_drawdown": 0.05,
            "big_loss_selection_rate": 0.2,
        }
    )
    production = _metrics(
        {
            "top_k": 3,
            "total_return": 0.10,
            "objective_score": 0.04,
            "max_drawdown": 0.04,
            "big_loss_selection_rate": 0.0,
        }
    )

    ranking_win, reasons, lane_metrics = _ranking_lane_decide(candidate, production)

    assert ranking_win is False
    assert lane_metrics["candidate"] == candidate["best_ranking_backtest"]
    assert "ranking challenger top-k total_return is below production" in reasons
    assert "ranking challenger objective_score does not exceed production" in reasons
    assert "ranking challenger max_drawdown exceeds production" in reasons
    assert "ranking challenger big_loss_selection_rate exceeds production" in reasons


def test_ranking_lane_passes_only_when_ranking_metrics_beat_or_match_production_risk():
    candidate = _metrics(
        {
            "top_k": 3,
            "total_return": 0.12,
            "objective_score": 0.08,
            "max_drawdown": 0.04,
            "big_loss_selection_rate": 0.0,
        }
    )
    production = _metrics(
        {
            "top_k": 3,
            "total_return": 0.10,
            "objective_score": 0.04,
            "max_drawdown": 0.04,
            "big_loss_selection_rate": 0.0,
        }
    )

    ranking_win, reasons, _ = _ranking_lane_decide(candidate, production)

    assert ranking_win is True
    assert "ranking challenger improves objective with acceptable top-k return, drawdown, and big-loss selection rate" in reasons
