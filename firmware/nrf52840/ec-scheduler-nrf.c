/* The Nordic main loop never sleeps: radio RX and gateway work are polled
 * continuously, including between UART commands. No timer ISR does RF work. */
#include "ec-nrf-platform.h"
#include "ec-scheduler.h"

/** @brief Read the scheduler's free-running microsecond clock.
 * @return Microsecond count modulo 2^32.
 */
uint32_t ec_scheduler_now(void)
{
	return RTIMER_NOW();
}

/** @brief No wakeup needed in the continuous main loop.
 * @param active Whether the scheduler has a pending deadline.
 * @param due Deadline in microseconds modulo 2^32.
 */
void ec_scheduler_alarm(int active, uint32_t due)
{
	/* No wake source is needed while main() is continuously runnable. If an
	 * idle/sleep path is introduced, it must arm a hardware timer here first. */
	(void)active;
	(void)due;
}
