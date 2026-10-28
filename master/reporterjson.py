"""A reporter writing JSON to a Unix socket: one connection per report, which carries the
report's body and gets a JSON answer back. An answer with an `error`, or none, is logged."""

import json
import socket

from buildbot import interfaces
from buildbot.reporters import utils
from buildbot.reporters.base import ReporterBase
from twisted.internet import defer, threads
from twisted.python import log
from zope.interface import implementer


@implementer(interfaces.IReportGenerator)
class StepGenerator:
    """A report for each finished step: its body is `request(build, step)`, or there is none
    when that is None. `build` has its properties and its builder."""

    wanted_event_keys = [('steps', None, 'finished')]

    def __init__(self, request):
        self.request = request

    def check(self):
        pass

    def generate_name(self):
        return self.__class__.__name__

    @defer.inlineCallbacks
    def generate(self, master, reporter, key, step):
        build = yield master.data.get(('builds', step['buildid']))
        yield utils.getDetailsForBuild(master, build, want_properties=True)
        request = self.request(build, step)
        return None if request is None else {'body': request}


def exchange(path, request):
    """Write the request, close the writing side, and read the answer until the server ends."""
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(path)
        s.sendall(request)
        s.shutdown(socket.SHUT_WR)
        return b''.join(iter(lambda: s.recv(4096), b''))


class SocketJsonReporter(ReporterBase):
    """Send each report's body to the socket at `path`. Nothing is retried."""

    def checkConfig(self, path, generators):
        super().checkConfig(generators)

    def reconfigService(self, path, generators):
        self.path = path
        return super().reconfigService(generators)

    @defer.inlineCallbacks
    def sendMessage(self, reports):
        for report in reports:
            request = json.dumps(report['body'])
            answer = yield threads.deferToThread(exchange, self.path, request.encode())
            try:
                error = json.loads(answer).get('error')
            except ValueError:
                error = f'no answer: {answer!r}'
            if error:
                log.msg(f'{self.path}: {error}, for {request}')
