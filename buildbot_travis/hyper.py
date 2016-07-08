# This file is part of Buildbot. Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members

# Needed so that this module name don't clash with docker-py on older python.
from __future__ import absolute_import

import socket

from twisted.internet import defer, threads
from twisted.python import log

from buildbot import config
from buildbot.interfaces import LatentWorkerFailedToSubstantiate
from buildbot.util import json
from buildbot.worker import AbstractLatentWorker

try:
    import docker
    from hypercompose.api import Hyper
    _hush_pyflakes = [docker, Hyper]
except ImportError:
    Hyper = None


def _handle_stream_line(line):
    """\
    Input is the json representation of: {'stream': "Content\ncontent"}
    Output is a generator yield "Content", and then "content"
    """
    # XXX This necessary processing is probably a bug from docker-py,
    # hence, might break if the bug is fixed, i.e. we should get decoded JSON
    # directly from the API.
    line = json.loads(line)
    if 'error' in line:
        content = "ERROR: " + line['error']
    else:
        content = line.get('stream', '')
    for streamline in content.split('\n'):
        if streamline:
            yield streamline


class HyperLatentWorker(AbstractLatentWorker):
    instance = None

    def __init__(self, name, password, image, hyper_host,
                 hyper_accesskey, hyper_secretkey, masterFQDN=None, **kwargs):

        if not Hyper:
            config.error("The python modules 'docker-py>=1.4' and 'hypercompose' are needed to use a"
                         " HyperLatentWorker")

        # Set build_wait_timeout to 10 if not explicitely set: Starting a
        # container is almost immediate, we can affort doing so for each build.

        if 'build_wait_timeout' not in kwargs:
            kwargs['build_wait_timeout'] = 10

        AbstractLatentWorker.__init__(self, name, password, **kwargs)

        self.image = image
        # Prepare the parameters for the Docker Client object.
        self.client_args = {'clouds': {
            hyper_host: {
                "accesskey": hyper_accesskey,
                "secretkey": hyper_secretkey
            }
        }}
        if not masterFQDN:
            masterFQDN = socket.getfqdn()
        self.masterFQDN = masterFQDN

    def createEnvironment(self):
        result = {
            "BUILDMASTER": self.masterFQDN,
            "WORKERNAME": self.name,
            "WORKERPASS": self.password
        }
        if self.registration is not None:
            result["BUILDMASTER_PORT"] = str(self.registration.getPBPort())
        print result
        return result

    @defer.inlineCallbacks
    def start_instance(self, build):
        if self.instance is not None:
            raise ValueError('instance active')
        image = yield build.render(self.image)
        res = yield threads.deferToThread(self._thd_start_instance, image)
        defer.returnValue(res)

    def _image_exists(self, client, name):
        # Make sure the image exists
        for image in client.images():
            for tag in image['RepoTags']:
                if ':' in name and tag == name:
                    return True
                if tag.startswith(name + ':'):
                    return True
        return False

    def _thd_start_instance(self, image):
        hyper_client = Hyper(self.client_args)
        instance = hyper_client.create_container(
            image,
            name='%s%s' % (self.workername, id(self)),
            environment=self.createEnvironment(),
        )

        if instance.get('Id') is None:
            log.msg('Failed to create the container')
            raise LatentWorkerFailedToSubstantiate(
                'Failed to start container'
            )
        shortid = instance['Id'][:6]
        log.msg('Container created, Id: %s...' % (shortid,))
        instance['image'] = image
        self.instance = instance
        hyper_client.start(instance)
        log.msg('Container started')
        return [instance['Id'], image]

    def stop_instance(self, fast=False):
        if self.instance is None:
            # be gentle. Something may just be trying to alert us that an
            # instance never attached, and it's because, somehow, we never
            # started.
            return defer.succeed(None)
        instance = self.instance
        self.instance = None
        return threads.deferToThread(self._thd_stop_instance, instance, fast)

    def _thd_stop_instance(self, instance, fast):
        hyper_client = Hyper(self.client_args)
        log.msg('Stopping container %s...' % instance['Id'][:6])
        hyper_client.stop(instance['Id'])
        if not fast:
            hyper_client.wait(instance['Id'])
        hyper_client.remove_container(instance['Id'], v=True, force=True)
