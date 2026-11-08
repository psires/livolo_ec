"""Narrow private engineering-trial admission, separate from public qualified firmware."""

from . import FIRMWARE_COMPAT_VERSION
from .firmware_bundle import (
    BundleError,
    _candidate,
    _manifest,
    load_candidate,
    select_candidate,
)
from .bootstrap_worker import target_from_json
from .zbgw04_provider import LoaderPolicy

# Maintainer-reviewed exact unit/artifact engineering trial, not public qualification.
APPROVED_TRIAL = dict(
    board="zb-gw04-1v2",
    version="0.14",
    sha256="b1358ed6f1ec77043544ca44e84f90d8214817b36d8185eea341ed70b67ba4c7",
    source_commit="0fd515ca156823e513d2d86f0507b8da9b8558e8",
    evidence_id="b437e13",
)


def engineering_entry(entry, policy):
    """Authorize only an explicitly unqualified entry bound to the reviewed trial.

    @param entry Manifest artifact already checked for its required fields.
    @param policy Exact server-owned engineering record; never supplied by HTTP.
    """
    return (
        policy == APPROVED_TRIAL
        and entry.get("qualified") is False
        and all(
            entry.get(key) == policy[key]
            for key in ("board", "version", "sha256", "source_commit")
        )
    )


def bootstrap_candidate(settings, candidate_id=None):
    """Reuse normal path/hash/layout/license/GBL checks with one scoped trial exception.

    @param settings Protected server configuration including exact-unit loader policy.
    @param candidate_id Optional immutable candidate handle to revalidate at admission.
    """
    policy = settings.get("loader_policy") or {}
    engineering = policy.get("engineering")
    if engineering is None:
        if candidate_id is None:
            return select_candidate(
                settings["catalog"],
                board="zb-gw04-1v2",
                host_version=FIRMWARE_COMPAT_VERSION,
                app_baudrate=460800,
            )
        return load_candidate(
            settings["catalog"],
            candidate_id,
            host_version=FIRMWARE_COMPAT_VERSION,
            app_baudrate=460800,
        )
    try:
        if (
            set(policy) != {"schema", "target", "loader", "engineering"}
            or policy["schema"] != 1
            or engineering != APPROVED_TRIAL
            or FIRMWARE_COMPAT_VERSION != engineering["version"]
        ):
            raise ValueError("Unknown engineering policy")
        target = target_from_json(policy["target"])
        loader = LoaderPolicy(**policy["loader"])
        loader.validate(target)
        if loader.evidence_id != engineering["evidence_id"]:
            raise ValueError("Unbound trial evidence")
    except (KeyError, TypeError, ValueError, BundleError):
        raise BundleError(
            "engineering_policy_required",
            "Exact reviewed unit and engineering evidence are required",
        ) from None
    root, entries = _manifest(
        settings["catalog"], FIRMWARE_COMPAT_VERSION, engineering_policy=engineering
    )
    if len(entries) != 1 or not engineering_entry(entries[0], engineering):
        raise BundleError(
            "engineering_policy_required",
            "Catalog differs from the reviewed engineering trial",
        )
    candidate = _candidate(root, entries[0], 460800)
    if candidate_id is not None and candidate["id"] != candidate_id:
        raise BundleError("candidate_stale", "Engineering artifact selection changed")
    candidate["engineering_only"] = True
    return candidate
