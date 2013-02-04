import functools

from twisted.enterprise import adbapi
from twisted.internet import defer

from bot import connect


dbpool = None

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
        name = name.lower()
        messages = list()
        to_delete = list()
        for (id_, from_, to_, message) in transaction:
            if name.startswith(to_.lower()):
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


def on_groupchat_received(bot, room, user, message):
    body = message.body
    if body.lower().startswith(bot.nick.lower() + ": message "):
            try:
                (_, _, receiver, message) = body.split(None, 3)
            except ValueError:
                pass
            else:
                if receiver.endswith(u":"):
                    # Most likely a syntax error and not part of the nick
                    receiver = receiver[:-1]
                dbpool.add_message(bot.room_jid, user.nick, receiver, message)

@defer.inlineCallbacks
def on_user_joined(bot, room, user):
    messages = yield dbpool.get_messages(bot.room_jid, user.nick)
    msg = '%s: %s (This message from %s has been postponed.)'
    for (from_, message) in messages:
        bot.groupChat(bot.room_jid,msg % (user.nick, message, from_))


def init(config):
    global dbpool
    dbpool = DatabaseRunner(config["database"])
    connect("groupchat-received", on_groupchat_received)
    connect("user-joined-room", on_user_joined)
