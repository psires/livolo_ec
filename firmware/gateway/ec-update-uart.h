#ifndef EC_UPDATE_UART_H
#define EC_UPDATE_UART_H

#include <stddef.h>

/**
 * @brief Handle one UART firmware-update command and produce its reply.
 *
 * @param count Token count, at least two.
 * @param tok Command tokens; token zero is the request ID.
 * @param out Reply buffer with room for fixed error replies.
 * @param size Available bytes in out for formatted replies.
 *
 * @return One for an update command, or zero when the command is unrelated.
 */
int ec_update_command(unsigned count, char **tok, char *out, size_t size);

/**
 * @brief Report whether an update or scheduled reboot requires exclusive
 *   access.
 *
 * @return Nonzero while receiving, ready to activate, or waiting to reboot.
 */
int ec_update_pending(void);

/**
 * @brief Service the watchdog and enforce reboot and trial-confirmation
 *   deadlines.
 */
void ec_update_poll(void);

/**
 * @brief Start the trial-confirmation deadline when the gateway process starts.
 */
void ec_update_init(void);

#endif
