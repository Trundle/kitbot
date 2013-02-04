# encoding: utf-8


"""
    kitbot
    ~~~~~~

    A simple logging bot.

    Copyright (C) 2009-2013 Andreas Stührk
"""

from __future__ import with_statement
import codecs
import collections
import glob
import imp
import os
from datetime import date, datetime, timedelta

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers.text import IrcLogsLexer
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound
from twisted.cred.portal import IRealm
from twisted.python import log
from twisted.python.logfile import DailyLogFile
from twisted.web import xmlrpc
from twisted.web.resource import IResource, NoResource, Resource
from wokkel import muc
from wokkel.xmppim import AvailablePresence
from zope.interface import implements


CSSFILE_TEMPLATE = '''\
td.linenos { background-color: #f0f0f0; padding-right: 10px; }
span.lineno { background-color: #f0f0f0; padding: 0 5px 0 5px; }
pre { line-height: 125%%; }
%(styledefs)s
'''

DOC_HEADER = '''\
<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN"
   "http://www.w3.org/TR/html4/strict.dtd">

<html>
<head>
  <title>%(title)s</title>
  <meta http-equiv="content-type" content="text/html; charset=%(encoding)s">
  <style type="text/css">
''' + CSSFILE_TEMPLATE + '''
  </style>
  <script type="text/javascript" src="/jsMath/easy/load.js"></script>
</head>
<body>
<h2>%(title)s</h2>

'''


DOC_FOOTER = '''\
</body>
</html>
'''

MENSA_URL = "http://www.studentenwerk-karlsruhe.de/en/essen/"


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
    def _wrap_pre(self, inner):
        # Oh noes, we overwrite an internal method, but Pygments has no
        # official API to do that.
        yield 0, ('<pre class="tex2math_process"'
                  + (self.prestyles and ' style="%s"' % self.prestyles) + '>')
        for tup in inner:
            yield tup
        yield 0, '</pre>'


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
        formatter = LogFormatter(style=style)

        if self.days_back:
            log_date = date.today() - timedelta(self.days_back)
            suffix = log_date.strftime('.%Y_%m_%d').replace('_0', '_')
            self.logfilename += suffix
        try:
            with codecs.open(self.logfilename, 'r', 'utf-8') as logfile:
                html = self.render_log(logfile.read(), formatter,
                                       prev_url, next_url)
        except IOError:
            request.setResponseCode(404)
            return '<html><body>Go away.</body></html>'
        request.setHeader('Content-Type', 'text/html;charset=utf-8')
        return html.encode('utf-8')

    def render_log(self, source, formatter, prev_url, next_url):
        html = [
            DOC_HEADER % dict(title='',
                              styledefs=formatter.get_style_defs('body'),
                              encoding='utf-8'),
        ]
        if prev_url:
            html.append(u'<a href="%s">Zurück</a>' % (prev_url, ))
        if next_url:
            html.append(u'<a href="%s">Weiter</a>' % (next_url, ))
        html.append(highlight(source, IrcLogsLexer(), formatter))
        if prev_url:
            html.append(u'<a href="%s">Zurück</a>' % (prev_url, ))
        if next_url:
            html.append(u'<a href="%s">Weiter</a>' % (next_url, ))
        html.append(DOC_FOOTER)
        return ''.join(html)

    def url_for(self, request, days_back):
        prepath = list(request.prepath)
        if self.days_back is not None:
            prepath.pop()
        if self.style_name and self.isLeaf:
            prepath.pop()
        url = '/%s/%s/' % ('/'.join(prepath), days_back)
        if self.style_name:
            url += self.style_name
        return url

class XMLRPCInterface(xmlrpc.XMLRPC):
    def __init__(self, bot, *args, **kwargs):
        xmlrpc.XMLRPC.__init__(self, *args, **kwargs)
        self.bot = bot

    def xmlrpc_say(self, channel_id, message):
        try:
            room = self.bot.rooms[channel_id]
        except KeyError:
            return False
        room.groupChat(room.room_jid, message)
        return True


class IMMixin(object):
    def connectionInitialized(self):
        self.xmlstream.addObserver('/message[@type="chat"]/body',
                                   self.receivedChat)
        self.send(AvailablePresence())

    def receivedChat(self, message):
        pass


class KITBot(muc.MUCClient, IMMixin):
    def __init__(self, room_jid, password='', logpath=os.curdir):
        muc.MUCClient.__init__(self)
        self.room_jid = room_jid
        self.nick = room_jid.resource
        # Set resource to None, otherwise
        # self.groupChat(self.room_jid, …) won't work as expected
        room_jid.resource = None
        self.room_password = password
        self.logger = ChatLogger(self.room_jid.user + '.log', logpath)

    def connectionInitialized(self):
        muc.MUCClient.connectionInitialized(self)
        IMMixin.connectionInitialized(self)

        if self.room_password:
           self.password(self.room_jid, self.room_password)
        self.join(self.room_jid, self.nick)

    def receivedGroupChat(self, room, user, message):
        body = message.body
        if body.startswith('/me '):
            self.logger.action(user.nick, body[len('/me '):])
        else:
            self.logger.message(user.nick, body)
        emit("groupchat-received", self, room, user, message)

    def receivedSubject(self, room, user, subject):
        self.logger.write_line(
            '-!- Topic for %s: %s' % (room.roomJID.user, subject))

    def userJoinedRoom(self, room, user):
        room_name = room.roomJID.userhost()
        self.logger.write_line('-!- %s has joined %s' % (user.nick, room_name))
        emit("user-joined-room", self, room, user)

    def userLeftRoom(self, room, user):
        room_name = room.roomJID.userhost()
        self.logger.write_line('-!- %s has left %s' % (user.nick, room_name))


def load_plugins(config, path=None):
    "Load all Python modules in `path` and call their `init` function."
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "plugins")
    for plugin_path in glob.glob(os.path.join(path, "*.py")):
        name = os.path.basename(plugin_path[:-3])
        try:
            (fo, pathname, descr) = imp.find_module(name, [path])
        except (ImportError, SyntaxError):
            continue
        plugin = imp.load_module(name, fo, pathname, descr)
        plugin.init(config.get(name, {}))


class _Observable(object):
    def __init__(self):
        self.observers = collections.defaultdict(list)

    def connect(self, signal, callback, *args, **kwargs):
        self.observers[signal].append((callback, args, kwargs))

    def emit(self, signal, *args):
        for (callback, cb_args, cb_kwargs) in self.observers[signal]:
            try:
                callback(*(args + cb_args), **cb_kwargs)
            except Exception:
                log.err()


_observable = _Observable()
connect = _observable.connect
emit = _observable.emit
