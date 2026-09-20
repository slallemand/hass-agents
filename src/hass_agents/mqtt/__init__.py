"""MQTT publish helpers (reports only — no device commands)."""

from __future__ import annotations

import json
import logging
from typing import Any

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MqttBus:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str = "",
        password: str = "",
        topic_prefix: str = "house",
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.topic_prefix = topic_prefix.rstrip("/")
        self._client: mqtt.Client | None = None

    def connect(self) -> None:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if self.username:
            client.username_pw_set(self.username, self.password or None)
        client.connect(self.host, self.port, keepalive=60)
        client.loop_start()
        self._client = client

    def disconnect(self) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    def publish_retained(self, topic: str, payload: str | dict[str, Any], *, qos: int = 1) -> None:
        """Publish a retained message (MQTT discovery configs, reports, etc.)."""
        body = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        if self._client is None:
            self.connect()
        assert self._client is not None
        self._client.publish(topic, body, qos=qos, retain=True)

    def publish_report(self, period: str, payload: dict[str, Any]) -> str:
        topic = f"{self.topic_prefix}/agents/consumption/report/{period}"
        self.publish_retained(topic, payload)
        summary_topic = f"{self.topic_prefix}/agents/consumption/summary/{period}"
        summary = {
            "headline": payload.get("headline"),
            "severity": payload.get("severity"),
            "period": period,
            "generated_at": payload.get("generated_at"),
            "energy_kwh": (payload.get("totals") or {}).get("energy_kwh"),
            "cost_eur": (payload.get("totals") or {}).get("cost_eur"),
        }
        self.publish_retained(summary_topic, summary)
        logger.info("Published report to %s", topic)
        return topic

    def request_topic(self) -> str:
        return f"{self.topic_prefix}/agents/consumption/request"
