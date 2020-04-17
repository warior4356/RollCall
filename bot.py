# bot.py

import discord
import cfg
from dotenv import load_dotenv
import database
from esipy import EsiApp
from esipy import EsiClient
from esipy import EsiSecurity
from datetime import datetime
from datetime import timezone
from datetime import timedelta
import asyncio

load_dotenv()
token = cfg.token
wait_time = 30

connection = database.create_connection(
    "rollcall", "postgres", cfg.db_password, "127.0.0.1", "5432"
)
connection.autocommit = True
cursor = connection.cursor()

esi_app = EsiApp()
app = esi_app.get_latest_swagger

security = EsiSecurity(
    redirect_uri='http://localhost:5000/tokens/new',
    client_id='1922eb4bb2294e1ab3f47f15b50de475',
    secret_key= cfg.secret,
    headers={'User-Agent': cfg.agent},
)

esi_client = EsiClient(
    retry_requests=True,
    headers={'User-Agent': cfg.agent},
    security=security
)

class MyClient(discord.Client):
    async def start_tracking(self, fleet_commander, message):
        commander_id = await self.get_character_id(fleet_commander)
        if not commander_id:
            await message.channel.send("Invalid FC name, tracking disabled. See help.")
            return
        i = 0
        while True:
            access_token = await self.get_access_token(commander_id, message)
            if not access_token:
                return
            fleet_id = await self.get_fleet_id(commander_id, access_token)
            if fleet_id.status == 200:
                break
            await asyncio.sleep(30)
            if i == 60:
                return
            i += 1

        insert_query = (
            "INSERT INTO fleets (date, fleet_id, fc, duration) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING;"
        )
        cursor.execute(insert_query, (datetime.now(timezone.utc), fleet_id.data.get('fleet_id'), commander_id, 0,))
        while True:
            await asyncio.sleep(wait_time)
            access_token = await self.get_access_token(commander_id, message)
            if not access_token:
                return
            fleet_id = await self.get_fleet_id(commander_id, access_token)
            if not fleet_id.status == 200:
                break

            cursor.execute("SELECT duration FROM fleets WHERE fleet_id = %s;",
                           (fleet_id.data.get('fleet_id'),))
            row = cursor.fetchone()

            update_query = (
                "UPDATE fleets SET duration = %s WHERE fleet_id = %s;"
            )
            cursor.execute(update_query, ((int(row[0]) + wait_time), fleet_id.data.get('fleet_id'),))

            status = await self.get_fleet_data(fleet_id.data.get('fleet_id'), access_token)
            if not status == 200:
                break

    async def add_name(self, char_id):
        cursor.execute("SELECT * FROM names WHERE char_id = %s;",
                       (char_id,))
        row = cursor.fetchone()
        if not row:
            get_name_opp = app.op['get_characters_character_id'](character_id=char_id,)
            response = esi_client.request(get_name_opp)
            name = response.data['name']
            insert_query = (
                "INSERT INTO names (char_id, name, role) VALUES"
                " (%s, %s, %s) ON CONFLICT DO NOTHING;"
            )
            cursor.execute(insert_query, (char_id, name, "UNSET"))

    async def add_ship(self, ship_id):
        cursor.execute("SELECT * FROM ships WHERE ship_id = %s;",
                       (ship_id,))
        row = cursor.fetchone()
        if not row:
            get_ship_opp = app.op['get_universe_types_type_id'](type_id=ship_id,)
            response = esi_client.request(get_ship_opp)
            name = response.data['name']
            insert_query = (
                "INSERT INTO ships (ship_id, ship_name) VALUES"
                " (%s, %s) ON CONFLICT DO NOTHING;"
            )
            cursor.execute(insert_query, (ship_id, name,))

    async def get_fleet_data(self, fleet_id, access_token):
        fleet_info_opp = app.op['get_fleets_fleet_id_members'](fleet_id=fleet_id, token=access_token,)
        response = esi_client.request(fleet_info_opp)
        if response.status == 200:
            for member in response.data:
                await self.add_name(member.get('character_id'))
                await self.add_ship(member.get('ship_type_id'))
                cursor.execute("SELECT duration FROM members WHERE char_id = %s AND fleet_id = %s AND ship_id = %s;",
                               (member.get('character_id'), fleet_id, member.get('ship_type_id'),))
                row = cursor.fetchone()
                if row:
                    update_query = (
                        "UPDATE members SET duration = %s WHERE char_id = %s AND fleet_id = %s AND ship_id = %s;"
                    )
                    cursor.execute(update_query, ((int(row[0])+wait_time), member.get('character_id'), fleet_id,
                                                  member.get('ship_type_id'),))
                else:
                    insert_query = (
                        "INSERT INTO members (char_id, fleet_id, ship_id, duration) VALUES"
                        " (%s, %s, %s, %s) ON CONFLICT DO NOTHING;"
                    )
                    cursor.execute(insert_query, (member.get('character_id'),
                                                  fleet_id, member.get('ship_type_id'), wait_time,))
        return response.status

    async def get_access_token(selfs, character_id, message):
        cursor.execute("SELECT access_token, expires, refresh_token FROM commanders WHERE char_id = %s;", (character_id,))
        row = cursor.fetchone()
        if not row:
            url = (security.get_auth_uri(state='SomeRandomGeneratedState', scopes=['esi-fleets.read_fleet.v1']))
            alert = ('I\'m sorry {0.author.mention}, you are not in my database. Please go to {1} and try again.'.format(message, url))
            await message.channel.send(alert)
            return
        else:
            if datetime.now(timezone.utc) > row[1]:
                security.update_token({
                    'access_token': '',  # leave this empty
                    'expires_in': -1,  # seconds until expiry, so we force refresh anyway
                    'refresh_token': row[2]
                })
                tokens = security.refresh()

                expiration = datetime.now(timezone.utc)
                expiration += timedelta(seconds=tokens.get('expires_in'))
                update_query = (
                    "UPDATE commanders SET access_token = %s, expires = %s WHERE char_id = %s;"
                )
                cursor.execute(update_query, (tokens.get('access_token'), expiration, character_id,))

                return tokens.get('access_token')
            else:
                security.update_token({
                    'access_token': row[0],
                    'expires_in': (row[1]-datetime.now(timezone.utc)).total_seconds(),
                    'refresh_token': row[2]
                })
                return row[0]

    async def get_character_id(self, character_name):
        cursor.execute("SELECT char_id FROM names WHERE names.name = %s;",
                       (character_name,))
        row = cursor.fetchone()
        if row:
            return row[0]
        char_id_opp = app.op['get_search'](categories='character', search=character_name, strict='true',)
        response = esi_client.request(char_id_opp)
        if response.status == 200:
            if (response.data):
                return response.data['character'][0]
            else:
                return 0

    async def get_fleet_id(self, character_id, access_token):
        fleet_id_opp = app.op['get_characters_character_id_fleet'](character_id=character_id, token=access_token,)
        response = esi_client.request(fleet_id_opp)
        return response

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
            return

        if message.content.startswith('!RC trackfleet'):
            fleet_commander = message.content.split(' ', 2)[2]
            await self.start_tracking(fleet_commander, message)
            return

        if str(message.author.id) not in cfg.authorized:
            await message.channel.send("I DO NOT RESPECT YOUR AUTHORITY PRIVATE!")

        elif message.content.startswith('!RC set'):
            role = message.content.split(' ', 3)[2]
            name = message.content.split(' ', 3)[3]
            char_id = await self.get_character_id(name)
            if not char_id:
                await message.channel.send("Invalid name. See help.")
                return
            update_query = (
                "UPDATE names SET role = %s WHERE char_id = %s;"
            )
            cursor.execute(update_query, (role, char_id,))

        elif message.content.startswith('!RC member'):
            count = message.content.split(' ', 3)[2]
            member = message.content.split(' ', 3)[3]
            char_id = await self.get_character_id(member)
            if not char_id:
                await message.channel.send("Invalid member name. See help.")
                return
            cursor.execute("SELECT DISTINCT fleets.fleet_id, fleets.fc, fleets.date, fleets.duration "
                           "FROM members LEFT JOIN fleets on members.fleet_id=fleets.fleet_id "
                           "WHERE members.char_id = %s LIMIT %s;",
                           (char_id, count,))
            rows = cursor.fetchall()
            output = "```Listing {0}'s last {1} fleets\n". format(member, count)
            output += "Fleet ID       | Date       | Fleet Duration | Fleet Commander      | Ships (Ship, Minutes)\n"
            for row in rows:
                cursor.execute("SELECT names.name "
                               "from names where names.char_id = %s;",
                               (row[1],))
                fc = cursor.fetchone()
                cursor.execute("SELECT ships.ship_name, CAST(round(members.duration/60) AS integer) "
                               "from ships LEFT JOIN members on members.ship_id=ships.ship_id"
                               " WHERE members.fleet_id = %s AND members.char_id = %s;",
                               (row[0], row[1],))
                ships = cursor.fetchall()
                output += "{0:014} | {1} |   {2:04} Minutes | {3: <20} | {4}".format(
                    row[0], row[2].date(), int(row[3]/60), fc[0], ships)
            output += "```"
            await message.channel.send(output)

        elif message.content.startswith('!RC list'):
            start = message.content.split(' ', 3)[2]
            end = message.content.split(' ', 3)[3]
            cursor.execute("SELECT DISTINCT fleets.fleet_id, fleets.date, fleets.duration, fleets.fc "
                           "from fleets where fleets.date between %s and %s;",
                           (start, end,))
            rows = cursor.fetchall()
            output = "```Listing fleets from {0} to {1}\n".format(start, end)
            output += "Fleet ID       | Date       | Fleet Duration | Members | Fleet Commander\n"
            for row in rows:
                cursor.execute("SELECT count(DISTINCT members.char_id) "
                               "from members where members.fleet_id = %s;",
                               (row[0],))
                count = cursor.fetchone()
                cursor.execute("SELECT names.name "
                               "from names where names.char_id = %s;",
                               (row[3],))
                fc = cursor.fetchone()
                output += "{0:014} | {1} |   {2:04} Minutes |     {3:03} | {4}\n".format(
                    row[0], row[1].date(), int(row[2]/60), count[0], fc[0])
            output += "```"
            await message.channel.send(output)

        elif message.content.startswith('!RC stats'):
            role = message.content.split(' ', 4)[2]
            start = message.content.split(' ', 4)[3]
            end = message.content.split(' ', 4)[4]
            cursor.execute("SELECT names.char_id, names.name, count(distinct members.fleet_id), "
                           "count(distinct fleets.fleet_id) FROM names LEFT JOIN members ON "
                           "names.char_id = members.char_id LEFT JOIN fleets on fleets.fleet_id = members.fleet_id "
                           "WHERE fleets.date > %s AND fleets.date < %s AND names.role = %s group by 1;",
                           (start, end, role,))
            rows = cursor.fetchall()
            output = "```Listing " + str(role) + " from " + str(start) + " to " + str(end) + "\n"
            output += "Name                 | Fleets as Member | Fleets as FC | Total Fleet Time\n"
            for row in rows:
                cursor.execute("SELECT round(sum(duration)/60) FROM members "
                               "LEFT JOIN fleets on fleets.fleet_id = members.fleet_id "
                               "WHERE char_id = %s AND fleets.date > %s AND fleets.date < %s;",
                               (row[0], start, end,))
                fleet_time = cursor.fetchone()
                output += "{0: <20} |             {1:04} |         {2:04} |   {3:06} Minutes\n".format(
                    row[1], row[2] - row[3], row[3], int(fleet_time[0]))
            output += "```"
            await message.channel.send(output)

        elif message.content.startswith('!RC fleet'):
            fleet_id = message.content.split(' ', 2)[2]
            cursor.execute("SELECT DISTINCT members.char_id, fleets.fc, fleets.date, "
                           "fleets.duration, names.name "
                           "FROM fleets LEFT JOIN members on fleets.fleet_id=members.fleet_id "
                           "Left JOIN names on members.char_id=names.char_id "
                           "WHERE fleets.fleet_id = %s ORDER BY 1 DESC;",
                           (fleet_id,))
            rows = cursor.fetchall()
            cursor.execute("SELECT names.name FROM names WHERE names.char_id = %s;", (rows[0][1],))
            commander = cursor.fetchone()
            output = "```Listing members of fleet {0} on {1} lasting {2} minutes led by {3}\n". format(
                fleet_id, rows[0][2], int(rows[0][3]/60), commander[0]
            )
            output += "Name                 | Time On Fleet | Ships\n"
            for row in rows:
                cursor.execute("SELECT round(sum(members.duration)/60) FROM fleets "
                               "LEFT JOIN members on fleets.fleet_id = members.fleet_id "
                               "WHERE char_id = %s AND fleets.fleet_id = %s;",
                               (row[0], fleet_id,))
                fleet_time = cursor.fetchone()
                cursor.execute("SELECT ships.ship_name, CAST(round(members.duration/60) AS integer) "
                               "from ships LEFT JOIN members on members.ship_id=ships.ship_id"
                               " WHERE members.fleet_id = %s AND members.char_id = %s;",
                               (fleet_id, row[0],))
                ships = cursor.fetchall()
                output += "{0: <20} |          {1:04} | {2}\n".format(
                    row[4], int(fleet_time[0]), ships
                )
            output += "```"
            await message.channel.send(output)

        elif message.content.startswith('!RC'):
            await message.channel.send("RollCall Commands:\n"
                                       "!RC trackfleets <FC name> - Starts tracking a fleet under <FC name>\n"
                                       "!RC member <Count> <Member Name> - Lists member's last <Count> fleets\n"
                                       "!RC list <start date> <end date> - Lists all fleets from "
                                       "<start date> to <end date>\n"
                                       "!RC stats <type> <start date> <end date> - Lists <type> statistics from "
                                       "<start date> to <end date>\n"
                                       "!RC fleet <fleet id> - Lists all information about <fleet id>")

        # if message.author.id == 89831133709103104:
        #     lines = message.content.splitlines()
        #     fleet_commander = lines[4].split(' ',1)[1]
        #     await message.channel.send(fleet_commander)
        #     await self.start_tracking(fleet_commander, message)


discord_client = MyClient()
discord_client.run(token)