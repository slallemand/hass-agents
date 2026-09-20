"""Deterministic baseline / anomaly engine (no LLM)."""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from hass_agents.schemas import (
    AnomalyFinding,
    BaselineStats,
    DeviceTotal,
    HouseConsumptionContext,
    PersonPresence,
    PresenceContext,
    ReportPeriod,
    ReportTotals,
    SeriesPoint,
    Severity,
    WeatherContext,
)


def _safe_mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _safe_median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _safe_stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return statistics.pstdev(values)


def _zscore(observed: float | None, mean: float | None, stdev: float | None) -> float | None:
    if observed is None or mean is None:
        return None
    if stdev is None:
        return None
    if stdev == 0:
        if observed == mean:
            return 0.0
        # Flat baseline then sudden change — treat as large deviation
        return 5.0 if observed > mean else -5.0
    return (observed - mean) / stdev


def _delta_pct(observed: float | None, baseline: float | None) -> float | None:
    if observed is None or baseline is None or baseline == 0:
        return None
    return ((observed - baseline) / abs(baseline)) * 100.0


def period_window(period: ReportPeriod, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    end = now
    if period == ReportPeriod.DAILY:
        start = end - timedelta(days=1)
    elif period == ReportPeriod.WEEKLY:
        start = end - timedelta(days=7)
    else:
        start = end - timedelta(days=30)
    return start, end


def extract_daily_changes(stats_rows: list[dict[str, Any]]) -> list[SeriesPoint]:
    points: list[SeriesPoint] = []
    for row in stats_rows:
        raw_start = row.get("start")
        value = row.get("change")
        if value is None:
            value = row.get("mean")
        if value is None or raw_start is None:
            continue
        if isinstance(raw_start, (int, float)):
            # HA sometimes returns ms epoch
            ts = datetime.fromtimestamp(raw_start / 1000.0, tz=timezone.utc)
        else:
            ts = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
        try:
            points.append(SeriesPoint(ts=ts, value=float(value)))
        except (TypeError, ValueError):
            continue
    points.sort(key=lambda p: p.ts)
    return points


def extract_mean_series(stats_rows: list[dict[str, Any]]) -> list[SeriesPoint]:
    points: list[SeriesPoint] = []
    for row in stats_rows:
        raw_start = row.get("start")
        value = row.get("mean")
        if value is None or raw_start is None:
            continue
        if isinstance(raw_start, (int, float)):
            ts = datetime.fromtimestamp(raw_start / 1000.0, tz=timezone.utc)
        else:
            ts = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
        try:
            points.append(SeriesPoint(ts=ts, value=float(value)))
        except (TypeError, ValueError):
            continue
    points.sort(key=lambda p: p.ts)
    return points


def sum_points_in_window(points: list[SeriesPoint], start: datetime, end: datetime) -> float | None:
    vals = [p.value for p in points if start <= p.ts < end]
    if not vals:
        return None
    return sum(vals)


def mean_points_in_window(points: list[SeriesPoint], start: datetime, end: datetime) -> float | None:
    vals = [p.value for p in points if start <= p.ts < end]
    return _safe_mean(vals)


def build_baseline(
    *,
    entity_id: str,
    label: str,
    points: list[SeriesPoint],
    period_start: datetime,
    period_end: datetime,
    unit: str | None = "kWh",
    aggregate: str = "sum",
) -> BaselineStats:
    if aggregate == "sum":
        observed = sum_points_in_window(points, period_start, period_end)
    else:
        observed = mean_points_in_window(points, period_start, period_end)

    # Historical daily values outside the analyzed window (for baseline)
    hist = [p.value for p in points if p.ts < period_start]
    mean = _safe_mean(hist)
    median = _safe_median(hist)
    stdev = _safe_stdev(hist)

    # Same weekday means from history (for daily reports)
    weekday = period_start.weekday()
    same_wd = [p.value for p in points if p.ts < period_start and p.ts.weekday() == weekday]
    same_weekday_mean = _safe_mean(same_wd)

    # Baseline = mean of history scaled to period length for multi-day windows
    days = max((period_end - period_start).total_seconds() / 86400.0, 1.0)
    if aggregate == "sum" and mean is not None and days > 1.1:
        baseline_mean = mean * days
    else:
        baseline_mean = same_weekday_mean if same_weekday_mean is not None else mean

    z = _zscore(observed, baseline_mean if days <= 1.1 else (mean * days if mean else None), 
                stdev if days <= 1.1 else (stdev * math.sqrt(days) if stdev else None))
    # Simpler z for multi-day: compare observed/days vs daily mean
    if days > 1.1 and observed is not None and mean is not None:
        daily_obs = observed / days
        if stdev and stdev > 0:
            z = (daily_obs - mean) / stdev
        else:
            z = _zscore(daily_obs, mean, 0.0)

    return BaselineStats(
        entity_id=entity_id,
        label=label,
        unit=unit,
        observed=observed,
        mean=mean,
        median=median,
        stdev=stdev,
        baseline_mean=baseline_mean,
        same_weekday_mean=same_weekday_mean,
        zscore=z,
        delta_pct=_delta_pct(observed, baseline_mean),
        points=points[-40:],
    )


def classify_anomaly(
    baseline: BaselineStats,
    *,
    window: str,
    z_warn: float,
    z_crit: float,
    delta_warn: float,
    delta_crit: float,
    evidence: dict[str, Any] | None = None,
) -> AnomalyFinding | None:
    if baseline.observed is None:
        return None
    z = baseline.zscore
    d = baseline.delta_pct
    severity: Severity | None = None
    if (z is not None and abs(z) >= z_crit) or (d is not None and abs(d) >= delta_crit):
        severity = Severity.CRITICAL
    elif (z is not None and abs(z) >= z_warn) or (d is not None and abs(d) >= delta_warn):
        severity = Severity.WARN
    if severity is None:
        return None

    direction = "élevée" if (d or 0) > 0 else "basse"
    hypothesis = (
        f"{baseline.label}: consommation {direction} "
        f"(observé={baseline.observed:.2f}, attendu≈{baseline.baseline_mean or 0:.2f}"
        f"{f', z={z:.2f}' if z is not None else ''}"
        f"{f', Δ={d:.0f}%' if d is not None else ''})."
    )
    return AnomalyFinding(
        severity=severity,
        metric=baseline.label,
        entity_id=baseline.entity_id,
        window=window,
        observed=baseline.observed,
        expected=baseline.baseline_mean,
        zscore=z,
        delta_pct=d,
        confidence=0.85 if severity == Severity.WARN else 0.9,
        evidence=evidence or {},
        hypothesis=hypothesis,
    )


def weather_from_temp_series(
    points: list[SeriesPoint],
    period_start: datetime,
    period_end: datetime,
    *,
    base_temp: float,
    weather_state: str | None,
) -> WeatherContext:
    vals = [p.value for p in points if period_start <= p.ts < period_end]
    if not vals and points:
        # fallback: last available means overlapping lookback
        vals = [p.value for p in points if p.ts >= period_start - timedelta(days=1)]
    mean = _safe_mean(vals)
    degree_days = None
    if mean is not None:
        days = max((period_end - period_start).total_seconds() / 86400.0, 1.0)
        degree_days = max(0.0, base_temp - mean) * days
    return WeatherContext(
        outdoor_temp_mean=mean,
        outdoor_temp_min=min(vals) if vals else None,
        outdoor_temp_max=max(vals) if vals else None,
        degree_days=degree_days,
        weather_state=weather_state,
    )


def presence_from_history(
    person_histories: dict[str, list[dict[str, Any]]],
    *,
    period_start: datetime,
    period_end: datetime,
    occupants_now: float | None,
) -> PresenceContext:
    duration = max((period_end - period_start).total_seconds(), 1.0)
    by_person: list[PersonPresence] = []

    for entity_id, events in person_histories.items():
        if not events:
            continue
        name = entity_id.replace("person.", "").replace("_", " ").title()
        home_seconds = _seconds_in_state(events, period_start, period_end, target="home")
        ratio = home_seconds / duration
        by_person.append(
            PersonPresence(
                entity_id=entity_id,
                name=name,
                home_ratio=ratio,
                hours_home=home_seconds / 3600.0,
            )
        )

    # Approximate empty ratio: fraction of time when NO tracked person is home
    # Use max of individual home ratios as crude occupancy proxy if timeline merge is heavy
    if by_person:
        # Merge: empty when all are away — approximate as 1 - max(home_ratio) is weak;
        # better: use average occupancy
        occupants_avg = sum(p.home_ratio for p in by_person)
        empty_ratio = max(0.0, 1.0 - min(1.0, occupants_avg))
        hours_someone = duration * (1.0 - empty_ratio) / 3600.0
        hours_empty = duration * empty_ratio / 3600.0
    else:
        occupants_avg = None
        empty_ratio = None
        hours_someone = None
        hours_empty = None

    return PresenceContext(
        occupants_now=occupants_now,
        occupants_avg=occupants_avg,
        hours_someone_home=hours_someone,
        hours_empty=hours_empty,
        empty_house_ratio=empty_ratio,
        by_person=by_person,
    )


def _seconds_in_state(
    events: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    target: str,
) -> float:
    if not events:
        return 0.0
    parsed: list[tuple[datetime, str]] = []
    for ev in events:
        state = str(ev.get("state", ""))
        lc = ev.get("last_changed") or ev.get("last_updated")
        if not lc:
            continue
        ts = datetime.fromisoformat(str(lc).replace("Z", "+00:00"))
        parsed.append((ts, state))
    parsed.sort(key=lambda x: x[0])
    if not parsed:
        return 0.0

    # Walk interval
    total = 0.0
    # Find state at start
    current_state = parsed[0][1]
    for ts, state in parsed:
        if ts <= start:
            current_state = state
        else:
            break
    cursor = start
    for ts, state in parsed:
        if ts <= start:
            current_state = state
            continue
        if ts >= end:
            break
        if current_state == target:
            total += (ts - cursor).total_seconds()
        cursor = ts
        current_state = state
    if current_state == target:
        total += (end - cursor).total_seconds()
    return max(total, 0.0)


def enrich_anomaly_with_context(
    finding: AnomalyFinding,
    weather: WeatherContext,
    presence: PresenceContext,
) -> AnomalyFinding:
    bits: list[str] = []
    if weather.outdoor_temp_mean is not None:
        bits.append(f"T°ext moy={weather.outdoor_temp_mean:.1f}°C")
    if weather.degree_days is not None:
        bits.append(f"degree-days={weather.degree_days:.1f}")
    if presence.empty_house_ratio is not None:
        bits.append(f"maison vide≈{presence.empty_house_ratio * 100:.0f}%")
    if presence.occupants_avg is not None:
        bits.append(f"occupants≈{presence.occupants_avg:.1f}")
    finding.evidence = {**finding.evidence, "context": ", ".join(bits)}
    if bits:
        finding.hypothesis = f"{finding.hypothesis} Contexte: {', '.join(bits)}."
    return finding


def build_heuristic_report(ctx: HouseConsumptionContext) -> "ConsumptionReport":
    from hass_agents.schemas import ConsumptionReport

    findings = list(ctx.candidate_anomalies)
    sev = Severity.INFO
    if any(f.severity == Severity.CRITICAL for f in findings):
        sev = Severity.CRITICAL
    elif any(f.severity == Severity.WARN for f in findings):
        sev = Severity.WARN

    energy = ctx.totals.energy_kwh
    cost = ctx.totals.cost_eur
    headline_bits = [f"Rapport {ctx.period.value}"]
    if energy is not None:
        headline_bits.append(f"{energy:.1f} kWh")
    if cost is not None:
        headline_bits.append(f"{cost:.2f} €")
    headline = " — ".join(headline_bits)

    if findings:
        summary = f"{len(findings)} écart(s) détecté(s). Principal: {findings[0].hypothesis}"
    else:
        summary = "Aucun écart significatif par rapport aux baselines."

    checks: list[str] = []
    for f in findings[:5]:
        checks.append(f"Vérifier {f.metric} ({f.entity_id or 'n/a'})")
    if presence_empty_high(ctx.presence) and energy_high(findings):
        checks.append("Maison souvent vide alors que conso élevée — vérifier appareils laissés allumés")

    narrative = [
        f"# Rapport consommation ({ctx.period.value})",
        "",
        f"**Période:** {ctx.period_start.date()} → {ctx.period_end.date()}",
        f"**Totaux:** {energy if energy is not None else '?'} kWh"
        f" / {cost if cost is not None else '?'} €",
        "",
        "## Contexte",
        f"- Météo: T°ext moy={ctx.weather.outdoor_temp_mean}, état={ctx.weather.weather_state}",
        f"- Présence: occupants≈{ctx.presence.occupants_avg},"
        f" vide≈{(ctx.presence.empty_house_ratio or 0) * 100:.0f}%",
        "",
        "## Écarts",
    ]
    if findings:
        for f in findings:
            narrative.append(f"- **{f.severity.value}** — {f.hypothesis}")
    else:
        narrative.append("- RAS")

    return ConsumptionReport(
        period=ctx.period,
        period_start=ctx.period_start,
        period_end=ctx.period_end,
        generated_at=ctx.generated_at,
        headline=headline,
        summary=summary,
        totals=ctx.totals,
        top_devices=sorted(
            [d for d in ctx.device_totals if d.kwh is not None],
            key=lambda d: d.kwh or 0,
            reverse=True,
        )[:5],
        findings=findings,
        suggested_checks=checks,
        narrative_md="\n".join(narrative),
        severity=sev,
        llm_used=False,
        weather=ctx.weather,
        presence=ctx.presence,
    )


def presence_empty_high(presence: PresenceContext, threshold: float = 0.4) -> bool:
    return presence.empty_house_ratio is not None and presence.empty_house_ratio >= threshold


def energy_high(findings: list[AnomalyFinding]) -> bool:
    return any(
        f.severity in (Severity.WARN, Severity.CRITICAL) and (f.delta_pct or 0) > 0
        for f in findings
    )
