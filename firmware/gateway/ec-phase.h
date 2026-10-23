#ifndef EC_PHASE_H
#define EC_PHASE_H

#include <stdint.h>

#define EC_PHASE_SCALE 65500u
#define EC_PHASE_MAX_SECONDS 12u

/**
 * @brief Encode a recent phase-reference age for an EC timing trailer.
 *
 * @param age_ticks Elapsed ticks since the reference timestamp.
 * @param hz Number of ticks per second; must be nonzero.
 * @param trailer Three-byte destination, unchanged for an invalid age or rate.
 *
 * @return One on success, or zero when the rate or reference age is invalid.
 */
int ec_phase_encode(uint32_t age_ticks, uint32_t hz, uint8_t trailer[3]);

#endif
