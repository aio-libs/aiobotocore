import pytest
import requests
import signal
import subprocess as sp
import time


@pytest.yield_fixture(scope="session")
def s3_server():
    host = "localhost"
    port = 5000
    command = ("moto_server s3 -H {host} -p {port}"
               .format(host=host, port=port)
               .split(" "))
    process = sp.Popen(command, stdin=sp.PIPE, stdout=sp.PIPE,
                       stderr=sp.DEVNULL)
    url = "http://{host}:{port}".format(host=host, port=port)
    for i in range(0, 60):
        try:
            requests.get(url)
            break
        except requests.exceptions.ConnectionError:
            time.sleep(1)

    yield url

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
