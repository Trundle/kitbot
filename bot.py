# encoding: utf-8


"""
    kitbot
    ~~~~~~

    A simple logging bot.

    Copyright (C) 2009-2011 Andreas Stührk
"""

from __future__ import with_statement
import codecs
import collections
import functools
import os
from datetime import date, datetime, timedelta

from lxml import cssselect, html
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers.text import IrcLogsLexer
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound
from twisted.cred.portal import IRealm
from twisted.enterprise import adbapi
from twisted.internet import defer
from twisted.python.logfile import DailyLogFile
from twisted.web import xmlrpc
from twisted.web.client import getPage
from twisted.web.error import NoResource
from twisted.web.resource import IResource, Resource
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

def interaction(func):
    """Convenient decorator for `t.e.a.ConnectionPool`"""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        return self.dbpool.runInteraction(
            functools.partial(func, self),
            *args, **kwargs
        )
    return wrapper

class DatabaseRunner(object):
    def __init__(self, database):
        self.dbpool = adbapi.ConnectionPool(
            "sqlite3", database,
            check_same_thread=False
        )

    @interaction
    def add_message(self, transaction, room_jid, from_, to, message):
        transaction.execute("""
            INSERT INTO postponed_messages
                        (from_, to_, room, message)
            VALUES      (?, ?, ?, ?)
        """, (from_, to, room_jid.userhost(), message))
        return bool(transaction.rowcount)

    @interaction
    def get_messages(self, transaction, room_jid, name):
        transaction.execute("""
            SELECT id, from_, to_, message
            FROM   postponed_messages
            WHERE  room = ?
         """, (room_jid.userhost(), ))
        messages = list()
        to_delete = list()
        for (id_, from_, to_, message) in transaction:
            if name.startswith(to_):
                to_delete.append(id_)
                messages.append((from_, message))
        if to_delete:
            transaction.execute("""
                DELETE FROM postponed_messages
                WHERE       room = ?
                            AND id IN (%s)""" %
                                ",".join(('?', ) * len(to_delete)),
                [room_jid.userhost()] + to_delete
             )
        return messages
        

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
        if self.style_name:
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
        body_lower = body.strip().lower()
        nick_lower = self.room_jid.resource.lower()
        if body_lower == 'ping':
            self.groupChat(self.room_jid, 'pong')
        elif body_lower in ["%s: mensa" % (nick_lower, ),
                            "%s: mensa heute" % (nick_lower, ),
                            "%s: mensa morgen" % (nick_lower, ),
                            "%s: mensa njam" % (nick_lower, )]:
            days = [1]
            if u"morgen" in body:
                days = [2]
            elif u"njam" in body:
                days = range(1, 6)
            d = getPage(MENSA_URL)
            d.addCallback(process_mensa, self, user, days)
        elif body_lower.startswith(nick_lower + ": message "):
            try:
                (_, _, receiver, message) = body.split(None, 3)
            except ValueError:
                pass
            else:
                self.parent.dbpool.add_message(self.room_jid,
                                               user.nick, receiver, message)

    def receivedSubject(self, room, body):
        self.logger.write_line('-!- Topic for %s: %s' % (room.user, body))

    @defer.inlineCallbacks
    def userJoinedRoom(self, room, user):
        self.logger.write_line('-!- %s has joined %s' % (user.nick,
                                                         room.roomIdentifier))
        messages = yield self.parent.dbpool.get_messages(self.room_jid,
                                                         user.nick)
        for (from_, message) in messages:
            self.groupChat(
                self.room_jid,
                '%s: %s (This message from %s has been postponed.)' %
                                                    (user.nick, message, from_)
            )

    def userLeftRoom(self, room, user):
        self.logger.write_line('-!- %s has left %s' % (user.nick,
                                                       room.roomIdentifier))

def scrape_mensa(document, day=1):
    if day not in xrange(1, 6):
        raise ValueError("day must be between 1 and 5, got " + str(day))
    selector = cssselect.CSSSelector(
        "div#canteen_place_1 div#fragment-c1-%i" % (day, )
    )
    meal_selector = cssselect.CSSSelector("span.bg")
    div = selector(document)[0]
    for element in div.findall("./table/tr/td"):
        if element.get("class") == "mensatype":
            current_line = element.text_content()
        elif element.get("class") == "mensadata":
            for meal in meal_selector(element):
                meal = meal.text_content()
                if meal.endswith(u" ab"):
                    meal = meal[:-3]
                yield (current_line, meal)

def process_mensa(string, bot, user, days):
    tree = html.fromstring(string)
    lines = list()
    for (i, day) in enumerate(days):
        if len(days) > 1:
            lines.append("In +%i Tagen:" % (day - 1, ))
        meals = collections.defaultdict(list)
        for (line, meal) in scrape_mensa(tree, day):
            meals[line].append(meal)

        for (line, meals) in sorted(meals.items()):
            if line in [u"Abend",u"Curry Queen", u"L6 Update", u"Schnitzelbar"]:
                continue
            lines.append("%s: %s" % (line, u", ".join(meals)))
        if i != (len(days) - 1):
            lines.append("")

    to = '%s/%s' % (bot.room_jid.userhost(), user.nick)
    bot.chat(to, '\n'.join(lines))
