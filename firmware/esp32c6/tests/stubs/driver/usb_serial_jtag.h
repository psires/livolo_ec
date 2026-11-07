/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef TEST_USB_SERIAL_JTAG_H
#define TEST_USB_SERIAL_JTAG_H

#include <stddef.h>
#include <stdint.h>

#define ESP_OK 0

typedef struct {
	size_t tx_buffer_size;
	size_t rx_buffer_size;
} usb_serial_jtag_driver_config_t;

/** @brief Emulate driver setup without accessing USB.
 * @param config Requested bounded transport buffers.
 * @return Configured test result.
 */
int usb_serial_jtag_driver_install(
	const usb_serial_jtag_driver_config_t *config);

/** @brief Supply fragmented input and injected receive failures to the adapter.
 * @param buffer Destination for at most length received bytes.
 * @param length Available destination capacity.
 * @param ticks Requested finite receive timeout.
 * @return Received bytes, zero on timeout, or a negative error.
 */
int usb_serial_jtag_read_bytes(void *buffer, size_t length, uint32_t ticks);

/** @brief Capture partial writes and inject transient backpressure/errors.
 * @param buffer Borrowed pending response bytes.
 * @param length Number of bytes offered for transmission.
 * @param ticks Requested write timeout; must be nonblocking.
 * @return Accepted bytes, zero on backpressure, or a negative error.
 */
int usb_serial_jtag_write_bytes(const void *buffer, size_t length,
				uint32_t ticks);

#endif
