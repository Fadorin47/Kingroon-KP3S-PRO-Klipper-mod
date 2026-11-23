# SPDX-License-Identifier: GPL-3.0-or-later
# Init values derived from Marlin's st7789v.h / ili9341.h (Marlin authors).
# Portrait 240x320; rotation/color order may need adjustment on the panel.

ST7789V_INIT = [
    (0x01, [], .150), (0x11, [], .120),
    (0xb2, [0x0c, 0x0c, 0, 0x33, 0x33], 0.),
    (0xb7, [0x35], 0.), (0xbb, [0x1f], 0.),
    (0xc0, [0x2c], 0.), (0xe4, [0x27, 0, 0x10], 0.),
    (0xc2, [1, 0xc3], 0.), (0xc4, [0x20], 0.),
    (0xc6, [0x0f], 0.), (0xd0, [0xa4, 0xa1], 0.),
    (0x3a, [0x55], 0.), (0x13, [], 0.), (0x29, [], .050),
]
ILI9341_INIT = [
    (0x01, [], .150), (0x11, [], .120),
    (0xcb, [0x39, 0x2c, 0, 0x34, 2], 0.),
    (0xcf, [0, 0xc1, 0x30], 0.), (0xe8, [0x85, 0, 0x78], 0.),
    (0xea, [0, 0], 0.), (0xed, [0x64, 3, 0x12, 0x81], 0.),
    (0xb6, [8, 0x82, 0x27], 0.), (0x20, [], 0.),
    (0xf7, [0x20], 0.), (0xc5, [0x3e, 0x28], 0.),
    (0xc7, [0x86], 0.), (0xb1, [0, 0x18], 0.),
    (0xc0, [0x23], 0.), (0xc1, [0x10], 0.),
    (0x3a, [0x55], 0.), (0xf2, [0], 0.), (0x26, [1], 0.),
    (0xe0, [15,49,43,12,14,8,78,241,55,7,16,3,14,9,0], 0.),
    (0xe1, [0,14,20,3,17,7,49,193,72,8,15,12,49,54,15], 0.),
    (0x13, [], 0.), (0x29, [], .050),
]
PROFILES = {'st7789v': ST7789V_INIT, 'ili9341': ILI9341_INIT}

def identify(readings):
    # Reads contain dummy byte + three ID bytes; same low-16 test as Marlin.
    ids = []
    for reg in ('04', 'd3'):
        raw = readings.get(reg, [])
        if len(raw) == 4:
            ids.append((raw[2] << 8) | raw[3])
    for ident, name in ((0x8552, 'st7789v'), (0x9341, 'ili9341')):
        if ident in ids:
            return name
    return None

def validate_rle(stream, expected_pixels, chunk_size=54, max_pixels=256):
    """Validate the entire rectangle before any MCU writes, split safely."""
    pos = count = cur_pixels = 0
    result, cur = [], bytearray()
    while pos < len(stream):
        header = stream[pos]
        pixels = (header & 127) + 1
        size = 3 if header & 128 else 1 + 2 * pixels
        if size > chunk_size or pos + size > len(stream):
            raise ValueError('Truncated or oversized RLE item')
        count += pixels
        if count > expected_pixels:
            raise ValueError('Too many RLE pixels')
        if len(cur) + size > chunk_size or cur_pixels + pixels > max_pixels:
            result.append(cur)
            cur, cur_pixels = bytearray(), 0
        cur.extend(stream[pos:pos+size])
        cur_pixels += pixels
        pos += size
    if count != expected_pixels:
        raise ValueError('RLE pixel count does not match rectangle')
    if cur:
        result.append(cur)
    return result
