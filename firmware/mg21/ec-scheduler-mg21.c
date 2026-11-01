/* The MG21 main loop never sleeps: radio RX and gateway work are polled
 * continuously, including between UART commands. No timer ISR does RF work. */
#include "ec-mg21-platform.h"
#include "ec-scheduler.h"

uint32_t ec_scheduler_now(void)
{
	return RTIMER_NOW();
}

void ec_scheduler_alarm(int active, uint32_t due)
{
	/* No wake source is needed while main() is continuously runnable. If an
	 * idle/sleep path is introduced, it must arm a RAIL timer here first. */
	(void)active;
	(void)due;
}
