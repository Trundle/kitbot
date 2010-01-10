import os.path
from getpass import getpass

from twisted.application import internet, service
from twisted.cred.credentials import IUsernamePassword
from twisted.cred.strcred import AuthOptionMixin
from twisted.cred.portal import Portal
from twisted.plugin import IPlugin
from twisted.python import usage
from twisted.web.guard import HTTPAuthSessionWrapper, DigestCredentialFactory
from twisted.web.resource import Resource
from twisted.web.server import Site
from twisted.web.util import Redirect
from twisted.words.protocols.jabber.jid import internJID
from wokkel.client import XMPPClient
from zope.interface import implements

from bot import KITBot, LogViewRealm


class Options(usage.Options, AuthOptionMixin):
    optFlags = [
        ('room-has-password', None, 'Whether the room has a password.'),
        ('verbose', 'v', 'Log XMPP traffic')
    ]

    optParameters = [
        ('jid', 'j', 'kitty@example.org', "The bot's Jabber ID"),
        ('room', 'r', 'kit@conference.example.org/Kitty', 'The room to join'),
        ('logpath', 'p', '.', 'Path where logs are written to'),
        ('http-port', None, 8080, 'Port of HTTPd for log views', int)
    ]

    supportedInterfaces = (IUsernamePassword, )


class KITBotMaker(object):
    implements(service.IServiceMaker, IPlugin)

    tapname = 'kitbot'
    description = "The KIT info bot."
    options = Options

    def makeService(self, options):
        # Get the passwords interactively, so they are not shown in the
        # process list
        options['password'] = getpass('Enter password: ')
        if options['room-has-password']:
            options['room-password'] = getpass('Enter room password: ')

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

        root = Resource()
        resource = HTTPAuthSessionWrapper(portal, [credential_factory])
        root.putChild('', Redirect('/%s/view/' % (str(room_jid.user, ))))
        root.putChild(room_jid.user, resource)

        httpd_log_view = internet.TCPServer(options['http-port'], Site(root))
        httpd_log_view.setServiceParent(bot)

        return bot


serviceMaker = KITBotMaker()
