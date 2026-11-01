#ifndef EC_UART_CONFIG_H_
#define EC_UART_CONFIG_H_

/* Application transport only; ROM/Gecko recovery keeps its own baud rate. */
#ifndef EC_UART_BAUD
#define EC_UART_BAUD 115200
#endif
#if EC_UART_BAUD != 115200 && EC_UART_BAUD != 230400 &&                        \
	EC_UART_BAUD != 460800 && EC_UART_BAUD != 921600
#error "Unsupported EC_UART_BAUD"
#endif

#endif
