"""Read-only Home Assistant client (states, history, statistics)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import websockets

logger = logging.getLogger(__name__)


class HAClient:
    """Read-only access to Home Assistant. Never calls write services."""

    def __init__(self, base_url: str, token: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                f"{self.base_url}/api/states/{entity_id}",
                headers=self._headers,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    def get_states(self, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for eid in entity_ids:
            state = self.get_state(eid)
            if state is not None:
                out[eid] = state
        return out

    def get_history(
        self,
        entity_ids: list[str],
        start: datetime,
        end: datetime | None = None,
        *,
        minimal: bool = True,
        significant_changes_only: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        end = end or datetime.now(timezone.utc)
        start_s = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        end_s = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        params: dict[str, Any] = {
            "filter_entity_id": ",".join(entity_ids),
            "end_time": end_s,
            "minimal_response": str(minimal).lower(),
            "significant_changes_only": str(significant_changes_only).lower(),
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                f"{self.base_url}/api/history/period/{start_s}",
                headers=self._headers,
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()
        result: dict[str, list[dict[str, Any]]] = {eid: [] for eid in entity_ids}
        for series in payload:
            if not series:
                continue
            eid = series[0].get("entity_id")
            if eid in result:
                result[eid] = series
        return result

    def get_statistics(
        self,
        statistic_ids: list[str],
        start: datetime,
        end: datetime | None = None,
        period: str = "day",
        types: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Long-term statistics via WebSocket recorder API."""
        return asyncio.run(
            self._statistics_ws(statistic_ids, start, end, period, types or ["change", "mean", "sum"])
        )

    async def _statistics_ws(
        self,
        statistic_ids: list[str],
        start: datetime,
        end: datetime | None,
        period: str,
        types: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        end = end or datetime.now(timezone.utc)
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        if not ws_url.endswith("/api/websocket"):
            ws_url = f"{ws_url}/api/websocket"

        empty = {sid: [] for sid in statistic_ids}
        try:
            async with websockets.connect(ws_url, open_timeout=15) as ws:
                hello = json.loads(await ws.recv())
                if hello.get("type") != "auth_required":
                    logger.warning("Unexpected WS hello: %s", hello)
                    return empty

                await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
                auth = json.loads(await ws.recv())
                if auth.get("type") != "auth_ok":
                    logger.error("HA WS auth failed: %s", auth)
                    return empty

                msg_id = 1
                await ws.send(
                    json.dumps(
                        {
                            "id": msg_id,
                            "type": "recorder/statistics_during_period",
                            "start_time": start.astimezone(timezone.utc).isoformat(),
                            "end_time": end.astimezone(timezone.utc).isoformat(),
                            "statistic_ids": statistic_ids,
                            "period": period,
                            "types": types,
                        }
                    )
                )
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    data = json.loads(raw)
                    if data.get("id") != msg_id:
                        continue
                    if not data.get("success"):
                        logger.error("statistics_during_period failed: %s", data)
                        return empty
                    result = data.get("result") or {}
                    return {sid: result.get(sid, []) for sid in statistic_ids}
        except Exception:
            logger.exception("Failed to fetch HA statistics via websocket")
            return empty

    @staticmethod
    def parse_float(state: str | None) -> float | None:
        if state is None:
            return None
        try:
            return float(state)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def lookback_for_period(period: str) -> timedelta:
        if period == "daily":
            return timedelta(days=35)
        if period == "weekly":
            return timedelta(days=60)
        return timedelta(days=120)
