#!/usr/bin/env python3
"""
Launch file to run twist_mux, Nav2 bringup, and slam_toolbox together.
Place this file in your package (e.g. src/dbot/launch/bringup_with_slam.launch.py)
Usage:
  ros2 launch dbot bringup_with_slam.launch.py
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration, ThisLaunchFileDir
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node

from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    dbot_share = get_package_share_directory('dbot')

    # paths to your config files inside the dbot package
    twist_mux_params = os.path.join(dbot_share, 'config', 'twist_mux.yaml')
    slam_params = os.path.join(dbot_share, 'config', 'mapper_params_online_async.yaml')

    # Nav2 share (assumes nav2_bringup is installed)
    nav2_share = get_package_share_directory('nav2_bringup')
    nav2_launch = os.path.join(nav2_share, 'launch', 'navigation_launch.py')

    # slam_toolbox share
    slam_share = get_package_share_directory('slam_toolbox')
    slam_launch = os.path.join(slam_share, 'launch', 'online_async_launch.py')

    # Node: twist_mux
    twist_mux_node = Node(
        package='twist_mux',
        executable='twist_mux',
        name='twist_mux',
        output='screen',
        parameters=[twist_mux_params],
        remappings=[('cmd_vel_out', '/diff_cont/cmd_vel_unstamped')],
    )

    # Include Nav2 bringup
    nav2_include = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(nav2_launch),
        launch_arguments={'use_sim_time': 'false'}.items(),
    )

    # Include slam_toolbox
    slam_include = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(slam_launch),
        launch_arguments={'slam_params_file': slam_params, 'use_sim_time': 'false'}.items(),
    )

    ld = LaunchDescription()

    # optional: export RCUTILS_LOGGING_BUFFERED_STREAM environment so logs are line buffered
    ld.add_action(SetEnvironmentVariable('RCUTILS_LOGGING_BUFFERED_STREAM', '1'))

    # add nodes / includes
    ld.add_action(twist_mux_node)
    ld.add_action(nav2_include)
    ld.add_action(slam_include)

    return ld
