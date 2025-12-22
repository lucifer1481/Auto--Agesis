from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import ExecuteProcess
import os
from ament_index_python.packages import get_package_share_directory

def generate_launch_description():
    # Your existing joystick config
    joy_params = os.path.join(
        get_package_share_directory('dbot'),
        'config',
        'joystick.yaml'
    )

    # 1) Joystick driver -> publishes /joy
    joy_node = Node(
        package='joy',
        executable='joy_node',
        parameters=[joy_params],
        output='screen'
    )

    # 2) teleop_twist_joy -> /joy -> /cmd_vel_joy
    teleop_node = Node(
        package='teleop_twist_joy',
        executable='teleop_node',
        name='teleop_node',
        parameters=[joy_params],
        remappings=[('/cmd_vel', '/cmd_vel_joy')],
        output='screen'
    )

    # 3) Run your Python script directly (NO CMake install required)
    python_script_path = '/home/lucifer/dev_ws/src/dbot/scripts/joy_button_pub.py'

    joy_button_pub = ExecuteProcess(
        cmd=['python3', python_script_path],
        output='screen'
    )

    return LaunchDescription([
        joy_node,
        teleop_node,
        joy_button_pub,
    ])

