#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_
extern const struct mac_driver ec_gateway_mac_driver;
#define NETSTACK_CONF_MAC ec_gateway_mac_driver
#define RF_CONF_MODE RF_MODE_2_4_GHZ
#define RF_CONF_BLE_BEACON_ENABLE 0
#define RF_CONF_TXPOWER_DBM 0
#define IEEE802154_CONF_DEFAULT_CHANNEL 11
#define IEEE_MODE_CONF_PROMISCOUS 1
#define IEEE_MODE_CONF_AUTOACK 0
#define TI_UART_CONF_BAUD_RATE 115200
#define TI_SPI_CONF_ENABLE 0
#define TI_I2C_CONF_ENABLE 0
#define TI_NVS_CONF_ENABLE 0
#ifdef EC_ENABLE_UPDATES
/*
 * The bootloader starts the watchdog; the update task feeds it and enforces
 * the unconfirmed trial deadline. Avoid a second TI Watchdog driver owner.
 */
#define WATCHDOG_CONF_DISABLE 1
#endif
#define CCFG_CONF_ROM_BOOTLOADER_ENABLE 1
#define CCFG_CONF_BL_PIN_NUMBER 15
#define CCFG_CONF_JTAG_INTERFACE_DISABLE 0
/* Preserve this module's verified factory clock/load/power configuration. */
#define CCFG_FORCE_VDDR_HH 1
#define SET_CCFG_MODE_CONF_DCDC_RECHARGE 1
#define SET_CCFG_MODE_CONF_DCDC_ACTIVE 1
#define SET_CCFG_MODE_CONF_XOSC_CAP_MOD 0
#define SET_CCFG_MODE_CONF_XOSC_CAPARRAY_DELTA 0xC1
#define SET_CCFG_MODE_CONF_VDDR_CAP 0xFF
#endif
