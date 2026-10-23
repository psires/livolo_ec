#include "ec-phase.h"

int ec_phase_encode(uint32_t age_ticks, uint32_t hz, uint8_t trailer[3])
{
	uint32_t fraction;

	if (!hz || (uint64_t)age_ticks >= (uint64_t)hz * EC_PHASE_MAX_SECONDS)
		return 0;
	fraction =
		(uint32_t)(((uint64_t)(age_ticks % hz) * EC_PHASE_SCALE) / hz);
	trailer[0] = fraction & 255;
	trailer[1] = fraction >> 8;
	trailer[2] = age_ticks / hz;
	return 1;
}
