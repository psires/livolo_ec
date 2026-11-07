"""CLI request shaping and uncertain transport handling without network access."""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from livolod.commission_cli import NoRedirect, main


class CommissionCLITests(unittest.TestCase):
    def test_malformed_or_wrong_command_results_are_unknown(self):
        """A generic JSON response or unrelated request cannot masquerade as acceptance."""
        for value in (
            None,
            {},
            [],
            {"request_id": "other", "status": "listening"},
            {"request_id": "pair-cli", "status": "pending"},
        ):
            output = io.StringIO()
            with (
                patch.dict("os.environ", {"LIVOLO_API_TOKEN": "private-token"}),
                patch(
                    "urllib.request.OpenerDirector.open",
                    return_value=io.BytesIO(json.dumps(value).encode()),
                ),
                redirect_stderr(output),
            ):
                self.assertEqual(main(["start", "--request-id", "pair-cli"]), 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "unknown")

    def test_redirects_and_missing_results_are_not_replayed(self):
        """Refuse redirects and preserve request identity after HTTP failures."""
        self.assertIsNone(
            NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://other/")
        )
        for code in (302, 404):
            output = io.StringIO()
            with (
                patch.dict("os.environ", {"LIVOLO_API_TOKEN": "private-token"}),
                patch(
                    "urllib.request.OpenerDirector.open",
                    side_effect=urllib.error.HTTPError(
                        "http://local/", code, "error", {}, None
                    ),
                ) as send,
                redirect_stderr(output),
            ):
                self.assertEqual(main(["status", "--request-id", "pair-cli"]), 1)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(json.loads(output.getvalue())["request_id"], "pair-cli")

    def test_start_is_one_explicit_native_request(self):
        """A bounded start uses environment authentication and never retries a POST."""
        output = io.StringIO()
        response = io.BytesIO(b'{"request_id":"pair-cli","status":"listening"}')
        with (
            patch.dict("os.environ", {"LIVOLO_API_TOKEN": "private-token"}),
            patch("urllib.request.OpenerDirector.open", return_value=response) as send,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["start", "--request-id", "pair-cli"]), 0)
        self.assertEqual(send.call_count, 1)
        request = send.call_args.args[0]
        self.assertEqual(
            json.loads(request.data),
            {
                "action": "commission",
                "payload": {"request_id": "pair-cli", "seconds": 120},
            },
        )
        self.assertNotIn("private-token", output.getvalue())

    def test_lost_reply_returns_stable_id_without_replay(self):
        """The operator can resolve uncertainty using the original ID after a timeout."""
        output = io.StringIO()
        with (
            patch.dict("os.environ", {"LIVOLO_API_TOKEN": "private-token"}),
            patch(
                "urllib.request.OpenerDirector.open", side_effect=TimeoutError()
            ) as send,
            redirect_stderr(output),
        ):
            self.assertEqual(main(["start", "--request-id", "pair-cli"]), 1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(json.loads(output.getvalue())["request_id"], "pair-cli")

    def test_status_and_cancel_use_exact_session(self):
        """Read-only status and explicit cancellation never create a new start."""
        for args, expected in [
            (["status"], None),
            (["cancel", "pair-cli", "--request-id", "close-cli"], "commission_cancel"),
        ]:
            response = io.BytesIO(
                b'{"data":{"commissioning_supported":true,"commissioning":null},"status":"confirmed","request_id":"close-cli"}'
            )
            with (
                patch.dict("os.environ", {"LIVOLO_API_TOKEN": "private-token"}),
                patch(
                    "urllib.request.OpenerDirector.open", return_value=response
                ) as send,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(args), 0)
            request = send.call_args.args[0]
            if expected is None:
                self.assertIsNone(request.data)
            else:
                self.assertEqual(
                    json.loads(request.data),
                    {
                        "action": expected,
                        "payload": {
                            "request_id": "close-cli",
                            "session_id": "pair-cli",
                        },
                    },
                )
