"""
serial_worker.py — Serial communication worker thread.

Runs serial I/O in a QThread to avoid blocking the UI.
Emits Qt signals for received data, connection status, and errors.
"""

import serial
import serial.tools.list_ports
from PyQt6.QtCore import QThread, pyqtSignal
from protocol import FrameParser, ParsedFrame, build_command, MAX_FRAME


class SerialWorker(QThread):
    """Background thread for serial port communication."""

    # Signals
    connected    = pyqtSignal(str)        # Port name
    disconnected = pyqtSignal()
    frame_received = pyqtSignal(object)   # ParsedFrame
    error        = pyqtSignal(str)        # Error message
    bytes_sent   = pyqtSignal(int)        # Number of bytes sent
    stats_update = pyqtSignal(int, int)   # bytes_rx, bytes_tx per second

    def __init__(self, parent=None):
        super().__init__(parent)
        self._serial: serial.Serial | None = None
        self._port_name = ""
        self._baud_rate = 921600
        self._running = False
        self._parser = FrameParser()
        # Write queue: list of bytes to send
        self._tx_queue: list[bytes] = []
        self._tx_lock = False  # Simple mutex for tx_queue (GIL is sufficient here)

    # ── Public API ──────────────────────────────────────────

    def open_port(self, port: str, baud: int = 921600):
        """Request connection to serial port."""
        self._port_name = port
        self._baud_rate = baud
        if not self.isRunning():
            self.start()

    def close_port(self):
        """Request disconnection."""
        self._running = False

    def send(self, data: bytes):
        """Queue data for transmission."""
        if data:
            self._tx_queue.append(data)

    def send_command(self, cmd: int, payload: bytes = b""):
        """Queue a command frame for transmission."""
        frame = build_command(cmd, payload)
        self._tx_queue.append(frame)

    # ── QThread run ─────────────────────────────────────────

    def run(self):
        """Main thread loop: open port, then read/write loop."""
        try:
            self._serial = serial.Serial(
                port=self._port_name,
                baudrate=self._baud_rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.001,    # 1ms timeout for faster polling
                write_timeout=0.1,
            )
            # Increase OS serial buffer to prevent overflow at 921600 bps
            try:
                self._serial.set_buffer_size(rx_size=65536, tx_size=65536)
            except Exception:
                pass  # Not all platforms support this
        except Exception as e:
            self.error.emit(f"无法打开串口 {self._port_name}: {e}")
            return

        self._running = True
        self.connected.emit(self._port_name)

        # Stats
        rx_total = 0
        tx_total = 0
        last_stats_time = 0
        frame_count = 0
        last_debug = 0.0

        try:
            import time as _time
            while self._running:
                # ── Read ──
                try:
                    if self._serial.in_waiting > 0:
                        data = self._serial.read(self._serial.in_waiting)
                        rx_total += len(data)
                        frames = self._parser.feed(data)
                        frame_count += len(frames)
                        for frame in frames:
                            self.frame_received.emit(frame)
                        # Debug every 2 seconds
                        now = _time.monotonic()
                        if now - last_debug >= 2.0:
                            print(f"[SERIAL] bytes_rx={rx_total} frames={frame_count} pending={self._serial.in_waiting}")
                            last_debug = now
                except serial.SerialException as e:
                    self.error.emit(f"串口读取错误: {e}")
                    break

                # ── Write ──
                while self._tx_queue:
                    packet = self._tx_queue.pop(0)
                    try:
                        n = self._serial.write(packet)
                        self._serial.flush()
                        tx_total += n
                        self.bytes_sent.emit(n)
                    except serial.SerialException as e:
                        self.error.emit(f"串口写入错误: {e}")
                        self._running = False
                        break

                # ── Stats update (every ~1s) ──
                try:
                    import time
                    now = time.monotonic()
                    if now - last_stats_time >= 1.0:
                        self.stats_update.emit(rx_total, tx_total)
                        rx_total = 0
                        tx_total = 0
                        last_stats_time = now
                except Exception:
                    pass

                # Yield to other threads (10ms sleep implied by timeout)
                self.msleep(5)

        finally:
            try:
                if self._serial and self._serial.is_open:
                    self._serial.close()
            except Exception:
                pass
            self._serial = None
            self.disconnected.emit()

    def stop(self):
        """Stop the worker thread."""
        self._running = False
        self.wait(2000)  # Wait up to 2 seconds for thread to finish

    # ── Static helpers ──────────────────────────────────────

    @staticmethod
    def list_ports() -> list[dict]:
        """List available serial ports. Returns list of {port, description, hwid}.
        Falls back to Windows System.IO.Ports when pyserial list_ports returns empty."""
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append({
                "port": p.device,
                "description": p.description,
                "hwid": p.hwid,
            })

        # Fallback: use Windows System.IO.Ports if pyserial found nothing
        if not ports:
            try:
                import clr
                clr.AddReference("System")
                from System.IO.Ports import SerialPort
                for name in SerialPort.GetPortNames():
                    ports.append({
                        "port": name,
                        "description": "Serial Port",
                        "hwid": "",
                    })
            except Exception:
                pass

        return ports

    @staticmethod
    def list_ports_raw() -> list[str]:
        """Return only port name strings (tries multiple methods)."""
        names = set()
        for p in serial.tools.list_ports.comports():
            names.add(p.device)
        if not names:
            try:
                import subprocess
                result = subprocess.run(
                    ['powershell', '-Command', '[System.IO.Ports.SerialPort]::GetPortNames()'],
                    capture_output=True, text=True, timeout=5)
                for line in result.stdout.strip().split('\n'):
                    port = line.strip()
                    if port:
                        names.add(port)
            except Exception:
                pass
        return sorted(names)
