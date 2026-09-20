"""CLI entrypoint: analyze-once | serve."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from hass_agents.config import Settings
from hass_agents.flows.consumption_analysis import run_analysis
from hass_agents.mqtt import MqttBus
from hass_agents.schemas import ReportPeriod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("hass_agents")


def cmd_analyze_once(args: argparse.Namespace) -> int:
    settings = Settings()
    report = run_analysis(
        args.period,
        settings,
        publish_mqtt=args.mqtt,
        write_path=None if not args.output else __import__("pathlib").Path(args.output),
    )
    print(report.model_dump_json(indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    settings = Settings()
    bus = MqttBus(
        settings.mqtt_host,
        settings.mqtt_port,
        settings.mqtt_username,
        settings.mqtt_password,
        settings.mqtt_topic_prefix,
    )
    bus.connect()

    if settings.mqtt_discovery:
        from hass_agents.mqtt.discovery import publish_discovery

        publish_discovery(bus, discovery_prefix=settings.mqtt_discovery_prefix)

    topic = bus.request_topic()
    logger.info("Listening for requests on %s", topic)

    import paho.mqtt.client as mqtt

    def on_message(_client: mqtt.Client, _userdata: object, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        period = payload.get("period", "daily")
        logger.info("Received analysis request period=%s", period)
        try:
            run_analysis(period, settings, publish_mqtt=True)
        except Exception:
            logger.exception("Analysis failed")

    assert bus._client is not None
    bus._client.subscribe(topic, qos=1)
    bus._client.on_message = on_message

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        bus.disconnect()
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hass-agents")
    sub = parser.add_subparsers(dest="command", required=True)

    p_once = sub.add_parser("analyze-once", help="Run one consumption analysis")
    p_once.add_argument(
        "--period",
        choices=[p.value for p in ReportPeriod],
        default="daily",
    )
    p_once.add_argument("--mqtt", action="store_true", help="Publish report to MQTT")
    p_once.add_argument("--output", help="Write JSON report to this path")
    p_once.set_defaults(func=cmd_analyze_once)

    p_serve = sub.add_parser("serve", help="Listen MQTT for analysis requests")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        raise SystemExit(args.func(args))
    except Exception as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main(sys.argv[1:])
