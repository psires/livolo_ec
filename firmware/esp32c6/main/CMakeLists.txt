option(EC_RF_DIAGNOSTICS "Bounded synthetic RF diagnostics; not a gateway" ON)
set(EC_SOURCES "main.c" "../../experimental/transport.c")
set(EC_REQUIRES esp_driver_usb_serial_jtag)
set(EC_LINKER_FRAGMENTS)
if(EC_RF_DIAGNOSTICS)
    list(APPEND EC_SOURCES "radio.c" "health.c" "adapter.c" "../../experimental/rf.c")
    list(APPEND EC_REQUIRES ieee802154 esp_timer esp_driver_gpio)
    list(APPEND EC_LINKER_FRAGMENTS "radio.lf")
endif()
idf_component_register(SRCS ${EC_SOURCES}
    INCLUDE_DIRS "../../experimental"
    REQUIRES ${EC_REQUIRES}
    LDFRAGMENTS ${EC_LINKER_FRAGMENTS})
if(EC_RF_DIAGNOSTICS)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE EC_RF_DIAGNOSTICS=1)
endif()
