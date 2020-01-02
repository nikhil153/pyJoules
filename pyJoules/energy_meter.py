# MIT License
# Copyright (c) 2019, INRIA
# Copyright (c) 2019, University of Lille
# All rights reserved.
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from __future__ import annotations

import time
import operator

from functools import reduce
from typing import List, Optional

from .exception import PyJoulesException
from .energy_device import EnergyDevice, EnergyDomain
from .energy_handler import EnergyHandler
from . import EnergySample

class NoNextStateException(PyJoulesException):
    """Exception raised when trying to compute duration or energy from a state
    which is the last state of an energy trace.
    """


class StateIsNotFinalError(PyJoulesException):
    """Exception raised when trying to add a state to a non final state on an energy trace
    """


class EnergyMeterNotStartedError(PyJoulesException):
    """
    Exception raised when trying to stop or record on a non started EnergyMeter instance
    """


class EnergyMeterNotStoppedError(PyJoulesException):
    """
    Exception raised when trying to get energy samples from non stopped EnergyMeter instance
    """


class SampleNotFoundError(PyJoulesException):
    """
    Exception raised when trying to retrieve a sample that does not exist on trace
    """


class EnergyMeter:
    """
    Tool used to record the energy consumption of given devices
    """

    def __init__(self, devices: List[EnergyDevice], default_tag: str = ''):
        """
        :param devices: list of the monitored devices
        :param default_tag: tag given if no tag were given to a measure
        """
        self.devices = devices
        self.default_tag = default_tag

        self._last_state = None
        self._first_state = None

    def _measure_new_state(self, tag):
        timestamp = time.perf_counter()
        values = [device.get_energy() for device in self.devices]

        return EnergyState(timestamp, tag if tag is not None else self.default_tag, values)

    def start(self, tag: Optional[str] = None):
        """
        Begin a new energy trace
        :param tag: sample name
        """
        new_state = self._measure_new_state(tag)
        self._first_state = new_state
        self._last_state = new_state

    def record(self, tag: Optional[str] = None):
        """
        Add a new state to the Trace
        :param tag: sample name
        :raise EnergyMeterNotStartedError: if the energy meter isn't started
        """
        if self._first_state is None:
            raise EnergyMeterNotStartedError()

        new_state = self._measure_new_state(tag)
        self._last_state.add_next_state(new_state)
        self._last_state = new_state

    def stop(self):
        """
        Set the end of the energy trace
        :raise EnergyMeterNotStartedError: if the energy meter isn't started
        """
        if self._first_state is None:
            raise EnergyMeterNotStartedError()

        new_state = self._measure_new_state('__stop__')
        self._last_state.add_next_state(new_state)
        self._last_state = new_state

    def get_sample(self, tag: str) -> EnergySample:
        """
        Retrieve the first sample in the trace with the given tag
        :param tag: tag of the sample to get
        :return: the sample with the given tag, if many sample have the same tag, the first sample created is returned
        :raise EnergyMeterNotStoppedError: if the energy meter isn't stopped
        :raise SampleNotFoundError: if the trace doesn't contains a sample with the given tag name
        """
        if self._first_state is None:
            raise EnergyMeterNotStartedError()

        if not self._last_state.tag == '__stop__':
            raise EnergyMeterNotStoppedError()

        for sample in self:
            if sample.tag == tag:
                return sample
        raise SampleNotFoundError()

    def __iter__(self):
        """
        iterate on the energy sample of the last trace
        :raise EnergyMeterNotStoppedError: if the energy meter isn't stopped
        """
        if self._first_state is None:
            raise EnergyMeterNotStartedError()

        if not self._last_state.tag == '__stop__':
            raise EnergyMeterNotStoppedError()

        return SampleIterator(self)

class SampleIterator:

    def __init__(self, energy_meter):
        self.energy_meter = energy_meter
        self._current_state = energy_meter._first_state

    def __next__(self):
        if self._current_state.next_state is None:
            raise StopIteration()

        domains = reduce(operator.add, [device.get_configured_domains() for device in self.energy_meter.devices])
        sample = EnergySample(self._current_state.timestamp, self._current_state.tag,
                              self._current_state.compute_duration(), self._current_state.compute_energy(domains))
        self._current_state = self._current_state.next_state
        return sample

class EnergyState:
    """
    Internal class that record the current energy state of the monitored device
    """

    def __init__(self, timestamp: float, tag: str, values: List[float]):
        """
        :param timstamp: timestamp of the measure
        :param tag: tag of the measure
        :param values: energy consumption measure, this is the list of measured energy consumption values for each
                       monitored device. This list contains the energy consumption since the last device reset to the
                       end of this sample
        """
        self.timestamp = timestamp
        self.tag = tag
        self.values = values
        self.next_state = None

    def is_last(self) -> bool:
        """
        indicate if the current state is the last state of the trace or not
        :return: True if the current state is the last state of the trace False otherwise
        """
        return self.next_state is None

    def compute_duration(self) -> float:
        """
        :return: compute the time elipsed between the current state and the next state
        :raise NoNextStateException: if the state is the last state of the trace
        """
        if self.next_state is None:
            raise NoNextStateException()

        return self.next_state.timestamp - self.timestamp

    def compute_energy(self, domains) -> List[float]:
        """
        :return: compute the energy consumed between the current state and the next state
        :raise NoNextStateException: if the state is the last state of the trace
        """
        if self.next_state is None:
            raise NoNextStateException()

        energy = []
        for next_device_values, current_device_values in zip(self.next_state.values, self.values):
            energy += [v_next - v_current for v_next, v_current in zip(next_device_values, current_device_values)]

        values_dict = {}
        for value, key in zip(energy, domains):
            values_dict[str(key)] = value
        return values_dict

    def add_next_state(self, state: EnergyState):
        """
        :param previous: next state for the same energy trace
        :raise StateIsNotFinalError: if there are already a next state
        """
        if self.next_state is not None:
            raise StateIsNotFinalError()
        self.next_state = state


def measureit(handler: EnergyHandler, domains: List[EnergyDomain]):
    """
    Measure the energy consumption of monitored devices during the execution of the decorated function
    :param handler: handler instance that will receive the power consummation data
    :param domains: list of the monitored energy domains
    """
    raise NotImplementedError()


class EnergyContext():

    def __init__(self, handler: EnergyHandler, domains: List[EnergyDomain]):
        raise NotImplementedError()

    def record(self, tag: Optional[str]):
        raise NotImplementedError()

    def __enter__(self) -> EnergyMeter:
        raise NotImplementedError()

    def __exit__(self, type, value, traceback):
        raise NotImplementedError()
