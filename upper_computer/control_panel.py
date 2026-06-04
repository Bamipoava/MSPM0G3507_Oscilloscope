"""
control_panel.py — Control panel widget for oscilloscope settings.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QComboBox, QSlider, QPushButton, QDoubleSpinBox, QSpinBox,
    QCheckBox, QFrame, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from protocol import (
    TRIG_MODE_AUTO, TRIG_MODE_NORMAL, TRIG_MODE_SINGLE,
    TRIG_EDGE_RISING, TRIG_EDGE_FALLING, TRIG_EDGE_BOTH,
    WAVE_TYPE_SINE, WAVE_TYPE_TRIANGLE, WAVE_TYPE_SQUARE, WAVE_TYPE_SAWTOOTH,
    WAVE_TYPE_NAMES,
)


class ControlPanel(QWidget):
    """Side panel with all oscilloscope and signal source controls."""

    # Signals emitted when user changes a setting
    sig_start         = pyqtSignal()
    sig_stop          = pyqtSignal()
    sig_sample_rate   = pyqtSignal(int)       # Hz
    sig_trig_mode     = pyqtSignal(int)       # TRIG_MODE_*
    sig_trig_level    = pyqtSignal(int)       # 0-4095
    sig_trig_edge     = pyqtSignal(int)       # TRIG_EDGE_*
    sig_wave_type     = pyqtSignal(int)       # WAVE_TYPE_*
    sig_wave_freq     = pyqtSignal(int)       # Hz
    sig_wave_amp      = pyqtSignal(int)       # 0-4095
    sig_soft_reset    = pyqtSignal()
    sig_time_div      = pyqtSignal(float)     # s/div
    sig_volt_div      = pyqtSignal(float)     # V/div

    VREF = 3.3
    ADC_MAX = 4095

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.setMaximumWidth(320)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Run Control ──
        self._add_run_control(layout)

        # ── Horizontal (Timebase) ──
        self._add_timebase_control(layout)

        # ── Vertical (Voltage) ──
        self._add_voltage_control(layout)

        # ── Trigger ──
        self._add_trigger_control(layout)

        # ── Signal Source ──
        self._add_signal_source_control(layout)

        # ── Status ──
        self._add_status_group(layout)

        layout.addStretch()

    # ── Run Control ────────────────────────────────────────

    def _add_run_control(self, parent_layout):
        grp = QGroupBox("运行控制")
        lay = QHBoxLayout()

        self._btn_start = QPushButton("▶ 开始")
        self._btn_start.setStyleSheet(
            "QPushButton { background-color: #2d6a4f; color: white; padding: 8px; "
            "font-size: 14px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #40916c; }"
        )
        self._btn_start.clicked.connect(self.sig_start.emit)

        self._btn_stop = QPushButton("⏹ 停止")
        self._btn_stop.setStyleSheet(
            "QPushButton { background-color: #9b2226; color: white; padding: 8px; "
            "font-size: 14px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #ae2012; }"
        )
        self._btn_stop.clicked.connect(self.sig_stop.emit)

        lay.addWidget(self._btn_start)
        lay.addWidget(self._btn_stop)
        grp.setLayout(lay)
        parent_layout.addWidget(grp)

    # ── Timebase ───────────────────────────────────────────

    def _add_timebase_control(self, parent_layout):
        grp = QGroupBox("时基 (水平)")
        lay = QGridLayout()

        lay.addWidget(QLabel("时基刻度:"), 0, 0)
        self._cmb_time_div = QComboBox()
        time_divs = [
            ("1 μs/div", 1e-6), ("5 μs/div", 5e-6), ("10 μs/div", 10e-6),
            ("50 μs/div", 50e-6), ("100 μs/div", 100e-6),
            ("500 μs/div", 500e-6),
            ("1 ms/div", 1e-3), ("5 ms/div", 5e-3), ("10 ms/div", 10e-3),
            ("50 ms/div", 50e-3), ("100 ms/div", 100e-3),
            ("500 ms/div", 500e-3), ("1 s/div", 1.0),
        ]
        for label, val in time_divs:
            self._cmb_time_div.addItem(label, val)
        self._cmb_time_div.setCurrentIndex(6)  # 1 ms/div
        self._cmb_time_div.currentIndexChanged.connect(self._on_time_div_changed)
        lay.addWidget(self._cmb_time_div, 0, 1)

        lay.addWidget(QLabel("采样率 (Hz):"), 1, 0)
        self._spn_sample_rate = QSpinBox()
        self._spn_sample_rate.setRange(1000, 100000)
        self._spn_sample_rate.setValue(20000)
        self._spn_sample_rate.setSingleStep(1000)
        self._spn_sample_rate.setSuffix(" Hz")
        self._spn_sample_rate.valueChanged.connect(self.sig_sample_rate.emit)
        lay.addWidget(self._spn_sample_rate, 1, 1)

        grp.setLayout(lay)
        parent_layout.addWidget(grp)

    # ── Voltage ────────────────────────────────────────────

    def _add_voltage_control(self, parent_layout):
        grp = QGroupBox("电压 (垂直)")
        lay = QGridLayout()

        lay.addWidget(QLabel("电压刻度:"), 0, 0)
        self._cmb_volt_div = QComboBox()
        volt_divs = [
            ("0.1 V/div", 0.1), ("0.2 V/div", 0.2), ("0.5 V/div", 0.5),
            ("1.0 V/div", 1.0), ("2.0 V/div", 2.0),
        ]
        for label, val in volt_divs:
            self._cmb_volt_div.addItem(label, val)
        self._cmb_volt_div.setCurrentIndex(3)  # 1 V/div
        self._cmb_volt_div.currentIndexChanged.connect(self._on_volt_div_changed)
        lay.addWidget(self._cmb_volt_div, 0, 1)

        grp.setLayout(lay)
        parent_layout.addWidget(grp)

    # ── Trigger ────────────────────────────────────────────

    def _add_trigger_control(self, parent_layout):
        grp = QGroupBox("触发")
        lay = QGridLayout()

        lay.addWidget(QLabel("触发方式:"), 0, 0)
        self._cmb_trig_mode = QComboBox()
        self._cmb_trig_mode.addItem("自动", TRIG_MODE_AUTO)
        self._cmb_trig_mode.addItem("正常", TRIG_MODE_NORMAL)
        self._cmb_trig_mode.addItem("单次", TRIG_MODE_SINGLE)
        self._cmb_trig_mode.currentIndexChanged.connect(
            lambda i: self.sig_trig_mode.emit(self._cmb_trig_mode.currentData()))
        lay.addWidget(self._cmb_trig_mode, 0, 1)

        lay.addWidget(QLabel("触发边沿:"), 1, 0)
        self._cmb_trig_edge = QComboBox()
        self._cmb_trig_edge.addItem("上升沿", TRIG_EDGE_RISING)
        self._cmb_trig_edge.addItem("下降沿", TRIG_EDGE_FALLING)
        self._cmb_trig_edge.addItem("双沿", TRIG_EDGE_BOTH)
        self._cmb_trig_edge.currentIndexChanged.connect(
            lambda i: self.sig_trig_edge.emit(self._cmb_trig_edge.currentData()))
        lay.addWidget(self._cmb_trig_edge, 1, 1)

        lay.addWidget(QLabel("触发电平:"), 2, 0)
        trig_lay = QHBoxLayout()
        self._slider_trig_level = QSlider(Qt.Orientation.Horizontal)
        self._slider_trig_level.setRange(0, 4095)
        self._slider_trig_level.setValue(2048)
        self._slider_trig_level.valueChanged.connect(self.sig_trig_level.emit)
        trig_lay.addWidget(self._slider_trig_level)

        self._lbl_trig_level = QLabel("1.65 V")
        self._lbl_trig_level.setMinimumWidth(50)
        self._slider_trig_level.valueChanged.connect(
            lambda v: self._lbl_trig_level.setText(f"{v / self.ADC_MAX * self.VREF:.2f} V"))
        trig_lay.addWidget(self._lbl_trig_level)
        lay.addLayout(trig_lay, 2, 1)

        grp.setLayout(lay)
        parent_layout.addWidget(grp)

    # ── Signal Source ──────────────────────────────────────

    def _add_signal_source_control(self, parent_layout):
        grp = QGroupBox("信号源 (DAC)")
        lay = QGridLayout()

        lay.addWidget(QLabel("波形:"), 0, 0)
        self._cmb_wave_type = QComboBox()
        for wave_id, wave_name in WAVE_TYPE_NAMES.items():
            self._cmb_wave_type.addItem(wave_name, wave_id)
        self._cmb_wave_type.currentIndexChanged.connect(
            lambda i: self.sig_wave_type.emit(self._cmb_wave_type.currentData()))
        lay.addWidget(self._cmb_wave_type, 0, 1)

        lay.addWidget(QLabel("频率:"), 1, 0)
        self._spn_wave_freq = QSpinBox()
        self._spn_wave_freq.setRange(10, 10000)
        self._spn_wave_freq.setValue(1000)
        self._spn_wave_freq.setSingleStep(100)
        self._spn_wave_freq.setSuffix(" Hz")
        self._spn_wave_freq.valueChanged.connect(self.sig_wave_freq.emit)
        lay.addWidget(self._spn_wave_freq, 1, 1)

        lay.addWidget(QLabel("幅度:"), 2, 0)
        amp_lay = QHBoxLayout()
        self._slider_wave_amp = QSlider(Qt.Orientation.Horizontal)
        self._slider_wave_amp.setRange(0, 4095)
        self._slider_wave_amp.setValue(2047)
        self._slider_wave_amp.valueChanged.connect(self.sig_wave_amp.emit)
        amp_lay.addWidget(self._slider_wave_amp)

        self._lbl_wave_amp = QLabel("1.65 V")
        self._lbl_wave_amp.setMinimumWidth(50)
        self._slider_wave_amp.valueChanged.connect(
            lambda v: self._lbl_wave_amp.setText(f"{v / self.ADC_MAX * self.VREF:.2f} V"))
        amp_lay.addWidget(self._lbl_wave_amp)
        lay.addLayout(amp_lay, 2, 1)

        grp.setLayout(lay)
        parent_layout.addWidget(grp)

    # ── Status ─────────────────────────────────────────────

    def _add_status_group(self, parent_layout):
        grp = QGroupBox("系统")
        lay = QVBoxLayout()

        self._btn_reset = QPushButton("🔄 软复位")
        self._btn_reset.clicked.connect(self.sig_soft_reset.emit)
        lay.addWidget(self._btn_reset)

        self._lbl_status = QLabel("状态: 未连接")
        self._lbl_status.setStyleSheet("color: #888888;")
        lay.addWidget(self._lbl_status)

        self._lbl_fps = QLabel("FPS: --")
        self._lbl_fps.setStyleSheet("color: #888888;")
        lay.addWidget(self._lbl_fps)

        self._lbl_dev_info = QLabel("")
        self._lbl_dev_info.setStyleSheet("color: #666666; font-size: 11px;")
        self._lbl_dev_info.setWordWrap(True)
        lay.addWidget(self._lbl_dev_info)

        grp.setLayout(lay)
        parent_layout.addWidget(grp)

    # ── Slot helpers ───────────────────────────────────────

    def _on_time_div_changed(self, idx):
        val = self._cmb_time_div.itemData(idx)
        if val is not None:
            self.sig_time_div.emit(val)
            # Suggest matching sample rate
            suggested_rate = int(10 / val)  # 10 samples per division
            if 1000 <= suggested_rate <= 100000:
                self._spn_sample_rate.blockSignals(True)
                self._spn_sample_rate.setValue(suggested_rate)
                self._spn_sample_rate.blockSignals(False)

    def _on_volt_div_changed(self, idx):
        val = self._cmb_volt_div.itemData(idx)
        if val is not None:
            self.sig_volt_div.emit(val)

    # ── Public status update ───────────────────────────────

    def set_connected(self, port: str, baud: int):
        self._lbl_status.setText(f"状态: 已连接 {port} @ {baud}")
        self._lbl_status.setStyleSheet("color: #00ff88;")

    def set_disconnected(self):
        self._lbl_status.setText("状态: 未连接")
        self._lbl_status.setStyleSheet("color: #888888;")

    def set_fps(self, fps: float):
        self._lbl_fps.setText(f"FPS: {fps:.1f}")

    def set_device_info(self, info: dict):
        if info:
            self._lbl_dev_info.setText(
                f"设备: {info.get('device_name', '?')}\n"
                f"固件: {info.get('fw_version', '?')}\n"
                f"ADC: {info.get('adc_bits', '?')}bit  "
                f"DAC: {info.get('dac_bits', '?')}bit\n"
                f"CPU: {info.get('cpuclk_freq', 0)/1e6:.0f} MHz"
            )
