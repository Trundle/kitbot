from __future__ import with_statement
try:
    import json
except ImportError:
    import simplejson as json
import os.path

from twisted.application import internet, service
from twisted.conch import checkers, manhole, manhole_ssh
from twisted.conch.insults import insults
from twisted.cred import strcred
from twisted.cred.portal import Portal
from twisted.plugin import IPlugin
from twisted.python import usage
from twisted.web.guard import HTTPAuthSessionWrapper, DigestCredentialFactory
from twisted.web import resource, server, static
from twisted.words.protocols.jabber.jid import JID
from wokkel.client import XMPPClient
from zope.interface import implements

from bot import KITBot, LogViewRealm, XMLRPCInterface, load_plugins


class Options(usage.Options):
    optFlags = [
        ('verbose', 'v', 'Log XMPP traffic')
    ]

    def getSynopsis(self):
        return 'Usage: twistd [options] kitbot <config file>'

    def parseArgs(self, *args):
        if len(args) == 1:
            self.config = args[0]
        else:
            self.opt_help()


class KITBotMaker(object):
    implements(service.IServiceMaker, IPlugin)

    tapname = 'kitbot'
    description = "The KIT info bot."
    options = Options

    def makeService(self, options):
        with open(options.config, "r") as config_file:
            config = json.load(config_file)

        root = resource.Resource()
        root.putChild('jsMath', static.File(config["global"]["jsmath"]))

        bot = service.MultiService()
        xmppclient = XMPPClient(JID(config["global"]["jid"]),
                                config["global"]["password"])
        xmppclient.logTraffic = options['verbose']
        xmppclient.setServiceParent(bot)
        xmppclient.rooms = dict()

        xmlrpc_port = config["global"].get("xml-rpc-port", None)
        if xmlrpc_port is not None:
            xmlrpcinterface = XMLRPCInterface(xmppclient)
            rpc = internet.TCPServer(xmlrpc_port, server.Site(xmlrpcinterface))
            rpc.setName('XML-RPC')
            rpc.setServiceParent(bot)

        for muc_config in config["mucs"]:
            room_jid = JID(muc_config["jid"])
            mucbot = KITBot(room_jid, muc_config.get("password", None),
                            config["global"]["logpath"])
            mucbot.setHandlerParent(xmppclient)

            if "xml-rpc-id" in muc_config:
                xmppclient.rooms[muc_config["xml-rpc-id"]] = mucbot

            # Log resource
            portal = Portal(
                LogViewRealm(os.path.join(config["global"]['logpath'],
                                          room_jid.user + '.log')),
                [strcred.makeChecker(muc_config["log-auth"])]
            )
            credential_factory = DigestCredentialFactory('md5', 'Hello Kitty!')
            auth_resource = HTTPAuthSessionWrapper(portal, [credential_factory])
            root.putChild(room_jid.user, auth_resource)

        httpd_log_view = internet.TCPServer(config["global"]["http-port"],
                                            server.Site(root))
        httpd_log_view.setServiceParent(bot)

        # REPL over SSH
        def makeREPLProtocol():
            namespace = dict(bot=xmppclient)
            return insults.ServerProtocol(manhole.ColoredManhole, namespace)
        repl_realm = manhole_ssh.TerminalRealm()
        repl_realm.chainedProtocolFactory = makeREPLProtocol
        repl_checker = checkers.SSHPublicKeyDatabase()
        repl_portal = Portal(repl_realm, [repl_checker])
        repl_factory = manhole_ssh.ConchFactory(repl_portal)
        repl = internet.TCPServer(config["global"]["ssh-port"], repl_factory)
        repl.setServiceParent(bot)

        load_plugins(config)

        return bot


serviceMaker = KITBotMaker()
