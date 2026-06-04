"""
protocol.py — Shared binary protocol encode/decode for PC ↔ MCU communication.

Frame format on wire:
    [SYNC 1B] [CMD 1B] [LEN 2B LE] [STUFFED_PAYLOAD...] [STUFFED_CRC 2B...]

Payload and CRC are byte-stuffed to eliminate 0xAA/0x55/0xAB from data,
so the sync byte 0xAA (or 0x55) can never appear mid-frame.

PC → MCU Sync: 0x55
MCU → PC Sync: 0xAA
"""

import struct
from typing import Optional, Tuple

# ── Constants ──────────────────────────────────────────────────

SYNC_PC_TO_MCU  = 0x55
SYNC_MCU_TO_PC  = 0xAA

ESCAPE_BYTE     = 0xAB
ESC_CODE_AA     = 0x01   # 0xAB 0x01 -> 0xAA
ESC_CODE_AB     = 0x02   # 0xAB 0x02 -> 0xAB
ESC_CODE_55     = 0x03   # 0xAB 0x03 -> 0x55

FRAME_OVERHEAD  = 6
MAX_PAYLOAD     = 1024   # Must hold 4(seq+n) + 256*2(samples) = 516 bytes + stuffing
MAX_FRAME       = FRAME_OVERHEAD + MAX_PAYLOAD * 2 + 10  # room for stuffing

# ── Commands: PC → MCU ─────────────────────────────────────────

CMD_START            = 0x01
CMD_STOP             = 0x02
CMD_SET_SAMPLE_RATE  = 0x03
CMD_SET_TRIG_MODE    = 0x04
CMD_SET_TRIG_LEVEL   = 0x05
CMD_SET_TRIG_EDGE    = 0x06
CMD_SET_WAVE_TYPE    = 0x07
CMD_SET_WAVE_FREQ    = 0x08
CMD_SET_WAVE_AMP     = 0x09
CMD_GET_STATUS       = 0x0A
CMD_GET_INFO         = 0x0B
CMD_SOFT_RESET       = 0x0C

# ── Responses: MCU → PC ────────────────────────────────────────

RESP_DATA = 0x01
RESP_ACK  = 0x80
RESP_NAK  = 0xFF

# ── Trigger modes ──────────────────────────────────────────────

TRIG_MODE_AUTO   = 0
TRIG_MODE_NORMAL = 1
TRIG_MODE_SINGLE = 2

# ── Trigger edge ───────────────────────────────────────────────

TRIG_EDGE_RISING  = 0
TRIG_EDGE_FALLING = 1
TRIG_EDGE_BOTH    = 2

# ── Waveform types ─────────────────────────────────────────────

WAVE_TYPE_SINE     = 0
WAVE_TYPE_TRIANGLE = 1
WAVE_TYPE_SQUARE   = 2
WAVE_TYPE_SAWTOOTH = 3
WAVE_TYPE_DC       = 4

WAVE_TYPE_NAMES = {
    0: "正弦波", 1: "三角波",
    2: "方波", 3: "锯齿波", 4: "直流"
}

# ── CRC-16-CCITT ───────────────────────────────────────────────

_CRC16_TABLE = [
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
    0x1231, 0x0210, 0x3273, 0x2252, 0x52B5, 0x4294, 0x72F7, 0x62D6,
    0x9339, 0x8318, 0xB37B, 0xA35A, 0xD3BD, 0xC39C, 0xF3FF, 0xE3DE,
    0x2462, 0x3443, 0x0420, 0x1401, 0x64E6, 0x74C7, 0x44A4, 0x5485,
    0xA56A, 0xB54B, 0x8528, 0x9509, 0xE5EE, 0xF5CF, 0xC5AC, 0xD58D,
    0x3653, 0x2672, 0x1611, 0x0630, 0x76D7, 0x66F6, 0x5695, 0x46B4,
    0xB75B, 0xA77A, 0x9719, 0x8738, 0xF7DF, 0xE7FE, 0xD79D, 0xC7BC,
    0x48C4, 0x58E5, 0x6886, 0x78A7, 0x0840, 0x1861, 0x2802, 0x3823,
    0xC9CC, 0xD9ED, 0xE98E, 0xF9AF, 0x8948, 0x9969, 0xA90A, 0xB92B,
    0x5AF5, 0x4AD4, 0x7AB7, 0x6A96, 0x1A71, 0x0A50, 0x3A33, 0x2A12,
    0xDBFD, 0xCBDC, 0xFBBF, 0xEB9E, 0x9B79, 0x8B58, 0xBB3B, 0xAB1A,
    0x6CA6, 0x7C87, 0x4CE4, 0x5CC5, 0x2C22, 0x3C03, 0x0C60, 0x1C41,
    0xEDAE, 0xFD8F, 0xCDEC, 0xDDCD, 0xAD2A, 0xBD0B, 0x8D68, 0x9D49,
    0x7E97, 0x6EB6, 0x5ED5, 0x4EF4, 0x3E13, 0x2E32, 0x1E51, 0x0E70,
    0xFF9F, 0xEFBE, 0xDFDD, 0xCFFC, 0xBF1B, 0xAF3A, 0x9F59, 0x8F78,
    0x9188, 0x81A9, 0xB1CA, 0xA1EB, 0xD10C, 0xC12D, 0xF14E, 0xE16F,
    0x1080, 0x00A1, 0x30C2, 0x20E3, 0x5004, 0x4025, 0x7046, 0x6067,
    0x83B9, 0x9398, 0xA3FB, 0xB3DA, 0xC33D, 0xD31C, 0xE37F, 0xF35E,
    0x02B1, 0x1290, 0x22F3, 0x32D2, 0x4235, 0x5214, 0x6277, 0x7256,
    0xB5EA, 0xA5CB, 0x95A8, 0x8589, 0xF56E, 0xE54F, 0xD52C, 0xC50D,
    0x34E2, 0x24C3, 0x14A0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
    0xA7DB, 0xB7FA, 0x8799, 0x97B8, 0xE75F, 0xF77E, 0xC71D, 0xD73C,
    0x26D3, 0x36F2, 0x0691, 0x16B0, 0x6657, 0x7676, 0x4615, 0x5634,
    0xD94C, 0xC96D, 0xF90E, 0xE92F, 0x99C8, 0x89E9, 0xB98A, 0xA9AB,
    0x5844, 0x4865, 0x7806, 0x6827, 0x18C0, 0x08E1, 0x3882, 0x28A3,
    0xCB7D, 0xDB5C, 0xEB3F, 0xFB1E, 0x8BF9, 0x9BD8, 0xABBB, 0xBB9A,
    0x4A75, 0x5A54, 0x6A37, 0x7A16, 0x0AF1, 0x1AD0, 0x2AB3, 0x3A92,
    0xFD2E, 0xED0F, 0xDD6C, 0xCD4D, 0xBDAA, 0xAD8B, 0x9DE8, 0x8DC9,
    0x7C26, 0x6C07, 0x5C64, 0x4C45, 0x3CA2, 0x2C83, 0x1CE0, 0x0CC1,
    0xEF1F, 0xFF3E, 0xCF5D, 0xDF7C, 0xAF9B, 0xBFBA, 0x8FD9, 0x9FF8,
    0x6E17, 0x7E36, 0x4E55, 0x5E74, 0x2E93, 0x3EB2, 0x0ED1, 0x1EF0
]

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) ^ _CRC16_TABLE[((crc >> 8) ^ byte) & 0xFF]) & 0xFFFF
    return crc

# ── Byte-stuffing ──────────────────────────────────────────────

def stuff_bytes(data: bytes) -> bytes:
    """Escape 0xAA, 0xAB, 0x55 so they never appear in output."""
    result = bytearray()
    for b in data:
        if b == 0xAA:
            result.extend([ESCAPE_BYTE, ESC_CODE_AA])
        elif b == 0xAB:
            result.extend([ESCAPE_BYTE, ESC_CODE_AB])
        elif b == 0x55:
            result.extend([ESCAPE_BYTE, ESC_CODE_55])
        else:
            result.append(b)
    return bytes(result)

def unstuff_into(data: bytes, offset: int, count: int):
    """Unstuff *count* unstuffed bytes from data starting at offset.
    Returns (unstuffed_bytes, new_offset)."""
    result = bytearray()
    i = offset
    while i < len(data) and len(result) < count:
        b = data[i]
        if b == ESCAPE_BYTE and i + 1 < len(data):
            code = data[i + 1]
            if code == ESC_CODE_AA:
                result.append(0xAA)
            elif code == ESC_CODE_AB:
                result.append(0xAB)
            elif code == ESC_CODE_55:
                result.append(0x55)
            else:
                result.append(b)
                result.append(code)
            i += 2
        else:
            result.append(b)
            i += 1
    return bytes(result), i

# ── Frame building (PC → MCU) ──────────────────────────────────

def build_command(cmd: int, payload: bytes = b"") -> bytes:
    """Build a complete command frame with stuffed payload+CRC."""
    header = struct.pack("<BBH", SYNC_PC_TO_MCU, cmd, len(payload))
    frame   = header + payload
    crc     = struct.pack("<H", crc16(frame))
    stuffed = stuff_bytes(payload + crc)
    return header + stuffed

def build_start() -> bytes:
    return build_command(CMD_START)
def build_stop() -> bytes:
    return build_command(CMD_STOP)
def build_set_sample_rate(rate_hz: int) -> bytes:
    return build_command(CMD_SET_SAMPLE_RATE, struct.pack("<I", rate_hz))
def build_set_trig_mode(mode: int) -> bytes:
    return build_command(CMD_SET_TRIG_MODE, struct.pack("<B", mode))
def build_set_trig_level(level: int) -> bytes:
    return build_command(CMD_SET_TRIG_LEVEL, struct.pack("<H", level))
def build_set_trig_edge(edge: int) -> bytes:
    return build_command(CMD_SET_TRIG_EDGE, struct.pack("<B", edge))
def build_set_wave_type(wave_type: int) -> bytes:
    return build_command(CMD_SET_WAVE_TYPE, struct.pack("<B", wave_type))
def build_set_wave_freq(freq_hz: int) -> bytes:
    return build_command(CMD_SET_WAVE_FREQ, struct.pack("<I", freq_hz))
def build_set_wave_amp(amp: int) -> bytes:
    return build_command(CMD_SET_WAVE_AMP, struct.pack("<H", amp))
def build_get_status() -> bytes:
    return build_command(CMD_GET_STATUS)
def build_get_info() -> bytes:
    return build_command(CMD_GET_INFO)
def build_soft_reset() -> bytes:
    return build_command(CMD_SOFT_RESET)

# ── Stuffed frame builder (MCU → PC), used by simulator ────────

def build_stuffed_response(sync: int, cmd: int, payload: bytes) -> bytes:
    """Build MCU→PC frame with stuffed payload+CRC."""
    header = struct.pack("<BBH", sync, cmd, len(payload))
    frame  = header + payload
    crc    = struct.pack("<H", crc16(frame))
    stuffed = stuff_bytes(payload + crc)
    return header + stuffed

# ── ParsedFrame ────────────────────────────────────────────────

class ParsedFrame:
    """Result of parsing a received frame."""

    def __init__(self, sync: int, cmd: int, payload: bytes, crc_ok: bool):
        self.sync = sync
        self.cmd = cmd
        self.payload = payload
        self.crc_ok = crc_ok

    def is_data(self) -> bool:
        return self.cmd == RESP_DATA
    def is_ack(self) -> bool:
        return (self.cmd & RESP_ACK) != 0
    def is_nak(self) -> bool:
        return self.cmd == RESP_NAK
    def ack_cmd(self) -> int:
        return self.cmd & ~RESP_ACK

    def parse_data_payload(self) -> Tuple[int, int, list]:
        if not self.is_data():
            raise ValueError("Not a data frame")
        if len(self.payload) < 4:
            raise ValueError("Data payload too short")
        seq, n = struct.unpack("<HH", self.payload[:4])
        samples = []
        for i in range(n):
            if 4 + (i+1)*2 > len(self.payload):
                break
            val = struct.unpack("<H", self.payload[4+i*2:4+(i+1)*2])[0]
            samples.append(val)
        return seq, n, samples

    def __repr__(self):
        tag = ""
        if self.is_ack():
            tag = f" ACK cmd=0x{self.ack_cmd():02X}"
        elif self.is_nak():
            tag = f" NAK"
        return (f"ParsedFrame(sync=0x{self.sync:02X}, cmd=0x{self.cmd:02X}, "
                f"len={len(self.payload)}, crc={'OK' if self.crc_ok else 'BAD'}{tag})")


# ── Robust FrameParser with byte-unstuffing ────────────────────

class FrameParser:
    """
    Flat-buffer stream parser. Appends bytes to an internal buffer,
    scans for 0xAA sync, tries to parse a complete frame, and removes
    consumed bytes on success. No residual state between frames.
    """

    def __init__(self):
        self._buf = bytearray()

    def reset(self):
        self._buf.clear()

    def feed(self, data: bytes) -> list:
        """Feed raw bytes. Returns list of complete ParsedFrame objects."""
        self._buf.extend(data)
        frames = []
        while True:
            frame = self._try_parse()
            if frame is None:
                break
            frames.append(frame)
        # Limit buffer growth (shouldn't happen in normal operation)
        if len(self._buf) > MAX_FRAME * 4:
            self._buf = self._buf[-MAX_FRAME:]
        return frames

    def _try_parse(self) -> Optional[ParsedFrame]:
        """Try to parse one frame from the front of the buffer."""
        # Find sync byte 0xAA
        sync_pos = -1
        for i in range(len(self._buf)):
            if self._buf[i] == SYNC_MCU_TO_PC:
                sync_pos = i
                break

        if sync_pos < 0:
            self._buf.clear()  # No sync found, discard noise
            return None

        if sync_pos > 0:
            del self._buf[:sync_pos]  # Discard noise before sync

        # Need header: SYNC(1) + CMD(1) + LEN(2) = 4 bytes
        if len(self._buf) < 4:
            return None

        cmd = self._buf[1]
        payload_len = self._buf[2] | (self._buf[3] << 8)

        if payload_len > MAX_PAYLOAD:
            del self._buf[0]  # Skip bad sync, retry
            return self._try_parse()

        # Unstuff payload_len + 2 (CRC) bytes from after header
        unstuffed, consumed = unstuff_into(self._buf[4:], 0, payload_len + 2)

        if len(unstuffed) < payload_len + 2:
            return None  # Wait for more data

        # Complete frame received
        total_consumed = 4 + consumed  # header + consumed raw bytes
        payload = unstuffed[:payload_len]
        crc_bytes = unstuffed[payload_len:payload_len + 2]
        expected_crc = crc_bytes[0] | (crc_bytes[1] << 8)

        # CRC over header + unstuffed payload
        computed_crc = crc16(bytes(self._buf[:4]) + payload)
        crc_ok = (expected_crc == computed_crc)

        del self._buf[:total_consumed]
        return ParsedFrame(SYNC_MCU_TO_PC, cmd, payload, crc_ok)
