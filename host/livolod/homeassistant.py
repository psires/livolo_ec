"""Home Assistant MQTT switch discovery, using the existing command contract."""

import json


STATUS_TOPIC = "homeassistant/status"


def discovery_messages(config, removed):
    """Reconcile stable Home Assistant switch identities and removed channels.

    @param config Daemon configuration containing the current device registry and MQTT prefix.
    @param removed Previously registered device IDs needing discovery tombstones.
    @return An iterator of discovery topic and payload pairs; empty payloads remove entities.
    """
    base = config.mqtt["base_topic"]
    devices = {device.id: device for device in config.devices}
    for device_id in sorted(set(devices) | set(removed)):
        device = devices.get(device_id)
        identifier = f"livolo_ec_{config.gateway}_{device_id}"
        for output in (1, 2):
            unique_id = f"{identifier}_{output}"
            topic = f"homeassistant/switch/{unique_id}/config"
            if device is None or output > device.outputs:
                yield topic, ""
                continue
            mask = 1 << (output - 1)
            yield topic, {
                "name": f"Output {output}",
                "unique_id": unique_id,
                "device": {
                    "identifiers": [identifier],
                    "name": device.name,
                    "manufacturer": "Livolo",
                    "model": f"EC {device.outputs}-gang switch",
                },
                "command_topic": f"{base}/{device_id}/set",
                "payload_on": json.dumps(
                    {"mask": mask, "value": mask}, separators=(",", ":")
                ),
                "payload_off": json.dumps(
                    {"mask": mask, "value": 0}, separators=(",", ":")
                ),
                "state_topic": f"{base}/{device_id}/state",
                "value_template": "{{ value_json.outputs['%d'] }}" % output,
                "state_on": "ON",
                "state_off": "OFF",
                "optimistic": False,
                "qos": 1,
                "retain": False,
                "availability_mode": "all",
                "availability": [
                    {"topic": f"{base}/bridge/state"},
                    {"topic": f"{base}/{device_id}/availability"},
                    {"topic": f"{base}/bridge/controls"},
                ],
            }
