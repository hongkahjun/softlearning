import argparse

import numpy as np
import ray
from ray import tune

from rllab.envs.normalized_env import normalize
from rllab.misc.instrument import run_experiment_lite
from softlearning.algorithms import SAC
from softlearning.environments import MultiGoalEnv
from softlearning.misc.plotter import QFPolicyPlotter
from softlearning.misc.utils import timestamp
from softlearning.misc.sampler import SimpleSampler
from softlearning.policies import GMMPolicy, LatentSpacePolicy
from softlearning.replay_buffers import SimpleReplayBuffer
from softlearning.value_functions import NNQFunction, NNVFunction


def run(variant, reporter):
    env = normalize(MultiGoalEnv(
        actuation_cost_coeff=1,
        distance_cost_coeff=0.1,
        goal_reward=1,
        init_sigma=0.1,
    ))

    pool = SimpleReplayBuffer(max_replay_buffer_size=1e6, env_spec=env.spec)

    sampler = SimpleSampler(
        max_path_length=30, min_pool_size=100, batch_size=64)

    base_kwargs = {
        'sampler': sampler,
        'epoch_length': 100,
        'n_epochs': 1000,
        'n_train_repeat': 1,
        'eval_render': True,
        'eval_n_episodes': 10,
        'eval_deterministic': False
    }

    M = 128
    qf1 = NNQFunction(env_spec=env.spec, hidden_layer_sizes=[M, M], name='qf1')
    qf2 = NNQFunction(env_spec=env.spec, hidden_layer_sizes=[M, M], name='qf2')
    vf = NNVFunction(env_spec=env.spec, hidden_layer_sizes=[M, M])

    if variant['policy_type'] == 'gmm':
        policy = GMMPolicy(
            env_spec=env.spec,
            K=4,
            hidden_layer_sizes=[M, M],
            qf=qf1,
            reg=0.001
        )
    elif variant['policy_type'] == 'lsp':
        bijector_config = {
            "scale_regularization": 0.0,
            "num_coupling_layers": 2,
            "translation_hidden_sizes": (M,),
            "scale_hidden_sizes": (M,),
        }

        policy = LatentSpacePolicy(
            env_spec=env.spec,
            mode="train",
            squash=True,
            bijector_config=bijector_config,
            observations_preprocessor=None,
            q_function=qf1
        )

    plotter = QFPolicyPlotter(
        qf=qf1,
        policy=policy,
        obs_lst=np.array([[-2.5, 0.0],
                          [0.0, 0.0],
                          [2.5, 2.5]]),
        default_action=[np.nan, np.nan],
        n_samples=100
    )

    algorithm = SAC(
        base_kwargs=base_kwargs,
        env=env,
        policy=policy,
        initial_exploration_policy=None,
        pool=pool,
        qf1=qf1,
        qf2=qf2,
        vf=vf,
        plotter=plotter,

        lr=3e-4,
        target_entropy=-6.0,
        discount=0.99,
        tau=1e-4,

        save_full_state=True
    )

    for epoch, mean_return in algorithm.train():
        reporter(timesteps_total=epoch, mean_accuracy=mean_return)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', type=str, default='local')
    parser.add_argument(
        '--policy-type', type=str, choices=('gmm', 'lsp'), default='gmm')
    args = parser.parse_args()

    return args

def main():
    args = parse_args()
    variants = {
        'policy_type': args.policy_type
    }

    tune.register_trainable('multigoal-runner', run)
    if args.mode == 'local':
        ray.init()
        local_dir_base = './data/ray/results'
    else:
        ray.init(redis_address=ray.services.get_node_ip_address() + ':6379')
        local_dir_base = '~/ray_results'

    local_dir = '{}/multigoal/default'.format(local_dir_base)
    variants['local_dir'] = local_dir

    tune.run_experiments({
        'multigoal-' + timestamp(): {
            'run': 'multigoal-runner',
            'trial_resources': {'cpu': 2},
            'config': variants,
            'local_dir': local_dir
        }
    })

if __name__ == "__main__":
    main()
