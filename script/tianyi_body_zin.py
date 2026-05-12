import rclpy
from rclpy.node import Node

from std_msgs.msg import String, Header
from bodyctrl_msgs.msg import MotorStatusMsg, CmdSetMotorPosition, SetMotorPosition
import sys
import select
import termios
import tty

leg51_pos = 0.0
leg52_pos = 0.0
waist31_pos = 0.0
waist32_pos = 0.0



class KeyboardReader(Node):
    def __init__(self):
        super().__init__('keyboard_reader')
        self.publisher_ = self.create_publisher(String, 'keyboard_input', 10)
        # 发布到 /leg/cmd_pos 话题来控制腿部
        self.leg_cmd_publisher_ = self.create_publisher(
            CmdSetMotorPosition,
            '/leg/cmd_pos',
            10
        )
        # 发布到 /waist/cmd_pos 话题来控制腰部
        self.waist_cmd_publisher_ = self.create_publisher(
            CmdSetMotorPosition,
            '/waist/cmd_pos',
            10
        )
        # 订阅 /leg/status 话题
        self.leg_subscription_ = self.create_subscription(
            MotorStatusMsg,
            '/leg/status',
            self.leg_status_callback,
            10
        )
        # 订阅 /waist/status 话题
        self.waist_subscription_ = self.create_subscription(
            MotorStatusMsg,
            '/waist/status',
            self.waist_status_callback,
            10
        )
        self.get_logger().info('Keyboard reader node started. Type on your keyboard:')
        self.get_logger().info('Subscribed to /leg/status and /waist/status topics')
        self.get_logger().info('Publisher to /leg/cmd_pos and /waist/cmd_pos topics for control')
        # Backup terminal settings
        self.settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())
        except Exception as e:
            self.get_logger().error(str(e))
            sys.exit(1)
        # Timer to check keyboard
        self.timer = self.create_timer(0.05, self.read_key)

        self.target_leg51_pos = 0.0
        self.target_leg52_pos = 0.0
        self.target_waist31_pos = 0.0
        self.target_waist32_pos = 0.0
        self.count = 0

        
    def leg_status_callback(self, msg):
        """处理 /leg/status 话题的回调函数"""
        global leg51_pos, leg52_pos
        # self.get_logger().info(f'收到腿部状态消息: {len(msg.status)} 个电机状态')
        for status in msg.status:
            if status.name == 51:
                leg51_pos = status.pos
            elif status.name == 52:
                leg52_pos = status.pos
    
    def waist_status_callback(self, msg):
        """处理 /waist/status 话题的回调函数"""
        global waist31_pos, waist32_pos
        # self.get_logger().info(f'收到腰部状态消息: {len(msg.status)} 个电机状态')
        for status in msg.status:
            if status.name == 31:
                waist31_pos = status.pos
            elif status.name == 32:
                waist32_pos = status.pos
    
    def send_leg_command(self, leg51_pos_target, leg52_pos_target, speed=0.1, current=60.0):
        """发送腿部控制命令到 /leg/cmd_pos 话题
        
        Args:
            leg51_pos_target: 电机51的目标位置 (second_leg_pitch_joint)
            leg52_pos_target: 电机52的目标位置 (first_leg_pitch_joint)
            speed: 速度限制，默认3.14
            current: 电流限制，默认60.0
        """
        msg = CmdSetMotorPosition()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'base_link'
        
        # 电机52 (first_leg_pitch_joint)
        cmd52 = SetMotorPosition()
        cmd52.name = 52
        cmd52.pos = leg52_pos_target
        cmd52.spd = float(speed)
        cmd52.cur = float(current)
        msg.cmds.append(cmd52)
        
        # 电机51 (second_leg_pitch_joint) - 注意：需要加上电机52的位置（解耦）
        cmd51 = SetMotorPosition()
        cmd51.name = 51
        cmd51.pos = leg51_pos_target
        cmd51.spd = float(speed)
        cmd51.cur = float(current)
        msg.cmds.append(cmd51)
        
        self.leg_cmd_publisher_.publish(msg)
        # self.get_logger().info(f'发送腿部控制命令: leg51={leg51_pos_target}, leg52={leg52_pos_target}')
    
    def send_waist_command(self, waist31_pos_target, waist32_pos_target, speed=0.1, current=60.0):
        """发送腰部控制命令到 /waist/cmd_pos 话题
        
        Args:
            waist31_pos_target: 电机31的目标位置 (waist_yaw_joint)
            waist32_pos_target: 电机32的目标位置 (waist_pitch_joint)
            speed: 速度限制，默认3.14
            current: 电流限制，默认60.0
        """
        msg = CmdSetMotorPosition()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'base_link'
        
        # 电机32 (waist_pitch_joint)
        cmd32 = SetMotorPosition()
        cmd32.name = 32
        cmd32.pos = waist32_pos_target
        cmd32.spd = float(speed)
        cmd32.cur = float(current)
        msg.cmds.append(cmd32)
        
        # 电机31 (waist_yaw_joint)
        cmd31 = SetMotorPosition()
        cmd31.name = 31
        cmd31.pos = waist31_pos_target
        cmd31.spd = float(speed)
        cmd31.cur = float(current)
        msg.cmds.append(cmd31)
        
        self.waist_cmd_publisher_.publish(msg)
        # self.get_logger().info(f'发送腰部控制命令: waist31={waist31_pos_target}, waist32={waist32_pos_target}')
    
    def read_key(self):
        self.count += 1
        global leg51_pos, leg52_pos, waist31_pos, waist32_pos

        if self.count < 100:
            self.target_leg51_pos = leg51_pos
            self.target_leg52_pos = leg52_pos
            self.target_waist31_pos = waist31_pos
            self.target_waist32_pos = waist32_pos
        
        if select.select([sys.stdin], [], [], 0)[0]:
            key = sys.stdin.read(1)
            msg = String()
            msg.data = key
            self.publisher_.publish(msg)
            # self.get_logger().info(f'Key pressed: {repr(key)}')
            if key == '\x03':  # Ctrl-C
                self.destroy_node()
                raise KeyboardInterrupt

            if key == 'q':
                self.target_leg52_pos += 0.005
            elif key == 'w':
                self.target_leg51_pos += 0.005
            elif key == 'e':
                self.target_waist32_pos += 0.005
            elif key == 'r':
                self.target_waist31_pos += 0.005
            elif key == 'a':
                self.target_leg52_pos -= 0.005
            elif key == 's':
                self.target_leg51_pos -= 0.005
            elif key == 'd':
                self.target_waist32_pos -= 0.005
            elif key == 'f':
                self.target_waist31_pos -= 0.005
            elif key == 'z':
                self.target_leg51_pos = 0.0255
                self.target_leg52_pos = 0.2980 #0.0585
                self.target_waist31_pos = -0.000
                self.target_waist32_pos = 0.0001
            self.send_leg_command(self.target_leg51_pos, self.target_leg52_pos)
            self.send_waist_command(self.target_waist31_pos, self.target_waist32_pos)
            print(f'leg51_pos: {self.target_leg51_pos:.4f}, leg52_pos: {self.target_leg52_pos:.4f}, waist31_pos: {self.target_waist31_pos:.4f}, waist32_pos: {self.target_waist32_pos:.4f}')
    def destroy_node(self):
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.settings)
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = KeyboardReader()
    print("按qwer,asdf来控制腿部和腰部")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
