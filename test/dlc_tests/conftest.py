import datetime
import os
import logging
import random
import sys
import re
import time
import uuid
import boto3
import pytest

from packaging.version import Version
from packaging.specifiers import SpecifierSet
from botocore.config import Config
from fabric import Connection

import test.test_utils.ec2 as ec2_utils

from test import test_utils
from test.test_utils import (
    get_framework_and_version_from_tag,
    get_cuda_version_from_tag,
    get_job_type_from_image,
    get_processor_from_image_uri,
    is_tf_version,
    is_above_framework_version,
    is_below_framework_version,
    is_below_cuda_version,
    is_equal_to_framework_version,
    is_ec2_image,
    is_sagemaker_image,
    is_nightly_context,
    DEFAULT_REGION,
    PT_GPU_PY3_BENCHMARK_IMAGENET_AMI_US_EAST_1,
    KEYS_TO_DESTROY_FILE,
    are_efa_tests_disabled,
    get_repository_and_tag_from_image_uri,
    get_ecr_repo_name,
    AL2023_HOME_DIR,
    NightlyFeatureLabel,
    is_mainline_context,
    is_pr_context,
)
from test.test_utils.imageutils import are_image_labels_matched, are_fixture_labels_enabled
from test.test_utils.test_reporting import TestReportGenerator

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
LOGGER.addHandler(logging.StreamHandler(sys.stderr))

ENABLE_IPV6_TESTING = os.getenv("ENABLE_IPV6_TESTING", "false").lower() == "true"

# Immutable constant for framework specific image fixtures
FRAMEWORK_FIXTURES = (
    # ECR repo name fixtures
    # PyTorch
    "pytorch_training",
    "pytorch_training___2__7",
    "pytorch_training___2__6",
    "pytorch_training___2__5",
    "pytorch_training___2__4",
    "pytorch_training___2__3",
    "pytorch_training___2__2",
    "pytorch_training___2__1",
    "pytorch_training___2__0",
    "pytorch_training___1__13",
    "pytorch_training_habana",
    "pytorch_training_arm64",
    "pytorch_training_arm64___2__7",
    "pytorch_inference",
    "pytorch_inference_eia",
    "pytorch_inference_neuron",
    "pytorch_inference_neuronx",
    "pytorch_training_neuronx",
    "pytorch_inference_graviton",
    "pytorch_inference_arm64",
    # TensorFlow
    "tensorflow_training",
    "tensorflow_inference",
    "tensorflow_inference_eia",
    "tensorflow_inference_neuron",
    "tensorflow_inference_neuronx",
    "tensorflow_training_neuron",
    "tensorflow_training_habana",
    "tensorflow_inference_graviton",
    "tensorflow_inference_arm64",
    # MxNET
    "mxnet_training",
    "mxnet_inference",
    "mxnet_inference_eia",
    "mxnet_inference_neuron",
    "mxnet_training_neuron",
    "mxnet_inference_graviton",
    # HuggingFace
    "huggingface_tensorflow_training",
    "huggingface_pytorch_training",
    "huggingface_mxnet_training",
    "huggingface_tensorflow_inference",
    "huggingface_pytorch_inference",
    "huggingface_mxnet_inference",
    "huggingface_tensorflow_trcomp_training",
    "huggingface_pytorch_trcomp_training",
    # Stability
    "stabilityai_pytorch_inference",
    "stabilityai_pytorch_training",
    # PyTorch trcomp
    "pytorch_trcomp_training",
    # Autogluon
    "autogluon_training",
    # Processor fixtures
    "gpu",
    "cpu",
    "eia",
    "neuron",
    "hpu",
    # Architecture
    "graviton",
    "arm64",
    # Job Type fixtures
    "training",
    "inference",
)

# Nightly image fixture dictionary, maps nightly fixtures to set of image labels
NIGHTLY_FIXTURES = {
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
    "feature_torchaudio_present": {
        NightlyFeatureLabel.PYTORCH_INSTALLED.value,
        NightlyFeatureLabel.TORCHAUDIO_INSTALLED.value,
    },
    "feature_torchvision_present": {
        NightlyFeatureLabel.PYTORCH_INSTALLED.value,
        NightlyFeatureLabel.TORCHVISION_INSTALLED.value,
    },
    "feature_torchdata_present": {
        NightlyFeatureLabel.PYTORCH_INSTALLED.value,
        NightlyFeatureLabel.TORCHDATA_INSTALLED.value,
    },
    "feature_s3_plugin_present": {NightlyFeatureLabel.AWS_S3_PLUGIN_INSTALLED.value},
}

# Skip telemetry tests for specific versions
TELEMETRY_SKIP_VERSIONS = {
    "entrypoint": {"pytorch": ["2.4.0", "2.5.1", "2.6.0"], "tensorflow": ["2.18.0"]},
    "bashrc": {"pytorch": ["2.4.0", "2.5.1", "2.6.0"], "tensorflow": ["2.18.0"]},
    "framework": {"pytorch": [""], "tensorflow": ["2.19.0"]},
}


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
def feature_torchaudio_present():
    pass


@pytest.fixture(scope="session")
def feature_torchvision_present():
    pass


@pytest.fixture(scope="session")
def feature_torchdata_present():
    pass


@pytest.fixture(scope="session")
def feature_s3_plugin_present():
    pass


# Ignore container_tests collection, as they will be called separately from test functions
collect_ignore = [os.path.join("container_tests")]


def pytest_addoption(parser):
    default_images = test_utils.get_dlc_images()
    images = default_images.split(" ") if default_images else []
    parser.addoption(
        "--images",
        default=images,
        nargs="+",
        help="Specify image(s) to run",
    )
    parser.addoption(
        "--canary",
        action="store_true",
        default=False,
        help="Run canary tests",
    )
    parser.addoption(
        "--deep-canary",
        action="store_true",
        default=False,
        help="Run Deep Canary tests",
    )
    parser.addoption(
        "--generate-coverage-doc",
        action="store_true",
        default=False,
        help="Generate a test coverage doc",
    )
    parser.addoption(
        "--multinode",
        action="store_true",
        default=False,
        help="Run only multi-node tests",
    )
    parser.addoption(
        "--efa",
        action="store_true",
        default=False,
        help="Run only efa tests",
    )
    parser.addoption(
        "--quick_checks",
        action="store_true",
        default=False,
        help="Run quick check tests",
    )


@pytest.fixture(scope="function")
def num_nodes(request):
    return request.param


@pytest.fixture(scope="function")
def ec2_key_name(request):
    return request.param


@pytest.fixture(scope="function")
def ec2_key_file_name(request):
    return request.param


@pytest.fixture(scope="function")
def ec2_user_name(request):
    return request.param


@pytest.fixture(scope="function")
def ec2_public_ip(request):
    return request.param


@pytest.fixture(scope="function")
def region(request):
    return request.param if hasattr(request, "param") else os.getenv("AWS_REGION", DEFAULT_REGION)


@pytest.fixture(scope="function")
def availability_zone_options(ec2_client, ec2_instance_type, region):
    """
    Parametrize with a reduced list of availability zones for particular instance types for which
    capacity has been reserved in that AZ. For other instance types, parametrize with list of all
    AZs in the region.
    :param ec2_client: boto3 Client for EC2
    :param ec2_instance_type: str instance type for which AZs must be determined
    :param region: str region in which instance must be created
    :return: list of str AZ names
    """
    allowed_availability_zones = None
    if ec2_instance_type in ["p4de.24xlarge"]:
        if region == "us-east-1":
            allowed_availability_zones = ["us-east-1d", "us-east-1c"]
    if ec2_instance_type in ["p4d.24xlarge"]:
        if region == "us-west-2":
            allowed_availability_zones = ["us-west-2b", "us-west-2c"]
    if not allowed_availability_zones:
        allowed_availability_zones = ec2_utils.get_availability_zone_ids(ec2_client)
    return allowed_availability_zones


@pytest.fixture(scope="function")
def ecr_client(region):
    return boto3.client("ecr", region_name=region)


@pytest.fixture(scope="function")
def sts_client(region):
    return boto3.client("sts", region_name=region)


@pytest.fixture(scope="function")
def ec2_client(region):
    return boto3.client("ec2", region_name=region, config=Config(retries={"max_attempts": 10}))


@pytest.fixture(scope="function")
def ec2_resource(region):
    return boto3.resource("ec2", region_name=region, config=Config(retries={"max_attempts": 10}))


def _validate_p4de_usage(request, instance_type):
    if instance_type in ["p4de.24xlarge"]:
        if not request.node.get_closest_marker("allow_p4de_use"):
            pytest.skip("Skip test because p4de instance usage is not allowed for this test")
    return


def _restrict_instance_usage(instance_type):
    restricted_instances = {"c": ["c4"], "m": ["m4"], "p": ["p2"]}

    for instance_serie, instance_list in restricted_instances.items():
        for instance_family in instance_list:
            if f"{instance_family}." in instance_type:
                raise RuntimeError(
                    f"{instance_family.upper()}-family instances are no longer supported in our system."
                    f"Please use a different instance type (i.e. another {instance_serie.upper()} series instance type)."
                )
    return


@pytest.fixture(scope="function")
def ec2_instance_type(request):
    instance_type = request.param if hasattr(request, "param") else "g4dn.xlarge"
    _restrict_instance_usage(instance_type)
    return instance_type


@pytest.fixture(scope="function")
def instance_type(request):
    return request.param if hasattr(request, "param") else "ml.g5.8xlarge"


@pytest.fixture(scope="function")
def ec2_instance_role_name(request):
    return request.param if hasattr(request, "param") else ec2_utils.EC2_INSTANCE_ROLE_NAME


@pytest.fixture(scope="function")
def ec2_instance_ami(request, region):
    return request.param if hasattr(request, "param") else test_utils.get_dlami_id(region)


@pytest.fixture(scope="function")
def ei_accelerator_type(request):
    return request.param if hasattr(request, "param") else None


@pytest.mark.timeout(300)
@pytest.fixture(scope="function")
def efa_ec2_instances(
    request,
    ec2_client,
    ec2_instance_type,
    ec2_instance_role_name,
    ec2_key_name,
    ec2_instance_ami,
    region,
    availability_zone_options,
):
    _validate_p4de_usage(request, ec2_instance_type)
    ec2_key_name = f"{ec2_key_name}-{str(uuid.uuid4())}"
    print(f"Creating instance: CI-CD {ec2_key_name}")
    key_filename = test_utils.generate_ssh_keypair(ec2_client, ec2_key_name)
    print(f"Using AMI for EFA EC2 {ec2_instance_ami}")

    def delete_ssh_keypair():
        if test_utils.is_pr_context():
            test_utils.destroy_ssh_keypair(ec2_client, key_filename)
        else:
            with open(KEYS_TO_DESTROY_FILE, "a") as destroy_keys:
                destroy_keys.write(f"{key_filename}\n")

    request.addfinalizer(delete_ssh_keypair)
    volume_name = "/dev/sda1" if ec2_instance_ami in test_utils.UL_AMI_LIST else "/dev/xvda"

    instance_name_prefix = f"CI-CD {ec2_key_name}"
    ec2_run_instances_definition = {
        "BlockDeviceMappings": [
            {
                "DeviceName": volume_name,
                "Ebs": {
                    "DeleteOnTermination": True,
                    "VolumeSize": 150,
                    "VolumeType": "gp3",
                    "Iops": 3000,
                    "Throughput": 125,
                },
            },
        ],
        "ImageId": ec2_instance_ami,
        "InstanceType": ec2_instance_type,
        "IamInstanceProfile": {"Name": ec2_instance_role_name},
        "KeyName": ec2_key_name,
        "MaxCount": 2,
        "MinCount": 2,
        "TagSpecifications": [
            {"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": instance_name_prefix}]}
        ],
    }
    instances = ec2_utils.launch_efa_instances_with_retry(
        ec2_client,
        ec2_instance_type,
        availability_zone_options,
        ec2_run_instances_definition,
        fn_name=request.node.name,
    )

    def terminate_efa_instances():
        ec2_client.terminate_instances(
            InstanceIds=[instance_info["InstanceId"] for instance_info in instances]
        )

    request.addfinalizer(terminate_efa_instances)

    master_instance_id = instances[0]["InstanceId"]
    ec2_utils.check_instance_state(master_instance_id, state="running", region=region)
    ec2_utils.check_system_state(
        master_instance_id, system_status="ok", instance_status="ok", region=region
    )
    print(f"Master instance {master_instance_id} is ready")

    if len(instances) > 1:
        ec2_utils.create_name_tags_for_instance(
            master_instance_id, f"{instance_name_prefix}_master", region
        )
        for i in range(1, len(instances)):
            worker_instance_id = instances[i]["InstanceId"]
            ec2_utils.create_name_tags_for_instance(
                worker_instance_id, f"{instance_name_prefix}_worker_{i}", region
            )
            ec2_utils.check_instance_state(worker_instance_id, state="running", region=region)
            ec2_utils.check_system_state(
                worker_instance_id, system_status="ok", instance_status="ok", region=region
            )
            print(f"Worker instance {worker_instance_id} is ready")

    num_efa_interfaces = ec2_utils.get_num_efa_interfaces_for_instance_type(
        ec2_instance_type, region=region
    )
    if num_efa_interfaces > 1:
        # p4d instances require attaching elastic ip to connect to them
        elastic_ip_allocation_ids = []
        # create and attach network interfaces and elastic ips to all instances
        for instance in instances:
            instance_id = instance["InstanceId"]

            network_interface_id = ec2_utils.get_network_interface_id(instance_id, region)

            elastic_ip_allocation_id = ec2_utils.attach_elastic_ip(
                network_interface_id, region, ENABLE_IPV6_TESTING
            )
            elastic_ip_allocation_ids.append(elastic_ip_allocation_id)

        def elastic_ips_finalizer():
            ec2_utils.delete_elastic_ips(elastic_ip_allocation_ids, ec2_client)

        request.addfinalizer(elastic_ips_finalizer)

    return_val = [(instance_info["InstanceId"], key_filename) for instance_info in instances]
    LOGGER.info(f"Launched EFA Test instances - {[instance_id for instance_id, _ in return_val]}")

    return return_val


@pytest.fixture(scope="function")
def efa_ec2_connections(request, efa_ec2_instances, ec2_key_name, ec2_instance_type, region):
    """
    Fixture to establish connection with EC2 instance if necessary
    :param request: pytest test request
    :param efa_ec2_instances: efa_ec2_instances pytest fixture
    :param ec2_key_name: unique key name
    :param ec2_instance_type: ec2_instance_type pytest fixture
    :param region: Region where ec2 instance is launched
    :return: Fabric connection object
    """
    master_instance_id, master_instance_pem_file = efa_ec2_instances[0]
    worker_instances = [
        {
            "worker_instance_id": worker_instance_id,
            "worker_instance_pem_file": worker_instance_pem_file,
        }
        for worker_instance_id, worker_instance_pem_file in efa_ec2_instances[1:]
    ]

    user_name = ec2_utils.get_instance_user(master_instance_id, region=region)
    master_public_ip = ec2_utils.get_public_ip(master_instance_id, region)
    LOGGER.info(f"Instance master_ip_address: {master_public_ip}")
    master_connection = Connection(
        user=user_name,
        host=master_public_ip,
        connect_kwargs={"key_filename": [master_instance_pem_file]},
        connect_timeout=18000,
    )

    if ENABLE_IPV6_TESTING:
        master_ipv6_address = ec2_utils.get_ipv6_address_for_eth0(master_instance_id, region)

        if master_ipv6_address:
            master_connection.ipv6_address = master_ipv6_address
            LOGGER.info(f"Master node IPv6 address (eth0): {master_connection.ipv6_address}")
        else:
            raise RuntimeError("IPv6 testing enabled but no IPv6 address found for master node")

    worker_instance_connections = []
    for instance in worker_instances:
        worker_instance_id = instance["worker_instance_id"]
        worker_instance_pem_file = instance["worker_instance_pem_file"]
        worker_public_ip = ec2_utils.get_public_ip(worker_instance_id, region)
        worker_connection = Connection(
            user=user_name,
            host=worker_public_ip,
            connect_kwargs={"key_filename": [worker_instance_pem_file]},
            connect_timeout=18000,
        )

        if ENABLE_IPV6_TESTING:
            worker_ipv6_address = ec2_utils.get_ipv6_address_for_eth0(worker_instance_id, region)

            if worker_ipv6_address:
                worker_connection.ipv6_address = worker_ipv6_address
                LOGGER.info(f"Worker node IPv6 address (eth0): {worker_connection.ipv6_address}")
            else:
                raise RuntimeError("IPv6 testing enabled but no IPv6 address found for worker node")

        worker_instance_connections.append(worker_connection)

    random.seed(f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}")
    unique_id = random.randint(1, 100000)

    artifact_folder = f"{ec2_key_name}-{unique_id}-folder"
    s3_test_artifact_location = test_utils.upload_tests_to_s3(artifact_folder)

    def delete_s3_artifact_copy():
        test_utils.delete_uploaded_tests_from_s3(s3_test_artifact_location)

    request.addfinalizer(delete_s3_artifact_copy)

    master_connection.run("rm -rf $HOME/container_tests")
    master_connection.run(
        f"aws s3 cp --recursive {test_utils.TEST_TRANSFER_S3_BUCKET}/{artifact_folder} $HOME/container_tests --region {test_utils.TEST_TRANSFER_S3_BUCKET_REGION}"
    )
    print(f"Successfully copying {test_utils.TEST_TRANSFER_S3_BUCKET} for master")
    master_connection.run(
        f"mkdir -p $HOME/container_tests/logs && chmod -R +x $HOME/container_tests/*"
    )
    for worker_connection in worker_instance_connections:
        worker_connection.run("rm -rf $HOME/container_tests")
        worker_connection.run(
            f"aws s3 cp --recursive {test_utils.TEST_TRANSFER_S3_BUCKET}/{artifact_folder} $HOME/container_tests --region {test_utils.TEST_TRANSFER_S3_BUCKET_REGION}"
        )
        print(f"Successfully copying {test_utils.TEST_TRANSFER_S3_BUCKET} for worker")
        worker_connection.run(
            f"mkdir -p $HOME/container_tests/logs && chmod -R +x $HOME/container_tests/*"
        )

    return [master_connection, *worker_instance_connections]


@pytest.mark.timeout(300)
@pytest.fixture(scope="function")
def ec2_instance(
    request,
    ec2_client,
    ec2_resource,
    ec2_instance_type,
    ec2_key_name,
    ec2_instance_role_name,
    ec2_instance_ami,
    region,
    ei_accelerator_type,
):
    _validate_p4de_usage(request, ec2_instance_type)

    ec2_key_name = f"{ec2_key_name}-{str(uuid.uuid4())}"
    print(f"Creating instance: CI-CD {ec2_key_name}")
    key_filename = test_utils.generate_ssh_keypair(ec2_client, ec2_key_name)

    def delete_ssh_keypair():
        if test_utils.is_pr_context():
            test_utils.destroy_ssh_keypair(ec2_client, key_filename)
        else:
            with open(KEYS_TO_DESTROY_FILE, "a") as destroy_keys:
                destroy_keys.write(f"{key_filename}\n")

    request.addfinalizer(delete_ssh_keypair)
    print(f"EC2 instance AMI-ID: {ec2_instance_ami}")

    params = {
        "KeyName": ec2_key_name,
        "ImageId": ec2_instance_ami,
        "InstanceType": ec2_instance_type,
        "IamInstanceProfile": {"Name": ec2_instance_role_name},
        "TagSpecifications": [
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": f"CI-CD {ec2_key_name}"}],
            },
        ],
        "MaxCount": 1,
        "MinCount": 1,
    }

    volume_name = "/dev/sda1" if ec2_instance_ami in test_utils.UL_AMI_LIST else "/dev/xvda"

    if (
        "pytorch_training_habana" in request.fixturenames
        or "tensorflow_training_habana" in request.fixturenames
        or "hpu" in request.fixturenames
    ):
        user_data = """#!/bin/bash
        sudo dnf update -y && sudo dnf install -y awscli"""
        params["UserData"] = user_data
        params["BlockDeviceMappings"] = [
            {
                "DeviceName": volume_name,
                "Ebs": {
                    "VolumeSize": 1000,
                },
            }
        ]
    elif (
        (
            ("benchmark" in os.getenv("TEST_TYPE", "UNDEFINED"))
            and (
                ("mxnet_training" in request.fixturenames and "gpu_only" in request.fixturenames)
                or "mxnet_inference" in request.fixturenames
            )
        )
        or (
            "tensorflow_training" in request.fixturenames
            and "gpu_only" in request.fixturenames
            and "horovod" in ec2_key_name
        )
        or (
            "tensorflow_inference" in request.fixturenames
            and (
                "graviton_compatible_only" in request.fixturenames
                or "arm64_compatible_only" in request.fixturenames
            )
        )
        or ("graviton" in request.fixturenames)
        or ("arm64" in request.fixturenames)
    ):
        params["BlockDeviceMappings"] = [
            {
                "DeviceName": volume_name,
                "Ebs": {
                    "VolumeSize": 300,
                },
            }
        ]
    elif is_neuron_image(request.fixturenames):
        params["BlockDeviceMappings"] = [
            {
                "DeviceName": volume_name,
                "Ebs": {
                    "VolumeSize": 512,
                },
            }
        ]
    else:
        params["BlockDeviceMappings"] = [
            {
                "DeviceName": volume_name,
                "Ebs": {
                    "VolumeSize": 150,
                },
            }
        ]

    # For TRN1 since we are using a private AMI that has some BERT data/tests, have a bifgger volume size
    # Once use DLAMI, this can be removed
    if ec2_instance_type == "trn1.32xlarge" or ec2_instance_type == "trn1.2xlarge":
        params["BlockDeviceMappings"] = [
            {
                "DeviceName": volume_name,
                "Ebs": {
                    "VolumeSize": 1024,
                },
            }
        ]

    # For neuron the current DLAMI does not have the latest drivers and compatibility
    # is failing. So reinstall the latest neuron driver
    if "pytorch_inference_neuronx" in request.fixturenames:
        params["BlockDeviceMappings"] = [
            {
                "DeviceName": volume_name,
                "Ebs": {
                    "VolumeSize": 1024,
                },
            }
        ]

    availability_zone_options = None
    if ei_accelerator_type:
        params["ElasticInferenceAccelerators"] = [{"Type": ei_accelerator_type, "Count": 1}]
        availability_zones = {
            "us-west-2": ["us-west-2a", "us-west-2b", "us-west-2c"],
            "us-east-1": ["us-east-1a", "us-east-1b", "us-east-1c"],
        }
        availability_zone_options = availability_zones[region]
    instances = ec2_utils.launch_instances_with_retry(
        ec2_resource=ec2_resource,
        availability_zone_options=availability_zone_options,
        ec2_create_instances_definition=params,
        ec2_client=ec2_client,
        fn_name=request.node.name,
    )
    instance_id = instances[0].id

    # Define finalizer to terminate instance after this fixture completes
    def terminate_ec2_instance():
        ec2_client.terminate_instances(InstanceIds=[instance_id])

    request.addfinalizer(terminate_ec2_instance)

    ec2_utils.check_instance_state(instance_id, state="running", region=region)
    ec2_utils.check_system_state(
        instance_id, system_status="ok", instance_status="ok", region=region
    )
    return instance_id, key_filename


def is_neuron_image(fixtures):
    """
    Returns true if a neuron fixture is present in request.fixturenames
    :param request.fixturenames: active fixtures in the request
    :return: bool
    """
    neuron_fixtures = [  # inference
        "tensorflow_inference_neuron",
        "tensorflow_inference_neuronx",
        "mxnet_inference_neuron",
        "pytorch_inference_neuron",
        "pytorch_inference_neuronx"
        # training
        "tensorflow_training_neuron",
        "mxnet_training_neuron",
        "pytorch_training_neuronx",
    ]

    for fixture in neuron_fixtures:
        if fixture in fixtures:
            return True
    return False


@pytest.fixture(scope="function")
def ec2_connection(request, ec2_instance, ec2_key_name, ec2_instance_type, region):
    """
    Fixture to establish connection with EC2 instance if necessary
    :param request: pytest test request
    :param ec2_instance: ec2_instance pytest fixture
    :param ec2_key_name: unique key name
    :param ec2_instance_type: ec2_instance_type pytest fixture
    :param region: Region where ec2 instance is launched
    :return: Fabric connection object
    """
    instance_id, instance_pem_file = ec2_instance
    ip_address = ec2_utils.get_public_ip(instance_id, region=region)
    LOGGER.info(f"Instance ip_address: {ip_address}")
    user = ec2_utils.get_instance_user(instance_id, region=region)

    LOGGER.info(f"Connecting to {user}@{ip_address}")

    conn = Connection(
        user=user,
        host=ip_address,
        connect_kwargs={"key_filename": [instance_pem_file]},
        connect_timeout=18000,
    )

    random.seed(f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}")
    unique_id = random.randint(1, 100000)

    artifact_folder = f"{ec2_key_name}-{unique_id}-folder"
    s3_test_artifact_location = test_utils.upload_tests_to_s3(artifact_folder)

    def delete_s3_artifact_copy():
        test_utils.delete_uploaded_tests_from_s3(s3_test_artifact_location)

    request.addfinalizer(delete_s3_artifact_copy)

    python_version = "3.9"
    if is_neuron_image(request.fixturenames):
        # neuron still support tf1.15 and that is only there in py37 and less.
        # so use python3.7 for neuron
        python_version = "3.7"
    ec2_utils.install_python_in_instance(conn, python_version=python_version)

    conn.run(
        f"aws s3 cp --recursive {test_utils.TEST_TRANSFER_S3_BUCKET}/{artifact_folder} $HOME/container_tests"
    )
    conn.run(f"mkdir -p $HOME/container_tests/logs && chmod -R +x $HOME/container_tests/*")

    # Log into ECR if we are in canary context
    if test_utils.is_canary_context():
        public_registry = test_utils.PUBLIC_DLC_REGISTRY
        test_utils.login_to_ecr_registry(conn, public_registry, region)

    return conn


@pytest.fixture(scope="function")
def upload_habana_test_artifact(request, ec2_connection):
    """
    Fixture to upload the habana test repo to ec2 instance
    :param request: pytest test request
    :param ec2_connection: fabric connection object
    :return: None
    """
    habana_test_repo = "gaudi-test-suite.tar.gz"
    ec2_connection.put(habana_test_repo, f"{AL2023_HOME_DIR}")
    ec2_connection.run(f"tar -xvf {habana_test_repo}")


@pytest.fixture(scope="function")
def existing_ec2_instance_connection(request, ec2_key_file_name, ec2_user_name, ec2_public_ip):
    """
    Fixture to establish connection with EC2 instance if necessary
    :param request: pytest test request
    :param ec2_key_file_name: ec2 key file name
    :param ec2_user_name: username of the ec2 instance to login
    :param ec2_public_ip: public ip address of the instance
    :return: Fabric connection object
    """
    conn = Connection(
        user=ec2_user_name,
        host=ec2_public_ip,
        connect_kwargs={"key_filename": [ec2_key_file_name]},
    )

    random.seed(f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}")
    unique_id = random.randint(1, 100000)
    ec2_key_name = ec2_public_ip.split(".")[0]
    artifact_folder = f"{ec2_key_name}-{unique_id}-folder"
    s3_test_artifact_location = test_utils.upload_tests_to_s3(artifact_folder)

    def delete_s3_artifact_copy():
        test_utils.delete_uploaded_tests_from_s3(s3_test_artifact_location)

    request.addfinalizer(delete_s3_artifact_copy)

    conn.run(
        f"aws s3 cp --recursive {test_utils.TEST_TRANSFER_S3_BUCKET}/{artifact_folder} $HOME/container_tests"
    )
    conn.run(f"mkdir -p $HOME/container_tests/logs && chmod -R +x $HOME/container_tests/*")

    return conn


@pytest.fixture(autouse=True)
def skip_trcomp_containers(request):
    if "training" in request.fixturenames:
        img_uri = request.getfixturevalue("training")
    elif "pytorch_training" in request.fixturenames:
        img_uri = request.getfixturevalue("pytorch_training")
    else:
        return
    if "trcomp" in img_uri:
        pytest.skip("Skipping training compiler integrated container with tag {}".format(img_uri))


@pytest.fixture(autouse=True)
def skip_inductor_test(request):
    if "training" in request.fixturenames:
        img_uri = request.getfixturevalue("training")
    elif "pytorch_training" in request.fixturenames:
        img_uri = request.getfixturevalue("pytorch_training")
    else:
        return
    _, fw_ver = get_framework_and_version_from_tag(img_uri)
    if request.node.get_closest_marker("skip_inductor_test"):
        if Version(fw_ver) < Version("2.0.0"):
            pytest.skip(
                f"SM inductor test only support PT2.0 and above, skipping this container with tag {fw_ver}"
            )


@pytest.fixture(autouse=True)
def skip_torchdata_test(request):
    lookup_fixtures = ["training", "pytorch_training", "inference", "pytorch_inference"]
    image_uri = ""

    for lookup_fixture in lookup_fixtures:
        if lookup_fixture in request.fixturenames:
            image_uri = request.getfixturevalue(lookup_fixture)
            break

    if not image_uri:
        return

    skip_dict = {
        ">2.1.1": ["cpu", "cu118", "cu121"],
        ">=2.4,<2.6": ["cpu", "cu124"],
    }
    if _validate_pytorch_framework_version(request, image_uri, "skip_torchdata_test", skip_dict):
        pytest.skip(
            f"Torchdata has paused development as of July 2023 and the latest compatible PyTorch version is 2.1.1."
            f"For more information, see https://github.com/pytorch/data/issues/1196."
            f"Skipping test"
            f"Start from PyTorch 2.6, Torchdata is added back to the container."
        )


@pytest.fixture(autouse=True)
def skip_smdebug_v1_test(request):
    """Skip SM Debugger and Profiler tests due to v1 deprecation for PyTorch 2.0.1 and above frameworks."""
    if "training" in request.fixturenames:
        image_uri = request.getfixturevalue("training")
    elif "pytorch_training" in request.fixturenames:
        image_uri = request.getfixturevalue("pytorch_training")
    else:
        return

    skip_dict = {
        "==2.0.*": ["cu121"],
        ">=2.1,<2.4": ["cpu", "cu121"],
        ">=2.4,<2.6": ["cpu", "cu124"],
        ">=2.6,<2.7.1": ["cpu", "cu126"],
        ">=2.7.1,<2.8": ["cpu", "cu128"],
    }
    if _validate_pytorch_framework_version(request, image_uri, "skip_smdebug_v1_test", skip_dict):
        pytest.skip(f"SM Profiler v1 is on path for deprecation, skipping test")


@pytest.fixture(autouse=True)
def skip_dgl_test(request):
    """Start from PyTorch 2.0.1 framework, DGL binaries are not installed in DLCs by default and will be added in per customer ask.
    The test condition should be modified appropriately and `skip_dgl_test` pytest mark should be removed from dgl tests
    when the binaries are added in.
    """
    if "training" in request.fixturenames:
        image_uri = request.getfixturevalue("training")
    elif "pytorch_training" in request.fixturenames:
        image_uri = request.getfixturevalue("pytorch_training")
    else:
        return

    skip_dict = {
        "==2.0.*": ["cu121"],
        ">=2.1,<2.4": ["cpu", "cu121"],
        ">=2.4,<2.6": ["cpu", "cu124"],
        ">=2.6,<2.7.1": ["cpu", "cu126"],
        ">=2.7.1,<2.8": ["cpu", "cu128"],
    }
    if _validate_pytorch_framework_version(request, image_uri, "skip_dgl_test", skip_dict):
        pytest.skip(f"DGL binaries are removed, skipping test")


@pytest.fixture(autouse=True)
def skip_efa_tests(request):
    efa_tests = [mark for mark in request.node.iter_markers(name="efa")]

    if efa_tests and are_efa_tests_disabled():
        pytest.skip("Skipping EFA tests as EFA tests are disabled.")


@pytest.fixture(autouse=True)
def skip_p5_tests(request, ec2_instance_type):
    allowed_p5_fixtures = (
        "gpu",
        "image",
        "training",
        "pytorch_training",
        r"pytorch_training___\S+",
    )
    image_uri = None

    if "p5." in ec2_instance_type:
        p5_fixture_stack = list(allowed_p5_fixtures)
        while p5_fixture_stack and not image_uri:
            fixture_name = p5_fixture_stack.pop()
            if fixture_name in request.fixturenames:
                image_uri = request.getfixturevalue(fixture_name)
            # Handle fixture names that include tag as regex
            elif "___" in fixture_name:
                regex = re.compile(fixture_name)
                matches = list(filter(regex.match, request.fixturenames))
                image_uri = request.getfixturevalue(matches[0]) if matches else None

        if not image_uri:
            pytest.skip(
                f"Current image doesn't support P5 EC2 instance. Must be of fixture name {allowed_p5_fixtures}"
            )

        framework, image_framework_version = get_framework_and_version_from_tag(image_uri)
        if "pytorch" not in framework:
            pytest.skip("Current image doesn't support P5 EC2 instance.")
        image_processor = get_processor_from_image_uri(image_uri)
        image_cuda_version = get_cuda_version_from_tag(image_uri)
        if image_processor != "gpu" or Version(image_cuda_version.strip("cu")) < Version("120"):
            pytest.skip("Images using less than CUDA 12.0 doesn't support P5 EC2 instance.")


@pytest.fixture(autouse=True)
def skip_serialized_release_pt_test(request):
    if "training" in request.fixturenames:
        image_uri = request.getfixturevalue("training")
    elif "pytorch_training" in request.fixturenames:
        image_uri = request.getfixturevalue("pytorch_training")
    else:
        return

    skip_dict = {
        "==1.13.*": ["cpu", "cu117"],
        ">=2.1,<2.4": ["cpu", "cu121"],
        ">=2.4,<2.6": ["cpu", "cu124"],
        ">=2.6,<2.7.1": ["cpu", "cu126"],
        ">=2.7.1,<2.8": ["cpu", "cu128"],
    }
    if _validate_pytorch_framework_version(
        request, image_uri, "skip_serialized_release_pt_test", skip_dict
    ):
        pytest.skip(
            f"Skip test for {image_uri} given that the image is being tested in serial execution."
        )


@pytest.fixture(autouse=True)
def skip_telemetry_tests(request):
    """Skip specific telemetry tests based on test name and image version"""
    test_name = request.node.name.lower()

    if "telemetry_entrypoint" in test_name:
        _check_telemetry_skip(request, "entrypoint")
    elif "telemetry_bashrc" in test_name:
        _check_telemetry_skip(request, "bashrc")
    elif "telemetry_framework" in test_name:
        _check_telemetry_skip(request, "framework")


def _get_telemetry_image_info(request):
    """Helper function to get image URI and framework info from fixtures."""
    telemetry_framework_fixtures = [
        "pytorch_training",
        "tensorflow_training",
        "tensorflow_inference",
        "pytorch_inference",
        "pytorch_inference_arm64",
        "pytorch_training_arm64",
        "tensorflow_inference_arm64",
    ]

    for fixture_name in telemetry_framework_fixtures:
        if fixture_name in request.fixturenames:
            img_uri = request.getfixturevalue(fixture_name)
            image_framework, image_framework_version = get_framework_and_version_from_tag(img_uri)
            return image_framework, image_framework_version
    return None, None


def _check_telemetry_skip(request, test_type):
    """Common logic for skipping telemetry tests."""
    if test_type not in TELEMETRY_SKIP_VERSIONS:
        return
    image_framework, image_framework_version = _get_telemetry_image_info(request)
    if not image_framework:
        return
    if image_framework not in TELEMETRY_SKIP_VERSIONS[test_type]:
        return

    if image_framework_version in TELEMETRY_SKIP_VERSIONS[test_type][image_framework]:
        pytest.skip(
            f"Telemetry {test_type} test is not supported for "
            f"{image_framework} version {image_framework_version}"
        )


def _validate_pytorch_framework_version(request, image_uri, test_name, skip_dict):
    """
    Expected format of skip_dic:
    {
        SpecifierSet("<comparable version string">): ["cpu", "cu118", "cu121"],
    }
    """
    if request.node.get_closest_marker(test_name):
        image_framework, image_framework_version = get_framework_and_version_from_tag(image_uri)
        image_processor = get_processor_from_image_uri(image_uri)
        image_cuda_version = (
            get_cuda_version_from_tag(image_uri) if image_processor == "gpu" else ""
        )

        if image_framework == "pytorch":
            for framework_condition, processor_conditions in skip_dict.items():
                if Version(image_framework_version) in SpecifierSet(framework_condition) and (
                    image_processor in processor_conditions
                    or image_cuda_version in processor_conditions
                ):
                    return True

    return False


@pytest.fixture(scope="session")
def telemetry():
    """
    Telemetry tests are run in ec2 job in PR context but will run in its own job in MAINLINE context.
    This fixture ensures that only telemetry tests are run in the `telemetry` job in the MAINLINE context.
    """
    is_telemetry_test_job = os.getenv("TEST_TYPE") == "telemetry"
    if is_mainline_context() and not is_telemetry_test_job:
        pytest.skip(
            f"Test in not running in `telemetry` job in the pipeline context, Skipping current test."
        )


@pytest.fixture(scope="session")
def security_sanity():
    """
    Skip test if job type is not `security_sanity` in either PR or Pipeline contexts.
    Otherwise, sanity tests can run as usual in Canary/Deep Canary contexts.
    Each sanity tests should only have either `security_sanity` or `functionality_sanity` fixtures.
    Both should not be used at the same time. If neither are used, the test will run in both jobs.
    """
    is_security_sanity_test_job = os.getenv("TEST_TYPE") == "security_sanity"
    if (is_pr_context() or is_mainline_context()) and not is_security_sanity_test_job:
        pytest.skip(
            f"Test in not running in `security_sanity` test type job, Skipping current test."
        )


@pytest.fixture(scope="session")
def functionality_sanity():
    """
    Skip test if job type is not `functionality_sanity` in either PR or Pipeline contexts.
    Otherwise, sanity tests can run as usual in Canary/Deep Canary contexts.
    Each sanity tests should only have either `security_sanity` or `functionality_sanity` fixtures.
    Both should not be used at the same time. If neither are used, the test will run in both jobs.
    """
    is_functionality_sanity_test_job = os.getenv("TEST_TYPE") == "functionality_sanity"
    if (is_pr_context() or is_mainline_context()) and not is_functionality_sanity_test_job:
        pytest.skip(
            f"Test in not running in `functionality_sanity` test type job, Skipping current test."
        )


@pytest.fixture(scope="session")
def dlc_images(request):
    return request.config.getoption("--images")


@pytest.fixture(scope="session")
def pull_images(docker_client, dlc_images):
    for image in dlc_images:
        docker_client.images.pull(image)


@pytest.fixture(scope="session")
def non_huggingface_only():
    pass


@pytest.fixture(scope="session")
def non_pytorch_trcomp_only():
    pass


@pytest.fixture(scope="session")
def training_compiler_only():
    pass


@pytest.fixture(scope="session")
def non_autogluon_only():
    pass


@pytest.fixture(scope="session")
def cpu_only():
    pass


@pytest.fixture(scope="session")
def gpu_only():
    pass


@pytest.fixture(scope="session")
def x86_compatible_only():
    pass


@pytest.fixture(scope="session")
def graviton_compatible_only():
    pass


@pytest.fixture(scope="session")
def arm64_compatible_only():
    pass


@pytest.fixture(scope="session")
def sagemaker():
    pass


@pytest.fixture(scope="session")
def sagemaker_only():
    pass


@pytest.fixture(scope="session")
def py3_only():
    pass


@pytest.fixture(scope="session")
def example_only():
    pass


@pytest.fixture(scope="session")
def huggingface_only():
    pass


@pytest.fixture(scope="session")
def huggingface():
    pass


@pytest.fixture(scope="session")
def stabilityai():
    pass


@pytest.fixture(scope="session")
def tf2_only():
    pass


@pytest.fixture(scope="session")
def tf23_and_above_only():
    pass


@pytest.fixture(scope="session")
def tf24_and_above_only():
    pass


@pytest.fixture(scope="session")
def tf25_and_above_only():
    pass


@pytest.fixture(scope="session")
def tf21_and_above_only():
    pass


@pytest.fixture(scope="session")
def below_tf213_only():
    pass


@pytest.fixture(scope="session")
def below_tf216_only():
    pass


@pytest.fixture(scope="session")
def below_tf218_only():
    pass


@pytest.fixture(scope="session")
def below_tf219_only():
    pass


@pytest.fixture(scope="session")
def below_cuda129_only():
    pass


@pytest.fixture(scope="session")
def skip_tf216():
    pass


@pytest.fixture(scope="session")
def skip_tf218():
    pass


@pytest.fixture(scope="session")
def skip_tf219():
    pass


@pytest.fixture(scope="session")
def mx18_and_above_only():
    pass


@pytest.fixture(scope="session")
def skip_pt200():
    pass


@pytest.fixture(scope="session")
def pt200_and_below_only():
    pass


@pytest.fixture(scope="session")
def pt113_and_above_only():
    pass


@pytest.fixture(scope="session")
def pt201_and_above_only():
    pass


@pytest.fixture(scope="session")
def below_pt113_only():
    pass


@pytest.fixture(scope="session")
def pt111_and_above_only():
    pass


@pytest.fixture(scope="session")
def skip_pt110():
    pass


@pytest.fixture(scope="session")
def pt21_and_above_only():
    pass


@pytest.fixture(scope="session")
def pt18_and_above_only():
    pass


@pytest.fixture(scope="session")
def pt17_and_above_only():
    pass


@pytest.fixture(scope="session")
def pt16_and_above_only():
    pass


@pytest.fixture(scope="session")
def pt15_and_above_only():
    pass


@pytest.fixture(scope="session")
def pt14_and_above_only():
    pass


@pytest.fixture(scope="session")
def outside_versions_skip():
    def _outside_versions_skip(img_uri, start_ver, end_ver):
        """
        skip test if the image framework versios is not within the (start_ver, end_ver) range
        """
        _, image_framework_version = get_framework_and_version_from_tag(img_uri)
        if Version(start_ver) > Version(image_framework_version) or Version(end_ver) < Version(
            image_framework_version
        ):
            pytest.skip(
                f"test has gone out of support, supported version range >{start_ver},<{end_ver}"
            )

    return _outside_versions_skip


@pytest.fixture(scope="session")
def version_skip():
    def _version_skip(img_uri, ver):
        """
        skip test if the image framework versios is not within the (start_ver, end_ver) range
        """
        _, image_framework_version = get_framework_and_version_from_tag(img_uri)
        if Version(ver) == Version(image_framework_version):
            pytest.skip(f"test is not supported for version {ver}")

    return _version_skip


def cuda_version_within_limit(metafunc_obj, image):
    """
    Test all pytest fixtures for CUDA version limits, and return True if all requirements are satisfied

    :param metafunc_obj: pytest metafunc object from which fixture names used by test function will be obtained
    :param image: Image URI for which the validation must be performed
    :return: True if all validation succeeds, else False
    """
    cuda129_requirement_failed = (
        "below_cuda129_only" in metafunc_obj.fixturenames
        and not is_below_cuda_version("12.9", image)
    )
    if cuda129_requirement_failed:
        return False
    return True


def framework_version_within_limit(metafunc_obj, image):
    """
    Test all pytest fixtures for TensorFlow version limits, and return True if all requirements are satisfied

    :param metafunc_obj: pytest metafunc object from which fixture names used by test function will be obtained
    :param image: Image URI for which the validation must be performed
    :return: True if all validation succeeds, else False
    """
    image_framework_name, _ = get_framework_and_version_from_tag(image)
    if image_framework_name in ("tensorflow", "huggingface_tensorflow_trcomp"):
        tf2_requirement_failed = "tf2_only" in metafunc_obj.fixturenames and not is_tf_version(
            "2", image
        )
        tf25_requirement_failed = (
            "tf25_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("2.5", image, image_framework_name)
        )
        tf24_requirement_failed = (
            "tf24_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("2.4", image, image_framework_name)
        )
        tf23_requirement_failed = (
            "tf23_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("2.3", image, image_framework_name)
        )
        tf21_requirement_failed = (
            "tf21_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("2.1", image, image_framework_name)
        )
        tf213_requirement_failed = (
            "below_tf213_only" in metafunc_obj.fixturenames
            and not is_below_framework_version("2.13", image, image_framework_name)
        )
        tf216_requirement_failed = (
            "below_tf216_only" in metafunc_obj.fixturenames
            and not is_below_framework_version("2.16", image, image_framework_name)
        )
        tf218_requrement_failed = (
            "below_tf218_only" in metafunc_obj.fixturenames
            and not is_below_framework_version("2.18", image, image_framework_name)
        )
        tf219_requrement_failed = (
            "below_tf219_only" in metafunc_obj.fixturenames
            and not is_below_framework_version("2.19", image, image_framework_name)
        )
        not_tf216_requirement_failed = (
            "skip_tf216" in metafunc_obj.fixturenames
            and is_equal_to_framework_version("2.16.*", image, image_framework_name)
        )
        not_tf218_requirement_failed = (
            "skip_tf218" in metafunc_obj.fixturenames
            and is_equal_to_framework_version("2.18.*", image, image_framework_name)
        )
        not_tf219_requirement_failed = (
            "skip_tf219" in metafunc_obj.fixturenames
            and is_equal_to_framework_version("2.19.*", image, image_framework_name)
        )
        if (
            tf2_requirement_failed
            or tf21_requirement_failed
            or tf24_requirement_failed
            or tf25_requirement_failed
            or tf23_requirement_failed
            or tf213_requirement_failed
            or tf216_requirement_failed
            or tf218_requrement_failed
            or tf219_requrement_failed
            or not_tf216_requirement_failed
            or not_tf218_requirement_failed
            or not_tf219_requirement_failed
        ):
            return False
    if image_framework_name == "mxnet":
        mx18_requirement_failed = (
            "mx18_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("1.8", image, "mxnet")
        )
        if mx18_requirement_failed:
            return False
    if image_framework_name in ("pytorch", "huggingface_pytorch_trcomp", "pytorch_trcomp"):
        pt20_and_below_requirement_failed = (
            "pt200_and_below_only" in metafunc_obj.fixturenames
            and is_above_framework_version("2.0.0", image, image_framework_name)
        )
        not_pt200_requirement_failed = (
            "skip_pt200" in metafunc_obj.fixturenames
            and is_equal_to_framework_version("2.0.0", image, image_framework_name)
        )
        pt113_requirement_failed = (
            "pt113_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("1.13", image, image_framework_name)
        )
        below_pt113_requirement_failed = (
            "below_pt113_only" in metafunc_obj.fixturenames
            and not is_below_framework_version("1.13", image, image_framework_name)
        )
        pt111_requirement_failed = (
            "pt111_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("1.11", image, image_framework_name)
        )
        not_pt110_requirement_failed = (
            "skip_pt110" in metafunc_obj.fixturenames
            and is_equal_to_framework_version("1.10.*", image, image_framework_name)
        )
        pt21_requirement_failed = (
            "pt21_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("2.1", image, image_framework_name)
        )
        pt18_requirement_failed = (
            "pt18_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("1.8", image, image_framework_name)
        )
        pt17_requirement_failed = (
            "pt17_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("1.7", image, image_framework_name)
        )
        pt16_requirement_failed = (
            "pt16_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("1.6", image, image_framework_name)
        )
        pt15_requirement_failed = (
            "pt15_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("1.5", image, image_framework_name)
        )
        pt14_requirement_failed = (
            "pt14_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("1.4", image, image_framework_name)
        )
        pt201_requirement_failed = (
            "pt201_and_above_only" in metafunc_obj.fixturenames
            and is_below_framework_version("2.0.1", image, image_framework_name)
        )
        if (
            pt20_and_below_requirement_failed
            or not_pt200_requirement_failed
            or pt113_requirement_failed
            or below_pt113_requirement_failed
            or pt111_requirement_failed
            or not_pt110_requirement_failed
            or pt21_requirement_failed
            or pt18_requirement_failed
            or pt17_requirement_failed
            or pt16_requirement_failed
            or pt15_requirement_failed
            or pt14_requirement_failed
            or pt201_requirement_failed
        ):
            return False
    return True


def pytest_configure(config):
    # register canary marker
    config.addinivalue_line(
        "markers", "canary(message): mark test to run as a part of canary tests."
    )
    config.addinivalue_line(
        "markers", "quick_checks(message): mark test to run as a part of quick check tests."
    )
    config.addinivalue_line(
        "markers", "integration(ml_integration): mark what the test is testing."
    )
    config.addinivalue_line("markers", "model(model_name): name of the model being tested")
    config.addinivalue_line(
        "markers", "multinode(num_instances): number of instances the test is run on, if not 1"
    )
    config.addinivalue_line(
        "markers", "processor(cpu/gpu/eia/hpu): explicitly mark which processor is used"
    )
    config.addinivalue_line("markers", "efa(): explicitly mark to run efa tests")
    config.addinivalue_line(
        "markers", "allow_p4de_use(): explicitly mark to allow test to use p4de instance types"
    )
    config.addinivalue_line("markers", "p3(): choose trcomp perf tests running on p3 instance type")
    config.addinivalue_line(
        "markers", "single_gpu(): choose trcomp perf tests that run on single-gpu instance types"
    )
    config.addinivalue_line("markers", "neuronx_test(): mark as neuronx integration test")
    config.addinivalue_line(
        "markers", "skip_torchdata_test(): mark test to skip due to dlc being incompatible"
    )
    config.addinivalue_line(
        "markers", "skip_smdebug_v1_test(): mark test to skip due to dlc being incompatible"
    )
    config.addinivalue_line(
        "markers", "skip_dgl_test(): mark test to skip due to dlc being incompatible"
    )
    config.addinivalue_line(
        "markers", "skip_inductor_test(): mark test to skip due to dlc being incompatible"
    )
    config.addinivalue_line("markers", "skip_trcomp_containers(): mark test to skip on trcomp dlcs")
    config.addinivalue_line("markers", "deep_canary(): explicitly mark to run as deep canary test")
    config.addinivalue_line("markers", "team(team_name): mark tests that belong to a team")
    config.addinivalue_line(
        "markers", "skip_serialized_release_pt_test(): mark to skip test included in serial testing"
    )


def pytest_runtest_setup(item):
    """
    Handle custom markers and options
    """
    # Handle quick check tests
    quick_checks_opts = [mark for mark in item.iter_markers(name="quick_checks")]

    # On PR, skip quick check tests unless we are on quick_checks job
    test_type = os.getenv("TEST_TYPE", "UNDEFINED")
    quick_checks_test_type = "quick_checks"
    if test_type != quick_checks_test_type and test_utils.is_pr_context():
        if quick_checks_opts:
            pytest.skip(
                f"Skipping quick check tests on PR, since test type is {test_type}, and not {quick_checks_test_type}"
            )

    # If we have enabled the quick_checks flag, we expect to only run tests marked as quick_check
    if item.config.getoption("--quick_checks"):
        if not quick_checks_opts:
            pytest.skip("Skipping non-quick-check tests")

    # Handle canary test conditional skipping
    if item.config.getoption("--canary"):
        canary_opts = [mark for mark in item.iter_markers(name="canary")]
        if not canary_opts:
            pytest.skip("Skipping non-canary tests")

    if item.config.getoption("--deep-canary"):
        deep_canary_opts = [mark for mark in item.iter_markers(name="deep_canary")]
        if not deep_canary_opts:
            pytest.skip("Skipping non-deep-canary tests")

    # Handle multinode conditional skipping
    if item.config.getoption("--multinode"):
        multinode_opts = [mark for mark in item.iter_markers(name="multinode")]
        if not multinode_opts:
            pytest.skip("Skipping non-multinode tests")

    # Handle efa conditional skipping
    if item.config.getoption("--efa"):
        efa_tests = [mark for mark in item.iter_markers(name="efa")]
        if not efa_tests:
            pytest.skip("Skipping non-efa tests")


def pytest_collection_modifyitems(session, config, items):
    for item in items:
        print(f"item {item}")
        for marker in item.iter_markers(name="team"):
            print(f"item {marker}")
            team_name = marker.args[0]
            item.user_properties.append(("team_marker", team_name))
            print(f"item.user_properties {item.user_properties}")

    if config.getoption("--generate-coverage-doc"):
        report_generator = TestReportGenerator(items)
        report_generator.generate_coverage_doc()
        report_generator.generate_sagemaker_reports()


def generate_unique_values_for_fixtures(
    metafunc_obj, images_to_parametrize, values_to_generate_for_fixture
):
    """
    Take a dictionary (values_to_generate_for_fixture), that maps a fixture name used in a test function to another
    fixture that needs to be parametrized, and parametrize to create unique resources for a test.

    :param metafunc_obj: pytest metafunc object
    :param images_to_parametrize: <list> list of image URIs which are used in a test
    :param values_to_generate_for_fixture: <dict> Mapping of "Fixture used" -> "Fixture to be parametrized"
    :return: <dict> Mapping of "Fixture to be parametrized" -> "Unique values for fixture to be parametrized"
    """
    job_type_map = {"training": "tr", "inference": "inf"}
    framework_name_map = {
        "tensorflow": "tf",
        "mxnet": "mx",
        "pytorch": "pt",
        "huggingface_pytorch": "hf-pt",
        "huggingface_tensorflow": "hf-tf",
        "huggingface_pytorch_trcomp": "hf-pt-trc",
        "huggingface_tensorflow_trcomp": "hf-tf-trc",
        "pytorch_trcomp": "pt-trc",
        "autogluon": "ag",
    }
    fixtures_parametrized = {}
    if images_to_parametrize:
        for key, new_fixture_name in values_to_generate_for_fixture.items():
            if key in metafunc_obj.fixturenames:
                fixtures_parametrized[new_fixture_name] = []
                for index, image in enumerate(images_to_parametrize):
                    # Tag fixtures with EC2 instance types if env variable is present
                    allowed_processors = ("gpu", "cpu", "eia", "neuronx", "neuron", "hpu")
                    instance_tag = ""
                    for processor in allowed_processors:
                        if processor in image:
                            if "graviton" in image:
                                instance_type_env = (
                                    f"EC2_{processor.upper()}_GRAVITON_INSTANCE_TYPE"
                                )
                            elif "arm64" in image:
                                instance_type_env = f"EC2_{processor.upper()}_ARM64_INSTANCE_TYPE"
                            else:
                                instance_type_env = f"EC2_{processor.upper()}_INSTANCE_TYPE"
                            instance_type = os.getenv(instance_type_env)
                            if instance_type:
                                instance_tag = f"-{instance_type.replace('.', '-')}"
                                break

                    image_tag = image.split(":")[-1].replace(".", "-")

                    framework, _ = get_framework_and_version_from_tag(image)

                    job_type = get_job_type_from_image(image)

                    fixtures_parametrized[new_fixture_name].append(
                        (
                            image,
                            f"{metafunc_obj.function.__name__}-{framework_name_map.get(framework)}-"
                            f"{job_type_map.get(job_type)}-{image_tag}-"
                            f"{os.getenv('CODEBUILD_RESOLVED_SOURCE_VERSION')}-{index}{instance_tag}",
                        )
                    )
    return fixtures_parametrized


def lookup_condition(lookup, image):
    """
    Return true if the ECR repo name ends with the lookup or lookup contains job type or device type part of the image uri.
    """
    # Extract ecr repo name from the image and check if it exactly matches the lookup (fixture name)
    repo_name = get_ecr_repo_name(image)

    # If lookup includes tag, check that we match beginning of string
    if ":" in lookup and ":" in image:
        _, tag = get_repository_and_tag_from_image_uri(image)
        generic_repo_tag = f"{repo_name}:{tag}".replace("pr-", "").replace("beta-", "")
        if re.match(rf"^{lookup}", generic_repo_tag):
            return True

    job_types = (
        "training",
        "inference",
    )
    device_types = ("cpu", "gpu", "eia", "neuronx", "neuron", "hpu", "graviton", "arm64")

    if not repo_name.endswith(lookup):
        if (lookup in job_types or lookup in device_types) and lookup in image:
            return True
        # Pytest does not allow usage of fixtures, specially dynamically loaded fixtures into pytest.mark.parametrize
        # See https://github.com/pytest-dev/pytest/issues/349.
        # Hence, explicitly setting the below fixtues to allow trcomp images to run on EC2 test
        elif "huggingface-pytorch-trcomp-training" in repo_name:
            if lookup == "pytorch-training":
                return True
        elif "huggingface-tensorflow-trcomp-training" in repo_name:
            if lookup == "tensorflow-training":
                return True
        elif "pytorch-trcomp-training" in repo_name:
            if lookup == "pytorch-training":
                return True
        else:
            return False
    else:
        return True


def pytest_generate_tests(metafunc):
    images = metafunc.config.getoption("--images")

    # Check for public registry canary first
    if os.getenv("IS_PUBLIC_REGISTRY_CANARY", "false").lower() == "true":
        # Only handle framework agnostic tests for public registry
        if "image" in metafunc.fixturenames:
            metafunc.parametrize("image", images)
        return

    # Parametrize framework specific tests
    for fixture in FRAMEWORK_FIXTURES:
        if fixture in metafunc.fixturenames:
            lookup = fixture.replace("___", ":").replace("__", ".").replace("_", "-")
            images_to_parametrize = []
            for image in images:
                if lookup_condition(lookup, image):
                    is_example_lookup = (
                        "example_only" in metafunc.fixturenames and "example" in image
                    )
                    is_huggingface_lookup = (
                        "huggingface_only" in metafunc.fixturenames
                        or "huggingface" in metafunc.fixturenames
                    ) and "huggingface" in image
                    is_trcomp_lookup = "trcomp" in image and all(
                        fixture_name not in metafunc.fixturenames
                        for fixture_name in ["example_only"]
                    )
                    is_standard_lookup = all(
                        fixture_name not in metafunc.fixturenames
                        for fixture_name in ["example_only", "huggingface_only"]
                    ) and all(keyword not in image for keyword in ["example", "huggingface"])
                    if "sagemaker_only" in metafunc.fixturenames and is_ec2_image(image):
                        continue
                    if is_sagemaker_image(image):
                        if (
                            "sagemaker_only" not in metafunc.fixturenames
                            and "sagemaker" not in metafunc.fixturenames
                        ):
                            continue
                    if (
                        "stabilityai" not in metafunc.fixturenames
                        and "stabilityai" in image
                        and "sanity" not in os.getenv("TEST_TYPE")
                    ):
                        LOGGER.info(
                            f"Skipping test, as this function is not marked as 'stabilityai'"
                        )
                        continue
                    if not framework_version_within_limit(metafunc, image):
                        continue
                    if not cuda_version_within_limit(metafunc, image):
                        continue
                    if "non_huggingface_only" in metafunc.fixturenames and "huggingface" in image:
                        continue
                    if (
                        "non_pytorch_trcomp_only" in metafunc.fixturenames
                        and "pytorch-trcomp" in image
                    ):
                        continue
                    if "non_autogluon_only" in metafunc.fixturenames and "autogluon" in image:
                        continue
                    if "x86_compatible_only" in metafunc.fixturenames and (
                        "graviton" in image or "arm64" in image
                    ):
                        continue
                    if "training_compiler_only" in metafunc.fixturenames and not (
                        "trcomp" in image
                    ):
                        continue
                    if (
                        is_example_lookup
                        or is_huggingface_lookup
                        or is_standard_lookup
                        or is_trcomp_lookup
                    ):
                        if (
                            "cpu_only" in metafunc.fixturenames
                            and "cpu" in image
                            and "eia" not in image
                        ):
                            images_to_parametrize.append(image)
                        elif "gpu_only" in metafunc.fixturenames and "gpu" in image:
                            images_to_parametrize.append(image)
                        elif (
                            "graviton_compatible_only" in metafunc.fixturenames
                            and "graviton" in image
                        ):
                            images_to_parametrize.append(image)
                        elif "arm64_compatible_only" in metafunc.fixturenames and "arm64" in image:
                            images_to_parametrize.append(image)
                        elif (
                            "cpu_only" not in metafunc.fixturenames
                            and "gpu_only" not in metafunc.fixturenames
                            and "graviton_compatible_only" not in metafunc.fixturenames
                            and "arm64_compatible_only" not in metafunc.fixturenames
                        ):
                            images_to_parametrize.append(image)

            # Remove all images tagged as "py2" if py3_only is a fixture
            if images_to_parametrize and "py3_only" in metafunc.fixturenames:
                images_to_parametrize = [
                    py3_image for py3_image in images_to_parametrize if "py2" not in py3_image
                ]

            if is_nightly_context():
                nightly_images_to_parametrize = []
                # filter the nightly fixtures in the current functional context
                func_nightly_fixtures = {
                    key: value
                    for (key, value) in NIGHTLY_FIXTURES.items()
                    if key in metafunc.fixturenames
                }
                # iterate through image candidates and select images with labels that match all nightly fixture labels
                for image_candidate in images_to_parametrize:
                    if all(
                        [
                            are_fixture_labels_enabled(image_candidate, nightly_labels)
                            for _, nightly_labels in func_nightly_fixtures.items()
                        ]
                    ):
                        nightly_images_to_parametrize.append(image_candidate)
                images_to_parametrize = nightly_images_to_parametrize

            # Parametrize tests that spin up an ecs cluster or tests that spin up an EC2 instance with a unique name
            values_to_generate_for_fixture = {
                "ecs_container_instance": "ecs_cluster_name",
                "efa_ec2_connections": "ec2_key_name",
                "ec2_connection": "ec2_key_name",
            }

            fixtures_parametrized = generate_unique_values_for_fixtures(
                metafunc, images_to_parametrize, values_to_generate_for_fixture
            )
            if fixtures_parametrized:
                for new_fixture_name, test_parametrization in fixtures_parametrized.items():
                    metafunc.parametrize(f"{fixture},{new_fixture_name}", test_parametrization)
            else:
                metafunc.parametrize(fixture, images_to_parametrize)

    # Parametrize for framework agnostic tests, i.e. sanity
    if "image" in metafunc.fixturenames:
        metafunc.parametrize("image", images)


@pytest.fixture(autouse=True)
def disable_test(request):
    test_name = request.node.name
    # We do not have a regex pattern to find CB name, which means we must resort to string splitting
    build_arn = os.getenv("CODEBUILD_BUILD_ARN")
    build_name = build_arn.split("/")[-1].split(":")[0] if build_arn else None
    version = os.getenv("CODEBUILD_RESOLVED_SOURCE_VERSION")

    if test_utils.is_test_disabled(test_name, build_name, version):
        pytest.skip(f"Skipping {test_name} test because it has been disabled.")
