import os.path

from twisted.application import internet, service
from twisted.cred.credentials import IUsernamePassword
from twisted.cred.strcred import AuthOptionMixin
from twisted.cred.portal import Portal
from twisted.plugin import IPlugin
from twisted.python import usage
from twisted.web.guard import HTTPAuthSessionWrapper, DigestCredentialFactory
from twisted.web.server import Site
from twisted.words.protocols.jabber.jid import internJID
from wokkel.client import XMPPClient
from zope.interface import implements

from bot import KITBot, LogViewRealm


class Options(usage.Options, AuthOptionMixin):
    optFlags = [
        ('verbose', 'v', 'Log XMPP traffic')
    ]

    optParameters = [
        ('jid', 'j', 'kitty@example.org', "The bot's Jabber ID"),
        ('password', 'p', '', "Password of bot's jabber account."),
        ('room', 'r', 'kit@conference.example.org/Kitty', 'The room to join'),
        ('room-password', None, '', "The room's password."),
        ('logpath', 'p', '.', 'Path where logs are written to'),
        ('http-port', None, 8080, 'Port of HTTPd for log views')
    ]

    supportedInterfaces = (IUsernamePassword, )


class KITBotMaker(object):
    implements(service.IServiceMaker, IPlugin)

    tapname = 'kitbot'
    description = "The KIT info bot."
    options = Options

    def makeService(self, options):
        bot = service.MultiService()

        xmppclient = XMPPClient(internJID(options['jid']),
                                options['password'])
        xmppclient.logTraffic = options['verbose']
        xmppclient.setServiceParent(bot)
        room_jid = internJID(options['room'])
        mucbot = KITBot(room_jid, options['room-password'], options['logpath'])
        mucbot.setHandlerParent(xmppclient)

        portal = Portal(LogViewRealm(os.path.join(options['logpath'],
                                     room_jid.user + '.log')),
                        options["credInterfaces"][IUsernamePassword])
        credential_factory = DigestCredentialFactory('md5', 'Hello Kitty!')
        resource = HTTPAuthSessionWrapper(portal, [credential_factory])
        httpd_log_view = internet.TCPServer(int(options['http-port']),
                                            Site(resource))
        httpd_log_view.setServiceParent(bot)

        return bot


serviceMaker = KITBotMaker()
