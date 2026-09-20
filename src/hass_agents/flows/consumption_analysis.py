"""Consumption analysis flow: context → (LLM crew | heuristic) → report."""

from __future__ import annotations

import logging
from pathlib import Path

from hass_agents.agents.crew import run_llm_crew
from hass_agents.analytics.baselines import build_heuristic_report
from hass_agents.analytics.context_builder import build_context
from hass_agents.config import Settings
from hass_agents.ha import HAClient
from hass_agents.mqtt import MqttBus
from hass_agents.schemas import ConsumptionReport, ReportPeriod

logger = logging.getLogger(__name__)


def run_analysis(
    period: ReportPeriod | str,
    settings: Settings | None = None,
    *,
    publish_mqtt: bool = False,
    write_path: Path | None = None,
) -> ConsumptionReport:
    settings = settings or Settings()
    if isinstance(period, str):
        period = ReportPeriod(period)

    if not settings.ha_token:
        raise RuntimeError("HA_TOKEN is required")

    client = HAClient(settings.ha_url, settings.ha_token)
    ctx = build_context(settings, period, client=client)
    logger.info(
        "Context built: %s baselines, %s candidate anomalies",
        len(ctx.baselines),
        len(ctx.candidate_anomalies),
    )

    report: ConsumptionReport | None = None
    if settings.llm_enabled:
        report = run_llm_crew(ctx, settings)
        if report is None:
            logger.warning("Falling back to heuristic report")
    if report is None:
        report = build_heuristic_report(ctx)

    if write_path is None:
        write_path = Path("reports") / f"consumption_{period.value}.json"
    write_path.parent.mkdir(parents=True, exist_ok=True)
    write_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Wrote %s", write_path)

    if publish_mqtt:
        bus = MqttBus(
            settings.mqtt_host,
            settings.mqtt_port,
            settings.mqtt_username,
            settings.mqtt_password,
            settings.mqtt_topic_prefix,
        )
        try:
            bus.connect()
            bus.publish_report(period.value, report.model_dump(mode="json"))
        finally:
            bus.disconnect()

    return report
