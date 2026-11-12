注意：记录时使用record.py,复现轨迹时使用duplicate.py
注意记录的timer和复现的timer要保持一致

control可以调控机器人的动作
tianyi_body_zin用来调控机器人高度
teach暂时弃用

## 猜拳与轨迹回放

从 `robot-agent` 目录运行 `python -m script.play_game`。每次语音触发猜拳，
会读取 `script/trajectory_manager/data/trajectories.db`，复用
`duplicate_path.PlaybackNode` 回放双臂；双手保持本局随机选中的猜拳姿势。
回放完成后等待玩家亮拳，再截图判定输赢。回放失败时取消本局。

将 `.env.example` 中的 `GAME_TRAJECTORY_ID` 复制到 `.env` 选择记录：
默认 ID 5（`12314545`，1658 帧，采样周期 10 ms），也有 ID 6（`1243124`）
和 ID 2（`123123`）。播放器使用记录的采样周期，ID 错误时不会换播其他记录。
视觉判定需填写 `GAME_VISION_API_URL`（完整 `/v1/chat/completions` 地址）、
`GAME_VISION_API_KEY` 和支持图片输入的 `GAME_VISION_MODEL`。

单独运行 `duplicate_path.py` 仍从启动目录的 `path/trajectory.json` 回放双臂和双手，
默认周期为 0.1 秒。
