import os

from buildbot.plugins import steps
from buildbot.process import buildstep
from buildbot.process.results import SUCCESS, WARNINGS
from twisted.internet import defer

from pipeline_steps import CompanionSummary


class GatherSeries(CompanionSummary, buildstep.ShellMixin, steps.MultipleFileUpload):
    """Gather one revision and publish its mailboxes for the offline patch builds."""

    def __init__(self, decodeRC, **kwargs):
        super().__init__(**kwargs)
        self.decodeRC = decodeRC

    @defer.inlineCallbacks
    def run(self):
        self.checkWorkerHasCommand('uploadFile')
        cmd = yield self.makeRemoteShellCommand(
            command=['gather-series', '../mail', self.getProperty('msgid')],
            collectStdout=True)
        yield self.runCommand(cmd)
        result = cmd.results()
        if result not in (SUCCESS, WARNINGS):
            self.descriptionDone = ['gather series']
            yield self.summarize_task(result, cmd.rc)
            return result

        self.stdio_log = self.getLog('stdio')
        for source in self.workersrcs:
            name = os.path.basename(source)
            uploaded = yield self.uploadFile(source, os.path.join(self.masterdest, name))
            if uploaded != SUCCESS:
                self.descriptionDone = [f'upload {name}']
                yield self.stdio_log.addStderr(f'Could not upload {name}\n')
                return uploaded

        self.setProperty('patch_ids', cmd.stdout.strip())
        self.descriptionDone = ['gathered']
        yield self.summarize_task(result, cmd.rc)
        return result
