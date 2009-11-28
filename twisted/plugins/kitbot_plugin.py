import os.path

from twisted.application import internet, service
from twisted.plugin import IPlugin
from twisted.python import usage
from twisted.web.server import Site
from twisted.words.protocols.jabber.jid import internJID
from wokkel.client import XMPPClient
from zope.interface import implements

from bot import KITBot, LogViewPage


class Options(usage.Options):
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


class KITBotMaker(object):
    implements(service.IServiceMaker, IPlugin)

    tapname = 'kitbot'
    description = "The KIT info bot."
    options = Options

    def makeService(self, options):
        bot = service.MultiService()
                                          
        xmppclient = XMPPClient(internJID(options['jid']),
                                options['password'])
        xmppclient.logTraffic = ('verbose' in options)
        xmppclient.setServiceParent(bot)
        room_jid = internJID(options['room'])
        mucbot = KITBot(room_jid, options['room-password'], options['logpath'])
        mucbot.setHandlerParent(xmppclient)
       
        log_view = LogViewPage(os.path.join(options['logpath'],
                               room_jid.user + '.log'))
        httpd_log_view = internet.TCPServer(int(options['http-port']),
                                            Site(log_view))
        httpd_log_view.setServiceParent(bot)

        return bot


serviceMaker = KITBotMaker()
