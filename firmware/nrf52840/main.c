#include "ec-nrf-platform.h"
#include "ec-runtime.h"
#include "nrf_802154.h"
#include "nrf_clock.h"
#include "nrf_gpio.h"
#include "nrfx_power.h"
#include "tusb.h"
#include <stdio.h>
#include <string.h>

#define USB_SIZE 8192u
#define RX_SLOTS 16u
static uint8_t usb_tx[USB_SIZE], tx_frame[128];
static unsigned usb_head, usb_tail;
static unsigned long usb_lost;
static int (*usb_input)(unsigned char);
static volatile uint32_t milliseconds;
static volatile int tx_done, tx_result;
static unsigned use_cca;
static unsigned usb_ready;
static volatile unsigned long rf_drops, rx_errors, tx_timeouts;
static volatile unsigned long crc_errors, filter_errors, runtime_errors;
static volatile unsigned rx_head, rx_tail;
static struct {
	uint8_t data[125], length, lqi;
	int8_t rssi;
} rx_frames[RX_SLOTS];

/** @brief Reset through PCA10059's open-drain self-reset connection. */
static _Noreturn void reset_to_dfu(void)
{
	__disable_irq();
	nrf_gpio_pin_clear(19);
	nrf_gpio_cfg(19, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
		     NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_S0D1,
		     NRF_GPIO_PIN_NOSENSE);
	for (;;)
		__WFI();
}

/** @brief Run deferred driver clock notifications from thread context. */
void ec_nrf_clock_poll(void);
/** @brief Deliver a USB power transition to TinyUSB.
 * @param event Nordic USB power event number.
 */
void tusb_hal_nrf_power_event(uint32_t event);

/** @brief Advance the independent lease clock once per millisecond. */
void SysTick_Handler(void)
{
	milliseconds++;
}

clock_time_t clock_time(void)
{
	return milliseconds;
}

/** @brief Supply TinyUSB with monotonic milliseconds. @return Clock sample. */
uint32_t tusb_time_millis_api(void)
{
	return milliseconds;
}

uint32_t ec_nrf_now(void)
{
	NRF_TIMER3->TASKS_CAPTURE[0] = 1;
	return NRF_TIMER3->CC[0];
}

/** @brief Handle USB device interrupts without parsing gateway commands. */
void USBD_IRQHandler(void)
{
	tud_int_handler(0);
}

/** @brief Dispatch power events to the USB controller driver.
 * @param event Detected, removed or ready power transition.
 */
static void power_event(nrfx_power_usb_evt_t event)
{
	tusb_hal_nrf_power_event((uint32_t)event);
}

unsigned ec_uart_free(void)
{
	return (usb_tail - usb_head - 1) & (USB_SIZE - 1);
}

unsigned long ec_uart_dropped(void)
{
	return usb_lost;
}

int uart0_write(const void *data, size_t length)
{
	const uint8_t *bytes = data;
	if (length > ec_uart_free()) {
		usb_lost += length;
		return -1;
	}
	for (size_t i = 0; i < length; i++) {
		usb_tx[usb_head] = bytes[i];
		usb_head = (usb_head + 1) & (USB_SIZE - 1);
	}
	return (int)length;
}

void uart0_set_callback(int (*callback)(unsigned char))
{
	usb_input = callback;
}

/** @brief Pump bounded USB output without running host commands. */
static void usb_flush(void)
{
	tud_task();
	if (!tud_cdc_connected())
		return;
	unsigned length = usb_head >= usb_tail ? usb_head - usb_tail
					       : USB_SIZE - usb_tail;
	unsigned sent = tud_cdc_write(usb_tx + usb_tail, length);
	usb_tail = (usb_tail + sent) & (USB_SIZE - 1);
	tud_cdc_write_flush();
}

/** @brief Feed at most one bounded USB chunk to the shared line collector. */
static void usb_poll(void)
{
	uint8_t bytes[128];
	usb_flush();
	uint32_t length = tud_cdc_read(bytes, sizeof(bytes));
	for (uint32_t i = 0; i < length; i++)
		if (usb_input)
			usb_input(bytes[i]);
}

void ec_nrf_status(char *out, size_t capacity)
{
	snprintf(out, capacity,
		 " radio_drops=%lu radio_rx_errors=%lu radio_tx_timeouts=%lu"
		 " transport=usb_cdc uart_rx_errors=0 uart_rx_overruns=0"
		 " radio_crc_errors=%lu radio_filter_errors=%lu "
		 "radio_runtime_errors=%lu",
		 rf_drops, rx_errors, tx_timeouts, crc_errors, filter_errors,
		 runtime_errors);
}

void ec_nrf_bootloader(void)
{
	uint32_t start = clock_time();
	while ((usb_head != usb_tail ||
		tud_cdc_write_available() < CFG_TUD_CDC_TX_BUFSIZE) &&
	       clock_time() - start < 1000)
		usb_flush();
	/* Allow the final short CDC IN transfer to complete after its FIFO
	 * has emptied; FIFO space alone does not mean the host received it. */
	start = clock_time();
	while (clock_time() - start < 20)
		usb_flush();
	tud_disconnect();
	/* PCA10059 P0.19 is wired to nRESET. Factory USB bootloader enters
	 * DFU on pin reset; no MBR, UICR or bootloader flash is modified.
	 */
	reset_to_dfu();
}

/** @brief Copy one CRC-valid raw packet out of the driver's owned buffer.
 * @param data Driver-owned PHR, MAC and FCS; released before return.
 * @param power Received power in dBm.
 * @param lqi Link quality indicator from the driver.
 */
void nrf_802154_received_raw(uint8_t *data, int8_t power, uint8_t lqi)
{
	unsigned next = (rx_head + 1) % RX_SLOTS;
	unsigned length = data[0];
	if (length < 3 || length > 127 || next == rx_tail) {
		rf_drops++;
	} else {
		rx_frames[rx_head].length = length - 2;
		rx_frames[rx_head].rssi = power;
		rx_frames[rx_head].lqi = lqi;
		memcpy(rx_frames[rx_head].data, data + 1, length - 2);
		__DMB();
		rx_head = next;
	}
	nrf_802154_buffer_free_raw(data);
}

/** @brief Record receive failures separately from software queue exhaustion.
 * @param error Driver failure reason; intentional RX aborts are excluded.
 */
void nrf_802154_receive_failed(nrf_802154_rx_error_t error)
{
	if (error == NRF_802154_RX_ERROR_ABORTED)
		return;
	rx_errors++;
	if (error == NRF_802154_RX_ERROR_INVALID_FCS)
		crc_errors++;
	else if (error == NRF_802154_RX_ERROR_INVALID_FRAME ||
		 error == NRF_802154_RX_ERROR_INVALID_DEST_ADDR ||
		 error == NRF_802154_RX_ERROR_INVALID_LENGTH)
		filter_errors++;
	else if (error == NRF_802154_RX_ERROR_RUNTIME)
		runtime_errors++;
}

/** @brief Record successful TX and release an optional IEEE ACK buffer.
 * @param frame Borrowed persistent TX frame, not freed.
 * @param ack Driver-owned ACK buffer, or NULL.
 * @param power ACK power, unused.
 * @param lqi ACK quality, unused.
 */
void nrf_802154_transmitted_raw(const uint8_t *frame, uint8_t *ack,
				int8_t power, uint8_t lqi)
{
	(void)frame;
	(void)power;
	(void)lqi;
	if (ack)
		nrf_802154_buffer_free_raw(ack);
	tx_result = RADIO_TX_OK;
	tx_done = 1;
}

/** @brief Finish a failed asynchronous transmission.
 * @param frame Borrowed persistent TX frame.
 * @param error Driver error code mapped to the shared radio result.
 */
void nrf_802154_transmit_failed(const uint8_t *frame,
				nrf_802154_tx_error_t error)
{
	(void)frame;
	tx_result = error == NRF_802154_TX_ERROR_BUSY_CHANNEL
			    ? RADIO_TX_COLLISION
			    : RADIO_TX_ERR;
	tx_done = 1;
}

/** @brief Select single-CCA or immediate transmission.
 * @param parameter Shared radio parameter, must select TX mode.
 * @param value Nonzero enables CCA.
 * @return Zero on success, -1 for unsupported parameters.
 */
static int radio_setting(int parameter, int value)
{
	if (parameter != RADIO_PARAM_TX_MODE)
		return -1;
	use_cca = value != 0;
	return 0;
}

/** @brief Transmit one raw MAC frame with the existing 20 ms hardware bound.
 * @param data Borrowed MAC bytes without PHR/FCS, copied before starting.
 * @param length MAC length, 1..125 bytes.
 * @return Shared success, collision or error code; no additional retries.
 */
static int radio_send(const void *data, unsigned length)
{
	if (!length || length > 125)
		return RADIO_TX_ERR;
	tx_frame[0] = length + 2;
	memcpy(tx_frame + 1, data, length);
	tx_done = 0;
	tx_result = RADIO_TX_ERR;
	uint32_t started = ec_nrf_now();
	if (nrf_802154_transmit_raw(tx_frame, use_cca)) {
		while (!tx_done && (uint32_t)(ec_nrf_now() - started) < 20000u)
			ec_nrf_clock_poll();
		if (!tx_done)
			tx_timeouts++;
	}
	int result = tx_done ? tx_result : RADIO_TX_ERR;
	nrf_802154_receive();
	return result;
}

/** @brief Request radio idle before bootloader handoff. @return Zero. */
static int radio_off(void)
{
	nrf_802154_sleep();
	return 0;
}

const struct ec_nrf_radio NETSTACK_RADIO = { radio_setting, radio_send,
					     radio_off };

/** @brief Deliver one buffered radio packet before host work. */
static void radio_poll(void)
{
	if (rx_head == rx_tail)
		return;
	/* Keep the slot owned until ec_receive returns, including ACK TX. */
	__DMB();
	ec_receive(rx_frames[rx_tail].data, rx_frames[rx_tail].length,
		   rx_frames[rx_tail].rssi, rx_frames[rx_tail].lqi);
	__DMB();
	rx_tail = (rx_tail + 1) % RX_SLOTS;
}

/** @brief Return a failed startup to the preserved pin-reset DFU bootloader.
 * @param file Assertion source file, or startup stage.
 * @param line Source line, or zero for startup checks.
 * @param function Function name, if available.
 * @param expression Failed condition, if available.
 */
void __assert_func(const char *file, int line, const char *function,
		   const char *expression)
{
	if (usb_ready && !__get_IPSR() && !__get_PRIMASK()) {
		char message[220];
		int n = snprintf(message, sizeof(message),
				 "FATAL file=%s line=%d function=%s expr=%s\n",
				 file, line, function ? function : "?",
				 expression ? expression : "?");
		uart0_write(message, n > 0 && n < (int)sizeof(message)
					     ? (size_t)n
					     : sizeof(message) - 1);
		uint32_t start = clock_time();
		while (clock_time() - start < 100)
			usb_flush();
	}
	reset_to_dfu();
}

/** @brief Recover an unexpected CPU fault through the preserved bootloader. */
void HardFault_Handler(void)
{
	__assert_func("hardfault", 0, NULL, NULL);
}

/** @brief Fail closed if a required startup operation fails.
 * @param ok Nonzero permits startup; zero enters the USB DFU bootloader.
 */
static void require_ok(int ok)
{
	if (!ok)
		__assert_func("startup", 0, NULL, NULL);
}

/** @brief Emit a startup marker before entering potentially failing code.
 * @param name Constant name of the next initialization operation.
 */
static void stage(const char *name)
{
	uart0_write("INIT ", 5);
	uart0_write(name, strlen(name));
	uart0_write("\n", 1);
	uint32_t start = clock_time();
	while (clock_time() - start < 10)
		usb_flush();
}

/** @brief Start native USB and the raw radio, then poll the shared gateway.
 * @return Never returns.
 */
int main(void)
{
	/* MBR launches this application's vectors at 0x1000. */
	SCB->VTOR = 0x1000;
	__DSB();
	__ISB();
	__enable_irq();
	SystemCoreClockUpdate();
	SysTick_Config(SystemCoreClock / CLOCK_SECOND);
	NRF_CLOCK->TASKS_HFCLKSTART = 1;
	uint32_t start = clock_time();
	while (!nrf_clock_hf_is_running(NRF_CLOCK_HFCLK_HIGH_ACCURACY) &&
	       clock_time() - start < 100)
		;
	require_ok(nrf_clock_hf_is_running(NRF_CLOCK_HFCLK_HIGH_ACCURACY));
	NRF_CLOCK->LFCLKSRC = CLOCK_LFCLKSRC_SRC_RC;
	NRF_CLOCK->TASKS_LFCLKSTART = 1;
	start = clock_time();
	while (!nrf_clock_lf_is_running() && clock_time() - start < 1000)
		;
	require_ok(nrf_clock_lf_is_running());
	NRF_TIMER3->MODE = TIMER_MODE_MODE_Timer;
	NRF_TIMER3->BITMODE = TIMER_BITMODE_BITMODE_32Bit;
	NRF_TIMER3->PRESCALER = 4;
	NRF_TIMER3->TASKS_CLEAR = 1;
	NRF_TIMER3->TASKS_START = 1;
	const nrfx_power_config_t power = { 0 };
	const nrfx_power_usbevt_config_t events = { .handler = power_event };
	require_ok(nrfx_power_init(&power) == NRFX_SUCCESS);
	nrfx_power_usbevt_init(&events);
	nrfx_power_usbevt_enable();
	NVIC_SetPriority(USBD_IRQn, 2);
	uint32_t status = NRF_POWER->USBREGSTATUS;
	if (status & POWER_USBREGSTATUS_VBUSDETECT_Msk)
		tusb_hal_nrf_power_event(NRFX_POWER_USB_EVT_DETECTED);
	if (status & POWER_USBREGSTATUS_OUTPUTRDY_Msk)
		tusb_hal_nrf_power_event(NRFX_POWER_USB_EVT_READY);
	tusb_init();
	usb_ready = 1;
	/* Enumerate before radio startup so failures remain diagnosable.
         * This wait is bounded and is not a prerequisite for RF operation.
         */
	start = clock_time();
	while (!tud_cdc_connected() && clock_time() - start < 5000)
		usb_flush();
	stage("radio_init");
	nrf_802154_init();
	stage("radio_config");
	nrf_802154_auto_ack_set(false);
	nrf_802154_promiscuous_set(true);
	nrf_802154_channel_set(11);
	nrf_802154_tx_power_set(0);
	stage("radio_receive");
	require_ok(nrf_802154_receive());
	stage("gateway_start");
	ec_gateway_start();
	for (;;) {
		ec_nrf_clock_poll();
		radio_poll();
		usb_poll();
		ec_gateway_poll();
	}
}
