/* The timer ISR only wakes Contiki. RF and UART operations stay in process
 * context; this first stage reports dispatch timing, not scheduled RF SFD. */
#include "contiki.h"
#include "ec-scheduler.h"
#include <ti/drivers/dpl/ClockP.h>

PROCESS_NAME(gateway_process);
static ClockP_Struct alarm;
static unsigned initialized;

/**
 * @brief Wake the gateway process at a timer deadline without doing radio work.
 * @param unused Unused ClockP callback argument.
 */
static void wake(uintptr_t unused)
{
	(void)unused;
	process_poll(&gateway_process);
}

uint32_t ec_scheduler_now(void)
{
	return RTIMER_NOW();
}

void ec_scheduler_alarm(int active, uint32_t due)
{
	ClockP_Params params;
	uint32_t now, ticks;
	int32_t delta;
	uint64_t us;

	if (!initialized) {
		ClockP_Params_init(&params);
		params.startFlag = false;
		params.period = 0;
		ClockP_construct(&alarm, wake, 1, &params);
		initialized = 1;
	}
	ClockP_stop(&alarm);
	if (!active)
		return;
	now = RTIMER_NOW();
	delta = (int32_t)(due - now);
	us = delta > 0 ? ((uint64_t)delta * 1000000 + 65535) / 65536 : 1;
	ticks = (us + ClockP_getSystemTickPeriod() - 1) /
		ClockP_getSystemTickPeriod();
	ClockP_setTimeout(&alarm, ticks ? ticks : 1);
	ClockP_start(&alarm);
}
