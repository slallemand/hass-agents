"""Pydantic contracts for consumption analysis."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReportPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class DeviceConfig(BaseModel):
    id: str
    name: str
    energy: str


class EntitiesConfig(BaseModel):
    grid: dict[str, str]
    solar: dict[str, str] = Field(default_factory=dict)
    devices: list[DeviceConfig] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class SeriesPoint(BaseModel):
    ts: datetime
    value: float


class BaselineStats(BaseModel):
    entity_id: str
    label: str
    unit: str | None = None
    observed: float | None = None
    mean: float | None = None
    median: float | None = None
    stdev: float | None = None
    baseline_mean: float | None = None
    same_weekday_mean: float | None = None
    zscore: float | None = None
    delta_pct: float | None = None
    points: list[SeriesPoint] = Field(default_factory=list)


class WeatherContext(BaseModel):
    outdoor_temp_mean: float | None = None
    outdoor_temp_min: float | None = None
    outdoor_temp_max: float | None = None
    degree_days: float | None = None
    weather_state: str | None = None


class PersonPresence(BaseModel):
    entity_id: str
    name: str
    home_ratio: float
    hours_home: float


class PresenceContext(BaseModel):
    occupants_now: float | None = None
    occupants_avg: float | None = None
    hours_someone_home: float | None = None
    hours_empty: float | None = None
    empty_house_ratio: float | None = None
    by_person: list[PersonPresence] = Field(default_factory=list)


class AnomalyFinding(BaseModel):
    severity: Severity
    metric: str
    entity_id: str | None = None
    window: str
    observed: float | None = None
    expected: float | None = None
    zscore: float | None = None
    delta_pct: float | None = None
    confidence: float = 0.8
    evidence: dict[str, Any] = Field(default_factory=dict)
    hypothesis: str = ""


class DeviceTotal(BaseModel):
    id: str
    name: str
    kwh: float | None = None
    delta_pct: float | None = None


class ReportTotals(BaseModel):
    energy_kwh: float | None = None
    energy_hc_kwh: float | None = None
    energy_hp_kwh: float | None = None
    cost_eur: float | None = None
    solar_kwh: float | None = None


class HouseConsumptionContext(BaseModel):
    period: ReportPeriod
    period_start: datetime
    period_end: datetime
    generated_at: datetime
    baselines: list[BaselineStats] = Field(default_factory=list)
    candidate_anomalies: list[AnomalyFinding] = Field(default_factory=list)
    weather: WeatherContext = Field(default_factory=WeatherContext)
    presence: PresenceContext = Field(default_factory=PresenceContext)
    device_totals: list[DeviceTotal] = Field(default_factory=list)
    totals: ReportTotals = Field(default_factory=ReportTotals)
    notes: list[str] = Field(default_factory=list)


class ConsumptionReport(BaseModel):
    period: ReportPeriod
    period_start: datetime
    period_end: datetime
    generated_at: datetime
    scope: str = "house_energy_dashboard"
    headline: str
    summary: str
    totals: ReportTotals = Field(default_factory=ReportTotals)
    top_devices: list[DeviceTotal] = Field(default_factory=list)
    findings: list[AnomalyFinding] = Field(default_factory=list)
    suggested_checks: list[str] = Field(default_factory=list)
    narrative_md: str = ""
    severity: Severity = Severity.INFO
    llm_used: bool = False
    weather: WeatherContext | None = None
    presence: PresenceContext | None = None
