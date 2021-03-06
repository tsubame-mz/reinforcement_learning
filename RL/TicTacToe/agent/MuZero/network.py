import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


def weight_init(m):
    if isinstance(m, nn.Linear):
        nn.init.kaiming_normal_(m.weight)
        nn.init.constant_(m.bias, 0)


class Representation(nn.Module):
    """
    observation -> hidden state
    """

    def __init__(self, input_num, hid_num, state_num):
        super(Representation, self).__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_num, hid_num),
            nn.ReLU(inplace=True),
            nn.Linear(hid_num, hid_num),
            nn.ReLU(inplace=True),
            nn.Linear(hid_num, state_num),
        )
        self.layers.apply(weight_init)

    def inference(self, x):
        h = self.layers(x)

        # [0, 1]にスケーリング
        h_scaled = h - h.min(1, keepdim=True)[0]
        h_scaled = h_scaled / h_scaled.max(1, keepdim=True)[0]
        # print(h_scaled, h_scaled.min(1, keepdim=True)[0], h_scaled.max(1, keepdim=True)[0])
        return h_scaled


class Prediction(nn.Module):
    """
    hidden state -> policy + value
    """

    def __init__(self, state_num, hid_num, action_num):
        super(Prediction, self).__init__()

        # self.common_layers = nn.Sequential(
        #     nn.Linear(state_num, hid_num), nn.ReLU(inplace=True), nn.Linear(hid_num, hid_num), nn.ReLU(inplace=True),
        # )
        self.policy_layers = nn.Sequential(
            nn.Linear(state_num, hid_num),
            nn.ReLU(inplace=True),
            nn.Linear(hid_num, hid_num),
            nn.ReLU(inplace=True),
            nn.Linear(hid_num, action_num),
        )
        self.value_layers = nn.Sequential(
            nn.Linear(state_num, hid_num),
            nn.ReLU(inplace=True),
            nn.Linear(hid_num, hid_num),
            nn.ReLU(inplace=True),
            nn.Linear(hid_num, 1),
            nn.Tanh(),
        )

        # self.common_layers.apply(weight_init)
        self.policy_layers.apply(weight_init)
        self.value_layers.apply(weight_init)

    def inference(self, x: torch.Tensor, mask: torch.Tensor = None):
        # print(x, mask)
        # h = self.common_layers(x)
        # policy = self.policy_layers(h)
        # value = self.value_layers(h)
        policy_logit = self.policy_layers(x)
        value = self.value_layers(x)
        return policy_logit, value


class Dynamics(nn.Module):
    """
    hidden state + action -> next hidden state + reward
    """

    def __init__(self, state_num, action_num, hid_num):
        super(Dynamics, self).__init__()
        input_num = state_num + action_num
        # self.common_layers = nn.Sequential(
        #     nn.Linear(input_num, hid_num), nn.ReLU(inplace=True), nn.Linear(hid_num, hid_num), nn.ReLU(inplace=True),
        # )
        self.state_layers = nn.Sequential(
            nn.Linear(input_num, hid_num),
            nn.ReLU(inplace=True),
            nn.Linear(hid_num, hid_num),
            nn.ReLU(inplace=True),
            nn.Linear(hid_num, state_num),
        )
        self.reward_layers = nn.Sequential(
            nn.Linear(input_num, hid_num),
            nn.ReLU(inplace=True),
            nn.Linear(hid_num, hid_num),
            nn.ReLU(inplace=True),
            nn.Linear(hid_num, 1),
        )

        # self.common_layers.apply(weight_init)
        self.state_layers.apply(weight_init)
        self.reward_layers.apply(weight_init)

    def inference(self, x):
        # h = self.common_layers(x)
        # return self.state_layers(h), self.reward_layers(h)
        h = self.state_layers(x)

        # [0, 1]にスケーリング
        h_scaled = h - h.min(1, keepdim=True)[0]
        h_scaled = h_scaled / h_scaled.max(1, keepdim=True)[0]
        # print(h_scaled, h_scaled.min(1, keepdim=True)[0], h_scaled.max(1, keepdim=True)[0])

        return h_scaled, self.reward_layers(x)


class Network(nn.Module):
    def __init__(self):
        super(Network, self).__init__()
        input_num = 10  # TODO
        hid_num = 64  # TODO
        state_num = 32  # TODO
        action_num = 9

        self.representation = Representation(input_num, hid_num, state_num)
        self.prediction = Prediction(state_num, hid_num, action_num)
        self.dynamics = Dynamics(state_num, action_num, hid_num)

    def initial_inference(self, x: torch.Tensor, mask: torch.Tensor = None):
        """
        Representation + Prediction
        """
        # print(x, mask)
        state = self.representation.inference(x)
        policy_logit, value = self.prediction.inference(state, mask)
        # print(state, policy_logit, value)
        return state, policy_logit, value

    def recurrent_inference(self, x: torch.Tensor):
        """
        Dynamics + Prediction
        """
        # print(x)
        next_state, reward = self.dynamics.inference(x)
        policy_logit, value = self.prediction.inference(next_state)
        # print(next_state, reward, policy_logit, value)
        return next_state, reward, policy_logit, value
