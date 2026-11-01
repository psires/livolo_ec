/*
 * UART commands run in process context. Fast responses run in MAC input before
 * any logging. Boot/configuration/lease expiry all leave RF transmission off.
 */
#ifdef EC_PLATFORM_MG21
#include "ec-mg21-platform.h"
#define EC_PLATFORM_POLL
#define ec_platform_status ec_mg21_status
#define ec_platform_bootloader ec_mg21_bootloader
#elif defined(EC_PLATFORM_NRF52840)
#include "ec-nrf-platform.h"
#define EC_PLATFORM_POLL
#define ec_platform_status ec_nrf_status
#define ec_platform_bootloader ec_nrf_bootloader
#else
#include "contiki.h"
#include "net/netstack.h"
#include "dev/uart0-arch.h"
#include <ti/drivers/dpl/HwiP.h>
#define EC_BOARD_NAME "codm-cc2652p"
#endif
#include "ec-runtime.h"
#include "ec-uart-config.h"
#include "ec-protocol.h"
#include "ec-peers.h"
#include "ec-phase.h"
#ifdef EC_ENABLE_SCHEDULER
#include "ec-scheduler.h"
#if EC_SCHED_HZ != RTIMER_SECOND
#error Scheduler and phase timestamps must use the same clock
#endif
#define EC_SCHED_CAPS                                                          \
	" scheduler=timed_v1 scheduler_slots=8 scheduler_hz=" EC_STRINGIFY(    \
		EC_SCHED_HZ) " scheduler_horizon_ms=30000 "                    \
			     "scheduler_late_ms=20 "                           \
			     "tx_sequence=adapter_when_hb "                    \
			     "query=transaction_v1 query_slots=1 "             \
			     "query_ac=slot_preserved_v1 "                     \
			     "query_grid=heartbeat_200ms_v1 "                  \
			     "output_grid=transaction_200ms_v1"
#else
#define EC_SCHED_CAPS ""
#endif
#include "../version.h"
#ifdef EC_ENABLE_UPDATES
#include "ec-update-uart.h"
#define EC_UPDATE_CAPS " update=uart_signed_v1 boot_version=" EC_BOOT_VERSION
#else
#define EC_UPDATE_CAPS " update=none boot_version=unknown"
#endif
#include <stdio.h>
#include <string.h>
#include <stdint.h>

#ifdef EC_AUTO_ACK_PHASE
#define EC_ACK_PHASE_CAPS " auto_ack_phase=tx_entry_or_zero_v1"
#else
#define EC_ACK_PHASE_CAPS ""
#endif

#define LINE_SIZE 320
#define COMMAND_SLOTS 4
__attribute__((section(".fw_version"), used)) const char ec_build_version[] =
	"EC-FW-VERSION=" EC_VERSION;
#ifndef EC_PLATFORM_POLL
PROCESS(gateway_process, "EC UART gateway");
AUTOSTART_PROCESSES(&gateway_process);
#endif
static ec_network_t network;
static uint8_t session[8];
static unsigned legacy, ack_enabled, lease_seconds, metadata_enabled;
static uint32_t revision;
static clock_time_t armed_at;
static unsigned long received, tx_attempts, tx_ok, ack_attempts, ack_ok,
	rx_drops;
static unsigned long meta_drops;
static char commands[COMMAND_SLOTS][LINE_SIZE];
static volatile unsigned command_head, command_tail, input_pos, discard_line;
static volatile unsigned long input_errors;
static uint32_t last_id;
static char last_command[LINE_SIZE], last_reply[1024];
static unsigned phase_valid;
static rtimer_clock_t phase_anchor_ticks;
static clock_time_t phase_anchor_clock;
static uint32_t phase_generation;
typedef struct {
	rtimer_clock_t entry, returned, anchor, age;
	uint32_t generation;
	uint8_t raw[125];
	unsigned len;
} phase_tx_t;

/**
 * @brief Invalidate the timing reference used for phase-aware transmissions.
 */
static void phase_clear(void)
{
	phase_valid = 0;
}

/**
 * @brief Expire stale phase references before they can be used for
 *   transmission.
 *
 * @param now Current high-resolution timer value.
 *
 * @return Nonzero if a usable phase reference remains, otherwise zero.
 */
static int phase_live(rtimer_clock_t now)
{
	if (phase_valid && ((clock_time_t)(clock_time() - phase_anchor_clock) >=
				    EC_PHASE_MAX_SECONDS * CLOCK_SECOND ||
			    (rtimer_clock_t)(now - phase_anchor_ticks) >=
				    EC_PHASE_MAX_SECONDS * RTIMER_SECOND))
		phase_clear();
	return phase_valid;
}

/**
 * @brief Check whether a byte range contains only zero values.
 *
 * @param p Bytes to inspect.
 * @param n Number of bytes to inspect.
 *
 * @return One for an all-zero range, otherwise zero.
 */
static int zeros(const uint8_t *p, unsigned n)
{
	while (n--)
		if (*p++)
			return 0;
	return 1;
}

/**
 * @brief Recognize a gateway heartbeat eligible to establish a phase reference.
 *
 * @param raw Candidate transmitted frame bytes.
 * @param len Number of frame bytes.
 *
 * @return Nonzero for a valid gateway heartbeat, otherwise zero.
 */
static int own_heartbeat(const uint8_t *raw, unsigned len)
{
	return len == 41 && ec_network_tx_allowed(&network, raw, len) &&
	       !memcmp(raw + 3, "\xff\xff\xff\xff", 4) && zeros(raw + 11, 8) &&
	       raw[20] == 0x0e && raw[22] == 8 && zeros(raw + 23, 6) &&
	       zeros(raw + 38, 3);
}

/**
 * @brief Check that an outgoing frame is eligible for a phase timing trailer.
 *
 * @param raw Candidate frame bytes with a zero timing trailer.
 * @param len Number of frame bytes.
 *
 * @return Nonzero for an eligible frame, otherwise zero.
 */
static int phase_frame_allowed(const uint8_t *raw, unsigned len)
{
	/* The network policy enforces identities, tag and checksum. */
	return len == 41 && ec_network_tx_allowed(&network, raw, len) &&
	       memcmp(raw + 3, network.gateway, 4) &&
	       memcmp(raw + 3, "\xff\xff\xff\xff", 4) && zeros(raw + 11, 4) &&
	       !memcmp(raw + 15, raw + 3, 4) && raw[20] == 0x1e &&
	       (raw[22] == 2 || ((raw[22] == 0x10 || raw[22] == 0xac) &&
				 zeros(raw + 29, 7) && raw[36] == 1)) &&
	       !memcmp(raw + 23, raw + 3, 4) && zeros(raw + 38, 3);
}

/**
 * @brief Expire the transmit lease and report whether transmission is enabled.
 *
 * @return Nonzero for a configured network with a live lease, otherwise zero.
 */
static int armed(void)
{
	if (lease_seconds &&
	    (clock_time_t)(clock_time() - armed_at) >=
		    (clock_time_t)(lease_seconds * CLOCK_SECOND)) {
		lease_seconds = 0;
		ack_enabled = 0;
		phase_clear();
#ifdef EC_ENABLE_SCHEDULER
		ec_scheduler_clear();
#endif
	}
	return network.configured && lease_seconds != 0;
}

/**
 * @brief Transmit an authorized frame under the active lease and track its
 *   timing.
 *
 * @param raw Frame bytes already checked against the applicable transmit
 *   policy.
 * @param len Number of frame bytes, at most 125.
 * @param fast Nonzero to bypass clear-channel assessment for a prompt reply.
 * @param phase Optional phase result; requests a trailer using the current
 *   reference.
 * @param ack_wire Mutable automatic-reply bytes, or NULL for other sends.
 *   With EC_AUTO_ACK_PHASE, stamp in place so ACKTX logs actual wire bytes.
 *
 * @return Radio status, -1 when disarmed, or -2 without a usable phase
 *   reference.
 */
static int radio_send_impl(const uint8_t *raw, unsigned len, int fast,
			   phase_tx_t *phase, uint8_t *ack_wire)
{
	rtimer_clock_t entry;
	clock_time_t entry_clock;
	int result;
#ifdef EC_ENABLE_SCHEDULER
	uint8_t wire[125];
#endif

	if (!armed())
		return -1;
#ifdef EC_ENABLE_SCHEDULER
	if (!fast && ec_scheduler.heartbeat_active) {
		memcpy(wire, raw, len);
		wire[2] = ++ec_scheduler.sequence;
		raw = wire;
	}
#endif
	if (!fast)
		NETSTACK_RADIO.set_value(RADIO_PARAM_TX_MODE,
					 RADIO_TX_MODE_SEND_ON_CCA);
	/* This is radio-call entry, NOT SFD. CCA and scheduler delay follow it. */
	entry = RTIMER_NOW();
	entry_clock = clock_time();
	if (phase) {
		if (!phase_live(entry)) {
			NETSTACK_RADIO.set_value(RADIO_PARAM_TX_MODE, 0);
			return -2;
		}
		phase->entry = entry;
		phase->anchor = phase_anchor_ticks;
		phase->age = entry - phase_anchor_ticks;
		phase->generation = phase_generation;
		phase->len = len;
		memcpy(phase->raw, raw, len);
		if (!ec_phase_encode(phase->age, RTIMER_SECOND,
				     phase->raw + len - 3)) {
			NETSTACK_RADIO.set_value(RADIO_PARAM_TX_MODE, 0);
			return -2;
		}
		raw = phase->raw;
	}
#ifdef EC_AUTO_ACK_PHASE
	/* Keep the immediate, no-CCA ACK path. Missing/stale references retain
	 * the existing zero trailer rather than delaying or dropping the ACK. */
	if (fast && ack_wire && phase_live(entry))
		ec_phase_encode(entry - phase_anchor_ticks, RTIMER_SECOND,
				ack_wire + len - 3);
#else
	(void)ack_wire;
#endif
	tx_attempts++;
	result = NETSTACK_RADIO.send(raw, len);
	if (phase)
		phase->returned = RTIMER_NOW();
	if (result == RADIO_TX_OK)
		tx_ok++;
	if (!fast && own_heartbeat(raw, len)) {
		if (result == RADIO_TX_OK) {
			phase_anchor_ticks = entry;
			phase_anchor_clock = entry_clock;
			phase_generation++;
			phase_valid = 1;
		} else if (result != RADIO_TX_COLLISION) {
			phase_clear();
		}
	}
	if (!fast)
		NETSTACK_RADIO.set_value(RADIO_PARAM_TX_MODE, 0);
	return result;
}

/**
 * @brief Transmit a policy-approved frame without adding a phase trailer.
 *
 * @param raw Validated frame bytes.
 * @param len Number of frame bytes.
 * @param fast Nonzero for a prompt reply without clear-channel assessment.
 *
 * @return Radio status, or -1 when the gateway is disarmed.
 */
static int radio_send(const uint8_t *raw, unsigned len, int fast)
{
	return radio_send_impl(raw, len, fast, NULL, NULL);
}

/**
 * @brief Send a phase-aware frame and describe its outcome for a UART response.
 *
 * @param raw Validated frame ending in a three-byte timing trailer.
 * @param len Number of frame bytes, between three and 125.
 * @param result Destination for the UART response text.
 * @param cap Available bytes in result.
 */
static void phase_send_reply(const uint8_t *raw, unsigned len, char *result,
			     size_t cap)
{
	phase_tx_t tx;
	char hex[251];
	int r = radio_send_impl(raw, len, 0, &tx, NULL);

	if (r == -2) {
		snprintf(result, cap, "ERR PHASE_REFERENCE");
		return;
	}
	if (r == -1) {
		snprintf(result, cap, "ERR DISARMED");
		return;
	}
	ec_hex(tx.raw, tx.len, hex);
	snprintf(result, cap,
		 "%s TX %d timestamp=tx_entry entry_ticks=%lu return_ticks=%lu "
		 "anchor_ticks=%lu anchor_gen=%lu age_ticks=%lu raw=%s",
		 r == RADIO_TX_OK ? "OK" : "ERR", r, (unsigned long)tx.entry,
		 (unsigned long)tx.returned, (unsigned long)tx.anchor,
		 (unsigned long)tx.generation, (unsigned long)tx.age, hex);
}

#ifdef EC_ENABLE_SCHEDULER
int ec_scheduler_phase_reference(uint32_t *anchor, uint32_t *generation)
{
	if (!phase_live(RTIMER_NOW()))
		return 0;
	*anchor = phase_anchor_ticks;
	*generation = phase_generation;
	return 1;
}

void ec_scheduler_transmit(ec_sched_tx_t *tx, int phase)
{
	phase_tx_t stamped = { 0 };

	tx->entry = RTIMER_NOW();
	tx->result = radio_send_impl(tx->raw, tx->len, 0,
				     phase ? &stamped : NULL, NULL);
	tx->returned = RTIMER_NOW();
	if (phase && tx->result >= 0) {
		tx->entry = stamped.entry;
		tx->returned = stamped.returned;
		tx->anchor = stamped.anchor;
		tx->age = stamped.age;
		tx->generation = stamped.generation;
		memcpy(tx->raw, stamped.raw, stamped.len);
	} else if (!phase && ec_scheduler.heartbeat_active) {
		tx->raw[2] = ec_scheduler.sequence;
	}
}

/**
 * @brief Format retained queue and heartbeat diagnostics without draining jobs.
 * @param out Output buffer.
 * @param cap Output capacity including NUL.
 */
static void scheduler_status(char *out, size_t cap)
{
	unsigned i, pending = 0, done = 0;

	for (i = 0; i < EC_SCHED_SLOTS; i++) {
		pending += ec_scheduler.jobs[i].state == EC_SCHED_PENDING;
		done += ec_scheduler.jobs[i].state == EC_SCHED_DONE;
	}
	snprintf(out, cap,
		 "OK now=%lu free=%u pending=%u done=%u full=%lu late=%lu "
		 "cancelled=%lu hb_active=%u hb_due=%lu hb_sent=%lu "
		 "hb_missed=%lu hb_retries=%lu hb_late_max=%lu hb_last_due=%lu "
		 "hb_last_entry=%lu hb_result=%d phase_valid=%u "
		 "phase_anchor_ticks=%lu phase_anchor_gen=%lu",
		 (unsigned long)RTIMER_NOW(), EC_SCHED_SLOTS - pending - done,
		 pending, done, (unsigned long)ec_scheduler.full,
		 (unsigned long)ec_scheduler.late,
		 (unsigned long)ec_scheduler.cancelled,
		 ec_scheduler.heartbeat_active,
		 (unsigned long)ec_scheduler.heartbeat_due,
		 (unsigned long)ec_scheduler.heartbeat_sent,
		 (unsigned long)ec_scheduler.heartbeat_missed,
		 (unsigned long)ec_scheduler.heartbeat_retries,
		 (unsigned long)ec_scheduler.heartbeat_late_max,
		 (unsigned long)ec_scheduler.heartbeat_last_due,
		 (unsigned long)ec_scheduler.heartbeat_last_entry,
		 ec_scheduler.heartbeat_result, phase_live(RTIMER_NOW()),
		 (unsigned long)phase_anchor_ticks,
		 (unsigned long)phase_generation);
}
/**
 * @brief Read the retained transaction, latest TX and native confirmation.
 * @param out Output buffer, never consumes the result.
 * @param cap Available bytes including terminating NUL.
 */
static void query_status(char *out, size_t cap)
{
	ec_query_t *q = &ec_scheduler.query;
	char tx[251], rx[79];

	ec_hex(q->tx.raw, q->tx.len, tx);
	ec_hex(q->report, q->report_len, rx);
	snprintf(
		out, cap,
		"OK QUERY id=%lu state=%s outcome=%u started=%lu deadline=%lu "
		"due=%lu rounds=%u sent=%u attempts=%u retries=%u late_max=%lu "
		"tx_result=%d entry_ticks=%lu return_ticks=%lu "
		"anchor_ticks=%lu "
		"anchor_gen=%lu age_ticks=%lu raw=%s rx_ticks=%lu "
		"rx_sequence=%lu "
		"rssi=%d lqi=%u report=%s grid=%u grid_wait_ticks=%lu "
		"grid_skips=%lu output=%u",
		(unsigned long)q->id,
		q->state == EC_SCHED_DONE ? "done" : "pending", q->outcome,
		(unsigned long)q->started, (unsigned long)q->deadline,
		(unsigned long)q->due, q->rounds, q->sent, q->attempts,
		q->retries, (unsigned long)q->late_max, q->tx.result,
		(unsigned long)q->tx.entry, (unsigned long)q->tx.returned,
		(unsigned long)q->tx.anchor, (unsigned long)q->tx.generation,
		(unsigned long)q->tx.age, q->tx.len ? tx : "-",
		(unsigned long)q->rx_ticks, (unsigned long)q->rx_sequence,
		q->rssi, q->lqi, q->report_len ? rx : "-", q->grid,
		(unsigned long)q->grid_wait_ticks, (unsigned long)q->grid_skips,
		q->output);
}

#endif

void ec_receive(const uint8_t *raw, unsigned len, int rssi, unsigned lqi)
{
	uint8_t ack[41];
	size_t ack_len = 0;
	int ack_result = -1;
	rtimer_clock_t start = RTIMER_NOW(), elapsed = 0;
	clock_time_t callback_ticks = clock_time();
	char line[340];

	received++;
	if (armed() && ack_enabled) {
		ack_len = ec_network_ack(&network, raw, len, ack, sizeof(ack));
		if (ack_len) {
			ack_attempts++;
			ack_result =
				radio_send_impl(ack, ack_len, 1, NULL, ack);
			if (ack_result == RADIO_TX_OK)
				ack_ok++;
			elapsed = RTIMER_NOW() - start;
		}
	}
#ifdef EC_ENABLE_SCHEDULER
	if (armed())
		ec_query_receive(raw, len, start, received, rssi, lqi);
#endif
	/* Bulk receive logs may be dropped, but never stall an acknowledgement. */
	if (len <= 125) {
		int n = snprintf(line, sizeof(line), "RX %lu %lu %d %u %u ",
				 received, (unsigned long)callback_ticks, rssi,
				 lqi, len);

		if (n > 0 && (unsigned)n + 2 * len + 2 < sizeof(line)) {
			ec_hex(raw, len, line + n);
			n += 2 * len;
			line[n++] = '\n';
			if (ec_uart_free() > (unsigned)n + 1024)
				uart0_write(line, n);
			else
				rx_drops++;
		}
	}
	if (metadata_enabled) {
		int n = snprintf(line, sizeof(line),
				 "RXMETA %lu callback_rtimer=%lu\n", received,
				 (unsigned long)start);

		if (ec_uart_free() > (unsigned)n + 1024)
			uart0_write(line, n);
		else
			meta_drops++;
	}
	if (ack_len) {
		char hex[83];
		int n;

		ec_hex(ack, ack_len, hex);
		n = snprintf(line, sizeof(line),
			     "ACKTX %lu result=%d work_ticks=%lu %s\n",
			     received, ack_result, (unsigned long)elapsed, hex);
		uart0_write(line, n);
	}
}

/**
 * @brief Collect a UART command line for deferred processing by the gateway.
 *
 * @param c Received character.
 *
 * @return Zero, as required by the UART input callback interface.
 */
static int input_byte(unsigned char c)
{
	if (c == '\r')
		return 0;
	if (c == '\n') {
		unsigned next = (command_head + 1) % COMMAND_SLOTS;

		if (discard_line || next == command_tail) {
			input_errors++;
		} else if (input_pos) {
			commands[command_head][input_pos] = 0;
			command_head = next;
#ifndef EC_PLATFORM_POLL
			process_poll(&gateway_process);
#endif
		}
		discard_line = 0;
		input_pos = 0;
	} else if (!discard_line) {
		if (c < 32 || c > 126 || input_pos >= LINE_SIZE - 1)
			discard_line = 1;
		else
			commands[command_head][input_pos++] = (char)c;
	}
	return 0;
}

/**
 * @brief Parse an unsigned decimal command argument without overflow.
 *
 * @param s NUL-terminated decimal text.
 * @param value Parsed value, unchanged on failure.
 *
 * @return One for a valid value, otherwise zero.
 */
static int number(const char *s, uint32_t *value)
{
	uint32_t n = 0;

	if (!*s)
		return 0;
	while (*s) {
		if (*s < '0' || *s > '9' || n > (UINT32_MAX - (*s - '0')) / 10)
			return 0;
		n = n * 10 + (*s++ - '0');
	}
	*value = n;
	return 1;
}

/**
 * @brief Check whether a command belongs to the active non-legacy session.
 *
 * @param text Hexadecimal session token from the command.
 *
 * @return Nonzero for the current configured session, otherwise zero.
 */
static int session_valid(const char *text)
{
	uint8_t candidate[8];

	return network.configured && !legacy &&
	       ec_unhex(text, candidate, 8) == 8 &&
	       !memcmp(session, candidate, 8);
}

/**
 * @brief Consume the next space-delimited token from a mutable command line.
 *
 * @param cursor Position to scan, advanced past the returned token.
 *
 * @return Token in the original buffer, or NULL at the end of the line.
 */
static char *word(char **cursor)
{
	char *p = *cursor;
	char *start;

	while (*p == ' ')
		p++;
	if (!*p) {
		*cursor = p;
		return NULL;
	}
	start = p;
	while (*p && *p != ' ')
		p++;
	if (*p)
		*p++ = 0;
	*cursor = p;
	return start;
}

/**
 * @brief Describe the gateway configuration, counters and current lease state.
 *
 * @param out Destination for the NUL-terminated status text.
 * @param cap Available bytes in out.
 */
static void status(char *out, size_t cap)
{
	int live = armed();
	char gw[9], dev[9], epoch[17];

	ec_hex(network.gateway, 4, gw);
	ec_hex(network.slots[0].device, 4, dev);
	ec_hex(session, 8, epoch);
	snprintf(out, cap,
		 "channel=11 rx_mode=0 configured=%u gateway=%s tag=%02x "
		 "device=%s "
		 "session=%s revision=%lu peers=%u legacy=%u meta=%u "
		 "armed=%d ack=%u lease=%u rx=%lu tx=%lu tx_ok=%lu ack_tx=%lu "
		 "ack_ok=%lu "
		 "rx_drops=%lu uart_drops=%lu meta_drops=%lu input_errors=%lu "
		 "ticks=%lu "
		 "phase_valid=%u phase_anchor_ticks=%lu phase_anchor_gen=%lu "
		 "fw_version=" EC_VERSION,
		 network.configured, gw, network.tag, dev, epoch,
		 (unsigned long)revision, network.count, legacy,
		 metadata_enabled, live, ack_enabled, lease_seconds, received,
		 tx_attempts, tx_ok, ack_attempts, ack_ok, rx_drops,
		 ec_uart_dropped(), meta_drops, input_errors,
		 (unsigned long)clock_time(), phase_live(RTIMER_NOW()),
		 (unsigned long)phase_anchor_ticks,
		 (unsigned long)phase_generation);
#ifdef EC_PLATFORM_POLL
	ec_platform_status(out + strlen(out), cap - strlen(out));
#endif
}

/**
 * @brief Execute one UART request and cache its response for duplicate
 *   detection.
 *
 * @param line Mutable NUL-terminated command line, at most LINE_SIZE - 1 bytes.
 */
static void command(char *line)
{
	char original[LINE_SIZE], result[896] = "ERR SYNTAX";
	char *tok[8], *save = line;
	char *t;
	unsigned count = 0;
	uint32_t id = 0, value;

	strcpy(original, line);
	t = word(&save);
	while (t && count < 8) {
		tok[count++] = t;
		t = word(&save);
	}
	if (t || count < 2 || !number(tok[0], &id) || !id) {
		uart0_write("RSP 0 ERR SYNTAX\n", 17);
		return;
	}
	if (id == last_id) {
		if (!strcmp(original, last_command)) {
			uart0_write(last_reply, strlen(last_reply));
		} else {
			char error[64];
			int n = snprintf(error, sizeof(error),
					 "RSP %lu ERR ID_REUSE\n",
					 (unsigned long)id);

			uart0_write(error, n);
		}
		return;
	}
	if (count == 2 && !strcmp(tok[1], "PING")) {
		strcpy(result, "OK EC-GATEWAY 3");
#ifdef EC_PLATFORM_POLL
	} else if (count == 2 && !strcmp(tok[1], "BOOTLOADER")) {
		lease_seconds = 0;
		ack_enabled = 0;
		phase_clear();
		NETSTACK_RADIO.off();
		strcpy(result, "OK BOOTLOADER");
#endif
	} else if (count == 2 && !strcmp(tok[1], "VERSION")) {
		strcpy(result,
		       "OK fw_version=" EC_VERSION " board=" EC_BOARD_NAME
		       " proto=3" EC_UPDATE_CAPS);
	} else if (count == 2 && !strcmp(tok[1], "CAPS")) {
		snprintf(result, sizeof(result),
			 "OK proto=3 peers=%u ack_mask=1f max_raw=125 "
			 "max_line=319 "
			 "command_slots=3 request_cache=1 lease_max=60 "
			 "session_bytes=8 "
			 "channel=11 power_dbm=0 baud=%lu ticks_hz=%lu "
			 "ticks_bits=%u "
			 "rtimer_hz=%lu rtimer_bits=%u rx_timestamp=callback "
			 "meta=1 "
			 "tx_policy=ec8c legacy_config=1 persistence=none "
			 "phase=tx_entry_v1 phase_scale=65500 "
			 "phase_max_ms=12000 phase_probe=1 "
			 "phase_queries=10_ac_v1 "
			 "board=" EC_BOARD_NAME
			 " fw_version=" EC_VERSION EC_UPDATE_CAPS EC_SCHED_CAPS
				 EC_ACK_PHASE_CAPS,
			 EC_PEER_CAPACITY, (unsigned long)EC_UART_BAUD,
			 (unsigned long)CLOCK_SECOND,
			 (unsigned)(8 * sizeof(clock_time_t)),
			 (unsigned long)RTIMER_SECOND,
			 (unsigned)(8 * sizeof(rtimer_clock_t)));
	} else if (count == 2 && !strcmp(tok[1], "STATUS")) {
		strcpy(result, "OK ");
		status(result + 3, sizeof(result) - 3);
	} else if (count == 2 && !strcmp(tok[1], "DISARM")) {
		ack_enabled = 0;
		lease_seconds = 0;
		phase_clear();
#ifdef EC_ENABLE_SCHEDULER
		ec_scheduler_clear();
#endif
		strcpy(result, "OK DISARMED");
#ifdef EC_ENABLE_UPDATES
	} else if (!strncmp(tok[1], "UPDATE_", 7)) {
		if (armed() && strcmp(tok[1], "UPDATE_STATUS") &&
		    strcmp(tok[1], "UPDATE_CONFIRM")) {
			strcpy(result, "ERR DISARM_REQUIRED");
		} else {
			if (!strcmp(tok[1], "UPDATE_BEGIN"))
				NETSTACK_RADIO.off();
			ec_update_command(count, tok, result, sizeof(result));
		}
	} else if (ec_update_pending()) {
		strcpy(result, "ERR UPDATE_BUSY");
#endif
	} else if (count == 3 && !strcmp(tok[1], "META")) {
		if (!strcmp(tok[2], "0") || !strcmp(tok[2], "1")) {
			metadata_enabled = tok[2][0] == '1';
			strcpy(result, "OK META");
		}
	} else if (count == 5 && !strcmp(tok[1], "NETWORK")) {
		uint8_t gw[4], tag, epoch[8], nonzero = 0;
		unsigned i;

		if (ec_unhex(tok[4], epoch, 8) == 8)
			for (i = 0; i < 8; i++)
				nonzero |= epoch[i];
		if (nonzero && ec_unhex(tok[2], gw, 4) == 4 &&
		    ec_unhex(tok[3], &tag, 1) == 1 &&
		    ec_network_set(&network, gw, tag)) {
			legacy = 0;
			ack_enabled = 0;
			lease_seconds = 0;
			phase_clear();
#ifdef EC_ENABLE_SCHEDULER
			ec_scheduler_clear();
			ec_scheduler.last_id = 0;
#endif
			memcpy(session, epoch, 8);
			revision++;
			strcpy(result, "OK NETWORK DISARMED");
		} else {
			strcpy(result, "ERR CONFIG");
		}
	} else if (count >= 3 && !strcmp(tok[1], "PEER")) {
		if (!network.configured) {
			strcpy(result, "ERR NOT_CONFIGURED");
		} else if (count == 4 && !strcmp(tok[2], "GET")) {
			if (!number(tok[3], &value) ||
			    value >= EC_PEER_CAPACITY) {
				strcpy(result, "ERR PEER");
			} else {
				ec_slot_t *p = &network.slots[value];

				if (!p->used) {
					snprintf(result, sizeof(result),
						 "OK PEER slot=%lu used=0",
						 (unsigned long)value);
				} else {
					char dev[9];

					ec_hex(p->device, 4, dev);
					snprintf(result, sizeof(result),
						 "OK PEER slot=%lu used=1 "
						 "device=%s ack_mask=%02x",
						 (unsigned long)value, dev,
						 p->ack_mask);
				}
			}
		} else if (armed()) {
			strcpy(result, "ERR ARMED");
		} else if (count == 3 && !strcmp(tok[2], "CLEAR")) {
			ec_peers_clear(&network);
			revision++;
			strcpy(result, "OK PEERS_CLEARED");
		} else if (count == 4 && !strcmp(tok[2], "DEL")) {
			if (!number(tok[3], &value) ||
			    !ec_peer_del(&network, value)) {
				strcpy(result, "ERR PEER");
			} else {
				revision++;
				strcpy(result, "OK PEER_DELETED");
			}
		} else if (count == 6 && !strcmp(tok[2], "SET")) {
			uint8_t dev[4], mask;
			int r = 0;

			if (number(tok[3], &value) &&
			    ec_unhex(tok[4], dev, 4) == 4 &&
			    ec_unhex(tok[5], &mask, 1) == 1)
				r = ec_peer_set(&network, value, dev, mask);
			if (r == 1) {
				revision++;
				strcpy(result, "OK PEER_SET");
			} else {
				strcpy(result, r == -1 ? "ERR DUPLICATE_PEER"
						       : "ERR PEER");
			}
		}
	} else if (count == 5 && !strcmp(tok[1], "CONFIG")) {
		ec_peer_t candidate;

		if (ec_unhex(tok[2], candidate.gateway, 4) == 4 &&
		    ec_unhex(tok[3], &candidate.tag, 1) == 1 &&
		    ec_unhex(tok[4], candidate.device, 4) == 4 &&
		    ec_identity_valid(candidate.gateway) &&
		    ec_identity_valid(candidate.device) &&
		    memcmp(candidate.gateway, candidate.device, 4) &&
		    candidate.tag && candidate.tag != 255) {
			ack_enabled = 0;
			lease_seconds = 0;
			phase_clear();
			ec_network_set(&network, candidate.gateway,
				       candidate.tag);
			ec_peer_set(&network, 0, candidate.device, EC_ACK_MASK);
#ifdef EC_ENABLE_SCHEDULER
			ec_scheduler_clear();
			ec_scheduler.last_id = 0;
#endif
			memset(session, 0, 8);
			legacy = 1;
			revision++;
			strcpy(result, "OK CONFIGURED DISARMED");
		} else {
			strcpy(result, "ERR CONFIG");
		}
	} else if ((count == 3 || count == 4) && !strcmp(tok[1], "ARM")) {
		if (!network.configured) {
			strcpy(result, "ERR NOT_CONFIGURED");
		} else if ((count == 3 && !legacy) ||
			   (count == 4 && !session_valid(tok[2]))) {
			strcpy(result, "ERR SESSION");
		} else if (number(tok[count - 1], &value) && value >= 1 &&
			   value <= 60) {
			if (!armed())
				ack_enabled = 0;
			armed_at = clock_time();
			lease_seconds = value;
			snprintf(result, sizeof(result), "OK ARMED %lu",
				 (unsigned long)value);
		} else {
			strcpy(result, "ERR LEASE");
		}
	} else if ((count == 3 || count == 4) && !strcmp(tok[1], "ACK")) {
		if ((count == 3 && !legacy) ||
		    (count == 4 && !session_valid(tok[2]))) {
			strcpy(result, "ERR SESSION");
		} else if (!strcmp(tok[count - 1], "0")) {
			ack_enabled = 0;
			strcpy(result, "OK ACK_OFF");
		} else if (!strcmp(tok[count - 1], "1")) {
			if (armed()) {
				ack_enabled = 1;
				strcpy(result, "OK ACK_ON");
			} else {
				strcpy(result, "ERR DISARMED");
			}
		}
#ifdef EC_ENABLE_SCHEDULER
	} else if (!strcmp(tok[1], "SCHED") || !strcmp(tok[1], "QUEUE") ||
		   !strcmp(tok[1], "QGET") || !strcmp(tok[1], "QDROP") ||
		   !strcmp(tok[1], "QCANCEL") || !strcmp(tok[1], "HEARTBEAT") ||
		   !strcmp(tok[1], "QUERY") || !strcmp(tok[1], "QUERY_GRID") ||
		   !strcmp(tok[1], "OUTPUT_GRID") ||
		   !strcmp(tok[1], "QUERY_GET") ||
		   !strcmp(tok[1], "QUERY_CANCEL") ||
		   !strcmp(tok[1], "QUERY_DROP")) {
		uint32_t job_id, due, grace, mode;
		uint8_t raw[125];
		int len, r;
		ec_sched_job_t *job;

		if (count < 3 || !session_valid(tok[2])) {
			strcpy(result, "ERR SESSION");
		} else if (!armed()) {
			strcpy(result, "ERR DISARMED");
		} else if (count == 3 && !strcmp(tok[1], "SCHED")) {
			scheduler_status(result, sizeof(result));
		} else if (count == 7 && !strcmp(tok[1], "OUTPUT_GRID")) {
			uint8_t baseline[3];
			len = ec_unhex(tok[6], raw, sizeof(raw));
			if (!number(tok[3], &job_id) ||
			    !number(tok[4], &mode) ||
			    ec_unhex(tok[5], baseline, sizeof(baseline)) != 3) {
				strcpy(result, "ERR SYNTAX");
			} else if (len != 41 ||
				   !phase_frame_allowed(raw, len) ||
				   raw[22] != 2) {
				strcpy(result, "ERR FRAME_POLICY");
			} else if (!ec_scheduler.heartbeat_active) {
				strcpy(result, "ERR HEARTBEAT_REQUIRED");
			} else {
				r = ec_output_begin(job_id, mode, baseline,
						    raw);
				strcpy(result, r == 0	 ? "OK OUTPUT"
					       : r == -2 ? "ERR QUERY_BUSY"
					       : r == -3 ? "ERR JOB_ID"
							 : "ERR SCHEDULE");
			}
		} else if (count == 8 && (!strcmp(tok[1], "QUERY") ||
					  !strcmp(tok[1], "QUERY_GRID"))) {
			len = ec_unhex(tok[7], raw, sizeof(raw));
			if (!number(tok[3], &job_id) || !number(tok[4], &due) ||
			    !number(tok[5], &grace) || !number(tok[6], &mode)) {
				strcpy(result, "ERR SYNTAX");
			} else if (len != 41 ||
				   !phase_frame_allowed(raw, len) ||
				   (raw[22] != 0x10 && raw[22] != 0xac) ||
				   (!strcmp(tok[1], "QUERY_GRID") &&
				    raw[22] != 0xac)) {
				strcpy(result, "ERR FRAME_POLICY");
			} else if (!ec_scheduler.heartbeat_active) {
				strcpy(result, "ERR HEARTBEAT_REQUIRED");
			} else {
				r = ec_query_begin(
					job_id, due, grace, mode, raw,
					!strcmp(tok[1], "QUERY_GRID"));
				strcpy(result, r == 0	 ? "OK QUERY"
					       : r == -2 ? "ERR QUERY_BUSY"
					       : r == -3 ? "ERR JOB_ID"
							 : "ERR SCHEDULE");
			}
		} else if (count == 4 && number(tok[3], &job_id) &&
			   (!strcmp(tok[1], "QUERY_GET") ||
			    !strcmp(tok[1], "QUERY_CANCEL") ||
			    !strcmp(tok[1], "QUERY_DROP"))) {
			ec_query_t *q = &ec_scheduler.query;
			if (!q->state || q->id != job_id) {
				strcpy(result, "ERR JOB_UNKNOWN");
			} else if (!strcmp(tok[1], "QUERY_DROP")) {
				if (q->state != EC_SCHED_DONE)
					strcpy(result, "ERR JOB_PENDING");
				else {
					memset(q, 0, sizeof(*q));
					strcpy(result, "OK DROPPED");
				}
			} else {
				if (!strcmp(tok[1], "QUERY_CANCEL"))
					ec_query_cancel();
				query_status(result, sizeof(result));
			}
		} else if (count == 5 && !strcmp(tok[1], "HEARTBEAT")) {
			len = ec_unhex(tok[4], raw, sizeof(raw));
			if (!number(tok[3], &due) || len != 41 ||
			    !own_heartbeat(raw, len))
				strcpy(result, "ERR FRAME_POLICY");
			else if (!ec_scheduler_heartbeat(due, raw))
				strcpy(result, "ERR SCHEDULE");
			else
				strcpy(result, "OK HEARTBEAT");
		} else if (count == 8 && !strcmp(tok[1], "QUEUE")) {
			len = ec_unhex(tok[7], raw, sizeof(raw));
			if (!number(tok[3], &job_id) || !number(tok[4], &due) ||
			    !number(tok[5], &grace) || !number(tok[6], &mode)) {
				strcpy(result, "ERR SYNTAX");
			} else if (len < 3 || mode > 1 ||
				   !ec_network_tx_allowed(&network, raw, len) ||
				   (mode == 1
					    ? (!phase_frame_allowed(raw, len) ||
					       (raw[22] != 0x10 &&
						raw[22] != 0xac))
					    : (memcmp(raw + 3, network.gateway,
						      4) ||
					       memcmp(raw + 7, network.gateway,
						      4)))) {
				strcpy(result, "ERR FRAME_POLICY");
			} else {
				r = ec_scheduler_add(job_id, due, grace, mode,
						     raw, len);
				strcpy(result, r == 0	 ? "OK QUEUED"
					       : r == -2 ? "ERR QUEUE_FULL"
					       : r == -3 ? "ERR JOB_ID"
							 : "ERR SCHEDULE");
			}
		} else if (count == 4 && number(tok[3], &job_id) &&
			   (!strcmp(tok[1], "QGET") ||
			    !strcmp(tok[1], "QCANCEL") ||
			    !strcmp(tok[1], "QDROP"))) {
			job = ec_scheduler_find(job_id);
			if (!job) {
				strcpy(result, "ERR JOB_UNKNOWN");
			} else if (!strcmp(tok[1], "QDROP")) {
				if (job->state != EC_SCHED_DONE)
					strcpy(result, "ERR JOB_PENDING");
				else {
					memset(job, 0, sizeof(*job));
					strcpy(result, "OK DROPPED");
				}
			} else {
				char hex[251];
				if (!strcmp(tok[1], "QCANCEL"))
					ec_scheduler_cancel(job);
				ec_hex(job->tx.raw, job->tx.len, hex);
				snprintf(
					result, sizeof(result),
					"OK JOB id=%lu state=%s due=%lu "
					"result=%d "
					"entry_ticks=%lu return_ticks=%lu "
					"anchor_ticks=%lu "
					"anchor_gen=%lu age_ticks=%lu raw=%s",
					(unsigned long)job->id,
					job->state == EC_SCHED_DONE ? "done"
								    : "pending",
					(unsigned long)job->due, job->tx.result,
					(unsigned long)job->tx.entry,
					(unsigned long)job->tx.returned,
					(unsigned long)job->tx.anchor,
					(unsigned long)job->tx.generation,
					(unsigned long)job->tx.age, hex);
			}
		}
#endif
	} else if (count == 4 && !strcmp(tok[1], "SEND_PHASE")) {
		uint8_t raw[125];
		int len = ec_unhex(tok[3], raw, sizeof(raw));

		if (!network.configured)
			strcpy(result, "ERR NOT_CONFIGURED");
		else if (!session_valid(tok[2]))
			strcpy(result, "ERR SESSION");
		else if (!armed())
			strcpy(result, "ERR DISARMED");
		else if (len < 0 || !phase_frame_allowed(raw, len))
			strcpy(result, "ERR FRAME_POLICY");
		else
			phase_send_reply(raw, len, result, sizeof(result));
	} else if (count == 5 && !strcmp(tok[1], "PHASE_PROBE")) {
		uint8_t raw[38] = { 1, 0x88 }, sequence, nonce[4];

		if (!network.configured) {
			strcpy(result, "ERR NOT_CONFIGURED");
		} else if (!session_valid(tok[2])) {
			strcpy(result, "ERR SESSION");
		} else if (!armed()) {
			strcpy(result, "ERR DISARMED");
		} else if (ec_unhex(tok[3], &sequence, 1) != 1 ||
			   ec_unhex(tok[4], nonce, 4) != 4) {
			strcpy(result, "ERR SYNTAX");
		} else {
			raw[2] = sequence;
			memcpy(raw + 3, network.gateway, 4);
			memcpy(raw + 7, network.gateway, 4);
			memcpy(raw + 11, "EC_PHASE_PROBE_V1", 17);
			memcpy(raw + 28, nonce, 4);
			if (!ec_network_tx_allowed(&network, raw,
						   sizeof(raw))) {
				strcpy(result, "ERR FRAME_POLICY");
			} else {
				phase_send_reply(raw, sizeof(raw), result,
						 sizeof(result));
			}
		}
	} else if ((count == 3 &&
		    (!strcmp(tok[1], "TX") || !strcmp(tok[1], "CHECK"))) ||
		   (count == 4 && !strcmp(tok[1], "SEND"))) {
		uint8_t raw[125], ack[41];
		int len = ec_unhex(tok[count - 1], raw, sizeof(raw));

		if (len <= 0) {
			strcpy(result, "ERR HEX");
		} else if (!network.configured) {
			strcpy(result, "ERR NOT_CONFIGURED");
		} else if (!strcmp(tok[1], "CHECK")) {
			size_t n = ec_network_ack(&network, raw, len, ack,
						  sizeof(ack));

			if (n) {
				strcpy(result, "OK ACKHEX ");
				ec_hex(ack, n, result + 10);
			} else {
				strcpy(result, "OK NO_ACK");
			}
		} else if ((!strcmp(tok[1], "TX") && !legacy) ||
			   (!strcmp(tok[1], "SEND") &&
			    !session_valid(tok[2]))) {
			strcpy(result, "ERR SESSION");
		} else if (!armed()) {
			strcpy(result, "ERR DISARMED");
		} else if (!ec_network_tx_allowed(&network, raw, len)) {
			strcpy(result, "ERR FRAME_POLICY");
#ifdef EC_ENABLE_SCHEDULER
		} else if (ec_scheduler.heartbeat_active &&
			   own_heartbeat(raw, len)) {
			strcpy(result, "ERR HEARTBEAT_OWNED");
#endif
		} else {
			int r = radio_send(raw, len, 0);

			snprintf(result, sizeof(result), "%s TX %d",
				 r == RADIO_TX_OK ? "OK" : "ERR", r);
		}
	}
	last_id = id;
	strcpy(last_command, original);
	snprintf(last_reply, sizeof(last_reply), "RSP %lu %s\n",
		 (unsigned long)id, result);
	uart0_write(last_reply, strlen(last_reply));
#ifdef EC_PLATFORM_POLL
	if (!strcmp(result, "OK BOOTLOADER"))
		ec_platform_bootloader();
#endif
}

static clock_time_t status_at;

void ec_gateway_start(void)
{
	char line[180];
	int n;

	uart0_set_callback(input_byte);
	n = snprintf(line, sizeof(line),
		     "EC-GATEWAY 3 board=" EC_BOARD_NAME
		     " baud=%lu ticks=%lu rtimer_hz=%lu tx=disabled "
		     "fw_version=" EC_VERSION "\n",
		     (unsigned long)EC_UART_BAUD, (unsigned long)CLOCK_SECOND,
		     (unsigned long)RTIMER_SECOND);
	uart0_write(line, n);
	status_at = clock_time();
#ifdef EC_ENABLE_UPDATES
	ec_update_init();
#endif
}

void ec_gateway_poll(void)
{
#ifdef EC_ENABLE_UPDATES
	ec_update_poll();
#endif
	/* Expire leases even when neither UART nor RF traffic is arriving. */
	(void)armed();
#ifdef EC_ENABLE_SCHEDULER
	if (armed())
		ec_scheduler_poll();
#endif
	while (command_tail != command_head) {
		char line[LINE_SIZE];
		uintptr_t key = HwiP_disable();

		strcpy(line, commands[command_tail]);
		command_tail = (command_tail + 1) % COMMAND_SLOTS;
		HwiP_restore(key);
		command(line);
#ifdef EC_ENABLE_SCHEDULER
		if (armed())
			ec_scheduler_poll();
#endif
	}
	if ((clock_time_t)(clock_time() - status_at) >= 5 * CLOCK_SECOND) {
#ifdef EC_PLATFORM_POLL
		char line[896];
#else
		char line[700];
#endif

		strcpy(line, "STATUS ");
		status(line + 7, sizeof(line) - 9);
		strcat(line, "\n");
		uart0_write(line, strlen(line));
		status_at = clock_time();
	}
}

#ifndef EC_PLATFORM_POLL
/**
 * @brief Run the shared gateway from the Contiki scheduler.
 * @param ev Contiki event that resumes the gateway process.
 * @param data Event-specific payload supplied by Contiki.
 * @return Contiki protothread scheduling state.
 */
PROCESS_THREAD(gateway_process, ev, data)
{
	static struct etimer heartbeat;

	PROCESS_BEGIN();
	ec_gateway_start();
	etimer_set(&heartbeat, CLOCK_SECOND);
	while (1) {
		PROCESS_YIELD();
		ec_gateway_poll();
		if (etimer_expired(&heartbeat))
			etimer_reset(&heartbeat);
	}
	PROCESS_END();
}
#endif
