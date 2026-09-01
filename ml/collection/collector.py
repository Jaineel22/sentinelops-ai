"""Track A dataset collector.

Drives a seeded sequence of operational scenarios against a running
``orders-service`` and scrapes ``/metrics`` every ``--step`` seconds, writing
cumulative snapshots plus the ground-truth scenario active at each scrape.

    python -m ml.collection.collector --run-id run_a --plan main
    python -m ml.collection.collector --run-id run_b --plan holdout

Requires the Phase 1 stack (``docker compose up -d kafka orders-service``) and
the dev-only ``/admin/simulation`` endpoint. This is a one-off generator; the
produced dataset is committed so downstream steps need not re-run it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import json
import random
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ml.collection.scenarios import SCENARIOS, Scenario, label_to_binary
from ml.config import RANDOM_SEED, RAW_DIR
from ml.data.prometheus_parse import parse_metrics

PLANS: dict[str, list[str]] = {
    # Deterministic and **interleaved**: fault segments are evenly spaced between
    # normal segments so a later chronological train/val/test split gives each
    # part a proportional share of anomalies. `normal` outnumbers faults so the
    # dataset is realistically imbalanced (majority normal). `recovery` is a
    # normal-behaviour segment used to mark the return to baseline on the
    # timeline. Order is NOT shuffled.
    "main": ["normal", "latency", "normal", "errors", "normal", "surge", "normal", "recovery"] * 3,
    "holdout": ["normal", "publish_failure", "normal", "surge", "normal", "recovery"] * 3,
    "smoke": ["normal", "latency", "normal", "errors"],
}

CUSTOMERS = [f"customer-{i:03d}" for i in range(50)]
CURRENCIES = ["INR", "USD", "EUR", "GBP"]


async def _one_order(client: httpx.AsyncClient, rng: random.Random) -> None:
    body = {
        "customer_id": rng.choice(CUSTOMERS),
        "amount": round(rng.uniform(5, 5000), 2),
        "currency": rng.choice(CURRENCIES),
    }
    with contextlib.suppress(httpx.HTTPError):
        await client.post("/orders", json=body)


async def _drive_traffic(
    client: httpx.AsyncClient,
    *,
    rate: float,
    stop: asyncio.Event,
    rng: random.Random,
    max_inflight: int = 8,
) -> None:
    """Fire ~``rate`` requests/second with a hard cap on concurrent requests so a
    slow service cannot cause an unbounded task backlog."""

    interval = 1.0 / rate if rate > 0 else 0.2
    sem = asyncio.Semaphore(max_inflight)

    async def _bounded() -> None:
        async with sem:
            await _one_order(client, rng)

    pending: set[asyncio.Task[None]] = set()
    while not stop.is_set():
        if sem.locked():  # at capacity — skip this tick rather than queue
            await asyncio.sleep(interval)
            continue
        task = asyncio.create_task(_bounded())
        pending.add(task)
        task.add_done_callback(pending.discard)
        await asyncio.sleep(interval)
    if pending:
        await asyncio.wait(pending, timeout=15)


def _snapshot_row(
    *, scrape_ts: float, run_id: str, scenario: Scenario, text: str
) -> dict[str, float | str | int]:
    snap = parse_metrics(text)
    row: dict[str, float | str | int] = {
        "run_id": run_id,
        "scrape_ts": scrape_ts,
        "scrape_iso": datetime.fromtimestamp(scrape_ts, tz=UTC).isoformat(),
        "scenario": scenario.name,
        "label": scenario.label,
        "is_anomaly": label_to_binary(scenario.label),
    }
    for key, value in asdict(snap).items():
        if key == "http_post_latency_buckets":
            for le, count in value.items():
                tag = "inf" if le == float("inf") else str(le)
                row[f"http_post_bucket_{tag}"] = count
        else:
            row[key] = value
    return row


async def collect(args: argparse.Namespace) -> Path:
    rng = random.Random(args.seed)  # drives request payloads only; the plan is fixed
    plan = list(PLANS[args.plan])

    out_dir = RAW_DIR / "sentinelops" / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, float | str | int]] = []
    segments: list[dict[str, object]] = []

    async with httpx.AsyncClient(base_url=args.base_url, timeout=10.0) as client:
        await client.get("/health")
        for i, name in enumerate(plan):
            scenario = SCENARIOS[name]
            await client.put("/admin/simulation", json=scenario.simulation_payload)
            seg_start = time.time()
            stop = asyncio.Event()
            traffic = asyncio.create_task(
                _drive_traffic(
                    client, rate=args.rate * scenario.rate_multiplier, stop=stop, rng=rng
                )
            )
            # Let the injection + rate change settle before the first scrape.
            await asyncio.sleep(args.settle)
            n_scrapes = max(2, int(args.segment_seconds // args.step))
            for _ in range(n_scrapes):
                await asyncio.sleep(args.step)
                text = (await client.get("/metrics")).text
                rows.append(
                    _snapshot_row(
                        scrape_ts=time.time(), run_id=args.run_id, scenario=scenario, text=text
                    )
                )
            stop.set()
            await traffic
            segments.append(
                {
                    "index": i,
                    "scenario": scenario.name,
                    "label": scenario.label,
                    "start_ts": seg_start,
                    "end_ts": time.time(),
                }
            )
            print(f"[{i + 1}/{len(plan)}] {scenario.name}: {n_scrapes} scrapes")

        await client.put("/admin/simulation", json=SCENARIOS["normal"].simulation_payload)

    fieldnames = sorted({k for row in rows for k in row})
    snapshots_path: Path = out_dir / "snapshots.csv"
    with snapshots_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    manifest = {
        "run_id": args.run_id,
        "plan": args.plan,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "seed": args.seed,
        "base_url": args.base_url,
        "step_seconds": args.step,
        "segment_seconds": args.segment_seconds,
        "settle_seconds": args.settle,
        "baseline_rate_rps": args.rate,
        "scenario_order": plan,
        "segments": segments,
        "n_snapshots": len(rows),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} snapshots -> {snapshots_path}")
    return snapshots_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", required=True)
    p.add_argument("--plan", choices=sorted(PLANS), default="main")
    p.add_argument("--base-url", default="http://localhost:8001")
    p.add_argument("--rate", type=float, default=3.0, help="baseline requests/second")
    p.add_argument("--step", type=float, default=10.0, help="seconds between scrapes")
    p.add_argument("--segment-seconds", type=float, default=70.0, help="seconds per scenario")
    p.add_argument("--settle", type=float, default=4.0, help="settle time after switching scenario")
    p.add_argument("--seed", type=int, default=RANDOM_SEED)
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(collect(parse_args()))
