#include "ec-scheduler.h"
#include "ec-protocol.h"
#include <stddef.h>
#include <string.h>

ec_scheduler_t ec_scheduler;

/**
 * @brief Compare wrapping timestamps within the supported half-range horizon.
 * @param a First timestamp.
 * @param b Second timestamp.
 * @return Nonzero when a is at or after b.
 */
static int reached(uint32_t a, uint32_t b)
{
	return (int32_t)(a - b) >= 0;
}

/**
 * @brief Reject past targets and targets outside the bounded horizon.
 * @param due Absolute target tick.
 * @return Nonzero for an admissible future timestamp.
 */
static int future(uint32_t due)
{
	uint32_t delta = due - ec_scheduler_now();

	return delta > 0 && delta <= EC_SCHED_HORIZON;
}

void ec_scheduler_clear(void)
{
	unsigned i;

	for (i = 0; i < EC_SCHED_SLOTS; i++) {
		if (ec_scheduler.jobs[i].state == EC_SCHED_PENDING)
			ec_scheduler.cancelled++;
		memset(&ec_scheduler.jobs[i], 0, sizeof(ec_scheduler.jobs[i]));
	}
	memset(&ec_scheduler.query, 0, sizeof(ec_scheduler.query));
	ec_scheduler.heartbeat_active = 0;
	ec_scheduler.heartbeat_attempts = 0;
	ec_scheduler_alarm(0, 0);
}

ec_sched_job_t *ec_scheduler_find(uint32_t id)
{
	unsigned i;

	for (i = 0; i < EC_SCHED_SLOTS; i++)
		if (ec_scheduler.jobs[i].state && ec_scheduler.jobs[i].id == id)
			return &ec_scheduler.jobs[i];
	return NULL;
}

int ec_scheduler_add(uint32_t id, uint32_t due, uint32_t grace, int phase,
		     const uint8_t *raw, unsigned len)
{
	unsigned i;
	ec_sched_job_t *job;

	if (!id || !future(due) || !grace || grace > EC_SCHED_HZ / 10 || !len ||
	    len > 125 || (phase != 0 && phase != 1))
		return -1;
	if (id <= ec_scheduler.last_id)
		return -3;
	for (i = 0; i < EC_SCHED_SLOTS; i++) {
		job = &ec_scheduler.jobs[i];
		if (job->state)
			continue;
		memset(job, 0, sizeof(*job));
		job->id = id;
		job->due = due;
		job->grace = grace;
		job->phase = phase;
		job->tx.len = len;
		memcpy(job->tx.raw, raw, len);
		job->state = EC_SCHED_PENDING;
		ec_scheduler.last_id = id;
		return 0;
	}
	ec_scheduler.full++;
	return -2;
}

void ec_scheduler_cancel(ec_sched_job_t *job)
{
	if (job && job->state == EC_SCHED_PENDING) {
		job->state = EC_SCHED_DONE;
		job->tx.result = -4;
		ec_scheduler.cancelled++;
	}
}

int ec_scheduler_heartbeat(uint32_t due, const uint8_t *raw)
{
	if (ec_scheduler.heartbeat_active || !future(due))
		return 0;
	memcpy(ec_scheduler.heartbeat, raw, 41);
	ec_scheduler.sequence = raw[2] - 1;
	ec_scheduler.heartbeat_due = due;
	ec_scheduler.heartbeat_active = 1;
	ec_scheduler.heartbeat_attempts = 0;
	return 1;
}

/**
 * @brief Advance a heartbeat template's seconds counter by whole skipped slots.
 * @param slots Number of ten-second cadence slots consumed.
 */
static void advance_heartbeat(uint32_t slots)
{
	uint8_t *p = ec_scheduler.heartbeat;
	uint32_t seconds = (uint32_t)p[29] | ((uint32_t)p[30] << 8) |
			   ((uint32_t)p[31] << 16) | ((uint32_t)p[32] << 24);
	unsigned i, sum = 0;

	seconds += slots * 10;
	for (i = 0; i < 4; i++)
		p[29 + i] = seconds >> (8 * i);
	for (i = 21; i < 37; i++)
		sum += p[i];
	p[37] = sum;
	ec_scheduler.heartbeat_due += slots * EC_SCHED_PERIOD;
}

int ec_query_begin(uint32_t id, uint32_t pair_ticks, uint32_t wait_ticks,
		   unsigned mask, const uint8_t *raw, unsigned grid)
{
	ec_query_t *q = &ec_scheduler.query;

	if (grid > 1 || (grid && raw[22] != 0xac) || !id ||
	    pair_ticks < EC_SCHED_HZ / 5 || pair_ticks > EC_SCHED_HZ * 3 / 10 ||
	    wait_ticks < EC_SCHED_HZ * 4 ||
	    wait_ticks > EC_SCHED_HZ * 41 / 10 || !mask || mask > 15)
		return -1;
	if (id <= ec_scheduler.last_id)
		return -3;
	if (q->state)
		return -2;
	memset(q, 0, sizeof(*q));
	q->id = id;
	q->started = ec_scheduler_now();
	q->deadline = q->started + 14 * EC_SCHED_HZ;
	q->due = q->started;
	q->pair_ticks = pair_ticks;
	q->wait_ticks = wait_ticks;
	q->mask = mask;
	q->grid = grid;
	q->opcode = 0x10;
	memcpy(q->raw, raw, 41);
	ec_scheduler.last_id = id;
	q->state = EC_SCHED_PENDING;
	return 0;
}

void ec_query_cancel(void)
{
	ec_query_t *q = &ec_scheduler.query;

	if (q->state == EC_SCHED_PENDING) {
		q->outcome = 3;
		q->state = EC_SCHED_DONE;
	}
}

int ec_output_begin(uint32_t id, unsigned mask, const uint8_t *baseline,
		    const uint8_t *raw)
{
	ec_query_t *q = &ec_scheduler.query;
	if (!id || !mask || mask > 15 || raw[22] != 2 || raw[29] != 1 ||
	    raw[30] || !raw[31] || (raw[31] & ~mask) || (raw[32] & ~raw[31]) ||
	    raw[33] || raw[34] || raw[35] || !raw[36])
		return -1;
	if (id <= ec_scheduler.last_id)
		return -3;
	if (q->state)
		return -2;
	memset(q, 0, sizeof(*q));
	q->id = id;
	q->started = ec_scheduler_now();
	q->deadline = q->started + 4 * EC_SCHED_HZ;
	q->due = q->started;
	q->mask = mask;
	q->grid = q->output = 1;
	memcpy(q->baseline, baseline, 3);
	memcpy(q->raw, raw, 41);
	ec_scheduler.last_id = id;
	q->state = EC_SCHED_PENDING;
	return 0;
}

void ec_query_receive(const uint8_t *raw, unsigned len, uint32_t ticks,
		      uint32_t sequence, int rssi, unsigned lqi)
{
	ec_query_t *q = &ec_scheduler.query;
	uint16_t crc;

	if (q->state != EC_SCHED_PENDING || !reached(ticks, q->started) ||
	    reached(ticks, q->deadline) || len != 39 || raw[0] != 1 ||
	    raw[1] != 0x88 || raw[21] != 0x9e || raw[22] != 5 ||
	    raw[19] != q->raw[19] || memcmp(raw + 7, q->raw + 3, 4) ||
	    memcmp(raw + 23, q->raw + 23, 6) ||
	    (memcmp(raw + 3, q->raw + 7, 4) &&
	     memcmp(raw + 3, "\xff\xff\xff\xff", 4)) ||
	    raw[29] != 1 || raw[30] != 0 || (raw[31] & ~q->mask) ||
	    (raw[32] & ~q->mask))
		return;
	crc = ec_crc16(raw + 21, 16);
	if (raw[37] != (crc >> 8) || raw[38] != (crc & 255))
		return;
	if (q->output &&
	    (!q->attempts || !reached(ticks, q->tx.entry) ||
	     raw[31] != q->raw[31] || (raw[32] & raw[31]) != q->raw[32] ||
	     raw[36] != q->raw[36] ||
	     (raw[32] == q->baseline[0] && raw[35] == q->baseline[1] &&
	      raw[36] == q->baseline[2])))
		return;
	memcpy(q->report, raw, len);
	q->report_len = len;
	q->rx_ticks = ticks;
	q->rx_sequence = sequence;
	q->rssi = rssi;
	q->lqi = lqi;
	q->outcome = 1;
	q->state = EC_SCHED_DONE;
}

/**
 * @brief Defer eligible attempts to a live heartbeat-relative 200 ms slot.
 * @param q Borrowed pending transaction; updated or failed if phase is absent.
 * @param now Wrapping EC_SCHED_HZ timestamp used to select an eligible slot.
 * @return Nonzero when due now, zero when deferred or failed.
 */
static int query_grid_ready(ec_query_t *q, uint32_t now)
{
	uint32_t anchor, generation, target;
	uint64_t slot;

	if (!ec_scheduler_phase_reference(&anchor, &generation)) {
		q->outcome = 4;
		q->state = EC_SCHED_DONE;
		return 0;
	}
	/* A missed slot or new heartbeat requires a new target. Never round the
	 * phase bytes: radio_send_impl still stamps the actual radio-call entry. */
	if (q->grid_ready && now - q->due > EC_SCHED_HZ / 1000) {
		q->grid_skips++;
		q->grid_ready = 0;
	}
	if (!q->grid_ready || q->grid_generation != generation) {
		slot = ((uint64_t)(uint32_t)(now - anchor) * 5 + EC_SCHED_HZ -
			1) /
		       EC_SCHED_HZ;
		target = anchor + (uint32_t)((slot * EC_SCHED_HZ + 4) / 5);
		q->grid_wait_ticks += target - now;
		q->due = target;
		q->grid_generation = generation;
		q->grid_ready = 1;
	}
	return reached(now, q->due);
}

/**
 * @brief Send at most once per grid slot; busy attempts consume the budget.
 * @param q Borrowed pending output transaction to advance.
 * @param now Wrapping EC_SCHED_HZ timestamp for this dispatch.
 */
static void output_poll(ec_query_t *q, uint32_t now)
{
	if (q->attempts >= 6) {
		q->due = q->deadline;
		return;
	}
	if (!query_grid_ready(q, now))
		return;
	memset(&q->tx, 0, sizeof(q->tx));
	q->tx.len = 41;
	memcpy(q->tx.raw, q->raw, 41);
	q->attempts++;
	ec_scheduler_transmit(&q->tx, 1);
	if (!q->id)
		return;
	if (q->tx.result == 0)
		q->sent++;
	if (q->state != EC_SCHED_PENDING)
		return;
	if (q->tx.result != 0 && q->tx.result != 2) {
		q->outcome = 4;
		q->state = EC_SCHED_DONE;
		return;
	}
	q->grid_ready = 0;
	if (q->attempts == 6) {
		q->due = q->deadline;
		return;
	}
	/* Select the next absolute edge, even if a definite non-send consumed
	 * no clock ticks. Never retransmit immediately in the same slot. */
	query_grid_ready(q, ec_scheduler_now() + 1);
}

/** @brief Advance one query attempt, checking freshness/deadline before every send. */
static void query_poll(void)
{
	ec_query_t *q = &ec_scheduler.query;
	uint32_t now = ec_scheduler_now(), base;
	unsigned i, sum = 0;

	if (q->state != EC_SCHED_PENDING)
		return;
	if (reached(now, q->deadline)) {
		q->outcome = 2;
		q->state = EC_SCHED_DONE;
		return;
	}
	if (!reached(now, q->due))
		return;
	if (q->output) {
		output_poll(q, now);
		return;
	}
	if (q->waiting) {
		if (q->rounds == 3) {
			q->outcome = 2;
			q->state = EC_SCHED_DONE;
			return;
		}
		q->waiting = 0;
		q->opcode = 0x10;
	}
	if (now - q->due > q->late_max)
		q->late_max = now - q->due;
	if (!q->attempt && q->opcode == 0x10) {
		q->rounds++;
		/* AC-only retains the unused preparation slot. No fake TX or CCA
		 * attempt is counted; the AC and response-wait budgets stay intact. */
		if (q->raw[22] == 0xac) {
			q->opcode = 0xac;
			q->due = now + q->pair_ticks;
			return;
		}
	}
	if (q->grid && !query_grid_ready(q, now))
		return;
	memset(&q->tx, 0, sizeof(q->tx));
	q->tx.len = 41;
	memcpy(q->tx.raw, q->raw, 41);
	q->tx.raw[22] = q->opcode;
	for (i = 21; i < 37; i++)
		sum += q->tx.raw[i];
	q->tx.raw[37] = sum;
	ec_scheduler_transmit(&q->tx, 1);
	/* A lease expiry during the radio guard can clear the entire transaction. */
	if (!q->id)
		return;
	q->grid_ready = 0;
	q->attempts++;
	q->attempt++;
	if (q->tx.result == 0)
		q->sent++;
	if (q->state != EC_SCHED_PENDING)
		return;
	now = ec_scheduler_now();
	if (q->tx.result == 2 && q->attempt < 4) {
		q->retries++;
		q->due = now + (q->attempt * EC_SCHED_HZ + 39) / 40;
		return;
	}
	if (q->tx.result != 0 && q->tx.result != 2 && q->tx.result != -2) {
		q->outcome = 4;
		q->state = EC_SCHED_DONE;
		return;
	}
	/* Preserve the previous measured wire interval, without UART round trips.
	 * A definite non-send consumes the same logical operation, not a new round. */
	base = q->tx.result == 0 ? q->tx.entry : now;
	q->attempt = 0;
	if (q->opcode == 0x10) {
		q->opcode = 0xac;
		q->due = base + q->pair_ticks;
	} else {
		q->waiting = 1;
		q->due = base + q->wait_ticks;
	}
}

void ec_scheduler_poll(void)
{
	uint32_t now = ec_scheduler_now(), due = 0, delta, slots, wake;
	unsigned i;
	int active = 0;
	ec_sched_job_t *job, *earliest;
	ec_sched_tx_t tx;

	wake = ec_scheduler.heartbeat_attempts
		       ? ec_scheduler.heartbeat_retry_due
		       : ec_scheduler.heartbeat_due;
	if (ec_scheduler.heartbeat_active && reached(now, wake)) {
		delta = now - wake;
		slots = (now - ec_scheduler.heartbeat_due) / EC_SCHED_PERIOD +
			1;
		ec_scheduler.heartbeat_last_due = wake;
		if (delta > ec_scheduler.heartbeat_late_max)
			ec_scheduler.heartbeat_late_max = delta;
		if (delta <= EC_SCHED_LATE) {
			memset(&tx, 0, sizeof(tx));
			tx.len = 41;
			memcpy(tx.raw, ec_scheduler.heartbeat, 41);
			ec_scheduler_transmit(&tx, 0);
			ec_scheduler.heartbeat_last_entry = tx.entry;
			ec_scheduler.heartbeat_result = tx.result;
			ec_scheduler.heartbeat_attempts++;
			/* Match the daemon's four CCA attempts and 25/50/75 ms
			 * backoff, without shifting the periodic epoch. */
			if (tx.result == 2 &&
			    ec_scheduler.heartbeat_attempts < 4) {
				ec_scheduler.heartbeat_retries++;
				ec_scheduler.heartbeat_retry_due =
					ec_scheduler_now() +
					(ec_scheduler.heartbeat_attempts *
						 EC_SCHED_HZ +
					 39) / 40;
			} else {
				if (tx.result == 0)
					ec_scheduler.heartbeat_sent++;
				else
					ec_scheduler.heartbeat_missed++;
				ec_scheduler.heartbeat_attempts = 0;
				advance_heartbeat(slots);
			}
		} else {
			ec_scheduler.heartbeat_missed += slots;
			ec_scheduler.heartbeat_result = -3;
			ec_scheduler.heartbeat_attempts = 0;
			advance_heartbeat(slots);
		}
	}
	query_poll();
	/* Heartbeats have priority; queued jobs use earliest target order. */
	for (;;) {
		earliest = NULL;
		for (i = 0; i < EC_SCHED_SLOTS; i++) {
			job = &ec_scheduler.jobs[i];
			if (job->state == EC_SCHED_PENDING &&
			    (!earliest || reached(earliest->due, job->due)))
				earliest = job;
		}
		now = ec_scheduler_now();
		if (!earliest || !reached(now, earliest->due))
			break;
		if (now - earliest->due > earliest->grace) {
			earliest->tx.result = -3;
			ec_scheduler.late++;
		} else {
			ec_scheduler_transmit(&earliest->tx, earliest->phase);
		}
		if (earliest->id)
			earliest->state = EC_SCHED_DONE;
	}
	if (ec_scheduler.heartbeat_active) {
		active = 1;
		due = ec_scheduler.heartbeat_attempts
			      ? ec_scheduler.heartbeat_retry_due
			      : ec_scheduler.heartbeat_due;
	}
	if (earliest && (!active || reached(due, earliest->due))) {
		active = 1;
		due = earliest->due;
	}
	if (ec_scheduler.query.state == EC_SCHED_PENDING) {
		uint32_t target = ec_scheduler.query.due;
		if (reached(target, ec_scheduler.query.deadline))
			target = ec_scheduler.query.deadline;
		if (!active || reached(due, target)) {
			active = 1;
			due = target;
		}
	}
	ec_scheduler_alarm(active, due);
}
