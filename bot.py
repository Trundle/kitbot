# encoding: utf-8


"""
    kitbot
    ~~~~~~

    A simple logging bot.

    Copyright (C) 2009 Andreas Stührk
"""

from __future__ import with_statement
import os
from datetime import datetime

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers.text import IrcLogsLexer
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound
from twisted.cred.portal import IRealm
from twisted.python.logfile import DailyLogFile
from twisted.web.error import NoResource
from twisted.web.resource import IResource, Resource
from wokkel import muc
from wokkel.xmppim import AvailablePresence
from zope.interface import implements


class ChatLogger(object):
    def __init__(self, logfile, path):
        self.log = DailyLogFile(logfile, path)
        date = datetime.now().strftime('%a %b %d %H:%M %Y')
        self.log.write('--- Log opened: %s\n' % (date, ))

    def write_line(self, line):
        self.log.write(datetime.now().strftime('%H:%M '))
        if isinstance(line, unicode):
            line = line.encode('utf-8')
        self.log.write(line)
        self.log.write('\n')
        self.log.flush()

    def action(self, nick, message):
        self.write_line(' * %s %s' % (nick, message))

    def message(self, nick, message):
        self.write_line('<%s> %s' % (nick, message))


class LogViewRealm(object):
    implements(IRealm)

    def __init__(self, logfilename):
        self.logfilename = logfilename

    def requestAvatar(self, avatarID, mind, *interfaces):
        if IResource in interfaces:
            return (IResource, LogViewPage(self.logfilename), lambda: None)
        raise NotImplementedError()


class LogViewPage(Resource):
    def __init__(self, logfilename, style_name='default'):
        Resource.__init__(self)
        self.logfilename = logfilename
        self.style_name = style_name

    def getChild(self, name, request):
        if '.' in name:
            return NoResource()
        page = LogViewPage(self.logfilename, name)
        page.isLeaf = True
        return page

    def render_GET(self, request):
        try:
            style = get_style_by_name(self.style_name)
        except ClassNotFound:
            style = get_style_by_name('default')
        formatter = HtmlFormatter(full=True, style=style)
        with open(self.logfilename, 'r') as logfile:
            html = highlight(logfile.read(), IrcLogsLexer(), formatter)
        request.setHeader('Content-Type', 'text/html;charset=utf-8')
        return html.encode('utf-8')


class IMMixin(object):
    def initialized(self):
        self.xmlstream.addObserver('/message[@type="chat"]/body',
                                   self.receivedChat)
        self.send(AvailablePresence())

    def receivedChat(self, message):
        pass


class KITBot(muc.MUCClient, IMMixin):
    def __init__(self, room_jid, password='', logpath=os.curdir):
        muc.MUCClient.__init__(self)
        self.room_jid = room_jid
        self.room_password = password
        self.logger = ChatLogger(self.room_jid.user + '.log', logpath)

    def initialized(self):
        IMMixin.initialized(self)

        if self.room_password:
           self.password(self.room_jid, self.room_password)
        self.join(self.room_jid.host, self.room_jid.user,
                  self.room_jid.resource)

    def receivedGroupChat(self, room, user, body):
        if body.startswith('/me '):
            self.logger.action(user.nick, body[len('/me '):])
        else:
            self.logger.message(user.nick, body)

    def receivedSubject(self, room, body):
        self.logger.write_line('-!- Topic for %s: %s' % (room.user, body))

    def userJoinedRoom(self, room, user):
        self.logger.write_line('-!- %s has joined %s' % (user.nick,
                                                         room.roomIdentifier))

    def userLeftRoom(self, room, user):
        self.logger.write_line('-!- %s has left %s' % (user.nick,
                                                       room.roomIdentifier))
