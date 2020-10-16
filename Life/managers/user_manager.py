"""
Life
Copyright (C) 2020 MrRandom#9258

Life is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later version.

Life is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.  See the GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with Life. If not, see <https://www.gnu.org/licenses/>.
"""
import io
import math
import os
import typing

import discord
import pendulum
from PIL import Image, ImageDraw, ImageFont
from discord.ext import tasks

from utilities import exceptions, objects


class UserConfigManager:

    def __init__(self, bot) -> None:
        self.bot = bot

        self.default_user_config = objects.DefaultUserConfig()
        self.configs = {}

        self.update_database.start()

    @tasks.loop(minutes=2)
    async def update_database(self) -> None:

        if not self.configs:
            return

        need_updating = {user_id: config for user_id, config in self.configs.items() if config.requires_db_update is True}

        async with self.bot.db.acquire(timeout=300) as db:
            for user_id, user_config in need_updating.items():
                await db.execute('UPDATE user_configs SET xp = $2 WHERE id = $1', user_id, user_config.xp)

    @update_database.before_loop
    async def before_update_database(self) -> None:
        await self.bot.wait_until_ready()

    #

    async def load(self) -> None:

        user_configs = await self.bot.db.fetch('SELECT * FROM user_configs')
        for user_config in user_configs:
            self.configs[user_config['id']] = objects.UserConfig(data=dict(user_config))

        print(f'[POSTGRESQL] Loaded user configs. [{len(user_configs)} users(s)]')

    async def create_user_config(self, *, user_id: int) -> objects.UserConfig:

        data = await self.bot.db.fetchrow('INSERT INTO user_configs (id) values ($1) ON CONFLICT (id) DO UPDATE SET id = excluded.id RETURNING *', user_id)
        self.configs[user_id] = objects.UserConfig(data=dict(data))

        return self.get_user_config(user_id=user_id)

    def get_user_config(self, *, user_id: int) -> typing.Union[objects.DefaultUserConfig, objects.UserConfig]:
        return self.configs.get(user_id, self.default_user_config)

    async def edit_user_config(self, *, user_id: int, attribute: str, operation: str = 'set', value: typing.Any = None) -> objects.UserConfig:

        user_config = self.get_user_config(user_id=user_id)
        if isinstance(user_config, objects.DefaultUserConfig):
            user_config = await self.create_user_config(user_id=user_id)

        if attribute == 'colour':

            if operation == 'set':
                data = await self.bot.db.fetchrow('UPDATE user_configs SET colour = $1 WHERE id = $2 RETURNING colour', value, user_id)
            elif operation == 'reset':
                data = await self.bot.db.fetchrow('UPDATE user_configs SET colour = $1 WHERE id = $2 RETURNING colour', f'0x{str(discord.Colour.gold()).strip("#")}', user_id)
            else:
                raise exceptions.LifeError('Invalid operation code.')

            user_config.colour = discord.Colour(int(data['colour'], 16))

        elif attribute == 'timezone':

            if operation == 'set':
                data = await self.bot.db.fetchrow('UPDATE user_configs SET timezone = $1 WHERE id = $2 RETURNING timezone', value, user_id)
            elif operation == 'reset':
                data = await self.bot.db.fetchrow('UPDATE user_configs SET timezone = $1 WHERE id = $2 RETURNING timezone', 'UTC', user_id)
            else:
                raise exceptions.LifeError('Invalid operation code.')

            user_config.timezone = pendulum.timezone(data['timezone'])

        elif attribute == 'timezone_private':

            if operation == 'set':
                data = await self.bot.db.fetchrow('UPDATE user_configs SET timezone_private = $1 WHERE id = $2 RETURNING timezone_private', True, user_id)
            elif operation == 'reset':
                data = await self.bot.db.fetchrow('UPDATE user_configs SET timezone_private = $1 WHERE id = $2 RETURNING timezone_private', False, user_id)
            else:
                raise exceptions.LifeError('Invalid operation code.')

            user_config.timezone_private = data['timezone_private']

        elif attribute == 'blacklist':

            if operation == 'set':
                query = 'UPDATE user_configs SET blacklisted = $1, blacklisted_reason = $2 WHERE id = $3 RETURNING blacklisted, blacklisted_reason'
                data = await self.bot.db.fetchrow(query, True, value, user_id)
            elif operation == 'reset':
                query = 'UPDATE user_configs SET blacklisted = $1, blacklisted_reason = $2 WHERE id = $3 RETURNING blacklisted, blacklisted_reason'
                data = await self.bot.db.fetchrow(query, False, 'None', user_id)
            else:
                raise exceptions.LifeError('Invalid operation code.')

            user_config.blacklisted = data['blacklisted']
            user_config.blacklisted_reason = data['blacklisted_reason']

        elif attribute == 'xp':

            if operation == 'set':
                user_config.xp = value
            elif operation == 'add':
                user_config.xp += value
            elif operation == 'minus':
                user_config.xp -= value
            else:
                raise exceptions.LifeError('Invalid operation code.')

            user_config.requires_db_update = True

        return user_config

    #

    async def create_timecard(self, *, guild_id: int) -> io.BytesIO:

        guild = self.bot.get_guild(guild_id)
        if not guild:
            raise exceptions.ArgumentError('Guild with that id not found.')

        configs = sorted(self.configs.items(), key=lambda kv: kv[1].time.offset_hours)
        timezone_users = {}

        for user_id, config in configs:

            if config.timezone_private or config.timezone == pendulum.timezone('UTC'):
                continue

            user = guild.get_member(user_id)
            if not user:
                continue

            if timezone_users.get(config.time.format('HH:mm (ZZ)')) is None:
                timezone_users[config.time.format('HH:mm (ZZ)')] = [io.BytesIO(await user.avatar_url_as(format='png', size=256).read())]

            else:
                timezone_users[config.time.format('HH:mm (ZZ)')].append(io.BytesIO(await user.avatar_url_as(format='png', size=256).read()))

        if not timezone_users:
            raise exceptions.ArgumentError('There are no users with timezones set in this server.')

        buffer = await self.bot.loop.run_in_executor(None, self.create_timecard_image, timezone_users)
        return buffer

    def create_timecard_image(self, timezone_users: dict) -> io.BytesIO:

        width_x = (1600 * (len(timezone_users.keys()) if len(timezone_users.keys()) < 5 else 5)) + 100
        height_y = (1800 * math.ceil(len(timezone_users.keys()) / 5)) + 100

        image = Image.new('RGBA', (width_x, height_y), color='#f1c30f')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(os.path.join(os.path.dirname(__file__), 'arial.ttf'), 120)

        x = 100
        y = 100

        for timezone, users in timezone_users.items():

            draw.text((x, y), timezone, font=font, fill='#1b1a1c')

            user_x = x
            user_y = y + 200

            for user in users[:36]:

                avatar = Image.open(user)
                avatar = avatar.resize((250, 250))

                image.paste(avatar, (user_x, user_y))

                if user_x < x + 1200:
                    user_x += 250
                else:
                    user_y += 250
                    user_x = x

            if x > 6400:
                y += 1800
                x = 100
            else:
                x += 1600

        buffer = io.BytesIO()
        image.save(buffer, 'png')
        buffer.seek(0)

        return buffer

    #

    def rank(self, *, guild_id: int, user_id: int) -> int:

        guild = self.bot.get_guild(guild_id)
        if not guild:
            raise exceptions.ArgumentError('Guild with that id not found.')

        member = guild.get_member(user_id)
        if not member:
            raise exceptions.ArgumentError('Member with that id not found in guild.')

        member_ids = [member.id for member in guild.members]
        configs = sorted({user_id: config for user_id, config in self.configs.items() if user_id in member_ids}.items(), key=lambda kv: kv[1].level, reverse=True)
        return [config[0] for config in configs].index(member.id) + 1

    def leaderboard(self, *, guild_id: int, leaderboard_type: str) -> typing.List[objects.UserConfig]:

        guild = self.bot.get_guild(guild_id)
        if not guild:
            raise exceptions.ArgumentError('Guild with that id not found.')

        configs = {user_id: config for user_id, config in self.configs.items() if user_id in [member.id for member in guild.members]}.items()
        return sorted(configs, key=lambda kv: getattr(kv[1], leaderboard_type), reverse=True)