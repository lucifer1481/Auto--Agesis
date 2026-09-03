# 🧠 Security D-Bot: AI & Perception Node (Auto--Agesis)

This repository contains the Al-enabled perception and decision-making workspace for the Security D-Bot, an autonomous indoor surveillance robot. Designed to run on an NVIDIA Jetson Nano, this package serves as the computation and perception layer of the system[cite: 1].

## 🔗 Related Repository
*   **[dbot](https://github.com/lucifer1481/dbot):** The companion Raspberry Pi 4 controller for ROS2 navigation and hardware actuation[cite: 1].

## 📸 System Architecture & Visuals

**1. Perception Pipeline Flowchart**
*(Replace this text and the link below with your flowchart image showing YOLO and CNN integration)*
![Perception Pipeline](docs/images/perception_flow.png)

**2. Jetson Nano Setup**
*(Replace this text and the link below with a photo of the Jetson Nano mounted on the bot)*
![Jetson Nano Hardware](docs/images/jetson_setup.jpg)

## ⚙️ Core Features
*   **Real-Time Object Detection:** Utilizes a YOLO model for person detection at approximately 30 FPS[cite: 1].
*   **Face Recognition:** Implements a CNN-based framework to extract facial embeddings and match identities[cite: 1].
*   **Intelligent Decision Logic:** Classifies individuals as known or unknown to trigger selective alerting via cloud communication[cite: 1].

## 🛠️ Prerequisites
*   Ubuntu 20.04 / 22.04 running on NVIDIA Jetson Nano
*   ROS2 (Humble/Foxy) installed and sourced
*   Python 3.8+ with OpenCV, PyTorch, and YOLO dependencies

## 🚀 Installation & Setup

1. Clone the repository into your ROS2 workspace:
   ```bash
   cd ~/ros2_ws/src
   git clone [https://github.com/lucifer1481/Auto--Agesis.git](https://github.com/lucifer1481/Auto--Agesis.git)

   Install Python dependencies:
    cd ~/ros2_ws/src/Auto--Agesis
    pip install -r requirements.txt
   
   Build the ROS2 workspace:
  
    cd ~/ros2_ws
    colcon build --packages-select auto_agesis
   
   Running the Nodes
   Source the workspace:
    source ~/ros2_ws/install/setup.bash
   
   Launch the perception node:
    ros2 launch auto_agesis perception.launch.py
