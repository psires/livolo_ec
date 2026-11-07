idf_component_register(SRCS "main.c" "../../experimental/transport.c"
    INCLUDE_DIRS "../../experimental"
    REQUIRES esp_driver_usb_serial_jtag)
