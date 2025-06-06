import os
import numpy as np
import time
import argparse


def _clean_up_reports():
    if os.path.exists("/tmp/test_request.txt"):
        os.system("rm /tmp/test_request.txt")
    if os.path.exists("/tmp/test_tag_request.txt"):
        os.system("rm /tmp/test_tag_request.txt")


def opt_in_opt_out_test(exec_cmd):
    os.environ["TEST_MODE"] = "1"

    for opt_out_value in ["True", "TRUE", "true"]:
        _clean_up_reports()
        os.environ["OPT_OUT_TRACKING"] = opt_out_value
        cmd = f"python -c '{exec_cmd}'"
        os.system(cmd)
        time.sleep(20)
        assert not os.path.exists(
            "/tmp/test_request.txt"
        ), f"URL request placed even though OPT_OUT_TRACKING is {opt_out_value}."
        assert not os.path.exists(
            "/tmp/test_tag_request.txt"
        ), f"Tag request placed even though OPT_OUT_TRACKING is {opt_out_value}."

    for opt_out_value in ["False", "XYgg"]:
        _clean_up_reports()
        os.environ["OPT_OUT_TRACKING"] = opt_out_value
        cmd = f"python -c '{exec_cmd}'"
        os.system(cmd)
        time.sleep(20)
        assert os.path.exists(
            "/tmp/test_request.txt"
        ), f"URL request not placed even though OPT_OUT_TRACKING is {opt_out_value}."
        assert os.path.exists(
            "/tmp/test_tag_request.txt"
        ), f"Tag request not placed even though OPT_OUT_TRACKING is {opt_out_value}."

    os.environ["TEST_MODE"] = "0"
    print("Opt-In/Opt-Out Test passed")


def perf_test(exec_cmd):
    os.environ["TEST_MODE"] = "0"
    os.environ["OPT_OUT_TRACKING"] = "False"
    NUM_ITERATIONS = 5

    for itr in range(NUM_ITERATIONS):
        total_time_in = 0
        for x in range(NUM_ITERATIONS):
            cmd = f"python -c '{exec_cmd}'"
            start = time.time()
            os.system(cmd)
            total_time_in += time.time() - start
        print("avg in time: ", total_time_in / NUM_ITERATIONS)

        total_time_out = 0
        for x in range(NUM_ITERATIONS):
            cmd = f"export OPT_OUT_TRACKING='true' && python -c '{exec_cmd}'"
            start = time.time()
            os.system(cmd)
            total_time_out += time.time() - start
        print("avg out time: ", total_time_out / NUM_ITERATIONS)

        np.testing.assert_allclose(
            total_time_in / NUM_ITERATIONS, total_time_out / NUM_ITERATIONS, rtol=0.2, atol=0.5
        )

        print("DLC Telemetry performance test Passed")


def run_tests(test_cmd):
    print(f"Running tests with command: {test_cmd}")
    perf_test(test_cmd)
    opt_in_opt_out_test(test_cmd)
    print("All DLC telemetry test passed")


def main():
    parser = argparse.ArgumentParser(description="Run DLC telemetry tests")
    parser.add_argument(
        "--test-cmd",
        type=str,
        required=True,
        help="The Python command to test",
    )
    args = parser.parse_args()

    run_tests(args.test_cmd)


if __name__ == "__main__":
    main()
