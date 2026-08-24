"""Post-training stack for the embodied delivery agent.

Layering (bottom-up):

    agent_env/            closed-loop world: protocol, faults, L1 sim, scripted expert
    training/chat_format  the single prompt surface shared by every stage
    training/rewards/     composite step reward + turn-level credit assignment
    training/envs/        chat-native multi-turn wrappers (L1 fast sim, L2 ROS2/Gazebo)
    training/data/        SFT corpus, DPO pairs, fixed fault-injection benchmark
    training/policies/    LLM policy backends, scripted baseline, frozen OPD teacher
    training/train_*.py   SFT -> DPO -> step-level PPO -> OPD -> OPD->PPO hybrid
    training/evaluate.py  bucketed metrics, reward ablation, checkpoint regression

Only `chat_format` decides what the policy sees, so a prompt change propagates to
data generation, training and evaluation at once.

Heavy dependencies (torch, transformers, vllm, deepspeed) are imported lazily by
the modules that need them. The environment, reward, credit-assignment and data
layers are pure stdlib + numpy so they stay testable on a laptop without a GPU.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
