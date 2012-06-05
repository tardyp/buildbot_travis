
from buildbot.status import mail

from ..factories import SpawnerFactory
from ..travisyml import TravisYml


class MailNotifier(mail.MailNotifier):

    def getConfiguration(self, build):
        for step in build.getSteps():
            for log in step.getLogs():
                if log.getName() != '.travis.yml':
                    continue

                config = TravisYml()
                config.parse(log.getText())
                return config
        raise TravisYmlInvalid("No configuration found in branch")

    def isMailNeeded(self, build, results):
        builder = build.getBuilder()
        builder_config = filter(lambda b: b.name == builder.name, self.master.config.builders)[0]

        # This notifier will only generate emails for the "spawner" builds
        if not isinstance(builder_config.factory, SpawnerFactory):
            return False

        # That have valid configuration
        try:
            config = self.getConfiguration(build)
        except TravisYmlInvalid:
            return False

        # And emails are enabled
        if not config.email.enabled:
            return False

        def decide(config):
            if config == "never":
                return False
            elif config == "always":
                return True
            elif config == "change":
                prev = build.getPreviousBuild()
                return prev.getResults() != results

        if results == SUCCESS:
            decide(config.email.success)
        else:
            decide(config.email.failure)

        return False

    def getTravisAddresses(self, build):
        config = self.getConfiguration(build)
        return config.email.addresses

    @defer.inlineCallbacks
    def useLookup(self, build):
        recipients = self.getTravisAddresses(build)
        if not recipients:
            recipients = yield mail.MailNotifier.useLookup(self, build)
        defer.returnValue(recipients)

    @defer.inlineCallbacks
    def useUsers(self, build):
        recipients = self.getTravisAddresses(build)
        if not recipients:
            recipients = yield mail.MailNotifier.useUsers(self, build)
        defer.returnValue(recipients)

