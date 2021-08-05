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

    def __init__(self):
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

    def _add_docker_config(self, cfg):
        if cfg:
            target = self.plugins[0][f"docker#{DOCKER_PLUGIN_VERSION}"]
            for key, val in cfg.items():
                target[key] = val

    def build(self, input):
        # Mandatory keys.
        test_name = input.get('test_name')
        command = input.get('command')
        platform = input.get('platform')

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

        # Optional keys.
        docker_cfg = input.get('docker_plugin')
        self._add_docker_config(docker_cfg)

        # This is purely for readability. It guarantees that the keys
        # will appear in this order in the step.
        ordered_keys = ['label', 'command', 'retry', 'agents', 'plugins']
        for key in ordered_keys:
            if key in self.__dict__:
                self.__dict__[key] = self.__dict__.pop(key)

        return self.__dict__


class BuildkiteConfig:
    """
    This builds the final Buildkite configuration from the json input
    using BuidkiteStep objects. The output is a dictionary that can
    be put into yaml format by the pyyaml package.
    """

    __instance = None

    def __new__(cls):
        if cls.__instance is None:
            cls.__instance = object.__new__(cls)
            cls.__instance.steps = []

        return cls.__instance

    def _env_override_agent_tags(self, test_index):
        """ Override the tags by which the linux agent is selected. """

        for env_var, platform in [
            (X86_AGENT_TAGS, 'x86_64.metal'),
            (AARCH64_AGENT_TAGS, 'arm.metal')
        ]:
            if env_var:
                env_cfg = json.loads(env_var)
                for test_name in env_cfg['tests']:
                    step_index = test_index[test_name]
                    step = self.steps[step_index]
                    assert step['agents']['platform'] == platform,\
                        "Wrong environment variable config."
                    target = step['agents']
                    target.clear()
                    cfg = env_cfg['cfg']
                    for key, val in cfg.items():
                        target[key] = val

    def _env_add_docker_config(self, test_index):
        """ Specifies additional configuration for the docker plugin. """

        if DOCKER_PLUGIN_CONFIG:
            env_cfg = json.loads(DOCKER_PLUGIN_CONFIG)
            for test_name in env_cfg['tests']:
                step_index = test_index[test_name]
                step = self.steps[step_index]
                target = step['plugins'][0][f"docker#{DOCKER_PLUGIN_VERSION}"]
                cfg = env_cfg['cfg']
                for key, val in cfg.items():
                    target[key] = val

    def build(self, input):
        tests = input.get('tests')
        assert tests, "Input is missing list of tests."

        # Dictionary for retrieving a step from its test_name.
        test_index = {}
        idx = 0

        for test in tests:
            platforms = test.get('platform')
            assert len(platforms), "Input is missing platforms."

            platforms = [platform for platform in platforms]

            for platform in platforms:
                test['platform'] = platform

                step = BuildkiteStep()
                step_output = step.build(test)
                self.steps.append(step_output)

                test_index[test['test_name']] = idx
                idx += 1

        self._env_override_agent_tags(test_index)
        self._env_add_docker_config(test_index)

        return self.__dict__


def generate_pipeline(config_file=f"{PARENT_DIR}/test_description.json"):
    with open(config_file) as json_file:
        json_cfg = json.load(json_file)
        json_file.close()

    config = BuildkiteConfig()
    output = config.build(json_cfg)
    yaml.dump(output, sys.stdout, sort_keys=False)


if __name__ == '__main__':
    generate_pipeline()
