from bot import connect

def on_groupchat_received(bot, room, user, message):
    if message.body.lower() == "ping":
        bot.groupChat(bot.room_jid, "pong")

def init(config):
    connect("groupchat-received", on_groupchat_received)
