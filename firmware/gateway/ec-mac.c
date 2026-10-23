#include "contiki.h"
#include "net/netstack.h"
#include "net/mac/mac.h"
#include "net/packetbuf.h"
#include "ec-runtime.h"

/**
 * @brief Enable raw reception and disable automatic radio acknowledgements.
 */
static void init(void)
{
	NETSTACK_RADIO.set_value(RADIO_PARAM_RX_MODE, 0);
	NETSTACK_RADIO.set_value(RADIO_PARAM_TX_MODE, 0);
	NETSTACK_RADIO.on();
}

/**
 * @brief Reject generic MAC transmission so frames use the gateway policy path.
 *
 * @param cb Completion callback to notify of the rejected send.
 * @param ptr Caller context passed to the completion callback.
 */
static void send(mac_callback_t cb, void *ptr)
{
	mac_call_sent_callback(cb, ptr, MAC_TX_ERR_FATAL, 0);
}

/**
 * @brief Deliver the current radio packet and link measurements to the gateway.
 */
static void input(void)
{
	ec_receive(packetbuf_dataptr(), packetbuf_datalen(),
		   (int)(int16_t)packetbuf_attr(PACKETBUF_ATTR_RSSI),
		   packetbuf_attr(PACKETBUF_ATTR_LINK_QUALITY));
}

/**
 * @brief Enable radio reception through the MAC interface.
 *
 * @return Status returned by the radio driver.
 */
static int on(void)
{
	return NETSTACK_RADIO.on();
}

/**
 * @brief Disable the radio through the MAC interface.
 *
 * @return Status returned by the radio driver.
 */
static int off(void)
{
	return NETSTACK_RADIO.off();
}

/**
 * @brief Report the largest raw frame accepted without the radio FCS.
 *
 * @return Maximum frame length in bytes.
 */
static int max_payload(void)
{
	return 125;
}

const struct mac_driver ec_gateway_mac_driver = {
	"EC guarded raw gateway", init, send, input, on, off, max_payload
};
