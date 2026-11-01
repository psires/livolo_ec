#ifndef EC_SCHEDULER_H
#define EC_SCHEDULER_H

#include <stdint.h>

#define EC_SCHED_SLOTS 8
#ifndef EC_SCHED_HZ
#define EC_SCHED_HZ 65536
#endif
#define EC_SCHED_PERIOD (10u * EC_SCHED_HZ)
#define EC_SCHED_HORIZON (30u * EC_SCHED_HZ)
#define EC_SCHED_LATE (EC_SCHED_HZ / 50u)
#define EC_SCHED_PENDING 1
#define EC_SCHED_DONE 2

typedef struct {
	uint32_t entry, returned, anchor, age, generation;
	int result;
	uint8_t raw[125], len;
} ec_sched_tx_t;

typedef struct {
	uint32_t id, due, grace;
	uint8_t state, phase;
	ec_sched_tx_t tx;
} ec_sched_job_t;

typedef struct {
	uint32_t id, started, deadline, due, pair_ticks, wait_ticks;
	uint32_t rx_ticks, rx_sequence, late_max;
	uint32_t grid_generation, grid_wait_ticks, grid_skips;
	uint8_t grid, grid_ready;
	uint8_t output, baseline[3];
	uint8_t state, outcome, mask, rounds, sent, attempts, retries, attempt;
	uint8_t opcode, waiting, raw[41], report[39], report_len;
	int rssi;
	unsigned lqi;
	ec_sched_tx_t tx;
} ec_query_t;

typedef struct {
	ec_query_t query;
	ec_sched_job_t jobs[EC_SCHED_SLOTS];
	uint32_t last_id, full, late, cancelled;
	uint32_t heartbeat_due, heartbeat_sent, heartbeat_missed;
	uint32_t heartbeat_retry_due, heartbeat_retries;
	uint8_t heartbeat_attempts;
	uint32_t heartbeat_late_max, heartbeat_last_due, heartbeat_last_entry;
	int heartbeat_result;
	uint8_t heartbeat_active, sequence, heartbeat[41];
} ec_scheduler_t;

extern ec_scheduler_t ec_scheduler;

/** @brief Read the MCU's wrapping EC_SCHED_HZ clock. @return Current ticks. */
uint32_t ec_scheduler_now(void);
/**
 * @brief Arm or stop the platform wake timer; no radio work runs in its ISR.
 * @param active Nonzero to arrange a process-context wake.
 * @param due Absolute EC_SCHED_HZ target, ignored when inactive.
 */
void ec_scheduler_alarm(int active, uint32_t due);
/**
 * @brief Execute one frame using the current network policy and lease.
 * @param tx Frame/result storage owned by the caller.
 * @param phase Nonzero to apply the current phase reference.
 */
void ec_scheduler_transmit(ec_sched_tx_t *tx, int phase);
/**
 * @brief Read the current successful heartbeat's live phase reference.
 * @param anchor Radio-entry timestamp in the scheduler clock.
 * @param generation Changes whenever a heartbeat establishes a new reference.
 * @return Nonzero when the reference is live; zero never authorizes a send.
 */
int ec_scheduler_phase_reference(uint32_t *anchor, uint32_t *generation);
/**
 * @brief Clear volatile jobs and heartbeat; retain the session job-ID high water.
 * Counters reset only at boot; cancellation never transmits.
 */
void ec_scheduler_clear(void);
/**
 * @brief Enqueue a policy-validated one-shot frame without transmitting.
 * @param id Increasing nonzero job identity within this session.
 * @param due Absolute target within the next 30 seconds.
 * @param grace Maximum process-dispatch lateness, 1 through EC_SCHED_HZ/10 ticks.
 * @param phase Nonzero for phase stamping.
 * @param raw Validated frame bytes, copied into RAM.
 * @param len Number of bytes, 1 through 125.
 * @return 0 accepted, -1 invalid, -2 full, -3 reused/out-of-order identity.
 */
int ec_scheduler_add(uint32_t id, uint32_t due, uint32_t grace, int phase,
		     const uint8_t *raw, unsigned len);
/**
 * @brief Find a retained pending or completed job.
 * @param id Job identity.
 * @return RAM slot or NULL if absent.
 */
ec_sched_job_t *ec_scheduler_find(uint32_t id);
/**
 * @brief Cancel a pending job, retaining its terminal result.
 * @param job Existing slot returned by find; NULL is permitted.
 */
void ec_scheduler_cancel(ec_sched_job_t *job);
/**
 * @brief Start a ten-second absolute heartbeat cadence from a validated frame.
 * @param due First absolute target within the next 30 seconds.
 * @param raw Validated 41-byte heartbeat template.
 * @return One accepted, zero if active or target invalid.
 */
int ec_scheduler_heartbeat(uint32_t due, const uint8_t *raw);
/**
 * @brief Install one bounded query transaction in a separate retained RAM slot.
 * @param id Increasing session job identity shared with one-shot jobs.
 * @param pair_ticks TX-entry interval between 10 and ac, 200 through 300 ms.
 * @param wait_ticks TX-entry response interval after ac, 4 through 4.1 seconds.
 * @param mask Host-authorized output bits, 1 through 15.
 * @param grid Nonzero for AC-only sends on the heartbeat-relative 200 ms grid.
 * @param raw Validated direct 41-byte 10 template, or ac to leave each 10 slot silent.
 * @return 0 accepted, -1 invalid, -2 occupied, -3 replayed identity.
 */
int ec_query_begin(uint32_t id, uint32_t pair_ticks, uint32_t wait_ticks,
		   unsigned mask, const uint8_t *raw, unsigned grid);
/**
 * @brief Install an output transaction with six attempts and a four-second limit.
 * Uses the query result slot; the Pi owns authoritative state and command tokens.
 * @param id Increasing nonzero session job identity shared with one-shot jobs.
 * @param mask Host-authorized full output mask, 1 through 15.
 * @param baseline Borrowed three bytes: state, report counter and token; all ff
 * if unknown. Copied into the transaction.
 * @param raw Borrowed validated 41-byte opcode-02 frame, copied into RAM.
 * @return 0 accepted, -1 invalid, -2 occupied, -3 reused/out-of-order identity.
 */
int ec_output_begin(uint32_t id, unsigned mask, const uint8_t *baseline,
		    const uint8_t *raw);
/**
 * @brief Stop on a valid temporally fresh native state report before UART logging.
 * @param raw Received MAC bytes, borrowed.
 * @param len Received length without FCS.
 * @param ticks MCU callback timestamp, not hardware SFD.
 * @param sequence RX event identity for retained report recovery.
 * @param rssi Received signal strength in dBm.
 * @param lqi Received link quality.
 */
void ec_query_receive(const uint8_t *raw, unsigned len, uint32_t ticks,
		      uint32_t sequence, int rssi, unsigned lqi);
/** @brief Cancel a pending transaction while retaining its outcome. */
void ec_query_cancel(void);
/** @brief Dispatch due work and arm the next wake; caller verifies its lease. */
void ec_scheduler_poll(void);

#endif
