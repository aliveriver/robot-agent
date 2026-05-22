#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import random
import glob
import numpy as np
import sounddevice as sd
import sherpa_onnx

# ── ROS2 workspace Python 路径注入 ──────────
_ROS2_WS = os.environ.get("ROS2_WS", "/opt/PARTITIONS/A/ros2ws")
for _p in glob.glob(f"{_ROS2_WS}/install/*/local/lib/python*/dist-packages"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
# ──────────────────────────────────────────
import re
import rclpy
from rclpy.node import Node
from src.robot_agent.bootstrap.logging import get_logger

# 初始化系统结构化日志
logger = get_logger(__name__)

try:
    from bodyctrl_msgs.msg import MotorStatusMsg, CmdSetMotorPosition, SetMotorPosition
    from sensor_msgs.msg import JointState
except ImportError as e:
    print(f"\n[CRITICAL] 核心消息包不可导入: {e}")
    sys.exit(1)

# ==========================================
# 1. 配置与常量定义
# ==========================================
GESTURES = {
    "scissors": [1.0, 1.0, 0.0, 0.0, 1.0, 0.3],  # 剪刀 (peace)
    "paper":    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # 布 (open)
    "rock":     [1.0, 1.0, 1.0, 1.0, 1.0, 0.5],  # 石头 (close)
}

LEFT_ARM_IDS = [11, 12, 13, 14, 15, 16, 17]
RIGHT_ARM_IDS = [21, 22, 23, 24, 25, 26, 27]

SAFE_LOCK_CURRENT = {
    11: 6.0, 12: 5.0, 13: 4.0, 14: 4.0, 15: 2.0, 16: 2.0, 17: 2.0,
    21: 6.0, 22: 5.0, 23: 4.0, 24: 4.0, 25: 2.0, 26: 2.0, 27: 2.0,
}

class AudioConfig:
    sample_rate = 16000
    channels = 1
    blocksize = 4000         # 每次阻塞读取的数据块大小 (4000/16000 = 0.25秒)
    input_gain = 1.0
    noise_floor = 0.0
    speech_threshold = 0.02  # VAD 触发阈值
    max_silence_sec = 1.0    # 连续静音截断时长
    min_segment_sec = 0.5    # 抛弃短于 0.5 秒的杂音
    max_segment_sec = 10.0   # 单次录音强制截断最大长

audio_cfg = AudioConfig()
trigger_keywords = ["石头剪刀布", "猜拳", "玩游戏", "玩个游戏", "比划比划"]

# ==========================================
# 2. Sherpa-ONNX (SenseVoice) 识别引擎
# ==========================================
recognizer = None

def initSherpaOnnx():
    global recognizer
    print("\n[LOG][INIT] 正在加载 Sherpa-ONNX SenseVoice 模型...")
    logger.info("ASR_Engine: Starting model initialization")
    try:
        model_dir = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
        model_path = os.path.join(model_dir, "model_quant.onnx")
        tokens_path = os.path.join(model_dir, "tokens.txt")
        
        if not os.path.exists(model_path):
             model_path = os.path.join(model_dir, "model.onnx")
             
        if not os.path.exists(model_path) or not os.path.exists(tokens_path):
            print(f"[LOG][ERROR] 模型文件未在 {model_dir} 中找到。")
            logger.error("ASR_Engine: Initialization failed, files missing", model_dir=model_dir)
            return False

        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model_path,
            tokens=tokens_path,
            use_itn=True,
            debug=False,
        )
        print("[LOG][INIT] Sherpa-ONNX SenseVoice 模型加载成功")
        logger.info("ASR_Engine: Initialization successful")
        return True
    except Exception as e:
        print(f"[LOG][ERROR] Sherpa-ONNX 模型加载失败: {e}")
        logger.exception("ASR_Engine: Exception during init", error=str(e))
        return False
def match_trigger_keywords(text: str) -> bool:
    """
    终极高灵敏度模糊匹配（纯 Python，无第三方依赖）
    极大降低触发门槛，容忍残缺词、方言及 ASR 严重翻车
    """
    if not text:
        return False
        
    # 1. 深度清洗文本（去掉所有标点、空格、特殊符号，全变小写）
    clean_text = re.sub(r'[ \t\r\n\s、，。！？～—…,.!?\-_:：([\]{}）)"\'“财”]', '', text).lower()
    
    # 2. 【核心升级：残缺词计数法】
    # 只要一句话里包含了以下核心手势词中的任意 2 个或以上，不管少说了哪个、顺序如何，直接算触发！
    # 完美解决：“石头布！”（漏了剪刀）、“剪子锤子”（方言残缺）
    core_game_words = ["石头", "剪刀", "剪子", "布", "包袱", "锤", "拳头"]
    match_count = sum(1 for word in core_game_words if word in clean_text)
    if match_count >= 2:
        return True
        
    # 3. 【动作意图组合拳】
    # 只要包含一个“动作倾向词” + 任意一个“游戏相关词”，就判定用户想玩
    # 完美解决：“出个布”、“来石头”、“玩剪刀”
    action_words = ["出", "来", "玩", "打", "比", "划", "跟", "陪", "开"]
    game_words = ["石头", "剪刀", "剪子", "布", "包袱", "锤", "拳", "游戏"]
    
    has_action = any(act in clean_text for act in action_words)
    has_game_word = any(gw in clean_text for gw in game_words)
    if has_action and has_game_word:
        return True

    # 4. 【终极方言与 ASR 灾难级谐音库】
    # 收集了北方方言、南方常见叫法，以及 SenseVoice 各种离奇的同音字翻车现场
    ultimate_keywords = [
        # 方言/口语拓展
        "包袱锤", "剪子包", "锤子剪子", "江包剪", "猜拳", "划拳", "比划", "黑白配",
        # ASR “猜拳/划拳” 史诗级翻车容错
        "才拳", "裁拳", "彩拳", "财拳", "拆拳", "踩拳", "菜拳", "发拳", "抓拳", "化拳",
        # ASR “玩游戏” 史诗级翻车容错
        "玩有戏", "万游戏", "完游戏", "晚游戏", "网游戏", "具有戏",
        # ASR “石头剪刀布” 各种单字同音错乱
        "尖刀布", "十头", "时头", "实头", "石头不", "石头步"
    ]
    
    if any(k in clean_text for k in ultimate_keywords):
        return True
        
    return False
def useASR_science_voice(audio_data):
    asr1 = time.time()
    print(f"\n[时间戳] ASR开始: {asr1}")
    logger.info("ASR_Process: Inference started", timestamp=asr1, data_len=len(audio_data))
    raw_text = ""                 
    try:
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, audio_data)
        recognizer.decode_stream(stream)
        raw_text = stream.result.text
    except Exception as e:
        print(f"[LOG][ERROR] ASR识别出错: {e}")
        logger.error("ASR_Process: Decode exception", error=str(e))
    finally:
        # 移至 finally 块，确保无论成功或失败均能统计耗时与结束戳
        asr2 = time.time()
        print(f"[时间戳] ASR结束: {asr2}")
        print(f"[时间戳] ASR识别时长: {asr2 - asr1:.4f} 秒")
        logger.info("ASR_Process: Inference completed", timestamp=asr2, duration=asr2-asr1, text=raw_text)
    return raw_text

# ==========================================
# 3. ROS2 机械臂控制节点
# ==========================================
class ManipulatorControlNode(Node):
    def __init__(self):
        super().__init__('manipulator_control_node')
        self.arm_cmd_pub = self.create_publisher(CmdSetMotorPosition, '/arm/cmd_pos', 10)
        self.lhand_cmd_pub = self.create_publisher(JointState, '/inspire_hand/ctrl/left_hand', 10)
        self.rhand_cmd_pub = self.create_publisher(JointState, '/inspire_hand/ctrl/right_hand', 10)
        
        self.current_arm_pos = {mid: 0.0 for mid in LEFT_ARM_IDS + RIGHT_ARM_IDS}
        self.locked_arm_pos = {mid: 0.0 for mid in LEFT_ARM_IDS + RIGHT_ARM_IDS}
        self.arm_mode = {'l': 'idle', 'r': 'idle'}
        self.target_hand_pos = {'left': [1.0]*6, 'right': [1.0]*6}
        
        self.ctrl_timer = self.create_timer(0.1, self.ctrl_cb)
        logger.info("ROS2_Node: ManipulatorControlNode initialized")

    def set_hand_target(self, target_hand, values):
        ratios = [float(max(0.0, min(1.0, (v / 100.0) if v > 1.5 else v))) for v in values]
        if target_hand in ['l', 'b']: self.target_hand_pos['left'] = ratios.copy()
        if target_hand in ['r', 'b']: self.target_hand_pos['right'] = ratios.copy()
        logger.debug("ROS2_Node: Target hand position updated", target=target_hand, ratios=ratios)

    def set_arm_mode(self, side, mode):
        targets = ['l', 'r'] if side == 'b' else [side]
        for t in targets:
            if mode == 'lock':
                ids = LEFT_ARM_IDS if t == 'l' else RIGHT_ARM_IDS
                for mid in ids: self.locked_arm_pos[mid] = self.current_arm_pos[mid]
            self.arm_mode[t] = mode
        logger.info("ROS2_Node: Arm mode altered", side=side, mode=mode)

    def ctrl_cb(self):
        arm_msg = CmdSetMotorPosition()
        arm_msg.header.stamp = self.get_clock().now().to_msg()
        arm_msg.header.frame_id = 'control_mode'
        arm_msg.cmds = []

        for side, arm_ids in [('l', LEFT_ARM_IDS), ('r', RIGHT_ARM_IDS)]:
            if self.arm_mode[side] != 'idle':
                for mid in arm_ids:
                    item = SetMotorPosition()
                    item.name = mid
                    if self.arm_mode[side] == 'limp':
                        item.pos, item.spd, item.cur = self.current_arm_pos[mid], 0.0, 0.0
                    elif self.arm_mode[side] == 'lock':
                        item.pos, item.spd, item.cur = self.locked_arm_pos[mid], 3.14, SAFE_LOCK_CURRENT.get(mid, 2.0)
                    arm_msg.cmds.append(item)
        if arm_msg.cmds:
            self.arm_cmd_pub.publish(arm_msg)

        for hand_side, pub in [('left', self.lhand_cmd_pub), ('right', self.rhand_cmd_pub)]:
            h_msg = JointState()
            h_msg.header.stamp = self.get_clock().now().to_msg()
            h_msg.name = ['1', '2', '3', '4', '5', '6']
            h_msg.position = self.target_hand_pos[hand_side]
            pub.publish(h_msg)

# ==========================================
# 4. 串行交互动作流
# ==========================================
def play_game_routine(node: ManipulatorControlNode):
    # print("\n[LOG][GAME] >>> ====== 猜拳游戏流正式触发 ======")
    # logger.info("Game_Flow: Triggered")
    
    # print("[LOG][GAME] ⏳ 游戏响应：就绪停顿 3 秒...")
    # time.sleep(3.0)
    
    # print("[LOG][GAME] 🔊 模拟广播音频: '石头、剪刀、布！'")
    # logger.info("Game_Flow: Playing audio indicator")
    # time.sleep(1.5) 
    
    action_name, action_values = random.choice(list(GESTURES.items()))
    print(f"[LOG][GAME] 🤖 动作下发 -> 机器人选择出：【{action_name.upper()}】")
    logger.info("Game_Flow: Robot action determined", gesture=action_name)
    
    node.set_hand_target('b', action_values)
    
    print("[LOG][GAME] ⏳ 保持出招姿态 4 秒供用户观察结果...")
    # time.sleep(4.0)
    
    print("[LOG][GAME] >>> 恢复默认状态 (Paper)，结束本次游戏。\n")
    logger.info("Game_Flow: Resetting to default posture")
    node.set_hand_target('b', GESTURES['paper'])

# ==========================================
# 5. 主单线程串行循环 (含 VAD 原版平移)
# ==========================================
def main():
    if not initSherpaOnnx():
        print("[LOG][FATAL] ASR 引擎未能成功构建，程序终止。")
        sys.exit(1)

    rclpy.init()
    node = ManipulatorControlNode()
    node.set_arm_mode('b', 'lock')  # 默认锁紧双臂支撑体

    print("\n" + "★"*60)
    print(" 🎮 语音猜拳核心循环已开启 (纯串行无多线程)")
    print(f" 🎯 关键字表: {trigger_keywords}")
    print(" 🎤 运行中... 请对着麦克风说话。")
    print("★"*60 + "\n")
    logger.info("Main_Loop: Serial pipeline running")

    # 开启物理输入流
    stream = sd.InputStream(
        samplerate=audio_cfg.sample_rate,
        channels=audio_cfg.channels,
        dtype="float32",
        blocksize=audio_cfg.blocksize
    )
    stream.start()

    # 原版 VAD 状态机变量
    _in_speech = False
    _silence_started_at = None
    _current_audio_frames = []

    try:
        while True:
            # 1. 刷新 ROS 底层状态（耗时 ~0，防止单线程逻辑导致死节点）
            rclpy.spin_once(node, timeout_sec=0.0)

            # 2. 阻塞式录音切片读取 (节拍器：每 0.25 秒循环一次)
            indata, overflow = stream.read(audio_cfg.blocksize)
            if indata is None or len(indata) == 0:
                continue

            # 3. 录音数据预处理 (完全保留原回调逻辑)
            audio_chunk = np.squeeze(indata.copy())
            if audio_chunk.ndim == 0:
                audio_chunk = np.array([float(audio_chunk)], dtype=np.float32)
            if audio_chunk.ndim > 1:
                audio_chunk = audio_chunk.reshape(-1)

            if audio_cfg.input_gain != 1.0:
                audio_chunk = np.clip(audio_chunk * audio_cfg.input_gain, -1.0, 1.0)
            if audio_cfg.noise_floor > 0:
                audio_chunk = np.where(np.abs(audio_chunk) >= audio_cfg.noise_floor, audio_chunk, 0.0)

            # 4. 原版 VAD 条件核心判定
            peak = float(np.max(np.abs(audio_chunk))) if audio_chunk.size > 0 else 0.0
            now = time.time()

            if peak >= audio_cfg.speech_threshold:
                if not _in_speech:
                    _in_speech = True
                    _current_audio_frames = []
                    print(f"\n[LOG][VAD] 🎤 检测到人声起跑 (当前峰值: {peak:.4f})")
                    logger.info("VAD_State: Speech segment started", peak_val=peak)
                _current_audio_frames.append(audio_chunk)
                _silence_started_at = None
            elif _in_speech:
                _current_audio_frames.append(audio_chunk)
                if _silence_started_at is None:
                    _silence_started_at = now

            if not _in_speech:
                continue

            # 计算缓存中累计音频长度
            total_samples = sum(len(frame) for frame in _current_audio_frames)
            buffered_duration = total_samples / float(audio_cfg.sample_rate)

            # 判断截断条件
            silence_too_long = (_silence_started_at is not None and (now - _silence_started_at) >= audio_cfg.max_silence_sec)
            segment_too_long = (buffered_duration >= audio_cfg.max_segment_sec)

            if not silence_too_long and not segment_too_long:
                # 未达到截断标准，继续在主循环中追加接收下一个分块
                continue

            # 5. Flush 阶段：提取完整语音帧
            frames = _current_audio_frames
            duration = buffered_duration

            # 状态重置归零
            _current_audio_frames = []
            _in_speech = False
            _silence_started_at = None

            if duration < audio_cfg.min_segment_sec:
                print(f"[LOG][VAD] ⚠️ 录音片段过短 ({duration:.2f}s)，判定为环境杂音，丢弃。")
                logger.debug("VAD_State: Segment dropped (too short)", duration=duration)
                continue

            print(f"[LOG][VAD] 🛑 语音流截断完成。有效语音长度: {duration:.2f}s, 切片总数: {len(frames)}")
            logger.info("VAD_State: Segment flushed successfully", duration=duration)

            # 6. 串行阻塞业务执行：停止录音 -> ASR 推理 -> 逻辑匹配 -> 动作 -> 恢复
            stream.stop()
            print("[LOG][VAD] ⏸️ 麦克风输入已处于休眠态（避免录入设备动作噪音）")
            
            # 拼接 1D NumPy 连续数组给 SenseVoice
            full_audio = np.concatenate(frames)
            
            # 执行真实识别
            text = useASR_science_voice(full_audio)
            print(f"[LOG][ASR] SenseVoice 识别文本结果 -> 『 {text} 』")
            # 模糊匹配敏感词
            if match_trigger_keywords(text):
                print(f"[LOG][MATCH] ✨ 成功匹配！触发游戏流程。")
                play_game_routine(node)
            else:
                print(f"[LOG][MATCH] 🔍 未包含有效指令，跳过。")

            print("\n[LOG][VAD] ▶️ 恢复麦克风监听...")
            stream.start()

    except KeyboardInterrupt:
        print("\n[LOG][EXIT] 捕捉到用户中断信号 (Ctrl+C)。")
    finally:
        stream.stop()
        stream.close()
        node.destroy_node()
        rclpy.shutdown()
        print("[LOG][EXIT] 节点与底层音频资源已安全离线。")

if __name__ == '__main__':
    main()