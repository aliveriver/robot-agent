注意：记录时使用record.py,复现轨迹时使用duplicate.py
注意记录的timer和复现的timer要保持一致

control可以调控机器人的动作
tianyi_body_zin用来调控机器人高度
teach暂时弃用

## 独立录制猜拳轨迹

在机器人已有 ROS2 驱动运行、已 source ROS2 环境的终端执行：

```bash
cd ~/robot-agent
python -m script.record_game_trajectory --name game_first
```

回车开始后双臂进入松弛模式，可手动带着机器人完成猜拳手臂动作；
只录到出手位置，再次回车停止并保存，不录收回动作。
游戏判定完输赢后会缓慢回到本局开始时保存的实际姿态。脚本复用 `trajectory_manager.Recorder`、`RosBridge`，
不需要完整 Agent、麦克风、相机或 TTS。默认采样周期 10 ms，
可用 `--interval-ms 100` 改为 100 ms。每次录制名称应不同；省略 `--name`
会自动生成含时间戳的名称，旧轨迹不覆盖。

数据直接保存到 `script/trajectory_manager/data/trajectories.db`（不是 JSON 文件）。
成功后打印数据库位置、轨迹 ID 和帧数，并且仅更新 `.env` 的
`GAME_TRAJECTORY_ID`，保留原 API、音频等配置。之后运行：

```bash
python -m script.play_game
```

就会使用新录的 ID。回放入口和数据库路径已写在 `play_game.py` 的
`GAME_TRAJECTORY_DB` / `GAME_TRAJECTORY_ID`，每次启动都会打印路径和选择。
如需只保存、不切换猜拳配置，用 `--keep-selection`。
`--simulate` 仅用于调试录制流程，生成的模拟轨迹不会自动选为猜拳轨迹。
真实模式缺少 ROS2 或关节反馈会报错，不自动切换为模拟数据。

## 猜拳与轨迹回放

从 `robot-agent` 目录运行 `python -m script.play_game`。
这是独立 Demo，无需启动完整 Agent，也不要求 `configs/app.yaml`；
脚本直接打印状态，参数从 `.env` / 环境变量读取。
启动时会按 `GAME_CAMERA_LAUNCH_COMMAND` 自动拉起 Orbbec Gemini 330 摄像头，
默认工作目录为 `/home/nvidia/orbbec_camera_ros2`，退出时只停止本次启动的进程。
如摄像头已由其他终端启动，可设置 `GAME_CAMERA_AUTO_START=false`。
每次语音触发猜拳，
会读取 `script/trajectory_manager/data/trajectories.db`，复用
`duplicate_path.PlaybackNode` 回放双臂。每局只播放 `GAME_TRAJECTORY_ID` 指定的一条轨迹，
不会顺序播放数据库的其他记录。流程为：保存本局双臂实际姿态 → 准备停顿 → 轨迹回放，末段
播放本地“石头、剪刀、布”口令 → 轨迹和口令均结束 → 随机出最终手势 →
保持姿势并等待玩家亮拳 → 截图判定输赢 → 双手张开 → 缓慢回到保存的开局姿态。
回位直接从当前实测位置生成缓起缓停的插值，复用原播放器，不倒序播放录制轨迹。
每局开始及回位前都会等待完整的 14 个关节新反馈；缺少反馈时取消该动作。
出手后持续保持轨迹末帧和最终手势，截图/API 请求期间也持续刷新 ROS 控制。
只有视觉返回胜、负或平局，才启动收回；请求失败或结果未知时保持姿势。
收回由判定结果触发，没有定时收回。`GAME_RESULT_WAIT_SEC` 仅控制截图前的观察停顿。
准备和轨迹回放时统一保持握拳，不提前显示最终随机手势；回放失败时取消本局。

在 `.env` 填 `GAME_COUNTDOWN_AUDIO=data/audio/rock_paper_scissors.wav`，
也可使用绝对路径。音频由你预先生成，程序只复用现有 `AudioPlaybackController`
异步播放，不调用 TTS。按文件实际时长从轨迹尾部倒推播放起点；若音频比轨迹长，
从回放开始播放，并在轨迹结束后保持末帧，等音频播完才出手。
当前默认轨迹约 16.58 秒，例如 1.5 秒音频约在回放第 15.08 秒开始。
出手以实际音频播放结束为准，设备启动和调度会产生少量偏差。

停顿仍保留，使用以下 `.env` 参数调整；等待期间继续刷新 ROS 控制：

- `GAME_READY_DELAY_SEC=3.0`：回放前的准备停顿。
- `GAME_REVEAL_DELAY_SEC=0.0`：轨迹和口令结束后，到出手的额外停顿。
- `GAME_RESULT_WAIT_SEC=2.0`：最终出手后，到截图判定的观察停顿。
- `GAME_AUDIO_LEAD_SEC=0.0`：音频启动提前量，用于补偿扬声器延迟。
- `GAME_RETURN_DURATION_SEC=4.0`：判定完成后回位的最短时长（秒）。
- `GAME_RETURN_SPEED_RAD_SEC=0.35`：回位的关节速度上限（弧度/秒）；位移大时自动延长回位时间。
- `GAME_COUNTDOWN_DURATION_SEC=1.5`：音频路径留空时的口令模拟时长；
  提供文件后自动采用文件真实时长。明确配置的文件缺失或播放失败会取消本局。

将 `.env.example` 中的 `GAME_TRAJECTORY_ID` 复制到 `.env` 选择记录：
默认 ID 5（`12314545`，1658 帧，采样周期 10 ms），也有 ID 6（`1243124`）
和 ID 2（`123123`）。播放器使用记录的采样周期，ID 错误时不会换播其他记录。
视觉判定需填写 `GAME_VISION_API_URL`（完整 `/v1/chat/completions` 地址）、
`GAME_VISION_API_KEY` 和支持图片输入的 `GAME_VISION_MODEL`。

单独运行 `duplicate_path.py` 仍从启动目录的 `path/trajectory.json` 回放双臂和双手，
默认周期为 0.1 秒。

双臂电流集中配置在 `trajectory_manager/arm_currents.json`（单位 A），
由 `play_game.py` 的姿势保持、`duplicate_path.PlaybackNode` 的出手回放和缓慢回位，
以及录制桥接的锁臂共用；修改后重启脚本生效。左右臂对应关节配置相同：

| 关节 | 左/右电机 ID | 电流上限 |
|------|-------------|----------|
| 肩俯仰 | 11 / 21 | 8A |
| 肩侧摆 | 12 / 22 | 7A |
| 肩旋转 | 13 / 23 | 4A |
| 肘关节 | 14 / 24 | 4A |
| 腕旋转、俯仰、偏转 | 15–17 / 25–27 | 3A |

拖动录制时的松弛模式仍发送 0A，停止后锁臂采用上表电流。


### 猜拳结束后自动回到双臂下垂姿态

启动 `python -m script.play_game` 前，先将双臂摆到希望保存的自然下垂位置。
程序收到完整双臂反馈后保存一次固定初始姿态；后续每一局都使用同一份姿态，
不会用下一局开始时的位置覆盖它。重启程序会重新记录，因此重启前也应先摆好双臂。

胜负或平局播报完成后，程序张开双手并平滑回到固定初始姿态。
视觉结果未知或本局执行异常时也会尝试复位；若关节反馈缺失，则报错并取消运动。
回位复用已有缓起缓停插值，默认最短 4 秒、峰值速度上限 0.35 rad/s，
分别通过 `.env` 中 `GAME_RETURN_DURATION_SEC`、`GAME_RETURN_SPEED_RAD_SEC` 调整。
不需要另外录制收回轨迹。
