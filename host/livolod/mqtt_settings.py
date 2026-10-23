"""Web-editable MQTT settings; secrets are never part of a command or snapshot."""

from .config import fields, mqtt_config


def updated_settings(current, request, gateway):
    """Apply a web settings request while preserving or replacing credentials explicitly.

    @param current Existing validated MQTT settings, including private fields.
    @param request Submitted editable fields and password action.
    @param gateway Gateway identity used to validate MQTT defaults.
    @return The validated replacement settings, including retained private fields.
    """
    fields(
        request,
        "host port base_topic username password password_action",
        "MQTT settings",
    )
    if (
        not {"host", "port", "base_topic", "username", "password_action"}
        <= request.keys()
    ):
        raise ValueError(
            "Server, port, topic prefix, username and password action are required"
        )
    settings = dict(current)
    for key in ("host", "base_topic", "username"):
        if not isinstance(request[key], str):
            raise ValueError("MQTT server, topic prefix and username must be text")
        settings[key] = request[key].strip()
    settings["port"] = request["port"]
    action = request["password_action"]
    if action not in ("keep", "replace", "clear"):
        raise ValueError("Password action must be keep, replace or clear")
    if action != "replace" and "password" in request:
        raise ValueError("Only a replacement may include a password")
    if action == "replace":
        password = request.get("password")
        if not isinstance(password, str) or not password:
            raise ValueError("Enter a nonempty replacement password")
        settings["password"] = password
        settings.pop("password_env", None)
    elif action == "clear":
        settings.pop("password", None)
        settings.pop("password_env", None)
    if not settings["username"]:
        settings.pop("username")
        if settings.get("password") or settings.get("password_env"):
            raise ValueError(
                "Enter a username or choose Clear password for anonymous access"
            )
    return mqtt_config(settings, gateway, allow_password=True)
