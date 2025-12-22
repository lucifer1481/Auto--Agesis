#!/usr/bin/env python3
"""
Launch nav2 navigation stack + localization (map_server + AMCL).

Equivalent to:
  ros2 launch nav2_bringup navigation_launch.py use_sim_time:=false map_subscribe_transient_local:=true
  ros2 launch nav2_bringup localization_launch.py map:=./lidarrrr.yaml use_sim_time:=false
"""

import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetEnvironmentVariable
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    # Launch args (override at the command line if needed)
    use_sim_time = LaunchConfiguration('use_sim_time')
    map_yaml = LaunchConfiguration('map')
    map_subscribe_transient_local = LaunchConfiguration('map_subscribe_transient_local')

    # Package shares
    nav2_share = get_package_share_directory('nav2_bringup')

    navigation_launch = os.path.join(nav2_share, 'launch', 'navigation_launch.py')
    localization_launch = os.path.join(nav2_share, 'launch', 'localization_launch.py')

    

    # Include: localization_launch.py
    loc_include = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(localization_launch),
        launch_arguments={
            'map': map_yaml,
            'use_sim_time': use_sim_time
        }.items()
    )

    # Include: navigation_launch.py
    nav_include = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(navigation_launch),
        launch_arguments={
            'use_sim_time': use_sim_time,
            'map_subscribe_transient_local': map_subscribe_transient_local
        }.items()
    )

    ld = LaunchDescription()

    # Arguments (defaults match your commands)
    ld.add_action(DeclareLaunchArgument(
        'use_sim_time', default_value='false',
        description='Use simulated clock'
    ))
    ld.add_action(DeclareLaunchArgument(
        # Default matches your example "./lidarrrr.yaml".
        # Provide an absolute path if you prefer.
        'map', default_value='./lidarrrr.yaml',
        description='Full path to map YAML file'
    ))
    ld.add_action(DeclareLaunchArgument(
        'map_subscribe_transient_local', default_value='true',
        description='QoS transient local for map subscription'
    ))

    # Line-buffer logs
    ld.add_action(SetEnvironmentVariable('RCUTILS_LOGGING_BUFFERED_STREAM', '1'))

    # Add the two includes
    ld.add_action(nav_include)
    ld.add_action(loc_include)

    return ld
