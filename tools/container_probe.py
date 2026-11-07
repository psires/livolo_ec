#!/usr/bin/env python3
"""Probe an isolated running release container without opening any radio."""

import argparse
import asyncio
from importlib.metadata import version
import json
import re
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

TOKEN = "isolated-container-test-token-0000000000000000"
DEVICE = "10203041"


def installed_versions():
    """Read explicit runtime identities, permitting only known legacy package fallback.

    @return Software/compatibility fields; raises AssertionError on inconsistent installation.
    """
    import livolod

    daemon = version("livolo-ec-daemon")
    assert daemon == livolod.__version__
    compatibility = getattr(livolod, "FIRMWARE_COMPAT_VERSION", None)
    if compatibility is None:
        assert daemon in ("0.12", "0.13"), "Unknown legacy version contract"
        compatibility = daemon
    assert re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", compatibility)
    return {"daemon_version": daemon, "firmware_compat_version": compatibility}


def request(url, body=None, headers=None):
    """Return HTTP status and bytes, including expected HTTP rejection responses.

    @param url Local test endpoint.
    @param body Optional JSON request payload.
    @param headers Optional authentication and origin headers.
    """
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers or {},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


async def websocket():
    """Verify authenticated push snapshots from the installed aiohttp server."""
    import aiohttp

    async with aiohttp.ClientSession(
        headers={"Authorization": "Bearer " + TOKEN}
    ) as session:
        async with session.ws_connect(
            "http://127.0.0.1:8090/api/v1/events", timeout=5
        ) as ws:
            value = await ws.receive_json(timeout=5)
            assert value["api_version"] == 1 and "data" in value


def mqtt_online():
    """Verify retained service status against the isolated broker."""
    import paho.mqtt.client as mqtt

    event = threading.Event()
    values = {}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="release-probe")

    def connected(client, userdata, flags, reason, properties):
        """Subscribe once the isolated broker accepts the connection.

        @param client Connected Paho test client.
        @param userdata Unused callback context.
        @param flags Unused connection flags.
        @param reason Broker connection result.
        @param properties Unused MQTT properties.
        """
        assert not reason.is_failure
        client.subscribe("container-test/simulated/bridge/state", 1)

    def received(client, userdata, message):
        """Record the retained bridge status for the waiting test.

        @param client Unused Paho test client.
        @param userdata Unused callback context.
        @param message Received status payload and retained flag.
        """
        # Registry writes briefly mark the bridge offline while peers reconcile.
        # Wait for recovery, then re-subscribe to prove the broker retained online.
        if message.payload == b"online":
            if message.retain:
                values.update(payload="online", retained=True)
                event.set()
            else:
                client.subscribe("container-test/simulated/bridge/state", 1)

    client.on_connect = connected
    client.on_message = received
    client.connect("broker", 1883, 30)
    client.loop_start()
    try:
        assert event.wait(15), "Bridge did not recover to retained online status"
        assert values == {"payload": "online", "retained": True}, values
    finally:
        client.disconnect()
        client.loop_stop()


def firmware_preflight(base, headers, snapshot):
    """Verify the installed release's advisory API without starting a firmware job.

    @param base Isolated native API URL.
    @param headers Test bearer credentials.
    @param snapshot Current state from a simulator or missing-radio test container.
    @return Whether this release exposes the additive preflight capability.
    """
    firmware = snapshot["firmware_update"]
    if not firmware.get("preflight_supported"):
        # Previous release images remain part of the persisted-data upgrade test.
        assert version("livolo-ec-daemon") == "0.12"
        return False
    endpoint = base + "/api/v1/firmware/preflight"
    assert request(endpoint)[0] == 401
    status, body = request(endpoint, headers=headers)
    report = json.loads(body)
    assert status == 200 and report["schema"] == 1, report
    assert report["operation"] == "update"
    assert report["executable"] is False and report["blocked_reason"], report
    assert report["candidate_id"] is None, "Public catalog must remain empty"
    assert request(endpoint + "?path=/tmp/firmware", headers=headers)[0] == 400
    status, body = request(base + "/api/v1/snapshot", headers=headers)
    after = json.loads(body)["data"]["firmware_update"]
    assert status == 200
    for key in ("request_id", "status", "stage", "in_progress"):
        assert after[key] == firmware[key], (key, firmware, after)
    return True


def check(kind, seed, local_catalog=False):
    """Check live authentication, registry writes, persistence and wrapper UID.

    @param kind Standalone simulated daemon or HA missing-radio wrapper.
    @param seed Whether to create a registry entry before the replacement test.
    @param local_catalog Whether the HA app must use persistent catalog/no Web options.
    """
    headers = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}
    base = "http://127.0.0.1:8090"
    deadline = time.monotonic() + 30
    while True:
        try:
            status, body = request(base + "/api/v1/snapshot", headers=headers)
            assert status == 200
            snapshot = json.loads(body)["data"]
            mqtt_ready = (
                kind == "ha"
                or json.loads(request("http://127.0.0.1:8080/api/state")[1])["mqtt"][
                    "connected"
                ]
            )
            if kind == "ha" or (snapshot["ready"] and mqtt_ready):
                break
        except (OSError, AssertionError, ValueError, KeyError):
            pass
        if time.monotonic() >= deadline:
            raise AssertionError("API startup timeout")
        time.sleep(0.2)
    assert request(base + "/api/v1/snapshot")[0] == 401
    assert snapshot["gateway"] == "10203040" and snapshot["tag"] == "42"
    assert snapshot["simulated"] == (kind == "standalone")
    versions = installed_versions()
    code, body = request(base + "/api/v1/info", headers=headers)
    info = json.loads(body)
    assert code == 200 and info["host_version"] == versions["firmware_compat_version"]
    if versions["daemon_version"] not in ("0.12", "0.13"):
        for key, value in dict(versions, api_version=1, uart_protocol=3).items():
            assert info[key] == value
            assert snapshot["diagnostics"][key] == value
        for name in (
            "livolod",
            "livolod-firmware",
            "livolod-firmware-manage",
            "livolod-commission",
        ):
            assert json.loads(
                subprocess.check_output([name, "--version-info"], text=True)
            ) == dict(versions, api_version=1, uart_protocol=3)
    if kind == "ha":
        assert not snapshot["ready"], "Missing radio must not be ready"
    preflight_supported = firmware_preflight(base, headers, snapshot)
    status_lines = Path("/proc/1/status").read_text().splitlines()
    assert (
        next(line for line in status_lines if line.startswith("Uid:")).split()[1:]
        == ["10001"] * 4
    )
    if seed:
        code, body = request(
            base + "/api/v1/commands",
            {
                "action": "add",
                "payload": {
                    "request_id": "release-add",
                    "device": DEVICE,
                    "name": "Container persistence test",
                    "outputs": 1,
                    "suffix": "0001",
                },
            },
            headers,
        )
        assert code == 200 and json.loads(body)["status"] == "confirmed", body
    else:
        devices = snapshot["devices"]
        assert len(devices) == 1 and devices[0]["id"] == DEVICE, devices
        assert devices[0]["name"] == "Container persistence test"
        code, body = request(base + "/api/v1/commands/release-add", headers=headers)
        assert code == 200 and json.loads(body)["status"] == "confirmed", body
    asyncio.run(websocket())
    if kind == "standalone":
        for path in ("/", "/app.js", "/style.css", "/api/state"):
            assert request("http://127.0.0.1:8080" + path)[0] == 200
        assert (
            request(
                "http://127.0.0.1:8080/api/command",
                {},
                {"Content-Type": "application/json"},
            )[0]
            == 403
        )
        mqtt_online()
    elif local_catalog:
        config = json.loads(Path("/data/daemon.json").read_text())
        assert config["web"]["enabled"] is False
        assert config["firmware_bundle_dir"] == "/data/firmware"
        manifest = json.loads(Path("/data/firmware/manifest.json").read_text())
        assert manifest == {
            "schema": 1,
            "host_version": versions["firmware_compat_version"],
            "artifacts": [],
        }
        assert Path("/data/firmware/persistence-check").read_text() == (
            "isolated-catalog-survives-recreation"
        )
        try:
            connection = socket.create_connection(("127.0.0.1", 8099), timeout=2)
        except ConnectionRefusedError:
            pass
        else:
            connection.close()
            raise AssertionError("Disabled Web UI must have no listener")
    else:
        # Direct requests must not bypass Supervisor ingress authentication.
        assert request("http://127.0.0.1:8099/")[0] == 403
    print(
        json.dumps(
            {
                "phase": "seed" if seed else "persistent-restart",
                "kind": kind,
                "api": True,
                "websocket": True,
                "daemon_uid": 10001,
                "firmware_preflight": preflight_supported,
                "persistent_catalog_without_web": local_catalog,
            }
        )
    )


def ingress(host):
    """Model the Supervisor socket peer on a private test network, not real auth.

    @param host App container's private network DNS name.
    """
    headers = {
        "X-Forwarded-Proto": "http",
        "X-Forwarded-Host": "homeassistant.test:8123",
        "X-Ingress-Path": "/api/hassio_ingress/test",
        "Origin": "http://homeassistant.test:8123",
    }
    for path in ("/", "/app.js", "/style.css", "/api/state"):
        assert request("http://" + host + ":8099" + path, headers=headers)[0] == 200
    assert (
        request(
            "http://" + host + ":8099/",
            headers=dict(headers, Origin="http://foreign.test"),
        )[0]
        == 403
    )
    print("Isolated trusted-peer ingress assets and Origin checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["standalone", "ha"])
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--local-catalog", action="store_true")
    parser.add_argument("--ingress")
    args = parser.parse_args()
    if args.ingress:
        ingress(args.ingress)
    else:
        check(args.kind, args.seed, args.local_catalog)
