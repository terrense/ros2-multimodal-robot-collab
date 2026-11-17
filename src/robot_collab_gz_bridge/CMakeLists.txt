cmake_minimum_required(VERSION 3.8)
project(robot_collab_gz_bridge)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(gazebo_msgs REQUIRED)
find_package(ignition-transport11 REQUIRED)
find_package(ignition-msgs5 REQUIRED)

add_executable(twist_forwarder src/twist_forwarder.cpp)
target_include_directories(twist_forwarder PRIVATE
  /usr/include/ignition/msgs5
)
target_link_libraries(twist_forwarder
  rclcpp::rclcpp
  ${geometry_msgs_TARGETS}
  ignition-transport11::ignition-transport11
)

# The world's ground truth, republished in the message the rest of the
# stack reads. gz-sim has no state plugin; this is its counterpart.
add_executable(gz_pose_to_model_states src/gz_pose_to_model_states.cpp)
target_include_directories(gz_pose_to_model_states PRIVATE
  /usr/include/ignition/msgs5
)
target_link_libraries(gz_pose_to_model_states
  rclcpp::rclcpp
  ${geometry_msgs_TARGETS}
  ${gazebo_msgs_TARGETS}
  ignition-transport11::ignition-transport11
)

install(TARGETS twist_forwarder gz_pose_to_model_states
  DESTINATION lib/${PROJECT_NAME}
)

ament_export_dependencies(rclcpp geometry_msgs gazebo_msgs)

ament_package()
