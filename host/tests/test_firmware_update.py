import hashlib
import struct
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from livolod.update_package import HEADER, BOARD, FORMAT
from livolod.firmware_update import transfer, recover_failed_transfer


class FakePort:
    def __init__(
        self,
        key,
        digest,
        *,
        state="ffffffff",
        trial="0",
        version="0.1",
        lost_commit=False,
    ):
        """Prepare the simulated collaborator with the requested test behavior.

        @param key Expected firmware signing public key as hexadecimal.
        @param digest Expected candidate SHA-512 digest as hexadecimal.
        @param state Persistent update-state word as hexadecimal.
        @param trial Trial-image status field.
        @param version Reported firmware release.
        @param lost_commit Whether to simulate a lost commit reply after persisting the update.
        """
        self.key = key
        self.digest = digest
        self.state = state
        self.trial = trial
        self.version = version
        self.lost_commit = lost_commit
        self.commands = []

    def command(self, text, **kwargs):
        """Provide the scenario-specific UART response or injected failure.

        @param text Command or encoded protocol text for this exchange.
        @param kwargs Optional transport arguments accepted for interface compatibility.
        """
        self.commands.append(text)
        if text == "VERSION":
            return (
                f"OK fw_version={self.version} board=codm-cc2652p update=uart_signed_v1"
            )
        if text == "UPDATE_STATUS":
            return f"OK state={self.state} trial={self.trial} key={self.key} candidate_sha512={self.digest}"
        if text.startswith("UPDATE_DATA "):
            _, _, offset, data = text.split()
            return f"OK DATA received={int(offset)+len(data)//2}"
        if text.startswith("UPDATE_COMMIT "):
            self.state = "fffffffe"
            if self.lost_commit:
                raise TimeoutError("lost reply")
        if text == "UPDATE_ABORT" and self.state == "fffffffe":
            raise RuntimeError("ERR ALREADY_COMMITTED")
        return "OK"


class UpdateClientTests(unittest.TestCase):
    def setUp(self):
        """Prepare isolated fixtures for the next test case."""
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key()
        self.raw_key = self.public.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ).hex()
        image = bytearray(256)
        struct.pack_into("<II", image, 0, 0x20014000, 0x8081)
        digest = hashlib.sha512(image).digest()
        self.digest = digest.hex()
        header = HEADER.pack(
            b"ECUPD01\0",
            FORMAT,
            BOARD,
            0,
            2,
            3,
            0x8000,
            len(image),
            0,
            digest,
            bytes(24),
        )
        self.package = header + self.key.sign(header) + image

    def test_preflight_failure_never_disarms_aborts_or_resets(self):
        """Verify preflight failure never disarms aborts or resets."""
        for changes in (
            dict(key="00" * 32),
            dict(version="0.2"),
            dict(state="fffffff8", trial="1"),
        ):
            args = dict(key=self.raw_key, digest=self.digest)
            args.update(changes)
            port = FakePort(**args)
            transaction = {}
            with self.assertRaises((ValueError, RuntimeError)):
                transfer(port, self.package, self.public, transaction=transaction)
            recover_failed_transfer(
                port, transaction, dict(sha512=self.digest, version="0.2")
            )
            self.assertFalse(transaction.get("started"))
            self.assertTrue(
                all(x in ("VERSION", "UPDATE_STATUS") for x in port.commands)
            )

    def test_lost_commit_reply_resolves_by_matching_persistent_candidate(self):
        """Verify lost commit reply resolves by matching persistent candidate."""
        port = FakePort(self.raw_key, self.digest, lost_commit=True)
        transaction = {}
        transfer(port, self.package, self.public, transaction=transaction)
        self.assertEqual(port.commands[-1], "UPDATE_ACTIVATE")
        self.assertEqual(sum(x.startswith("UPDATE_COMMIT ") for x in port.commands), 1)

    def test_previously_committed_same_package_resumes_without_erasing(self):
        """Verify previously committed same package resumes without erasing."""
        port = FakePort(self.raw_key, self.digest, state="fffffffe")
        transfer(port, self.package, self.public)
        self.assertEqual(port.commands, ["VERSION", "UPDATE_STATUS", "UPDATE_ACTIVATE"])

    def test_cleanup_activates_matching_commit_without_abort_and_leaves_trial(self):
        """Verify cleanup activates matching commit without abort and leaves trial."""
        port = FakePort(self.raw_key, self.digest, state="fffffffe")
        recover_failed_transfer(
            port, {"started": True}, dict(sha512=self.digest, version="0.2")
        )
        self.assertEqual(port.commands, ["UPDATE_STATUS", "UPDATE_ACTIVATE"])
        port = FakePort(self.raw_key, self.digest, state="fffffff8", trial="1")
        recover_failed_transfer(
            port, {"started": True}, dict(sha512=self.digest, version="0.2")
        )
        self.assertEqual(port.commands, ["UPDATE_STATUS"])

    def test_ready_resume_with_lost_activation_ack_continues_to_trial_polling(self):
        """Verify ready resume with lost activation ack continues to trial polling."""
        port = FakePort(self.raw_key, self.digest, state="fffffffe")
        original = port.command

        def command(text, **kwargs):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            @param kwargs Optional transport arguments accepted for interface compatibility.
            """
            reply = original(text, **kwargs)
            if text == "UPDATE_ACTIVATE":
                raise TimeoutError("activation reply lost")
            return reply

        port.command = command
        self.assertEqual(transfer(port, self.package, self.public)["version"], "0.2")

    def test_other_committed_package_is_not_activated(self):
        """Verify other committed package is not activated."""
        port = FakePort(self.raw_key, "00" * 64, state="fffffffe")
        transaction = {}
        with self.assertRaises(RuntimeError):
            transfer(port, self.package, self.public, transaction=transaction)
        self.assertNotIn("UPDATE_ACTIVATE", port.commands)
