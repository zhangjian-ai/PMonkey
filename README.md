# Play-Monkey

跨平台移动端 Monkey 测试工具，支持 Android 和 iOS 设备的自动化压力测试、性能监控和稳定性分析。

## 功能特性

### 核心功能
- ✅ 支持 Android 和 iOS 设备（真机和模拟器）
- ✅ 可配置的随机点击和滑动操作
- ✅ 灵活的测试时长控制（按次数或时长）
- ✅ 可配置的操作坐标边界

### 性能监控
- ✅ CPU 使用率监控
- ✅ 内存占用监控
- ✅ FPS 帧率监控
- ✅ 电池消耗监控
- ✅ 详细的统计数据（最大值、平均值、百分位值）
- ✅ 时序图表可视化

### 稳定性监控
- ✅ 自动检测应用崩溃
- ✅ 自动检测 ANR/Hang
- ✅ 错误日志采集
- ✅ 崩溃后自动恢复并继续测试
- ✅ ANR 后自动恢复并继续测试
- ✅ 稳定性评分和详细报告

## 安装

### 前置要求

**Python 环境**:
- Python 3.12 或更高版本

**Android 测试**:
- Android SDK Platform Tools (ADB)
- 已连接的 Android 设备或模拟器

**iOS 测试**:
- macOS 系统
- Xcode Command Line Tools
- pymobiledevice3 依赖
- 已连接的 iOS 设备或模拟器

### 安装步骤

```bash
# 克隆仓库
git clone <repository-url>
cd play-monkey

# 创建虚拟环境
python3.12 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# 或
.venv\Scripts\activate  # Windows

# 安装依赖
pip install -e .

# 安装开发依赖（可选）
pip install -e ".[dev]"
```

## 快速开始

### 1. 列出可用设备

```bash
play-monkey list-devices
```

### 2. 创建配置文件

创建 `config.yaml`:

```yaml
platform: android
device_id: emulator-5554
app_package: com.example.app

event_ratios:
  tap: 0.7
  swipe: 0.3

interval_ms: 500
event_count: 1000

bounds:
  x_min: 100
  x_max: 900
  y_min: 200
  y_max: 1800

monitoring:
  enabled: true
  sample_interval_seconds: 2
  metrics: [cpu, memory, fps, battery]

stability:
  monitor_crashes: true
  monitor_anr: true
  monitor_errors: true
  continue_on_crash: true
  continue_on_anr: true

report:
  output_path: ./report.html
```

### 3. 运行测试

```bash
# 使用配置文件
play-monkey run --config config.yaml

# 或使用命令行参数
play-monkey run \
  --platform android \
  --device emulator-5554 \
  --app com.example.app \
  --tap-ratio 0.7 \
  --swipe-ratio 0.3 \
  --events 1000 \
  --interval 500 \
  --output report.html
```

### 4. 查看报告

```bash
open report.html  # macOS
# 或在浏览器中打开 report.html
```

## 配置说明

详细的配置选项说明请参考 [examples/](examples/) 目录中的示例配置文件。

## 开发

### 运行测试

```bash
pytest
```

### 代码格式化

```bash
black src/ tests/
ruff check src/ tests/
```

### 类型检查

```bash
mypy src/
```

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
