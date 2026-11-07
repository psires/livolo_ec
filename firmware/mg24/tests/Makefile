CC ?= cc
BUILD ?= build
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined

.PHONY: test clean
test: $(BUILD)/test_health
	$(BUILD)/test_health

$(BUILD)/test_health: ../health.c ../health.h test_health.c Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I.. ../health.c test_health.c -o $@

clean:
	rm -rf $(BUILD)
