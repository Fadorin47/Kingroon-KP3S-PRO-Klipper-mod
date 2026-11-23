// Parallel (8080 style) TFT displays on the stm32f1 FSMC bus
//
// Copyright (C) 2026  Aregus393 <97291209+Aregus393@users.noreply.github.com>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include "autoconf.h" // CONFIG_STM32_FSMC_LCD_IMAGES
#include "basecmd.h" // oid_alloc
#include "command.h" // DECL_COMMAND
#include "internal.h" // gpio_peripheral
#include "sched.h" // shutdown

DECL_CONSTANT("KINGROON_TFT24", 1);

// Run length decoder state (lets a long stream be written in pieces)
struct rle_state {
    const uint8_t *pos;
    uint32_t len, run;
    uint16_t color, literal;
};

struct fsmc_lcd {
    volatile uint16_t *cmd, *data;
    struct rle_state image;
};

// Most pixels written by one command, so no command runs for long
// (Klipper tasks should stay well below 500us)
#define FSMC_LCD_MAX_PIXELS 256

// MIPI DCS commands used for rectangle fills (ILI9488, ILI9341, ST7789...)
enum { DCS_CASET = 0x2a, DCS_PASET = 0x2b, DCS_RAMWR = 0x2c };

// Data bus D0-D15 followed by NOE and NWE (fixed pins on the stm32f1)
static const uint8_t fsmc_bus_pins[] = {
    GPIO('D', 14), GPIO('D', 15), GPIO('D', 0), GPIO('D', 1),
    GPIO('E', 7), GPIO('E', 8), GPIO('E', 9), GPIO('E', 10),
    GPIO('E', 11), GPIO('E', 12), GPIO('E', 13), GPIO('E', 14),
    GPIO('E', 15), GPIO('D', 8), GPIO('D', 9), GPIO('D', 10),
    GPIO('D', 4), GPIO('D', 5),
};

// Chip select for sub-banks NE1-NE4 of bank 1
static const uint8_t fsmc_ne_pins[] = {
    GPIO('D', 7), GPIO('G', 9), GPIO('G', 10), GPIO('G', 12),
};

// Address lines A0-A23 (the display RS / D-C signal uses one of them)
static const uint8_t fsmc_addr_pins[] = {
    GPIO('F', 0), GPIO('F', 1), GPIO('F', 2), GPIO('F', 3),
    GPIO('F', 4), GPIO('F', 5), GPIO('F', 12), GPIO('F', 13),
    GPIO('F', 14), GPIO('F', 15), GPIO('G', 0), GPIO('G', 1),
    GPIO('G', 2), GPIO('G', 3), GPIO('G', 4), GPIO('G', 5),
    GPIO('D', 11), GPIO('D', 12), GPIO('D', 13), GPIO('E', 3),
    GPIO('E', 4), GPIO('E', 5), GPIO('E', 6), GPIO('E', 2),
};

// Configure a pin as alternate function push-pull at 50Mhz
static void
fsmc_pin_setup(uint32_t pin)
{
    gpio_peripheral(pin, GPIO_FUNCTION(0), 0);
    GPIO_TypeDef *regs = gpio_pin_to_regs(pin);
    uint32_t pos = pin % 16, shift = (pos % 8) * 4, msk = 0xf << shift;
    volatile uint32_t *cr = pos & 0x8 ? &regs->CRH : &regs->CRL;
    *cr = (*cr & ~msk) | (0xb << shift);
}

void
command_config_fsmc_lcd(uint32_t *args)
{
    struct fsmc_lcd *l = oid_alloc(args[0], command_config_fsmc_lcd
                                   , sizeof(*l));
    uint32_t bank = args[1], rs_line = args[2];
    uint32_t addset = args[3], datast = args[4];
    if (bank < 1 || bank > ARRAY_SIZE(fsmc_ne_pins)
        || rs_line >= ARRAY_SIZE(fsmc_addr_pins)
        || addset > 15 || !datast || datast > 255)
        shutdown("Invalid fsmc_lcd parameters");

    RCC->AHBENR |= RCC_AHBENR_FSMCEN;
    int i;
    for (i = 0; i < ARRAY_SIZE(fsmc_bus_pins); i++)
        fsmc_pin_setup(fsmc_bus_pins[i]);
    fsmc_pin_setup(fsmc_ne_pins[bank - 1]);
    fsmc_pin_setup(fsmc_addr_pins[rs_line]);

    // SRAM mode A, 16 bit bus, write enabled
    uint32_t idx = (bank - 1) * 2;
    FSMC_Bank1->BTCR[idx + 1] = ((datast << FSMC_BTRx_DATAST_Pos)
                                 | (addset << FSMC_BTRx_ADDSET_Pos));
    FSMC_Bank1->BTCR[idx] = (FSMC_BCRx_MWID_0 | FSMC_BCRx_WREN
                             | FSMC_BCRx_MBKEN);

    // On a 16 bit bus internal address bit N+1 drives line A[N]
    uint32_t base = 0x60000000 + (bank - 1) * 0x04000000;
    l->cmd = (void*)base;
    l->data = (void*)(base + (2 << rs_line));
}
DECL_COMMAND(command_config_fsmc_lcd,
             "config_fsmc_lcd oid=%c bank=%c rs_line=%c addset=%c datast=%c");

static void
lcd_send(struct fsmc_lcd *l, uint8_t cmd, uint8_t len, uint8_t *data)
{
    *l->cmd = cmd;
    while (len--)
        *l->data = *data++;
}

void
command_fsmc_lcd_cmd(uint32_t *args)
{
    struct fsmc_lcd *l = oid_lookup(args[0], command_config_fsmc_lcd);
    uint8_t len = args[2], *data = command_decode_ptr(args[3]);
    lcd_send(l, args[1], len, data);
}
DECL_COMMAND(command_fsmc_lcd_cmd, "fsmc_lcd_cmd oid=%c cmd=%c data=%*s");

void
command_fsmc_lcd_fill(uint32_t *args)
{
    struct fsmc_lcd *l = oid_lookup(args[0], command_config_fsmc_lcd);
    uint32_t x0 = args[1], y0 = args[2], x1 = args[3], y1 = args[4];
    uint16_t color = args[5];
    if (x1 < x0 || y1 < y0)
        shutdown("Invalid fsmc_lcd fill rectangle");
    uint8_t xwin[4] = { x0 >> 8, x0, x1 >> 8, x1 };
    uint8_t ywin[4] = { y0 >> 8, y0, y1 >> 8, y1 };
    lcd_send(l, DCS_CASET, sizeof(xwin), xwin);
    lcd_send(l, DCS_PASET, sizeof(ywin), ywin);
    lcd_send(l, DCS_RAMWR, 0, NULL);
    volatile uint16_t *data = l->data;
    uint32_t count = (x1 - x0 + 1) * (y1 - y0 + 1);
    if (count > FSMC_LCD_MAX_PIXELS)
        shutdown("Oversized fsmc_lcd fill");
    while (count >= 8) {
        *data = color; *data = color; *data = color; *data = color;
        *data = color; *data = color; *data = color; *data = color;
        count -= 8;
    }
    while (count--)
        *data = color;
}
DECL_COMMAND(command_fsmc_lcd_fill,
             "fsmc_lcd_fill oid=%c x0=%hu y0=%hu x1=%hu y1=%hu color=%hu");

// Write raw little endian RGB565 pixels (continues the last RAMWR)
void
command_fsmc_lcd_pixels(uint32_t *args)
{
    struct fsmc_lcd *l = oid_lookup(args[0], command_config_fsmc_lcd);
    uint8_t len = args[1] / 2, *data = command_decode_ptr(args[2]);
    while (len--) {
        *l->data = data[0] | (data[1] << 8);
        data += 2;
    }
}
DECL_COMMAND(command_fsmc_lcd_pixels, "fsmc_lcd_pixels oid=%c data=%*s");

// Decode run length encoded pixels, writing at most max_px of them; the
// state allows continuing later. Each item starts with a header byte:
// 0x80|(n-1) is a run of n pixels of the following color, (n-1) is n
// literal pixels. Colors are little endian.
static uint32_t
lcd_write_rle(volatile uint16_t *data, struct rle_state *s, uint32_t max_px)
{
    uint32_t done = 0;
    while (done < max_px) {
        if (s->run) {
            uint32_t n = s->run < max_px - done ? s->run : max_px - done;
            uint16_t color = s->color;
            s->run -= n;
            done += n;
            while (n--)
                *data = color;
        } else if (s->literal) {
            uint32_t n = (s->literal < max_px - done
                          ? s->literal : max_px - done);
            const uint8_t *d = s->pos;
            s->literal -= n;
            done += n;
            while (n--) {
                *data = d[0] | (d[1] << 8);
                d += 2;
            }
            s->pos = d;
        } else {
            if (!s->len)
                break;
            uint32_t hdr = *s->pos++, count = (hdr & 0x7f) + 1;
            s->len--;
            if (hdr & 0x80) {
                if (s->len < 2)
                    shutdown("Invalid fsmc_lcd rle data");
                s->color = s->pos[0] | (s->pos[1] << 8);
                s->pos += 2;
                s->len -= 2;
                s->run = count;
            } else {
                if (s->len < count * 2)
                    shutdown("Invalid fsmc_lcd rle data");
                s->len -= count * 2;
                s->literal = count;
            }
        }
    }
    return done;
}

// Write run length encoded pixels (continues the last RAMWR); the host
// keeps the pixel count of one message within FSMC_LCD_MAX_PIXELS
void
command_fsmc_lcd_rle(uint32_t *args)
{
    struct fsmc_lcd *l = oid_lookup(args[0], command_config_fsmc_lcd);
    struct rle_state s = { .pos = command_decode_ptr(args[2]),
                           .len = args[1] };
    // Validate before writing: bound the amount of work per MCU command.
    uint32_t pos = 0, pixels = 0;
    const uint8_t *raw = s.pos;
    while (pos < s.len) {
        uint8_t h = raw[pos];
        uint32_t count = (h & 127) + 1;
        uint32_t size = (h & 128) ? 3 : 1 + 2 * count;
        pixels += count;
        if (pos + size > s.len || pixels > FSMC_LCD_MAX_PIXELS)
            shutdown("Invalid or oversized fsmc_lcd RLE");
        pos += size;
    }
    lcd_write_rle(l->data, &s, FSMC_LCD_MAX_PIXELS);
}
DECL_COMMAND(command_fsmc_lcd_rle, "fsmc_lcd_rle oid=%c data=%*s");


// Read-only display probe. Response includes the full first 16-bit word
// for older register-addressed controllers, then four low-byte reads.
void
command_fsmc_lcd_read(uint32_t *args)
{
    struct fsmc_lcd *l = oid_lookup(args[0], command_config_fsmc_lcd);
    uint8_t len = args[2], data[4];
    if (!len || len > sizeof(data))
        shutdown("Invalid fsmc_lcd read length");
    *l->cmd = args[1];
    uint16_t word = *l->data;
    data[0] = word;
    for (uint8_t i = 1; i < len; i++)
        data[i] = *l->data;
    sendf("fsmc_lcd_read_response oid=%c word=%hu data=%*s",
          args[0], word, len, data);
}
DECL_COMMAND(command_fsmc_lcd_read,
             "fsmc_lcd_read oid=%c cmd=%c len=%c");
