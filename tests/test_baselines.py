"""Unit tests for baseline engine (no HA / LLM required)."""

from datetime import datetime, timedelta, timezone

from hass_agents.analytics.baselines import (
    build_baseline,
    build_heuristic_report,
    classify_anomaly,
    period_window,
    weather_from_temp_series,
)
from hass_agents.schemas import (
    AnomalyFinding,
    HouseConsumptionContext,
    ReportPeriod,
    ReportTotals,
    SeriesPoint,
    Severity,
    WeatherContext,
    PresenceContext,
)


def _pts(values: list[float], start: datetime) -> list[SeriesPoint]:
    return [
        SeriesPoint(ts=start + timedelta(days=i), value=v) for i, v in enumerate(values)
    ]


def test_period_window_daily():
    now = datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc)
    start, end = period_window(ReportPeriod.DAILY, now)
    assert (end - start).days == 1


def test_build_baseline_detects_spike():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    # ~10 kWh/day history, then spike day
    hist = [10.0] * 20 + [10.0, 10.0, 25.0]
    points = _pts(hist, start)
    period_end = points[-1].ts + timedelta(days=1)
    period_start = points[-1].ts
    bl = build_baseline(
        entity_id="sensor.energy_daily_2",
        label="Conso maison",
        points=points,
        period_start=period_start,
        period_end=period_end,
    )
    assert bl.observed == 25.0
    assert bl.mean is not None
    assert bl.mean < 12
    assert bl.zscore is not None and bl.zscore > 2

    finding = classify_anomaly(
        bl,
        window="daily",
        z_warn=2.0,
        z_crit=3.0,
        delta_warn=25.0,
        delta_crit=50.0,
    )
    assert finding is not None
    assert finding.severity in (Severity.WARN, Severity.CRITICAL)


def test_weather_degree_days():
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    points = _pts([5.0, 6.0, 4.0], start)
    wx = weather_from_temp_series(
        points,
        start,
        start + timedelta(days=3),
        base_temp=18.0,
        weather_state="cloudy",
    )
    assert wx.outdoor_temp_mean is not None
    assert wx.degree_days is not None
    assert wx.degree_days > 0


def test_heuristic_report():
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    ctx = HouseConsumptionContext(
        period=ReportPeriod.DAILY,
        period_start=now - timedelta(days=1),
        period_end=now,
        generated_at=now,
        candidate_anomalies=[
            AnomalyFinding(
                severity=Severity.WARN,
                metric="Conso maison",
                window="daily",
                observed=20.0,
                expected=12.0,
                hypothesis="Conso élevée",
            )
        ],
        totals=ReportTotals(energy_kwh=20.0, cost_eur=3.5),
        weather=WeatherContext(outdoor_temp_mean=10.0),
        presence=PresenceContext(empty_house_ratio=0.5, occupants_avg=0.5),
    )
    report = build_heuristic_report(ctx)
    assert report.llm_used is False
    assert report.severity == Severity.WARN
    assert "20" in report.headline or "kWh" in report.headline
    assert report.narrative_md
