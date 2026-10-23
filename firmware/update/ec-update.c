/* Power-fail recoverable fixed-slot updater. No network state lives here. */
#include "ec-update.h"
#include "third_party/monocypher/monocypher-ed25519.h"
#include <string.h>
static uint8_t incoming[EC_MANIFEST_SIZE];
static uint32_t received;
static int receiving;

/**
 * @brief Read an unsigned little-endian word from a byte buffer.
 *
 * @param p Buffer containing at least four bytes.
 *
 * @return Decoded 32-bit value.
 */
uint32_t ec_u32(const uint8_t *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 |
	       (uint32_t)p[3] << 24;
}

/**
 * @brief Write and verify data within the writable firmware region.
 *
 * @param a Word-aligned destination flash address.
 * @param p SRAM buffer containing at least n bytes to persist.
 * @param n Byte count, divisible by four.
 *
 * @return Zero on success, or -1 for an invalid range or write failure.
 */
static int program(uint32_t a, const uint8_t *p, size_t n)
{
	if (a < EC_APP || a >= EC_FLASH_SIZE - EC_SECTOR ||
	    n > EC_FLASH_SIZE - EC_SECTOR - a || (a | n) & 3)
		return -1;
	ec_update_watchdog_feed();
	return ec_flash_program(a, p, n) || memcmp(ec_flash_read(a, n), p, n)
		       ? -1
		       : 0;
}

/**
 * @brief Prepare a writable flash sector for new contents.
 *
 * @param a Sector-aligned address to erase.
 *
 * @return Zero on success, or -1 for an invalid address or erase failure.
 */
static int erase(uint32_t a)
{
	if (a < EC_APP || a >= EC_FLASH_SIZE - EC_SECTOR || a % EC_SECTOR)
		return -1;
	ec_update_watchdog_feed();
	return ec_flash_erase(a);
}

/**
 * @brief Authenticate a manifest and check its board and image constraints.
 *
 * @param m Manifest containing EC_MANIFEST_SIZE readable bytes.
 *
 * @return Nonzero when the signed manifest is acceptable, otherwise zero.
 */
int ec_manifest_valid(const uint8_t *m)
{
	static const uint8_t magic[8] = {
		'E', 'C', 'U', 'P', 'D', '0', '1', 0
	};

	if (memcmp(m, magic, 8) || ec_u32(m + 8) != EC_UPDATE_FORMAT ||
	    ec_u32(m + 12) != EC_BOARD_ID || ec_u32(m + 16) > 65535 ||
	    ec_u32(m + 20) > 65535 || ec_u32(m + 24) != 3 ||
	    ec_u32(m + 28) != EC_APP || ec_u32(m + 32) < 256 ||
	    ec_u32(m + 32) > EC_SLOT_SIZE || ec_u32(m + 32) % 4 ||
	    ec_u32(m + 36))
		return 0;
	for (unsigned i = 104; i < 128; i++)
		if (m[i])
			return 0;
	ec_update_watchdog_feed();
	return crypto_ed25519_check(m + 128, ec_update_public_key, m, 128) == 0;
}

/**
 * @brief Authenticate a firmware image and validate its application entry point.
 *
 * @param a Start of a readable flash range large enough for the image.
 * @param m Non-NULL manifest containing EC_MANIFEST_SIZE readable bytes
 *          that describe and authenticate the image.
 *
 * @return Nonzero when the image is bootable, otherwise zero.
 */
int ec_image_valid(uint32_t a, const uint8_t *m)
{
	uint8_t digest[64];
	const uint8_t *p;
	uint32_t sp, pc;

	if (!ec_manifest_valid(m))
		return 0;
	p = ec_flash_read(a, ec_u32(m + 32));
	sp = ec_u32(p);
	pc = ec_u32(p + 4);
	if (sp <= 0x20000000 || sp > 0x20014000 || sp % 8 || !(pc & 1) ||
	    (pc & ~1u) < EC_APP + 8 || (pc & ~1u) >= EC_APP + ec_u32(m + 32))
		return 0;
	crypto_sha512(digest, p, ec_u32(m + 32));
	return !memcmp(digest, m + 40, 64);
}

/**
 * @brief Replace a metadata sector with a verified manifest.
 *
 * @param a Sector-aligned metadata address.
 * @param m Manifest containing EC_MANIFEST_SIZE bytes.
 *
 * @return Zero on success, or -1 on an erase or write failure.
 */
static int meta_write(uint32_t a, const uint8_t *m)
{
	return erase(a) || program(a, m, EC_MANIFEST_SIZE) ? -1 : 0;
}

/**
 * @brief Populate an image slot with an authenticated firmware image.
 *
 * @param dst Destination slot address.
 * @param src Source slot address.
 * @param m Manifest authenticating the source image.
 *
 * @return Zero when the destination verifies, or -1 on failure.
 */
static int copy_slot(uint32_t dst, uint32_t src, const uint8_t *m)
{
	uint8_t block[256];

	if (!ec_image_valid(src, m))
		return -1;
	for (uint32_t i = 0; i < EC_SLOT_SIZE; i += EC_SECTOR)
		if (erase(dst + i))
			return -1;
	for (uint32_t i = 0; i < ec_u32(m + 32); i += sizeof(block)) {
		size_t n = ec_u32(m + 32) - i;

		if (n > sizeof(block))
			n = sizeof(block);
		memcpy(block, ec_flash_read(src + i, n), n);
		if (program(dst + i, block, n))
			return -1;
	}
	return ec_image_valid(dst, m) ? 0 : -1;
}

/**
 * @brief Read the persisted update lifecycle state.
 *
 * @return Raw journal state word, including the erased value when uninitialized.
 */
uint32_t ec_update_state(void)
{
	return ec_u32(ec_flash_read(EC_JOURNAL, 4));
}

/**
 * @brief Persist the next update lifecycle state.
 *
 * @param state Journal state reachable by clearing bits in the current word.
 *
 * @return Zero on success, or -1 on a write or verification failure.
 */
static int state_write(uint32_t state)
{
	uint8_t p[4] = { (uint8_t)state, (uint8_t)(state >> 8),
			 (uint8_t)(state >> 16), (uint8_t)(state >> 24) };

	return program(EC_JOURNAL, p, 4);
}

/**
 * @brief Start receiving an authenticated upgrade newer than the active firmware.
 *
 * @param m Candidate manifest containing EC_MANIFEST_SIZE bytes.
 *
 * @return Zero when ready to receive, or -1 if rejected or preparation fails.
 */
int ec_update_begin(const uint8_t *m)
{
	uint32_t state = ec_update_state();
	const uint8_t *current =
		ec_flash_read(EC_CURRENT_META, EC_MANIFEST_SIZE);

	if (state == EC_TRIAL || state == EC_READY || state == EC_INSTALLING ||
	    state == EC_ROLLBACK)
		return -1;
	if (!ec_manifest_valid(m) || !ec_image_valid(EC_APP, current))
		return -1;
	if (ec_u32(m + 16) < ec_u32(current + 16) ||
	    (ec_u32(m + 16) == ec_u32(current + 16) &&
	     ec_u32(m + 20) <= ec_u32(current + 20)))
		return -1;
	receiving = 0;
	received = 0;
	memcpy(incoming, m, sizeof(incoming));
	for (uint32_t i = 0; i < EC_SLOT_SIZE; i += EC_SECTOR)
		if (erase(EC_STAGE + i))
			return -1;
	receiving = 1;
	return 0;
}

/**
 * @brief Accept the next candidate image block or an identical replay.
 *
 * @param offset Byte offset within the candidate image.
 * @param p SRAM buffer containing at least n candidate bytes to store.
 * @param n Positive, word-aligned byte count of at most 96.
 *
 * @return Zero when accepted, or -1 for invalid data or a write failure.
 */
int ec_update_write(uint32_t offset, const uint8_t *p, size_t n)
{
	if (!receiving || !n || n > 96 || (offset | n) & 3 ||
	    offset > received || offset > ec_u32(incoming + 32) ||
	    n > ec_u32(incoming + 32) - offset)
		return -1;
	if (offset < received)
		return offset + n <= received &&
				       !memcmp(ec_flash_read(EC_STAGE + offset,
							     n),
					       p, n)
			       ? 0
			       : -1;
	if (program(EC_STAGE + offset, p, n)) {
		receiving = 0;
		return -1;
	}
	received += n;
	return 0;
}

/**
 * @brief Commit a complete candidate for installation with a durable rollback image.
 *
 * @return Zero when the update is ready to install, or -1 on failure.
 */
int ec_update_finish(void)
{
	uint8_t old[EC_MANIFEST_SIZE];

	if (!receiving || received != ec_u32(incoming + 32) ||
	    !ec_image_valid(EC_STAGE, incoming))
		return -1;
	memcpy(old, ec_flash_read(EC_CURRENT_META, sizeof(old)), sizeof(old));
	if (copy_slot(EC_BACKUP, EC_APP, old) ||
	    meta_write(EC_BACKUP_META, old))
		return -1;
	/*
	 * The active image is untouched until backup and candidate are durable.
	 * READY is the final one-bit commit, after the signed header is read back.
	 */
	if (erase(EC_JOURNAL) ||
	    program(EC_CANDIDATE_META, incoming, sizeof(incoming)) ||
	    state_write(EC_READY))
		return -1;
	receiving = 0;
	return 0;
}

/**
 * @brief Discard volatile transfer state without changing persisted images.
 */
void ec_update_abort(void)
{
	receiving = 0;
	received = 0;
	memset(incoming, 0, sizeof(incoming));
}

/**
 * @brief Report progress of the current candidate transfer.
 *
 * @return Number of candidate image bytes accepted in this transfer.
 */
uint32_t ec_update_received(void)
{
	return received;
}

/**
 * @brief Report whether a candidate transfer is active.
 *
 * @return Nonzero while receiving, otherwise zero.
 */
int ec_update_receiving(void)
{
	return receiving;
}

/**
 * @brief Recover the previous authenticated application after an interrupted trial.
 *
 * @return One when recovery succeeds, otherwise zero.
 */
static int restore(void)
{
	uint8_t old[EC_MANIFEST_SIZE];

	memcpy(old, ec_flash_read(EC_BACKUP_META, sizeof(old)), sizeof(old));
	if (!ec_image_valid(EC_BACKUP, old))
		return 0;
	if (ec_update_state() != EC_ROLLBACK && state_write(EC_ROLLBACK))
		return 0;
	if (copy_slot(EC_APP, EC_BACKUP, old) ||
	    meta_write(EC_CURRENT_META, old) || state_write(EC_RESTORED))
		return 0;
	return 1;
}

/**
 * @brief Resolve pending installation or rollback before allowing an application boot.
 *
 * @return Nonzero when the application may safely run, otherwise zero.
 */
int ec_update_boot(void)
{
	uint32_t state = ec_update_state();
	uint8_t candidate[EC_MANIFEST_SIZE];

	ec_update_abort();
	if (state == EC_READY || state == EC_INSTALLING) {
		memcpy(candidate,
		       ec_flash_read(EC_CANDIDATE_META, sizeof(candidate)),
		       sizeof(candidate));
		if (!ec_image_valid(EC_STAGE, candidate) ||
		    !ec_image_valid(EC_BACKUP, ec_flash_read(EC_BACKUP_META,
							     EC_MANIFEST_SIZE)))
			return 0;
		if (state == EC_READY && state_write(EC_INSTALLING))
			return 0;
		if (copy_slot(EC_APP, EC_STAGE, candidate) ||
		    meta_write(EC_CURRENT_META, candidate) ||
		    state_write(EC_TRIAL))
			return 0;
		return 1;
	}
	/* TRIAL was persisted before jumping. Any reset before confirmation rolls back. */
	if (state == EC_TRIAL || state == EC_ROLLBACK)
		return restore();
	if (state != 0xffffffffu && state != EC_CONFIRMED &&
	    state != EC_RESTORED)
		return 0;
	return ec_image_valid(EC_APP,
			      ec_flash_read(EC_CURRENT_META, EC_MANIFEST_SIZE));
}

/**
 * @brief Accept the running trial image as the permanent application.
 *
 * @return Zero when confirmed or already confirmed, otherwise -1.
 */
int ec_update_confirm(void)
{
	if (ec_update_state() == EC_CONFIRMED)
		return 0;
	if (ec_update_state() != EC_TRIAL ||
	    !ec_image_valid(EC_APP,
			    ec_flash_read(EC_CURRENT_META, EC_MANIFEST_SIZE)))
		return -1;
	return state_write(EC_CONFIRMED);
}
