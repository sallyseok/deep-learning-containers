import pytest

import test.test_utils.ecs as ecs_utils
import test.test_utils.ec2 as ec2_utils
from test.test_utils import get_framework_and_version_from_tag
from test.test_utils import request_mxnet_inference, request_mxnet_inference_gluonnlp
from test.test_utils import (
    ECS_AML2_CPU_USWEST2,
    ECS_AML2_GPU_USWEST2,
    ECS_AML2_NEURON_USWEST2,
    ECS_AML2_ARM64_CPU_USWEST2,
)


@pytest.mark.model("squeezenet")
@pytest.mark.team("frameworks")
@pytest.mark.parametrize("ecs_instance_type", ["c5.18xlarge"], indirect=True)
@pytest.mark.parametrize("ecs_ami", [ECS_AML2_CPU_USWEST2], indirect=True)
def test_ecs_mxnet_inference_cpu(mxnet_inference, ecs_container_instance, region, cpu_only):
    __test_ecs_mxnet_inference_cpu(mxnet_inference, ecs_container_instance, region)


@pytest.mark.model("squeezenet")
@pytest.mark.parametrize("ecs_instance_type", ["c6g.16xlarge"], indirect=True)
@pytest.mark.parametrize("ecs_ami", [ECS_AML2_ARM64_CPU_USWEST2], indirect=True)
def test_ecs_mxnet_inference_graviton_cpu(
    mxnet_inference_graviton, ecs_container_instance, region, cpu_only
):
    __test_ecs_mxnet_inference_cpu(mxnet_inference_graviton, ecs_container_instance, region)


def __test_ecs_mxnet_inference_cpu(mxnet_inference, ecs_container_instance, region):
    worker_instance_id, ecs_cluster_arn = ecs_container_instance
    public_ip_address = ec2_utils.get_public_ip(worker_instance_id, region=region)

    model_name = "squeezenet"
    service_name = task_family = revision = None
    try:
        service_name, task_family, revision = ecs_utils.setup_ecs_inference_service(
            mxnet_inference, "mxnet", ecs_cluster_arn, model_name, worker_instance_id, region=region
        )
        inference_result = request_mxnet_inference(public_ip_address)
        assert inference_result, f"Failed to perform inference at IP address: {public_ip_address}"

    finally:
        ecs_utils.tear_down_ecs_inference_service(
            ecs_cluster_arn, service_name, task_family, revision
        )


@pytest.mark.model("mxnet-resnet-neuron")
@pytest.mark.parametrize("ecs_instance_type", ["inf1.2xlarge"], indirect=True)
@pytest.mark.parametrize("ecs_ami", [ECS_AML2_NEURON_USWEST2], indirect=True)
@pytest.mark.team("neuron")
def test_ecs_mxnet_inference_neuron(mxnet_inference_neuron, ecs_container_instance, region):
    worker_instance_id, ecs_cluster_arn = ecs_container_instance
    public_ip_address = ec2_utils.get_public_ip(worker_instance_id, region=region)
    num_neurons = ec2_utils.get_instance_num_inferentias(worker_instance_id, region=region)

    model_name = "mxnet-resnet-neuron"
    service_name = task_family = revision = None
    try:
        service_name, task_family, revision = ecs_utils.setup_ecs_inference_service(
            mxnet_inference_neuron,
            "mxnet",
            ecs_cluster_arn,
            model_name,
            worker_instance_id,
            num_neurons=num_neurons,
            region=region,
        )
        inference_result = request_mxnet_inference(public_ip_address, model=model_name)
        assert inference_result, f"Failed to perform inference at IP address: {public_ip_address}"

    finally:
        ecs_utils.tear_down_ecs_inference_service(
            ecs_cluster_arn, service_name, task_family, revision
        )


@pytest.mark.model("squeezenet")
@pytest.mark.team("frameworks")
@pytest.mark.parametrize("ecs_instance_type", ["g5.12xlarge"], indirect=True)
@pytest.mark.parametrize("ecs_ami", [ECS_AML2_GPU_USWEST2], indirect=True)
def test_ecs_mxnet_inference_gpu(mxnet_inference, ecs_container_instance, region, gpu_only):
    worker_instance_id, ecs_cluster_arn = ecs_container_instance
    public_ip_address = ec2_utils.get_public_ip(worker_instance_id, region=region)
    num_gpus = ec2_utils.get_instance_num_gpus(worker_instance_id, region=region)

    model_name = "squeezenet"
    service_name = task_family = revision = None
    try:
        service_name, task_family, revision = ecs_utils.setup_ecs_inference_service(
            mxnet_inference,
            "mxnet",
            ecs_cluster_arn,
            model_name,
            worker_instance_id,
            num_gpus=num_gpus,
            region=region,
        )
        inference_result = request_mxnet_inference(public_ip_address)
        assert inference_result, f"Failed to perform inference at IP address: {public_ip_address}"

    finally:
        ecs_utils.tear_down_ecs_inference_service(
            ecs_cluster_arn, service_name, task_family, revision
        )


@pytest.mark.model("bert_sst")
@pytest.mark.integration("gluonnlp")
@pytest.mark.team("frameworks")
@pytest.mark.parametrize("ecs_instance_type", ["c5.large"], indirect=True)
@pytest.mark.parametrize("ecs_ami", [ECS_AML2_CPU_USWEST2], indirect=True)
def test_ecs_mxnet_inference_gluonnlp_cpu(
    mxnet_inference, ecs_container_instance, region, cpu_only, py3_only
):
    worker_instance_id, ecs_cluster_arn = ecs_container_instance
    public_ip_address = ec2_utils.get_public_ip(worker_instance_id, region=region)

    model_name = "bert_sst"
    service_name = task_family = revision = None
    try:
        service_name, task_family, revision = ecs_utils.setup_ecs_inference_service(
            mxnet_inference, "mxnet", ecs_cluster_arn, model_name, worker_instance_id, region=region
        )
        inference_result = request_mxnet_inference_gluonnlp(public_ip_address)
        assert inference_result, f"Failed to perform inference at IP address: {public_ip_address}"

    finally:
        ecs_utils.tear_down_ecs_inference_service(
            ecs_cluster_arn, service_name, task_family, revision
        )


@pytest.mark.model("bert_sst")
@pytest.mark.integration("gluonnlp")
@pytest.mark.team("frameworks")
@pytest.mark.parametrize("ecs_instance_type", ["g4dn.12xlarge"], indirect=True)
@pytest.mark.parametrize("ecs_ami", [ECS_AML2_GPU_USWEST2], indirect=True)
def test_ecs_mxnet_inference_gluonnlp_gpu(
    mxnet_inference, ecs_container_instance, region, gpu_only, py3_only
):
    worker_instance_id, ecs_cluster_arn = ecs_container_instance
    public_ip_address = ec2_utils.get_public_ip(worker_instance_id, region=region)
    num_gpus = ec2_utils.get_instance_num_gpus(worker_instance_id, region=region)

    model_name = "bert_sst"
    service_name = task_family = revision = None
    try:
        service_name, task_family, revision = ecs_utils.setup_ecs_inference_service(
            mxnet_inference,
            "mxnet",
            ecs_cluster_arn,
            model_name,
            worker_instance_id,
            num_gpus=num_gpus,
            region=region,
        )
        inference_result = request_mxnet_inference_gluonnlp(public_ip_address)
        assert inference_result, f"Failed to perform inference at IP address: {public_ip_address}"

    finally:
        ecs_utils.tear_down_ecs_inference_service(
            ecs_cluster_arn, service_name, task_family, revision
        )
