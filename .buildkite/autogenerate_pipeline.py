"""
This script is printing the Buildkite pipeline.yml to stdout.
This can also be used as a library to print the steps from a different pipeline
specified as a parameter to the `generate_test_pipeline`.

The pipeline is generated based on the test configuration in
`test_description.json`. The JSON contains list of tests to be run by all
rust-vmm components.

Some components need to override the default configuration such that they can
access devices while running the tests (for example access to `/dev/kvm`),
access to a temporary volume, and others. As such, this script supports
overriding the following configurations through environment variables:
- `X86_LINUX_AGENT_TAGS`: overrides the tags by which the x86_64 linux agent is
  selected.
- `AARCH64_LINUX_AGENT_TAGS`: overrides the tags by which the aarch64 linux
  agent is selected.
- `DOCKER_PLUGIN_CONFIG`: specifies additional configuration for the docker
  plugin. For available configuration, please check the
  https://github.com/buildkite-plugins/docker-buildkite-plugin.

NOTE: The environment variables are specified as dictionaries, where the first
key is `tests` and its value is a list of test names where the configuration
should be applied; the second key is `cfg` and its value is a dictionary with
the actual configuration.

Examples of a valid configuration:
```shell
DOCKER_PLUGIN_CONFIG='{
    "tests": ["coverage"],
    "cfg": {
        "devices": [ "/dev/vhost-vdpa-0" ],
        "privileged": true
    }
}'
```
"""

import yaml
import json
import os
import sys
import pathlib

CONTAINER_VERSION = "v12"
DOCKER_PLUGIN_VERSION = "v3.8.0"

X86_AGENT_TAGS = os.getenv('X86_LINUX_AGENT_TAGS')
AARCH64_AGENT_TAGS = os.getenv('AARCH64_LINUX_AGENT_TAGS')
DOCKER_PLUGIN_CONFIG = os.getenv('DOCKER_PLUGIN_CONFIG')

PARENT_DIR = pathlib.Path(__file__).parent.resolve()


class BuildkiteStep:
    """
    This builds a Buildkite step according to a json configuration and the
    environment variables `X86_LINUX_AGENT_TAGS`, `AARCH64_LINUX_AGENT_TAGS`
    and `DOCKER_PLUGIN_CONFIG`. The output is a dictionary.
    """

    def __init__(self, test_name, command, platform):
        """
        Initialize a Buildkite step with values provided as arguments for the
        mandatory keys `test_name`, `command` and `platform` and default values
        for the other keys.
        """

        # Default values
        self.retry = {'automatic': False}
        self.agents = {'os': 'linux'}
        self.plugins = [
            {
                f"docker#{DOCKER_PLUGIN_VERSION}": {
                    'image': f"rustvmm/dev:{CONTAINER_VERSION}",
                    'always-pull': True
                }
            }
        ]

        # Mandatory values
        assert test_name, "Step is missing test name."
        self.label = f"{test_name}-{platform}"

        assert command, "Step is missing command."
        self.command = command.replace(
            "{target_platform}", platform
        )

        assert platform, "Step is missing platform."
        if platform == 'aarch64':
            platform = 'arm'
        self.agents['platform'] = f"{platform}.metal"

    def _set_key_val(self, target, cfg):
        """ Add the key-value pairs of the dictionary cfg to target. """

        for key, val in cfg.items():
            target[key] = val

    def override_agent_tags(self, test_name):
        """ Override the tags by which the linux agent is selected. """

        env_cfg = None
        platform = self.agents.get('platform')

        if platform == 'x86_64.metal' and X86_AGENT_TAGS:
            env_cfg = X86_AGENT_TAGS
        if platform == 'arm.metal' and AARCH64_AGENT_TAGS:
            env_cfg = AARCH64_AGENT_TAGS
        if env_cfg:
            env_cfg = json.loads(env_cfg)
            if test_name in env_cfg['tests']:
                target = self.agents
                target.clear()
                cfg = env_cfg['cfg']
                self._set_key_val(target, cfg)

    def add_docker_config(self, test_name, input_cfg):
        """ Specifies additional configuration for the docker plugin. """

        # self.plugins is a list. We want to change the first plugin,
        # more precisely the value of the key
        # f"docker#{DOCKER_PLUGIN_VERSION}", which is a dictionary.
        target = self.plugins[0][f"docker#{DOCKER_PLUGIN_VERSION}"]
        if input_cfg:
            self._set_key_val(target, input_cfg)

        if DOCKER_PLUGIN_CONFIG:
            env_cfg = json.loads(DOCKER_PLUGIN_CONFIG)
            if test_name in env_cfg['tests']:
                cfg = env_cfg['cfg']
                self._set_key_val(target, cfg)

    def build_json(self):
        # This is purely for readability. It guarantees that the keys
        # will appear in this order in the step.
        ordered_keys = ['label', 'command', 'retry', 'agents', 'plugins']
        for key in ordered_keys:
            self.__dict__[key] = self.__dict__.pop(key)
        return self.__dict__


class BuildkiteConfig:
    """
    This builds the final Buildkite configuration from the json input
    using BuidkiteStep objects. The output is a dictionary that can
    be put into yaml format by the pyyaml package.
    """

    _instance = None

    def __new__(cls, json_input):
        if cls._instance is None:
            cls._instance = object.__new__(cls)
            cls._input = json_input
            cls._output = {'steps': []}
            cls._is_built = False

        return cls._instance

    def build(self):
        if not self._is_built:
            tests = self._input.get('tests')
            assert tests, "Input is missing list of tests."

            for test in tests:
                platforms = test.get('platform')
                assert len(platforms), "Input is missing platforms."

                for platform in platforms:
                    test_name = test.get('test_name')
                    command = test.get('command')
                    docker_cfg = test.get('docker_plugin')

                    step = BuildkiteStep(test_name, command, platform)
                    step.override_agent_tags(test_name)
                    step.add_docker_config(test_name, docker_cfg)

                    step_output = step.build_json()
                    self._output['steps'].append(step_output)

            self._is_built = True

    def get_output(self):
        return self._output


def generate_pipeline(config_file=f"{PARENT_DIR}/test_description.json"):
    with open(config_file) as json_file:
        json_cfg = json.load(json_file)
        json_file.close()

    config = BuildkiteConfig(json_cfg)
    config.build()
    output = config.get_output()
    yaml.dump(output, sys.stdout, sort_keys=False)


if __name__ == '__main__':
    generate_pipeline()
