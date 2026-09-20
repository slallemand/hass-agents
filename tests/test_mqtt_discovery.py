"""Tests for Home Assistant MQTT discovery payloads."""

from hass_agents.mqtt.discovery import PERIODS, discovery_configs


def test_discovery_configs_cover_periods_and_kinds():
    configs = discovery_configs("house", "homeassistant")
    # 3 periods × (headline + severity)
    assert len(configs) == 6

    topics = [t for t, _ in configs]
    for period in PERIODS:
        assert f"homeassistant/sensor/house_ai_conso_{period}_headline/config" in topics
        assert f"homeassistant/sensor/house_ai_conso_{period}_severity/config" in topics

    by_topic = dict(configs)
    headline = by_topic["homeassistant/sensor/house_ai_conso_daily_headline/config"]
    assert headline["state_topic"] == "house/agents/consumption/summary/daily"
    assert headline["unique_id"] == "house_ai_consumption_daily_headline"
    assert headline["device"]["identifiers"] == ["hass_agents"]

    severity = by_topic["homeassistant/sensor/house_ai_conso_daily_severity/config"]
    assert severity["state_topic"] == "house/agents/consumption/report/daily"
    assert "{{ value_json.severity }}" in severity["value_template"]
