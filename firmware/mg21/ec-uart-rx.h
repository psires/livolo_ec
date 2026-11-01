#ifndef EC_UART_RX_H
#define EC_UART_RX_H

#include <stdint.h>

#define EC_UART_RX_SIZE 8192u
#define EC_UART_RX_CHUNK 256u

/* A polling consumer for a continuously running DMA ring. Time bounds make a
 * complete, otherwise invisible DMA lap fail closed, including while copying.
 * The caller brackets each DMA pointer read with a free-running microsecond
 * clock, and observes again after copying, before delivering any bytes.
 */
typedef struct {
	uint32_t before_us, max_gap_us, resyncs;
	unsigned head, tail, pending, highwater;
} ec_uart_rx_t;

/**
 * @brief Initialize before reception starts with the producer at offset zero.
 * @param rx Caller-owned cursor to clear; no concurrent access is permitted.
 * @param now Initial wrapping microsecond timestamp.
 */
void ec_uart_rx_init(ec_uart_rx_t *rx, uint32_t now);
/**
 * @brief Validate producer progress; loss discards pending and staged bytes.
 * On zero, feed a nonprinting byte to the parser to discard through newline.
 * @param rx Caller-owned cursor to update.
 * @param head Producer byte offset, less than EC_UART_RX_SIZE.
 * @param before Wrapping microsecond timestamp before reading the DMA pointer.
 * @param after Timestamp after that read, including any intervening preemption.
 * @param baud Nonzero configured UART bit rate in bits per second, using 8N1.
 * @param fault Nonzero for a sticky UART error or DMA restart.
 * @return One when progress is valid, zero after resynchronizing on loss.
 */
int ec_uart_rx_observe(ec_uart_rx_t *rx, unsigned head, uint32_t before,
		       uint32_t after, unsigned baud, int fault);
/**
 * @brief Stage a bounded chunk without consuming it; DMA may keep running.
 * Revalidate with observe before delivering bytes or committing with consume.
 * @param rx Borrowed cursor describing available bytes.
 * @param ring Borrowed live DMA buffer of EC_UART_RX_SIZE bytes.
 * @param copy Caller-owned destination of at least EC_UART_RX_CHUNK bytes.
 * @return Number of copied bytes, from zero through EC_UART_RX_CHUNK.
 */
unsigned ec_uart_rx_copy(const ec_uart_rx_t *rx, const volatile uint8_t *ring,
			 uint8_t *copy);
/**
 * @brief Commit a successfully revalidated copy to the consumer cursor.
 * @param rx Caller-owned cursor; must not have resynchronized since the copy.
 * @param length Validated byte count, at most rx->pending.
 */
void ec_uart_rx_consume(ec_uart_rx_t *rx, unsigned length);
#endif
