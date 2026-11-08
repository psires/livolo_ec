/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef EC_MG24_TRACE_H
#define EC_MG24_TRACE_H
#include <stddef.h>
#include <stdint.h>

#define EC_MG24_TRACE_EVENTS 4096u
#define EC_MG24_TRACE_ANCHORS 512u
#define EC_MG24_TRACE_MARKS 128u

enum ec_mg24_trace_mark {
	EC_TRACE_START = 1,
	EC_TRACE_START_RESULT,
	EC_TRACE_STOP,
	EC_TRACE_STOP_RESULT,
	EC_TRACE_CALIBRATE,
	EC_TRACE_CALIBRATE_RESULT,
};

struct ec_mg24_trace_metadata {
	uint32_t poll_ticks, packet_ticks, time_position, time_valid;
	uint32_t packet_status, air_bytes;
	uint32_t end_ticks, end_valid, end_status;
};

/** @brief Begin a new trace epoch while the previous radio epoch is quiescent.
 * Counters saturate; epoch wraps at 32 bits and must be paired with host session.
 */
void ec_mg24_trace_reset(void);
/** @brief Freeze callback and marker records after synchronous radio stop.
 * Main may still append anchors while draining packets held before the stop.
 */
void ec_mg24_trace_freeze(void);
/** @brief Admit one callback writer without waiting or masking interrupts.
 * @return Nonzero for the owner; one failed reservation records reentry and
 * refuses tracing, including conservative weak-CAS spurious failures.
 * This flag invalidates completeness claims for the entire epoch.
 */
int ec_mg24_trace_callback_enter(void);
/** @brief Retain one merged callback mask without parsing or packet operations.
 * @param ticks Callback-entry RAIL time in microseconds modulo 2^32.
 * @param events Full SDK event mask; one record is not necessarily one packet.
 * Only the admitted callback writer may call this; readers require frozen idle.
 */
void ec_mg24_trace_event(uint32_t ticks, uint64_t events);
/** @brief Retain measured callback-body elapsed time and release writer admission.
 * @param elapsed Unsigned modulo-32 RAIL microseconds from entry through the
 * original handler, excluding this final bookkeeping. Not isolated overhead
 * or a worst-case execution bound; interrupt preemption may inflate it.
 * Only a successfully admitted callback owner may call this, exactly once
 * before returning, even if recording froze after its admission.
 */
void ec_mg24_trace_callback_cost(uint32_t elapsed);
/** @brief Record a main-loop lifecycle/calibration boundary in separate storage.
 * @param kind Named boundary; result semantics follow the corresponding SDK call.
 * @param ticks Main-loop RAIL clock, modulo 2^32 microseconds.
 * @param result SDK return/status encoded as uint32, or zero before the call.
 */
void ec_mg24_trace_mark(enum ec_mg24_trace_mark kind, uint32_t ticks,
			uint32_t result);
/** @brief Retain a bounded full-MAC anchor before portable queue admission.
 * @param metadata Initialized timing/status; air_bytes is derived PHR+MAC+FCS.
 * @param data Complete MAC including original FCS, excluding PHR.
 * @param length Bytes available, 3..127; invalid records are counted and omitted.
 * Main is sole writer. Invalid SDK timestamp is explicit, never inferred from zero.
 * Anchors remain writable after freeze during held-packet drain. Readout requires
 * completed drain and stable snapshots; freeze alone does not make them immutable.
 */
void ec_mg24_trace_anchor(const struct ec_mg24_trace_metadata *metadata,
			  const uint8_t *data, size_t length);
/** @brief Fence TX and expose immutable trace records only after stopped drain.
 * @param command Complete command after the request ID.
 * @param out Response body storage.
 * @param capacity Available bytes including terminating NUL.
 * @return Complete response length, or zero for insufficient storage.
 * RFTRACE returns metadata; RFTRACE EVENT/ANCHOR/MARK index reads one record.
 */
size_t ec_mg24_trace_command(const char *command, char *out, size_t capacity);
#endif
