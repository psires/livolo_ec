"""Install pinned pure-Python Nordic helper sources and retain their provenance."""

import hashlib
import io
import json
from pathlib import Path
import sysconfig
import tarfile
import urllib.request

COMMIT = "cc3a9d259b9363524823aa4a48dbe6991eca6bd7"
DIGEST = "0efbea8b32c0636a29f1b7f0fd074f98064587ec132552163ab9909a4b97267b"


def main():
    """Verify upstream sources, copy Python modules, and inventory the helper wheels."""
    from container_sources import collect

    destination = Path("/sources/nordic")
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("requirements-nordic.txt", "requirements-nordic-build.txt"):
        (destination / name).write_bytes(Path(name).read_bytes())
    url = (
        "https://codeload.github.com/NordicSemiconductor/pc-ble-driver-py/tar.gz/"
        + COMMIT
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        source = response.read(1024 * 1024)
    if hashlib.sha256(source).hexdigest() != DIGEST:
        raise ValueError("Pinned Nordic helper source checksum mismatch")
    (destination / ("pc-ble-driver-py-" + COMMIT + ".tar.gz")).write_bytes(source)
    prefix = "pc-ble-driver-py-" + COMMIT + "/"
    site = Path(sysconfig.get_paths()["purelib"])
    installed = {}
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.isdir() and member.name.rstrip("/") == prefix.rstrip("/"):
                continue
            if not member.name.startswith(prefix):
                raise ValueError("Unexpected upstream source layout")
            relative = Path(member.name[len(prefix) :])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe upstream source path")
            if not member.isfile():
                continue
            if relative.parts[0] == "pc_ble_driver_py" and relative.suffix == ".py":
                path = site / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                body = archive.extractfile(member).read()
                path.write_bytes(body)
                installed[str(relative)] = hashlib.sha256(body).hexdigest()
            elif relative.name.lower().startswith(("license", "copying", "notice")):
                (destination / relative.name).write_bytes(
                    archive.extractfile(member).read()
                )
    (destination / "pc-ble-driver-source.json").write_text(
        json.dumps(
            dict(
                commit=COMMIT,
                url=url,
                sha256=DIGEST,
                python_files=installed,
                native_ble_libraries_included=False,
            ),
            indent=2,
        )
        + "\n"
    )
    collect(Path("/wheels"), destination)
    Path("/usr/local/livolo-runtime.json").write_text(
        json.dumps({"python": [3, 10], "nrfutil": "6.1.7", "fixed_port": 1}) + "\n"
    )


if __name__ == "__main__":
    main()
