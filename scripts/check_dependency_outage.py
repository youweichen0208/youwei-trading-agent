"""Stop only app-local Alpha, verify readiness/real alerts, and restore it in finally."""

import json
import time

import httpx
from check_container_stack import COMPOSE, command


def main():
    alpha = command(*COMPOSE, "ps", "-q", "alpha-engine")
    assert alpha, "Start app-local with its monitoring overlay first"
    for service in ("alpha-engine", "yta-bff", "yta-web"):
        identity = command(*COMPOSE, "ps", "-q", service)
        project = command(
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "com.docker.compose.project"}}',
            identity,
        )
        assert project == "yta-app-local", "Only isolated app-local containers may be exercised"
    # Validate private configuration in-process; never print its values.
    command(
        *COMPOSE,
        "exec",
        "-T",
        "yta-bff",
        "node",
        "-e",
        """
if(process.env.YTA_ENV !== 'test' || process.env.YTA_DATA_MODE !== 'REPLAY'
 || !new URL(process.env.YTA_DATABASE_URL).pathname.endsWith('_test')) process.exit(1);
""",
    )
    phases = []
    started = time.monotonic()
    with httpx.Client(trust_env=False, timeout=5) as client:

        def ready():
            response = client.get("http://127.0.0.1:3300/api/yta/v1/ready")
            return response.status_code, response.json()

        def query(expression):
            response = client.get(
                "http://127.0.0.1:39090/api/v1/query", params={"query": expression}
            )
            response.raise_for_status()
            return response.json()["data"]["result"]

        def wait_for(predicate, timeout):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    if predicate():
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            raise RuntimeError("Dependency recovery/alert transition timed out")

        def healthy():
            status, body = ready()
            return (
                status == 200
                and body["data_mode"] == "REPLAY"
                and len(query('probe_success{job="yta-readiness"} == 1')) == 2
                and not query('ALERTS{alertname=~"YTA.*"}')
            )

        def record(phase):
            phases.append({"phase": phase, "elapsed_seconds": round(time.monotonic() - started, 2)})
            print(json.dumps(phases[-1]), flush=True)

        wait_for(healthy, 60)
        record("healthy")
        try:
            command("docker", "stop", "--time", "10", alpha)

            def unavailable():
                status, body = ready()
                return (
                    status == 503
                    and body["checks"]["alpha"] == "not_ready"
                    and body["checks"]["database"] == "ready"
                )

            wait_for(unavailable, 30)
            assert client.get("http://127.0.0.1:3300/api/yta/v1/health").status_code == 200
            record("alpha_unavailable_database_ready_bff_alive")
            wait_for(
                lambda: (
                    len(query('ALERTS{alertname="YTAServiceNotReady",alertstate="firing"}')) == 2
                ),
                180,
            )
            assert unavailable()
            record("both_readiness_alerts_firing")
        finally:
            command("docker", "start", alpha)
            wait_for(healthy, 75)
            record("recovered_alerts_resolved")
    print(
        json.dumps(
            {
                "phases": phases,
                "actual_dependency": "app-local alpha-engine",
                "database_stopped": False,
                "host_services_changed": False,
                "notifications_sent": 0,
                "deployment_qualification": "NOT_ASSESSED",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
