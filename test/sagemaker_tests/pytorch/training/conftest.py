# Copyright 2018-2020 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
#     http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.
from __future__ import absolute_import

import json
import logging
import os
import platform
import shutil
import sys
import tempfile

import boto3
import pytest

from botocore.exceptions import ClientError
from sagemaker import LocalSession, Session
from sagemaker.pytorch import PyTorch

from . import get_efa_test_instance_type

from .utils import get_ecr_registry, NightlyFeatureLabel, is_nightly_context
from .integration import (
    get_framework_and_version_from_tag,
    get_cuda_version_from_tag,
)
from .utils.image_utils import build_base_image, are_fixture_labels_enabled
from .. import NO_P4_REGIONS, NO_G5_REGIONS, P5_AVAIL_REGIONS

from packaging.version import Version
from packaging.specifiers import SpecifierSet

logger = logging.getLogger(__name__)
logging.getLogger("boto").setLevel(logging.INFO)
logging.getLogger("boto3").setLevel(logging.INFO)
logging.getLogger("botocore").setLevel(logging.INFO)
logging.getLogger("factory.py").setLevel(logging.INFO)
logging.getLogger("auth.py").setLevel(logging.INFO)
logging.getLogger("connectionpool.py").setLevel(logging.INFO)


dir_path = os.path.dirname(os.path.realpath(__file__))

NEURON_TRN1_REGIONS = [
    "us-west-2",
]

NEURON_TRN1_INSTANCES = ["ml.trn1.2xlarge", "ml.trn1.32xlarge"]


def pytest_addoption(parser):
    parser.addoption("--build-image", "-D", action="store_true")
    parser.addoption("--build-base-image", "-B", action="store_true")
    parser.addoption("--aws-id")
    parser.addoption("--instance-type")
    parser.addoption("--docker-base-name", default="pytorch")
    parser.addoption("--region", default="us-west-2")
    parser.addoption("--framework-version", default="")
    parser.addoption(
        "--py-version",
        choices=["2", "3", "37", "38", "39", "310", "311", "312"],
        default=str(sys.version_info.major),
    )
    parser.addoption("--processor", choices=["gpu", "cpu", "neuron", "neuronx"], default="cpu")
    # If not specified, will default to {framework-version}-{processor}-py{py-version}
    parser.addoption("--tag", default=None)
    parser.addoption(
        "--generate-coverage-doc",
        default=False,
        action="store_true",
        help="use this option to generate test coverage doc",
    )
    parser.addoption(
        "--efa",
        action="store_true",
        default=False,
        help="Run only efa tests",
    )
    parser.addoption("--sagemaker-regions", default="us-west-2")


def pytest_configure(config):
    config.addinivalue_line("markers", "efa(): explicitly mark to run efa tests")
    config.addinivalue_line("markers", "deploy_test(): mark to run deploy tests")
    config.addinivalue_line("markers", "skip_test_in_region(): mark to skip test in some regions")
    config.addinivalue_line("markers", "skip_py2_containers(): skip testing py2 containers")
    config.addinivalue_line("markers", "model(): note the model being tested")
    config.addinivalue_line("markers", "integration(): note the feature being tested")
    config.addinivalue_line("markers", "skip_cpu(): skip cpu images on test")
    config.addinivalue_line("markers", "skip_gpu(): skip gpu images on test")
    config.addinivalue_line("markers", "multinode(): mark as multi-node test")
    config.addinivalue_line("markers", "processor(): note the processor type being tested")
    config.addinivalue_line("markers", "team(): note the team responsible for the test")
    config.addinivalue_line("markers", "skip_trcomp_containers(): skip trcomp images on test")
    config.addinivalue_line(
        "markers", "skip_inductor_test(): skip inductor test on incompatible images"
    )
    config.addinivalue_line("markers", "neuronx_test(): mark as neuronx image test")
    config.addinivalue_line("markers", "gdrcopy(): mark as gdrcopy integration test")
    config.addinivalue_line("markers", "skip_smppy_test(): skip smppy test")


def pytest_runtest_setup(item):
    efa_tests = [mark for mark in item.iter_markers(name="efa")]
    if item.config.getoption("--efa") and not efa_tests:
        pytest.skip("Skipping non-efa tests due to --efa flag")
    elif not item.config.getoption("--efa") and efa_tests:
        pytest.skip("Skipping efa tests because --efa flag is missing")


def pytest_collection_modifyitems(session, config, items):
    for item in items:
        print(f"item {item}")
        for marker in item.iter_markers(name="team"):
            print(f"item {marker}")
            team_name = marker.args[0]
            item.user_properties.append(("team_marker", team_name))
            print(f"item.user_properties {item.user_properties}")

    if config.getoption("--generate-coverage-doc"):
        from test.test_utils.test_reporting import TestReportGenerator

        report_generator = TestReportGenerator(items, is_sagemaker=True)
        report_generator.generate_coverage_doc(framework="pytorch", job_type="training")


# Nightly image fixture dictionary, maps nightly fixtures to set of image labels
NIGHTLY_FIXTURES = {
    "feature_smppy_present": {
        NightlyFeatureLabel.AWS_FRAMEWORK_INSTALLED.value,
        NightlyFeatureLabel.AWS_SMPPY_INSTALLED.value,
    },
    "feature_smdebug_present": {
        NightlyFeatureLabel.AWS_FRAMEWORK_INSTALLED.value,
        NightlyFeatureLabel.AWS_SMDEBUG_INSTALLED.value,
    },
    "feature_smddp_present": {
        NightlyFeatureLabel.AWS_FRAMEWORK_INSTALLED.value,
        NightlyFeatureLabel.AWS_SMDDP_INSTALLED.value,
    },
    "feature_smmp_present": {NightlyFeatureLabel.AWS_SMMP_INSTALLED.value},
    "feature_aws_framework_present": {NightlyFeatureLabel.AWS_FRAMEWORK_INSTALLED.value},
    "feature_smart_sifting_present": {NightlyFeatureLabel.AWS_SMART_SIFTING_INSTALLED.value},
}


# Nightly fixtures
@pytest.fixture(scope="session")
def feature_smppy_present():
    pass


# Nightly fixtures
@pytest.fixture(scope="session")
def feature_smdebug_present():
    pass


@pytest.fixture(scope="session")
def feature_smddp_present():
    pass


@pytest.fixture(scope="session")
def feature_smmp_present():
    pass


@pytest.fixture(scope="session")
def feature_aws_framework_present():
    pass


@pytest.fixture(scope="session")
def feature_smart_sifting_present():
    pass


@pytest.fixture(scope="session", name="docker_base_name")
def fixture_docker_base_name(request):
    return request.config.getoption("--docker-base-name")


@pytest.fixture(scope="session", name="region")
def fixture_region(request):
    return request.config.getoption("--region")


@pytest.fixture(scope="session", name="framework_version")
def fixture_framework_version(request):
    return request.config.getoption("--framework-version")


@pytest.fixture(scope="session", name="py_version")
def fixture_py_version(request):
    return "py{}".format(int(request.config.getoption("--py-version")))


@pytest.fixture(scope="session", name="processor")
def fixture_processor(request):
    return request.config.getoption("--processor")


@pytest.fixture(scope="session", name="sagemaker_regions")
def fixture_sagemaker_regions(request):
    sagemaker_regions = request.config.getoption("--sagemaker-regions")
    return sagemaker_regions.split(",")


@pytest.fixture(scope="session", name="tag")
def fixture_tag(request, framework_version, processor, py_version):
    provided_tag = request.config.getoption("--tag")
    default_tag = "{}-{}-{}".format(framework_version, processor, py_version)
    return provided_tag if provided_tag else default_tag


@pytest.fixture(scope="session", name="docker_image")
def fixture_docker_image(docker_base_name, tag):
    return "{}:{}".format(docker_base_name, tag)


@pytest.fixture
def opt_ml():
    tmp = tempfile.mkdtemp()
    os.mkdir(os.path.join(tmp, "output"))

    # Docker cannot mount Mac OS /var folder properly see
    # https://forums.docker.com/t/var-folders-isnt-mounted-properly/9600
    opt_ml_dir = "/private{}".format(tmp) if platform.system() == "Darwin" else tmp
    yield opt_ml_dir

    shutil.rmtree(tmp, True)


@pytest.fixture(scope="session", name="use_gpu")
def fixture_use_gpu(processor):
    return processor == "gpu"


@pytest.fixture(scope="session", name="build_base_image", autouse=True)
def fixture_build_base_image(
    request, framework_version, py_version, processor, tag, docker_base_name
):
    build_base_image_option = request.config.getoption("--build-base-image")
    if build_base_image_option:
        return build_base_image(
            framework_name=docker_base_name,
            framework_version=framework_version,
            py_version=py_version,
            base_image_tag=tag,
            processor=processor,
            cwd=os.path.join(dir_path, ".."),
        )

    return tag


@pytest.fixture(scope="session", name="sagemaker_session")
def fixture_sagemaker_session(region):
    return Session(boto_session=boto3.Session(region_name=region))


@pytest.fixture(name="efa_instance_type")
def fixture_efa_instance_type(request):
    try:
        return request.param
    except AttributeError:
        return get_efa_test_instance_type(default=["ml.p4d.24xlarge"])[0]


@pytest.fixture(scope="session", name="sagemaker_local_session")
def fixture_sagemaker_local_session(region):
    return LocalSession(boto_session=boto3.Session(region_name=region))


@pytest.fixture(name="aws_id", scope="session")
def fixture_aws_id(request):
    return request.config.getoption("--aws-id")


@pytest.fixture(name="instance_type", scope="session")
def fixture_instance_type(request, processor):
    provided_instance_type = request.config.getoption("--instance-type")
    default_instance_type = "local" if processor == "cpu" else "local_gpu"
    return provided_instance_type or default_instance_type


@pytest.fixture(name="docker_registry", scope="session")
def fixture_docker_registry(aws_id, region):
    return get_ecr_registry(aws_id, region)


@pytest.fixture(name="ecr_image", scope="session")
def fixture_ecr_image(docker_registry, docker_base_name, tag):
    return "{}/{}:{}".format(docker_registry, docker_base_name, tag)


@pytest.fixture(scope="session", name="dist_cpu_backend", params=["gloo"])
def fixture_dist_cpu_backend(request):
    return request.param


@pytest.fixture(scope="session", name="dist_gpu_backend", params=["gloo", "nccl"])
def fixture_dist_gpu_backend(request):
    return request.param


@pytest.fixture(autouse=True)
def skip_by_device_type(request, use_gpu, instance_type):
    is_gpu = use_gpu or instance_type.lstrip("ml.")[0] in ["g", "p"]

    # Skip a neuronx test that's not on an neuron instance or a test which
    # uses a neuron instance and is not a neuronx test
    is_neuronx_test = request.node.get_closest_marker("neuronx_test") is not None
    is_neuronx_instance = "trn1" in instance_type
    if is_neuronx_test != is_neuronx_instance:
        pytest.skip("Skipping because running on '{}' instance".format(instance_type))

    if (request.node.get_closest_marker("skip_gpu") and is_gpu) or (
        request.node.get_closest_marker("skip_cpu") and not is_gpu
    ):
        pytest.skip('Skipping because running on "{}" instance'.format(instance_type))


@pytest.fixture(autouse=True)
def skip_by_py_version(request, py_version):
    """
    This will cause tests to be skipped w/ py3 containers if "py-version" flag is not set
    and pytest is running from py2. We can rely on this when py2 is deprecated, but for now
    we must use "skip_py2_containers"
    """
    if request.node.get_closest_marker("skip_py2") and "py2" in py_version:
        pytest.skip("Skipping the test because Python 2 is not supported.")


@pytest.fixture(autouse=True)
def skip_test_in_region(request, region):
    if request.node.get_closest_marker("skip_test_in_region"):
        if region == "me-south-1":
            pytest.skip("Skipping SageMaker test in region {}".format(region))


@pytest.fixture(autouse=True)
def skip_gpu_instance_restricted_regions(region, instance_type):
    if (region in NO_P4_REGIONS and instance_type.startswith("ml.p4")) or (
        region in NO_G5_REGIONS and instance_type.startswith("ml.g5")
    ):
        pytest.skip("Skipping GPU test in region {}".format(region))


@pytest.fixture(autouse=True)
def skip_gpu_instance_restricted_regions_efa(region, efa_instance_type):
    # NOTE list for P5 instances is *available* regions
    if region not in P5_AVAIL_REGIONS and efa_instance_type.startswith("ml.p5"):
        pytest.skip("Skipping GPU test in region {}".format(region))


@pytest.fixture(autouse=True)
def skip_neuron_trn1_test_in_region(request, region):
    if request.node.get_closest_marker("skip_neuron_trn1_test_in_region"):
        if region not in NEURON_TRN1_REGIONS:
            pytest.skip("Skipping SageMaker test in region {}".format(region))


@pytest.fixture(autouse=True)
def skip_py2_containers(request, tag):
    if request.node.get_closest_marker("skip_py2_containers"):
        if "py2" in tag:
            pytest.skip("Skipping python2 container with tag {}".format(tag))


@pytest.fixture(autouse=True)
def skip_trcomp_containers(request, ecr_image):
    if request.node.get_closest_marker("skip_trcomp_containers"):
        if "trcomp" in ecr_image:
            pytest.skip(
                "Skipping training compiler integrated container with tag {}".format(ecr_image)
            )


@pytest.fixture(autouse=True)
def skip_inductor_test(request):
    if "framework_version" in request.fixturenames:
        fw_ver = request.getfixturevalue("framework_version")
    elif "ecr_image" in request.fixturenames:
        fw_ver = request.getfixturevalue("ecr_image")
    else:
        return
    if request.node.get_closest_marker("skip_inductor_test"):
        if Version(fw_ver) < Version("2.0.0"):
            pytest.skip(
                f"SM inductor test only support PT2.0 and above, skipping this container with tag {fw_ver}"
            )


@pytest.fixture(autouse=True)
def skip_smdebug_v1_test(
    request,
    processor,
    ecr_image,
):
    """Skip SM Debugger and Profiler tests due to v1 deprecation for PyTorch 2.0.1 and above frameworks."""
    skip_dict = {
        "==2.0.*": ["cu121"],
        ">=2.1,<2.4": ["cpu", "cu121"],
        ">=2.4,<2.6": ["cpu", "cu124"],
        ">=2.6,<2.7.1": ["cpu", "cu126"],
        ">=2.7.1,<2.8": ["cpu", "cu128"],
    }
    if _validate_pytorch_framework_version(
        request, processor, ecr_image, "skip_smdebug_v1_test", skip_dict
    ):
        pytest.skip(f"SM Profiler v1 is on path for deprecation, skipping test")


@pytest.fixture(autouse=True)
def skip_dgl_test(
    request,
    processor,
    ecr_image,
):
    """Start from PyTorch 2.0.1 framework, DGL binaries are not installed in DLCs by default and will be added in per customer ask.
    The test condition should be modified appropriately and `skip_dgl_test` pytest mark should be removed from dgl tests
    when the binaries are added in.
    """
    skip_dict = {
        "==2.0.*": ["cu121"],
        ">=2.1,<2.4": ["cpu", "cu121"],
        ">=2.4,<2.6": ["cpu", "cu124"],
        ">=2.6,<2.7.1": ["cpu", "cu126"],
        ">=2.7.1,<2.8": ["cpu", "cu128"],
    }
    if _validate_pytorch_framework_version(
        request, processor, ecr_image, "skip_dgl_test", skip_dict
    ):
        pytest.skip(f"DGL binary is removed, skipping test")


@pytest.fixture(autouse=True)
def skip_pytorchddp_test(
    request,
    processor,
    ecr_image,
):
    """Start from PyTorch 2.0.1 framework, SMDDP binary releases are decoupled from DLC releases.
    For each currency release, Once SMDDP binary is added, we skip pytorchddp tests due to `pytorchddp` and `smdistributed` launcher consolidation.
    See https://github.com/aws/sagemaker-python-sdk/pull/4698.
    """
    skip_dict = {
        ">=2.1,<2.4": ["cu121"],
        ">=2.4,<2.6": ["cu124"],
    }
    if _validate_pytorch_framework_version(
        request, processor, ecr_image, "skip_pytorchddp_test", skip_dict
    ):
        pytest.skip(f"SM Data Parallel binaries exist in this image, skipping test")


@pytest.fixture(autouse=True)
def skip_smdmodelparallel_test(
    request,
    processor,
    ecr_image,
):
    skip_dict = {
        "==2.0.*": ["cu121"],
        ">=2.1,<2.4": ["cpu", "cu121"],
        ">=2.4,<2.6": ["cpu", "cu124"],
        ">=2.6,<2.7.1": ["cpu", "cu126"],
        ">=2.7.1,<2.8": ["cpu", "cu128"],
    }
    if _validate_pytorch_framework_version(
        request, processor, ecr_image, "skip_smdmodelparallel_test", skip_dict
    ):
        pytest.skip(
            f"SM Model Parallel team is maintaining their own Docker Container, skipping test"
        )


@pytest.fixture(autouse=True)
def skip_smddataparallel_test(
    request,
    processor,
    ecr_image,
):
    """Start from PyTorch 2.0.1 framework, SMDDP binary releases are decoupled from DLC releases.
    For each currency release, we can skip SMDDP tests if the binary does not exist.
    However, when the SMDDP binaries are added, be sure to fix the test logic such that the tests are not skipped.
    """
    skip_dict = {"==2.0.*": ["cu121"], ">=2.6,<2.7.1": ["cu126"], ">=2.7.1,<2.8": ["cu128"]}
    if _validate_pytorch_framework_version(
        request, processor, ecr_image, "skip_smddataparallel_test", skip_dict
    ):
        pytest.skip(f"SM Data Parallel binaries do not exist in this image, skipping test")


@pytest.fixture(autouse=True)
def skip_smppy_test(
    request,
    processor,
    ecr_image,
):
    """For each currency release, we can skip smppy tests if the Profiler binary does not exist.
    However, when the Profiler binaries are added, be sure to fix the test logic such that the tests are not skipped.
    """
    skip_dict = {">=2.7.1,<2.8": ["cpu", "cu128"]}
    if _validate_pytorch_framework_version(
        request, processor, ecr_image, "skip_smppy_test", skip_dict
    ):
        pytest.skip(f"Profiler binaries do not exist in this image, skipping test")


@pytest.fixture(autouse=True)
def skip_p5_tests(request, processor, ecr_image):
    if "efa_instance_type" in request.fixturenames:
        test_instance_type = request.getfixturevalue("efa_instance_type")
    elif "instance_type" in request.fixturenames:
        test_instance_type = request.getfixturevalue("instance_type")
    else:
        return

    if "p5." in test_instance_type:
        image_cuda_version = get_cuda_version_from_tag(ecr_image)
        if processor != "gpu" or Version(image_cuda_version.strip("cu")) < Version("120"):
            pytest.skip("P5 EC2 instance require CUDA 12.0 or higher.")


def _validate_pytorch_framework_version(request, processor, ecr_image, test_name, skip_dict):
    """
    Expected format of skip_dic:
    {
        SpecifierSet("<comparable version string">): ["cpu", "cu118", "cu121"],
    }
    """
    if request.node.get_closest_marker(test_name):
        image_framework, image_framework_version = get_framework_and_version_from_tag(ecr_image)
        image_cuda_version = get_cuda_version_from_tag(ecr_image) if processor == "gpu" else ""

        if image_framework == "pytorch":
            for framework_condition, processor_conditions in skip_dict.items():
                if Version(image_framework_version) in SpecifierSet(framework_condition) and (
                    processor in processor_conditions or image_cuda_version in processor_conditions
                ):
                    return True

    return False


def _get_remote_override_flags():
    try:
        s3_client = boto3.client("s3")
        sts_client = boto3.client("sts")
        account_id = sts_client.get_caller_identity().get("Account")
        result = s3_client.get_object(
            Bucket=f"dlc-cicd-helper-{account_id}", Key="override_tests_flags.json"
        )
        json_content = json.loads(result["Body"].read().decode("utf-8"))
    except ClientError as e:
        logger.warning("ClientError when performing S3/STS operation: {}".format(e))
        json_content = {}
    return json_content


def _is_test_disabled(test_name, build_name, version):
    """
    Expected format of remote_override_flags:
    {
        "CB Project Name for Test Type A": {
            "CodeBuild Resolved Source Version": ["test_type_A_test_function_1", "test_type_A_test_function_2"]
        },
        "CB Project Name for Test Type B": {
            "CodeBuild Resolved Source Version": ["test_type_B_test_function_1", "test_type_B_test_function_2"]
        }
    }

    :param test_name: str Test Function node name (includes parametrized values in string)
    :param build_name: str Build Project name of current execution
    :param version: str Source Version of current execution
    :return: bool True if test is disabled as per remote override, False otherwise
    """
    remote_override_flags = _get_remote_override_flags()
    remote_override_build = remote_override_flags.get(build_name, {})
    if version in remote_override_build:
        return not remote_override_build[version] or any(
            [test_keyword in test_name for test_keyword in remote_override_build[version]]
        )
    return False


@pytest.fixture(autouse=True)
def disable_test(request):
    test_name = request.node.name
    # We do not have a regex pattern to find CB name, which means we must resort to string splitting
    build_arn = os.getenv("CODEBUILD_BUILD_ARN")
    build_name = build_arn.split("/")[-1].split(":")[0] if build_arn else None
    version = os.getenv("CODEBUILD_RESOLVED_SOURCE_VERSION")

    if build_name and version and _is_test_disabled(test_name, build_name, version):
        pytest.skip(f"Skipping {test_name} test because it has been disabled.")


@pytest.fixture(autouse=True)
def disable_nightly_test(request):
    test_name = request.node.name
    if is_nightly_context():
        # default image uri
        image_uri = None
        # get a list of nightly fixtures present for the test function
        nightly_fixtures_present = {
            key: value for (key, value) in NIGHTLY_FIXTURES.items() if key in request.fixturenames
        }
        # get image uri value
        if "ecr_image" in request.fixturenames:
            image_uri = request.getfixturevalue("ecr_image")

        if nightly_fixtures_present and image_uri:
            for _, labels in nightly_fixtures_present.items():
                if not are_fixture_labels_enabled(image_uri, labels):
                    pytest.skip(f"{test_name} will be skipped.")


@pytest.fixture(autouse=True)
def skip_test_successfully_executed_before(request):
    """
    "cache/lastfailed" contains information about failed tests only. We're running SM tests in separate threads for each image.
    So when we retry SM tests, successfully executed tests executed again because pytest doesn't have that info in /.cache.
    But the flag "--last-failed-no-failures all" requires pytest to execute all the available tests.
    The only sign that a test passed last time - lastfailed file exists and the test name isn't in that file.
    The method checks whether lastfailed file exists and the test name is not in it.
    """
    test_name = request.node.name
    lastfailed = request.config.cache.get("cache/lastfailed", None)

    if lastfailed is not None and not any(
        test_name in failed_test_name for failed_test_name in lastfailed.keys()
    ):
        pytest.skip(f"Skipping {test_name} because it was successfully executed for this commit")
