# The master: its state in the directory holding this file, its configuration in
# /opt/sashing. The start copies it there, where buildbot upgrade-master reads `configfile`.

import os
import sys

from buildbot.master import BuildMaster
from twisted.application import service
from twisted.python.log import FileLogObserver, ILogObserver

basedir = os.path.abspath(os.path.dirname(__file__))
configfile = '/opt/sashing/master.cfg'

application = service.Application('buildmaster')
application.setComponent(ILogObserver, FileLogObserver(sys.stdout).emit)
BuildMaster(basedir, configfile, umask=None).setServiceParent(application)
