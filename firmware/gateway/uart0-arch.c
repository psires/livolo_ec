/*
 * Project-local implementation of Contiki's UART0 API. All writes are queued.
 * The TI callback owns each transmitted ring segment until completion. No UART
 * logging call waits for bytes to leave the peripheral.
 */
#include "contiki.h"
#include "dev/uart0-arch.h"
#include "ec-runtime.h"
#include <Board.h>
#include <ti/drivers/UART.h>
#include <ti/drivers/dpl/HwiP.h>
#include <string.h>

#define CAPACITY 4096u
static UART_Handle handle;
static volatile uart0_input_fxn_t input;
static unsigned char rx_byte;
static uint8_t ring[CAPACITY];
static volatile unsigned head, tail, active;
static volatile unsigned long dropped;

unsigned ec_uart_free(void)
{
	return (tail + CAPACITY - head - 1) % CAPACITY;
}

unsigned long ec_uart_dropped(void)
{
	return dropped;
}

/**
 * @brief Start transmission of queued UART bytes when the peripheral is idle.
 */
static void kick(void);

/**
 * @brief Release a completed UART segment and continue queued transmission.
 *
 * @param h UART handle supplied by the driver; unused.
 * @param buf Completed transmit buffer supplied by the driver; unused.
 * @param count Number of bytes the driver reports as transmitted.
 */
static void written(UART_Handle h, void *buf, size_t count)
{
	uintptr_t key;

	(void)h;
	(void)buf;
	key = HwiP_disable();
	if (count != active)
		dropped += active > count ? active - count : 0;
	tail = (tail + active) % CAPACITY;
	active = 0;
	HwiP_restore(key);
	kick();
}

static void kick(void)
{
	uintptr_t key = HwiP_disable();
	unsigned n, start;

	if (active || tail == head || !handle) {
		HwiP_restore(key);
		return;
	}
	n = head > tail ? head - tail : CAPACITY - tail;
	if (n > 128)
		n = 128;
	start = tail;
	active = n;
	HwiP_restore(key);
	if (UART_write(handle, ring + start, n) < 0) {
		key = HwiP_disable();
		dropped += n;
		tail = (tail + n) % CAPACITY;
		active = 0;
		HwiP_restore(key);
	}
}

/**
 * @brief Deliver a received byte and continue reading while the callback
 *   remains set.
 *
 * @param h UART handle supplied by the driver; unused.
 * @param buf Receive buffer supplied by the driver; unused.
 * @param count Number of bytes received.
 */
static void received(UART_Handle h, void *buf, size_t count)
{
	uart0_input_fxn_t cb;

	(void)h;
	(void)buf;
	cb = input;
	if (cb && count)
		cb(rx_byte);
	if (cb && input == cb)
		UART_read(handle, &rx_byte, 1);
}

/**
 * @brief Open the board UART for asynchronous binary input and output.
 */
void uart0_init(void)
{
	UART_Params p;

	if (handle)
		return;
	UART_Params_init(&p);
	p.baudRate = TI_UART_CONF_BAUD_RATE;
	p.readMode = UART_MODE_CALLBACK;
	p.writeMode = UART_MODE_CALLBACK;
	p.readCallback = received;
	p.writeCallback = written;
	p.readDataMode = UART_DATA_BINARY;
	p.writeDataMode = UART_DATA_BINARY;
	p.readReturnMode = UART_RETURN_FULL;
	p.readEcho = UART_ECHO_OFF;
	handle = UART_open(Board_UART0, &p);
}

/**
 * @brief Queue a complete UART write without waiting for peripheral
 *   transmission.
 *
 * @param data Bytes to copy into the transmit queue.
 * @param n Number of bytes to queue.
 *
 * @return Queued byte count, or UART_STATUS_ERROR if unopened or lacking
 *   capacity.
 */
int_fast32_t uart0_write(const void *data, size_t n)
{
	const uint8_t *p = data;
	uintptr_t key;
	size_t i;

	if (!handle)
		return UART_STATUS_ERROR;
	key = HwiP_disable();
	if (n > ec_uart_free()) {
		dropped += n;
		HwiP_restore(key);
		return UART_STATUS_ERROR;
	}
	for (i = 0; i < n; i++) {
		ring[head] = p[i];
		head = (head + 1) % CAPACITY;
	}
	HwiP_restore(key);
	kick();
	return (int_fast32_t)n;
}

/**
 * @brief Queue one byte for asynchronous UART transmission.
 *
 * @param b Byte to transmit.
 *
 * @return One on success, or UART_STATUS_ERROR if the byte cannot be queued.
 */
int_fast32_t uart0_write_byte(uint8_t b)
{
	return uart0_write(&b, 1);
}

/**
 * @brief Replace the UART receive callback and enable or cancel byte reception.
 *
 * @param cb Callback for received bytes, or NULL to disable reception.
 *
 * @return Driver read status, or UART_STATUS_ERROR if the UART is unopened.
 */
int_fast32_t uart0_set_callback(uart0_input_fxn_t cb)
{
	if (!handle)
		return UART_STATUS_ERROR;
	input = NULL;
	UART_readCancel(handle);
	input = cb;
	return cb ? UART_read(handle, &rx_byte, 1) : UART_STATUS_SUCCESS;
}
