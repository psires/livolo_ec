/* Application only: reserve factory MBR, bootloader, settings and MBR RAM. */
MEMORY
{
  FLASH (rx) : ORIGIN = 0x1000, LENGTH = 0xdf000
  RAM (rwx) : ORIGIN = 0x20000008, LENGTH = 0x3fff8
}
SECTIONS
{
  .fw_version : { KEEP(*(.fw_version)) } > FLASH
} INSERT AFTER .text;
INCLUDE "nrf_common.ld"
