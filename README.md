# Play-Monkey

跨平台移动端 Monkey 测试工具，支持 Android 和 iOS 设备的自动化压力测试、性能监控和稳定性分析。

## 功能特性

### 核心能力
- Android 和 iOS 双平台支持（含 iOS 17+）
- 可配置比例的随机 tap / swipe 事件下发
- 测试时长支持按事件数或按秒数限制
- 事件下发失败时指数退避（3 → 6 → 12 → 24 → 30s），单次成功即重置

### 性能监控
- CPU 使用率（应用 + 系统）
- 内存占用（实际物理占用 physFootprint / RSS）
- FPS（CoreAnimation / SurfaceFlinger）
- 电池消耗与温度

### 稳定性监控
- 崩溃 / ANR 自动捕获
- 错误日志采集
- 崩溃或 ANR 后自动恢复继续测试
- 稳定性评分 + HTML 详细报告

---

## 安装

### 通用环境
- macOS（iOS 测试必须，Android 测试推荐）
- Python 3.12 或更高版本

### 项目安装
```bash
git clone <repository-url>
cd play-monkey

python3.12 -m venv .venv
source .venv/bin/activate

pip install -e .
# 如需开发测试依赖
pip install -e ".[dev]"
```

安装完成后 `play-monkey` 命令即可用。

---

## Android 测试准备

### 1. 安装 adb
```bash
brew install android-platform-tools
```

### 2. 在设备上打开"开发者选项"
- 设置 → 关于手机 → 多次点击"版本号"进入开发者模式
- 设置 → 开发者选项 → 启用 **USB 调试**
- 通过 USB 连接电脑，首次连接需在设备上点击"允许 USB 调试"

### 3. 验证设备已连接
```bash
adb devices
# List of devices attached
# c33042bb    device
```

看到 `device` 状态即可。`unauthorized` 说明还没在设备上确认授权，`offline` 说明连接有问题。

### 4. 启动目标应用
Play-Monkey 不负责启动 App，测试开始前请手动启动要测试的 App 并保持前台。

### 5. 列出设备并核对
```bash
play-monkey list-devices
```

可以开始测试了，跳到 [运行测试](#运行测试)。

---

## iOS 测试准备

iOS 测试需要三样东西：**WebDriverAgent（下发事件）**、**开发者隧道（性能监控）**、**应用签名**。iOS 16 和 iOS 17+ 在底层机制上略有不同，下面分版本说明。

### 0. 通用准备
```bash
# 安装命令行工具（已装 Xcode 可跳过）
xcode-select --install
```

- 在 iPhone 上：**设置 → 隐私与安全性 → 开发者模式** 打开（iOS 16+ 才有此开关）
- USB 连接电脑，第一次连接时在设备上点击"信任此电脑"

### 1. 构建并部署 WebDriverAgent
Play-Monkey 的 tap/swipe 走 WebDriverAgent (WDA) 的 HTTP 接口。

1. 克隆 WDA 源码：https://github.com/appium/WebDriverAgent
2. Xcode 打开 `WebDriverAgent.xcodeproj`
3. 选中 `WebDriverAgentRunner` target，签名配置用你自己的 Apple ID
4. 选中目标设备（真机），构建一次即部署（⌘ + B 或 ⌘ + U）
5. 构建成功后设备上会出现 `WebDriverAgentRunner-Runner` 应用

### 2. 启动 WebDriverAgent

**方式 A：让 Play-Monkey 自动启动（推荐用于 iOS 16 及以下）**

什么都不用做，测试运行时会通过 `tidevice wdaproxy` 自动启动并监听 `localhost:8100`。

**方式 B：手动启动（iOS 17+ 必须，iOS 16 可选）**

```bash
# 在 Xcode 里用 Cmd+U 运行 WebDriverAgentRunner 的 UITest
# 或命令行：
xcodebuild \
  -project /path/to/WebDriverAgent.xcodeproj \
  -scheme WebDriverAgentRunner \
  -destination "id=<DEVICE_UDID>" \
  test

# 另开终端建立端口转发
iproxy 8100 8100
# 或
tidevice relay 8100 8100
```

验证：
```bash
curl http://localhost:8100/status
# 返回 JSON 状态即为就绪
```

### 3. 启动开发者隧道（仅 iOS 17+ 需要）

iOS 17+ 的性能数据（CPU / Memory / FPS）通过 pymobiledevice3 的 DVT instruments 采集，需要开发者隧道。

选一种方式启动：

```bash
# 方式 A（推荐）：后台驻留的 tunneld 服务
sudo t3 tunneld

# 方式 B：绑定到具体 UDID
pymobiledevice3 lockdown start-tunnel --script-mode --udid <DEVICE_UDID>
```

tunneld 启动后会在 `http://localhost:5555` 或 `http://localhost:49151` 暴露 tunnel 地址，Play-Monkey 会自动发现。

> 如果不启动 tunneld，iOS 17+ 测试依然能跑，但报告里 CPU / Memory / FPS 图表会没有数据，只保留电池与温度。

### 4. 列出设备

```bash
play-monkey list-devices
```

能看到你的 iOS 设备 UDID 就表明设备链路打通了。

---

## 运行测试

### 1. 准备配置文件

`examples/` 目录里已经有 `android_test.yaml` 和 `ios_test.yaml`，按需修改 `device_id` 和 `app_package` 即可。最小示例：

```yaml
platform: ios              # 或 android
device_id: 00008101-...    # UDID 或 Android serial
app_package: com.example.app

event_ratios:
  tap: 0.7
  swipe: 0.3

interval_ms: 200
duration_seconds: 180      # 和 event_count 二选一

# swipe 手势时长（每次从这个范围内均匀采样）
swipe_duration:
  min_ms: 50
  max_ms: 100

# 可选：事件坐标边界（不配置则使用全屏）
# 配置后所有 tap/swipe 都在此范围内生成
bounds:
  x_min: 15
  x_max: 415
  y_min: 350
  y_max: 750

# 可选：禁区配置（避开特定区域）
# 所有 tap 和 swipe 的起点/终点都会避开这些区域
# 适用场景：状态栏、导航栏、退出按钮等敏感区域
exclusion_zones:
  # 顶部状态栏
  - x_min: 0
    x_max: 415
    y_min: 0
    y_max: 100
  # 底部导航栏
  - x_min: 0
    x_max: 415
    y_min: 750
    y_max: 844

monitoring:
  enabled: true
  sample_interval_seconds: 1

stability:
  monitor_crashes: true
  monitor_anr: true          # Android 专属，iOS 可设 false
  monitor_errors: true
  continue_on_crash: true
  continue_on_anr: true
  max_crash_count: 1

report:
  output_path: ./report.html
```

### 2. 下发测试

**使用配置文件**：
```bash
play-monkey run --config examples/ios_test.yaml
```

**命令行快速运行**：
```bash
play-monkey run \
  --platform android \
  --device c33042bb \
  --app com.example.app \
  --tap-ratio 0.7 --swipe-ratio 0.3 \
  --events 1000 \
  --interval 200 \
  --output report.html
```

命令行参数会覆盖配置文件中的同名字段。

### 3. 查看报告

```bash
open report.html
```

报告里会显示事件统计、CPU / 内存 / FPS / 温度 的时序图、崩溃与 ANR 明细、整体稳定性评分。

---

## 常见问题

**Q：iOS 测试跑起来 tap 全部失败？**
大概率是上一次测试残留的 WDA 还在设备上。关掉 Xcode 里的 WebDriverAgentRunner，或者在设备上手动划掉 `WebDriverAgentRunner-Runner` 应用，再重新跑。

**Q：iOS 17+ 报告里 CPU / Memory / FPS 图都是空的？**
tunneld 没启动，参照前面 [启动开发者隧道](#3-启动开发者隧道仅-ios-17-需要) 那一节。

**Q：Android 测试 ANR 阈值怎么调？**
配置文件 `stability.anr_threshold_seconds`，默认 5 秒，想更严格的话降到 3。

**Q：事件下发中途开始变慢？**
这是指数退避在起作用。事件下发失败时 scheduler 会等 3s 再发下一个，再失败翻倍（最长 30s），成功一次就重置。通常是 WDA 或 app 短时间进入不健康状态，等一会儿能恢复。

---

## 开发

### 运行测试
```bash
pytest
```

### 代码格式化 / 静态检查
```bash
black src/ tests/
ruff check src/ tests/
mypy src/
```

---

## 许可证

MIT License
