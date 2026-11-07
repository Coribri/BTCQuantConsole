from dataclasses import replace

import pytest

from btcquant.config import load_config
from btcquant.futures_paper import FuturesPaperService
from btcquant.grid_paper import GridPaperService


def cfg_at(tmp_path):
    base = load_config("config.toml")
    return replace(base, root=tmp_path)


def test_futures_long_market_and_close(tmp_path):
    service = FuturesPaperService(cfg_at(tmp_path))
    prices = {"value": 100.0}
    service.price = lambda: prices["value"]

    service.place_order(side="LONG", qty=1.0, leverage=10)
    status = service.status()
    assert status["position"]["side"] == "LONG"
    assert status["position"]["leverage"] == 10
    assert status["account"]["margin"] == pytest.approx(10.002, abs=0.01)

    prices["value"] = 110.0
    service.poll()
    status = service.status()
    assert status["position"]["unrealized_pnl"] == pytest.approx(9.98, abs=0.01)
    service.close_position()
    status = service.status()
    assert status["position"] is None
    assert status["account"]["realized_pnl"] == pytest.approx(9.96, abs=0.01)
    assert status["account"]["equity"] > 10_000


def test_futures_short_and_limit_order(tmp_path):
    service = FuturesPaperService(cfg_at(tmp_path))
    prices = {"value": 100.0}
    service.price = lambda: prices["value"]

    service.place_order(side="SHORT", qty=1.0, leverage=5)
    prices["value"] = 90.0
    service.poll()
    assert service.status()["position"]["unrealized_pnl"] == pytest.approx(9.98, abs=0.01)
    service.close_position()
    assert service.status()["account"]["realized_pnl"] == pytest.approx(9.96, abs=0.01)

    service.reset()
    service.place_order(side="LONG", order_type="LIMIT", qty=1.0, leverage=10, limit_price=95)
    assert len(service.status()["pending"]) == 1
    prices["value"] = 96.0
    service.poll()
    assert len(service.status()["pending"]) == 1
    prices["value"] = 94.0
    service.poll()
    assert service.status()["position"] is not None
    assert service.status()["position"]["side"] == "LONG"


def test_futures_take_profit_stop_loss_and_limit_close(tmp_path):
    service = FuturesPaperService(cfg_at(tmp_path))
    prices = {"value": 100.0}
    service.price = lambda: prices["value"]

    service.place_order(side="LONG", qty=1.0, leverage=10, take_profit=110, stop_loss=95)
    prices["value"] = 111.0
    service.poll()
    assert service.status()["position"] is None
    assert service.status()["account"]["realized_pnl"] == pytest.approx(9.98, abs=0.02)

    service.reset()
    prices["value"] = 100.0
    service.place_order(side="SHORT", qty=1.0, leverage=10, stop_loss=105)
    prices["value"] = 106.0
    service.poll()
    assert service.status()["position"] is None
    assert service.status()["account"]["realized_pnl"] < 0

    service.reset()
    prices["value"] = 100.0
    service.place_order(side="LONG", qty=1.0, leverage=10)
    service.place_order(side="LONG", action="CLOSE", order_type="LIMIT", qty=1.0, leverage=10, limit_price=110)
    assert len(service.status()["pending"]) == 1
    prices["value"] = 111.0
    service.poll()
    assert service.status()["position"] is None
    assert service.status()["account"]["realized_pnl"] == pytest.approx(9.98, abs=0.02)


def test_futures_multiple_entries_merge(tmp_path):
    service = FuturesPaperService(cfg_at(tmp_path))
    service.price = lambda: 100.0
    service.place_order(side="LONG", qty=1.0, leverage=10)
    service.place_order(side="LONG", qty=1.0, leverage=10)
    status = service.status()
    assert status["position"]["qty"] == pytest.approx(2.0)
    assert status["orders"][0]["action"] == "ADD"
    service.close_position()
    service.reset()


def test_futures_hedge_long_and_short_together(tmp_path):
    service = FuturesPaperService(cfg_at(tmp_path))
    service.price = lambda: 100.0
    service.place_order(side="LONG", qty=0.5, leverage=10)
    service.place_order(side="SHORT", qty=0.3, leverage=10)
    status = service.status()
    assert len(status["positions"]) == 2
    assert {p["side"] for p in status["positions"]} == {"LONG", "SHORT"}
    service.close_position()
    assert service.status()["positions"] == []
    service.reset()


def test_futures_set_tpsl(tmp_path):
    service = FuturesPaperService(cfg_at(tmp_path))
    service.price = lambda: 100.0
    service.place_order(side="LONG", qty=1.0, leverage=10)
    service.set_tpsl(110, 95)
    status = service.status()
    assert status["position"]["take_profit"] == pytest.approx(110)
    assert status["position"]["stop_loss"] == pytest.approx(95)
    with pytest.raises(ValueError):
        service.set_tpsl(90, 95)
    service.close_position()
    service.reset()


def test_futures_cross_margin(tmp_path):
    service = FuturesPaperService(cfg_at(tmp_path))
    service.price = lambda: 100.0
    service.place_order(side="LONG", qty=1.0, leverage=10, margin_mode="CROSS")
    status = service.status()
    assert status["position"]["margin_mode"] == "CROSS"
    assert status["position"]["margin"] == pytest.approx(10.002, abs=0.01)
    assert status["position"]["liq_price"] < status["position"]["entry_price"]
    service.close_position()
    service.reset()


def test_futures_leverage_validation(tmp_path):
    service = FuturesPaperService(cfg_at(tmp_path))
    service.price = lambda: 100.0
    with pytest.raises(ValueError):
        service.place_order(side="LONG", qty=1.0, leverage=101)
    with pytest.raises(ValueError):
        service.place_order(side="LONG", qty=1.0, leverage=0)


def test_grid_buy_then_sell(tmp_path):
    service = GridPaperService(cfg_at(tmp_path))
    prices = {"value": 100.0}
    service.price = lambda: prices["value"]
    service.start_grid(
        {
            "lower": 90,
            "upper": 110,
            "grids": 4,
            "investment": 1000,
            "mode": "arithmetic",
            "initial_ratio": 0.5,
            "stop_loss_pct": 0.1,
        }
    )
    status = service.status()
    assert status["running"] is True
    assert len(status["levels"]) == 5
    assert status["account"]["inventory"] > 0

    prices["value"] = 94.0
    service.poll()
    status = service.status()
    assert status["account"]["inventory"] > 5

    prices["value"] = 101.0
    service.poll()
    status = service.status()
    assert status["account"]["realized_pnl"] != 0

    service.stop_grid(liquidate=True)
    status = service.status()
    assert status["running"] is False
    assert status["account"]["inventory"] == 0
