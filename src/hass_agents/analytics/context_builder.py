"""Build HouseConsumptionContext from HA + entity mapping."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from hass_agents.analytics.baselines import (
    build_baseline,
    classify_anomaly,
    enrich_anomaly_with_context,
    extract_daily_changes,
    extract_mean_series,
    period_window,
    presence_from_history,
    weather_from_temp_series,
)
from hass_agents.config import Settings
from hass_agents.ha import HAClient
from hass_agents.schemas import (
    AnomalyFinding,
    DeviceTotal,
    EntitiesConfig,
    HouseConsumptionContext,
    ReportPeriod,
    ReportTotals,
    WaterContext,
)

logger = logging.getLogger(__name__)


def load_entities_config(path: Path) -> EntitiesConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return EntitiesConfig.model_validate(data)


def build_context(
    settings: Settings,
    period: ReportPeriod,
    *,
    client: HAClient | None = None,
    now: datetime | None = None,
) -> HouseConsumptionContext:
    now = now or datetime.now(timezone.utc)
    period_start, period_end = period_window(period, now)
    entities = load_entities_config(settings.entities_config)
    ha = client or HAClient(settings.ha_url, settings.ha_token)

    lookback_start = now - HAClient.lookback_for_period(period.value)

    # Collect statistic ids
    grid = entities.grid
    energy_daily = grid.get("energy_daily")
    energy_hc = grid.get("energy_daily_hc")
    energy_hp = grid.get("energy_daily_hp")
    cost_daily = grid.get("cost_daily")
    outdoor = entities.context.get("outdoor_temp")
    weather_entity = entities.context.get("weather")
    zone_home = entities.context.get("zone_home")
    household: list[str] = list(entities.context.get("household") or [])
    water_total = entities.context.get("water_total")
    water_daily = entities.context.get("water_daily")
    water_flow = entities.context.get("water_flow")

    device_ids = [d.energy for d in entities.devices]
    stat_ids = [
        eid
        for eid in [
            energy_daily,
            energy_hc,
            energy_hp,
            cost_daily,
            outdoor,
            water_total,
            water_daily,
            *device_ids,
        ]
        if eid
    ]
    solar = entities.solar.get("lifetime")
    if solar:
        stat_ids.append(solar)

    stats = ha.get_statistics(stat_ids, lookback_start, now, period="day") if stat_ids else {}

    baselines = []
    anomalies: list[AnomalyFinding] = []

    def add_metric(entity_id: str | None, label: str, unit: str, aggregate: str = "sum") -> None:
        if not entity_id:
            return
        rows = stats.get(entity_id, [])
        points = extract_mean_series(rows) if aggregate == "mean" else extract_daily_changes(rows)
        if not points:
            logger.warning("No statistics for %s", entity_id)
            return
        bl = build_baseline(
            entity_id=entity_id,
            label=label,
            points=points,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            aggregate=aggregate,
        )
        baselines.append(bl)
        finding = classify_anomaly(
            bl,
            window=period.value,
            z_warn=settings.zscore_warn,
            z_crit=settings.zscore_critical,
            delta_warn=settings.delta_pct_warn,
            delta_crit=settings.delta_pct_critical,
        )
        if finding:
            anomalies.append(finding)

    add_metric(energy_daily, "Conso maison (journalière)", "kWh")
    add_metric(energy_hc, "Conso HC", "kWh")
    add_metric(energy_hp, "Conso HP", "kWh")
    add_metric(cost_daily, "Coût journalier", "EUR")
    if solar:
        add_metric(solar, "Production solaire", "kWh")

    device_totals: list[DeviceTotal] = []
    for device in entities.devices:
        rows = stats.get(device.energy, [])
        points = extract_daily_changes(rows)
        bl = build_baseline(
            entity_id=device.energy,
            label=device.name,
            points=points,
            period_start=period_start,
            period_end=period_end,
            unit="kWh",
        )
        baselines.append(bl)
        device_totals.append(
            DeviceTotal(id=device.id, name=device.name, kwh=bl.observed, delta_pct=bl.delta_pct)
        )
        finding = classify_anomaly(
            bl,
            window=period.value,
            z_warn=settings.zscore_warn,
            z_crit=settings.zscore_critical,
            delta_warn=settings.delta_pct_warn,
            delta_crit=settings.delta_pct_critical,
        )
        if finding:
            anomalies.append(finding)

    # Weather
    weather_state = None
    if weather_entity:
        st = ha.get_state(weather_entity)
        if st:
            weather_state = st.get("state")
    outdoor_points = extract_mean_series(stats.get(outdoor, [])) if outdoor else []
    weather = weather_from_temp_series(
        outdoor_points,
        period_start,
        period_end,
        base_temp=settings.comfort_base_temp,
        weather_state=weather_state,
    )

    # Presence
    occupants_now = None
    if zone_home:
        zs = ha.get_state(zone_home)
        if zs:
            occupants_now = HAClient.parse_float(zs.get("state"))

    person_histories: dict[str, list[dict[str, Any]]] = {}
    if household:
        person_histories = ha.get_history(
            household,
            period_start,
            period_end,
            minimal=False,
            significant_changes_only=False,
        )
    presence = presence_from_history(
        person_histories,
        period_start=period_start,
        period_end=period_end,
        occupants_now=occupants_now,
    )

    # Water usage (explains water-heater electricity)
    water = WaterContext()
    water_stat_id = water_total or water_daily
    if water_stat_id:
        rows = stats.get(water_stat_id, [])
        points = extract_daily_changes(rows)
        if points:
            bl = build_baseline(
                entity_id=water_stat_id,
                label="Consommation d'eau",
                points=points,
                period_start=period_start,
                period_end=period_end,
                unit="L",
            )
            baselines.append(bl)
            water.volume_l = bl.observed
            if bl.observed is not None:
                water.volume_m3 = bl.observed / 1000.0
            water.baseline_mean_l = bl.baseline_mean
            water.delta_pct = bl.delta_pct
        elif water_daily:
            st = ha.get_state(water_daily)
            if st:
                vol = HAClient.parse_float(st.get("state"))
                water.volume_l = vol
                if vol is not None:
                    water.volume_m3 = vol / 1000.0
    if water_flow:
        st = ha.get_state(water_flow)
        if st:
            water.flow_l_per_min = HAClient.parse_float(st.get("state"))

    anomalies = [
        enrich_anomaly_with_context(a, weather, presence, water) for a in anomalies
    ]
    anomalies.sort(key=lambda a: {"critical": 0, "warn": 1, "info": 2}[a.severity.value])

    def _obs(label_substr: str) -> float | None:
        for b in baselines:
            if label_substr.lower() in b.label.lower():
                return b.observed
        return None

    totals = ReportTotals(
        energy_kwh=_obs("maison") or _obs("journalière"),
        energy_hc_kwh=_obs("hc"),
        energy_hp_kwh=_obs("hp"),
        cost_eur=_obs("coût") or _obs("cout"),
        solar_kwh=_obs("solaire"),
    )
    # Prefer explicit entity baselines
    for b in baselines:
        if b.entity_id == energy_daily:
            totals.energy_kwh = b.observed
        elif b.entity_id == energy_hc:
            totals.energy_hc_kwh = b.observed
        elif b.entity_id == energy_hp:
            totals.energy_hp_kwh = b.observed
        elif b.entity_id == cost_daily:
            totals.cost_eur = b.observed
        elif solar and b.entity_id == solar:
            totals.solar_kwh = b.observed

    notes: list[str] = []
    if not energy_daily:
        notes.append("Aucune entité energy_daily configurée")
    if not outdoor_points:
        notes.append("Pas de stats T° ext — météo partielle")
    if not household:
        notes.append("Pas de household configuré — présence partielle")
    if water.volume_l is None:
        notes.append("Pas de stats eau — corrélation chauffe-eau limitée")
    else:
        notes.append(
            "Rappel: la conso électrique du chauffe-eau est fortement liée "
            "au volume d'eau consommé dans la maison."
        )

    return HouseConsumptionContext(
        period=period,
        period_start=period_start,
        period_end=period_end,
        generated_at=now,
        baselines=baselines,
        candidate_anomalies=anomalies,
        weather=weather,
        presence=presence,
        water=water,
        device_totals=device_totals,
        totals=totals,
        notes=notes,
    )
