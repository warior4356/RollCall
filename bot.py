# bot.py

import discord
import cfg
import database
import traceback
from esipy import EsiApp
from esipy import EsiClient
from esipy import EsiSecurity
from datetime import datetime
from datetime import timezone
from datetime import timedelta
from random import randint
import asyncio

token = cfg.token
wait_time = 30

connection = database.create_connection(
    "rollcall", "postgres", cfg.db_password, "127.0.0.1", "5432"
)
connection.autocommit = True
cursor = connection.cursor()

esi_app = EsiApp()
app = esi_app.get_dev_swagger

security = EsiSecurity(
    redirect_uri='http://51.158.104.35:5000/tokens/new',
    client_id='1922eb4bb2294e1ab3f47f15b50de475',
    secret_key=cfg.secret,
    headers={'User-Agent': cfg.agent},
)

esi_client = EsiClient(
    retry_requests=True,
    headers={'User-Agent': cfg.agent},
    security=security,
)

class MyClient(discord.Client):
    async def start_tracking(self, fleet_commander, channel):
        boss_id = await self.get_character_id(fleet_commander)
        if not boss_id:
            await channel.send("Invalid FC name, tracking disabled.")
            return

        access_token = await self.get_access_token(boss_id, channel)
        if not access_token:
            return

        cursor.execute(
            "SELECT watching FROM commanders WHERE char_id = %s;", (boss_id,)
        )
        row = cursor.fetchone()
        if row[0] == 1:
            await channel.send("I am already watching. Please just start your fleet if you haven't already.")
            return

        update_query = (
            "UPDATE commanders SET watching = %s WHERE char_id = %s;"
        )
        cursor.execute(update_query, (1, boss_id,))

        await channel.send("Tracking Started.")

        i = 0
        while True:
            access_token = await self.get_access_token(boss_id, channel)
            if not access_token:
                update_query = (
                    "UPDATE commanders SET watching = %s WHERE char_id = %s;"
                )
                cursor.execute(update_query, (0, boss_id,))
                return
            fleet_id = await self.get_fleet_id(boss_id, access_token)

            if fleet_id.status == 403:
                revoke_query = (
                    "DELETE FROM commanders WHERE commanders.char_id = %s;"
                )
                cursor.execute(revoke_query, (boss_id,))
                return

            if fleet_id.status == 200:
                if not fleet_id.data.get("fleet_boss_id") == boss_id:
                    # await channel.send("The requested name is not the fleet commander. Please try again.")
                    update_query = (
                        "UPDATE commanders SET watching = %s WHERE char_id = %s;"
                    )
                    cursor.execute(update_query, (0, boss_id,))

                    boss_id = fleet_id.data.get("fleet_boss_id")

                    update_query = (
                        "UPDATE commanders SET watching = %s WHERE char_id = %s;"
                    )
                    cursor.execute(update_query, (1, boss_id,))
                    continue
                break
            await asyncio.sleep(60)
            if i == 30:
                update_query = (
                    "UPDATE commanders SET watching = %s WHERE char_id = %s;"
                )
                cursor.execute(update_query, (0, boss_id,))
                return
            i += 1

        insert_query = (
            "INSERT INTO fleets (date, fleet_id, fc, duration) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING;"
        )
        cursor.execute(insert_query, (datetime.now(timezone.utc), fleet_id.data.get('fleet_id'), boss_id, 0,))
        while True:
            await asyncio.sleep(wait_time)
            access_token = await self.get_access_token(boss_id, channel)
            if not access_token:
                update_query = (
                    "UPDATE commanders SET watching = %s WHERE char_id = %s;"
                )
                cursor.execute(update_query, (0, boss_id,))
                return
            fleet_id = await self.get_fleet_id(boss_id, access_token)

            if fleet_id.status == 403:
                revoke_query = (
                    "DELETE FROM commanders WHERE commanders.char_id = %s;"
                )
                cursor.execute(revoke_query, (boss_id,))
                return

            if not fleet_id.status == 200:
                break

            if not fleet_id.data.get("fleet_boss_id") == boss_id:
                # await channel.send("The requested name is not the fleet commander. Please try again.")
                update_query = (
                    "UPDATE commanders SET watching = %s WHERE char_id = %s;"
                )
                cursor.execute(update_query, (0, boss_id,))

                boss_id = fleet_id.data.get("fleet_boss_id")

                update_query = (
                    "UPDATE commanders SET watching = %s WHERE char_id = %s;"
                )

                cursor.execute(update_query, (1, boss_id,))
                continue

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

        update_query = (
            "UPDATE commanders SET watching = %s WHERE char_id = %s;"
        )
        cursor.execute(update_query, (0, boss_id,))

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

    async def get_access_token(selfs, character_id, channel):
        cursor.execute("SELECT access_token, expires, refresh_token FROM commanders WHERE char_id = %s;", (character_id,))
        row = cursor.fetchone()
        if not row:
            url = (security.get_auth_uri(state=str(randint(100000000, 999999999)), scopes=['esi-fleets.read_fleet.v1']))
            alert = ('I\'m sorry that FC is not in my database. Please go to \n{0}\nand '
                     'try again using `!RC trackfleet <fc name>`'.format(url))
            await channel.send(alert)
            return
        else:
            if datetime.now(timezone.utc) > row[1]:
                security.update_token({
                    'access_token': '',  # leave this empty
                    'expires_in': -1,  # seconds until expiry, so we force refresh anyway
                    'refresh_token': row[2]
                })
                try:
                    tokens = security.refresh()
                except:
                    print(str(datetime.utcnow()) + '\n' + traceback.format_exc())
                    url = (
                        security.get_auth_uri(state=str(randint(100000000, 999999999)), scopes=['esi-fleets.read_fleet.v1']))
                    alert = ('I\'m sorry that FC\'s ESI token has expired. Please go to \n{0}\nand '
                             'try again using `!RC trackfleet <fc name>`'.format(url))
                    await channel.send(alert)
                    return
                expiration = datetime.now(timezone.utc)
                expiration += timedelta(seconds=tokens.get('expires_in') - 120)
                update_query = (
                    "UPDATE commanders SET access_token = %s, expires = %s, refresh_token = %s WHERE char_id = %s;"
                )
                cursor.execute(update_query, (tokens.get('access_token'), expiration, tokens.get('refresh_token'),
                                              character_id,))

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
        fleet_id_opp = app.op['get_characters_character_id_fleet'](character_id=character_id,
                                                                   token=access_token,)
        response = esi_client.request(fleet_id_opp)
        return response

    async def on_ready(self):
        print('Logged in as')
        print(self.user.name)
        print(self.user.id)
        print('------')
        update_query = (
            "UPDATE commanders SET watching = %s;"
        )
        cursor.execute(update_query, (0,))

    async def on_message(self, message):
        # we do not want the bot to reply to itself
        if message.author.id == self.user.id:
            return

        # if message.author.id == 357164098007465986 or message.channel.id == 362030937401196554 or \
        #         message.channel.id == 699851455112413194:
        # if message.channel.id == 699851455112413194:
        #     lines = message.content.splitlines()
        #     fleet_commander = ""
        #     for line in lines:
        #         if line.split(' ', 1)[0] == "FC:":
        #             fleet_commander = line.split(' ', 1)[1]
        #             break
        #
        #     if len(fleet_commander) == 0:
        #         return
        #
        #     for line in lines:
        #         member_name = re.search(r"#### SENT BY (.*?) to Dreddit - Fleets.*", line)
        #         if member_name:
        #             break
        #
        #     member = message.guild.get_member_named(member_name.group(1))
        #     if not member:
        #         channel = message.channel
        #     else:
        #         channel = member
        #
        #     await message.add_reaction(u"\U0001F440")
        #     await self.start_tracking(fleet_commander, channel)

        if message.author.bot:
            return

        if message.content.lower().startswith('!hello'):
            await message.channel.send('Hello {0.author.mention}'.format(message))
            return

        if message.content.lower().startswith('!rc'):

            if message.content.lower().startswith('!rc trackfleet'):
                fleet_commander = message.content.split(' ', 2)[2]
                await self.start_tracking(fleet_commander, message.author)
                return

            if str(message.author.id) not in cfg.authorized_users:
                await message.channel.send("I DO NOT RESPECT YOUR AUTHORITY PRIVATE! "
                                           "(Please try `!RC trackfleet <FC name>` instead)")

            elif message.content.lower().startswith('!rc set'):
                role = message.content.split(' ', 3)[2]
                name = message.content.split(' ', 3)[3]
                char_id = await self.get_character_id(name)
                if not char_id:
                    await message.channel.send("Invalid name. See help.")
                    return
                check_query = (
                    "SELECT names.name FROM names WHERE names.char_id = %s;"
                )
                cursor.execute(check_query, (char_id,))
                row = cursor.fetchone()
                if not row:
                    insert_query = (
                        "INSERT INTO names(char_id, name, role) VALUES"
                        " (%s, %s, %s) ON CONFLICT DO NOTHING;"
                    )
                    cursor.execute(insert_query, (char_id, name, role,))
                    await message.channel.send("{0} inserted as {1}".format(name, role))
                else:
                    update_query = (
                        "UPDATE names SET role = %s WHERE char_id = %s;"
                    )
                    cursor.execute(update_query, (role, char_id,))
                    await message.channel.send("{0} set to {1}".format(name, role))

            elif message.content.lower().startswith('!rc member'):
                count = message.content.split(' ', 3)[2]
                member = message.content.split(' ', 3)[3]
                char_id = await self.get_character_id(member)
                if not char_id:
                    await message.channel.send("Invalid member name. See help.")
                    return
                cursor.execute("SELECT DISTINCT fleets.fleet_id, fleets.fc, fleets.date, fleets.duration, names.role "
                               "FROM members LEFT JOIN fleets on members.fleet_id=fleets.fleet_id "
                               "LEFT JOIN names on members.char_id = names.char_id "
                               "WHERE members.char_id = %s ORDER BY 3 DESC LIMIT %s;",
                               (char_id, count,))
                rows = cursor.fetchall()
                output = "```Listing {0}'s ({2}) last {1} fleets\n". format(member, count, rows[0][4])
                output += "Fleet ID       | Date       | Fleet Duration | Fleet Commander      | Ships (Ship, Minutes)\n"
                output += "-------------------------------------------------------------------------------------------\n"
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
                    line = "{0:014} | {1} |   {2:04} Minutes | {3: <20} | {4}\n".format(
                        row[0], row[2].date(), int(row[3]/60), fc[0], ships)

                    # Handle character limit
                    if len(line) + len(output) > 1950:
                        output += "```"
                        await message.channel.send(output)
                        output = "```"
                    output += line

                output += "```"
                await message.channel.send(output)

            elif message.content.lower().startswith('!rc list'):
                start = message.content.split(' ', 3)[2]
                end = message.content.split(' ', 3)[3]
                cursor.execute("SELECT DISTINCT fleets.fleet_id, fleets.date, fleets.duration, fleets.fc "
                               "from fleets where fleets.date between %s and %s ORDER BY 2 DESC;",
                               (start, end,))
                rows = cursor.fetchall()
                output = "```Listing fleets from {0} to {1}\n".format(start, end)
                output += "Fleet ID       | Date       | Fleet Duration | Members | Fleet Commander\n"
                output += "------------------------------------------------------------------------\n"
                for row in rows:
                    cursor.execute("SELECT count(DISTINCT members.char_id) "
                                   "from members where members.fleet_id = %s;",
                                   (row[0],))
                    count = cursor.fetchone()
                    cursor.execute("SELECT names.name "
                                   "from names where names.char_id = %s;",
                                   (row[3],))
                    fc = cursor.fetchone()
                    line = "{0:014} | {1} |   {2:04} Minutes |     {3:03} | {4}\n".format(
                        row[0], row[1].date(), int(row[2]/60), count[0], fc[0])

                    # Handle character limit
                    if len(line) + len(output) > 1950:
                        output += "```"
                        await message.channel.send(output)
                        output = "```"
                    output += line

                output += "```"
                await message.channel.send(output)

            elif message.content.lower().startswith('!rc stats'):
                role = message.content.split(' ', 4)[2]
                start = message.content.split(' ', 4)[3]
                end = message.content.split(' ', 4)[4]
                # The nice
                cursor.execute("SELECT names.char_id, names.name, count(distinct members.fleet_id), names.role "
                               "FROM names LEFT JOIN members ON "
                               "names.char_id = members.char_id LEFT JOIN fleets on members.fleet_id = fleets.fleet_id "
                               "WHERE fleets.date > %s AND fleets.date < %s AND names.role LIKE %s "
                               "group by 1 ORDER BY 4 ASC, 2 ASC;",
                               (start, end, role,))
                rows = cursor.fetchall()
                output = "```Listing participating " + str(role) + " from " + str(start) + " to " + str(end) + "\n"
                output += "Name                 | Fleets as Member | Fleets as FC | Total Fleet Time | Role\n"
                output += "--------------------------------------------------------------------------------\n"
                for row in rows:
                    cursor.execute("SELECT round(sum(members.duration)/60) FROM members "
                                   "LEFT JOIN fleets on fleets.fleet_id = members.fleet_id "
                                   "WHERE char_id = %s AND fleets.date > %s AND fleets.date < %s;",
                                   (row[0], start, end,))
                    fleet_time = cursor.fetchone()
                    cursor.execute("SELECT count(distinct fleets.fleet_id) FROM fleets "
                                   "WHERE fleets.fc = %s AND fleets.date > %s AND fleets.date < %s;",
                                   (row[0], start, end,))
                    fc_count = cursor.fetchone()
                    line = "{0: <20} |             {1:04} |         {2:04} |   {3:06} Minutes | {4}\n".format(
                        row[1], row[2] - fc_count[0], fc_count[0], int(fleet_time[0]), row[3])

                    # Handle character limit
                    if len(line) + len(output) > 1950:
                        output += "```"
                        await message.channel.send(output)
                        output = "```"
                    output += line

                output += "```"
                await message.channel.send(output)

                # The naughty
                cursor.execute("SELECT names.char_id, names.name, names.role FROM names WHERE names.char_id NOT IN "
                               "(SELECT names.char_id "
                               "FROM names LEFT JOIN members ON "
                               "names.char_id = members.char_id LEFT JOIN fleets on members.fleet_id = fleets.fleet_id "
                               "WHERE fleets.date > %s AND fleets.date < %s AND names.role LIKE %s "
                               "group by 1) AND names.role LIKE %s ORDER BY 3 ASC, 2 ASC;",
                               (start, end, role, role,))
                rows = cursor.fetchall()
                output = "```Listing non participating " + str(role) + " from " + str(start) + " to " + str(end) + "\n"
                output += "Name                 | Role\n"
                output += "---------------------------\n"
                for row in rows:
                    line = "{0: <20} | {1}\n".format(
                        row[1], row[2])

                    # Handle character limit
                    if len(line) + len(output) > 1950:
                        output += "```"
                        await message.channel.send(output)
                        output = "```"
                    output += line

                output += "```"
                await message.channel.send(output)

            elif message.content.lower().startswith('!rc fleet'):
                fleet_id = message.content.split(' ', 2)[2]
                cursor.execute("SELECT DISTINCT members.char_id, fleets.fc, fleets.date, "
                               "fleets.duration, names.name "
                               "FROM fleets LEFT JOIN members on fleets.fleet_id=members.fleet_id "
                               "Left JOIN names on members.char_id=names.char_id "
                               "WHERE fleets.fleet_id = %s ORDER BY 5 ASC;",
                               (fleet_id,)) # was order by 1
                rows = cursor.fetchall()
                cursor.execute("SELECT names.name FROM names WHERE names.char_id = %s;", (rows[0][1],))
                commander = cursor.fetchone()
                output = "```Listing members of fleet {0} on {1} lasting {2} minutes led by {3}\n". format(
                    fleet_id, rows[0][2], int(rows[0][3]/60), commander[0]
                )
                output += "Name                 | Time On Fleet | Ships (Ship, Minutes)\n"
                output += "------------------------------------------------------------\n"
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
                    line = "{0: <20} |          {1:04} | {2}\n".format(
                        row[4], int(fleet_time[0]), ships
                    )

                    # Handle character limit
                    if len(line) + len(output) > 1950:
                        output += "```"
                        await message.channel.send(output)
                        output = "```"
                    output += line

                output += "```"
                await message.channel.send(output)

            elif message.content.lower().startswith('!rc'):
                await message.channel.send("```RollCall Commands:\n"
                                           "!rc trackfleet <FC name> - Starts tracking a fleet under <FC name>\n"
                                           "!rc set <role> <name> - Set's <name>'s role to <role> or adds them\n"
                                           "!rc member <Count> <Member Name> - Lists member's last <Count> fleets\n"
                                           "!rc list <start date> <end date> - Lists all fleets from "
                                           "<start date> to <end date>\n"
                                           "!rc stats <type> <start date> <end date> - Lists <type> (% is wildcard)"
                                           " statistics from <start date> to <end date>\n"
                                           "!rc fleet <fleet id> - Lists all information about <fleet id>\n"
                                           "(Date format is DD-MM-YYYY)```")


discord_client = MyClient()
discord_client.run(token)