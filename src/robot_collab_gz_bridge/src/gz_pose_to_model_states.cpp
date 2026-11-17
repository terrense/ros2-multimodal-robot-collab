// Republishes gz-sim's ground truth in the message the rest of the stack
// reads.
//
// Gazebo Classic had a plugin for it: gazebo_ros_state published
// /gazebo/model_states and /link_states, and the navigation and the
// detector read the robot's real pose from there while distrusting the
// odometry. gz-sim has no counterpart plugin: the world's poses are
// published by a system of the world, on the Gazebo Transport, as a vector
// of poses.
//
// This node is the counterpart plugin. It takes that vector and publishes
// the two messages the stack reads: the models' poses and the links'
// poses, with the names Gazebo Classic gave them.

#include <memory>
#include <string>
#include <vector>

#include <gazebo_msgs/msg/link_states.hpp>
#include <gazebo_msgs/msg/model_states.hpp>
#include <geometry_msgs/msg/pose.hpp>
#include <ignition/msgs/pose_v.pb.h>
#include <ignition/transport/Node.hh>
#include <rclcpp/rclcpp.hpp>

namespace robot_collab_gz_bridge
{

/// Turns gz-sim's pose vector into the two state messages.
class GzPoseToModelStates : public rclcpp::Node
{
public:
  GzPoseToModelStates()
  : Node("gz_pose_to_model_states")
  {
    const std::string gz_topic = this->declare_parameter<std::string>(
        "gz_topic", "/model/collab_mobile_base/pose");
    const std::string models_topic = this->declare_parameter<std::string>(
        "model_states_topic", "/model_states");
    const std::string links_topic = this->declare_parameter<std::string>(
        "link_states_topic", "/link_states");

    this->model_publisher_ = this->create_publisher<gazebo_msgs::msg::ModelStates>(
        models_topic, 10);
    this->link_publisher_ = this->create_publisher<gazebo_msgs::msg::LinkStates>(
        links_topic, 10);

    // The message type is named: a lambda does not tell a template which
    // type it takes, and the transport has several overloads.
    this->subscribed_ = this->transport_.Subscribe<ignition::msgs::Pose_V>(
        gz_topic,
        [this](const ignition::msgs::Pose_V & message)
        {
          this->on_poses(message);
        });
    if (!this->subscribed_)
    {
      RCLCPP_WARN(
          this->get_logger(),
          "could not subscribe to the Gazebo topic [%s]", gz_topic.c_str());
    }

    RCLCPP_INFO(
        this->get_logger(),
        "republishing the poses on [%s] as [%s] and [%s]",
        gz_topic.c_str(), models_topic.c_str(), links_topic.c_str());
  }

private:
  /// The pose of one thing in the world.
  static geometry_msgs::msg::Pose to_pose(const ignition::msgs::Pose & pose)
  {
    geometry_msgs::msg::Pose out;
    out.position.x = pose.position().x();
    out.position.y = pose.position().y();
    out.position.z = pose.position().z();
    out.orientation.x = pose.orientation().x();
    out.orientation.y = pose.orientation().y();
    out.orientation.z = pose.orientation().z();
    out.orientation.w = pose.orientation().w();
    return out;
  }

  void on_poses(const ignition::msgs::Pose_V & message)
  {
    this->received_++;
    if (this->received_ == 1 || this->received_ % 200 == 0)
    {
      RCLCPP_INFO(
          this->get_logger(),
          "read %d pose messages from the Gazebo topic", this->received_);
    }

    gazebo_msgs::msg::ModelStates models;
    gazebo_msgs::msg::LinkStates links;

    for (const auto & pose : message.pose())
    {
      const std::string & name = pose.name();
      const std::size_t separator = name.find("::");
      if (separator == std::string::npos)
      {
        // A model: the name is the model's own.
        models.name.push_back(name);
        models.pose.push_back(to_pose(pose));
      }
      else if (name.find("::", separator + 2) == std::string::npos)
      {
        // A link: Gazebo Classic named it "model::link", and so does this.
        links.name.push_back(name);
        links.pose.push_back(to_pose(pose));
      }
      // Anything deeper is a visual, a collision or a sensor of a link.
    }

    if (!models.name.empty())
    {
      this->model_publisher_->publish(models);
    }
    if (!links.name.empty())
    {
      this->link_publisher_->publish(links);
    }
  }

  ignition::transport::Node transport_;
  rclcpp::Publisher<gazebo_msgs::msg::ModelStates>::SharedPtr model_publisher_;
  rclcpp::Publisher<gazebo_msgs::msg::LinkStates>::SharedPtr link_publisher_;
  bool subscribed_ = false;
  int received_ = 0;
};

}  // namespace robot_collab_gz_bridge

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(
      std::make_shared<robot_collab_gz_bridge::GzPoseToModelStates>());
  rclcpp::shutdown();
  return 0;
}
