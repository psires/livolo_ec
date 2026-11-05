#!/usr/bin/env python3
"""Promote only tested digests and check GHCR access without stored credentials."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def digest(reference, missing=False):
    """Read an image descriptor; treat authentication/network failures as fatal.

    @param reference Full registry image reference.
    @param missing Whether an absent manifest may return None.
    """
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", reference],
        text=True,
        capture_output=True,
    )
    if result.returncode:
        if missing and (
            "manifest unknown" in result.stderr.lower()
            or "not found" in result.stderr.lower()
        ):
            return None
        raise RuntimeError(result.stderr)
    match = re.search(r"^Digest:\s+(sha256:[0-9a-f]{64})\s*$", result.stdout, re.M)
    if not match:
        raise ValueError("No immutable registry digest returned")
    return match[1]


def anonymous(reference):
    """Pull using an empty Docker client credential store, retaining daemon access.

    @param reference Registry tag or digest that must permit anonymous access.
    """
    with tempfile.TemporaryDirectory() as temporary:
        subprocess.run(
            ["docker", "pull", reference],
            check=True,
            env=dict(os.environ, DOCKER_CONFIG=temporary),
        )


def promote(image, version, candidate_digest, output):
    """Refuse replacement of a full version tag, then verify the promoted digest.

    @param image Approved repository name without a tag.
    @param version Full release version, never a mutable alias.
    @param candidate_digest Tested OCI index digest.
    @param output Destination for successful publication evidence.
    """
    assert re.fullmatch(r"ghcr.io/psires/(livolod|livolo-ec-ha-app)", image)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_digest)
    candidate = image + "@" + candidate_digest
    assert digest(candidate) == candidate_digest
    try:
        anonymous(candidate)  # Public visibility is a gate BEFORE release tagging.
    except subprocess.CalledProcessError as error:
        package = image.rsplit("/", 1)[1]
        settings = (
            "https://github.com/users/psires/packages/container/"
            + package
            + "/settings"
        )
        message = (
            "Anonymous pull failed for " + candidate + ". No release tag was created. "
            "Check package visibility/network access; package settings: " + settings
        )
        print("::error title=Container public access gate::" + message, flush=True)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                summary.write(
                    "\n### Public image access required\n\n"
                    + message
                    + "\n\nSet package visibility to **Public**, then rerun the failed workflow. "
                    "The existing candidate is not a published release.\n"
                )
        raise RuntimeError(message) from error
    final = image + ":" + version
    existing = digest(final, missing=True)
    if existing is not None and existing != candidate_digest:
        raise RuntimeError(
            "Immutable release already exists with a different digest: " + final
        )
    if existing is None:
        subprocess.run(
            ["docker", "buildx", "imagetools", "create", "--tag", final, candidate],
            check=True,
        )
    assert digest(final) == candidate_digest, "Promotion changed the tested index"
    anonymous(final)
    Path(output).write_text(
        json.dumps(
            {
                "image": final,
                "digest": candidate_digest,
                "anonymous_pull": True,
                "promoted_tested_digest": True,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    promote(args.image, args.version, args.digest, args.output)
