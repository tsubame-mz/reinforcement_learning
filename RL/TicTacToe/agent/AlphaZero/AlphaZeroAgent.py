from __future__ import annotations

import copy
import logging
import os
from typing import Dict, List, Tuple

import gym
import numpy as np
import torch
import torch.nn.functional as F

import gym_tictactoe  # NOQA
from agent import Agent
from logger import setup_logger

from .network import AlphaZeroNetwork

logger = setup_logger(__name__, logging.INFO)


class Node:
    def __init__(self, id: int, player: int):
        self.id = id
        self.player = player
        self.edges: List[Edge] = []

    def expand(self, actions: List[int], policy: List[float], next_id: int = 1):
        for i, action in enumerate(actions):
            child = Node(next_id + i, (self.player + 1) % 2)
            edge = Edge(self, child, action, policy[action])
            self.edges.append(edge)

    def add_exploration_noise(self):
        root_dirichlet_alpha = 0.15  # TODO
        root_exploration_fraction = 0.25  # TODO
        noise = np.random.gamma(root_dirichlet_alpha, 1, len(self.edges))
        frac = root_exploration_fraction
        for edge, n in zip(self.edges, noise):
            edge.prior = (edge.prior * (1 - frac)) + (n * frac)

    def select_edge(self) -> Edge:
        # UCBを計算
        # self.print_node()
        total_visit = sum([edge.visit_count for edge in self.edges])
        ucb_scores = [edge.ucb_score(total_visit) for edge in self.edges]
        max_idx = np.argmax(ucb_scores)
        edge = self.edges[max_idx]
        logger.debug("USB score: " + str(ucb_scores))
        logger.debug(f"Max UCB: edge[{edge}]/score[{ucb_scores[max_idx]}]")
        return edge

    def select_action(self) -> int:
        visits = [edge.visit_count for edge in self.edges]
        max_idx = np.argmax(visits)
        action = self.edges[max_idx].action
        logger.debug(f"Max action[{action}]/visits[{visits[max_idx]}]")
        return action

    @property
    def expanded(self) -> bool:
        return len(self.edges) > 0

    def print_node(self, depth=0, limit_depth: int = None, edge_info: str = None):
        if limit_depth and depth > limit_depth:
            return
        if depth == 0:
            logger.debug("- Node -" + "-" * 72)
        prefix_str = " " * depth + " - "
        info_str = prefix_str + edge_info + " : " + str(self) if edge_info else str(self)
        logger.debug(info_str)
        for i, edge in enumerate(self.edges):
            edge.out_node.print_node(depth=depth + 1, limit_depth=limit_depth, edge_info=str(edge))
        if depth == 0:
            logger.debug("-" * 80)

    def __str__(self):
        return f"id[{self.id}]/player[{self.player}]/edges[{len(self.edges)}]"


class Edge:
    def __init__(self, in_node: Node, out_node: Node, action: int, prior: float):
        self.id = str(in_node.id) + ":" + str(out_node.id)
        self.player = in_node.player
        self.action = action
        self.visit_count = 0
        self.value_sum = 0.0
        self.prior = prior
        self.in_node = in_node
        self.out_node = out_node

    def ucb_score(self, total_visit):
        pb_c_base = 19652  # TODO
        pb_c_init = 1.25  # TODO
        pb_c = np.log((total_visit + pb_c_base + 1) / pb_c_base) + pb_c_init
        pb_c *= np.sqrt(total_visit) / (self.visit_count + 1)
        prior_score = pb_c * self.prior
        value_score = self.value
        score = prior_score + value_score
        return score

    @property
    def value(self):
        if self.visit_count == 0:
            return 0
        return self.value_sum / self.visit_count

    def __str__(self):
        return f"id[{self.id}]/player[{self.player}]/action[{self.action}]/N[{self.visit_count}]/W[{self.value_sum:.2f}]/P[{self.prior:.2f}]"


class AlphaZeroAgent(Agent):  # type: ignore
    # TODO:
    # value support: 価値のカテゴリ分布化
    def __init__(self):
        self.simulation_num = 20
        obs_space = (3, 3, 3)  # TODO
        num_channels = 8  # TODO
        fc_hid_num = 16  # TODO
        fc_output_num = 9  # TODO
        self.network = AlphaZeroNetwork(obs_space, num_channels, fc_hid_num, fc_output_num)
        # print(self.network)
        self.node_num = 0

    def get_action(self, env: gym.Env, obs: Dict, return_root=False):
        self.network.eval()
        with torch.no_grad():
            return self._run_mcts(env, obs, return_root)

    def train(self, batch, optimizer):
        self.network.train()

        observations, targets = zip(*batch)
        observations = torch.from_numpy(np.array(observations)).float()
        target_values, target_policies = zip(*targets)
        target_values = torch.from_numpy(np.array(target_values)).unsqueeze(1).float()
        target_policies = torch.from_numpy(np.array(target_policies)).float()

        policy_logits, value = self.network.inference(observations)

        p_loss = -(target_policies * F.log_softmax(policy_logits, dim=1)).mean()
        v_loss = F.mse_loss(value, target_values)

        optimizer.zero_grad()
        total_loss = p_loss + v_loss
        total_loss.backward()
        # torch.nn.utils.clip_grad_norm_(self.network.parameters(), 0.5)
        optimizer.step()

        return p_loss.item(), v_loss.item()

    def save_model(self, filename):
        print(f"Save model: {filename}")
        save_data = {"state_dict": self.network.state_dict()}
        torch.save(save_data, filename)

    def load_model(self, filename):
        print(f"Load model: {filename}")
        if os.path.exists(filename):
            load_data = torch.load(filename)
            self.network.load_state_dict(load_data["state_dict"])
        else:
            print(f"{filename} not found")

    def _run_mcts(self, env: gym.Env, obs: Dict, return_root=False):
        logger.debug(f"*** Run MCTS ***")
        root = Node(0, obs["to_play"])
        policy, _ = self._inference(obs)

        root.expand(obs["legal_actions"], policy)
        root.add_exploration_noise()
        self.node_num = len(root.edges) + 1
        # root.print_node()

        for i in range(self.simulation_num):
            logger.debug("#" * 80)
            logger.debug(f"### *** Simulation[{i+1}] *** ###")
            temp_env = copy.deepcopy(env)
            search_path, temp_obs, temp_done = self._find_leaf(root, temp_env)
            value = self._evaluate(temp_env, temp_obs, temp_done, search_path[-1])
            self._backup(search_path, value)

        logger.debug("#" * 80)
        logger.debug("### *** Simulation complete *** ###")
        # root.print_node()
        root.print_node(limit_depth=1)
        action = root.select_action()

        if return_root:
            return action, root
        return action

    def _inference(self, obs: Dict):
        obs_tsr = torch.from_numpy(obs["board"]).unsqueeze(0).float()
        mask = self._make_mask(obs["legal_actions"])
        policy_logit, value = self.network.inference(obs_tsr)

        policy_logit += mask.masked_fill(mask == 1, -np.inf)
        policy = F.softmax(policy_logit, dim=1)
        # print(policy)
        return policy[0], value.item()

    def _make_mask(self, legal_actions: List[int]):
        mask = torch.ones(9)  # TODO
        mask[legal_actions] = 0
        return mask

    def _find_leaf(self, node: Node, env: gym.Env) -> Tuple:
        logger.debug(f"*** Find leaf ***")
        search_path = []

        edge = None
        temp_done = False
        while node.expanded:
            edge = node.select_edge()
            temp_obs, _, temp_done, _ = env.step(edge.action)
            search_path.append(edge)
            node = edge.out_node

        assert temp_obs is not None
        return search_path, temp_obs, temp_done

    def _evaluate(self, env: gym.Env, obs: Dict, done: bool, edge: Edge):
        if done:
            winner = obs["winner"]
            if winner is not None:
                if winner == edge.player:
                    return +10
                else:
                    return -10
            else:
                return 0
        else:
            policy, value = self._inference(obs)
            logger.debug(f"Node expand")
            edge.out_node.expand(obs["legal_actions"], policy, self.node_num)
            self.node_num += len(edge.out_node.edges)
            return -value  # 相手から見た価値なので反転させる

    def _backup(self, search_path: List[Edge], value: int):
        logger.debug(f"*** Backup[value=[{value}]] ***")
        player = search_path[-1].player
        for edge in reversed(search_path):
            edge.visit_count += 1
            edge.value_sum += value if edge.player == player else -value
            logger.debug(edge)
