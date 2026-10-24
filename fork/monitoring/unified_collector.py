#!/usr/bin/env python3
"""Bounded one-shot host, Docker, and n8n chain metrics collector."""

from __future__ import annotations

import os
import math
import json
import argparse
import errno
import fcntl
import http.client
import socket
import subprocess
import time
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional


CPU_MODES = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
DOCKER_LIST_PATH = "/v1.47/containers/json?all=true"
DOCKER_STATS_PATH = "/v1.47/containers/{container_id}/stats?stream=false&one-shot=true"
DOCKER_INSPECT_PATH = "/v1.47/containers/{container_id}/json"
DB_TIMEOUT_SECONDS = 5
CHAIN_RESPONSE_LIMIT = 2 * 1024 * 1024
DOCKER_REQUEST_TIMEOUT = 1.0
DOCKER_RESPONSE_LIMIT = 2 * 1024 * 1024
RUN_DEADLINE_SECONDS = 24.0
OUTPUT_LIMIT = 256 * 1024
STATE_LIMIT = 1024 * 1024
COMPONENTS = ("host", "docker", "chain")
METRIC_METADATA = {
    "melown_host_cpu_seconds_total": ("Host CPU time in seconds by mode.", "counter"),
    "melown_host_memory_bytes": ("Host memory bytes by kind.", "gauge"),
    "melown_host_disk_bytes": ("Host filesystem bytes by kind.", "gauge"),
    "melown_host_load1": ("Host one minute load average.", "gauge"),
    "melown_host_uptime_seconds": ("Host uptime in seconds.", "gauge"),
    "melown_docker_running": ("Docker container running state.", "gauge"),
    "melown_docker_restarts": ("Docker container restart count.", "gauge"),
    "melown_docker_cpu_seconds_total": ("Docker container CPU time in seconds.", "counter"),
    "melown_docker_memory_bytes": ("Docker container memory bytes.", "gauge"),
    "melown_docker_memory_limit_bytes": ("Docker container memory limit in bytes.", "gauge"),
    "melown_chain_queue": ("Chain queue entries by status.", "gauge"),
    "melown_chain_oldest_pending_age_seconds": ("Age of the oldest pending chain entry in seconds.", "gauge"),
    "melown_chain_last_delivery_timestamp_seconds": ("Unix timestamp of the last delivery.", "gauge"),
    "melown_chain_delivered_today": ("Chain deliveries today.", "gauge"),
    "melown_chain_delivery_delay_seconds": ("Chain delivery delay in seconds by daily statistic.", "gauge"),
    "melown_chain_delivery_delay_samples": ("Chain delivery delay sample count.", "gauge"),
    "melown_chain_events_today": ("Chain events today.", "gauge"),
    "melown_chain_last_webhook_timestamp_seconds": ("Unix timestamp of the last webhook.", "gauge"),
    "melown_chain_workflow_executions_today": ("Workflow executions today.", "gauge"),
    "melown_chain_source_success": ("Latest source check success state.", "gauge"),
    "melown_chain_source_checked_timestamp_seconds": ("Unix timestamp of the source check.", "gauge"),
    "melown_collector_success": ("Collector component success state.", "gauge"),
    "melown_collector_last_success_timestamp_seconds": ("Unix timestamp of the last component success.", "gauge"),
    "melown_collector_timestamp_seconds": ("Unix timestamp of the collector snapshot.", "gauge"),
    "melown_collector_duration_seconds": ("Collector duration in seconds.", "gauge"),
}


class CollectorError(Exception):
    """Expected collector failure."""


class DeadlineExceeded(CollectorError):
    """The bounded collection deadline elapsed."""


def _check_deadline(deadline: Optional[float], monotonic_fn: Callable[[], float]) -> None:
    if deadline is not None and monotonic_fn() >= deadline:
        raise DeadlineExceeded("deadline")


def _label_text(value: object, limit: int = 100) -> str:
    text = str(value).strip(" \t")
    text = text.lstrip("/")
    text = "".join("_" if (ord(char) < 0x20 and char != "\n") or ord(char) == 0x7F else char for char in text)
    return text[:limit] or "unknown"


def _prom_escape(value: object) -> str:
    escaped = _label_text(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    if len(escaped) <= 100:
        return escaped
    escaped = escaped[:100]
    while escaped.endswith("\\"):
        escaped = escaped[:-1]
    return escaped


def _format_value(value: object) -> str:
    if value is None:
        return "NaN"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "+Inf" if number > 0 else "-Inf"
    return str(number)


def _metric(name: str, value: object, **labels: object) -> str:
    if labels:
        label_text = ",".join(
            f'{key}="{_prom_escape(labels[key])}"' for key in labels
        )
        return f"{name}{{{label_text}}} {_format_value(value)}"
    return f"{name} {_format_value(value)}"


def _metadata_lines() -> list[str]:
    lines: list[str] = []
    for name, (help_text, metric_type) in METRIC_METADATA.items():
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")
    return lines


def collect_host(
    proc_root: str = "/proc",
    root_path: str = "/",
    loadavg_fn: Callable[[], tuple[float, float, float]] = os.getloadavg,
    clk_tck: Optional[int] = None,
) -> dict[str, object]:
    """Read bounded host counters from procfs and statvfs."""
    proc = Path(proc_root)
    stat_lines = (proc / "stat").read_text(encoding="ascii").splitlines()
    if not stat_lines:
        raise ValueError("missing proc stat")
    cpu_fields = stat_lines[0].split()
    if not cpu_fields or cpu_fields[0] != "cpu" or len(cpu_fields) < 1 + len(CPU_MODES):
        raise ValueError("invalid proc stat")
    if clk_tck is None:
        clk_tck = int(os.sysconf("SC_CLK_TCK"))
    if clk_tck <= 0:
        raise ValueError("invalid clock tick")
    cpu_ticks = [int(value) for value in cpu_fields[1:]]
    cpu_seconds = {
        mode: cpu_ticks[index] / clk_tck
        for index, mode in enumerate(CPU_MODES)
    }

    memory: dict[str, int] = {}
    for line in (proc / "meminfo").read_text(encoding="ascii").splitlines():
        key, separator, value = line.partition(":")
        if not separator or key not in ("MemTotal", "MemAvailable"):
            continue
        words = value.strip().split()
        if not words:
            continue
        multiplier = 1024 if len(words) > 1 and words[1].lower() == "kb" else 1
        memory[{"MemTotal": "total", "MemAvailable": "available"}[key]] = int(words[0]) * multiplier
    if set(memory) != {"total", "available"}:
        raise ValueError("missing proc memory")

    uptime_words = (proc / "uptime").read_text(encoding="ascii").split()
    if not uptime_words:
        raise ValueError("missing proc uptime")
    stat = os.statvfs(root_path)
    block_size = stat.f_frsize or stat.f_bsize
    disk = {
        "total": int(block_size * stat.f_blocks),
        "available": int(block_size * stat.f_bavail),
    }
    return {
        "cpu_seconds_total": cpu_seconds,
        "memory_bytes": memory,
        "disk_bytes": disk,
        "load1": float(loadavg_fn()[0]),
        "uptime_seconds": float(uptime_words[0]),
    }


def render_host_metrics(data: dict[str, object]) -> list[str]:
    cpu = data.get("cpu_seconds_total")
    memory = data.get("memory_bytes")
    disk = data.get("disk_bytes")
    if not isinstance(cpu, dict) or not isinstance(memory, dict) or not isinstance(disk, dict):
        raise ValueError("invalid host metrics")
    lines: list[str] = []
    for mode in CPU_MODES:
        if mode not in cpu:
            raise ValueError("missing cpu mode")
        lines.append(_metric("melown_host_cpu_seconds_total", cpu[mode], mode=mode))
    for kind in ("total", "available"):
        if kind not in memory:
            raise ValueError("missing memory kind")
        lines.append(_metric("melown_host_memory_bytes", memory[kind], kind=kind))
    for kind in ("total", "available"):
        if kind not in disk:
            raise ValueError("missing disk kind")
        lines.append(_metric("melown_host_disk_bytes", disk[kind], kind=kind))
    if "load1" not in data or "uptime_seconds" not in data:
        raise ValueError("missing host scalar")
    lines.append(_metric("melown_host_load1", data["load1"]))
    lines.append(_metric("melown_host_uptime_seconds", data["uptime_seconds"]))
    return lines


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    """HTTP/1.1 connection over a Docker Unix socket."""

    def __init__(self, socket_path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except Exception:
            sock.close()
            raise
        self.sock = sock


class DockerClient:
    """Small GET-only Docker Engine API client."""

    def __init__(
        self,
        socket_path: str = "/var/run/docker.sock",
        monotonic_fn: Callable[[], float] = time.monotonic,
        connection_factory: Optional[Callable[[str, float], object]] = None,
    ):
        self.socket_path = socket_path
        self.monotonic_fn = monotonic_fn
        self.connection_factory = connection_factory

    def get_json(self, path: str, deadline: Optional[float]) -> object:
        if not path.startswith("/v1.47/"):
            raise ValueError("unsupported docker path")
        remaining = DOCKER_REQUEST_TIMEOUT
        if deadline is not None:
            remaining = min(remaining, deadline - self.monotonic_fn())
            if remaining <= 0:
                raise DeadlineExceeded("docker deadline")
        connection_factory = self.connection_factory or UnixSocketHTTPConnection
        connection = connection_factory(self.socket_path, remaining)
        try:
            connection.request("GET", path, headers={"Host": "localhost", "Accept": "application/json"})
            response = connection.getresponse()
            if response.status < 200 or response.status >= 300:
                raise ValueError("docker http failure")
            body = response.read(DOCKER_RESPONSE_LIMIT + 1)
            if isinstance(body, str):
                body = body.encode("utf-8")
            if len(body) > DOCKER_RESPONSE_LIMIT:
                raise ValueError("docker response too large")
            return json.loads(body.decode("utf-8"))
        finally:
            connection.close()


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(field)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(field)
    return number


def _container_name(container: dict[str, object]) -> str:
    names = container.get("Names")
    if isinstance(names, list) and names and names[0] is not None:
        return _label_text(names[0])
    if container.get("Id") is not None:
        return _label_text(container["Id"])
    raise ValueError("container name")


def _container_id(container: dict[str, object]) -> str:
    value = container.get("Id")
    if not isinstance(value, str) or not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in value):
        raise ValueError("container id")
    return value


def _memory_values(stats: dict[str, object]) -> tuple[float, float]:
    memory_stats = stats.get("memory_stats")
    if not isinstance(memory_stats, dict):
        raise ValueError("memory stats")
    usage = _number(memory_stats.get("usage"), "memory usage")
    limit = _number(memory_stats.get("limit"), "memory limit")
    details = memory_stats.get("stats")
    inactive: object = None
    if isinstance(details, dict):
        inactive = details.get("inactive_file")
        if inactive is None:
            inactive = details.get("total_inactive_file")
        if inactive is None:
            inactive = details.get("cache")
    if inactive is None:
        inactive = memory_stats.get("inactive_file")
    if inactive is not None:
        usage = max(0.0, usage - _number(inactive, "inactive memory"))
    return usage, limit


def collect_docker(
    client: object,
    deadline: Optional[float],
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Collect at most 64 Docker containers through an injected client."""
    _check_deadline(deadline, monotonic_fn)
    listed = client.get_json(DOCKER_LIST_PATH, deadline)
    if not isinstance(listed, list):
        raise ValueError("invalid docker list")
    if len(listed) > 64:
        raise ValueError("too many docker containers")
    containers: list[dict[str, object]] = []
    used_names: set[str] = set()
    for item in listed:
        _check_deadline(deadline, monotonic_fn)
        if not isinstance(item, dict):
            raise ValueError("invalid docker container")
        container_id = _container_id(item)
        running = item.get("State") == "running"
        restart_count = item.get("RestartCount")
        if restart_count is None:
            inspected = client.get_json(DOCKER_INSPECT_PATH.format(container_id=container_id), deadline)
            if not isinstance(inspected, dict):
                raise ValueError("invalid docker inspect")
            restart_count = inspected.get("RestartCount")
            if restart_count is None and isinstance(inspected.get("State"), dict):
                restart_count = inspected["State"].get("RestartCount")
        restarts = _number(restart_count, "restart count")
        name = _container_name(item)
        if name in used_names:
            base = name
            suffix = 2
            while True:
                suffix_text = f"_{suffix}"
                name = f"{base[:100 - len(suffix_text)]}{suffix_text}"
                if name not in used_names:
                    break
                suffix += 1
        used_names.add(name)
        result: dict[str, object] = {
            "container": name,
            "running": running,
            "restarts": int(restarts) if restarts.is_integer() else restarts,
        }
        if running:
            stats = client.get_json(DOCKER_STATS_PATH.format(container_id=container_id), deadline)
            if not isinstance(stats, dict):
                raise ValueError("invalid docker stats")
            cpu_stats = stats.get("cpu_stats")
            if not isinstance(cpu_stats, dict) or not isinstance(cpu_stats.get("cpu_usage"), dict):
                raise ValueError("cpu stats")
            total_usage = _number(cpu_stats["cpu_usage"].get("total_usage"), "cpu usage")
            memory, limit = _memory_values(stats)
            result["cpu_seconds"] = total_usage / 1_000_000_000
            result["memory_bytes"] = memory
            result["memory_limit_bytes"] = limit
        containers.append(result)
    return {"containers": containers}


def render_docker_metrics(data: dict[str, object]) -> list[str]:
    containers = data.get("containers")
    if not isinstance(containers, list):
        raise ValueError("invalid docker metrics")
    lines: list[str] = []
    for container in containers:
        if not isinstance(container, dict):
            raise ValueError("invalid docker metric")
        name = _label_text(container.get("container"))
        lines.append(_metric("melown_docker_running", 1 if container.get("running") else 0, container=name))
        lines.append(_metric("melown_docker_restarts", container.get("restarts"), container=name))
        if container.get("running"):
            lines.append(_metric("melown_docker_cpu_seconds_total", container.get("cpu_seconds"), container=name))
            lines.append(_metric("melown_docker_memory_bytes", container.get("memory_bytes"), container=name))
            lines.append(_metric("melown_docker_memory_limit_bytes", container.get("memory_limit_bytes"), container=name))
    return lines


def collect_chain(
    sql_path: str,
    db_container: str,
    db_name: str,
    db_user: str,
    deadline: Optional[float],
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Run the bounded read-only chain query and parse its single JSON result."""
    _check_deadline(deadline, monotonic_fn)
    sql_file = Path(sql_path)
    sql = sql_file.read_text(encoding="utf-8")
    _check_deadline(deadline, monotonic_fn)
    timeout = DB_TIMEOUT_SECONDS
    if deadline is not None:
        timeout = min(timeout, max(0.001, deadline - monotonic_fn()))
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            db_container,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            db_user,
            "-d",
            db_name,
        ],
        input=sql,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise ValueError("psql failure")
    raw = result.stdout
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if len(raw.encode("utf-8")) > CHAIN_RESPONSE_LIMIT:
        raise ValueError("chain response too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid chain json")
    return value


def _append_if_present(
    lines: list[str],
    data: dict[str, object],
    key: str,
    metric_name: str,
    null_as_nan: bool = False,
) -> None:
    if key in data:
        value = data[key]
        if value is None:
            if null_as_nan:
                lines.append(_metric(metric_name, value))
            return
        _number(value, key)
        lines.append(_metric(metric_name, value))


def render_chain_metrics(data: dict[str, object]) -> list[str]:
    if not isinstance(data, dict):
        raise ValueError("invalid chain metrics")
    lines: list[str] = []
    queue = data.get("queue")
    if queue is not None:
        if not isinstance(queue, list):
            raise ValueError("invalid chain queue")
        for entry in queue:
            if not isinstance(entry, dict) or "status" not in entry or "count" not in entry:
                raise ValueError("invalid chain queue entry")
            if entry["count"] is None:
                continue
            _number(entry["count"], "queue count")
            lines.append(_metric("melown_chain_queue", entry["count"], status=entry["status"]))

    _append_if_present(lines, data, "oldest_pending_age_seconds", "melown_chain_oldest_pending_age_seconds", null_as_nan=True)
    _append_if_present(lines, data, "last_delivery_timestamp_seconds", "melown_chain_last_delivery_timestamp_seconds", null_as_nan=True)
    _append_if_present(lines, data, "delivered_today", "melown_chain_delivered_today")

    if "delivery_delay_seconds" in data:
        delays = data.get("delivery_delay_seconds")
        if delays is None:
            delays = {"p50": None, "p95": None, "max": None}
        if not isinstance(delays, dict):
            raise ValueError("invalid chain delays")
        for key in ("p50", "p95", "max"):
            if key in delays:
                value = delays[key]
                if value is not None:
                    _number(value, key)
                lines.append(_metric("melown_chain_delivery_delay_seconds", value, statistic=key))
        if "samples" in delays and delays["samples"] is not None:
            _number(delays["samples"], "delay samples")
            lines.append(_metric("melown_chain_delivery_delay_samples", delays["samples"]))

    _append_if_present(lines, data, "events_today", "melown_chain_events_today")
    _append_if_present(lines, data, "last_webhook_timestamp_seconds", "melown_chain_last_webhook_timestamp_seconds", null_as_nan=True)

    workflow = data.get("workflow")
    if workflow is not None:
        if not isinstance(workflow, list):
            raise ValueError("invalid chain workflow")
        for entry in workflow:
            if not isinstance(entry, dict) or not {"workflow_id", "status", "count"} <= entry.keys():
                raise ValueError("invalid chain workflow entry")
            if entry["count"] is None:
                continue
            _number(entry["count"], "workflow count")
            lines.append(
                _metric(
                    "melown_chain_workflow_executions_today",
                    entry["count"],
                    workflow_id=entry["workflow_id"],
                    status=entry["status"],
                )
            )

    source = data.get("source")
    if source is not None:
        if not isinstance(source, list):
            raise ValueError("invalid chain source")
        for entry in source:
            if not isinstance(entry, dict) or "scope" not in entry:
                raise ValueError("invalid chain source entry")
            scope = entry["scope"]
            if "success" in entry and entry["success"] is not None:
                _number(entry["success"], "source success")
                lines.append(_metric("melown_chain_source_success", entry["success"], scope=scope))
            if "checked_timestamp_seconds" in entry:
                checked = entry["checked_timestamp_seconds"]
                if checked is not None:
                    _number(checked, "source checked timestamp")
                lines.append(_metric("melown_chain_source_checked_timestamp_seconds", checked, scope=scope))
    return lines


def state_path_for(output: str | Path) -> Path:
    output_path = Path(output)
    return output_path.with_name(output_path.name + ".state.json")


def lock_path_for(output: str | Path) -> Path:
    output_path = Path(output)
    return output_path.with_name(output_path.name + ".lock")


def _atomic_write(path: Path, text: str, mode: int, limit: int) -> None:
    encoded = text.encode("utf-8")
    if len(encoded) > limit:
        raise ValueError("output too large")
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(path.parent),
            prefix=f".{path.name}.tmp-",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(handle.name, mode)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _load_state(path: Path) -> dict[str, dict[str, object]]:
    try:
        if path.stat().st_size > STATE_LIMIT:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or not isinstance(value.get("components"), dict):
        return {}
    components: dict[str, dict[str, object]] = {}
    for name in COMPONENTS:
        entry = value["components"].get(name)
        if not isinstance(entry, dict):
            continue
        metrics = entry.get("metrics")
        timestamp = entry.get("last_success_timestamp")
        if not isinstance(metrics, list) or not all(isinstance(line, str) for line in metrics):
            continue
        if timestamp is not None:
            try:
                timestamp = float(timestamp)
            except (TypeError, ValueError):
                continue
        components[name] = {"metrics": metrics, "last_success_timestamp": timestamp}
    return components


def _state_text(components: dict[str, dict[str, object]]) -> str:
    return json.dumps({"version": 1, "components": components}, ensure_ascii=True, separators=(",", ":"))


@contextmanager
def _run_lock(path: Path):
    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise
        yield locked
    finally:
        if locked:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(handle)


def _validate_lines(lines: list[str]) -> None:
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError("invalid metric lines")
    if len("\n".join(lines).encode("utf-8")) > OUTPUT_LIMIT:
        raise ValueError("component too large")


def run_once(
    output: str,
    sql_path: str,
    db_container: str = "apps-postgres",
    db_name: str = "n8n",
    db_user: str = "n8n_dashboard_metrics",
    docker_socket: str = "/var/run/docker.sock",
    proc_root: str = "/proc",
    root_path: str = "/",
    now_fn: Callable[[], float] = time.time,
    monotonic_fn: Callable[[], float] = time.monotonic,
    run_deadline_seconds: float = RUN_DEADLINE_SECONDS,
) -> dict[str, object]:
    """Run one bounded collection and atomically publish its snapshot."""
    output_path = Path(output)
    lock_path = lock_path_for(output_path)
    with _run_lock(lock_path) as locked:
        if not locked:
            return {"skipped": True, "status": {name: 0 for name in COMPONENTS}}
        start = monotonic_fn()
        deadline = start + run_deadline_seconds
        timestamp = float(now_fn())
        old_state = _load_state(state_path_for(output_path))
        current_state: dict[str, dict[str, object]] = {}
        statuses: dict[str, int] = {}
        component_lines: dict[str, list[str]] = {}

        def collect_component(name: str, producer: Callable[[], object], renderer: Callable[[object], list[str]]) -> None:
            try:
                _check_deadline(deadline, monotonic_fn)
                value = producer()
                _check_deadline(deadline, monotonic_fn)
                lines = renderer(value)
                _check_deadline(deadline, monotonic_fn)
                _validate_lines(lines)
                component_lines[name] = lines
                statuses[name] = 1
                current_state[name] = {"metrics": lines, "last_success_timestamp": timestamp}
            except Exception:
                statuses[name] = 0
                previous = old_state.get(name, {})
                previous_lines = previous.get("metrics", [])
                if not isinstance(previous_lines, list) or not all(isinstance(line, str) for line in previous_lines):
                    previous_lines = []
                component_lines[name] = previous_lines
                current_state[name] = {
                    "metrics": previous_lines,
                    "last_success_timestamp": previous.get("last_success_timestamp"),
                }

        collect_component(
            "host",
            lambda: collect_host(proc_root=proc_root, root_path=root_path),
            render_host_metrics,
        )
        docker_client = DockerClient(socket_path=docker_socket, monotonic_fn=monotonic_fn)
        collect_component(
            "docker",
            lambda: collect_docker(docker_client, deadline, monotonic_fn=monotonic_fn),
            render_docker_metrics,
        )
        collect_component(
            "chain",
            lambda: collect_chain(db_container=db_container, db_name=db_name, db_user=db_user, sql_path=sql_path, deadline=deadline, monotonic_fn=monotonic_fn),
            render_chain_metrics,
        )

        lines: list[str] = _metadata_lines()
        for name in COMPONENTS:
            lines.extend(component_lines[name])
        for name in COMPONENTS:
            lines.append(_metric("melown_collector_success", statuses[name], component=name))
        for name in COMPONENTS:
            last_success = current_state[name].get("last_success_timestamp")
            if last_success is not None:
                lines.append(_metric("melown_collector_last_success_timestamp_seconds", last_success, component=name))
        duration = max(0.0, monotonic_fn() - start)
        lines.append(_metric("melown_collector_timestamp_seconds", timestamp))
        lines.append(_metric("melown_collector_duration_seconds", duration))
        output_text = "\n".join(lines) + "\n"
        state_text = _state_text(current_state)
        if len(state_text.encode("utf-8")) > STATE_LIMIT:
            raise ValueError("state too large")
        _atomic_write(output_path, output_text, 0o644, OUTPUT_LIMIT)
        _atomic_write(state_path_for(output_path), state_text, 0o600, STATE_LIMIT)
        return {"skipped": False, "status": statuses}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bounded one-shot Melown metrics collector")
    parser.add_argument("--output", required=True)
    parser.add_argument("--sql", required=True)
    parser.add_argument("--db-container", default="apps-postgres")
    parser.add_argument("--db-name", default="n8n")
    parser.add_argument("--db-user", default="n8n_dashboard_metrics")
    parser.add_argument("--docker-socket", default="/var/run/docker.sock")
    parser.add_argument("--proc-root", default="/proc")
    parser.add_argument("--root-path", default="/")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = parse_args(argv)
        run_once(
            output=args.output,
            sql_path=args.sql,
            db_container=args.db_container,
            db_name=args.db_name,
            db_user=args.db_user,
            docker_socket=args.docker_socket,
            proc_root=args.proc_root,
            root_path=args.root_path,
        )
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

