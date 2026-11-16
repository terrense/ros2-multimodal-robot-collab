// Carries the base's velocity command onto the Gazebo Transport.
//
// gz-sim's diff-drive system takes its command on a Gazebo Transport
// topic. The bridge that normally carries a ROS topic onto a Gazebo one
// will not carry this one: it looks the Gazebo topic's type up, and the
// command topic has a subscriber and no publisher, so it is not on the
// transport's list and there is no type to look up.
//
// So the command is carried here, by a node that publishes the message
// itself. It needs no lookup: the publisher registers the type it
// publishes.

#include <memory>
#include <string>

#include <geometry_msgs/msg/twist.hpp>
#include <ignition/msgs/twist.pb.h>
#include <ignition/transport/Node.hh>
#include <rclcpp/rclcpp.hpp>

namespace robot_collab_gz_bridge
{

/// Publishes the base's command on the Gazebo Transport.
class TwistForwarder : public rclcpp::Node
{
public:
  TwistForwarder()
  : Node("gz_twist_forwarder")
  {
    const std::string topic = this->declare_parameter<std::string>(
        "gz_topic", "/diff_drive_controller/cmd_vel");
    const std::string ros_topic = this->declare_parameter<std::string>(
        "ros_topic", "/diff_drive_controller/cmd_vel");

    this->publisher_ = this->transport_.Advertise<ignition::msgs::Twist>(topic);
    if (!this->publisher_)
    {
      RCLCPP_FATAL(
          this->get_logger(),
          "could not publish on the Gazebo topic [%s]", topic.c_str());
      rclcpp::shutdown();
      return;
    }

    this->subscription_ = this->create_subscription<geometry_msgs::msg::Twist>(
        ros_topic, 10,
        [this](const geometry_msgs::msg::Twist & message)
        {
          ignition::msgs::Twist out;
          out.mutable_linear()->set_x(message.linear.x);
          out.mutable_linear()->set_y(message.linear.y);
          out.mutable_linear()->set_z(message.linear.z);
          out.mutable_angular()->set_x(message.angular.x);
          out.mutable_angular()->set_y(message.angular.y);
          out.mutable_angular()->set_z(message.angular.z);
          this->publisher_.Publish(out);
        });
    RCLCPP_INFO(
        this->get_logger(),
        "carrying [%s] onto the Gazebo topic [%s]",
        ros_topic.c_str(), topic.c_str());
  }

private:
  ignition::transport::Node transport_;
  ignition::transport::v11::Node::Publisher publisher_;
  rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr subscription_;
};

}  // namespace robot_collab_gz_bridge

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<robot_collab_gz_bridge::TwistForwarder>());
  rclcpp::shutdown();
  return 0;
}
