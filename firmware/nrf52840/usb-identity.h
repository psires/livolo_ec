/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef EC_USB_IDENTITY_H
#define EC_USB_IDENTITY_H

/* A selected number is not evidence of an allocation or permission to use it. */
#ifndef EC_USB_VID
#define EC_USB_VID 0xcafe
#endif
#ifndef EC_USB_PID
#define EC_USB_PID 0x4001
#endif
#ifndef EC_USB_RELEASE
#define EC_USB_RELEASE 0
#endif

#if EC_USB_RELEASE != 0 && EC_USB_RELEASE != 1
#error "EC_USB_RELEASE must be 0 or 1"
#endif
#if EC_USB_VID <= 0 || EC_USB_VID >= 0xffff
#error "EC_USB_VID must be a nonreserved 16-bit vendor ID"
#endif
#if EC_USB_PID < 0 || EC_USB_PID > 0xffff
#error "EC_USB_PID must be a 16-bit product ID"
#endif
#if EC_USB_RELEASE && EC_USB_VID == 0xcafe
#error "Release firmware cannot use the TinyUSB development vendor ID"
#endif
#if EC_USB_VID == 0x1915 && EC_USB_PID == 0x521f
#error "The application must not impersonate the preserved factory DFU device"
#endif
#if EC_USB_VID == 0x16c0 && EC_USB_PID == 0x27dd
#error "Shared CDC identity requires an owner-qualified serial namespace first"
#endif

#endif
