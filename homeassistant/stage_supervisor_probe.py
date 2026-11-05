#!/usr/bin/env python3
"""Prepare an isolated Supervisor catalog for the authorized no-radio upgrade test."""

import argparse
from pathlib import Path
import re


def stage(version, output):
    """Write a disposable app identity without USB, host ports or live credentials.

    @param version Existing public HA-wrapper image version under test.
    @param output New Git-tree directory for the stable validation branch.
    @return Staged repository root; publication remains a separate operation.
    """
    if not re.fullmatch(r"0\.2\.[01]", version):
        raise ValueError("Probe is limited to the reviewed 0.2.0/0.2.1 images")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "repository.yaml").write_text(
        "name: Livolo EC isolated channel validation\nurl: https://github.com/psires/livolo_ec\nmaintainer: Dariusz Swiderski (@psires)\n"
    )
    app = output / "livolo_ec_channel_test"
    app.mkdir()
    (app / "config.yaml").write_text(
        f"""name: Livolo EC channel test (no radio)
version: "{version}"
slug: livolo_ec_channel_test
description: Disposable isolated software installation and upgrade validation
arch:
  - aarch64
image: ghcr.io/psires/livolo-ec-ha-app
startup: application
boot: manual
init: false
stage: experimental
options:
  serial_port: /dev/livolo-channel-test-no-radio
  serial_baudrate: 460800
  gateway: "8a314b72"
  tag: "5d"
  api_token: ""
  ha_origin: http://homeassistant.local:8123
  output_mode: disabled
schema:
  serial_port: match(^/dev/livolo-channel-test-no-radio$)
  serial_baudrate: list(460800)
  gateway: match(^8a314b72$)
  tag: match(^5d$)
  api_token: password
  ha_origin: url
  output_mode: list(disabled)
"""
    )
    (app / "README.md").write_text(
        "Disposable validation app. No USB, host ports, ingress or live network identity. Set a generated API secret before start. Never connect it to the user integration.\n"
    )
    (app / "CHANGELOG.md").write_text(
        f"# {version}\n\nExercise the exact published HA-wrapper image; no radio firmware changes.\n"
    )
    return output


def main():
    """Generate catalog content without contacting or changing a Home Assistant host."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(stage(args.version, args.output))


if __name__ == "__main__":
    main()
