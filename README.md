# teensyio

Small command-line tools for reading and writing a TeensyROM+ (TR+) SD card and USB drive, and for controlling the attached C64, over TCP. Python 3, standard library only.

They speak the [TeensyROM remote command protocol](https://github.com/SensoriumEmbedded/TeensyROM/blob/main/docs/ControlComms.md) on port 2112.

## Setup

1. On the TR+, set **Enable TCP Listener** to **On**.
2. Tell the tools where the TR+ is. There is **no default host**: every command that talks to the TR+ fails with `No TR_HOST value set or --host specified` unless you give one of:

   | Setting | How | Default |
   |---|---|---|
   | Host | `-H host[:port]` / `--host host[:port]`, or the `TR_HOST` environment variable | none (required) |
   | Port | the `:port` part of `-H`, or the `TR_PORT` environment variable | `2112` |

   `-H` takes precedence over `TR_HOST`. For example:

   ```bash
   export TR_HOST=192.168.1.50      # or your .local name, once, in your shell profile
   ./trls sd:/
   ./trls -H 192.168.1.50:2112 sd:/    # or per command
   ```

   `trpng` works on local files only and needs no host.

3. Run the scripts from this directory (they are executable), or put the directory on your `PATH`.

```bash
./trls -l sd:/
```

## Paths

Remote paths look like `[drive:]/path/to/file`, where `drive` is `sd` or `usb`. The drive defaults to `sd`. `tr:` (the TR's own built-in menu items) is also accepted by `trls` and `trrun` only. Paths may be up to 357 bytes.

Numbers for `trpeek` and `trpoke` accept decimal (`1024`, `53280`), hex (`0x400`) or C64-style hex (`$400`). Plain digits are always decimal, so `0400` means 400.

All commands exit 0 on success and 1 on any error, printing a message to stderr.

## Commands

### File access

| Command | Usage | Description |
|---|---|---|
| `trls` | `trls [-l] [drive:]path` | List a directory. Directories first, then files, sorted by name. `-l` adds type and size. The firmware filters some hidden entries, but not all: on a USB stick formatted by macOS, `.Spotlight-V100`, `.Trashes` and `._` files were listed. Large directories are fetched in pages of 200. `trls tr:/` lists the TR's built-in menu, but firmware v0.8 stops sending that listing after about 100 bytes over TCP, so it currently fails with an explanatory error; the items come flat, with a folder's contents following the folder. |
| `trget` | `trget [-f] [drive:]/file [local]` | Download a file. The local name defaults to the remote file name; `-` writes to stdout. Verifies the 16-bit checksum, writes to `<name>.part` and renames on success. Refuses to overwrite a local file unless `-f`. |
| `trput` | `trput [-f] local [drive:]/dest` | Upload a file. A destination ending in `/` keeps the local name; `-` as the local file reads stdin (then give a full remote path). Missing folders are created. The TR refuses to write over an existing file ("File already exists"); `-f` deletes the old one first, so the replacement is not atomic. The TR verifies the checksum. |
| `trrm` | `trrm [drive:]/file ...` | Delete one or more files. No undo. Files only; the firmware cannot remove directories. |
| `trcp` | `trcp [drive:]/src [drive:]/dst` | Copy a file on the TR+ itself (no data crosses the network). Both paths must be on the same drive. Creates missing folders and overwrites the destination. A destination ending in `/` keeps the name. |
| `trmv` | `trmv [drive:]/src [drive:]/dst` | Move or rename a file: copy on the TR+, confirm the copy's size, then delete the source. Same rules as `trcp`. Refuses to move a file onto itself. Not atomic, so an interrupted move can leave the file in both places. |

Examples:

```bash
./trls -l sd:/Demos
./trget "sd:/Demos/2 of 5 Swirler.prg"
./trput game.prg sd:/Games/
cat notes.txt | ./trput - sd:/notes.txt
./trcp sd:/Games/game.prg sd:/Backup/
./trmv sd:/Games/game.prg sd:/Games/old/game.prg
./trrm sd:/Games/old/game.prg
```

### Machine control

| Command | Usage | Description |
|---|---|---|
| `trrun` | `trrun [drive:]/file` | Launch a PRG, CRT, SID, etc. on the C64. The drive may be `sd`, `usb` or `tr` (a built-in menu item, e.g. `trrun tr:/Games/name`). For `.sid` files it waits up to 3 s for the TR's SID header verdict, prints `SID header ok`, and fails if the header is bad. |
| `trreset` | `trreset [-w]` | Reset the C64 and return to the TR menu. `-w` waits (up to 60 s) until the menu reports `Boot: complete`; this needs firmware that sends that line. |
| `trstat` | `trstat` | Print firmware version, build date and Teensy clock/temperature, plus machine type and boot state on firmware that reports them, and whether the TR is running its `full` or `minimal` firmware (minimal answers only a few commands). |
| `trpause` | `trpause [-r]` | Freeze the C64 via DMA. `-r` resumes it. |
| `trsid` | `trsid pause` / `song N` / `speed [--log] PCT` / `mute [voice ...]` | Control the SID that is playing at the TR menu. `pause` toggles pause/resume. `song N` selects sub-song N (1-based). `speed PCT` sets playback speed as a percent change from normal (-68 to just under 128; `0` is normal); `--log` uses the firmware's log scale instead (-127 to 99; +50 is double speed, -100 half speed). `mute 1 3` mutes those voices (1-3) and unmutes the rest; `mute` with no voices unmutes all. The TR refuses with a bare failure when no SID is playing. Put `-H` before the subcommand. |
| `trcolor` | `trcolor element color` | Set a color in the TR menu UI: element is `background border banner time option sources names`, color is a name (`black white red cyan purple green blue yellow orange brown ltred dkgrey grey ltgreen ltblue ltgrey`) or 0-15. Saved in the TR's EEPROM, so it is permanent; reset the menu to see it. |

### C64 memory (TR+ only)

| Command | Usage | Description |
|---|---|---|
| `trpeek` | `trpeek [-o file] addr [len]` | Read C64 memory by DMA. Length defaults to 256. Prints a hex dump; `-o file` writes raw bytes instead (`-` for stdout). |
| `trpoke` | `trpoke addr byte [byte ...]` or `trpoke -f file addr` | Write bytes, or a file (`-` for stdin), into C64 memory by DMA. |
| `trspans` | `trspans [-s slice] [-g gap] span...` | Write up to 64 memory regions in one DMA job (TR+ only). A span is `ADDR:FILE` or `ADDR=b1,b2,...`. Each DMA moves at most `-s` bytes (0 = whole span, max 255) and the C64 runs for `-g` microseconds (0-255) between slices, so interrupt-driven code isn't starved; smaller slices and longer gaps are gentler but slower. Limits: 64 KiB total, 1024 slices, nothing in `$DE00`-`$DFFF`. Refused while REU emulation runs or the C64 is paused. Needs span-capable firmware (older firmware answers `Unk cmd`). |
| `trscreendump` | `trscreendump [-a [-b]] [-c auto\|upper\|lower]` | Print the 40x25 text screen as ASCII, one line per row with trailing spaces trimmed. Fails if the VIC is in bitmap mode (`$D011`). Finds the screen address from the VIC bank (`$DD00`) and screen pointer (`$D018`). Reverse video is shown as normal text, graphics characters become `#`, and `£`/arrows become `[ \ ] ^ _`. `-c` picks the letter set; the default `auto` reads `$D018`. Refuses a screen located under I/O (`$D000`-`$DFFF`). `-a` adds color as 24-bit ANSI escapes in the C64 palette: foreground from color RAM (`$D800`), background from `$D021`, reverse-video cells swapped; `-b` omits the background. The border and multicolor text mode are not shown. |
| `trscreenshot` | `trscreenshot [-o file] [--png file] [-s n] [-f] [-p]` | Save the C64 bitmap screen as a file. Reads the VIC registers to find the mode and addresses: multicolor bitmap writes a Koala `.kla` (10003 bytes, load address `$6000`), hires bitmap writes an Art Studio `.art` (9002 bytes, load address `$2000`). Fails in text mode (use `trscreendump`) and in extended-color bitmap mode. The file defaults to `screenshot.kla`/`screenshot.art` and is not overwritten without `-f`; `-o -` writes to stdout. `--png file` also writes a PNG of the picture, scaled by `-s` (default 3). `-p` pauses the C64 during the capture. A bitmap or screen under I/O (`$D000`-`$DFFF`) is refused; one overlapping BASIC/KERNAL ROM space gets a warning, because DMA may read the ROM instead of the RAM the VIC sees. FLI, interlace and raster tricks are not captured. |
| `trpng` | `trpng [-s n] [-f] file [out.png]` | Convert a Koala `.kla` or Art Studio `.art` file to a PNG (default scale 3, so 960x600), using the Pepto C64 palette. Format is taken from the extension, load address or size; works with or without the 2-byte load address. Runs locally and needs no TR+. |
| `trtype` | `trtype [-r] [-s] [-n] text...` | Type on the C64 by filling the KERNAL keyboard buffer (`$0277`, count at `$C6`). Special keys in braces: `{F1}`-`{F8}`, `{RETURN}`, `{DEL}`, `{INST}`, `{HOME}`, `{CLR}`, `{UP}`, `{DOWN}`, `{LEFT}`, `{RIGHT}`, `{SPACE}`, or a raw PETSCII code such as `{147}`; `{{` and `}}` are literal braces; a newline is Return. `-r` presses Return at the end, `-s` sends upper-case letters shifted (default: all letters as unshifted keys, so `RUN` and `run` both work), `-n` prints the PETSCII codes without sending. Sends 10 keys at a time, waiting for the C64 to consume them. Only works for programs that read keys through the KERNAL. |

```bash
./trscreendump > screen.txt
./trscreendump -a           # colored, for a truecolor terminal
./trscreenshot -p -o pic.kla --png pic.png   # capture a graphics screen
./trpng pic.kla && open pic.png              # view a saved picture
./trtype -r 'load"*",8'     # then {F1}.. for function keys
./trpeek '$0400' 64          # quote $ so the shell doesn't expand it
./trpoke 0xd020 2            # red border
./trpoke -f sprite.bin 0x2000
```

Ranges must fit within `$0000`-`$FFFF`. Writes are sent in 4 KB chunks; reads in 256-byte chunks. The TR+ occasionally fails to answer a read, so `trpeek` and `trscreendump` retry a timed-out chunk on a new connection (up to 4 attempts).

## Library

[c64img.py](c64img.py) decodes Koala/Art files and writes PNGs (standard library only).

[trlib.py](trlib.py) holds the protocol client (`TR` class: `list_dir`, `list_all`, `get_file`, `put_file`, `delete_file`, `copy_file`, `launch`, `reset`, `version`, `pause`, `read_mem`, `write_mem`) and is easy to reuse from your own scripts:

```python
from trlib import TR
with TR() as tr:
    for e in tr.list_all("sd", "/"):
        print(e["type"], e["name"])
```

## Notes and limitations

- **Busy TR:** most file commands only work while the TR is idle at its menu; otherwise the TR answers `Busy!`. A reset clears this. While the ASID Player runs, TCP does not answer at all.
- **After errors:** the TR may be briefly unresponsive after a failed command, so the next call can time out once. Retrying works.
- **Directories:** the protocol has no make-directory, remove-directory or rename command. Folders are created as a side effect of `trput`, `trcp` and `trmv`, and cannot be removed remotely.
- **Protocol doc gaps:** `trcp` sends a storage-type byte before the paths that the protocol document omits; this was taken from the firmware source.
- **Not implemented:** only the deprecated JSON directory listing (`0x64DD`); everything else in the protocol has a command (ping and debug are left out on purpose).
- **Status:** developed against a TR+ on firmware v0.8. Verified on the real device: `trls`, `trget`, `trput`, `trrm`, `trcp`, `trmv` (SD card and USB drive), `trstat`, `trpeek`, `trpoke`, `trpause`, `trtype` (F3), `trscreendump`. `trspans` was tried and firmware v0.8 does not have the span command (it answers `Unk cmd: 0x64fc`, which `trspans` reports as "firmware doesn't support span writes"); its encoding was checked only against a mock, so it needs newer firmware to be proven. Also checked only against mocks: `trsid`, `trcolor`, `trscreenshot` graphics modes, and the `trrun` handshake and SID verdict. Not tried: `trreset`, and a real launch with `trrun`. `trls tr:/` was tried and stalls (see above); after it and a launch of a nonexistent `tr:` item, the test TR+ stopped answering reads of more than about 200 bytes until it was power-cycled, so avoid both on a machine you care about.

## License

[Apache-2.0](LICENSE)
