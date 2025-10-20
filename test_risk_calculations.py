import math
from pytest import approx
from risk_detector import calculate_lp_percent, calculate_dump_risk_lp_vs_cluster, calculate_price_impact_cluster_sell, TOTAL_SUPPLY


def test_calculate_lp_percent():
    val = calculate_lp_percent(1000, 1)
    expected = ((1000 / 2) / 1) / TOTAL_SUPPLY * 100
    assert val == approx(expected)
    assert calculate_lp_percent(None, 1) == 0.0
    assert calculate_lp_percent(1000, 0) == 0.0


def test_calculate_dump_risk_lp_vs_cluster():
    assert calculate_dump_risk_lp_vs_cluster(20, 10) == approx(200.0)
    assert calculate_dump_risk_lp_vs_cluster(0, 10) == 0.0
    assert calculate_dump_risk_lp_vs_cluster(10, 0) == math.inf
    assert calculate_dump_risk_lp_vs_cluster(None, 10) == 0.0


def test_calculate_price_impact_cluster_sell():
    assert calculate_price_impact_cluster_sell(100, 100) == approx((1 - (100 / 200) ** 2) * 100)
    assert calculate_price_impact_cluster_sell(0, 10) == 100.0
    assert calculate_price_impact_cluster_sell(None, 100) == 0.0
    assert calculate_price_impact_cluster_sell(100, 0) == 0.0
