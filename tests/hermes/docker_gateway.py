"""Actual Compose transport for the shared opt-in system acceptance scenario."""

import json
import socket
from pathlib import Path
from types import SimpleNamespace

from container_integration import docker, wait_ready


def reserve_three_ports():
    for _ in range(100):
        sockets = []
        port = 0
        try:
            for offset in range(3):
                sock = socket.socket()
                sockets.append(sock)
                sock.bind(("127.0.0.1", 0 if offset == 0 else port + offset))
                if offset == 0:
                    port = sock.getsockname()[1]
                    if port > 65533:
                        raise OSError("port range overflow")
            return port, sockets
        except OSError:
            for sock in sockets:
                sock.close()
    raise AssertionError("No free adjacent port triple")


class DockerGateway:
    def __init__(self, prepared, profile, image):
        self.role = profile["role"]
        self.compose = prepared["composeFile"]
        self.home = Path(prepared["directory"]) / "profiles" / profile["id"] / "home"
        self.api_key = json.loads((self.home.parent / "credentials.json").read_text())["apiKey"]
        self.service = "profile-" + profile["id"]
        config = json.loads(Path(self.compose).read_text())["services"][self.service]
        self.container = config["container_name"]
        self.port = int(config["ports"][0]["published"])
        self.image = image
        self.original_id = None
        self.process = None

    def start(self):
        if self.original_id:
            docker("start", self.original_id)
        else:
            docker(
                "compose",
                "-f",
                self.compose,
                "up",
                "-d",
                "--no-deps",
                "--no-recreate",
                "--no-build",
                "--pull",
                "never",
                self.service,
            )
        wait_ready(
            {
                "container": self.container,
                "origin": f"http://127.0.0.1:{self.port}",
                "apiKey": self.api_key,
            }
        )
        observed = json.loads(docker("inspect", self.container))[0]
        assert observed["Image"] == self.image
        if self.original_id:
            assert observed["Id"] == self.original_id
            assert observed["State"]["Pid"] != self.process.pid
        self.original_id = observed["Id"]
        self.process = SimpleNamespace(pid=observed["State"]["Pid"])

    def stop(self):
        if self.original_id:
            docker("stop", "--timeout", "30", self.original_id)
