import os
import subprocess
import tempfile

from buildbot.plugins import steps
from buildbot.process import buildstep
from buildbot.process.results import ALL_RESULTS, FAILURE, SUCCESS, WARNINGS, statusToString
from twisted.internet import defer, threads
from twisted.python import log


class ForEachPatch(steps.Trigger):
    """Start a build for each patch, identified before application by its message-id."""

    def getCurrentSummary(self):
        return {'step': 'triggered'} if self.triggeredNames else super().getCurrentSummary()

    def getResultSummary(self):
        if self.ended or not self._result_list:
            return super().getResultSummary()
        counts = []
        for status in ALL_RESULTS:
            count = self._result_list.count(status)
            if count:
                labels = {SUCCESS: 'passed', WARNINGS: 'warning' if count == 1 else 'warnings',
                          FAILURE: 'failed'}
                counts.append(f'{count} {labels.get(status, statusToString(status, count))}')
        return {'step': ', '.join(counts)}

    def getSchedulersAndProperties(self):
        return [
            {'sched_name': name, 'props_to_set': {**self.set_properties, 'msgid': msgid, 'number': str(n)},
             'unimportant': False}
            for n, msgid in enumerate(self.getProperty('patch_ids').splitlines(), 1)
            for name in self.schedulerNames
        ]


class ForEachBinding(buildstep.ShellMixin, steps.BuildStep):
    """Add a check for each binding path listed by the discovery command."""

    def __init__(self, check, **kwargs):
        super().__init__(**self.setupShellMixin(kwargs))
        self.check = check

    @defer.inlineCallbacks
    def run(self):
        cmd = yield self.makeRemoteShellCommand(collectStdout=True)
        yield self.runCommand(cmd)
        if cmd.results() == SUCCESS:
            self.build.addStepsAfterCurrentStep([self.check(path) for path in cmd.stdout.split()])
        return cmd.results()


class CompanionSummary:
    """An executable summary companion may describe a finished task but cannot change its verdict."""

    companion_summary = None

    def getResultSummary(self):
        if self.companion_summary is not None and not self.stopped:
            return {'step': self.build.properties.cleanupTextFromSecrets(self.companion_summary)}
        return super().getResultSummary()

    @defer.inlineCallbacks
    def runCommand(self, command):
        result = yield super().runCommand(command)
        self.task_rc = command.rc
        return result

    async def summarize(self, companion, rc):
        stdio = self.getLog('stdio')
        if not stdio.finished:
            await stdio.finish()
        with tempfile.TemporaryFile() as output:
            async for line in self.master.db.logs.iter_log_lines(stdio.logid):
                if line.startswith(('o', 'e')):
                    output.write(line[1:].encode())
            output.seek(0)
            parser = await threads.deferToThread(
                subprocess.run, [companion, str(rc)], stdin=output,
                stdout=subprocess.PIPE, timeout=10, text=True)
        lines = parser.stdout.strip().splitlines()
        if parser.returncode == 0 and len(lines) == 1 and not self.stopped:
            self.companion_summary = lines[0]

    @defer.inlineCallbacks
    def summarize_task(self, result, rc):
        companion = os.path.join(os.path.dirname(__file__), 'summaries', self.command[0])
        if (rc is not None and not self.stopped
                and not self.timed_out and not self.max_lines_reached
                and result in (SUCCESS, WARNINGS, FAILURE) and os.access(companion, os.X_OK)):
            try:
                yield defer.Deferred.fromCoroutine(self.summarize(companion, rc))
            except Exception:
                log.err(None, f'Could not summarize {self.name}')

    @defer.inlineCallbacks
    def run(self):
        result = yield super().run()
        yield self.summarize_task(result, getattr(self, 'task_rc', None))
        return result


class TaskShell(CompanionSummary, steps.ShellCommand):
    pass


class TaskCompile(CompanionSummary, steps.Compile):
    pass
