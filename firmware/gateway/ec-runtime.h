#ifndef EC_RUNTIME_H
#define EC_RUNTIME_H

#include <stdint.h>
#include <stddef.h>

/**
 * @brief Handle a received radio frame and report its reception over UART.
 *
 * @param raw Received frame bytes, without the radio FCS.
 * @param len Number of received bytes.
 * @param rssi Received signal strength in dBm.
 * @param lqi Radio-provided link quality indicator.
 */
void ec_receive(const uint8_t *raw, unsigned len, int rssi, unsigned lqi);

/**
 * @brief Report the remaining capacity of the asynchronous UART transmit queue.
 *
 * @return Number of bytes available for a new queued write.
 */
unsigned ec_uart_free(void);

/**
 * @brief Report bytes lost to UART queue exhaustion or transmission failure.
 *
 * @return Cumulative number of dropped transmit bytes.
 */
unsigned long ec_uart_dropped(void);

#endif
