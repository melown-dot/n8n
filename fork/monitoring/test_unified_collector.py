import json
import os
import tempfile
import unittest
import fcntl
from types import SimpleNamespace
from pathlib import Path
from unittest import mock


try:
    from . import unified_collector as uc
    from .unified_collector import (
        collect_chain,
        collect_docker,
        collect_host,
        DockerClient,
        DOCKER_RESPONSE_LIMIT,
        DeadlineExceeded,
        render_docker_metrics,
        render_chain_metrics,
        run_once,
    )
except ImportError:  # pragma: no cover - supports direct execution from monitoring/
    import unified_collector as uc
    from unified_collector import (
        collect_chain,
        collect_docker,
        collect_host,
        DockerClient,
        DOCKER_RESPONSE_LIMIT,
        DeadlineExceeded,
        render_chain_metrics,
        render_docker_metrics,
        run_once,
    )


class HostCollectionTests(unittest.TestCase):
    def test_collect_host_keeps_guest_inclusive_first_eight_cpu_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory) / "proc"
            proc.mkdir()
            (proc / "stat").write_text(
                "cpu 100 20 30 400 5 6 7 8 9 10\n", encoding="ascii"
            )
            (proc / "meminfo").write_text(
                "MemTotal:       1000 kB\nMemAvailable:    600 kB\n",
                encoding="ascii",
            )
            (proc / "uptime").write_text("123.5 42.0\n", encoding="ascii")

            metrics = collect_host(
                proc_root=str(proc),
                root_path=directory,
                loadavg_fn=lambda: (2.5, 1.0, 0.5),
                clk_tck=100,
            )

            self.assertEqual(metrics["cpu_seconds_total"], {
                "user": 1.0,
                "nice": 0.2,
                "system": 0.3,
                "idle": 4.0,
                "iowait": 0.05,
                "irq": 0.06,
                "softirq": 0.07,
                "steal": 0.08,
            })
            self.assertEqual(metrics["memory_bytes"], {"total": 1_024_000, "available": 614_400})
            self.assertEqual(metrics["load1"], 2.5)
            self.assertEqual(metrics["uptime_seconds"], 123.5)
            self.assertEqual(metrics["disk_bytes"]["total"], os.statvfs(directory).f_frsize * os.statvfs(directory).f_blocks)


class DockerCollectionTests(unittest.TestCase):
    def test_running_and_stopped_containers_render_bounded_escaped_labels(self):
        class FakeDocker:
            def __init__(self):
                self.paths = []

            def get_json(self, path, deadline):
                self.paths.append(path)
                if path.endswith("/containers/json?all=true"):
                    return [
                        {"Id": "running-id", "Names": ['/' + 'api"\\line\n'], "State": "running", "RestartCount": 2},
                        {"Id": "stopped-id", "Names": ["/stopped"], "State": "exited", "RestartCount": 4},
                    ]
                if path.endswith("/containers/running-id/stats?stream=false&one-shot=true"):
                    return {
                        "cpu_stats": {"cpu_usage": {"total_usage": 2_000_000_000}},
                        "memory_stats": {
                            "usage": 1000,
                            "limit": 2000,
                            "stats": {"inactive_file": 200},
                        },
                    }
                raise AssertionError(path)

        client = FakeDocker()
        data = collect_docker(client, deadline=100.0, monotonic_fn=lambda: 1.0)
        text = "\n".join(render_docker_metrics(data))

        self.assertIn(r'melown_docker_running{container="api\"\\line\n"} 1', text)
        self.assertIn('melown_docker_restarts{container="stopped"} 4', text)
        self.assertIn('melown_docker_cpu_seconds_total{container="api', text)
        self.assertIn('melown_docker_memory_bytes{container="api', text)
        self.assertNotIn('melown_docker_cpu_seconds_total{container="stopped"}', text)
        self.assertNotIn('melown_docker_memory_bytes{container="stopped"}', text)
        self.assertLessEqual(max(len(line) for line in text.splitlines()), 256)

    def test_restart_count_falls_back_to_inspect_and_inventory_over_64_fails(self):
        paths = []

        class FakeDocker:
            def get_json(self, path, deadline):
                paths.append(path)
                if path.endswith("/containers/json?all=true"):
                    return [
                        {"Id": f"id-{index}", "Names": [f"/c-{index}"], "State": "exited"}
                        for index in range(65)
                    ]
                if path.endswith("/json"):
                    return {"RestartCount": 0}
                raise AssertionError(path)

        with self.assertRaises(ValueError):
            collect_docker(FakeDocker(), deadline=100.0, monotonic_fn=lambda: 1.0)
        self.assertEqual(sum(path.endswith("/json") for path in paths), 0)


class ChainCollectionTests(unittest.TestCase):
    def test_psql_contract_renders_empty_queue_and_nulls_as_nan(self):
        payload = {
            "queue": [],
            "oldest_pending_age_seconds": None,
            "last_delivery_timestamp_seconds": None,
            "delivered_today": 0,
            "delivery_delay_seconds": {"p50": None, "p95": 1.5, "max": None, "samples": 3},
            "events_today": 0,
            "last_webhook_timestamp_seconds": None,
            "workflow": [{"workflow_id": "wf-1", "status": "success", "count": 2}],
            "source": [{"scope": "crm", "success": 1, "checked_timestamp_seconds": None}],
        }
        with tempfile.TemporaryDirectory() as directory:
            sql_path = Path(directory) / "unified-chain.sql"
            sql_path.write_text("select 1;\n", encoding="utf-8")
            with mock.patch(
                "monitoring.unified_collector.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=__import__("json").dumps(payload), stderr=""),
            ) as run:
                data = collect_chain(
                    str(sql_path),
                    "apps-postgres",
                    "n8n",
                    "n8n_dashboard_metrics",
                    deadline=100.0,
                    monotonic_fn=lambda: 1.0,
                )

        text = "\n".join(render_chain_metrics(data))
        self.assertNotIn("melown_chain_queue", text)
        self.assertIn("melown_chain_oldest_pending_age_seconds NaN", text)
        self.assertIn("melown_chain_last_delivery_timestamp_seconds NaN", text)
        self.assertIn("melown_chain_delivered_today 0", text)
        self.assertIn('melown_chain_delivery_delay_seconds{statistic="p50"} NaN', text)
        self.assertIn('melown_chain_delivery_delay_seconds{statistic="p95"} 1.5', text)
        self.assertIn('melown_chain_delivery_delay_seconds{statistic="max"} NaN', text)
        self.assertIn("melown_chain_delivery_delay_samples 3", text)
        self.assertIn('melown_chain_workflow_executions_today{workflow_id="wf-1",status="success"} 2', text)
        self.assertIn('melown_chain_source_checked_timestamp_seconds{scope="crm"} NaN', text)
        run.assert_called_once_with(
            [
                "docker",
                "exec",
                "-i",
                "apps-postgres",
                "psql",
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "n8n_dashboard_metrics",
                "-d",
                "n8n",
            ],
            input="select 1;\n",
            text=True,
            capture_output=True,
            timeout=5,
        )

    def test_unavailable_counters_are_omitted_and_multiple_json_values_fail(self):
        data = {
            "delivered_today": None,
            "events_today": None,
            "oldest_pending_age_seconds": None,
            "delivery_delay_seconds": None,
        }
        text = "\n".join(render_chain_metrics(data))
        self.assertNotIn("melown_chain_delivered_today", text)
        self.assertNotIn("melown_chain_events_today", text)
        self.assertIn("melown_chain_oldest_pending_age_seconds NaN", text)
        self.assertIn('melown_chain_delivery_delay_seconds{statistic="p50"} NaN', text)

        with tempfile.TemporaryDirectory() as directory:
            sql_path = Path(directory) / "unified-chain.sql"
            sql_path.write_text("select 1;", encoding="utf-8")
            with mock.patch(
                "monitoring.unified_collector.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout='{"ok":1} {"extra":2}', stderr=""),
            ):
                with self.assertRaises(ValueError):
                    collect_chain(str(sql_path), "db", "n8n", "role", deadline=100.0, monotonic_fn=lambda: 1.0)


class TransportAndBoundTests(unittest.TestCase):
    def test_docker_transport_is_get_only_bounded_and_deadline_aware(self):
        calls = []

        class Response:
            status = 200

            def read(self, size):
                self.size = size
                return b"{}"

        class Connection:
            def request(self, method, path, headers):
                calls.append((method, path, headers))

            def getresponse(self):
                return Response()

            def close(self):
                calls.append(("close",))

        client = DockerClient(
            socket_path="/tmp/docker.sock",
            monotonic_fn=lambda: 10.0,
            connection_factory=lambda path, timeout: Connection(),
        )
        self.assertEqual(client.get_json("/v1.47/containers/json?all=true", deadline=11.0), {})
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[0][1], "/v1.47/containers/json?all=true")
        self.assertEqual(calls[0][2]["Host"], "localhost")
        self.assertEqual(calls[-1], ("close",))
        with self.assertRaises(DeadlineExceeded):
            client.get_json("/v1.47/containers/json?all=true", deadline=10.0)

    def test_docker_response_over_limit_is_rejected(self):
        class Response:
            status = 200

            def read(self, size):
                return b"x" * (DOCKER_RESPONSE_LIMIT + 1)

        class Connection:
            def request(self, method, path, headers):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        client = DockerClient(connection_factory=lambda path, timeout: Connection())
        with self.assertRaises(ValueError):
            client.get_json("/v1.47/containers/json?all=true", deadline=None)

    def test_oversize_component_is_marked_failed_without_oversize_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unified.prom"
            sql = Path(directory) / "unified-chain.sql"
            sql.write_text("select 1;", encoding="utf-8")
            host = RunnerTests()._success_data()[0]
            docker = RunnerTests()._success_data()[1]
            oversized_chain = {"queue": [{"status": "s" * 100, "count": 1}] * 3000}
            with mock.patch.object(uc, "collect_host", return_value=host), mock.patch.object(
                uc, "collect_docker", return_value=docker
            ), mock.patch.object(uc, "collect_chain", return_value=oversized_chain):
                result = run_once(
                    output=str(output), sql_path=str(sql), db_container="db", db_name="n8n", db_user="role", docker_socket="sock", now_fn=lambda: 1.0, monotonic_fn=lambda: 2.0
                )
            text = output.read_text(encoding="utf-8")
            self.assertEqual(result["status"]["chain"], 0)
            self.assertLessEqual(output.stat().st_size, 256 * 1024)
            self.assertNotIn("s" * 100, text)

    def test_locked_output_skips_without_partial_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unified.prom"
            output.write_text("old\n", encoding="utf-8")
            lock_path = output.with_name(output.name + ".lock")
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = run_once(
                    output=str(output), sql_path=str(output), db_container="db", db_name="n8n", db_user="role", docker_socket="sock", now_fn=lambda: 1.0, monotonic_fn=lambda: 2.0
                )
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            self.assertTrue(result["skipped"])
            self.assertEqual(output.read_text(encoding="utf-8"), "old\n")


class ValidationAndFailureTests(unittest.TestCase):
    def _proc_tree(self, directory, stat="cpu 1 2 3 4 5 6 7 8\n", mem="MemTotal: 1 kB\nMemAvailable: 1 kB\n", uptime="1.0 0.0\n"):
        proc = Path(directory) / "proc"
        proc.mkdir()
        (proc / "stat").write_text(stat, encoding="ascii")
        (proc / "meminfo").write_text(mem, encoding="ascii")
        (proc / "uptime").write_text(uptime, encoding="ascii")
        return proc

    def test_collect_host_rejects_malformed_cpu_memory_and_uptime_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = self._proc_tree(directory, stat="")
            with self.assertRaises(ValueError):
                collect_host(proc_root=str(proc), root_path=directory, clk_tck=100)

        with tempfile.TemporaryDirectory() as directory:
            proc = self._proc_tree(directory, stat="notcpu 1 2 3 4 5 6 7 8\n")
            with self.assertRaises(ValueError):
                collect_host(proc_root=str(proc), root_path=directory, clk_tck=100)

        with tempfile.TemporaryDirectory() as directory:
            proc = self._proc_tree(directory, stat="cpu 1 2 3\n")
            with self.assertRaises(ValueError):
                collect_host(proc_root=str(proc), root_path=directory, clk_tck=100)

        with tempfile.TemporaryDirectory() as directory:
            proc = self._proc_tree(directory, mem="Other: 1 kB\nMemTotal:\n")
            with self.assertRaises(ValueError):
                collect_host(proc_root=str(proc), root_path=directory, clk_tck=100)

        with tempfile.TemporaryDirectory() as directory:
            proc = self._proc_tree(directory, uptime="")
            with self.assertRaises(ValueError):
                collect_host(proc_root=str(proc), root_path=directory, clk_tck=100)

        with tempfile.TemporaryDirectory() as directory:
            proc = self._proc_tree(directory)
            with mock.patch.object(uc.os, "sysconf", return_value=100):
                data = collect_host(proc_root=str(proc), root_path=directory)
            self.assertEqual(data["uptime_seconds"], 1.0)
            with self.assertRaises(ValueError):
                collect_host(proc_root=str(proc), root_path=directory, clk_tck=0)

    def test_render_host_rejects_missing_cpu_memory_disk_and_scalars(self):
        valid = {
            "cpu_seconds_total": {mode: 1.0 for mode in uc.CPU_MODES},
            "memory_bytes": {kind: 1 for kind in ("total", "available")},
            "disk_bytes": {kind: 1 for kind in ("total", "available")},
            "load1": 1.0,
            "uptime_seconds": 1.0,
        }
        for field in ("cpu_seconds_total", "memory_bytes", "disk_bytes"):
            invalid = dict(valid)
            invalid[field] = None
            with self.assertRaises(ValueError):
                uc.render_host_metrics(invalid)
        for field, key in (("cpu_seconds_total", "user"), ("memory_bytes", "total"), ("disk_bytes", "total")):
            invalid = {**valid, field: dict(valid[field])}
            invalid[field].pop(key)
            with self.assertRaises(ValueError):
                uc.render_host_metrics(invalid)
        for field in ("load1", "uptime_seconds"):
            invalid = dict(valid)
            invalid.pop(field)
            with self.assertRaises(ValueError):
                uc.render_host_metrics(invalid)

    def test_docker_rejects_invalid_cpu_and_memory_numbers(self):
        class FakeDocker:
            def __init__(self, stats):
                self.stats = stats

            def get_json(self, path, deadline):
                if path.endswith("/containers/json?all=true"):
                    return [{"Id": "id", "Names": ["/worker"], "State": "running", "RestartCount": 0}]
                return self.stats

        invalid_cpu = {
            "cpu_stats": {"cpu_usage": {"total_usage": "bad"}},
            "memory_stats": {"usage": 10, "limit": 20},
        }
        with self.assertRaises(ValueError):
            collect_docker(FakeDocker(invalid_cpu), deadline=100.0, monotonic_fn=lambda: 1.0)

        invalid_memory = {
            "cpu_stats": {"cpu_usage": {"total_usage": 1_000_000_000}},
            "memory_stats": {"usage": float("nan"), "limit": 20},
        }
        with self.assertRaises(ValueError):
            collect_docker(FakeDocker(invalid_memory), deadline=100.0, monotonic_fn=lambda: 1.0)

    def test_docker_inspect_fallback_duplicate_names_and_memory_cache_fallback(self):
        class FakeDocker:
            def get_json(self, path, deadline):
                if path.endswith("/containers/json?all=true"):
                    return [
                        {"Id": "id1", "Names": ["/same"], "State": "exited"},
                        {"Id": "id2", "Names": ["/same"], "State": "running"},
                    ]
                if path.endswith("/containers/id1/json"):
                    return {"State": {"RestartCount": 3}}
                if path.endswith("/containers/id2/json"):
                    return {"RestartCount": 4}
                if path.endswith("/containers/id2/stats?stream=false&one-shot=true"):
                    return {
                        "cpu_stats": {"cpu_usage": {"total_usage": 1_000_000_000}},
                        "memory_stats": {
                            "usage": 10,
                            "limit": 20,
                            "stats": {"cache": 3},
                        },
                    }
                raise AssertionError(path)

        data = collect_docker(FakeDocker(), deadline=100.0, monotonic_fn=lambda: 1.0)
        self.assertEqual([item["container"] for item in data["containers"]], ["same", "same_2"])
        self.assertEqual(data["containers"][0]["restarts"], 3)
        self.assertEqual(data["containers"][1]["restarts"], 4)
        self.assertEqual(data["containers"][1]["memory_bytes"], 7.0)

    def test_docker_http_errors_and_bad_json_close_connections(self):
        class Response:
            def __init__(self, status, body):
                self.status = status
                self.body = body

            def read(self, size):
                return self.body

        class Connection:
            def __init__(self, response):
                self.response = response
                self.closed = False

            def request(self, method, path, headers):
                self.request_args = (method, path, headers)

            def getresponse(self):
                return self.response

            def close(self):
                self.closed = True

        for response in (Response(503, b"{}"), Response(200, b"not-json")):
            connection = Connection(response)
            client = DockerClient(connection_factory=lambda path, timeout, connection=connection: connection)
            with self.assertRaises(ValueError):
                client.get_json("/v1.47/containers/json?all=true", deadline=None)
            self.assertTrue(connection.closed)

    def test_unix_socket_connect_closes_socket_after_failure(self):
        class BrokenSocket:
            def __init__(self):
                self.closed = False

            def settimeout(self, timeout):
                self.timeout = timeout

            def connect(self, path):
                raise OSError("missing socket")

            def close(self):
                self.closed = True

        broken = BrokenSocket()
        with mock.patch.object(uc.socket, "socket", return_value=broken):
            with self.assertRaises(OSError):
                uc.UnixSocketHTTPConnection("/missing.sock", 1.0).connect()
        self.assertTrue(broken.closed)

    def test_chain_source_numeric_failures_are_visible(self):
        with self.assertRaises(ValueError):
            render_chain_metrics({"source": [{"scope": "crm", "success": "bad"}]})
        with self.assertRaises(ValueError):
            render_chain_metrics({"source": [{"scope": "crm", "success": float("nan")}]})
        with self.assertRaises(ValueError):
            render_chain_metrics({"source": [{"scope": "crm", "checked_timestamp_seconds": True}]})
        with self.assertRaises(ValueError):
            render_chain_metrics({"queue": {}})
        with self.assertRaises(ValueError):
            render_chain_metrics({"workflow": [{"workflow_id": "wf"}]})
        with self.assertRaises(ValueError):
            render_chain_metrics({"source": [{}]})


class CliAndCacheTests(unittest.TestCase):
    def _success(self):
        return RunnerTests()._success_data()

    def _write_success(self, output, sql, now=100.0):
        host, docker, chain = self._success()
        sql.write_text("select 1;", encoding="utf-8")
        with mock.patch.object(uc, "collect_host", return_value=host), mock.patch.object(
            uc, "collect_docker", return_value=docker
        ), mock.patch.object(uc, "collect_chain", return_value=chain):
            return run_once(
                output=str(output),
                sql_path=str(sql),
                db_container="db",
                db_name="n8n",
                db_user="role",
                docker_socket="sock",
                now_fn=lambda: now,
                monotonic_fn=lambda: now / 100,
            )

    def test_parse_args_defaults_and_overrides(self):
        defaults = uc.parse_args(["--output", "out", "--sql", "query.sql"])
        self.assertEqual(defaults.db_container, "apps-postgres")
        self.assertEqual(defaults.db_name, "n8n")
        self.assertEqual(defaults.db_user, "n8n_dashboard_metrics")
        self.assertEqual(defaults.docker_socket, "/var/run/docker.sock")
        self.assertEqual(defaults.proc_root, "/proc")
        self.assertEqual(defaults.root_path, "/")
        overrides = uc.parse_args(
            [
                "--output", "custom.prom", "--sql", "custom.sql", "--db-container", "db",
                "--db-name", "metrics", "--db-user", "role", "--docker-socket", "sock",
                "--proc-root", "proc", "--root-path", "root",
            ]
        )
        self.assertEqual(overrides.output, "custom.prom")
        self.assertEqual(overrides.sql, "custom.sql")
        self.assertEqual(overrides.db_container, "db")
        self.assertEqual(overrides.root_path, "root")

    def test_main_returns_zero_for_success_and_skipped_and_one_for_failure(self):
        with mock.patch.object(uc, "run_once", return_value={"skipped": False}) as run:
            self.assertEqual(uc.main(["--output", "out", "--sql", "query.sql"]), 0)
            self.assertEqual(run.call_args.kwargs["db_user"], "n8n_dashboard_metrics")
        with mock.patch.object(uc, "run_once", return_value={"skipped": True}):
            self.assertEqual(uc.main(["--output", "out", "--sql", "query.sql"]), 0)
        with mock.patch.object(uc, "run_once", side_effect=RuntimeError("failure")):
            self.assertEqual(uc.main(["--output", "out", "--sql", "query.sql"]), 1)

    def test_corrupt_and_oversized_state_are_rejected_without_fake_stale_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unified.prom"
            sql = Path(directory) / "unified-chain.sql"
            self._write_success(output, sql)
            state = uc.state_path_for(output)
            host, docker, chain = self._success()
            for raw in (b"not-json", b"x" * (uc.STATE_LIMIT + 1)):
                state.write_bytes(raw)
                with mock.patch.object(uc, "collect_host", side_effect=RuntimeError("host unavailable")), mock.patch.object(
                    uc, "collect_docker", return_value=docker
                ), mock.patch.object(uc, "collect_chain", return_value=chain):
                    result = run_once(
                        output=str(output), sql_path=str(sql), db_container="db", db_name="n8n", db_user="role",
                        docker_socket="sock", now_fn=lambda: 200.0, monotonic_fn=lambda: 2.0,
                    )
                text = output.read_text(encoding="utf-8")
                self.assertEqual(result["status"]["host"], 0)
                self.assertNotIn('melown_host_cpu_seconds_total{mode="user"} 1.0', text)

    def test_valid_state_keeps_only_well_formed_component_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unified.prom"
            sql = Path(directory) / "unified-chain.sql"
            sql.write_text("select 1;", encoding="utf-8")
            uc.state_path_for(output).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "components": {
                            "host": {"metrics": ["host-old"], "last_success_timestamp": "bad"},
                            "docker": {"metrics": "not-a-list", "last_success_timestamp": 10},
                            "chain": {"metrics": ["chain-old"], "last_success_timestamp": "123"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(uc, "collect_host", side_effect=RuntimeError("host unavailable")), mock.patch.object(
                uc, "collect_docker", side_effect=RuntimeError("docker unavailable")
            ), mock.patch.object(uc, "collect_chain", side_effect=RuntimeError("chain unavailable")):
                result = run_once(
                    output=str(output), sql_path=str(sql), db_container="db", db_name="n8n", db_user="role",
                    docker_socket="sock", now_fn=lambda: 200.0, monotonic_fn=lambda: 2.0,
                )
            text = output.read_text(encoding="utf-8")
            self.assertEqual(result["status"], {"host": 0, "docker": 0, "chain": 0})
            self.assertNotIn("host-old", text)
            self.assertNotIn("not-a-list", text)
            self.assertIn("chain-old", text)
            self.assertIn('melown_collector_last_success_timestamp_seconds{component="chain"} 123.0', text)


class AtomicOutputTests(unittest.TestCase):
    def test_atomic_replace_failure_keeps_previous_snapshot_and_cleans_tempfile(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unified.prom"
            sql = Path(directory) / "unified-chain.sql"
            output.write_text("old\n", encoding="utf-8")
            host, docker, chain = RunnerTests()._success_data()
            sql.write_text("select 1;", encoding="utf-8")
            with mock.patch.object(uc, "collect_host", return_value=host), mock.patch.object(
                uc, "collect_docker", return_value=docker
            ), mock.patch.object(uc, "collect_chain", return_value=chain), mock.patch.object(
                uc.os, "replace", side_effect=OSError("replace failed")
            ):
                with self.assertRaises(OSError):
                    run_once(
                        output=str(output), sql_path=str(sql), db_container="db", db_name="n8n", db_user="role",
                        docker_socket="sock", now_fn=lambda: 100.0, monotonic_fn=lambda: 1.0,
                    )
            self.assertEqual(output.read_text(encoding="utf-8"), "old\n")
            self.assertFalse(list(Path(directory).glob(".*.tmp-*")))


class RunnerTests(unittest.TestCase):
    def _success_data(self):
        return (
            {
                "cpu_seconds_total": {"user": 1.0, "nice": 0.0, "system": 2.0, "idle": 3.0, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0},
                "memory_bytes": {"total": 100, "available": 40},
                "disk_bytes": {"total": 1000, "available": 900},
                "load1": 0.5,
                "uptime_seconds": 10.0,
            },
            {"containers": [{"container": "n8n", "running": True, "restarts": 1, "cpu_seconds": 2.0, "memory_bytes": 300, "memory_limit_bytes": 500}]},
            {"queue": [{"status": "pending", "count": 2}], "delivered_today": 3},
        )

    def test_run_once_writes_all_components_and_private_state_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unified.prom"
            sql = Path(directory) / "unified-chain.sql"
            sql.write_text("select 1;", encoding="utf-8")
            host, docker, chain = self._success_data()
            with mock.patch.object(uc, "collect_host", return_value=host), mock.patch.object(
                uc, "collect_docker", return_value=docker
            ), mock.patch.object(uc, "collect_chain", return_value=chain):
                result = run_once(
                    output=str(output),
                    sql_path=str(sql),
                    db_container="apps-postgres",
                    db_name="n8n",
                    db_user="n8n_dashboard_metrics",
                    docker_socket="/var/run/docker.sock",
                    now_fn=lambda: 100.0,
                    monotonic_fn=lambda: 1.0,
                )

            text = output.read_text(encoding="utf-8")
            self.assertEqual(result["status"], {"host": 1, "docker": 1, "chain": 1})
            self.assertIn('melown_host_cpu_seconds_total{mode="user"} 1.0', text)
            self.assertIn('melown_docker_running{container="n8n"} 1', text)
            self.assertIn('melown_chain_queue{status="pending"} 2', text)
            self.assertIn('melown_collector_success{component="chain"} 1', text)
            self.assertIn("melown_collector_timestamp_seconds 100.0", text)
            self.assertLessEqual(output.stat().st_size, 256 * 1024)
            state = output.with_name(output.name + ".state.json")
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            self.assertEqual(output.stat().st_mode & 0o777, 0o644)
            self.assertFalse(list(Path(directory).glob(".*.tmp-*")))

    def test_output_declares_each_metric_family_once_with_help_and_type(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unified.prom"
            sql = Path(directory) / "unified-chain.sql"
            sql.write_text("select 1;", encoding="utf-8")
            host, docker, chain = self._success_data()
            with mock.patch.object(uc, "collect_host", return_value=host), mock.patch.object(
                uc, "collect_docker", return_value=docker
            ), mock.patch.object(uc, "collect_chain", return_value=chain):
                run_once(
                    output=str(output),
                    sql_path=str(sql),
                    db_container="db",
                    db_name="n8n",
                    db_user="role",
                    docker_socket="sock",
                    now_fn=lambda: 100.0,
                    monotonic_fn=lambda: 1.0,
                )

            lines = output.read_text(encoding="utf-8").splitlines()
            samples = {
                line.split("{", 1)[0].split(" ", 1)[0]
                for line in lines
                if line and not line.startswith("#")
            }
            help_families = {
                line.split(" ", 3)[2]
                for line in lines
                if line.startswith("# HELP ")
            }
            type_families = {
                line.split(" ", 3)[2]
                for line in lines
                if line.startswith("# TYPE ")
            }
            self.assertTrue(samples)
            self.assertTrue(samples <= help_families)
            self.assertTrue(samples <= type_families)
            self.assertEqual(help_families, set(uc.METRIC_METADATA))
            self.assertEqual(type_families, set(uc.METRIC_METADATA))
            self.assertEqual(
                sum(line.startswith("# TYPE melown_host_cpu_seconds_total counter") for line in lines),
                1,
            )
            self.assertEqual(
                sum(line.startswith("# TYPE melown_docker_cpu_seconds_total counter") for line in lines),
                1,
            )
            self.assertTrue(all("untyped" not in line for line in lines if line.startswith("# TYPE ")))

    def test_component_failure_retains_last_good_metrics_and_first_failure_has_no_fake_zeroes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unified.prom"
            sql = Path(directory) / "unified-chain.sql"
            sql.write_text("select 1;", encoding="utf-8")
            host, docker, chain = self._success_data()
            with mock.patch.object(uc, "collect_host", return_value=host), mock.patch.object(
                uc, "collect_docker", return_value=docker
            ), mock.patch.object(uc, "collect_chain", return_value=chain):
                run_once(
                    output=str(output), sql_path=str(sql), db_container="db", db_name="n8n", db_user="role", docker_socket="sock", now_fn=lambda: 100.0, monotonic_fn=lambda: 1.0
                )
            with mock.patch.object(uc, "collect_host", return_value=host), mock.patch.object(
                uc, "collect_docker", side_effect=RuntimeError("unavailable")
            ), mock.patch.object(uc, "collect_chain", return_value=chain):
                run_once(
                    output=str(output), sql_path=str(sql), db_container="db", db_name="n8n", db_user="role", docker_socket="sock", now_fn=lambda: 200.0, monotonic_fn=lambda: 2.0
                )
            text = output.read_text(encoding="utf-8")
            self.assertIn('melown_docker_running{container="n8n"} 1', text)
            self.assertIn('melown_collector_success{component="docker"} 0', text)
            self.assertIn('melown_collector_last_success_timestamp_seconds{component="docker"} 100.0', text)

            fresh_output = Path(directory) / "fresh.prom"
            with mock.patch.object(uc, "collect_host", side_effect=RuntimeError("unavailable")), mock.patch.object(
                uc, "collect_docker", side_effect=RuntimeError("unavailable")
            ), mock.patch.object(uc, "collect_chain", side_effect=RuntimeError("unavailable")):
                run_once(
                    output=str(fresh_output), sql_path=str(sql), db_container="db", db_name="n8n", db_user="role", docker_socket="sock", now_fn=lambda: 300.0, monotonic_fn=lambda: 3.0
                )
            fresh_text = fresh_output.read_text(encoding="utf-8")
            fresh_samples = "\n".join(line for line in fresh_text.splitlines() if not line.startswith("#"))
            self.assertNotIn("melown_host_cpu_seconds_total", fresh_samples)
            self.assertNotIn("melown_docker_running", fresh_samples)
            self.assertNotIn("melown_chain_queue", fresh_samples)
            self.assertIn('melown_collector_success{component="host"} 0', fresh_text)


if __name__ == "__main__":
    unittest.main()
