#include "ec-uart-rx.h"
#include <string.h>

void ec_uart_rx_init(ec_uart_rx_t *rx, uint32_t now)
{
	memset(rx, 0, sizeof(*rx));
	rx->before_us = now;
}

int ec_uart_rx_observe(ec_uart_rx_t *rx, unsigned head, uint32_t before,
		       uint32_t after, unsigned baud, int fault)
{
	uint32_t elapsed = after - rx->before_us;
	/* Allow 5% baud/clock uncertainty and 16 bytes for USART/in-flight DMA.
	 * Use 64 bits: the ring's 8N1 bit-time exceeds a 32-bit microsecond product.
	 */
	uint32_t safe_us = (uint64_t)(EC_UART_RX_SIZE - 16u) * 9500000u / baud;
	unsigned delta = (head - rx->head) & (EC_UART_RX_SIZE - 1u);

	if (elapsed > rx->max_gap_us)
		rx->max_gap_us = elapsed;
	rx->before_us = before;
	rx->head = head;
	if (fault || elapsed >= safe_us ||
	    rx->pending + delta >= EC_UART_RX_SIZE) {
		rx->tail = head;
		rx->pending = 0;
		rx->resyncs++;
		return 0;
	}
	rx->pending += delta;
	if (rx->pending > rx->highwater)
		rx->highwater = rx->pending;
	return 1;
}

unsigned ec_uart_rx_copy(const ec_uart_rx_t *rx, const volatile uint8_t *ring,
			 uint8_t *copy)
{
	unsigned length =
		rx->pending < EC_UART_RX_CHUNK ? rx->pending : EC_UART_RX_CHUNK;
	for (unsigned i = 0; i < length; i++)
		copy[i] = ring[(rx->tail + i) & (EC_UART_RX_SIZE - 1u)];
	return length;
}

void ec_uart_rx_consume(ec_uart_rx_t *rx, unsigned length)
{
	rx->tail = (rx->tail + length) & (EC_UART_RX_SIZE - 1u);
	rx->pending -= length;
}
