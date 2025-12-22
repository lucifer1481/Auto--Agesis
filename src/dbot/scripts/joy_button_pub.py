#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy
from std_msgs.msg import Bool


class JoyButtonPub(Node):
    def __init__(self):
        super().__init__('joy_button_pub')

        # Parameters
        self.declare_parameter('button_index', 0)
        self.declare_parameter('topic', '/relay_cmd')
        self.declare_parameter('mode', 'hold')      # 'hold' or 'toggle'

        # Get params
        self.idx   = self.get_parameter('button_index').get_parameter_value().integer_value
        self.topic = self.get_parameter('topic').get_parameter_value().string_value
        self.mode  = self.get_parameter('mode').get_parameter_value().string_value

        # ROS pub/sub
        self.pub = self.create_publisher(Bool, self.topic, 10)
        self.sub = self.create_subscription(Joy, '/joy', self._cb, 10)

        # Internal state
        self._last_button = 0
        self._toggled = False
        self._current_state = None  # last sent True/False

        self.get_logger().info(
            f"JoyButtonPub running: buttons[{self.idx}] -> {self.topic} "
            f"({self.mode} mode)"
        )

    def _publish(self, state: bool):
        """Publish Bool only when state changes."""
        if self._current_state == state:
            return  # no change, do nothing

        self._current_state = state
        msg = Bool(data=state)
        self.pub.publish(msg)
        self.get_logger().info(f"Button state -> {state} (published on {self.topic})")

    def _cb(self, msg: Joy):
        if self.idx >= len(msg.buttons):
            return

        cur = msg.buttons[self.idx]
        new_state = None

        if self.mode == 'hold':
            # Direct mapping: button pressed = True, released = False
            new_state = bool(cur)
        else:  # 'toggle' mode
            # Rising edge detection
            if cur == 1 and self._last_button == 0:
                self._toggled = not self._toggled
                new_state = self._toggled

        if new_state is not None:
            self._publish(new_state)

        self._last_button = cur


def main():
    rclpy.init()
    node = JoyButtonPub()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()