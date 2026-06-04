# MSPM0G3507 简易示波器 & 信号源

基于 LCKFB 地猛星 MSPM0G3507 (LQFP-48) 开发板的 USB 示波器项目。

## 项目结构

```
MSPM0_Oscilloscope/
├── firmware/           # MCU 固件 (CCS Theia 工程)
├── upper_computer/     # 上位机软件 (Python)
├── simulator/          # MCU 协议模拟器
└── README.md
```

## 硬件连接

| 引脚 | 功能 | 说明 |
|------|------|------|
| PA27 | ADC 输入 (ch0) | 示波器探头 |
| PA15 | DAC 输出 | 信号源 (250Hz 正弦波) |
| PA10/PA11 | UART0 TX/RX | USB 串口 (CH340, 921600bps) |
| PA0 | LED | 运行指示 |

> PA15 ↔ PA27 需用杜邦线连接以自测信号源。

## 固件

### 环境
- CCS Theia 20.4.0
- MSPM0 SDK 2.10.00.04
- SysConfig 1.26.2
- TI Arm Clang 4.0.4 LTS

### 导入
1. CCS Theia → Import CCS Project → 选择 `firmware/` 目录
2. SysConfig 自动打开 `dac_test.syscfg`
3. Build → Debug → 烧录

### 功能
- ADC 轮询采样 (~13 ksps, 256点/帧)
- DAC DMA 正弦波输出 (250Hz, 64点表)
- UART 二进制协议 (921600bps, CRC-16-CCITT)
- 字节填充抗干扰

## 上位机

### 依赖
```bash
pip install -r upper_computer/requirements.txt
```

### 运行
```bash
python upper_computer/main.py
```

### 功能
- 实时波形显示 (pyqtgraph)
- 时基/电压幅度调节
- 触发方式/触发电平设置
- 串口连接管理
- 波形数据 CSV 导出

## 模拟器

### 运行
```bash
python simulator/simulator.py --port COM10 --baud 921600
```

配合虚拟串口对 (com0com) 使用，可在无硬件时调试上位机。

## 通信协议

二进制帧，CRC-16-CCITT 校验：

```
Frame: [SYNC 1B] [CMD 1B] [LEN 2B LE] [PAYLOAD] [CRC16 2B LE]
PC→MCU: SYNC=0x55
MCU→PC: SYNC=0xAA
```

Payload 中使用 0xAB 转义特殊字节 (0xAA/0xAB/0x55)。

## 版本

v1.0 — 2024年6月
