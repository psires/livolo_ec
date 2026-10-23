#ifndef EC_UPDATE_H
#define EC_UPDATE_H
#include <stddef.h>
#include <stdint.h>
#define EC_FLASH_SIZE 0x58000u
#define EC_SECTOR 0x2000u
#define EC_APP 0x8000u
#define EC_SLOT_SIZE 0x18000u
#define EC_STAGE 0x20000u
#define EC_BACKUP 0x38000u
#define EC_JOURNAL 0x50000u
#define EC_CURRENT_META 0x52000u
#define EC_BACKUP_META 0x54000u
#define EC_CANDIDATE_META (EC_JOURNAL + 256u)
#define EC_MANIFEST_SIZE 192u
#define EC_SIGNED_SIZE 128u
#define EC_BOARD_ID 0x26520001u
#define EC_UPDATE_FORMAT 0x00010000u
#define EC_READY 0xfffffffeu
#define EC_INSTALLING 0xfffffffcu
#define EC_TRIAL 0xfffffff8u
#define EC_CONFIRMED 0xfffffff0u
#define EC_ROLLBACK 0xffffffe8u
#define EC_RESTORED 0xffffffc8u
/*
 * Platform routines: reads never mutate; writes return 0 on success.
 * Only these bounded ranges may be written; bootloader and CCFG are excluded.
 */
/**
 * @brief Expose a bounded, read-only view of device flash.
 *
 * @param address First flash byte address.
 * @param size Number of readable bytes requested.
 *
 * @return Pointer to the requested range, or NULL when outside flash.
 */
const uint8_t *ec_flash_read(uint32_t address, size_t size);

/**
 * @brief Erase an allowed flash sector safely.
 *
 * @param address Sector-aligned destination address.
 *
 * @return Zero on success, or -1 for an invalid request or hardware failure.
 */
int ec_flash_erase(uint32_t address);

/**
 * @brief Program aligned flash data from SRAM while preserving platform state.
 *
 * @param address Word-aligned destination address.
 * @param data Source bytes held in SRAM.
 * @param size Positive byte count divisible by four.
 *
 * @return Zero on success, or -1 for an invalid request or hardware failure.
 */
int ec_flash_program(uint32_t address, const uint8_t *data, size_t size);

/**
 * @brief Keep an enabled watchdog alive during lengthy update work.
 */
void ec_update_watchdog_feed(void);
extern const uint8_t ec_update_public_key[32];

/**
 * @brief Read an unsigned little-endian word from a byte buffer.
 *
 * @param p Buffer containing at least four bytes.
 *
 * @return Decoded 32-bit value.
 */
uint32_t ec_u32(const uint8_t *p);

/**
 * @brief Authenticate a manifest and check its board and image constraints.
 *
 * @param manifest Manifest containing EC_MANIFEST_SIZE readable bytes.
 *
 * @return Nonzero when the signed manifest is acceptable, otherwise zero.
 */
int ec_manifest_valid(const uint8_t *manifest);

/**
 * @brief Authenticate a firmware image and validate its application entry point.
 *
 * @param address Start of a readable flash range large enough for the image.
 * @param manifest Non-NULL manifest containing EC_MANIFEST_SIZE readable
 *                 bytes that describe and authenticate the image.
 *
 * @return Nonzero when the image is bootable, otherwise zero.
 */
int ec_image_valid(uint32_t address, const uint8_t *manifest);

/**
 * @brief Start receiving an authenticated upgrade newer than the active firmware.
 *
 * @param manifest Candidate manifest containing EC_MANIFEST_SIZE bytes.
 *
 * @return Zero when ready to receive, or -1 if rejected or preparation fails.
 */
int ec_update_begin(const uint8_t *manifest);

/**
 * @brief Accept the next candidate image block or an identical replay.
 *
 * @param offset Byte offset within the candidate image.
 * @param data SRAM buffer containing at least size candidate bytes to store.
 * @param size Positive, word-aligned byte count of at most 96.
 *
 * @return Zero when accepted, or -1 for invalid data or a write failure.
 */
int ec_update_write(uint32_t offset, const uint8_t *data, size_t size);

/**
 * @brief Commit a complete candidate for installation with a durable rollback image.
 *
 * @return Zero when the update is ready to install, or -1 on failure.
 */
int ec_update_finish(void);

/**
 * @brief Discard volatile transfer state without changing persisted images.
 */
void ec_update_abort(void);

/**
 * @brief Report progress of the current candidate transfer.
 *
 * @return Number of candidate image bytes accepted in this transfer.
 */
uint32_t ec_update_received(void);

/**
 * @brief Report whether a candidate transfer is active.
 *
 * @return Nonzero while receiving, otherwise zero.
 */
int ec_update_receiving(void);

/**
 * @brief Read the persisted update lifecycle state.
 *
 * @return Raw journal state word, including the erased value when uninitialized.
 */
uint32_t ec_update_state(void);
/* Called on every reset, before branching to the fixed application slot. */
/**
 * @brief Resolve pending installation or rollback before allowing an application boot.
 *
 * @return Nonzero when the application may safely run, otherwise zero.
 */
int ec_update_boot(void);

/**
 * @brief Accept the running trial image as the permanent application.
 *
 * @return Zero when confirmed or already confirmed, otherwise -1.
 */
int ec_update_confirm(void);
#endif
