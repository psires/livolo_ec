/* Passive raw-MAC adapter: no frame parsing, acknowledgements, or transmit path. */
#include "contiki.h"
#include "net/netstack.h"
#include "net/mac/mac.h"
#include "net/packetbuf.h"
#include "sys/clock.h"
#include "dev/uart0-arch.h"
#include <stdio.h>

static unsigned long received;

/**
 * @brief Enable passive reception without filtering or automatic acknowledgements.
 */
static void init(void)
{
	/* Filtering and auto-ACK are disabled at driver initialization too. */
	NETSTACK_RADIO.set_value(RADIO_PARAM_RX_MODE, 0);
	NETSTACK_RADIO.on();
}

/**
 * @brief Reject transmissions requested through the receive-only MAC driver.
 *
 * @param callback Completion callback to notify of the rejected transmission.
 * @param ptr Opaque caller context passed to the completion callback.
 */
static void send(mac_callback_t callback, void *ptr)
{
	mac_call_sent_callback(callback, ptr, MAC_TX_ERR_FATAL, 0);
}

/**
 * @brief Publish one received radio frame and its metadata over UART.
 */
static void input(void)
{
	const unsigned char *data = packetbuf_dataptr();
	unsigned len = packetbuf_datalen();
	char line[340];
	static const char hex[] = "0123456789abcdef";
	int n = snprintf(line, sizeof(line), "RX %lu %lu %d %u %u ", ++received,
			 (unsigned long)clock_time(),
			 (int)(int16_t)packetbuf_attr(PACKETBUF_ATTR_RSSI),
			 (unsigned)packetbuf_attr(PACKETBUF_ATTR_LINK_QUALITY),
			 len);

	if (len > 127 || n < 0 || (unsigned)n + 2 * len + 1 > sizeof(line))
		return;
	for (unsigned i = 0; i < len; ++i) {
		line[n++] = hex[data[i] >> 4];
		line[n++] = hex[data[i] & 15];
	}
	line[n++] = '\n';
	uart0_write(line, n);
}

/**
 * @brief Enable the receiver radio.
 *
 * @return Status returned by the radio driver.
 */
static int on(void)
{
	return NETSTACK_RADIO.on();
}

/**
 * @brief Disable the receiver radio.
 *
 * @return Status returned by the radio driver.
 */
static int off(void)
{
	return NETSTACK_RADIO.off();
}

/**
 * @brief Report the frame payload capacity supported by this MAC adapter.
 *
 * @return Maximum payload size in bytes.
 */
static int max_payload(void)
{
	return 125;
}
const struct mac_driver ec_sniffer_mac_driver = {
	"EC receive only", init, send, input, on, off, max_payload
};
