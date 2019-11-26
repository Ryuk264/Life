import asyncio
import json
import os
import time

import aiohttp
import asyncpg
import config
import psutil
from discord.ext import commands

from Life.Life.cogs.music.player import Player
from Life.Life.cogs.utilities.paginators import Paginator, CodeBlockPaginator, EmbedPaginator, EmbedsPaginator

os.environ["JISHAKU_HIDE"] = "True"
os.environ["JISHAKU_NO_UNDERSCORE"] = "True"

EXTENSIONS = [
    "cogs.information",
    "cogs.fun",
    "cogs.help",
    "cogs.owner",
    "jishaku",
    "cogs.background",
    "cogs.events",
    "cogs.music.music",
    #"cogs.rpg.accounts",
]


class Life(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.DISCORD_PREFIX),
            reconnect=True,
        )
        self.loop = asyncio.get_event_loop()
        self.session = aiohttp.ClientSession()

        self.config = config
        self.start_time = time.time()
        self.process = psutil.Process()

        self.db = None
        self.db_ready = False

        self.owner_ids = {238356301439041536}
        self.user_blacklist = []
        self.guild_blacklist = []

        self.usage = {}

        for extension in EXTENSIONS:
            try:
                self.load_extension(extension)
                print(f"[EXT] Success - {extension}")
            except commands.ExtensionNotFound:
                print(f"[EXT] Failed - {extension}")

    def run(self):
        try:
            self.loop.run_until_complete(self.bot_start())
        except KeyboardInterrupt:
            self.loop.run_until_complete(self.bot_close())

    async def db_connect(self):
        # Try to connect to the database.
        try:
            self.db = await asyncpg.create_pool(**config.DB_CONN_INFO)
            print(f"\n[DB] Connected to database.")

            # Create tables if the dont exist.
            print("\n[DB] Creating tables.")
            with open("schema.sql") as r:
                await self.db.execute(r.read())
            print("[DB] Done creating tables.")

            # Tell the bot that the databse is ready.
            self.db_ready = True

            # Fetch command usage from database.
            usage = await self.db.fetch("SELECT * FROM bot_usage")

            # Add the usage of each guild to the bots usage.
            for guild in usage:
                self.usage[guild["id"]] = json.loads(guild["usage"])

            # Fetch user/guild blacklists.
            blacklisted_users = await self.db.fetch("SELECT * FROM user_blacklist")
            blacklisted_guilds = await self.db.fetch("SELECT * FROM guild_blacklist")

            # Append blacklisted users and guilds to the respective blacklists.
            for user in range(len(blacklisted_users)):
                self.user_blacklist.append(int(blacklisted_users[user]["id"]))
            for user in range(len(blacklisted_guilds)):
                self.guild_blacklist.append(int(blacklisted_guilds[user]["id"]))

        # Accept any exceptions we might find.
        except ConnectionRefusedError:
            print(f"\n[DB] Connection to database was denied.")
        except Exception as e:
            print(f"\n[DB] An error occured: {e}")

    async def bot_start(self):
        await self.db_connect()
        await self.login(config.DISCORD_TOKEN)
        await self.connect()

    async def bot_close(self):
        await super().logout()
        await self.session.close()

    async def is_owner(self, user):
        # Set custom owner ids.
        return user.id in self.owner_ids

    async def on_message(self, message):

        if message.author.bot:
            return

        ctx = await self.get_context(message)

        if ctx.command:
            if message.author.id in self.user_blacklist:
                return await message.channel.send(f"Sorry, you are blacklisted.")
            await self.process_commands(message)

    async def on_message_edit(self, before, after):

        # If the edited message is not embedded or pinned, process it, this allows for users to edit commands.
        if not after.embeds and not after.pinned and not before.pinned and not before.embeds:

            # Get the context of the message.
            ctx = await self.get_context(after)

            # If the message was a command.
            if ctx.command:
                # And the author is in the user blacklist, dont process the command.
                if after.author.id in self.user_blacklist:
                    return await after.channel.send(f"Sorry, you are blacklisted.")
                # Otherwise, process the message.
                await self.process_commands(after)

    async def get_context(self, message, *, cls=None):
        return await super().get_context(message, cls=MyContext)

class MyContext(commands.Context):

    @property
    def player(self):
        return self.bot.granitepy.get_player(self.guild.id, cls=Player)

    async def paginate(self, **kwargs):

        paginator = Paginator(ctx=self, **kwargs)
        return await paginator.paginate()

    async def paginate_embed(self, **kwargs):

        paginator = EmbedPaginator(ctx=self, **kwargs)
        return await paginator.paginate()

    async def paginate_codeblock(self, **kwargs):

        paginator = CodeBlockPaginator(ctx=self, **kwargs)
        await paginator.paginate()

    async def paginate_embeds(self, **kwargs):

        paginator = EmbedsPaginator(ctx=self, **kwargs)
        return await paginator.paginate()


if __name__ == "__main__":
    Life().run()
