#ifndef EC_MG21_PLATFORM_H
#define EC_MG21_PLATFORM_H

#include <stddef.h>
#include <stdint.h>
#include "em_device.h"
#include "rail.h"
#include "ec-mg21-board.h"

#define CLOCK_SECOND 1000u
#define RTIMER_SECOND 1000000u
#define RTIMER_NOW() RAIL_GetTime()
typedef uint32_t clock_time_t;
typedef uint32_t rtimer_clock_t;
#define RADIO_PARAM_TX_MODE 1
#define RADIO_TX_MODE_SEND_ON_CCA 1
#define RADIO_TX_OK 0
#define RADIO_TX_ERR 1
#define RADIO_TX_COLLISION 2

struct ec_mg21_radio {
	int (*set_value)(int parameter, int value);
	int (*send)(const void *data, unsigned length);
	int (*off)(void);
};
extern const struct ec_mg21_radio NETSTACK_RADIO;

/** @brief Read monotonic milliseconds, wrapping after 49 days.
 * @return Millisecond count modulo 2^32.
 */
clock_time_t clock_time(void);
/** @brief Queue UART output from the main loop without masking RX interrupts.
 * @param data Borrowed bytes copied before return.
 * @param length Number of bytes; entire write is dropped on exhaustion.
 * @return Queued length, or -1 on exhaustion.
 */
int uart0_write(const void *data, size_t length);
/** @brief Register the bounded UART receive callback.
 * @param callback Callback invoked in the main loop after DMA-copy validation.
 * A zero byte requests discarding a damaged line through its next newline.
 */
void uart0_set_callback(int (*callback)(unsigned char));
/** @brief Flush UART boundedly and reboot into the existing Gecko bootloader. */
void ec_mg21_bootloader(void);
/** @brief Append radio hardware loss diagnostics to a status response.
 * @param out Destination buffer.
 * @param capacity Available bytes including the NUL terminator.
 */
void ec_mg21_status(char *out, size_t capacity);
/** @brief Save interrupt state and enter a short critical section.
 * @return Saved PRIMASK, for HwiP_restore().
 */
static inline uintptr_t HwiP_disable(void)
{
	uintptr_t key = __get_PRIMASK();
	__disable_irq();
	return key;
}
/** @brief Restore interrupt state after a critical section.
 * @param key Saved PRIMASK returned by HwiP_disable().
 */
static inline void HwiP_restore(uintptr_t key)
{
	__set_PRIMASK(key);
}
#endif
