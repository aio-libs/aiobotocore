import pytest
import requests
import shutil
import signal
import subprocess as sp
import sys
import time


_proxy_bypass = {
  "http": None,
  "https": None,
}


def start_service(service_name, host, port):
    moto_svr_path = shutil.which("moto_server")
    args = [sys.executable, moto_svr_path, service_name, "-H", host,
            "-p", str(port)]
    process = sp.Popen(args, stdin=sp.PIPE, stdout=sp.PIPE, stderr=sp.DEVNULL)
    url = "http://{host}:{port}".format(host=host, port=port)

    for i in range(0, 10):
        if process.poll() is not None:
            break

        try:
            # we need to bypass the proxies due to monkeypatches
            requests.get(url, timeout=0.5, proxies=_proxy_bypass)
            break
        except requests.exceptions.ConnectionError:
            time.sleep(0.5)
    else:
        stop_process(process)  # pytest.fail doesn't call stop_process
        pytest.fail("Can not start service: {}".format(service_name))

    return process


def stop_process(process):
    try:
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=20)
    except sp.TimeoutExpired:
        process.kill()
        outs, errors = process.communicate(timeout=20)
        exit_code = process.returncode
        msg = "Child process finished {} not in clean way: {} {}" \
            .format(exit_code, outs, errors)
        raise RuntimeError(msg)


@pytest.yield_fixture(scope="session")
def s3_server():
    host = "localhost"
    port = 5000
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('s3', host, port)
    yield url
    stop_process(process)


@pytest.yield_fixture(scope="session")
def dynamodb2_server():
    host = "localhost"
    port = 5001
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('dynamodb2', host, port)
    yield url
    stop_process(process)
