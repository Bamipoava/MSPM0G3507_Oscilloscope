"""
wave_display.py — Real-time waveform display widget using pyqtgraph.
"""

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt
from collections import deque


class WaveDisplay(QWidget):
    """Real-time oscilloscope waveform display."""

    # Default display parameters
    DEFAULT_TIME_DIV   = 1e-3    # 1 ms/div
    DEFAULT_VOLT_DIV   = 1.0     # 1 V/div
    NUM_DIVS_H          = 10      # Horizontal divisions
    NUM_DIVS_V          = 8       # Vertical divisions
    MAX_SAMPLES         = 10000   # Max samples in display buffer
    ADC_MAX             = 4095    # 12-bit ADC
    VREF                = 3.3     # Reference voltage

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._setup_plot()

        # Display buffer (ring buffer)
        self._buffer: deque = deque(maxlen=self.MAX_SAMPLES)
        self._x_data = np.zeros(self.MAX_SAMPLES, dtype=np.float64)
        self._y_data = np.zeros(self.MAX_SAMPLES, dtype=np.float64)

        # Display settings
        self._time_div  = self.DEFAULT_TIME_DIV   # s/div
        self._volt_div  = self.DEFAULT_VOLT_DIV    # V/div
        self._volt_offset = 0.0                    # Vertical offset (V)
        self._sample_rate = 20000                  # Hz (updated from MCU)

        # Cached curve
        self._curve = None

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # pyqtgraph PlotWidget
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground('#1a1a2e')
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        layout.addWidget(self._plot_widget)

    def _setup_plot(self):
        """Configure plot axes and appearance."""
        plot = self._plot_widget.getPlotItem()

        # X axis (time)
        plot.setLabel('bottom', '时间', units='s')
        plot.getAxis('bottom').setTickSpacing(
            major=self.DEFAULT_TIME_DIV,
            minor=self.DEFAULT_TIME_DIV / 5
        )

        # Y axis (voltage)
        plot.setLabel('left', '电压', units='V')
        plot.getAxis('left').setTickSpacing(
            major=self.DEFAULT_VOLT_DIV,
            minor=self.DEFAULT_VOLT_DIV / 5
        )

        # Range
        plot.setXRange(-self.NUM_DIVS_H * self.DEFAULT_TIME_DIV, 0, padding=0)
        plot.setYRange(-self.DEFAULT_VOLT_DIV, self.VREF + self.DEFAULT_VOLT_DIV, padding=0)

        # ViewBox: disable mouse (simplified interaction)
        plot.vb.setMouseEnabled(x=True, y=True)

        # Trigger level line
        self._trig_line = pg.InfiniteLine(
            pos=1.65, angle=0, pen=pg.mkPen('#ff6600', width=1, style=Qt.PenStyle.DashLine))
        plot.addItem(self._trig_line)

        # Grid styling
        plot.getAxis('bottom').setPen(pg.mkPen('#555555'))
        plot.getAxis('left').setPen(pg.mkPen('#555555'))

    # ── Data input ──────────────────────────────────────────

    def append_samples(self, samples: list, sample_rate: int = 0):
        """
        Append ADC sample values (0-4095) to the display buffer.
        Converts to voltage using VREF/ADC_MAX.
        """
        if sample_rate > 0:
            self._sample_rate = sample_rate

        for raw in samples:
            voltage = (raw / self.ADC_MAX) * self.VREF
            self._buffer.append(voltage)

    def clear(self):
        """Clear all waveform data."""
        self._buffer.clear()
        self._plot_widget.getPlotItem().clear()

    # ── Display update ──────────────────────────────────────

    def update_display(self):
        """Render current buffer to plot. Call at ~30-60 Hz from timer."""
        if not self._buffer:
            return

        n = len(self._buffer)
        if n == 0:
            return

        # Build x/y arrays
        # x: time axis going backwards from 0 (trigger point)
        dt = 1.0 / self._sample_rate if self._sample_rate > 0 else 50e-6
        y_arr = np.array(list(self._buffer), dtype=np.float64)
        x_arr = np.arange(-(n - 1) * dt, dt, dt, dtype=np.float64)  # -T...0

        # Ensure lengths match
        if len(x_arr) != len(y_arr):
            x_arr = np.linspace(-(n - 1) * dt, 0, n, dtype=np.float64)

        plot = self._plot_widget.getPlotItem()

        # Update or create curve
        if self._curve is None:
            self._curve = plot.plot(
                x_arr, y_arr,
                pen=pg.mkPen('#00ff88', width=1.5),
                antialias=True,
                name='CH1'
            )
        else:
            self._curve.setData(x_arr, y_arr)

        # Auto-range X axis to show last N divs
        window_width = self.NUM_DIVS_H * self._time_div
        x_min = -window_width
        x_max = 0

        if self._sample_rate > 0:
            plot.setXRange(x_min, x_max, padding=0)
        else:
            plot.autoRange()

    # ── Settings ────────────────────────────────────────────

    def set_time_div(self, seconds_per_div: float):
        """Set horizontal scale (seconds per division)."""
        self._time_div = max(1e-6, min(1.0, seconds_per_div))
        plot = self._plot_widget.getPlotItem()
        plot.getAxis('bottom').setTickSpacing(
            major=self._time_div,
            minor=self._time_div / 5
        )

    def set_volt_div(self, volts_per_div: float):
        """Set vertical scale (volts per division)."""
        self._volt_div = max(0.01, min(5.0, volts_per_div))
        plot = self._plot_widget.getPlotItem()
        plot.getAxis('left').setTickSpacing(
            major=self._volt_div,
            minor=self._volt_div / 5
        )

    def set_trigger_level(self, voltage: float):
        """Move the trigger level indicator line."""
        self._trig_line.setPos(voltage)

    def set_sample_rate(self, rate_hz: int):
        """Update the assumed sample rate for time axis calculation."""
        self._sample_rate = rate_hz

    def time_div(self) -> float:
        return self._time_div

    def volt_div(self) -> float:
        return self._volt_div

    def sample_rate(self) -> int:
        return self._sample_rate
