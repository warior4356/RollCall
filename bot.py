# bot.py
import os
import random

import discord
import cfg
import psycopg2
from dotenv import load_dotenv
from aiohttp import ClientSession

load_dotenv()
token = cfg.token


class MyClient(discord.Client):
    async def get_character_id(self, character_name):
        async with ClientSession() as session:
            async with session.get(("https://esi.evetech.net/latest/search/?categories=character&search="
                                    + character_name.replace(" ", "+") + "&strict=true")
                                    , headers={"User-Agent":cfg.email}) as response:
                if response.status == 200:
                    body =  await response.json()
                    return body.get('character')

    async def get_fleet_id(self, character_id):
        while True:
            async with ClientSession() as session:
                async with session.get(("https://esi.evetech.net/latest/characters/"
                                        + character_id + "/fleet/")
                                        , headers={"User-Agent":cfg.email}) as response:
                    if response.status == 200:
                        body =  await response.json()
                        return body.get('fleet_id')

    async def on_ready(self):
        print('Logged in as')
        print(self.user.name)
        print(self.user.id)
        print('------')

    async def on_message(self, message):
        # we do not want the bot to reply to itself
        if message.author.id == self.user.id:
            return

        if message.content.startswith('!hello'):
            await message.channel.send('Hello {0.author.mention}'.format(message))

        if message.author.id == 89831133709103104:
            lines = message.content.splitlines()
            fleet_commander = lines[4].split(' ',1)[1]
            await message.channel.send(fleet_commander)
            response = await self.get_character_id(fleet_commander)
            if not response:
                await message.channel.send("Invalid FC name, tracking disabled. See help.")
                return
            fleet_id = await self.get_fleet_id(fleet_commander)
            time = lines[7].split()[1]
            await message.channel.send(time)


client = MyClient()
client.run(token)