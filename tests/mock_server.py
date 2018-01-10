import asyncio
import aiohttp.web
import pytest
import requests
import shutil
import signal
import subprocess as sp
import sys
import time
import threading

_proxy_bypass = {
  "http": None,
  "https": None,
}


class AIOServer(threading.Thread):
    def __init__(self, port):
        super().__init__(target=self._run)
        self._loop = None
        self._port = port
        self.start()
        self.endpoint_url = 'http://localhost:{}'.format(port)
        self._shutdown_evt = asyncio.Event()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        app = aiohttp.web.Application(loop=self._loop)
        app.router.add_route('*', '/ok', self.ok)
        app.router.add_route('*', '/{anything:.*}', self.stream_handler)

        try:
            aiohttp.web.run_app(app, host='localhost', port=self._port,
                                loop=self._loop, handle_signals=False)
        except:
            pytest.fail('unable to start and connect to aiohttp server')
            raise
        finally:
            self._shutdown_evt.set()

    @asyncio.coroutine
    def ok(self, request):
        return aiohttp.web.Response()

    @asyncio.coroutine
    def stream_handler(self, request):
        # Without the Content-Type, most (all?) browsers will not render
        # partially downloaded content. Note, the response type is
        # StreamResponse not Response.
        resp = aiohttp.web.StreamResponse(status=200, reason='OK',
                                          headers={'Content-Type': 'text/html'})

        yield from resp.prepare(request)
        yield from asyncio.sleep(5)
        yield from resp.drain()
        return resp

    def wait_until_up(self):
        connected = False
        for i in range(0, 30):
            try:
                # we need to bypass the proxies due to monkey patches
                requests.get(self.endpoint_url + '/ok', timeout=0.5, proxies=_proxy_bypass)
                connected = True
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
                time.sleep(0.5)
            except:
                pytest.fail('unable to start and connect to aiohttp server')
                raise

        if not connected:
            pytest.fail('unable to start and connect to aiohttp server')

    @asyncio.coroutine
    def stop(self):
        if self._loop:
            self._loop.stop()

            try:
                # FIXME
                # yield from self._shutdown_evt.wait()
                # for some reason yielding on wait hangs, so we busy loop
                for i in range(20):
                    if not self._shutdown_evt.is_set():
                        yield from asyncio.sleep(0.5)

                    break

                if not self._shutdown_evt.is_set():
                    pytest.fail("Unable to shut down server")
            except:
                raise


def start_service(service_name, host, port):
    moto_svr_path = shutil.which("moto_server")
    args = [sys.executable, moto_svr_path, service_name, "-H", host,
            "-p", str(port)]
    process = sp.Popen(args, stdin=sp.PIPE, stdout=sp.PIPE, stderr=sp.PIPE)
    url = "http://{host}:{port}".format(host=host, port=port)

    for i in range(0, 30):
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail("service failed starting up: {}  stdout: {} stderr: {}".format(service_name, stdout, stderr))
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

    try:
        yield url
    finally:
        stop_process(process)


@pytest.yield_fixture(scope="session")
def dynamodb2_server():
    host = "localhost"
    port = 5001
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('dynamodb2', host, port)

    try:
        yield url
    finally:
        stop_process(process)


@pytest.yield_fixture(scope="session")
def cloudformation_server():
    host = "localhost"
    port = 5002
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('cloudformation', host, port)

    try:
        yield url
    finally:
        stop_process(process)


@pytest.yield_fixture(scope="session")
def sns_server():
    host = "localhost"
    port = 5003
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('sns', host, port)

    try:
        yield url
    finally:
        stop_process(process)


@pytest.yield_fixture(scope="session")
def sqs_server():
    host = "localhost"
    port = 5004
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('sqs', host, port)

    try:
        yield url
    finally:
        stop_process(process)
