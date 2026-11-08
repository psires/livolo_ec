/* SPDX-License-Identifier: BSD-2-Clause */
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define main mg24_application_main
#include "../main.c"
#undef main

/* A conservative one-byte UART model: 10 bits at 115200 baud, rounded up. */
#define BYTE_US 87u
#define POLL_US 500u
#define WIRE_SIZE 65536u
static USART_TypeDef uart;
GPIO_TypeDef fixture_gpio;
unsigned fixture_irq_disabled;
uint32_t fixture_flags;
static uint32_t cleared_flags;
static uint32_t clock_us, shift_until, started_at, stop_requested_at;
static unsigned tx_enabled, shifting, in_irq, always_ready, arm_race;
static unsigned wire_used, starts, stops, frame_count, frame_goal;
static unsigned stop_requested, running, injected_loss, injected_expiry;
static unsigned maximum_irq_bytes, maximum_input_batch, previous_tail;
static unsigned irq_calls, shifted_byte, stop_while_pending, legacy_run;
static unsigned stop_requested_while_pending;
static char wire[WIRE_SIZE];
static jmp_buf finished;
enum scenario { BURST, OVERLOAD, FAIR, RX_LOSS, EXPIRY, LONG_INPUT, STOP_BUSY };
static enum scenario scenario;

/** @brief Retain exact copied output bytes and forbid silent fixture truncation.
 * @param byte One completed UART byte.
 */
static void record_byte(unsigned byte)
{
	assert(wire_used + 1 < sizeof(wire));
	wire[wire_used++] = (char)byte;
	wire[wire_used] = 0;
}

/** @brief Commit a real main/ISR TXDATA write into the timed fake UART. */
static void commit_register(void)
{
	if (uart.TXDATA == UINT32_MAX)
		return;
	assert(!shifting || always_ready);
	if (always_ready) {
		record_byte(uart.TXDATA);
	} else {
		shifted_byte = uart.TXDATA;
		shifting = 1;
		shift_until = clock_us + BYTE_US;
	}
	uart.TXDATA = UINT32_MAX;
}

USART_TypeDef *fixture_uart(void)
{
	commit_register();
	uart.STATUS = shifting && !always_ready ? 0 : USART_STATUS_TXBL;
	return &uart;
}

/** @brief Run one real TX ISR and enforce its fixed work bound. */
static void service_irq(void)
{
	unsigned before;

	if (!tx_enabled || fixture_irq_disabled || in_irq ||
	    (shifting && !always_ready))
		return;
	before = tx_sent;
	in_irq = 1;
	USART0_TX_IRQHandler();
	commit_register();
	in_irq = 0;
	assert(tx_sent >= before && tx_sent - before <= TX_IRQ_BUDGET);
	if (tx_sent - before > maximum_irq_bytes)
		maximum_irq_bytes = (unsigned)tx_sent - before;
	irq_calls++;
}

void fixture_enable(USART_TypeDef *usart, uint32_t flags)
{
	assert(usart == &uart);
	if (!(flags & USART_IEN_TXBL))
		return;
	if (arm_race) {
		/* A stale pending IRQ can run after publication, before IEN_SET. */
		arm_race = 0;
		tx_enabled = 1;
		service_irq();
	}
	tx_enabled = 1;
	service_irq();
}

void fixture_clear(USART_TypeDef *usart, uint32_t flags)
{
	assert(usart == &uart);
	cleared_flags |= flags;
	fixture_flags &= ~flags;
}

void fixture_disable(USART_TypeDef *usart, uint32_t flags)
{
	assert(usart == &uart && flags == USART_IEN_TXBL);
	commit_register();
	tx_enabled = 0;
}

void fixture_priority(unsigned irq, unsigned priority)
{
	assert(irq == USART0_TX_IRQn && priority == 15);
}

void fixture_init(USART_TypeDef *usart, const USART_InitAsync_TypeDef *config)
{
	assert(usart == &uart && config->baudrate == 115200);
}

/** @brief Advance physical UART time while main is occupied by radio work.
 * @param elapsed Microseconds of modeled CPU/main-loop work.
 */
static void advance(uint32_t elapsed)
{
	uint32_t until = clock_us + elapsed;

	commit_register();
	service_irq();
	while (shifting && shift_until <= until) {
		if (shift_until > clock_us)
			clock_us = shift_until;
		record_byte(shifted_byte);
		shifting = 0;
		service_irq();
	}
	clock_us = until;
	service_irq();
}

/** @brief Supply the core's modulo-32-bit radio clock.
 * @return Current fixture microseconds.
 */
static uint32_t now_us(void)
{
	return clock_us;
}

/** @brief Admit only receive-only channel 11 in output scheduling experiments.
 * @param channel Requested channel.
 * @return Zero confirms successful passive reception.
 */
static int start(unsigned channel)
{
	assert(channel == 11);
	starts++;
	started_at = clock_us;
	return 0;
}

/** @brief Retain cancellation latency independently of output completion.
 * @return Zero confirms the mock radio is idle.
 */
static int stop(void)
{
	stops++;
	stop_while_pending |= atomic_load(&tx_pending) != 0;
	if (stop_requested_at && !legacy_run)
		assert(clock_us - stop_requested_at < 100000);
	return 0;
}

/** @brief Forbid any real or synthetic transmission in these receive fixtures.
 * @param mac Borrowed request bytes.
 * @param length Request length.
 * @param cca Requested CCA mode.
 * @return Always fails after asserting no caller should reach this operation.
 */
static int send_frame(const uint8_t *mac, size_t length, unsigned cca)
{
	(void)mac;
	(void)length;
	(void)cca;
	assert(!"Unexpected radio transmission");
	return -1;
}

/** @brief Inject host bytes through the same bounded ring consumed by main.
 * @param text Complete ASCII request stream.
 */
static void input(const char *text)
{
	while (*text) {
		unsigned next = (head + 1) & (RX_SIZE - 1);

		assert(next != tail);
		rx[head] = (uint8_t)*text++;
		head = next;
	}
}

/** @brief Model expensive bounded radio polling, bursts, and maintenance input. */
static void poll(void)
{
	unsigned batch;

	if (!running)
		return;
	batch = (tail - previous_tail) & (RX_SIZE - 1);
	if (batch > maximum_input_batch)
		maximum_input_batch = batch;
	previous_tail = tail;
	advance(POLL_US);
	assert(clock_us < 62000000);
	if (!starts)
		return;
	while (frame_count < frame_goal &&
	       clock_us - started_at >= 10000 + frame_count * 2000) {
		uint8_t bytes[12] = { 0 };

		bytes[0] = (uint8_t)frame_count++;
		ec_rf_receive(bytes, 10, 1, -40, 80, clock_us);
	}
	if (scenario == EXPIRY && frame_count && atomic_load(&tx_pending) &&
	    !injected_expiry) {
		injected_expiry = 1;
		clock_us = started_at + 60000000;
		return;
	}
	if (scenario == RX_LOSS && frame_count && atomic_load(&tx_pending) &&
	    !injected_loss) {
		injected_loss = 1;
		lost = 1;
	}
	if (!stop_requested && (clock_us - started_at > 100000 ||
				(scenario == STOP_BUSY && frame_count &&
				 atomic_load(&tx_pending)))) {
		stop_requested = 1;
		stop_requested_at = clock_us;
		stop_requested_while_pending = atomic_load(&tx_pending) != 0;
		input("\n900 RFSTOP\n");
	}
	if (stops && clock_us - started_at > 1000000 && head == tail &&
	    !atomic_load(&tx_pending) && !shifting && !ec_rf_events_pending())
		longjmp(finished, 1);
}

/** @brief Report the same bounded transmit-power setting as the candidate.
 * @return Zero dBm.
 */
static int power_dbm(void)
{
	return 0;
}

const struct ec_rf_ops ec_mg24_rf_ops = {
	.now_us = now_us,
	.start = start,
	.stop = stop,
	.send = send_frame,
	.poll = poll,
	.power_dbm = power_dbm,
};

/** @brief Reset the host-only peripheral/core environment between scenarios.
 * @param kind Chosen independent fault or workload scenario.
 */
static void reset(enum scenario kind)
{
	memset(&uart, 0, sizeof(uart));
	uart.TXDATA = UINT32_MAX;
	clock_us = shift_until = started_at = stop_requested_at = 0;
	tx_enabled = shifting = in_irq = always_ready = arm_race = 0;
	wire_used = starts = stops = frame_count = 0;
	stop_requested = running = injected_loss = injected_expiry = 0;
	maximum_irq_bytes = maximum_input_batch = previous_tail = 0;
	irq_calls = shifted_byte = stop_while_pending = legacy_run = 0;
	stop_requested_while_pending = 0;
	fixture_irq_disabled = 0;
	fixture_flags = cleared_flags = 0;
	head = tail = lost = 0;
	tx_buffer = NULL;
	tx_sent = 0;
	atomic_store(&tx_pending, 0);
	wire[0] = 0;
	scenario = kind;
	frame_goal = kind == OVERLOAD	 ? 30
		     : kind == BURST	 ? 10
		     : kind == STOP_BUSY ? 1
					 : 3;
}

/** @brief Reproduce the previous one-byte-per-radio-poll output schedule. */
static void legacy_main(void)
{
	struct ec_experimental parser;
	char response[640];
	size_t pending = 0, sent = 0;

	ec_experimental_init(&parser, "seeed-xiao-mg24", "xiao-queue-1",
			     115200);
	ec_rf_setup(&ec_mg24_rf_ops);
	parser.radio_command = ec_rf_command;
	for (;;) {
		ec_rf_poll();
		if (sent < pending) {
			if (USART0->STATUS & USART_STATUS_TXBL)
				USART0->TXDATA = (uint8_t)response[sent++];
			continue;
		}
		pending = sent = 0;
		if (tail != head) {
			uint8_t byte = rx[tail];

			tail = (tail + 1) & (RX_SIZE - 1);
			pending = ec_experimental_byte(&parser, byte, response,
						       sizeof(response));
		}
		if (!pending)
			pending = ec_rf_event(response, sizeof(response));
	}
}

/** @brief Read one validated decimal counter from stopped diagnostic output.
 * @param command Read-only core query.
 * @param name Exact counter prefix ending in equals.
 * @return Counter value.
 */
static unsigned counter(const char *command, const char *name)
{
	char response[512];
	const char *value;

	assert(ec_rf_command(command, response, sizeof(response)));
	assert(!strncmp(response, "OK ", 3));
	value = strstr(response, name);
	assert(value);
	return (unsigned)strtoul(value + strlen(name), NULL, 10);
}

/** @brief Exercise either the real candidate main or frozen baseline policy.
 * @param legacy Nonzero selects the explicit previous scheduling reference.
 * @return Measured portable-queue overflow count.
 */
static unsigned run(unsigned legacy)
{
	legacy_run = legacy;
	input("1 RFLISTEN 1234abcd\n");
	if (scenario == LONG_INPUT) {
		for (unsigned i = 0; i < 700; i++)
			input("x");
		input("\n2 UNKNOWN\n3 PING\n");
	}
	if (scenario == FAIR)
		for (unsigned i = 2; i < 40; i++) {
			char command[32];

			snprintf(command, sizeof(command), "%u PING\n", i);
			input(command);
		}
	running = 1;
	if (!setjmp(finished)) {
		if (legacy)
			legacy_main();
		else
			mg24_application_main();
	}
	running = 0;
	assert(starts == 1 && stops >= 1);
	assert(counter("RFSTATUS", "active=") == 0);
	assert(counter("RFSTATUS", "queued=") == 0);
	assert(counter("RFQUEUE", "rx_enqueued=") ==
	       counter("RFQUEUE", "rx_dequeued="));
	return counter("RFQUEUE", "rx_queue_full=");
}

/** @brief Verify every burst record and reply reaches the wire without interleaving.
 * @param expected Number of sequential fixture frames accepted without overflow.
 */
static void verify_capture(unsigned expected)
{
	const char *cursor = wire;
	unsigned records = 0, replies = 0;

	while (*cursor) {
		const char *end = strchr(cursor, '\n');

		assert(end);
		if (!strncmp(cursor, "RF RX ", 6)) {
			static const char prefix[] =
				"RF RX len=10 fcs=included crc=1 rssi=-40 "
				"lqi=80 ticks=";
			char suffix[64], *ticks_end;

			assert(!strncmp(cursor, prefix, sizeof(prefix) - 1));
			assert(strtoul(cursor + sizeof(prefix) - 1, &ticks_end,
				       10));
			snprintf(suffix, sizeof(suffix),
				 " data=%02x0000000000000000000000", records);
			assert((size_t)(end - ticks_end) == strlen(suffix));
			assert(!memcmp(ticks_end, suffix, strlen(suffix)));
			records++;
		} else {
			const char *reply = replies ? "RSP 900 OK active=0"
						    : "RSP 1 OK active=1 "
						      "channel=11 mode=passive";

			assert((size_t)(end - cursor) == strlen(reply));
			assert(!memcmp(cursor, reply, strlen(reply)));
			replies++;
		}
		cursor = end + 1;
	}
	assert(records == expected && replies == 2);
	assert(counter("RFQUEUE", "rx_dequeued=") == records);
}

/** @brief Prove modeled scheduling loss is avoidable without changing capacity. */
static void test_burst_pressure(void)
{
	unsigned baseline, candidate;

	reset(BURST);
	baseline = run(1);
	assert(baseline > 0);
	reset(BURST);
	candidate = run(0);
	assert(candidate == 0);
	assert(counter("RFQUEUE", "rx_enqueued=") == 10);
	verify_capture(10);
	assert(counter("RFQUEUE", "queue_capacity=") == 7);
	assert(strstr(wire, "RSP 1 OK active=1 channel=11 mode=passive\n"));
	assert(strstr(wire, "RSP 900 OK active=0\n"));
	printf("Timed 115200-baud burst: baseline full=%u, IRQ full=%u\n",
	       baseline, candidate);
	reset(OVERLOAD);
	assert(run(0) > 0);
	puts("Unavoidable wire-rate overload remains explicitly counted");
}

/** @brief Keep long/error input bounded and alternate commands with RF records. */
static void test_input_fairness(void)
{
	const char *first_rx, *last_ping;

	reset(LONG_INPUT);
	assert(run(0) == 0);
	assert(maximum_input_batch <= INPUT_BUDGET);
	assert(strstr(wire, "RSP 2 ERR UNSUPPORTED experimental=1\n"));
	assert(strstr(wire, "RSP 3 OK EC-GATEWAY 3\n"));
	reset(FAIR);
	assert(run(0) == 0);
	first_rx = strstr(wire, "RF RX ");
	last_ping = strstr(wire, "RSP 39 OK EC-GATEWAY 3\n");
	assert(first_rx && last_ping && first_rx < last_ping);
	assert(strstr(wire, "RSP 2 OK EC-GATEWAY 3\n") < first_rx);
}

/** @brief Preserve line bytes after RX invalidation and stop while UART is busy. */
static void test_fault_progress(void)
{
	reset(RX_LOSS);
	assert(run(0) == 0 && injected_loss);
	verify_capture(3);
	assert(strstr(wire, "RSP 900 OK active=0\n"));
	reset(STOP_BUSY);
	assert(run(0) == 0 && stop_requested_while_pending);
	verify_capture(1);
	reset(EXPIRY);
	assert(run(0) == 0 && injected_expiry && stop_while_pending);
}

/** @brief Keep RX flag acknowledgement separate from an owned UART output record. */
static void test_rx_irq_preserves_tx_flags(void)
{
	static const char record[] = "unchanged pending output\n";
	uint32_t rx_flags = USART_IF_RXDATAV | USART_IF_RXOF | USART_IF_FERR |
			    USART_IF_PERR;

	reset(BURST);
	serial_output_start(record, sizeof(record) - 1);
	assert(atomic_load(&tx_pending) && tx_enabled && shifting);
	fixture_flags = rx_flags | USART_IF_TXBL;
	USART0_RX_IRQHandler();
	assert(cleared_flags == rx_flags);
	assert(fixture_flags == USART_IF_TXBL);
	assert(lost && atomic_load(&tx_pending) && tx_enabled);
	advance(BYTE_US * sizeof(record));
	assert(!strcmp(wire, record));
	assert(!tx_enabled && !atomic_load(&tx_pending));
}

/** @brief Bound IRQ work and preserve ownership across immediate/stale IRQ races. */
static void test_irq_ownership(void)
{
	char data[64];

	reset(BURST);
	memset(data, 'x', sizeof(data));
	always_ready = 1;
	serial_output_start(data, sizeof(data));
	assert(maximum_irq_bytes == TX_IRQ_BUDGET);
	assert(atomic_load(&tx_pending) == sizeof(data));
	while (atomic_load(&tx_pending))
		service_irq();
	assert(!tx_enabled && wire_used == sizeof(data));
	assert(!memcmp(data, wire, sizeof(data)));
	reset(BURST);
	always_ready = arm_race = 1;
	serial_output_start("abc\n", 4);
	assert(!atomic_load(&tx_pending) && !tx_enabled);
	assert(!strcmp(wire, "abc\n"));
	assert(irq_calls == 2);
	/* Empty stale IRQs may never keep the source armed. */
	tx_enabled = 1;
	service_irq();
	assert(!tx_enabled && wire_used == 4);
	/* An empty IRQ must not consume main-owned, not-yet-published metadata. */
	tx_buffer = NULL;
	tx_sent = SIZE_MAX;
	tx_enabled = 1;
	USART0_TX_IRQHandler();
	assert(!tx_enabled && !tx_buffer && tx_sent == SIZE_MAX);
	reset(BURST);
	memcpy(data, "abc", 3);
	serial_output_start(data, 3);
	advance(BYTE_US * 2);
	assert(!atomic_load(&tx_pending) && shifting && wire_used == 2);
	/* The copied final byte remains safe when main reuses its only buffer. */
	memcpy(data, "XYZ", 3);
	serial_output_start(data, 3);
	advance(BYTE_US * 4);
	assert(!strcmp(wire, "abcXYZ") && !shifting && !tx_enabled);
}

/** @brief Run actual-main UART scheduling, boundedness and ownership regressions.
 * @return Zero only when every independent fixture passes.
 */
int main(void)
{
	test_irq_ownership();
	test_rx_irq_preserves_tx_flags();
	test_burst_pressure();
	test_input_fairness();
	test_fault_progress();
	puts("MG24 bounded UART output scheduling: PASS");
	return 0;
}
