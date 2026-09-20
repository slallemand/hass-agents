"""Home Assistant MQTT discovery for consumption report sensors."""

from __future__ import annotations

import logging
from typing import Any

from hass_agents.mqtt import MqttBus

logger = logging.getLogger(__name__)

PERIODS = ("daily", "weekly", "monthly")

_DEVICE: dict[str, Any] = {
    "identifiers": ["hass_agents"],
    "name": "Hass Agents",
    "manufacturer": "hass-agents",
    "model": "consumption-analysis",
}


def _object_id(period: str, kind: str) -> str:
    return f"house_ai_conso_{period}_{kind}"


def discovery_configs(
    topic_prefix: str,
    discovery_prefix: str = "homeassistant",
) -> list[tuple[str, dict[str, Any]]]:
    """Return (config_topic, payload) pairs for HA MQTT discovery."""
    prefix = topic_prefix.rstrip("/")
    disc = discovery_prefix.rstrip("/")
    out: list[tuple[str, dict[str, Any]]] = []

    for period in PERIODS:
        summary_topic = f"{prefix}/agents/consumption/summary/{period}"
        report_topic = f"{prefix}/agents/consumption/report/{period}"

        headline_id = _object_id(period, "headline")
        out.append(
            (
                f"{disc}/sensor/{headline_id}/config",
                {
                    "name": f"House AI Conso {period.capitalize()} Headline",
                    "unique_id": f"house_ai_consumption_{period}_headline",
                    "object_id": headline_id,
                    "state_topic": summary_topic,
                    "value_template": "{{ value_json.headline }}",
                    "json_attributes_topic": summary_topic,
                    "device": _DEVICE,
                },
            )
        )

        severity_id = _object_id(period, "severity")
        out.append(
            (
                f"{disc}/sensor/{severity_id}/config",
                {
                    "name": f"House AI Conso {period.capitalize()} Severity",
                    "unique_id": f"house_ai_consumption_{period}_severity",
                    "object_id": severity_id,
                    "state_topic": report_topic,
                    "value_template": "{{ value_json.severity }}",
                    "json_attributes_topic": report_topic,
                    "icon": "mdi:alert-circle-outline",
                    "device": _DEVICE,
                },
            )
        )

    return out


def publish_discovery(
    bus: MqttBus,
    *,
    discovery_prefix: str = "homeassistant",
) -> int:
    """Publish retained discovery configs. Returns number of messages published."""
    configs = discovery_configs(bus.topic_prefix, discovery_prefix)
    for topic, payload in configs:
        bus.publish_retained(topic, payload)
        logger.debug("MQTT discovery → %s", topic)

    logger.info(
        "Published %s MQTT discovery configs (prefix=%s)",
        len(configs),
        discovery_prefix,
    )
    return len(configs)
