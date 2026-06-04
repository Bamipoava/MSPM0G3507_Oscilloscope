#!/usr/bin/env python3
"""
simulator.py — MSPM0G3507 MCU 协议模拟器

模拟下位机 MCU 行为，通过串口与上位机通信，用于无硬件时调试上位机软件。

Usage:
    # 使用真实串口 (需要另一台电脑或用虚拟串口对)
    python simulator.py --port COM3 --baud 921600

    # 使用虚拟串口对 (推荐: 配合 com0com 或 socat 使用)
    # 终端1: python simulator.py --port COM10 --baud 921600
    # 终端2: python main.py --simulator-port COM11
    # (COM10 ↔ COM11 是 com0com 创建的虚拟串口对)

    # 模拟不同波形和频率
    python simulator.py --port COM3 --wave sine --freq 500 --noise 0.05

用法说明:
    1. 安装虚拟串口工具 (推荐 com0com 或 Virtual Serial Port Emulator)
    2. 创建一对虚拟串口 (如 COM10 ↔ COM11)
    3. 一端运行模拟器, 另一端运行上位机
    4. 模拟器会响应所有协议命令并生成模拟波形数据
"""

import sys
import os
import time
import math
import struct
import random
import argparse
import threading
from collections import deque

# Add parent path to import shared protocol
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'oscilloscope_ui'))
from protocol import (
    SYNC_PC_TO_MCU, SYNC_MCU_TO_PC,
    CMD_START, CMD_STOP,
    CMD_SET_SAMPLE_RATE, CMD_SET_TRIG_MODE, CMD_SET_TRIG_LEVEL,
    CMD_SET_TRIG_EDGE, CMD_SET_WAVE_TYPE, CMD_SET_WAVE_FREQ,
    CMD_SET_WAVE_AMP, CMD_GET_STATUS, CMD_GET_INFO, CMD_SOFT_RESET,
    RESP_DATA, RESP_ACK, RESP_NAK,
    TRIG_MODE_AUTO, TRIG_MODE_NORMAL, TRIG_MODE_SINGLE,
    TRIG_EDGE_RISING, TRIG_EDGE_FALLING, TRIG_EDGE_BOTH,
    WAVE_TYPE_SINE, WAVE_TYPE_TRIANGLE, WAVE_TYPE_SQUARE,
    WAVE_TYPE_SAWTOOTH, WAVE_TYPE_DC,
    crc16, build_stuffed_response, unstuff_into, MAX_PAYLOAD,
)
import serial


# ================================================================
# Simulated MCU State
# ================================================================

class SimulatedMCU:
    """Maintains the same state as a real MSPM0 MCU."""

    VREF      = 3.3
    ADC_MAX   = 4095
    DAC_MAX   = 4095
    CPUCLK    = 80_000_000
    DEVICE_NAME = "MSPM0G3507 (SIM)"
    FW_VERSION  = 0x00010000

    # Waveform table size (matches firmware WAVE_TABLE_SIZE)
    WAVE_TABLE_SIZE = 256

    def __init__(self):
        # Acquisition state
        self.running       = False
        self.sample_rate   = 20000   # Hz
        self.seq_number    = 0
        self.buffer_size   = 256     # samples per frame

        # Trigger settings
        self.trig_mode     = TRIG_MODE_AUTO
        self.trig_level    = 2048     # 12-bit ADC (mid-scale)
        self.trig_edge     = TRIG_EDGE_RISING

        # Signal source settings
        self.wave_type     = WAVE_TYPE_SINE
        self.wave_freq     = 1000     # Hz
        self.wave_amp      = 2047     # 12-bit DAC

        # Noise
        self.noise_level   = 0.02     # Fraction of full scale

        # Internal simulation clock
        self._sim_time     = 0.0      # Virtual time in seconds
        self._last_sample_time = 0.0

    def reset(self):
        """Reset to defaults."""
        self.__init__()

    def generate_samples(self, n: int) -> list:
        """
        Generate n simulated ADC samples based on current settings.
        The simulated signal is the DAC output (signal source) connected
        to ADC input via a jumper wire.
        """
        samples = []
        dt = 1.0 / self.sample_rate

        for i in range(n):
            t = self._sim_time + i * dt

            # Generate signal based on waveform type and frequency
            phase = (t * self.wave_freq) % 1.0
            signal = self._generate_waveform_value(phase)

            # Add noise
            noise = random.gauss(0, self.noise_level * self.ADC_MAX)
            raw_value = int(signal + noise)

            # Clamp
            raw_value = max(0, min(self.ADC_MAX, raw_value))
            samples.append(raw_value)

        self._sim_time += n * dt
        return samples

    def _generate_waveform_value(self, phase: float) -> float:
        """Generate waveform value (0 to ADC_MAX) at given phase [0, 1)."""
        center = self.ADC_MAX / 2  # 2048
        amp    = self.wave_amp

        if self.wave_type == WAVE_TYPE_SINE:
            return center + amp * math.sin(2 * math.pi * phase)

        elif self.wave_type == WAVE_TYPE_TRIANGLE:
            if phase < 0.5:
                return center + amp * (4 * phase - 1)
            else:
                return center + amp * (3 - 4 * phase)

        elif self.wave_type == WAVE_TYPE_SQUARE:
            return center + (amp if phase < 0.5 else -amp)

        elif self.wave_type == WAVE_TYPE_SAWTOOTH:
            return center + amp * (2 * phase - 1)

        elif self.wave_type == WAVE_TYPE_DC:
            return center  # DC offset at mid-scale

        else:
            return center

    def build_data_frame(self, samples: list) -> bytes:
        """Build a stuffed RESP_DATA frame."""
        n = len(samples)
        payload = struct.pack("<HH", self.seq_number, n)
        for val in samples:
            payload += struct.pack("<H", val & 0xFFFF)
        self.seq_number = (self.seq_number + 1) & 0xFFFF
        return build_stuffed_response(SYNC_MCU_TO_PC, RESP_DATA, payload)

    def build_ack(self, cmd: int, payload: bytes = b"") -> bytes:
        """Build a stuffed ACK response frame."""
        return build_stuffed_response(SYNC_MCU_TO_PC, RESP_ACK | cmd, payload)

    def build_nak(self, error_code: int = 1) -> bytes:
        """Build a stuffed NAK response frame."""
        return build_stuffed_response(SYNC_MCU_TO_PC, RESP_NAK, bytes([error_code]))

    def build_status_payload(self) -> bytes:
        """Build status response payload."""
        return struct.pack(
            "<B I B H B B I H",
            self.running,
            self.sample_rate,
            self.trig_mode,
            self.trig_level,
            self.trig_edge,
            self.wave_type,
            self.wave_freq,
            self.wave_amp,
        )

    def build_info_payload(self) -> bytes:
        """Build device info response payload."""
        name_bytes = self.DEVICE_NAME.encode('ascii').ljust(16, b'\x00')[:16]
        return struct.pack("<16s I I B B I",
            name_bytes,
            self.FW_VERSION,
            100000,      # max_sample_rate
            12,          # adc_bits
            12,          # dac_bits
            self.CPUCLK,
        )

    def handle_command_raw(self, raw_frame: bytes) -> bytes | None:
        """
        Process a raw command frame (from PC, sync=0x55).
        Payload+CRC are byte-stuffed; we un-stuff before processing.
        Returns response bytes or None.
        """
        if len(raw_frame) < 6:
            return None

        sync = raw_frame[0]
        if sync != SYNC_PC_TO_MCU:
            return None

        cmd = raw_frame[1]
        payload_len = struct.unpack("<H", raw_frame[2:4])[0]

        if payload_len > MAX_PAYLOAD:
            return None

        # Unstuff payload+CRC from the bytes after header
        unstuffed, consumed = unstuff_into(raw_frame[4:], 0, payload_len + 2)
        if len(unstuffed) < payload_len + 2:
            return None  # Incomplete

        payload = unstuffed[:payload_len]
        crc_bytes = unstuffed[payload_len:payload_len + 2]
        expected_crc = crc_bytes[0] | (crc_bytes[1] << 8)

        # CRC over header + unstuffed payload
        header = raw_frame[:4]
        computed_crc = crc16(header + payload)

        if expected_crc != computed_crc:
            return self.build_nak(2)  # BAD_CRC

        if cmd == CMD_START:
            self.running = True
            self._sim_time = 0.0
            return self.build_ack(CMD_START)

        elif cmd == CMD_STOP:
            self.running = False
            return self.build_ack(CMD_STOP)

        elif cmd == CMD_SET_SAMPLE_RATE:
            if len(payload) >= 4:
                self.sample_rate = struct.unpack("<I", payload[:4])[0]
                self.sample_rate = max(1000, min(100000, self.sample_rate))
                return self.build_ack(CMD_SET_SAMPLE_RATE)
            return self.build_nak(6)

        elif cmd == CMD_SET_TRIG_MODE:
            if len(payload) >= 1 and payload[0] <= TRIG_MODE_SINGLE:
                self.trig_mode = payload[0]
                return self.build_ack(CMD_SET_TRIG_MODE)
            return self.build_nak(6)

        elif cmd == CMD_SET_TRIG_LEVEL:
            if len(payload) >= 2:
                level = struct.unpack("<H", payload[:2])[0]
                if level <= 4095:
                    self.trig_level = level
                    return self.build_ack(CMD_SET_TRIG_LEVEL)
            return self.build_nak(6)

        elif cmd == CMD_SET_TRIG_EDGE:
            if len(payload) >= 1 and payload[0] <= TRIG_EDGE_BOTH:
                self.trig_edge = payload[0]
                return self.build_ack(CMD_SET_TRIG_EDGE)
            return self.build_nak(6)

        elif cmd == CMD_SET_WAVE_TYPE:
            if len(payload) >= 1 and payload[0] <= WAVE_TYPE_DC:
                self.wave_type = payload[0]
                return self.build_ack(CMD_SET_WAVE_TYPE)
            return self.build_nak(6)

        elif cmd == CMD_SET_WAVE_FREQ:
            if len(payload) >= 4:
                self.wave_freq = struct.unpack("<I", payload[:4])[0]
                self.wave_freq = max(10, min(10000, self.wave_freq))
                return self.build_ack(CMD_SET_WAVE_FREQ)
            return self.build_nak(6)

        elif cmd == CMD_SET_WAVE_AMP:
            if len(payload) >= 2:
                amp = struct.unpack("<H", payload[:2])[0]
                if amp <= 4095:
                    self.wave_amp = amp
                    return self.build_ack(CMD_SET_WAVE_AMP)
            return self.build_nak(6)

        elif cmd == CMD_GET_STATUS:
            return self.build_ack(CMD_GET_STATUS, self.build_status_payload())

        elif cmd == CMD_GET_INFO:
            return self.build_ack(CMD_GET_INFO, self.build_info_payload())

        elif cmd == CMD_SOFT_RESET:
            self.reset()
            return self.build_ack(CMD_SOFT_RESET)

        else:
            return self.build_nak(1)  # UNKNOWN_CMD


# ================================================================
# Simulator Main Loop
# ================================================================

class MCUSimulator:
    """Main simulator: manages serial port and MCU state."""

    def __init__(self, port: str, baud: int = 921600):
        self.port = port
        self.baud = baud
        self.mcu = SimulatedMCU()
        self._serial: serial.Serial | None = None
        self._running = False

        # Data generation thread
        self._data_thread: threading.Thread | None = None
        self._data_lock = threading.Lock()
        self._tx_queue: deque = deque()

        # Raw byte buffer for PC→MCU frame parsing (sync=0x55)
        self._rx_buf = bytearray()

    def start(self):
        """Open serial port and start the simulation."""
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.01,
                write_timeout=0.1,
            )
        except Exception as e:
            print(f"[ERROR] 无法打开串口 {self.port}: {e}")
            return False

        self._running = True
        print(f"[SIM] 模拟器已启动")
        print(f"[SIM] 串口: {self.port} @ {self.baud} bps")
        print(f"[SIM] 设备: {self.mcu.DEVICE_NAME}")
        print(f"[SIM] 默认波形: 正弦波 1kHz, 采样率 20ksps")
        print(f"[SIM] 等待上位机连接...")
        print()

        # Start data generation thread
        self._data_thread = threading.Thread(target=self._data_generator, daemon=True)
        self._data_thread.start()

        # Main I/O loop
        try:
            self._io_loop()
        except KeyboardInterrupt:
            print()
        finally:
            self.stop()

        return True

    def stop(self):
        """Stop simulation and close port."""
        self._running = False
        if self._data_thread and self._data_thread.is_alive():
            self._data_thread.join(timeout=2.0)
        if self._serial and self._serial.is_open:
            self._serial.close()
        print("[SIM] 模拟器已停止")

    def _parse_pc_frames(self, data: bytes) -> list:
        """Extract complete PC→MCU frames (sync=0x55) from raw bytes.
        Returns list of raw frame bytes."""
        frames = []
        self._rx_buf.extend(data)

        while len(self._rx_buf) >= 4:
            # Find sync byte 0x55
            sync_pos = self._rx_buf.find(b'\x55')
            if sync_pos < 0:
                self._rx_buf.clear()
                break
            if sync_pos > 0:
                del self._rx_buf[:sync_pos]  # Discard before sync

            # Read header: SYNC(1) + CMD(1) + LEN(2) = 4 bytes
            if len(self._rx_buf) < 4:
                break

            payload_len = self._rx_buf[2] | (self._rx_buf[3] << 8)
            total_len = 4 + payload_len + 2  # header + payload + CRC16

            if payload_len > MAX_PAYLOAD:
                # Invalid length, skip this sync byte
                del self._rx_buf[0]
                continue

            if len(self._rx_buf) >= total_len:
                frame = bytes(self._rx_buf[:total_len])
                del self._rx_buf[:total_len]
                frames.append(frame)
            else:
                break  # Wait for more data

        return frames

    def _io_loop(self):
        """Main serial I/O loop."""
        while self._running:
            try:
                # ── Read ──
                if self._serial.in_waiting > 0:
                    data = self._serial.read(self._serial.in_waiting)
                    frames = self._parse_pc_frames(data)
                    for raw_frame in frames:
                        response = self.mcu.handle_command_raw(raw_frame)
                        if response is not None:
                            self._tx_queue.append(response)

                # ── Write ──
                while self._tx_queue:
                    packet = self._tx_queue.popleft()
                    try:
                        self._serial.write(packet)
                        self._serial.flush()
                    except serial.SerialException:
                        pass

                # Yield
                time.sleep(0.005)

            except serial.SerialException as e:
                print(f"[ERROR] {e}")
                break
            except Exception as e:
                print(f"[ERROR] {e}")
                break

    def _data_generator(self):
        """
        Background thread: generates ADC data frames when acquisition is running.
        Matches the real MCU data rate based on sample_rate and buffer_size.
        """
        while self._running:
            if self.mcu.running:
                # Calculate time to generate one buffer of data
                dt_per_buffer = self.mcu.buffer_size / self.mcu.sample_rate  # seconds

                # Generate samples
                with self._data_lock:
                    samples = self.mcu.generate_samples(self.mcu.buffer_size)
                    frame = self.mcu.build_data_frame(samples)
                    self._tx_queue.append(frame)

                # Wait to simulate real sampling rate
                # Use actual wall time to match the configured sample rate
                time.sleep(max(0.001, dt_per_buffer * 0.9))  # Slightly faster than real-time
            else:
                time.sleep(0.1)  # Idle polling when stopped


# ================================================================
# Entry Point
# ================================================================

def main():
    parser = argparse.ArgumentParser(
        description="MSPM0G3507 MCU 协议模拟器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python simulator.py --port COM10 --baud 921600
  python simulator.py --port COM3 --wave square --freq 2000
  python simulator.py --port COM8 --list-commands
        """
    )
    parser.add_argument("--port", type=str, required=True,
                        help="串口名称 (如 COM3, /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=921600,
                        help="波特率 (默认: 921600)")
    parser.add_argument("--wave", type=str, default="sine",
                        choices=["sine", "triangle", "square", "sawtooth", "dc"],
                        help="默认波形类型 (默认: sine)")
    parser.add_argument("--freq", type=int, default=1000,
                        help="默认信号频率 Hz (默认: 1000)")
    parser.add_argument("--noise", type=float, default=0.02,
                        help="噪声水平，占满量程的比例 (默认: 0.02)")
    parser.add_argument("--sample-rate", type=int, default=20000,
                        help="默认采样率 Hz (默认: 20000)")
    parser.add_argument("--list-commands", action="store_true",
                        help="列出支持的命令并退出")

    args = parser.parse_args()

    if args.list_commands:
        _print_commands()
        return

    # Map waveform name to type
    wave_map = {
        "sine": WAVE_TYPE_SINE,
        "triangle": WAVE_TYPE_TRIANGLE,
        "square": WAVE_TYPE_SQUARE,
        "sawtooth": WAVE_TYPE_SAWTOOTH,
        "dc": WAVE_TYPE_DC,
    }

    # Create and start simulator
    sim = MCUSimulator(args.port, args.baud)
    sim.mcu.wave_type   = wave_map.get(args.wave, WAVE_TYPE_SINE)
    sim.mcu.wave_freq   = args.freq
    sim.mcu.noise_level  = args.noise
    sim.mcu.sample_rate  = args.sample_rate

    sim.start()


def _print_commands():
    """Print all supported protocol commands."""
    print("MCU 模拟器支持的命令 (PC → MCU):")
    print("=" * 50)
    commands = [
        (CMD_START,            "START",             "启动采集"),
        (CMD_STOP,             "STOP",              "停止采集"),
        (CMD_SET_SAMPLE_RATE,  "SET_SAMPLE_RATE",   "设置采样率 (4B, Hz)"),
        (CMD_SET_TRIG_MODE,    "SET_TRIG_MODE",     "设置触发模式 (1B)"),
        (CMD_SET_TRIG_LEVEL,   "SET_TRIG_LEVEL",    "设置触发电平 (2B, 12-bit)"),
        (CMD_SET_TRIG_EDGE,    "SET_TRIG_EDGE",     "设置触发边沿 (1B)"),
        (CMD_SET_WAVE_TYPE,    "SET_WAVE_TYPE",     "设置信号波形 (1B)"),
        (CMD_SET_WAVE_FREQ,    "SET_WAVE_FREQ",     "设置信号频率 (4B, Hz)"),
        (CMD_SET_WAVE_AMP,     "SET_WAVE_AMP",      "设置信号幅度 (2B, 12-bit)"),
        (CMD_GET_STATUS,       "GET_STATUS",        "查询状态"),
        (CMD_GET_INFO,         "GET_INFO",          "查询设备信息"),
        (CMD_SOFT_RESET,       "SOFT_RESET",        "软复位"),
    ]
    for cmd_id, name, desc in commands:
        print(f"  0x{cmd_id:02X}  {name:<20} {desc}")
    print()
    print("触发模式:  0=自动  1=正常  2=单次")
    print("触发边沿:  0=上升沿  1=下降沿  2=双沿")
    print("波形类型:  0=正弦波  1=三角波  2=方波  3=锯齿波  4=直流")

if __name__ == "__main__":
    main()
