"""Decode C64 Koala (multicolor) and Art Studio (hires) bitmaps and write them as PNG."""
import struct
import zlib

# C64 colors 0-15 (Pepto)
PALETTE = [
    (0x00, 0x00, 0x00), (0xFF, 0xFF, 0xFF), (0x68, 0x37, 0x2B), (0x70, 0xA4, 0xB2),
    (0x6F, 0x3D, 0x86), (0x58, 0x8D, 0x43), (0x35, 0x28, 0x79), (0xB8, 0xC7, 0x6F),
    (0x6F, 0x4F, 0x25), (0x43, 0x39, 0x00), (0x9A, 0x67, 0x59), (0x44, 0x44, 0x44),
    (0x6C, 0x6C, 0x6C), (0x9A, 0xD2, 0x84), (0x6C, 0x5E, 0xB5), (0x95, 0x95, 0x95),
]
KOALA_ADDR, ART_ADDR = 0x6000, 0x2000


def detect(data, name=""):
    """Return 'kla' or 'art' from the file extension, load address and size."""
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext in ("kla", "koa", "koala"):
        return "kla"
    if ext in ("art", "aas"):
        return "art"
    if len(data) >= 2:
        addr = data[0] | data[1] << 8
        if addr == KOALA_ADDR and len(data) >= 10003:
            return "kla"
        if addr == ART_ADDR and len(data) >= 9002:
            return "art"
    if len(data) in (10001, 10003):
        return "kla"
    if len(data) in (9000, 9002, 9009):
        return "art"
    raise ValueError("can't tell if this is a Koala or Art Studio file")


def decode(kind, data):
    """Return 200 rows of 320 palette indices. A 2-byte load address is skipped if present."""
    need = 10001 if kind == "kla" else 9000
    if len(data) >= need + 2:
        data = data[2:]  # skip the load address
    if len(data) < need:
        raise ValueError(f"file too short for {kind}: {len(data)} bytes")
    bitmap, screen = data[:8000], data[8000:9000]
    rows = [[0] * 320 for _ in range(200)]
    if kind == "kla":
        color, bg = data[9000:10000], data[10000] & 0x0F
    for cell in range(1000):
        cy, cx = divmod(cell, 40)
        s = screen[cell]
        hi, lo = s >> 4, s & 0x0F
        for r in range(8):
            b = bitmap[cell * 8 + r]
            row = rows[cy * 8 + r]
            x0 = cx * 8
            if kind == "art":
                for p in range(8):
                    row[x0 + p] = hi if b & (0x80 >> p) else lo
            else:
                pal = (bg, hi, lo, color[cell] & 0x0F)
                for p in range(4):
                    c = pal[(b >> (6 - 2 * p)) & 3]
                    row[x0 + 2 * p] = row[x0 + 2 * p + 1] = c
    return rows


def write_png(path, rows, scale=3):
    width, height = len(rows[0]) * scale, len(rows) * scale
    raw = bytearray()
    for row in rows:
        line = b"".join(bytes(PALETTE[c]) * scale for c in row)
        raw += (b"\x00" + line) * scale

    def chunk(tag, body):
        c = tag + body
        return struct.pack(">I", len(body)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
