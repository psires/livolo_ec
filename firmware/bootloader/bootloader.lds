ENTRY(resetISR)
MEMORY { FLASH (rx) : ORIGIN = 0, LENGTH = 0x8000
         RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 0x14000 }
SECTIONS {
 .vectors : { KEEP(*(.vectors)) KEEP(*(.boot_version)) } > FLASH
 .text : { *(.text*) *(.rodata*) } > FLASH
 .ARM.exidx : { *(.ARM.exidx*) } > FLASH
 .data : { __data_start__ = .; *(.data*) __data_end__ = .; } > RAM AT> FLASH
 __data_load__ = LOADADDR(.data);
 .bss (NOLOAD) : { __bss_start__ = .; *(.bss*) *(COMMON) __bss_end__ = .; } > RAM
 ASSERT(__bss_end__ < 0x20010000, "Bootloader requires 16KiB stack reserve")
}
