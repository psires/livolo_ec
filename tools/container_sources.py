#!/usr/bin/env python3
"""Inventory built wheels and retain verified upstream Python source archives."""

import email
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile


def collect(wheels=Path("/wheels"), destination=Path("/sources")):
    """Record wheel hashes/licenses and download hash-verified matching sdists.

    @param wheels Directory of the resolved runtime wheels.
    @param destination Directory receiving archives and inventory files.
    """
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for wheel in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            name = next(
                n for n in archive.namelist() if n.endswith(".dist-info/METADATA")
            )
            metadata = email.message_from_bytes(archive.read(name))
            record = {
                "name": metadata["Name"],
                "version": metadata["Version"],
                "wheel": wheel.name,
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "license_expression": metadata.get("License-Expression"),
                "license": metadata.get("License"),
                "license_files": [
                    n
                    for n in archive.namelist()
                    if ".dist-info/" in n
                    and any(x in n.lower() for x in ("license", "copying", "notice"))
                ],
            }
        if record["name"] != "livolo-ec-daemon":
            url = "https://pypi.org/pypi/{name}/{version}/json".format(**record)
            with urllib.request.urlopen(url, timeout=60) as response:
                release = json.load(response)
            sources = [v for v in release["urls"] if v["packagetype"] == "sdist"]
            if len(sources) != 1:
                raise ValueError(
                    "Expected exactly one source archive: " + record["name"]
                )
            source = sources[0]
            filename = source["filename"]
            if Path(filename).name != filename or not source["url"].startswith(
                "https://files.pythonhosted.org/"
            ):
                raise ValueError("Unexpected source archive location")
            with urllib.request.urlopen(source["url"], timeout=120) as response:
                body = response.read()
            digest = hashlib.sha256(body).hexdigest()
            if digest != source["digests"]["sha256"]:
                raise ValueError("Source digest mismatch: " + filename)
            existing = destination / filename
            if (
                existing.exists()
                and hashlib.sha256(existing.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("Conflicting source archive: " + filename)
            existing.write_bytes(body)
            record["source"] = {
                "file": filename,
                "url": source["url"],
                "sha256": digest,
            }
        else:
            record["source"] = {"release_metadata": "/usr/share/livolo-ec/release.json"}
        records.append(record)
    (destination / "python-inventory.json").write_text(
        json.dumps(records, indent=2) + "\n"
    )
    (destination / "SHA256SUMS").write_text(
        "".join(
            hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
            for p in sorted(destination.iterdir())
            if p.is_file() and p.name != "SHA256SUMS"
        )
    )


if __name__ == "__main__":
    collect()
