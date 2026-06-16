from services.core.inventory import stock_intelligence as S


def test_turnover_rate():
    assert S.turnover_rate(1.0, 365) == 1.0      # 1/day, 365 on hand → 1 turn/yr
    assert S.turnover_rate(2.0, 73) == 10.0
    assert S.turnover_rate(5.0, 0) == 0.0         # no stock → 0, no div-by-zero
    assert S.turnover_rate(None, 100) == 0.0


def test_movement_class():
    assert S.movement_class(avg_daily_demand=5, on_hand=50, last_dispensed_days=2) == "fast"   # turns ~36
    assert S.movement_class(avg_daily_demand=0.1, on_hand=100, last_dispensed_days=10) == "slow"  # turns ~0.4
    assert S.movement_class(avg_daily_demand=1, on_hand=120, last_dispensed_days=200) == "dead"  # stale
    assert S.movement_class(avg_daily_demand=1, on_hand=120, last_dispensed_days=None) == "dead"  # never dispensed
    assert S.movement_class(avg_daily_demand=0.5, on_hand=50, last_dispensed_days=10) == "normal"
    assert S.movement_class(avg_daily_demand=5, on_hand=0, last_dispensed_days=1) == "out"


def test_health_score_penalizes_dead_capital():
    items = [
        {"ndc11": "1", "on_hand": 100, "unit_cost": 1.0, "avg_daily_demand": 5, "last_dispensed_days": 1},   # fast, $100
        {"ndc11": "2", "on_hand": 100, "unit_cost": 1.0, "avg_daily_demand": 0,  "last_dispensed_days": 300}, # dead, $100
    ]
    classified = S.classify_items(items)
    score = S.health_score(classified)
    assert 0 <= score <= 100
    assert score == 50  # dead value is half of total → 100*(1-0.5)


def test_summarize_shape_and_dead_value():
    items = [
        {"ndc11": "A", "on_hand": 10, "unit_cost": 2.0, "avg_daily_demand": 3, "last_dispensed_days": 1},
        {"ndc11": "B", "on_hand": 20, "unit_cost": 5.0, "avg_daily_demand": 0, "last_dispensed_days": 400},
    ]
    s = S.summarize(items)
    assert s["dead_stock_value"] == 100.0          # 20 * 5.0
    assert s["total_value"] == 120.0
    assert s["by_class"]["dead"]["count"] == 1
    assert s["health_score"] < 100
    assert s["dead_stock"][0]["ndc11"] == "B"
