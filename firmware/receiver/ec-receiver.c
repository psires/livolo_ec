#include "contiki.h"
#include "net/netstack.h"
#include <stdio.h>
PROCESS(receiver_process, "EC passive receiver");
AUTOSTART_PROCESSES(&receiver_process);

/**
 * @brief Report passive receiver health at regular heartbeat intervals.
 * @param ev Contiki event delivered to the receiver process.
 * @param data Optional event payload supplied by Contiki.
 *
 * @return Contiki protothread scheduling state for the next process event.
 */
PROCESS_THREAD(receiver_process, ev, data)
{
	static struct etimer heartbeat;
	radio_value_t mode, channel;

	PROCESS_BEGIN();
	printf("EC-RECEIVER 1 board=codm-cc2652p baud=115200 ticks=%lu "
	       "tx=disabled\n",
	       (unsigned long)CLOCK_SECOND);
	etimer_set(&heartbeat, 5 * CLOCK_SECOND);
	while (1) {
		PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&heartbeat));
		NETSTACK_RADIO.get_value(RADIO_PARAM_RX_MODE, &mode);
		NETSTACK_RADIO.get_value(RADIO_PARAM_CHANNEL, &channel);
		printf("STATUS channel=%d rx_mode=%d ticks=%lu\n", channel,
		       mode, (unsigned long)clock_time());
		etimer_reset(&heartbeat);
	}
	PROCESS_END();
}
