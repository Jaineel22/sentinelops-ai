"""Development traffic generator for orders-service.

Drives HTTP load at ``POST /orders`` and can switch the service's failure
injection between scenarios via ``PUT /admin/simulation``. It exists to produce
a *time series* of normal and abnormal operational behaviour that later phases
(anomaly detection, incident correlation) will observe.

This is telemetry generation, not a load/benchmark tool and not ML data.

Examples
--------
    python scripts/generate_traffic.py --scenario normal --duration 60 --rate 5
    python scripts/generate_traffic.py --scenario latency --duration 45 --rate 5
    python scripts/generate_traffic.py --scenario sequence   # A -> B -> C -> E

Requires the dev-only ``/admin/simulation`` endpoint (default in non-production).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from dataclasses import dataclass

import httpx

CUSTOMERS = [f"customer-{i:03d}" for i in range(50)]
CURRENCIES = ["INR", "USD", "EUR", "GBP"]


@dataclass(frozen=True)
class Scenario:
    name: str
    rate_multiplier: float
    latency_ms: int
    error_rate: float
    publish_error_rate: float


SCENARIOS: dict[str, Scenario] = {
    "normal": Scenario("normal", 1.0, 0, 0.0, 0.0),
    "latency": Scenario("latency", 1.0, 400, 0.0, 0.0),
    "errors": Scenario("errors", 1.0, 0, 0.25, 0.0),
    "publish-errors": Scenario("publish-errors", 1.0, 0, 0.0, 0.25),
    "surge": Scenario("surge", 4.0, 0, 0.0, 0.0),
    "recovery": Scenario("recovery", 1.0, 0, 0.0, 0.0),
}


async def _set_simulation(client: httpx.AsyncClient, scenario: Scenario) -> None:
    payload = {
        "latency_ms": scenario.latency_ms,
        "error_rate": scenario.error_rate,
        "publish_error_rate": scenario.publish_error_rate,
    }
    response = await client.put("/admin/simulation", json=payload)
    response.raise_for_status()
    print(f"[sim] {scenario.name}: {payload}")


async def _one_order(client: httpx.AsyncClient, stats: dict[str, int]) -> None:
    body = {
        "customer_id": random.choice(CUSTOMERS),
        "amount": round(random.uniform(5, 5000), 2),
        "currency": random.choice(CURRENCIES),
    }
    try:
        response = await client.post("/orders", json=body)
        stats[f"http_{response.status_code}"] = stats.get(f"http_{response.status_code}", 0) + 1
    except httpx.HTTPError:
        stats["client_error"] = stats.get("client_error", 0) + 1


async def _drive(client: httpx.AsyncClient, *, rate: float, duration: float) -> dict[str, int]:
    stats: dict[str, int] = {}
    deadline = time.monotonic() + duration
    interval = 1.0 / rate if rate > 0 else 0.2
    tasks: list[asyncio.Task[None]] = []
    while time.monotonic() < deadline:
        tasks.append(asyncio.create_task(_one_order(client, stats)))
        tasks = [t for t in tasks if not t.done()]
        await asyncio.sleep(interval)
    await asyncio.gather(*tasks)
    return stats


async def run(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10.0) as client:
        if args.scenario == "sequence":
            plan = [
                ("normal", args.duration),
                ("latency", args.duration),
                ("errors", args.duration),
                ("surge", args.duration),
                ("recovery", args.duration),
            ]
        else:
            plan = [(args.scenario, args.duration)]

        for name, seconds in plan:
            scenario = SCENARIOS[name]
            if not args.no_admin:
                await _set_simulation(client, scenario)
            stats = await _drive(
                client, rate=args.rate * scenario.rate_multiplier, duration=seconds
            )
            print(f"[{name}] {seconds:.0f}s -> {stats}")

        if not args.no_admin:
            await _set_simulation(client, SCENARIOS["normal"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8001")
    parser.add_argument(
        "--scenario",
        default="normal",
        choices=[*SCENARIOS.keys(), "sequence"],
    )
    parser.add_argument("--rate", type=float, default=5.0, help="requests/second (baseline)")
    parser.add_argument("--duration", type=float, default=60.0, help="seconds per scenario")
    parser.add_argument(
        "--no-admin",
        action="store_true",
        help="do not touch /admin/simulation; only send traffic",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
