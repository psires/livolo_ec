#ifndef EC_MG21_BOARD_H
#define EC_MG21_BOARD_H

/* Both profiles use USART0 PB1/PB0 and a 38.4 MHz HFXO, without an FEM. */
#if defined(EC_BOARD_SONOFF_ZBDONGLE_E) && defined(EC_BOARD_ZB_GW04_1V2)
#error "Select exactly one MG21 board"
#elif defined(EC_BOARD_SONOFF_ZBDONGLE_E)
#define EC_BOARD_NAME "sonoff-zbdongle-e"
#define EC_BOARD_HAS_RTS_PD4 0
#elif defined(EC_BOARD_ZB_GW04_1V2)
#define EC_BOARD_NAME "zb-gw04-1v2"
#define EC_BOARD_HAS_RTS_PD4 1
#else
#error "Select a supported MG21 board in the Makefile"
#endif

#define EC_BOARD_HFXO_CTUNE 128
#define EC_BOARD_MARKER "EC-MG21-BOARD=" EC_BOARD_NAME

#endif
