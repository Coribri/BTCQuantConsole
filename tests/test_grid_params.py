from dataclasses import replace

import pytest

from btcquant.config import load_config
from btcquant.grid_paper import GridPaperService
from btcquant.live_risk import LiveRisk


def cfg_at(tmp_path):
    base = load_config("config.toml")
    return replace(base, root=tmp_path)


def test_grid_geometric_levels_are_monotonic(tmp_path):
    service = GridPaperService(cfg_at(tmp_path))
    lines = service._levels(100.0, 200.0, 6, "geometric")
    assert len(lines) == 7
    assert lines[0] == pytest.approx(100.0)
    assert lines[-1] == pytest.approx(200.0)
    assert all(a < b for a, b in zip(lines, lines[1:]))
    ratios = [b / a for a, b in zip(lines, lines[1:])]
    assert max(ratios) - min(ratios) < 1e-9


def test_grid_stop_loss_stops_and_clears_inventory(tmp_path):
    service = GridPaperService(cfg_at(tmp_path))
    prices = {"value": 76_000.0}
    service.price = lambda: prices["value"]
    service.start_grid({
        "lower": 76_000.0,
        "upper": 80_000.0,
        "grids": 6,
        "investment": 1_000.0,
        "mode": "arithmetic",
        "initial_ratio": 0.5,
        "stop_loss_pct": 0.0001,  # 0.01%
    })
    assert service.status()["running"] is True
    assert service.status()["account"]["inventory"] > 0

    prices["value"] = 75_990.0
    service.poll()
    status = service.status()
    assert status["running"] is False
    assert any("stop loss" in event["message"] for event in status["events"])
    assert status["account"]["inventory"] == pytest.approx(0.0)


def test_live_risk_grid_budget_persists(tmp_path):
    risk = LiveRisk(tmp_path)
    assert risk.public()["max_grid_budget"] == pytest.approx(50.0)
    risk.check_grid(50.0)
    with pytest.raises(ValueError):
        risk.check_grid(50.01)
    risk.save(max_grid_budget=30, test_mode=True)
    assert LiveRisk(tmp_path).load()["max_grid_budget"] == pytest.approx(30.0)
