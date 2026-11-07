#include "tusb.h"
#include "nrf.h"
#include "../version.h"
#include "usb-identity.h"
#include <stdio.h>
#include <string.h>

/* Development defaults remain available; release builds require explicit IDs. */
static const tusb_desc_device_t device = {
	.bLength = sizeof(tusb_desc_device_t),
	.bDescriptorType = TUSB_DESC_DEVICE,
	.bcdUSB = 0x0200,
	.bDeviceClass = TUSB_CLASS_MISC,
	.bDeviceSubClass = MISC_SUBCLASS_COMMON,
	.bDeviceProtocol = MISC_PROTOCOL_IAD,
	.bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
	.idVendor = EC_USB_VID,
	.idProduct = EC_USB_PID,
	.bcdDevice = ((EC_VERSION_MAJOR / 10) << 12) |
		     ((EC_VERSION_MAJOR % 10) << 8) |
		     ((EC_VERSION_MINOR / 10) << 4) | (EC_VERSION_MINOR % 10),
	.iManufacturer = 1,
	.iProduct = 2,
	.iSerialNumber = 3,
	.bNumConfigurations = 1,
};
static const uint8_t configuration[] = {
	TUD_CONFIG_DESCRIPTOR(1, 2, 0, TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN,
			      0, 100),
	TUD_CDC_DESCRIPTOR(0, 4, 0x81, 8, 0x02, 0x82, 64),
};

/** @brief Return the immutable USB device descriptor. */
const uint8_t *tud_descriptor_device_cb(void)
{
	return (const uint8_t *)&device;
}

/** @brief Return the sole USB configuration.
 * @param index Zero-based configuration index.
 * @return Borrowed descriptor, or NULL for an unsupported index.
 */
const uint8_t *tud_descriptor_configuration_cb(uint8_t index)
{
	return index == 0 ? configuration : NULL;
}

/** @brief Encode USB strings, including a stable hardware-derived serial.
 * @param index String descriptor index.
 * @param langid Requested language, ignored for these ASCII labels.
 * @return Borrowed UTF-16 descriptor valid until the next callback.
 */
const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
	static uint16_t buffer[64];
	char serial[17];
	const char *text;
	unsigned length;
	(void)langid;
	if (index == 0) {
		buffer[0] = (TUSB_DESC_STRING << 8) | 4;
		buffer[1] = 0x0409;
		return buffer;
	}
	snprintf(serial, sizeof(serial), "%08lx%08lx",
		 (unsigned long)NRF_FICR->DEVICEID[1],
		 (unsigned long)NRF_FICR->DEVICEID[0]);
	switch (index) {
	case 1:
		text = "Livolo local gateway";
		break;
	case 2:
		text = "EC gateway PCA10059";
		break;
	case 3:
		text = serial;
		break;
	case 4:
		text = "Gateway protocol 3";
		break;
	default:
		return NULL;
	}
	length = strlen(text);
	for (unsigned i = 0; i < length; i++)
		buffer[i + 1] = text[i];
	buffer[0] = (TUSB_DESC_STRING << 8) | (2 * length + 2);
	return buffer;
}
