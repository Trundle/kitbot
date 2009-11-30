# encoding: utf-8


"""
    kitbot
    ~~~~~~

    A simple logging bot.

    Copyright (C) 2009 Andreas Stührk
"""

from __future__ import with_statement
import codecs
import os
from datetime import date, datetime, timedelta

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


class LogFormatter(HtmlFormatter):
    def __init__(self, prev_url, next_url, *args, **kwargs):
        HtmlFormatter.__init__(self, *args, **kwargs)
        self.next_url = next_url
        self.prev_url = prev_url

    def wrap(self, source, outfile):
        if self.prev_url:
            yield (0, u'<a href="%s">Zurück</a>' % (self.prev_url, ))
        if self.next_url:
            yield (0, u'<a href="%s">Weiter</a>' % (self.next_url, ))

        for line in HtmlFormatter.wrap(self, source, outfile):
            yield line

        if self.prev_url:
            yield (0, u'<a href="%s">Zurück</a>' % (self.prev_url, ))
        if self.next_url:
            yield (0, u'<a href="%s">Weiter</a>' % (self.next_url, ))


class LogViewRealm(object):
    implements(IRealm)

    def __init__(self, logfilename):
        self.logfilename = logfilename

    def requestAvatar(self, avatarID, mind, *interfaces):
        if IResource in interfaces:
            return (IResource, LogViewPage(self.logfilename), lambda: None)
        raise NotImplementedError()


class LogViewPage(Resource):
    def __init__(self, logfilename, style_name='default', days_back=None):
        Resource.__init__(self)
        self.logfilename = logfilename
        self.style_name = style_name
        self.days_back = days_back

    def getChild(self, name, request):
        try:
            name = int(name)
        except ValueError:
            if '.' in name:
                return NoResource()
            page = LogViewPage(self.logfilename, name, self.days_back)
            page.isLeaf = True
            return page
        else:
            if self.days_back is not None:
                # This is ambiguous, we can only get one day parameter
                return NoResource()
            return LogViewPage(self.logfilename, self.style_name, name)

    def render_GET(self, request):
        try:
            style = get_style_by_name(self.style_name)
        except ClassNotFound:
            style = get_style_by_name('default')
            self.style_name = 'default'

        prev_url = None
        if self.days_back:
            prev_url = self.url_for(request, self.days_back - 1)
        next_url = self.url_for(request, (self.days_back or 0) + 1)
        formatter = LogFormatter(prev_url, next_url, full=True, style=style)

        if self.days_back:
            log_date = date.today() - timedelta(self.days_back)
            self.logfilename += log_date.strftime('.%Y_%m_%d')
        try:
            with codecs.open(self.logfilename, 'r', 'utf-8') as logfile:
                html = highlight(logfile.read(), IrcLogsLexer(), formatter)
        except IOError:
            request.setResponseCode(404)
            return '<html><body>Go away.</body></html>'
        request.setHeader('Content-Type', 'text/html;charset=utf-8')
        return html.encode('utf-8')

    def url_for(self, request, days_back):
        prepath = list(request.prepath)
        if self.days_back is not None:
            prepath.pop()
        if self.style_name:
            prepath.pop()
        url = '/%s/%s/' % ('/'.join(prepath), days_back)
        if self.style_name:
            url += self.style_name
        return url


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
