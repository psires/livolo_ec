"""Compile the actual command parser/MAC callback against a deterministic radio.

No UART/RF timing claims: hardware timing is checked separately with Nordic.
"""

import binascii
import ctypes as C
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EPOCH = "0123456789abcdef"
GW = "2a992b12"
DEV = "46715621"


def report(device=DEV, opcode=5, broadcast=False, tag=0xA7):
    """Build a device report suitable for testing ACK policy.

    @param device Eight-digit hexadecimal device identity.
    @param opcode Report opcode to exercise.
    @param broadcast Whether to use the broadcast destination.
    @param tag Network tag byte for the report.

    @return Encoded report with its checksum."""
    raw = bytearray.fromhex(
        "018842"
        + ("ffffffff" if broadcast else GW)
        + device
        + "0000000000000000"
        + f"{tag:02x}"
        + "00"
        + f"9e{opcode:02x}"
        + device
        + "00020100010100000100"
    )
    return bytes(raw) + binascii.crc_hqx(raw[21:37], 0xFFFF).to_bytes(2, "big")


def frame(device=DEV):
    """Build a valid gateway command frame addressed to a device.

    @param device Eight-digit hexadecimal device identity.

    @return Encoded command with its checksum."""
    raw = bytearray.fromhex(
        "018843"
        + device
        + GW
        + "0000000000000000a70e8c02"
        + device
        + "00020100010000000011"
    )
    return bytes(raw) + bytes((sum(raw[21:37]) & 255, 0, 0, 0))


def phased_frame():
    """Build a command frame eligible for gateway phase insertion.

    @return Encoded phase-eligible command."""
    raw = bytearray(frame())
    raw[15:19] = bytes.fromhex(DEV)
    raw[20] = 0x1E
    return bytes(raw)


def heartbeat():
    """Build a heartbeat frame that establishes a phase reference.

    @return Encoded heartbeat with its checksum."""
    raw = bytearray(phased_frame())
    raw[3:7] = b"\xff" * 4
    raw[11:19] = bytes(8)
    raw[20] = 0x0E
    raw[22] = 8
    raw[23:29] = bytes(6)
    raw[29:37] = bytes.fromhex("3200000001000000")
    raw[37] = sum(raw[21:37]) & 255
    return bytes(raw)


class FirmwareUART(unittest.TestCase):
    baudrate = 115200
    scheduler = False
    auto_ack_phase = False
    rtimer_hz = 65536

    @classmethod
    def setUpClass(cls):
        """Build the real firmware core with deterministic platform test adapters."""
        cls.temp = tempfile.TemporaryDirectory()
        p = Path(cls.temp.name)
        headers = {
            "contiki.h": """#include <stdint.h>
typedef uint32_t clock_time_t;
typedef uint32_t rtimer_clock_t;
extern uint32_t test_clock;
extern uint32_t test_rtimer;
#define CLOCK_SECOND 128
#define RTIMER_SECOND TEST_RTIMER_HZ
#define RTIMER_NOW() test_rtimer
/**
 * @brief Expose the deterministic scheduler clock to firmware.
 *
 * @return Current synthetic scheduler tick count.
 */
static inline uint32_t clock_time(void)
{
	return test_clock;
}
#define PROCESS(a, b) static int a
#define AUTOSTART_PROCESSES(...)
#define PROCESS_THREAD(a, b, c) int a##_thread(int b, void *c)
#define PROCESS_BEGIN()
#define PROCESS_END() return 0
#define PROCESS_YIELD() return 0
/**
 * @brief Accept a scheduler poll request without asynchronous execution.
 *
 * @param p Process that would be scheduled on hardware.
 */
static inline void process_poll(void *p)
{
	(void)p;
}
struct etimer {
	int dummy;
};

/**
 * @brief Accept a timer request without real elapsed-time scheduling.
 *
 * @param p Timer fixture receiving the request.
 * @param x Requested timer interval.
 */
static inline void etimer_set(struct etimer *p, int x)
{
	(void)p;
	(void)x;
}

/**
 * @brief Keep synthetic process timers inactive until explicitly exercised.
 *
 * @param p Timer fixture to query.
 *
 * @return Zero because these tests do not drive process timer events.
 */
static inline int etimer_expired(struct etimer *p)
{
	(void)p;
	return 0;
}

/**
 * @brief Accept a timer reset without scheduling asynchronous events.
 *
 * @param p Timer fixture to reset.
 */
static inline void etimer_reset(struct etimer *p)
{
	(void)p;
}
""",
            "net/netstack.h": """#define RADIO_PARAM_TX_MODE 1
#define RADIO_TX_MODE_SEND_ON_CCA 1
#define RADIO_TX_OK 0
#define RADIO_TX_COLLISION 2
struct test_radio {
	int (*set_value)(int, int);
	int (*send)(const void *, unsigned);
};
extern struct test_radio NETSTACK_RADIO;
""",
            "dev/uart0-arch.h": """#include <stddef.h>
/**
 * @brief Capture firmware UART output for test assertions.
 *
 * @param p Bytes emitted by firmware.
 * @param n Byte count to capture.
 *
 * @return Captured byte count, or -1 when the output buffer is full.
 */
int uart0_write(const void *p, size_t n);

/**
 * @brief Accept the UART input callback in the host test adapter.
 *
 * @param cb Callback that hardware would invoke for incoming bytes.
 */
void uart0_set_callback(int (*cb)(unsigned char));
""",
            "ti/drivers/dpl/HwiP.h": """#include <stdint.h>
/**
 * @brief Provide a deterministic interrupt guard for single-threaded tests.
 *
 * @return Synthetic interrupt state key.
 */
static inline uintptr_t HwiP_disable(void)
{
	return 0;
}

/**
 * @brief Accept restoration of a synthetic interrupt state.
 *
 * @param key Key returned by HwiP_disable().
 */
static inline void HwiP_restore(uintptr_t key)
{
	(void)key;
}
""",
        }
        for name, body in headers.items():
            target = p / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        wrapper = p / "wrapper.c"
        wrapper.write_text(
            """#include <string.h>
#include <stdint.h>
#include <stddef.h>
uint32_t test_clock;
uint32_t test_rtimer;
static char output[32768];
static size_t used;
static unsigned radio_sends, radio_settings;
static int radio_result;
static uint32_t radio_latency;
static uint8_t sent_raw[125];
static unsigned sent_len;

/**
 * @brief Accept radio configuration in the deterministic test adapter.
 *
 * @param a Radio parameter identifier.
 * @param b Requested radio parameter value.
 *
 * @return Zero to indicate the setting was accepted.
 */
static int setting(int a, int b)
{
	(void)a;
	(void)b;
	radio_settings++;
	return 0;
}

/** @brief Count radio configuration changes.
 * @return Configuration calls since fixture reset.
 */
unsigned test_settings(void)
{
	return radio_settings;
}

/**
 * @brief Record a radio transmission and expose the configured outcome.
 * @param p Borrowed frame bytes to copy into the last-send buffer.
 * @param n Number of frame bytes, at most 125.
 * @return Configured synthetic radio result.
 */
static int send_raw(const void *p, unsigned n)
{
	memcpy(sent_raw, p, n);
	sent_len = n;
	radio_sends++;
	test_rtimer += radio_latency;
	return radio_result;
}

/**
 * @brief Capture firmware UART output for test assertions.
 *
 * @param p Bytes emitted by firmware.
 * @param n Byte count to capture.
 *
 * @return Captured byte count, or -1 when the output buffer is full.
 */
int uart0_write(const void *p, size_t n)
{
	if (used + n >= sizeof(output))
		return -1;
	memcpy(output + used, p, n);
	used += n;
	output[used] = 0;
	return (int)n;
}

/**
 * @brief Accept the UART input callback in the host test adapter.
 *
 * @param cb Callback that hardware would invoke for incoming bytes.
 */
void uart0_set_callback(int (*cb)(unsigned char))
{
	(void)cb;
}

/**
 * @brief Advertise sufficient UART capacity for deterministic parser tests.
 *
 * @return Synthetic available UART buffer size.
 */
unsigned ec_uart_free(void)
{
	return 8192;
}

/**
 * @brief Report the absence of simulated UART transport loss.
 *
 * @return Zero dropped bytes.
 */
unsigned long ec_uart_dropped(void)
{
	return 0;
}
"""
            # Mach-O requires a segment in section names. Keep the embedded
            # source and its other attributes intact in this host-only harness.
            + (
                '#define section(name) section("__TEXT,__fw_version")\n'
                if sys.platform == "darwin"
                else ""
            )
            + f'#include "{ROOT / "ec-gateway.c"}"\n'
            + ("#undef section\n" if sys.platform == "darwin" else "")
            + """
struct test_radio NETSTACK_RADIO = { setting, send_raw };
#ifdef EC_ENABLE_SCHEDULER
/** @brief Expose the fixture clock. @return Current precise ticks. */
uint32_t ec_scheduler_now(void)
{
	return test_rtimer;
}
/**
 * @brief Ignore real wake scheduling; tests explicitly advance and poll.
 * @param active Whether a wake is required.
 * @param due Absolute timer deadline.
 */
void ec_scheduler_alarm(int active, uint32_t due)
{
	(void)active;
	(void)due;
}
#endif

/**
 * @brief Restore a clean gateway and deterministic radio fixture.
 */
void test_reset(void)
{
#ifdef EC_ENABLE_SCHEDULER
	memset(&ec_scheduler, 0, sizeof(ec_scheduler));
#endif
	memset(&network, 0, sizeof(network));
	memset(session, 0, sizeof(session));
	legacy = ack_enabled = lease_seconds = metadata_enabled = revision =
		armed_at = 0;
	received = tx_attempts = tx_ok = ack_attempts = ack_ok = rx_drops =
		meta_drops = input_errors = 0;
	command_head = command_tail = input_pos = discard_line = last_id = 0;
	memset(last_command, 0, sizeof(last_command));
	memset(last_reply, 0, sizeof(last_reply));
	test_clock = radio_sends = radio_settings = used = 0;
	output[0] = 0;
	test_rtimer = radio_latency = sent_len = phase_valid =
		phase_generation = 0;
	phase_anchor_ticks = phase_anchor_clock = 0;
	radio_result = 0;
}

/**
 * @brief Submit one command directly to the real gateway parser.
 *
 * @param text NUL-terminated command text.
 *
 * @return Captured reply, or an oversized-input diagnostic.
 */
const char *test_request(const char *text)
{
	char copy[320];
	used = 0;
	output[0] = 0;
	if (strlen(text) >= sizeof(copy))
		return "TEST_INPUT_TOO_LONG";
	strcpy(copy, text);
	command(copy);
	return output;
}

/** @brief Inject transport loss so the parser discards the partial line. */
void test_rx_loss(void)
{
	input_byte(0);
}

/**
 * @brief Exercise UART framing and command queuing with a byte stream.
 * @param text Borrowed NUL-terminated stream of UART input bytes.
 * @return Captured UART responses in the fixture-owned static buffer.
 */
const char *test_feed(const char *text)
{
	used = 0;
	output[0] = 0;
	while (*text)
		input_byte(*text++);
	while (command_tail != command_head) {
		char copy[320];
		strcpy(copy, commands[command_tail]);
		command_tail = (command_tail + 1) % COMMAND_SLOTS;
		command(copy);
	}
	return output;
}

/**
 * @brief Exercise the real receive callback with a synthetic radio frame.
 *
 * @param raw Received frame bytes.
 * @param len Received frame length.
 *
 * @return Captured UART records.
 */
const char *test_receive(const uint8_t *raw, unsigned len)
{
	used = 0;
	output[0] = 0;
	ec_receive(raw, len, -40, 60);
	return output;
}

/**
 * @brief Advance both deterministic clocks by a scheduler tick interval.
 *
 * @param ticks Scheduler ticks to advance.
 */
void test_advance(uint32_t ticks)
{
	test_clock += ticks;
	test_rtimer += (uint64_t)ticks * RTIMER_SECOND / CLOCK_SECOND;
}

/**
 * @brief Set the precise timer for wraparound and phase test scenarios.
 *
 * @param ticks New precise timer tick count.
 */
void test_set_rtimer(uint32_t ticks)
{
	test_rtimer = ticks;
}

/**
 * @brief Choose the outcome and duration of subsequent radio transmissions.
 *
 * @param result Synthetic result returned by the radio.
 * @param latency Precise timer ticks consumed by each transmission.
 */
void test_radio(int result, uint32_t latency)
{
	radio_result = result;
	radio_latency = latency;
}

/**
 * @brief Expose the last transmitted frame as hexadecimal test evidence.
 *
 * @return NUL-terminated hexadecimal frame in a static buffer.
 */
const char *test_sent(void)
{
	static char hex[251];
	ec_hex(sent_raw, sent_len, hex);
	return hex;
}

/**
 * @brief Report how many transmissions reached the radio adapter.
 *
 * @return Total transmission calls since reset.
 */
unsigned test_sends(void)
{
	return radio_sends;
}
"""
        )
        library = p / "firmware.so"
        subprocess.run(
            [
                shutil.which("cc") or "cc",
                "-std=c99",
                f"-DEC_UART_BAUD={cls.baudrate}",
                f"-DTEST_RTIMER_HZ={cls.rtimer_hz}",
                f"-DEC_SCHED_HZ={cls.rtimer_hz}",
                *(["-DEC_ENABLE_SCHEDULER=1"] if cls.scheduler else []),
                *(["-DEC_AUTO_ACK_PHASE=1"] if cls.auto_ack_phase else []),
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wno-unused-parameter",
                "-Wno-unused-function",
                "-fPIC",
                "-dynamiclib" if sys.platform == "darwin" else "-shared",
                "-I" + str(p),
                "-I" + str(ROOT),
                str(wrapper),
                str(ROOT / "ec-peers.c"),
                str(ROOT / "ec-protocol.c"),
                str(ROOT / "ec-phase.c"),
                *([str(ROOT / "ec-scheduler.c")] if cls.scheduler else []),
                "-o",
                str(library),
            ],
            check=True,
        )
        cls.lib = C.CDLL(str(library))
        for name in ("test_request", "test_feed"):
            fn = getattr(cls.lib, name)
            fn.argtypes = [C.c_char_p]
            fn.restype = C.c_char_p
        cls.lib.test_receive.argtypes = [C.c_char_p, C.c_uint]
        cls.lib.test_receive.restype = C.c_char_p
        cls.lib.test_advance.argtypes = [C.c_uint32]
        cls.lib.test_set_rtimer.argtypes = [C.c_uint32]
        cls.lib.test_radio.argtypes = [C.c_int, C.c_uint32]
        cls.lib.test_sent.restype = C.c_char_p
        cls.lib.ec_phase_encode.argtypes = [C.c_uint32, C.c_uint32, C.c_void_p]

    def sent(self):
        """Return the last transmitted frame from the deterministic radio.

        @return Raw bytes last passed to the radio."""
        return bytes.fromhex(self.lib.test_sent().decode())

    def test_phase_integer_vectors_and_bounds(self):
        """Match captured phase encodings and reject unsupported timing inputs."""
        out = (C.c_uint8 * 3)()
        # Use a 1 kHz synthetic clock for exact capture-derived millisecond vectors.
        for ticks, expected in (
            (0, "000000"),
            (200, "2c3300"),
            (5600, "849905"),
            (5812, "c2cf05"),
            (999, "9aff00"),
            (1000, "000001"),
        ):
            self.assertEqual(self.lib.ec_phase_encode(ticks, 1000, out), 1)
            self.assertEqual(bytes(out).hex(), expected)
        self.assertEqual(self.lib.ec_phase_encode(12000, 1000, out), 0)
        self.assertEqual(self.lib.ec_phase_encode(1, 0, out), 0)
        self.assertEqual(self.lib.ec_phase_encode(65535, 65536, out), 1)
        self.assertEqual(bytes(out).hex(), "dbff00")

    def test_phase_requires_reference_and_restricts_frames(self):
        """Require a live phase reference and an eligible frame before phase transmission."""
        self.configured()
        self.arm()
        self.cmd(f"SEND_PHASE {EPOCH} " + phased_frame().hex(), "ERR PHASE_REFERENCE")
        self.assertEqual(self.lib.test_sends(), 0)
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex())
        for offset in (3, 7, 15, 20, 23, 37, 38):
            raw = bytearray(phased_frame())
            raw[offset] ^= 1
            self.cmd(f"SEND_PHASE {EPOCH} " + raw.hex(), "ERR FRAME_POLICY")
        self.assertEqual(self.lib.test_sends(), 1)
        self.cmd(f"SEND_PHASE {EPOCH} " + phased_frame().hex())
        self.assertEqual(self.sent()[:38], phased_frame()[:38])

    def test_phase_entry_clock_wrap_and_cached_actual_bytes(self):
        """Handle phase clock wrap and replay the original transmitted bytes."""
        self.configured()
        self.arm()
        self.lib.test_set_rtimer((-self.rtimer_hz) & 0xFFFFFFFF)
        self.lib.test_radio(0, 100)
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex())
        self.lib.test_set_rtimer(self.rtimer_hz // 2)  # 1.5 seconds across timer wrap.
        command = f"SEND_PHASE {EPOCH} " + phased_frame().hex()
        response = self.cmd(command, request_id=1000)
        self.assertIn("timestamp=tx_entry", response)
        self.assertIn(f"age_ticks={self.rtimer_hz * 3 // 2}", response)
        self.assertIn(
            f"entry_ticks={self.rtimer_hz // 2} return_ticks={self.rtimer_hz // 2 + 100}",
            response,
        )
        self.assertEqual(self.sent()[-3:].hex(), "ee7f01")
        self.lib.test_set_rtimer(self.rtimer_hz)
        self.assertEqual(self.cmd(command, request_id=1000), response)
        self.assertEqual(self.lib.test_sends(), 2)
        self.cmd(command)
        self.assertEqual(self.sent()[-3:].hex(), "000002")

    def test_phase_queries_require_observed_payload_and_preserve_replay_cache(self):
        """Stamp only supported query forms and never retransmit a cached UART request."""
        self.configured()
        self.arm()
        self.assertIn("phase_queries=10_ac_v1", self.cmd("CAPS"))
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex())
        self.lib.test_set_rtimer(self.rtimer_hz // 2)
        for index, opcode in enumerate((0x10, 0xAC)):
            raw = bytearray(phased_frame())
            raw[22] = opcode
            raw[29:37] = bytes(7) + b"\x01"
            raw[37] = sum(raw[21:37]) & 255
            command = f"SEND_PHASE {EPOCH} " + raw.hex()
            reply = self.cmd(command, request_id=1000 + index)
            self.assertEqual(self.sent()[:38], raw[:38])
            self.assertEqual(self.sent()[-3:].hex(), "ee7f00")
            count = self.lib.test_sends()
            self.assertEqual(self.cmd(command, request_id=1000 + index), reply)
            self.assertEqual(self.lib.test_sends(), count)
            for offset in (22, 29, 36):
                invalid = bytearray(raw)
                invalid[offset] ^= 1
                invalid[37] = sum(invalid[21:37]) & 255
                self.cmd(f"SEND_PHASE {EPOCH} " + invalid.hex(), "ERR FRAME_POLICY")
            self.assertEqual(self.lib.test_sends(), count)

    def test_phase_probe_is_self_addressed_non_ec_and_cached(self):
        """Keep phase probes self-addressed, diagnostic-only, and replay-safe."""
        self.configured()
        self.arm()
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex())
        self.lib.test_advance(64)
        text = f"PHASE_PROBE {EPOCH} 42 11223344"
        self.cmd(text, request_id=1000)
        raw = self.sent()
        self.assertEqual(len(raw), 38)
        self.assertEqual(raw[:11], bytes.fromhex("018842" + GW + GW))
        self.assertEqual(raw[11:28], b"EC_PHASE_PROBE_V1")
        self.assertEqual(raw[28:35], bytes.fromhex("11223344000000"))
        self.assertEqual(raw[35:].hex(), "ee7f00")
        self.cmd(text, request_id=1000)
        self.assertEqual(self.lib.test_sends(), 2)
        self.cmd(f"PHASE_PROBE {EPOCH} 100 11223344", "ERR SYNTAX")
        self.cmd(f"PHASE_PROBE {EPOCH} 42 112233", "ERR SYNTAX")

    def test_phase_busy_heartbeat_and_unknown_error(self):
        """Preserve phase references on CCA busy and invalidate them on uncertain errors."""
        self.configured()
        self.arm()
        self.lib.test_radio(2, 100)
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex(), "ERR TX 2")
        self.cmd(f"SEND_PHASE {EPOCH} " + phased_frame().hex(), "ERR PHASE_REFERENCE")
        self.lib.test_radio(0, 0)
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex())
        self.lib.test_advance(64)
        self.lib.test_radio(2, 0)
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex(), "ERR TX 2")
        result = self.cmd(f"SEND_PHASE {EPOCH} " + phased_frame().hex(), "ERR TX 2")
        self.assertIn(f"age_ticks={self.rtimer_hz // 2}", result)
        self.assertIn("anchor_gen=1", result)
        self.lib.test_radio(1, 0)
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex(), "ERR TX 1")
        self.cmd(f"SEND_PHASE {EPOCH} " + phased_frame().hex(), "ERR PHASE_REFERENCE")

    def test_phase_reference_expiry_disarm_and_new_session(self):
        """Invalidate phase references after expiry, disarming, or session changes."""
        self.configured()
        self.arm()
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex())
        self.lib.test_advance(12 * 128)
        self.cmd(f"SEND_PHASE {EPOCH} " + phased_frame().hex(), "ERR PHASE_REFERENCE")
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex())
        self.cmd("DISARM")
        self.arm()
        self.cmd(f"SEND_PHASE {EPOCH} " + phased_frame().hex(), "ERR PHASE_REFERENCE")
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex())
        self.configured()
        self.arm()
        self.cmd(f"SEND_PHASE {EPOCH} " + phased_frame().hex(), "ERR PHASE_REFERENCE")
        self.cmd(f"SEND {EPOCH} " + heartbeat().hex())
        self.arm(1)
        self.lib.test_advance(129)
        self.arm()
        self.cmd(f"SEND_PHASE {EPOCH} " + phased_frame().hex(), "ERR PHASE_REFERENCE")

    @classmethod
    def tearDownClass(cls):
        """Release temporary compiler outputs after the test suite."""
        cls.temp.cleanup()

    def setUp(self):
        """Restore clean firmware state before each independent test."""
        self.lib.test_reset()
        self.request_id = 1

    def cmd(self, text, expected="OK", request_id=None):
        """Submit a numbered command and require the expected response prefix.

        @param text Command or response text to process.
        @param expected Required response prefix, or None to accept any response.
        @param request_id Explicit command identifier, or None for the next identifier.

        @return Complete gateway response line."""
        ident = self.request_id if request_id is None else request_id
        self.request_id += 1
        response = self.lib.test_request(f"{ident} {text}".encode()).decode().strip()
        self.assertTrue(response.startswith(f"RSP {ident} {expected}"), response)
        return response

    def configured(self, mask="1f", slot=0):
        """Configure a fresh session containing the requested peer fixture.

        @param mask Hexadecimal peer acknowledgement mask.
        @param slot Peer table slot to configure."""
        self.cmd(f"NETWORK {GW} a7 {EPOCH}")
        self.cmd(f"PEER SET {slot} {DEV} {mask}")

    def arm(self, seconds=30):
        """Enable the session transmit lease and automatic acknowledgements.

        @param seconds Duration in seconds."""
        self.cmd(f"ARM {EPOCH} {seconds}")
        self.cmd(f"ACK {EPOCH} 1")

    def receive(self, raw):
        """Deliver a radio frame to the real callback and return its UART output.

        @param raw Raw radio frame bytes.

        @return Decoded UART records emitted by the callback."""
        return self.lib.test_receive(raw, len(raw)).decode()

    def test_boot_capabilities_and_no_configuration(self):
        """Advertise capabilities while keeping an unconfigured gateway inactive."""
        self.cmd("PING", "OK EC-GATEWAY 3")
        self.assertIn("peers=256", self.cmd("CAPS"))
        self.assertIn(f"baud={self.baudrate} ", self.cmd("CAPS"))
        self.assertIn("configured=0", self.cmd("STATUS"))
        self.cmd(f"ARM {EPOCH} 1", "ERR NOT_CONFIGURED")
        self.assertNotIn("ACKTX", self.receive(report()))

    def test_full_table_last_peer_and_clear(self):
        """Support the final peer slot and remove all peers when cleared."""
        self.cmd(f"NETWORK {GW} a7 {EPOCH}")
        for i in range(255):
            self.cmd(f"PEER SET {i} {0x10000000+i:08x} 1f")
        self.cmd(f"PEER SET 255 {DEV} 1f")
        self.assertIn("peers=256", self.cmd("STATUS"))
        self.cmd(f"PEER SET 256 12345678 1f", "ERR PEER")
        self.arm()
        self.assertIn("ACKTX", self.receive(report()))
        self.cmd("DISARM")
        self.cmd("PEER CLEAR")
        self.assertIn("peers=0", self.cmd("STATUS"))
        self.cmd("CHECK " + report().hex(), "OK NO_ACK")

    def test_duplicate_replace_delete_and_invalid_identity(self):
        """Enforce peer identity rules across replacement and deletion."""
        self.configured()
        self.cmd(f"PEER SET 1 {DEV} 1f", "ERR DUPLICATE_PEER")
        for device in ("00000000", "ffffffff", GW):
            self.cmd(f"PEER SET 0 {device} 1f", "ERR PEER")
        self.cmd(f"PEER SET 0 {DEV} 20", "ERR PEER")
        self.assertIn("peers=1", self.cmd("STATUS"))
        self.cmd("PEER SET 0 12345678 00")
        self.cmd("CHECK " + report().hex(), "OK NO_ACK")
        self.cmd("PEER DEL 0")
        self.cmd("PEER DEL 0")
        self.assertIn("used=0", self.cmd("PEER GET 0"))

    def test_ack_masks_and_peer_isolation(self):
        """Restrict acknowledgements to each peer's allowed report types and network."""
        for bit, opcode in ((1, 5), (2, 0x15), (4, 0xA0), (8, 8)):
            self.configured(f"{bit:02x}")
            for candidate in (5, 0x15, 0xA0, 8):
                self.cmd(
                    "CHECK " + report(opcode=candidate).hex(),
                    "OK ACKHEX" if candidate == opcode else "OK NO_ACK",
                )
            self.cmd("CHECK " + report(broadcast=True).hex(), "OK NO_ACK")
        self.configured("11")
        self.cmd("CHECK " + report(broadcast=True).hex(), "OK ACKHEX")
        self.cmd("CHECK " + report(opcode=8, broadcast=True).hex(), "OK NO_ACK")
        self.cmd("CHECK " + report(device="12345678").hex(), "OK NO_ACK")
        self.cmd("CHECK " + report(tag=0xB1).hex(), "OK NO_ACK")
        self.configured("10")
        self.cmd("CHECK " + report(broadcast=True).hex(), "OK NO_ACK")

    def test_lease_expiry_renewal_and_table_edit_gate(self):
        """Enforce lease expiry, renewal, and disarmed-only peer editing."""
        self.configured()
        self.arm(1)
        self.lib.test_advance(64)
        self.cmd(f"ARM {EPOCH} 1")
        self.lib.test_advance(80)
        self.assertIn("ACKTX", self.receive(report()))
        self.cmd("PEER CLEAR", "ERR ARMED")
        self.cmd(f"PEER SET 1 12345678 1f", "ERR ARMED")
        self.lib.test_advance(49)
        self.assertNotIn("ACKTX", self.receive(report()))
        self.cmd(f"ARM {EPOCH} 1")
        self.assertNotIn("ACKTX", self.receive(report()))
        self.cmd(f"ACK {EPOCH} 1")
        self.assertIn("ACKTX", self.receive(report()))

    def test_session_changes_reset_and_legacy_bypass_rejected(self):
        """Reject stale sessions and legacy commands that bypass session protection."""
        self.configured()
        self.arm()
        self.cmd("TX " + frame().hex(), "ERR SESSION")
        self.cmd("ARM 30", "ERR SESSION")
        self.cmd("ACK 1", "ERR SESSION")
        self.cmd("NETWORK " + GW + " a7 fedcba9876543210")
        self.assertIn("peers=0", self.cmd("STATUS"))
        self.cmd(f"SEND {EPOCH} " + frame().hex(), "ERR SESSION")
        self.lib.test_reset()
        self.cmd(f"SEND {EPOCH} " + frame().hex(), "ERR NOT_CONFIGURED")
        self.assertEqual(self.lib.test_sends(), 0)

    def test_dedup_send_and_id_reuse(self):
        """Prevent duplicate transmissions and reject request identifier reuse."""
        self.configured()
        self.arm()
        text = f"SEND {EPOCH} " + frame().hex()
        self.cmd(text, request_id=1000)
        self.cmd(text, request_id=1000)
        self.assertEqual(self.lib.test_sends(), 1)
        self.cmd("PING", "ERR ID_REUSE", request_id=1000)
        self.assertEqual(self.lib.test_sends(), 1)

    def test_frame_policy_and_zero_ack_peer(self):
        """Enforce transmit policy independently of a peer's acknowledgement mask."""
        self.configured("00")
        self.arm()
        self.assertNotIn("ACKTX", self.receive(report()))
        self.cmd(f"SEND {EPOCH} " + frame().hex())
        self.cmd(f"SEND {EPOCH} " + frame("12345678").hex(), "ERR FRAME_POLICY")
        bad = bytearray(frame())
        bad[7] ^= 1
        self.cmd(f"SEND {EPOCH} " + bad.hex(), "ERR FRAME_POLICY")

    def test_invalid_network_does_not_mutate(self):
        """Preserve active configuration after invalid network requests."""
        self.configured()
        for text in (
            f"NETWORK {GW} a7 0000000000000000",
            f"NETWORK {GW} ff {EPOCH}",
            f"NETWORK ffffffff a7 {EPOCH}",
        ):
            self.cmd(text, "ERR CONFIG")
        self.assertIn("peers=1", self.cmd("STATUS"))
        self.assertIn("session=" + EPOCH, self.cmd("STATUS"))

    def test_legacy_capture_compatibility(self):
        """Preserve legacy capture commands and optional receive metadata."""
        self.cmd(f"CONFIG {GW} a7 {DEV}")
        self.cmd("ARM 1")
        self.cmd("ACK 1")
        self.assertIn("ACKTX", self.receive(report(broadcast=True)))
        self.cmd("TX " + frame().hex())
        self.assertNotIn("RXMETA", self.receive(report()))
        self.cmd("META 1")
        self.assertIn("RXMETA", self.receive(report()))

    def test_transport_loss_discards_partial_command_through_newline(self):
        """Lost bytes must not turn a damaged command into an executable one."""
        self.configured()
        self.arm()
        self.lib.test_feed(b"901 DIS")
        self.lib.test_rx_loss()
        self.assertEqual(self.lib.test_feed(b"ARM\n"), b"")
        self.assertIn("armed=1", self.cmd("STATUS"))
        self.assertIn("input_errors=1", self.cmd("STATUS"))
        self.assertIn(b"RSP 902 OK", self.lib.test_feed(b"902 DISARM\n"))
        self.assertIn("armed=0", self.cmd("STATUS"))

    def test_malformed_queue_and_overflow_do_not_transmit(self):
        """Reject oversized or excess UART requests without radio transmission."""
        self.configured()
        self.arm()
        text = "999 SEND " + EPOCH + " " + frame().hex() + " " * 400 + "\n"
        self.lib.test_feed(text.encode())
        self.assertEqual(self.lib.test_sends(), 0)
        self.assertIn("input_errors=1", self.cmd("STATUS"))
        result = self.lib.test_feed(
            b"100 PING\n101 PING\n102 PING\n103 PING\n"
        ).decode()
        self.assertEqual(result.count("EC-GATEWAY 3"), 3)
        self.assertIn("input_errors=2", self.cmd("STATUS"))

    def test_receive_bounds_crc_and_no_ack_loops(self):
        """Reject truncated or corrupt reports and avoid acknowledgement loops."""
        self.configured()
        self.arm()
        for n in range(39):
            self.assertNotIn("ACKTX", self.receive(report()[:n]))
        bad = bytearray(report())
        bad[-1] ^= 1
        self.assertNotIn("ACKTX", self.receive(bytes(bad)))
        for opcode in (1, 7, 0xFA):
            self.assertNotIn("ACKTX", self.receive(report(opcode=opcode)))


class FastFirmwareUART(FirmwareUART):
    """Exercise the same radio/parser contract at the alternate application rate."""

    baudrate = 460800


if __name__ == "__main__":
    unittest.main()
