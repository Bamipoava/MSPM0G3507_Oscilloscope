#!/usr/bin/env python3
"""
main.py — MSPM0G3507 简易示波器 & 信号源 上位机软件

Usage:
    python main.py                          # Launch GUI
    python main.py --demo                   # Built-in demo mode (no hardware/serial needed)
    python main.py --simulator-port COM3    # Connect to MCU simulator at COM3
    python main.py --list-ports             # List available serial ports
"""

import sys
import os
import time
import struct
import math
import random
import argparse
from collections import deque

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QMenuBar, QMenu, QToolBar, QStatusBar, QLabel, QComboBox,
    QPushButton, QMessageBox, QDialog, QFormLayout, QDialogButtonBox,
    QLineEdit, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QFont

from serial_worker import SerialWorker
from wave_display import WaveDisplay
from control_panel import ControlPanel
from protocol import (
    ParsedFrame, FrameParser,
    build_start, build_stop,
    build_set_sample_rate, build_set_trig_mode, build_set_trig_level,
    build_set_trig_edge, build_set_wave_type, build_set_wave_freq,
    build_set_wave_amp, build_get_status, build_get_info, build_soft_reset,
    RESP_DATA, RESP_ACK, RESP_NAK,
)


# ================================================================
# DemoWorker — built-in MCU simulator (QThread, no serial needed)
# ================================================================

# Re-export protocol constants needed by the demo simulator
from protocol import (
    SYNC_MCU_TO_PC, SYNC_PC_TO_MCU, crc16, build_stuffed_response,
    CMD_START, CMD_STOP,
    CMD_SET_SAMPLE_RATE, CMD_SET_TRIG_MODE, CMD_SET_TRIG_LEVEL,
    CMD_SET_TRIG_EDGE, CMD_SET_WAVE_TYPE, CMD_SET_WAVE_FREQ,
    CMD_SET_WAVE_AMP, CMD_GET_STATUS, CMD_GET_INFO, CMD_SOFT_RESET,
    TRIG_MODE_AUTO, TRIG_MODE_NORMAL, TRIG_MODE_SINGLE,
    TRIG_EDGE_RISING, TRIG_EDGE_FALLING, TRIG_EDGE_BOTH,
    WAVE_TYPE_SINE, WAVE_TYPE_TRIANGLE, WAVE_TYPE_SQUARE,
    WAVE_TYPE_SAWTOOTH, WAVE_TYPE_DC,
)


class DemoSimMCU:
    """Lightweight in-memory MCU simulator (no serial I/O)."""

    VREF, ADC_MAX, DAC_MAX = 3.3, 4095, 4095

    def __init__(self):
        self.running = False
        self.sample_rate = 20000
        self.seq_number = 0
        self.buffer_size = 256
        self.trig_mode = TRIG_MODE_AUTO
        self.trig_level = 2048
        self.trig_edge = TRIG_EDGE_RISING
        self.wave_type = WAVE_TYPE_SINE
        self.wave_freq = 1000
        self.wave_amp = 2047
        self.noise_level = 0.02
        self._sim_time = 0.0

    def reset(self):
        self.__init__()

    def generate_samples(self, n: int) -> list:
        samples = []
        dt = 1.0 / self.sample_rate
        for i in range(n):
            t = self._sim_time + i * dt
            phase = (t * self.wave_freq) % 1.0
            center = self.ADC_MAX / 2
            amp = self.wave_amp
            if self.wave_type == WAVE_TYPE_SINE:
                sig = center + amp * math.sin(2 * math.pi * phase)
            elif self.wave_type == WAVE_TYPE_TRIANGLE:
                sig = center + amp * (4*phase-1) if phase < 0.5 else center + amp * (3-4*phase)
            elif self.wave_type == WAVE_TYPE_SQUARE:
                sig = center + (amp if phase < 0.5 else -amp)
            elif self.wave_type == WAVE_TYPE_SAWTOOTH:
                sig = center + amp * (2*phase-1)
            else:
                sig = center
            noise = random.gauss(0, self.noise_level * self.ADC_MAX)
            samples.append(max(0, min(self.ADC_MAX, int(sig + noise))))
        self._sim_time += n * dt
        return samples

    def build_data_frame(self, samples: list) -> bytes:
        n = len(samples)
        payload = struct.pack("<HH", self.seq_number, n)
        for val in samples:
            payload += struct.pack("<H", val & 0xFFFF)
        self.seq_number = (self.seq_number + 1) & 0xFFFF
        return build_stuffed_response(SYNC_MCU_TO_PC, RESP_DATA, payload)

    def build_ack(self, cmd: int, payload: bytes = b"") -> bytes:
        return build_stuffed_response(SYNC_MCU_TO_PC, RESP_ACK | cmd, payload)

    def build_nak(self, err=1) -> bytes:
        return build_stuffed_response(SYNC_MCU_TO_PC, RESP_NAK, bytes([err]))

    def handle_command(self, cmd: int, payload: bytes) -> bytes:
        if cmd == CMD_START:
            self.running = True; self._sim_time = 0.0
            return self.build_ack(CMD_START)
        elif cmd == CMD_STOP:
            self.running = False
            return self.build_ack(CMD_STOP)
        elif cmd == CMD_SET_SAMPLE_RATE and len(payload) >= 4:
            self.sample_rate = max(1000, min(100000, struct.unpack("<I", payload[:4])[0]))
            return self.build_ack(CMD_SET_SAMPLE_RATE)
        elif cmd == CMD_SET_TRIG_MODE and len(payload) >= 1:
            self.trig_mode = payload[0]
            return self.build_ack(CMD_SET_TRIG_MODE)
        elif cmd == CMD_SET_TRIG_LEVEL and len(payload) >= 2:
            self.trig_level = struct.unpack("<H", payload[:2])[0]
            return self.build_ack(CMD_SET_TRIG_LEVEL)
        elif cmd == CMD_SET_TRIG_EDGE and len(payload) >= 1:
            self.trig_edge = payload[0]
            return self.build_ack(CMD_SET_TRIG_EDGE)
        elif cmd == CMD_SET_WAVE_TYPE and len(payload) >= 1:
            self.wave_type = payload[0]
            return self.build_ack(CMD_SET_WAVE_TYPE)
        elif cmd == CMD_SET_WAVE_FREQ and len(payload) >= 4:
            self.wave_freq = max(10, min(10000, struct.unpack("<I", payload[:4])[0]))
            return self.build_ack(CMD_SET_WAVE_FREQ)
        elif cmd == CMD_SET_WAVE_AMP and len(payload) >= 2:
            self.wave_amp = struct.unpack("<H", payload[:2])[0]
            return self.build_ack(CMD_SET_WAVE_AMP)
        elif cmd == CMD_GET_STATUS:
            p = struct.pack("<B I B H B B I H",
                self.running, self.sample_rate, self.trig_mode,
                self.trig_level, self.trig_edge, self.wave_type,
                self.wave_freq, self.wave_amp)
            return self.build_ack(CMD_GET_STATUS, p)
        elif cmd == CMD_GET_INFO:
            nb = b"MSPM0G3507(DEMO)".ljust(16, b'\x00')[:16]
            p = struct.pack("<16s I I B B I", nb, 0x00010000, 100000, 12, 12, 80000000)
            return self.build_ack(CMD_GET_INFO, p)
        elif cmd == CMD_SOFT_RESET:
            self.reset()
            return self.build_ack(CMD_SOFT_RESET)
        else:
            return self.build_nak(1)


class DemoWorker(QThread):
    """QThread wrapper: runs DemoSimMCU and emits the same signals as SerialWorker."""

    connected = pyqtSignal(str)
    disconnected = pyqtSignal()
    frame_received = pyqtSignal(object)
    error = pyqtSignal(str)
    bytes_sent = pyqtSignal(int)
    stats_update = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mcu = DemoSimMCU()
        self._parser = FrameParser()
        self._running = False
        self._tx_queue: list[bytes] = []

    def send(self, data: bytes):
        if data:
            self._tx_queue.append(data)

    def send_command(self, cmd: int, payload: bytes = b""):
        """Queue a command via build_command (uses stuffed frames)."""
        from protocol import build_command as _bc
        self._tx_queue.append(_bc(cmd, payload))

    def close_port(self):
        self._running = False

    def stop(self):
        self._running = False
        self.wait(2000)

    def run(self):
        self._running = True
        self.connected.emit("DEMO")
        rx_total = 0
        tx_total = 0
        last_stats = time.monotonic()
        last_data_time = 0.0
        # Buffer for parsing PC→MCU frames (look for 0x55 sync)
        cmd_buf = bytearray()

        while self._running:
            # ── Process TX queue (PC → MCU commands) ──
            while self._tx_queue:
                raw = self._tx_queue.pop(0)
                tx_total += len(raw)
                # Parse PC→MCU frame (sync=0x55): skip sync, read cmd+len, unstuff payload
                if len(raw) >= 4 and raw[0] == SYNC_PC_TO_MCU:
                    cmd = raw[1]
                    plen = raw[2] | (raw[3] << 8)
                    # unstuff payload+crc from bytes after header
                    from protocol import unstuff_into
                    unstuffed, _ = unstuff_into(raw[4:], 0, plen + 2)
                    payload = unstuffed[:plen]
                    resp = self.mcu.handle_command(cmd, payload)
                    if resp:
                        resp_frames = self._parser.feed(resp)
                        for rf in resp_frames:
                            self.frame_received.emit(rf)

            # ── Generate data when acquisition is running ──
            if self.mcu.running:
                now = time.monotonic()
                dt_per_buf = self.mcu.buffer_size / self.mcu.sample_rate
                if now - last_data_time >= dt_per_buf * 0.95:
                    samples = self.mcu.generate_samples(self.mcu.buffer_size)
                    frame = self.mcu.build_data_frame(samples)
                    resp_frames = self._parser.feed(frame)
                    for rf in resp_frames:
                        self.frame_received.emit(rf)
                        rx_total += len(frame)
                    last_data_time = now

            # ── Stats ──
            now = time.monotonic()
            if now - last_stats >= 1.0:
                self.stats_update.emit(rx_total, tx_total)
                rx_total = 0; tx_total = 0
                last_stats = now

            self.msleep(5)

        self.disconnected.emit()


class MainWindow(QMainWindow):
    """Main application window."""

    TITLE = "MSPM0G3507 简易示波器 & 信号源"

    def __init__(self, simulator_port: str = None, demo_mode: bool = False):
        super().__init__()
        title = self.TITLE + (" [演示模式]" if demo_mode else "")
        self.setWindowTitle(title)
        self.resize(1200, 700)

        # State
        self._demo_mode = demo_mode
        self._serial_worker = DemoWorker() if demo_mode else SerialWorker()
        self._connected = False
        self._acquisition_running = False
        self._sample_rate = 20000

        # FPS tracking
        self._frame_count = 0
        self._fps_timer_start = time.monotonic()
        self._current_fps = 0.0

        # Pending commands (for request-response tracking)
        self._pending_info_request = False

        self._setup_ui()
        self._setup_connections()
        self._setup_timers()

        # Auto-connect if demo mode or simulator port specified
        if demo_mode:
            self._connect_demo()
        elif simulator_port:
            self._connect_to_port(simulator_port)

    # ── UI Setup ───────────────────────────────────────────

    def _setup_ui(self):
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Waveform display (left, takes most space)
        self._wave_display = WaveDisplay()
        main_layout.addWidget(self._wave_display, stretch=4)

        # Control panel (right sidebar)
        self._control_panel = ControlPanel()
        main_layout.addWidget(self._control_panel, stretch=1)

        # ── Menu bar ──
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("文件(&F)")
        act_save = QAction("保存波形数据(&S)...", self)
        act_save.triggered.connect(self._save_waveform_data)
        file_menu.addAction(act_save)
        file_menu.addSeparator()
        act_exit = QAction("退出(&X)", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # Port menu
        port_menu = menubar.addMenu("串口(&P)")
        act_connect = QAction("连接(&C)...", self)
        act_connect.triggered.connect(self._show_connect_dialog)
        port_menu.addAction(act_connect)
        act_disconnect = QAction("断开(&D)", self)
        act_disconnect.triggered.connect(self._disconnect)
        port_menu.addAction(act_disconnect)
        port_menu.addSeparator()
        act_refresh = QAction("刷新端口列表(&R)", self)
        act_refresh.triggered.connect(self._refresh_ports)
        port_menu.addAction(act_refresh)

        # Help menu
        help_menu = menubar.addMenu("帮助(&H)")
        act_about = QAction("关于(&A)", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

        # ── Toolbar ──
        toolbar = self.addToolBar("主工具栏")
        toolbar.setMovable(False)

        toolbar.addWidget(QLabel(" 串口: "))
        self._cmb_ports = QComboBox()
        self._cmb_ports.setMinimumWidth(120)
        self._cmb_ports.setEditable(True)   # Allow manual port name entry
        self._cmb_ports.setToolTip("选择串口或直接输入端口名 (如 COM10)")
        toolbar.addWidget(self._cmb_ports)

        toolbar.addWidget(QLabel(" 波特率: "))
        self._cmb_baud = QComboBox()
        self._cmb_baud.addItems(["921600", "460800", "230400", "115200"])
        self._cmb_baud.setCurrentText("921600")
        self._cmb_baud.setToolTip("选择波特率")
        toolbar.addWidget(self._cmb_baud)

        self._btn_connect = QPushButton("连接")
        self._btn_connect.setStyleSheet(
            "QPushButton { background-color: #1b4332; color: white; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #2d6a4f; }"
        )
        self._btn_connect.clicked.connect(self._toggle_connection)
        toolbar.addWidget(self._btn_connect)

        toolbar.addSeparator()

        self._lbl_rx_bytes = QLabel(" RX: 0 B/s ")
        toolbar.addWidget(self._lbl_rx_bytes)
        self._lbl_tx_bytes = QLabel(" TX: 0 B/s ")
        toolbar.addWidget(self._lbl_tx_bytes)

        # ── Status bar ──
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("就绪 — 请连接串口")

        # Refresh port list
        self._refresh_ports()

    # ── Signal/Slot Connections ────────────────────────────

    def _setup_connections(self):
        # Serial worker signals
        self._serial_worker.connected.connect(self._on_connected)
        self._serial_worker.disconnected.connect(self._on_disconnected)
        self._serial_worker.frame_received.connect(self._on_frame_received)
        self._serial_worker.error.connect(self._on_serial_error)
        self._serial_worker.stats_update.connect(self._on_stats_update)

        # Control panel signals
        self._control_panel.sig_start.connect(self._start_acquisition)
        self._control_panel.sig_stop.connect(self._stop_acquisition)
        self._control_panel.sig_sample_rate.connect(self._set_sample_rate)
        self._control_panel.sig_trig_mode.connect(self._set_trig_mode)
        self._control_panel.sig_trig_level.connect(self._set_trig_level)
        self._control_panel.sig_trig_edge.connect(self._set_trig_edge)
        self._control_panel.sig_wave_type.connect(self._set_wave_type)
        self._control_panel.sig_wave_freq.connect(self._set_wave_freq)
        self._control_panel.sig_wave_amp.connect(self._set_wave_amp)
        self._control_panel.sig_soft_reset.connect(self._soft_reset)
        self._control_panel.sig_time_div.connect(self._wave_display.set_time_div)
        self._control_panel.sig_volt_div.connect(self._wave_display.set_volt_div)

    def _setup_timers(self):
        # Display update timer (~30 Hz)
        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self._update_display)
        self._display_timer.start(33)  # ~30 fps

        # Status poll timer (every 2 seconds)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._poll_status)
        self._status_timer.start(2000)

    # ── Serial connection ──────────────────────────────────

    def _refresh_ports(self):
        self._cmb_ports.clear()
        ports = SerialWorker.list_ports()
        seen = set()
        for p in ports:
            label = f"{p['port']} - {p['description'][:30]}"
            self._cmb_ports.addItem(label, p['port'])
            seen.add(p['port'])
        # Fallback: add raw port names not found by pyserial
        for name in SerialWorker.list_ports_raw():
            if name not in seen:
                self._cmb_ports.addItem(f"{name} - Serial Port", name)
                seen.add(name)
        # If still empty, allow manual entry hint
        if self._cmb_ports.count() == 0:
            self._cmb_ports.addItem("(手动输入端口名...)", "")

    def _toggle_connection(self):
        if self._connected:
            self._disconnect()
        elif self._demo_mode:
            self._connect_demo()
        else:
            # Try currentData first, fall back to currentText (manual entry)
            port = self._cmb_ports.currentData()
            if not port:
                port = self._cmb_ports.currentText().strip()
                # Strip description suffix if user selected from dropdown
                if ' - ' in port:
                    port = port.split(' - ')[0]
            if port:
                self._connect_to_port(port)
            else:
                self._status_bar.showMessage("未选择串口 — 请手动输入端口名 (如 COM10)")

    def _connect_demo(self):
        """Start built-in demo simulator."""
        self._status_bar.showMessage("正在启动演示模式...")
        self._serial_worker.start()

    def _connect_to_port(self, port: str):
        baud = int(self._cmb_baud.currentText())
        self._status_bar.showMessage(f"正在连接 {port} @ {baud}...")
        self._serial_worker.open_port(port, baud)

    def _disconnect(self):
        self._serial_worker.close_port()

    def _show_connect_dialog(self):
        """Show manual port entry dialog."""
        dlg = QDialog(self)
        dlg.setWindowTitle("连接串口")
        layout = QFormLayout(dlg)

        cmb = QComboBox()
        ports = SerialWorker.list_ports()
        for p in ports:
            cmb.addItem(f"{p['port']} - {p['description'][:40]}", p['port'])
        if self._cmb_ports.currentData():
            cmb.setCurrentText(self._cmb_ports.currentText())
        layout.addRow("串口:", cmb)

        baud_edit = QComboBox()
        baud_edit.addItems(["921600", "460800", "230400", "115200"])
        baud_edit.setCurrentText(self._cmb_baud.currentText())
        layout.addRow("波特率:", baud_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            port = cmb.currentData()
            if port:
                self._cmb_ports.setCurrentText(cmb.currentText())
                self._cmb_baud.setCurrentText(baud_edit.currentText())
                self._connect_to_port(port)

    # ── Connection callbacks ───────────────────────────────

    def _on_connected(self, port: str):
        self._connected = True
        self._btn_connect.setText("断开")
        self._btn_connect.setStyleSheet(
            "QPushButton { background-color: #9b2226; color: white; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #ae2012; }"
        )
        self._control_panel.set_connected(port, int(self._cmb_baud.currentText()))
        self._status_bar.showMessage(f"已连接 {port}")

        # Request device info
        self._pending_info_request = True
        self._serial_worker.send(build_get_info())

    def _on_disconnected(self):
        self._connected = False
        self._btn_connect.setText("连接")
        self._btn_connect.setStyleSheet(
            "QPushButton { background-color: #1b4332; color: white; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #2d6a4f; }"
        )
        self._control_panel.set_disconnected()
        self._status_bar.showMessage("已断开")

    def _on_serial_error(self, msg: str):
        self._status_bar.showMessage(f"错误: {msg}")
        QMessageBox.warning(self, "串口错误", msg)

    def _on_stats_update(self, rx: int, tx: int):
        """Update RX/TX rate display."""
        self._lbl_rx_bytes.setText(f" RX: {rx} B/s ")
        self._lbl_tx_bytes.setText(f" TX: {tx} B/s ")

    # ── Frame received handler ─────────────────────────────

    def _on_frame_received(self, frame: ParsedFrame):
        """Handle a complete frame received from MCU."""
        if not frame.crc_ok:
            self._status_bar.showMessage("⚠ CRC 校验失败")
            return

        if frame.is_data():
            # ADC data frame
            try:
                seq, n, samples = frame.parse_data_payload()
                self._wave_display.append_samples(samples, self._sample_rate)
                self._frame_count += 1
                # Debug: print once per 10 frames
                if self._frame_count % 10 == 1:
                    bufsize = len(self._wave_display._buffer)
                    print(f"[DATA] seq={seq} n={n} samples[0]={samples[0]} buf={bufsize} fps={self._current_fps:.0f}")
            except ValueError as e:
                print(f"[DATA ERR] {e}")

        elif frame.is_ack():
            ack_cmd = frame.ack_cmd()
            # Could handle specific ACKs here
            if ack_cmd == 0x0B and self._pending_info_request:
                # Device info response
                try:
                    info = frame.parse_info_payload()
                    if info:
                        self._control_panel.set_device_info(info)
                except Exception:
                    pass
                self._pending_info_request = False

        elif frame.is_nak():
            nak_code = frame.payload[0] if frame.payload else 0
            self._status_bar.showMessage(f"MCU 返回错误 (code={nak_code})")

    # ── Display update timer ───────────────────────────────

    def _update_display(self):
        """Called at ~30 Hz to refresh waveform display and FPS counter."""
        buf_len = len(self._wave_display._buffer)
        self._wave_display.update_display()

        # FPS calculation
        now = time.monotonic()
        elapsed = now - self._fps_timer_start
        if elapsed >= 1.0:
            self._current_fps = self._frame_count / elapsed
            print(f"[DISP] buf={buf_len} fps={self._current_fps:.0f} curve={self._wave_display._curve is not None}")
            self._frame_count = 0
            self._fps_timer_start = now
            self._control_panel.set_fps(self._current_fps)

    # ── Status polling ─────────────────────────────────────

    def _poll_status(self):
        """Periodically poll MCU status."""
        if self._connected:
            self._serial_worker.send(build_get_status())

    # ── Command handlers ───────────────────────────────────

    def _start_acquisition(self):
        if not self._connected:
            self._status_bar.showMessage("请先连接串口")
            return
        self._acquisition_running = True
        self._serial_worker.send(build_start())
        self._status_bar.showMessage("▶ 开始采集")

    def _stop_acquisition(self):
        if not self._connected:
            return
        self._acquisition_running = False
        self._serial_worker.send(build_stop())
        self._status_bar.showMessage("⏹ 停止采集")

    def _set_sample_rate(self, rate: int):
        self._sample_rate = rate
        self._wave_display.set_sample_rate(rate)
        if self._connected:
            self._serial_worker.send(build_set_sample_rate(rate))

    def _set_trig_mode(self, mode: int):
        if self._connected:
            self._serial_worker.send(build_set_trig_mode(mode))

    def _set_trig_level(self, level: int):
        voltage = level / 4095.0 * 3.3
        self._wave_display.set_trigger_level(voltage)
        if self._connected:
            self._serial_worker.send(build_set_trig_level(level))

    def _set_trig_edge(self, edge: int):
        if self._connected:
            self._serial_worker.send(build_set_trig_edge(edge))

    def _set_wave_type(self, wave_type: int):
        if self._connected:
            self._serial_worker.send(build_set_wave_type(wave_type))

    def _set_wave_freq(self, freq: int):
        if self._connected:
            self._serial_worker.send(build_set_wave_freq(freq))

    def _set_wave_amp(self, amp: int):
        if self._connected:
            self._serial_worker.send(build_set_wave_amp(amp))

    def _soft_reset(self):
        if self._connected:
            self._serial_worker.send(build_soft_reset())
            self._status_bar.showMessage("🔄 软复位已发送")
        self._wave_display.clear()
        self._acquisition_running = False

    # ── File operations ────────────────────────────────────

    def _save_waveform_data(self):
        """Save current waveform data to CSV file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "保存波形数据", "waveform.csv",
            "CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not path:
            return

        # Access buffer data from wave display
        try:
            buf = self._wave_display._buffer
            if not buf:
                QMessageBox.information(self, "提示", "没有波形数据可保存")
                return

            dt = 1.0 / self._sample_rate if self._sample_rate > 0 else 50e-6
            with open(path, 'w', encoding='utf-8') as f:
                f.write("Time (s),Voltage (V)\n")
                for i, voltage in enumerate(buf):
                    t = i * dt
                    f.write(f"{t:.9f},{voltage:.6f}\n")

            self._status_bar.showMessage(f"波形数据已保存到 {path}")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    # ── About dialog ───────────────────────────────────────

    def _show_about(self):
        QMessageBox.about(
            self, "关于",
            f"<h3>{self.TITLE}</h3>"
            "<p>MSPM0G3507 地猛星开发板<br>"
            "简易信号源与示波器上位机软件</p>"
            "<p>版本: 1.0.0</p>"
            "<p>技术栈: Python + PyQt6 + pyqtgraph + pyserial</p>"
            "<p>协议: 自定义二进制帧协议 (CRC-16-CCITT)</p>"
        )

    # ── Cleanup ────────────────────────────────────────────

    def closeEvent(self, event):
        """Clean shutdown."""
        if self._connected:
            if self._acquisition_running:
                self._serial_worker.send(build_stop())
            self._serial_worker.close_port()
        self._serial_worker.stop()
        event.accept()


# ================================================================
# Entry point
# ================================================================

def main():
    parser = argparse.ArgumentParser(description="MSPM0G3507 简易示波器上位机")
    parser.add_argument("--demo", action="store_true",
                        help="内置演示模式 (无需硬件和串口)")
    parser.add_argument("--simulator-port", type=str, default=None,
                        help="自动连接到 MCU 模拟器端口")
    parser.add_argument("--list-ports", action="store_true",
                        help="列出可用串口并退出")
    args = parser.parse_args()

    if args.list_ports:
        ports = SerialWorker.list_ports()
        if ports:
            print("可用串口:")
            for p in ports:
                print(f"  {p['port']:<10} {p['description']:<40} [{p['hwid']}]")
        else:
            print("未找到可用串口")
        return

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Dark theme stylesheet
    app.setStyleSheet("""
        QMainWindow { background-color: #1a1a2e; }
        QWidget { color: #e0e0e0; font-size: 13px; }
        QGroupBox {
            border: 1px solid #333355; border-radius: 6px;
            margin-top: 0.5em; padding-top: 0.5em;
            font-weight: bold; color: #aaaacc;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px; padding: 0 5px;
        }
        QComboBox {
            background-color: #16213e; border: 1px solid #333355;
            padding: 4px; border-radius: 3px;
        }
        QComboBox::drop-down { border: none; }
        QComboBox QAbstractItemView {
            background-color: #16213e; selection-background-color: #0f3460;
        }
        QSlider::groove:horizontal {
            border: 1px solid #333355; height: 6px;
            background: #16213e; border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #e94560; width: 14px; margin: -4px 0;
            border-radius: 7px;
        }
        QSpinBox, QDoubleSpinBox {
            background-color: #16213e; border: 1px solid #333355;
            padding: 4px; border-radius: 3px;
        }
        QPushButton {
            background-color: #16213e; border: 1px solid #333355;
            padding: 6px 12px; border-radius: 4px;
        }
        QPushButton:hover { background-color: #1a1a4e; }
        QToolBar {
            background-color: #0f0f23; border-bottom: 1px solid #333355;
            spacing: 6px; padding: 4px;
        }
        QStatusBar { background-color: #0f0f23; color: #888888; }
        QMenuBar { background-color: #0f0f23; }
        QMenuBar::item:selected { background-color: #16213e; }
        QMenu { background-color: #16213e; border: 1px solid #333355; }
        QMenu::item:selected { background-color: #0f3460; }
        QLabel { color: #cccccc; }
    """)

    window = MainWindow(simulator_port=args.simulator_port, demo_mode=args.demo)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
