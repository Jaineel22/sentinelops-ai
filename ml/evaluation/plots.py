"""Matplotlib figures for experiment reports.

Kept minimal and purposeful: a score timeline (detections vs ground truth), a
metric-comparison bar chart, and a telemetry overview. No decorative charts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def plot_score_timeline(
    y_true: np.ndarray,
    scores: np.ndarray,
    threshold: float,
    out_path: Path,
    *,
    title: str,
) -> Path:
    y_true = np.asarray(y_true)
    idx = np.arange(len(scores))
    fig, ax = plt.subplots(figsize=(11, 3.5))

    ax.fill_between(
        idx,
        0,
        1,
        where=y_true == 1,
        transform=ax.get_xaxis_transform(),
        color="tab:orange",
        alpha=0.15,
        label="true anomaly window",
    )
    ax.plot(idx, scores, color="tab:blue", lw=1.2, label="anomaly score")
    ax.axhline(threshold, color="tab:red", ls="--", lw=1, label=f"threshold={threshold:.3g}")

    fired = scores > threshold
    ax.scatter(idx[fired], scores[fired], s=18, color="tab:red", zorder=3, label="flagged")

    ax.set_xlabel("test window (chronological)")
    ax.set_ylabel("anomaly score")
    ax.set_title(title)
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def plot_metric_comparison(results: dict[str, Any], out_path: Path, *, title: str) -> Path:
    models = list(results)
    metrics = ["precision", "recall", "f1", "false_positive_rate"]
    values = {m: [results[model]["pointwise"][m] for model in models] for m in metrics}

    x = np.arange(len(models))
    width = 0.2
    fig, ax = plt.subplots(figsize=(1.6 * len(models) + 3, 4))
    for i, m in enumerate(metrics):
        ax.bar(x + (i - 1.5) * width, values[m], width, label=m.replace("_", " "))
    ax.set_xticks(x)
    ax.set_xticklabels(models, rotation=15, ha="right", fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("score")
    ax.set_title(f"{title} - test metrics")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def plot_telemetry_overview(df: pd.DataFrame, out_path: Path, *, title: str) -> Path:
    signals = ["request_rate", "error_rate", "latency_mean_ms", "publish_error_rate"]
    signals = [s for s in signals if s in df.columns]
    fig, axes = plt.subplots(len(signals), 1, figsize=(11, 2.2 * len(signals)), sharex=True)
    if len(signals) == 1:
        axes = [axes]
    anomaly = df["is_anomaly"].to_numpy() if "is_anomaly" in df.columns else np.zeros(len(df))
    idx = np.arange(len(df))
    for ax, sig in zip(axes, signals, strict=False):
        ax.plot(idx, df[sig].to_numpy(), lw=1.1, color="tab:blue")
        ax.fill_between(
            idx,
            0,
            1,
            where=anomaly == 1,
            transform=ax.get_xaxis_transform(),
            color="tab:orange",
            alpha=0.15,
        )
        ax.set_ylabel(sig, fontsize=8)
    axes[-1].set_xlabel("window")
    axes[0].set_title(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path
