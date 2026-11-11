# trajectory_manager — 轨迹管理模块

机器人手臂轨迹的录制、存储、回放与可视化。提供 Web 控制台 + REST API + WebSocket 实时通信。

## 快速启动

```bash
cd robot-agent
python -m script.trajectory_manager
# 浏览器打开 http://localhost:1255
```

无 ROS2 环境时自动进入模拟模式，可用于前端开发和调试。

## 模块结构

```
trajectory_manager/
├── __main__.py      # 启动入口
├── server.py        # FastAPI 主服务（REST + WebSocket + 静态文件）
├── ros_bridge.py    # ROS2 通信层（SimBridge / RosBridge 双模式）
├── recorder.py      # 轨迹录制引擎（异步采样 → SQLite）
├── player.py        # 轨迹回放引擎（自适应速度、循环播放）
├── db.py            # aiosqlite 数据库层
├── static/
│   └── index.html   # 单页 Web 控制台（Three.js URDF 3D 预览）
└── data/
    └── trajectories.db  # SQLite 数据文件（自动创建）
```

## 核心功能

| 功能 | 说明 |
|------|------|
| 模式切换 | idle / limp（拖动示教）/ lock（锁定保持） |
| 单关节控制 | 每个关节可独立设置模式和电流 |
| 轨迹录制 | 可配置采样间隔（10-5000ms），实时显示帧数和时长 |
| 轨迹回放 | 支持速度倍率、重复次数、循环间隔；自适应电机速度防抖动 |
| 灵巧手控制 | 左右手 6 自由度滑块 + 预设（张开/握紧） |
| 腿部/腰部控制 | 增量/绝对位置调节 + 预设姿势 |
| 3D 模型预览 | 加载 URDF + STL，关节角度实时同步 |

## API 概览

### REST

- `GET /api/trajectories` — 列出所有轨迹
- `GET /api/trajectories/{id}` — 获取轨迹详情（含全部帧数据）
- `PUT /api/trajectories/{id}` — 更新名称/描述
- `DELETE /api/trajectories/{id}` — 删除轨迹
- `GET /api/status` — 当前机器人状态快照

### WebSocket (`/ws`)

发送 JSON `{ "action": "xxx", ... }`，支持的 action：

| action | 参数 | 说明 |
|--------|------|------|
| `set_mode` | `mode` | 全局模式切换 |
| `set_joint_mode` | `motor_ids`, `mode` | 单关节模式 |
| `set_current` | `motor_ids`, `current` | 设置电流上限 |
| `set_hand` | `side`, `angles` | 灵巧手角度 |
| `start_record` | `name`, `interval_ms` | 开始录制 |
| `stop_record` | — | 停止录制并保存 |
| `play` | `trajectory_id`, `speed`, `repeat`, `interval_sec` | 回放轨迹 |
| `stop_play` | — | 停止回放 |
| `body_delta` | `target`, `motor_id`, `delta` | 增量调节腿/腰 |
| `body_pos` | `target`, `motor_id`, `pos` | 绝对位置设置 |
| `body_preset` | `preset` | 应用预设姿势 |

服务端 10Hz 推送 `status`、`record_status`、`play_status` 消息。

## 电机 ID 映射

```
左臂: 11-17 (肩俯仰/肩侧摆/肩旋转/肘弯曲/腕旋转/腕俯仰/腕偏转)
右臂: 21-27 (同上)
腰部: 31(偏航) 32(俯仰)
腿部: 51(小腿) 52(大腿)
```

## 与 Agent 系统融合

trajectory_manager 当前是独立运行的 Web 服务。要融合进 LangChain/LangGraph Agent 体系，有以下几种路径：

### 方案 1：作为 Agent Tool 调用（推荐起步）

将轨迹管理的核心操作封装为 LangChain Tool，Agent 可以通过自然语言触发录制/回放：

```python
from langchain.tools import tool
from script.trajectory_manager.ros_bridge import create_bridge
from script.trajectory_manager.recorder import Recorder
from script.trajectory_manager.player import Player

bridge = create_bridge()
recorder = Recorder(bridge)
player = Player(bridge)

@tool
def play_trajectory(name: str, speed: float = 1.0) -> str:
    """回放指定名称的轨迹。"""
    # 查询 DB 获取 trajectory_id，调用 player.play(...)
    ...

@tool
def record_trajectory(name: str, duration_sec: float = 10.0) -> str:
    """录制一段轨迹，持续指定秒数后自动停止。"""
    ...

@tool
def list_trajectories() -> str:
    """列出所有已保存的轨迹。"""
    ...
```

### 方案 2：HTTP API 集成

Agent 通过 HTTP 请求调用已运行的 trajectory_manager 服务：

```python
@tool
def robot_play_trajectory(trajectory_id: int, speed: float = 1.0) -> str:
    """通过 HTTP API 触发轨迹回放。"""
    import requests
    # WebSocket 或 REST 调用
    ...
```

适合 Agent 和轨迹管理器部署在不同进程/机器的场景。

### 方案 3：LangGraph 子图

将轨迹管理流程建模为 LangGraph 的子图节点：

```
用户意图识别 → 轨迹选择 → 安全检查 → 执行回放 → 结果反馈
```

适合需要多步决策的复杂场景（如"用慢速重复 3 次上次录的动作"）。

### 扩展建议

| 方向 | 说明 |
|------|------|
| 轨迹编辑 | 支持帧级别裁剪、拼接、镜像（左右手互换） |
| 轨迹标签 | 为轨迹添加语义标签（"挥手"、"抓取"），供 Agent 语义检索 |
| 安全约束 | 回放前检查关节限位、碰撞检测 |
| 力控模式 | 录制时记录力矩数据，回放时支持力位混合控制 |
| 多机协同 | 支持多台机器人同步回放同一轨迹 |
| 轨迹导入/导出 | 支持 JSON/CSV 格式导入导出，便于离线分析 |

## 依赖

```
fastapi
uvicorn
aiosqlite
```

ROS2 相关（仅真实模式需要）：
```
rclpy
bodyctrl_msgs
sensor_msgs
```
