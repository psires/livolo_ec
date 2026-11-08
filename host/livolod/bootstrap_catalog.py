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
from .gecko_profiles import gecko_profile

# Maintainer-reviewed exact unit/artifact engineering trial, not public qualification.
APPROVED_TRIAL = dict(
    board="zb-gw04-1v2",
    version="0.14",
    sha256="b1358ed6f1ec77043544ca44e84f90d8214817b36d8185eea341ed70b67ba4c7",
    source_commit="0fd515ca156823e513d2d86f0507b8da9b8558e8",
    evidence_id="b437e13",
)

# Separate exact-unit engineering evidence, never inherited from the ZB-GW04 trial.
# The operator retains the reviewed unit serial/topology/attachment in the protected
# target policy. This record does not establish general factory-loader eligibility.
SONOFF_APPROVED_TRIAL = dict(
    board="sonoff-zbdongle-e",
    version="0.14",
    sha256="87de29744fd7b991e0da8cdfda0fd160e12bd749764d4bca8c1337683804a7e6",
    source_commit="0fd515ca156823e513d2d86f0507b8da9b8558e8",
    evidence_id="sonoff-intel-loader-review-20261108",
)


def reviewed_trial(board):
    """Return the separate immutable-by-contract approval for a configured profile.

    @param board Exact server-owned board/profile identifier.
    """
    gecko_profile(board)
    return SONOFF_APPROVED_TRIAL if board == "sonoff-zbdongle-e" else APPROVED_TRIAL


def engineering_entry(entry, policy):
    """Authorize only an explicitly unqualified entry bound to the reviewed trial.

    @param entry Manifest artifact already checked for its required fields.
    @param policy Exact server-owned engineering record; never supplied by HTTP.
    """
    return (
        isinstance(policy, dict)
        and policy in (APPROVED_TRIAL, SONOFF_APPROVED_TRIAL)
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
    profile = gecko_profile(settings.get("profile", "zb-gw04-1v2"))
    approved = reviewed_trial(profile.board)
    engineering = policy.get("engineering")
    if engineering is None:
        if candidate_id is None:
            return select_candidate(
                settings["catalog"],
                board=profile.board,
                host_version=FIRMWARE_COMPAT_VERSION,
                app_baudrate=460800,
            )
        candidate = load_candidate(
            settings["catalog"],
            candidate_id,
            host_version=FIRMWARE_COMPAT_VERSION,
            app_baudrate=460800,
        )
        if candidate["board"] != profile.board:
            raise BundleError(
                "unsupported_profile", "Candidate belongs to another profile"
            )
        return candidate
    try:
        if (
            set(policy) != {"schema", "target", "loader", "engineering"}
            or policy["schema"] != 1
            or engineering != approved
            or FIRMWARE_COMPAT_VERSION != engineering["version"]
        ):
            raise ValueError("Unknown engineering policy")
        target = target_from_json(policy["target"])
        if target.board != profile.board:
            raise ValueError("Policy belongs to another board")
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
