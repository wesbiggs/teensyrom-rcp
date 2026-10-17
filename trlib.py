"""Minimal TeensyROM(+) remote-control client over TCP (stdlib only).

Protocol: https://github.com/SensoriumEmbedded/TeensyROM/blob/main/docs/ControlComms.md
Tokens/fields are sent big-endian; replies from the TR are little-endian.
"""
import json
import os
import socket
import struct
import time

DEFAULT_PORT = 2112

ACK = 0x64CC
FAIL = 0x9B7F
GET_DIR_NDJSON = 0x64DE
DIR_START = 0x5A5A
DIR_END = 0xA5A5

DRIVES = {"usb": 0, "sd": 1}
ALL_DRIVES = {**DRIVES, "tr": 2}  # "tr" = the TR's built-in menu; only ls and launch accept it


class TRError(Exception):
    pass


class TR:
    def __init__(self, host=None, port=None, timeout=10.0):
        self.host = host or os.environ.get("TR_HOST")
        if not self.host:
            raise TRError("No TR_HOST value set or --host specified")
        self.port = int(port or os.environ.get("TR_PORT", DEFAULT_PORT))
        self.timeout = timeout
        self._connect()

    def _connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise TRError(f"cannot connect to {self.host}:{self.port}: {e}") from e

    def reconnect(self):
        """Drop the connection (discarding any late reply bytes) and open a new one."""
        self.sock.close()
        self._connect()

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except socket.timeout as e:
                raise TRError("timed out waiting for TeensyROM") from e
            if not chunk:
                raise TRError("connection closed by TeensyROM")
            buf += chunk
        return buf

    def _fail_text(self):
        """After a FailToken the TR sends a line of text explaining why."""
        self.sock.settimeout(0.5)
        text = b""
        try:
            while not text.endswith(b"\n"):
                chunk = self.sock.recv(256)
                if not chunk:
                    break
                text += chunk
        except socket.timeout:
            pass
        return text.decode("utf-8", "replace").strip()

    def _expect_ack(self, what):
        (tok,) = struct.unpack("<H", self._recv(2))
        if tok == FAIL:
            raise TRError(f"{what}: {self._fail_text() or 'failed'}")
        if tok != ACK:
            raise TRError(f"{what}: unexpected reply 0x{tok:04x}")

    def list_dir(self, drive, path, skip=0, take=0xFFFF):
        """One page of a directory. Returns [{'type','name','size'?}, ...].

        Hidden dotfiles are omitted by the firmware. `skip` is 1-based per the
        firmware (skip=0 and skip=1 both start at the first entry).
        """
        if drive not in ALL_DRIVES:
            raise TRError(f"unknown drive {drive!r} (use sd, usb or tr)")
        self.sock.sendall(struct.pack(">H", GET_DIR_NDJSON))
        self._expect_ack("list")
        self.sock.sendall(
            struct.pack(">BHH", ALL_DRIVES[drive], skip, take) + path.encode() + b"\0"
        )
        self._expect_ack(f"list {drive}:{path}")  # directory exists
        (tok,) = struct.unpack("<H", self._recv(2))
        if tok != DIR_START:
            raise TRError(f"list: expected start marker, got 0x{tok:04x}")
        data = b""
        while not data.endswith(b"\xa5\xa5"):
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout as e:
                if drive == "tr":
                    raise TRError(
                        f"the TR stopped sending the menu listing after {len(data)} bytes "
                        "(a firmware limitation of listing tr: over TCP)") from e
                raise TRError("timed out reading directory listing") from e
            if not chunk:
                raise TRError("connection closed mid-listing")
            data += chunk
        entries = []
        for line in data[:-2].decode("utf-8", "replace").splitlines():
            if line.strip():
                entries.append(json.loads(line))
        return entries

    def list_all(self, drive, path, page=200):
        if drive == "tr":  # the TR ignores skip/take for its own menu and sends everything
            return self.list_dir(drive, path)
        out, skip = [], 1
        while True:
            entries = self.list_dir(drive, path, skip=skip, take=page)
            out.extend(entries)
            if len(entries) < page:
                return out
            skip += page


GET_FILE = 0x64B0


def _checksum(data_sum):
    return data_sum & 0xFFFF


def _get_file(self, drive, path, out, progress=None):
    """Download drive:path into the binary file object `out`. Returns byte count."""
    if drive not in DRIVES:
        raise TRError(f"unknown drive {drive!r} (use sd or usb)")
    self.sock.sendall(struct.pack(">H", GET_FILE))
    self._expect_ack("get")
    self.sock.sendall(struct.pack(">B", DRIVES[drive]) + path.encode() + b"\0")
    self._expect_ack(f"get {drive}:{path}")
    length, checksum = struct.unpack("<II", self._recv(8))
    total = remaining = length
    s = 0
    while remaining:
        chunk = self._recv(min(remaining, 4096))
        out.write(chunk)
        s += sum(chunk)
        remaining -= len(chunk)
        if progress:
            progress(total - remaining, total)
    self._expect_ack("get (end)")
    if _checksum(s) != (checksum & 0xFFFF):
        raise TRError(
            f"checksum mismatch: got 0x{_checksum(s):04x}, expected 0x{checksum & 0xFFFF:04x}"
        )
    return length


TR.get_file = _get_file


POST_FILE = 0x64BB
DELETE_FILE = 0x64CF
MAX_PATH = 358  # MaxNamePathLength in firmware


def _check_path(path):
    if len(path.encode()) >= MAX_PATH:
        raise TRError(f"path too long ({len(path.encode())} bytes, max {MAX_PATH - 1})")


def _put_file(self, drive, path, data, progress=None):
    """Upload bytes to drive:path (parent directories are created by the TR)."""
    if drive not in DRIVES:
        raise TRError(f"unknown drive {drive!r} (use sd or usb)")
    _check_path(path)
    self.sock.sendall(struct.pack(">H", POST_FILE))
    self._expect_ack("put")
    header = struct.pack(">IHB", len(data), sum(data) & 0xFFFF, DRIVES[drive])
    self.sock.sendall(header + path.encode() + b"\0")
    self._expect_ack(f"put {drive}:{path}")
    view = memoryview(data)
    for off in range(0, len(data), 4096):
        self.sock.sendall(view[off:off + 4096])
        if progress:
            progress(min(off + 4096, len(data)), len(data))
    self._expect_ack("put (end)")  # checksum verified by the TR
    return len(data)


def _delete_file(self, drive, path):
    if drive not in DRIVES:
        raise TRError(f"unknown drive {drive!r} (use sd or usb)")
    _check_path(path)
    self.sock.sendall(struct.pack(">H", DELETE_FILE))
    self._expect_ack("rm")
    self.sock.sendall(struct.pack(">B", DRIVES[drive]) + path.encode() + b"\0")
    self._expect_ack(f"rm {drive}:{path}")


TR.put_file = _put_file
TR.delete_file = _delete_file


COPY_FILE = 0x64FF
LAUNCH_FILE = 0x6444
GOOD_SID = 0x9B81
BAD_SID = 0x9B80
FW_CHECK = 0x64E0
FW_MINIMAL = 0x64E1
FW_FULL = 0x64E2
RESET_C64 = 0x64EE
VERSION_INFO = 0x6476
PAUSE_ON = 0x6431
PAUSE_OFF = 0x6430
READ_MEM = 0x64FD
WRITE_MEM = 0x64FB
MEM_CHUNK = 4096


def parse_target(spec, default_drive="sd"):
    """'sd:/a/b' | 'usb:/a' | '/a' -> (drive, '/a/b')."""
    drive, sep, path = spec.partition(":")
    if not sep or drive.lower() not in ALL_DRIVES:
        drive, path = default_drive, spec
    return drive.lower(), "/" + path.lstrip("/")


def split_host(arg):
    """'host[:port]' -> (host, port) with None for missing parts."""
    if not arg:
        return None, None
    host, _, port = arg.partition(":")
    return host, port or None


def parse_int(s):
    """Accepts 1234, 0x4d2, $4d2."""
    s = s.strip()
    if s.startswith("$"):
        return int(s[1:], 16)
    if s.isdigit():
        return int(s, 10)  # plain digits are decimal, even with leading zeros
    return int(s, 0)


def _copy_file(self, drive, src, dst):
    """Copy a file within one drive, on the TR itself. Overwrites dst."""
    if drive not in DRIVES:
        raise TRError(f"unknown drive {drive!r} (use sd or usb)")
    _check_path(src)
    _check_path(dst)
    self.sock.sendall(struct.pack(">H", COPY_FILE))
    self._expect_ack("cp")
    # The firmware reads a storage-type byte here that the protocol doc omits.
    self.sock.sendall(
        struct.pack(">B", DRIVES[drive]) + src.encode() + b"\0" + dst.encode() + b"\0"
    )
    self._expect_ack(f"cp {drive}:{src} -> {dst}")


def _launch(self, drive, path, sid_wait=3.0):
    """Launch drive:path (drive may be sd, usb or tr). Returns 'good' or 'bad' for a SID's
    header check (waiting up to `sid_wait` seconds for it), else None."""
    if drive not in ALL_DRIVES:
        raise TRError(f"unknown drive {drive!r} (use sd, usb or tr)")
    _check_path(path)
    self.sock.sendall(struct.pack(">H", LAUNCH_FILE))
    self._expect_ack("launch")
    self.sock.sendall(struct.pack(">B", ALL_DRIVES[drive]) + path.encode() + b"\0")
    self._expect_ack(f"launch {drive}:{path}")  # path accepted, launch started
    if not path.lower().endswith(".sid"):
        return None
    deadline = time.time() + sid_wait
    while time.time() < deadline:
        self.sock.settimeout(max(0.05, deadline - time.time()))
        try:
            chunk = self.sock.recv(2)
        except socket.timeout:
            break
        finally:
            self.sock.settimeout(self.timeout)
        if len(chunk) == 2:
            tok = struct.unpack("<H", chunk)[0]
            if tok == GOOD_SID:
                return "good"
            if tok == BAD_SID:
                return "bad"
    return None


def _read_idle(self, idle=0.5):
    """Read free-form text until the TR goes quiet."""
    self.sock.settimeout(idle)
    buf = b""
    try:
        while True:
            chunk = self.sock.recv(1024)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        pass
    self.sock.settimeout(10.0)
    return buf.decode("utf-8", "replace")


def _reset(self):
    self.sock.sendall(struct.pack(">H", RESET_C64))
    return _read_idle(self).strip()


def _version(self):
    self.sock.sendall(struct.pack(">H", VERSION_INFO))
    self._expect_ack("version")
    return _read_idle(self).strip("\n")


def _pause(self, on=True):
    self.sock.sendall(struct.pack(">H", PAUSE_ON if on else PAUSE_OFF))
    self._expect_ack("pause" if on else "resume")


def _check_range(addr, length):
    if not (0 <= addr <= 0xFFFF) or length < 0 or addr + length > 0x10000:
        raise TRError(f"range ${addr:04x}+{length} is outside $0000-$FFFF")


READ_CHUNK = 256
READ_RETRIES = 4


def _read_mem(self, addr, length):
    """DMA-read C64 memory (TR+ only).

    The TR+ sometimes fails to answer a read (it times out about one read in ten),
    so a timed-out chunk is retried on a fresh connection; reads are safe to repeat.
    """
    _check_range(addr, length)
    out = b""
    while len(out) < length:
        n = min(READ_CHUNK, length - len(out))
        here = addr + len(out)
        for attempt in range(READ_RETRIES):
            try:
                self.sock.sendall(struct.pack(">HHH", READ_MEM, here, n))
                self._expect_ack(f"read ${here:04x}")
                out += self._recv(n)
                break
            except TRError as e:
                if "timed out" not in str(e) or attempt == READ_RETRIES - 1:
                    raise
                self.reconnect()
    return out


def _write_mem(self, addr, data):
    """DMA-write C64 memory (TR+ only)."""
    _check_range(addr, len(data))
    for off in range(0, len(data), MEM_CHUNK):
        part = data[off:off + MEM_CHUNK]
        self.sock.sendall(struct.pack(">HHH", WRITE_MEM, addr + off, len(part)) + bytes(part))
        self._expect_ack(f"write ${addr + off:04x}")


TR.copy_file = _copy_file
def _fw_check(self):
    """Return 'full' or 'minimal' firmware."""
    self.sock.sendall(struct.pack(">H", FW_CHECK))
    (tok,) = struct.unpack("<H", self._recv(2))
    if tok == FW_FULL:
        return "full"
    if tok == FW_MINIMAL:
        return "minimal"
    raise TRError(f"fw check: unexpected reply 0x{tok:04x}")


TR.fw_check = _fw_check
TR.launch = _launch
TR.reset = _reset
TR.version = _version
TR.pause = _pause
TR.read_mem = _read_mem
TR.write_mem = _write_mem


SID_PAUSE = 0x6466
SID_SONG = 0x6488
SID_SPEED_LIN = 0x6499
SID_SPEED_LOG = 0x649A
SID_VOICE_MUTE = 0x6433
SET_COLOR = 0x6422

COLOR_REFS = ["background", "border", "banner", "time", "option", "sources", "names"]
COLOR_NAMES = [
    "black", "white", "red", "cyan", "purple", "green", "blue", "yellow",
    "orange", "brown", "ltred", "dkgrey", "grey", "ltgreen", "ltblue", "ltgrey",
]
SPEED_RANGE = {False: (-68.0, 127.99), True: (-127.0, 99.0)}  # linear, log (percent)


def _sid_cmd(self, what, token, payload=b""):
    """Send a SID command; the TR answers FailToken (with no text) if no SID is playing."""
    self.sock.sendall(struct.pack(">H", token) + payload)
    try:
        self._expect_ack(what)
    except TRError as e:
        if "failed" in str(e):
            raise TRError(f"{what}: refused (is a SID loaded and playing at the menu?)") from e
        raise


def _sid_pause(self):
    """Pause or resume SID playback (toggles)."""
    _sid_cmd(self, "sid pause", SID_PAUSE)


def _sid_song(self, number):
    """Select sub-song `number` (1-based) of the playing SID."""
    if not 1 <= number <= 256:
        raise TRError("song number must be 1-256")
    _sid_cmd(self, f"sid song {number}", SID_SONG, struct.pack(">B", number - 1))


def _sid_speed(self, percent, log=False):
    """Set SID playback speed. Linear: percent change from nominal (-68..<128).
    Log: the firmware's log-scale argument (-127..99); +50 is 2x speed, -100 is half speed."""
    lo, hi = SPEED_RANGE[log]
    if not lo <= percent <= hi:
        raise TRError(f"speed must be between {lo:g} and {hi:g} for {'log' if log else 'linear'} mode")
    raw = int(round(percent * 256))
    _sid_cmd(self, "sid speed", SID_SPEED_LOG if log else SID_SPEED_LIN, struct.pack(">h", raw))


def _sid_mute(self, voices):
    """Mute exactly the given voices (1-3); an empty list unmutes all."""
    if any(v not in (1, 2, 3) for v in voices):
        raise TRError("voices are 1, 2 or 3")
    mask = sum(1 << (v - 1) for v in set(voices))
    _sid_cmd(self, "sid mute", SID_VOICE_MUTE, struct.pack(">B", mask))


def _set_color(self, ref, color):
    """Set a TR menu UI color (stored in the TR's EEPROM; takes effect after a menu reset)."""
    if not 0 <= ref < len(COLOR_REFS) or not 0 <= color <= 15:
        raise TRError("bad color reference or color value")
    self.sock.sendall(struct.pack(">HBB", SET_COLOR, ref, color))
    self._expect_ack("color")


TR.sid_pause = _sid_pause
TR.sid_song = _sid_song
TR.sid_speed = _sid_speed
TR.sid_mute = _sid_mute
TR.set_color = _set_color


WRITE_SPANS = 0x64FC
MAX_SPANS = 64
MAX_SPAN_SLICES = 1024


def _check_spans(spans, slice_bytes):
    if not 1 <= len(spans) <= MAX_SPANS:
        raise TRError(f"need 1-{MAX_SPANS} spans, got {len(spans)}")
    total = slices = 0
    for addr, data in spans:
        end = addr + len(data)
        if not data:
            raise TRError(f"span at ${addr:04x} is empty")
        if not 0 <= addr <= 0xFFFF or end > 0x10000:
            raise TRError(f"span ${addr:04x}+{len(data)} runs outside $0000-$FFFF")
        if addr < 0xE000 and end > 0xDE00:
            raise TRError(f"span ${addr:04x}-${end - 1:04x} touches $DE00-$DFFF (the cartridge's own I/O)")
        total += len(data)
        slices += -(-len(data) // slice_bytes) if slice_bytes else 1
    if total > 0x10000:
        raise TRError(f"spans total {total} bytes; the limit is 65536")
    if slices > MAX_SPAN_SLICES:
        raise TRError(f"spans need {slices} slices; the limit is {MAX_SPAN_SLICES} (raise the slice size)")


def _write_spans(self, spans, slice_bytes=0, gap_us=0):
    """Write several C64 memory spans in one DMA job (TR+ only).

    spans: list of (address, bytes). Each DMA moves at most `slice_bytes` (0 = whole span, max 255)
    and the 6510 runs for `gap_us` microseconds (0-255) between slices, so interrupt-driven C64
    code is not starved. Smaller slices and longer gaps are gentler but slower.
    """
    if not 0 <= slice_bytes <= 255 or not 0 <= gap_us <= 255:
        raise TRError("slice bytes and gap must each be 0-255")
    _check_spans(spans, slice_bytes)
    self.sock.sendall(struct.pack(">H", WRITE_SPANS))
    self._spans_ack("spans", first=True)
    header = struct.pack(">BBBB", 0, slice_bytes, gap_us, len(spans))
    header += b"".join(struct.pack(">HH", a, len(d)) for a, d in spans)
    self.sock.sendall(header)
    self._spans_ack("spans (list)")
    self.sock.sendall(b"".join(bytes(d) for _, d in spans))
    self._spans_ack("spans (data)")


def _spans_ack(self, what, first=False):
    head = self._recv(2)
    tok = struct.unpack("<H", head)[0]
    if tok == ACK:
        return
    if tok == FAIL:
        text = self._fail_text()
        time.sleep(1.1)  # the TR discards what it is still owed for up to a second after a failure
        raise TRError(f"{what}: {text or 'failed'}")
    if first:  # old firmware treats the token as unknown and answers with text
        text = (head + (self._fail_text().encode())).decode("utf-8", "replace").strip()
        raise TRError(f"firmware doesn't support span writes ({text})")
    raise TRError(f"{what}: unexpected reply 0x{tok:04x}")


TR._spans_ack = _spans_ack
TR.write_spans = _write_spans
