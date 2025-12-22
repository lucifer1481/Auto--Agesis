#!/bin/bash
# 1. Load ROS 2 Environment
source /opt/ros/humble/setup.bash

# 2. Go to your folder
cd /home/ubuntu/dbot_bridge

# 3. Run the bridge
/usr/bin/python3 dbot_bridge.py
