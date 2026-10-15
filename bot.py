import asyncio
import io
import json
import logging
import os
import sqlite3
from urllib.parse import urlsplit
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
CFG = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
GUILD = int(os.getenv('GUILD_ID', '0'))
DB = sqlite3.connect(ROOT / 'project37.sqlite3')
DB.execute('CREATE TABLE IF NOT EXISTS requests (channel INTEGER PRIMARY KEY, owner INTEGER, kind TEXT, state TEXT, claimed INTEGER DEFAULT 0)')
DB.execute('CREATE TABLE IF NOT EXISTS streams (guild INTEGER, owner INTEGER, channel INTEGER, message INTEGER, links TEXT, PRIMARY KEY(guild,owner))')
DB.commit()
LOCKS = {}
COLOR = 0xD78CA9

def embed(title, description):
    return discord.Embed(title=title, description=description, color=COLOR).set_footer(text='Project 37 • Your next chapter starts here')

def role(guild, key):
    return guild.get_role(int(CFG['roles'].get(key, 0)))

def review_keys(kind):
    keys = ['management', 'management_rank', CFG['requests'][kind]['team']]
    if kind in ('report','support'): keys += ['administrator']
    if kind == 'support': keys += ['moderation_team','trial_moderator']
    return keys

def reviewer(member, kind):
    ids = {CFG['roles'].get(key,0) for key in review_keys(kind)} - {0}
    return member.guild_permissions.administrator or any(r.id in ids for r in member.roles)

def record(channel_id):
    return DB.execute('SELECT owner,kind,state,claimed FROM requests WHERE channel=?', (channel_id,)).fetchone()

async def deny(i, text):
    if i.response.is_done():
        await i.followup.send(text, ephemeral=True)
    else:
        await i.response.send_message(text, ephemeral=True)

async def create_request(i, kind, answers):
    await i.response.defer(ephemeral=True, thinking=True)
    lock = LOCKS.setdefault((i.user.id, kind), asyncio.Lock())
    async with lock:
        existing = DB.execute("SELECT channel FROM requests WHERE owner=? AND kind=? AND state='open'", (i.user.id,kind)).fetchone()
        if existing:
            if i.guild.get_channel(existing[0]):
                return await deny(i, f'You already have an open request: <#{existing[0]}>.')
            DB.execute("UPDATE requests SET state='closed' WHERE channel=?", existing)
            DB.commit()
        spec = CFG['requests'][kind]
        category = i.guild.get_channel(int(CFG['channels']['private_category']))
        team = role(i.guild, spec['team'])
        if not isinstance(category, discord.CategoryChannel) or not team:
            return await deny(i, 'This request type is not configured yet. Please contact management.')
        overwrites = {
            i.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            i.user: discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True),
            i.guild.me: discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True),
            team: discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True),
        }
        for key in review_keys(kind):
            staff_role=role(i.guild,key)
            if staff_role:
                overwrites[staff_role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)
        channel = await i.guild.create_text_channel(f'{kind}-{i.user.id}',category=category,overwrites=overwrites,reason='Project 37 private request')
        DB.execute('INSERT INTO requests(channel,owner,kind,state) VALUES(?,?,?,?)', (channel.id,i.user.id,kind,'open'))
        DB.commit()
        try:
            e = embed(spec['label'], f'Request from {i.user.mention}\nYour assigned team will assist you here. Please keep evidence and details in this private channel.')
            for question, answer in answers:
                e.add_field(name=question,value=answer or 'Not provided',inline=False)
            await channel.send(content=f'{i.user.mention} {team.mention}',embed=e,view=TicketControls(),allowed_mentions=discord.AllowedMentions(users=[i.user],roles=[team]))
        except Exception:
            logging.exception('Request created but initial message failed')
            await deny(i,f'Your request is at {channel.mention}; staff can use /request_action if the buttons did not post.')
            return
        await i.followup.send(f'Your private request is ready: {channel.mention}',ephemeral=True)

class RequestModal(discord.ui.Modal):
    def __init__(self,kind):
        super().__init__(title=CFG['requests'][kind]['label'][:45],timeout=600)
        self.kind = kind
        for question in CFG['requests'][kind]['questions']:
            self.add_item(discord.ui.TextInput(label=question[:45],style=discord.TextStyle.paragraph,max_length=900,required=True))
    async def on_submit(self,i):
        await create_request(i,self.kind,[(x.label,x.value) for x in self.children])
    async def on_error(self,i,error):
        logging.error('Modal failed',exc_info=(type(error),error,error.__traceback__))
        await deny(i,'Something went wrong. Contact support before submitting again.')

class EntryButton(discord.ui.Button):
    def __init__(self,kind,label,emoji):
        super().__init__(label=label,emoji=emoji,custom_id=f'p37:entry:{kind}',style=discord.ButtonStyle.secondary)
        self.kind=kind
    async def callback(self,i):
        if self.kind=='verify':
            r=role(i.guild,'verified')
            if not r:
                return await deny(i,'Verification role has not been configured.')
            await i.response.defer(ephemeral=True)
            await i.user.add_roles(r,reason='Acknowledged Project 37 rules')
            return await i.followup.send(f'✅ You are verified! Request WL in <#{CFG["channels"]["whitelist"]}>.',ephemeral=True)
        verified=role(i.guild,'verified')
        key=role(i.guild,'whitelist')
        if not any(r and r in i.user.roles for r in (verified,key)):
            return await deny(i,f'Please verify first in <#{CFG["channels"]["verify"]}>.')
        if self.kind in ('ems','pd','realestate') and (not key or key not in i.user.roles):
            return await deny(i,'You need Key to the city before applying for a city position.')
        if self.kind=='whitelist' and key and key in i.user.roles:
            return await deny(i,'You already have Key to the city.')
        await i.response.send_modal(RequestModal(self.kind))

class EntryView(discord.ui.View):
    def __init__(self,kinds):
        super().__init__(timeout=None)
        for kind in kinds:
            spec=CFG['requests'].get(kind,{'label':'Verify','emoji':'✅'})
            self.add_item(EntryButton(kind,spec['label'],spec['emoji']))
    async def on_error(self,i,error,item):
        logging.error('Button failed',exc_info=(type(error),error,error.__traceback__))
        await deny(i,'Action failed. Please contact support; check whether a request already opened.')

async def act(i,action):
    row=record(i.channel_id)
    if not row:
        return await deny(i,'Use this inside a bot-created request channel.')
    owner,kind,state,claimed=row
    if not reviewer(i.user,kind):
        return await deny(i,'Only the assigned team or management can do that.')
    await i.response.defer(ephemeral=True)
    async with LOCKS.setdefault(('channel',i.channel_id),asyncio.Lock()):
        owner,kind,state,claimed=record(i.channel_id)
        if state=='closed':
            return await deny(i,'This request is closed.')
        if action=='claim':
            if claimed and claimed!=i.user.id:
                return await deny(i,f'This request is already claimed by <@{claimed}>.')
            DB.execute('UPDATE requests SET claimed=? WHERE channel=?',(i.user.id,i.channel_id)); DB.commit()
            await i.channel.send(f'🎫 {i.user.mention} is assisting with this request.')
        elif action in ('approve','deny'):
            if state!='open':
                return await deny(i,'A decision has already been recorded.')
            if kind not in ('whitelist','female','ems','pd','realestate'):
                return await deny(i,'This ticket needs a resolution and close, rather than approval.')
            if action=='approve':
                member=i.guild.get_member(owner) or await i.guild.fetch_member(owner)
                r=role(i.guild,CFG['requests'][kind]['grant'])
                if not r:
                    return await deny(i,'The approval role is missing. Configure it before approving.')
                await member.add_roles(r,reason=f'{kind} approved by {i.user.id}')
            DB.execute('UPDATE requests SET state=? WHERE channel=?',(action,i.channel_id)); DB.commit()
            await i.channel.send(embed=embed('✅ Approved' if action=='approve' else '📋 Not approved',f'<@{owner}>, your {CFG["requests"][kind]["label"]} request has been {"approved" if action=="approve" else "denied"} by {i.user.mention}. Please ask your reviewer here if you need clarification.'))
        elif action=='close':
            logs=i.guild.get_channel(int(CFG['channels']['transcripts']))
            if not isinstance(logs,discord.TextChannel) or logs==i.channel:
                return await deny(i,'Configure a separate private transcript channel before closing.')
            lines=[]
            async for m in i.channel.history(limit=None,oldest_first=True):
                content=m.content
                for e in m.embeds:
                    content+='\n'+(e.title or '')+'\n'+(e.description or '')
                    content+=''.join(f'\n{f.name}: {f.value}' for f in e.fields)
                lines.append(f'[{m.created_at.isoformat()}] {m.author} ({m.author.id}): {content}\n'+ '\n'.join(a.url for a in m.attachments))
            payload='\n\n'.join(lines).encode('utf-8')
            await logs.send(content=f'{kind} • applicant {owner} • closed by {i.user.id}',file=discord.File(io.BytesIO(payload),filename=f'{i.channel_id}.txt'),allowed_mentions=discord.AllowedMentions.none())
            DB.execute("UPDATE requests SET state='closed' WHERE channel=?",(i.channel_id,)); DB.commit()
            await i.followup.send('Transcript saved. Closing this channel.',ephemeral=True)
            await i.channel.delete(reason=f'Closed by {i.user.id}')
            return
        await i.followup.send('Done.',ephemeral=True)

class ActionButton(discord.ui.Button):
    def __init__(self,action,label,style):
        super().__init__(label=label,style=style,custom_id=f'p37:action:{action}')
        self.action=action
    async def callback(self,i):
        if self.action=='close':
            row=record(i.channel_id)
            if not row or not reviewer(i.user,row[1]):
                return await deny(i,'Only the assigned team or management can close this request.')
            await i.response.send_message('Save a transcript and delete this ticket channel?',view=ConfirmClose(),ephemeral=True)
        else:
            await act(i,self.action)

class ConfirmClose(discord.ui.View):
    @discord.ui.button(label='Confirm close',style=discord.ButtonStyle.danger)
    async def confirm(self,i,button):
        await act(i,'close')
        self.stop()

class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for action,label,style in [('claim','Claim',discord.ButtonStyle.primary),('approve','Approve',discord.ButtonStyle.success),('deny','Deny',discord.ButtonStyle.secondary),('close','Close',discord.ButtonStyle.danger)]:
            self.add_item(ActionButton(action,label,style))
    async def on_error(self,i,error,item):
        logging.error('Request action failed',exc_info=(type(error),error,error.__traceback__))
        await deny(i,'Action failed. No success is assumed; contact management and check the bot log.')

class ProjectBot(commands.Bot):
    def __init__(self):
        intents=discord.Intents.default(); intents.members=True
        super().__init__(command_prefix='!',intents=intents,allowed_mentions=discord.AllowedMentions.none())
    async def setup_hook(self):
        self.add_view(EntryView(['verify','whitelist','donation','support','report','female','ems','pd','realestate']))
        self.add_view(TicketControls())
        self.add_view(EMSPanel())
        self.add_view(EMSClose())
        self.add_view(EMSDutyView())
        if EMS_GUILD and EMS_GUILD == GUILD:
            raise RuntimeError('EMS_GUILD_ID must differ from GUILD_ID')
        for guild_id, ems in [(GUILD, False), (EMS_GUILD, True)]:
            if not guild_id: continue
            target = discord.Object(id=guild_id)
            self.tree.clear_commands(guild=target)
            for command in self.tree.get_commands():
                if (command.name in EMS_COMMANDS) == ems:
                    self.tree.add_command(command, guild=target)
            await self.tree.sync(guild=target)

bot=ProjectBot()

@bot.event
async def on_ready():
    logging.info('Project 37 online as %s',bot.user)

@bot.event
async def on_member_join(member):
    if member.bot: return
    if EMS_GUILD and member.guild.id == EMS_GUILD:
        patient = member.guild.get_role(ems_settings().get('rank_roles',{}).get('patient',0))
        if patient:
            try: await member.add_roles(patient,reason='EMS patient access on joining')
            except discord.HTTPException: logging.exception('Could not assign Patient role; check hierarchy and Manage Roles')
        else: logging.warning('Patient role missing; run /setup_ems_roles in EMS Discord')
        return
    if member.guild.id!=GUILD: return
    channel=member.guild.get_channel(int(CFG['channels']['welcome']))
    if channel:
        await channel.send(content=member.mention,embed=embed('🏡 Welcome home to Project 37',f'Hey {member.mention}! Build your story, find your people, create a family, fall in love, or make your name in the city.\n\n📜 Read <#{CFG["channels"]["rules"]}>\n✅ Verify in <#{CFG["channels"]["verify"]}>\n🔑 Then request WL in <#{CFG["channels"]["whitelist"]}>\n\n🎫 Need help? Visit <#{CFG["channels"]["tickets"]}>.\n**Your next chapter starts at 37.**'),allowed_mentions=discord.AllowedMentions(users=[member]))



# One role refresh command. Existing configured role IDs are reused.
BASE_ROLE_PERMS = dict(view_channel=True,read_message_history=True,send_messages=True,
                       embed_links=True,attach_files=True,connect=True,speak=True)
ROLE_PROFILES = [
    ('management','👑 City Management',0xA12C4C,['manage_guild','manage_roles','manage_channels','kick_members','ban_members','moderate_members','manage_messages','view_audit_log']),
    ('management_rank','💼 Management',0x8E44AD,['manage_channels','kick_members','ban_members','moderate_members','manage_messages','view_audit_log']),
    ('administrator','🛡️ Administrator',0x3498DB,['kick_members','ban_members','moderate_members','manage_messages','view_audit_log']),
    ('moderation_team','🔨 Moderator',0x1ABC9C,['kick_members','moderate_members','manage_messages']),
    ('trial_moderator','🌱 Trial Moderator',0xA3CB38,['manage_messages']),
    ('wl_team','🔑 WL Team',0xF1C40F,[]),
    ('female_team','🌸 Female Verifier',0xFF69B4,[]),
    ('support_team','🎫 Support',0x5DADE2,[]),
    ('donation_team','💎 Donation Manager',0xE67E22,[]),
    ('ems_team','🚑 EMS Command',0xE74C3C,[]),
    ('pd_team','🚓 PD Command',0x2453A6,[]),
    ('realestate_team','🏡 Real Estate Management',0x16A085,[]),
    ('verified','✅ Verified',0x95A5A6,[]),
    ('whitelist',None,0xF4D03F,[]),
    ('female',None,0xE84393,[]),
    ('ems','🚑 EMS',0xFF7675,[]),
    ('pd','🚓 PD',0x74B9FF,[]),
    ('realestate','🏡 Real Estate',0x55EFC4,[]),
    ('donator','💖 Donator',0xD980FA,[]),
    ('creator','🎥 Streamer',0x9B59B6,[]),
    ('partner','🤝 Partner',0xA29BFE,[]),
]
STAFF_KEYS=[p[0] for p in ROLE_PROFILES[:12]]
CHAT_NAMES={'💬・community-chat','📸・behind-the-character','🍽️・whats-cooking','😂・memes-and-moments','🎬・city-clips','💡・suggestions','🪪・character-intros','📚・character-stories','🏡・family-connections','💌・missed-connections','🗞️・word-around-town','📱・city-socials','💬・baddies-chat','👗・looks-and-links','📸・baddies-gallery','🐛・bug-reports','👥・crew-registration','🏪・business-requests'}

def profile_permissions(key,flags):
    # Community roles receive channel access through overrides, not globally.
    return discord.Permissions(**({**BASE_ROLE_PERMS,**{flag:True for flag in flags}} if key in STAFF_KEYS else {}))

def layout_overwrites(guild,access,name):
    if access=='streamers': return streamer_overwrites(guild,name=='📺・stream-links')
    perms={guild.default_role:discord.PermissionOverwrite(view_channel=access=='arrival',send_messages=False,connect=False),
           guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True,connect=True)}
    staff=STAFF_KEYS if access not in ('female','private') else ['management','management_rank','female_team'] if access=='female' else ['management','management_rank']
    if name=='🗂️・ticket-transcripts': staff=['management','management_rank']
    for key in staff:
        r=role(guild,key)
        if r: perms[r]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True,connect=True,speak=True)
    if name!='🗂️・ticket-transcripts':
        members=['whitelist'] if access in ('city','arrival') else ['female'] if access=='female' else []
        if access=='arrival': members+=['verified']
        for key in members:
            r=role(guild,key)
            if r: perms[r]=discord.PermissionOverwrite(view_channel=True,read_message_history=True,send_messages=name in CHAT_NAMES,attach_files=name in CHAT_NAMES,connect=name!='🎧・support-room',speak=True)
    return perms

@bot.tree.command(name='refresh_roles',description='Create/color staff roles and align Project 37 channel and ticket permissions')
@app_commands.default_permissions(administrator=True)
async def refresh_roles(i:discord.Interaction):
    if not i.user.guild_permissions.administrator:
        return await deny(i,'Only the server owner or an administrator can refresh roles.')
    needed=discord.Permissions(manage_roles=True,manage_channels=True)
    for key,name,color,flags in ROLE_PROFILES: needed.value |= profile_permissions(key,flags).value
    missing=[name for name,enabled in needed if enabled and not getattr(i.guild.me.guild_permissions,name)]
    if missing:
        return await deny(i,'Enable these permissions on the BOT role first (Administrator is not needed): '+', '.join(name.replace('_',' ').title() for name in missing))
    for key,name,color,flags in ROLE_PROFILES:
        r=role(i.guild,key)
        if r and (r.managed or r>=i.guild.me.top_role):
            return await deny(i,f'Move the bot role above {r.name} first. The bot cannot update a role above itself.')
    await i.response.defer(ephemeral=True,thinking=True)
    async with LOCKS.setdefault('setup',asyncio.Lock()):
        backup=ROOT/'config.before-role-refresh.json'
        if not backup.exists(): backup.write_text(json.dumps(CFG,indent=2,ensure_ascii=False),encoding='utf-8')
        def save():
            temp=ROOT/'config.json.tmp'
            temp.write_text(json.dumps(CFG,indent=2,ensure_ascii=False),encoding='utf-8'); temp.replace(ROOT/'config.json')
        for key,name,color,flags in ROLE_PROFILES:
            r=role(i.guild,key)
            if not r:
                if key in ('whitelist','female'):
                    return await deny(i,'Run /setup_city first with the existing WL and Project Baddies role IDs configured.')
                candidates=[x for x in i.guild.roles if not x.managed and (x.name==name or x.name==name.split(' ',1)[1])]
                if len(candidates)>1: return await deny(i,f'Multiple roles match {name}. Set the intended ID in config.json first.')
                r=candidates[0] if candidates else await i.guild.create_role(name=name,permissions=discord.Permissions.none(),reason='Project 37 role refresh')
            if r>=i.guild.me.top_role: return await deny(i,f'Move the bot above {r.name}, then rerun /refresh_roles.')
            kwargs=dict(color=discord.Color(color),permissions=profile_permissions(key,flags),hoist=key in STAFF_KEYS,mentionable=False,reason='Project 37 role refresh')
            if name: kwargs['name']=name
            r=await r.edit(**kwargs)
            CFG['roles'][key]=r.id
            CFG['role_names'][key]=r.name
            save()
        # Highest staff role first beneath the bot. Department/duty roles below ranks.
        ordered=[role(i.guild,p[0]) for p in ROLE_PROFILES]
        # Use existing position slots, avoiding shifting unrelated roles deliberately.
        slots=sorted([r.position for r in ordered],reverse=True)
        await i.guild.edit_role_positions(positions={r:pos for r,pos in zip(ordered,slots)},reason='Project 37 staff hierarchy')
        for category_name,access,channels in CFG['layout']:
            category=discord.utils.get(i.guild.categories,name=category_name)
            if not category: continue
            await category.edit(overwrites=layout_overwrites(i.guild,access,''),reason='Project 37 permission alignment')
            for key,raw_name in channels:
                name=raw_name.removeprefix('voice:')
                channel=next((c for c in category.channels if c.name==name),None)
                if channel:
                    await channel.edit(overwrites=layout_overwrites(i.guild,access,name),reason='Project 37 permission alignment')
        for channel_id,owner,kind in DB.execute("SELECT channel,owner,kind FROM requests WHERE state!='closed'").fetchall():
            channel=i.guild.get_channel(channel_id)
            if not channel: continue
            member=i.guild.get_member(owner)
            if not member:
                try: member=await i.guild.fetch_member(owner)
                except discord.NotFound: member=None
            perms={i.guild.default_role:discord.PermissionOverwrite(view_channel=False),i.guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True)}
            if member: perms[member]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)
            for key in review_keys(kind):
                r=role(i.guild,key)
                if r: perms[r]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)
            await channel.edit(overwrites=perms,reason='Project 37 ticket permission alignment')
        await i.followup.send('✅ Roles colored and permissions aligned. No roles or channels deleted; existing configured role memberships preserved. Assign staff the correct rank PLUS duty roles. Key to the city and Project Baddies kept their names/IDs. Keep department roles below the bot. Existing unrelated old roles can still add permissions until cleaned up.',ephemeral=True)

@bot.tree.command(name='setup_city',description='Create Project 37 roles and organized channels; never delete old channels')
@app_commands.default_permissions(administrator=True)
async def setup_city(i:discord.Interaction):
    if not i.user.guild_permissions.administrator:
        return await deny(i,'Administrator permission is required for city setup.')
    if not all(getattr(i.guild.me.guild_permissions,p) for p in ('manage_roles','manage_channels','view_channel','send_messages')):
        return await deny(i,'The bot needs Manage Roles, Manage Channels, View Channels, and Send Messages.')
    await i.response.defer(ephemeral=True,thinking=True)
    async with LOCKS.setdefault('setup',asyncio.Lock()):
        def save():
            tmp=ROOT / 'config.json.tmp'
            tmp.write_text(json.dumps(CFG,indent=2,ensure_ascii=False),encoding='utf-8')
            tmp.replace(ROOT / 'config.json')
        aliases={'whitelist':['Key To Da City | 🔑','Key To Da City','Key to the city'], 'female':['👸🏽 Project Baddies 💞','Project Baddies']}
        for key,name in CFG['role_names'].items():
            r=role(i.guild,key)
            if not r:
                candidates=[x for x in i.guild.roles if not x.managed and (x.name.casefold()==name.casefold() or x.name in aliases.get(key,[]) or (key=='female' and 'project baddies' in x.name.casefold()) or (key=='whitelist' and ('key to da city' in x.name.casefold() or 'key to the city' in x.name.casefold())))]
                if len(candidates)>1:
                    return await deny(i,f'Multiple roles match {name}. Set the intended role ID in config.json and rerun setup.')
                r=candidates[0] if candidates else await i.guild.create_role(name=name,color=discord.Color(COLOR),permissions=discord.Permissions.none(),reason='Project 37 setup')
                CFG['roles'][key]=r.id; save()
        public_chat={'💬・community-chat','📸・behind-the-character','🍽️・whats-cooking','😂・memes-and-moments','🎬・city-clips','💡・suggestions','🪪・character-intros','📚・character-stories','🏡・family-connections','💌・missed-connections','🗞️・word-around-town','📱・city-socials','💬・baddies-chat','👗・looks-and-links','📸・baddies-gallery','🐛・bug-reports','👥・crew-registration','🏪・business-requests'}
        for category_name,access,channels in CFG['layout']:
            if access=='streamers': continue  # Managed by /setup_streamers.
            overwrites={i.guild.default_role:discord.PermissionOverwrite(view_channel=access=='arrival',send_messages=False,connect=False),i.guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True,connect=True)}
            for key in ['management','support_team','moderation_team','wl_team','female_team','donation_team','ems_team','pd_team','realestate_team']:
                overwrites[role(i.guild,key)]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,connect=True)
            if access in ('arrival','city'):
                overwrites[role(i.guild,'whitelist')]=discord.PermissionOverwrite(view_channel=True,read_message_history=True,connect=True)
            if access=='arrival':
                overwrites[role(i.guild,'verified')]=discord.PermissionOverwrite(view_channel=True,read_message_history=True,connect=True)
            if access=='female':
                overwrites[role(i.guild,'female')]=discord.PermissionOverwrite(view_channel=True,read_message_history=True,connect=True)
            category=discord.utils.get(i.guild.categories,name=category_name)
            if category is None:
                category=await i.guild.create_category(category_name,overwrites=overwrites,reason='Project 37 setup')
            if access=='private':
                CFG['channels']['private_category']=category.id; save()
            for key,raw_name in channels:
                voice=raw_name.startswith('voice:'); name=raw_name.removeprefix('voice:')
                channel=next((c for c in category.channels if c.name==name and isinstance(c,discord.VoiceChannel if voice else discord.TextChannel)),None)
                if channel:
                    if key: CFG['channels'][key]=channel.id; save()
                    continue
                perms={r:discord.PermissionOverwrite.from_pair(*o.pair()) for r,o in overwrites.items()}
                for member_key in (['female'] if access=='female' else ['whitelist']):
                    r=role(i.guild,member_key)
                    if r in perms:
                        perms[r].send_messages=(name in public_chat)
                        perms[r].attach_files=(name in public_chat)
                if name=='🐛・bug-reports':
                    perms[role(i.guild,'verified')].send_messages=True
                if name=='🎧・support-room':
                    for r in (i.guild.default_role,role(i.guild,'verified'),role(i.guild,'whitelist')):
                        if r in perms: perms[r].connect=False
                if key=='transcripts':
                    for r,o in perms.items():
                        if r not in (i.guild.me,role(i.guild,'management')):
                            o.view_channel=False
                if name=='💬・staff-chat':
                    for r,o in perms.items():
                        if r!=i.guild.default_role: o.send_messages=True
                channel=await (i.guild.create_voice_channel(name,category=category,overwrites=perms) if voice else i.guild.create_text_channel(name,category=category,overwrites=perms))
                if key: CFG['channels'][key]=channel.id; save()
        await i.followup.send('✅ City layout created. No old channels or roles were deleted. Check permissions, put the bot role above its assigned roles, then use /post_panel for verify, whitelist, tickets, and applications. Existing matching channels were left unchanged.',ephemeral=True)

@bot.tree.command(name='post_panel',description='Post a Project 37 verification, WL, ticket, or application panel')
@app_commands.default_permissions(manage_guild=True)
@app_commands.choices(panel=[app_commands.Choice(name=n,value=n) for n in ['verify','whitelist','tickets','applications']])
async def post_panel(i:discord.Interaction,panel:str):
    if not i.user.guild_permissions.manage_guild:
        return await deny(i,'Manage Server permission is required.')
    panels={
        'verify':('verify',['verify'],'✅ Make yourself at home','Read our city rules, then click Verify to acknowledge them. Verification unlocks WL requests; it does not grant city access.'),
        'whitelist':('whitelist',['whitelist'],'🔑 Your key to Project 37','Ready to start your story? Request Whitelist below. Our team will review your answers privately and arrange any required interview.'),
        'tickets':('tickets',['donation','support','report','female'],'🎫 Project 37 Help Desk','💎 Donations — purchases and supporter questions\n🛠️ Support — help with the city\n🚩 Player Report — private rule violation reports\n💋 Female Verification — access to Project Baddies\n\nChoose an option and tell us what you need. Please allow the team time to respond.'),
        'applications':('applications',['ems','pd','realestate'],'💼 Build your career in Project 37','🚑 EMS — care for the city\n🚓 PD — protect and serve\n🏡 Real Estate — help residents find home\n\nKey to the city is required. Applications are reviewed privately; acceptance grants the configured Discord department role. In-game jobs are assigned separately by staff.')}
    key,kinds,title,description=panels[panel]
    channel=i.guild.get_channel(int(CFG['channels'][key]))
    if not isinstance(channel,discord.TextChannel): return await deny(i,'Configure the panel channel first.')
    await i.response.defer(ephemeral=True)
    await channel.send(embed=embed(title,description),view=EntryView(kinds))
    await i.followup.send(f'Panel posted in {channel.mention}. Post once; older panels stay functional after bot restarts.',ephemeral=True)

@bot.tree.command(name='request_action',description='Staff fallback for a private request')
@app_commands.choices(action=[app_commands.Choice(name=n,value=n) for n in ['claim','approve','deny']])
async def request_action(i:discord.Interaction,action:str):
    await act(i,action)

@bot.tree.command(name='close_request',description='Save transcript and close this request')
async def close_request(i:discord.Interaction):
    row=record(i.channel_id)
    if not row or not reviewer(i.user,row[1]): return await deny(i,'Only the assigned team or management can close this request.')
    await i.response.send_message('Save transcript and delete this ticket?',view=ConfirmClose(),ephemeral=True)

@bot.tree.error
async def command_error(i,error):
    logging.error('Command failed',exc_info=(type(error),error,error.__traceback__))
    await deny(i,'Action failed. Please contact management and check configuration/permissions.')


STREAM_PLATFORMS={
    'Twitch':{'twitch.tv','www.twitch.tv'},
    'Kick':{'kick.com','www.kick.com'},
    'YouTube':{'youtube.com','www.youtube.com','m.youtube.com','youtu.be'},
    'TikTok':{'tiktok.com','www.tiktok.com','m.tiktok.com'},
}

def valid_stream_link(platform,value):
    value=value.strip()
    if not value: return ''
    if len(value)>300 or any(c.isspace() or c in '<>\\`[]' for c in value):
        raise ValueError(f'{platform}: enter a valid HTTPS channel link.')
    try:
        parsed=urlsplit(value)
        if parsed.scheme!='https' or parsed.hostname not in STREAM_PLATFORMS[platform] or parsed.username or parsed.password or parsed.port or not parsed.path.strip('/'):
            raise ValueError()
    except ValueError:
        raise ValueError(f'{platform}: use your HTTPS channel link on the official {platform} website.') from None
    return value

def streamer_overwrites(guild,public=False):
    perms={guild.default_role:discord.PermissionOverwrite(view_channel=False,send_messages=False,read_message_history=False),
           guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,embed_links=True,manage_channels=True)}
    streamer=role(guild,'creator')
    wl=role(guild,'whitelist')
    if public:
        if wl: perms[wl]=discord.PermissionOverwrite(view_channel=True,read_message_history=True,send_messages=False,add_reactions=False,create_public_threads=False,create_private_threads=False,send_messages_in_threads=False)
        # Streamer alone does not grant view access to the public WL channel.
        if streamer: perms[streamer]=discord.PermissionOverwrite(send_messages=True,embed_links=True,attach_files=True,create_public_threads=False,create_private_threads=False,send_messages_in_threads=False)
    elif streamer:
        perms[streamer]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,embed_links=True,attach_files=True)
    return perms

@bot.tree.command(name='setup_streamers',description='Create the Streamer role, private lounge, and WL-visible stream-links channel')
@app_commands.default_permissions(administrator=True)
async def setup_streamers(i:discord.Interaction):
    if not i.user.guild_permissions.administrator: return await deny(i,'Only an administrator can set up streamer channels.')
    if not i.guild.me.guild_permissions.manage_roles or not i.guild.me.guild_permissions.manage_channels:
        return await deny(i,'The bot needs Manage Roles and Manage Channels.')
    if not role(i.guild,'whitelist'): return await deny(i,'Configure the existing Key to the city role first.')
    await i.response.defer(ephemeral=True,thinking=True)
    async with LOCKS.setdefault('setup',asyncio.Lock()):
        r=role(i.guild,'creator')
        if not r:
            candidates=[r for r in i.guild.roles if not r.managed and r.name in ('Streamer','🎥 Streamer','Content Creator','🎥 Content Creator')]
            if len(candidates)>1: return await deny(i,'Multiple streamer/creator roles match. Set the intended role ID under roles.creator in config.json first.')
            r=candidates[0] if candidates else await i.guild.create_role(name='🎥 Streamer',permissions=discord.Permissions.none(),color=discord.Color(0x9B59B6),reason='Project 37 streamer setup')
        if r.managed or r>=i.guild.me.top_role: return await deny(i,'Move the bot role above the Streamer/Content Creator role first.')
        r=await r.edit(name='🎥 Streamer',color=discord.Color(0x9B59B6),permissions=discord.Permissions.none(),hoist=True,mentionable=False,reason='Project 37 streamer setup')
        CFG['roles']['creator']=r.id; CFG['role_names']['creator']=r.name
        def save():
            tmp=ROOT/'config.json.tmp'; tmp.write_text(json.dumps(CFG,indent=2,ensure_ascii=False),encoding='utf-8'); tmp.replace(ROOT/'config.json')
        save()
        name='🎥 ━ PROJECT STREAMERS'
        category=discord.utils.get(i.guild.categories,name=name)
        perms=streamer_overwrites(i.guild)
        if category: await category.edit(overwrites=perms,reason='Project 37 streamer access')
        else: category=await i.guild.create_category(name,overwrites=perms,reason='Project 37 streamer setup')
        for key,channel_name,public in [('streamer_lounge','🎥・streamer-lounge',False),('stream_links','📺・stream-links',True)]:
            channel=i.guild.get_channel(int(CFG['channels'].get(key,0)))
            if not isinstance(channel,discord.TextChannel): channel=next((c for c in category.text_channels if c.name==channel_name),None)
            topic='Private discussion for Project 37 streamers.' if not public else 'Key to the city can view. Streamers can post. Use /register_stream to publish/update your stream links.'
            perms=streamer_overwrites(i.guild,public)
            if channel: await channel.edit(overwrites=perms,topic=topic,reason='Project 37 streamer access')
            else: channel=await i.guild.create_text_channel(channel_name,category=category,overwrites=perms,topic=topic,reason='Project 37 streamer setup')
            CFG['channels'][key]=channel.id; save()
        CFG['layout']=[row for row in CFG['layout'] if row[0]!=name]
        CFG['layout'].append([name,'streamers',[['streamer_lounge','🎥・streamer-lounge'],['stream_links','📺・stream-links']]])
        save()
        await i.followup.send(f'✅ Streamer role and channels ready. Use /streamer_role to assign it. Streamers with Key to the city can use /register_stream. Public links: <#{CFG["channels"]["stream_links"]}>. Private lounge: <#{CFG["channels"]["streamer_lounge"]}>. Actual Discord administrators always retain access.',ephemeral=True)

@bot.tree.command(name='streamer_role',description='Give or remove the Streamer role for a member')
@app_commands.default_permissions(manage_roles=True)
async def streamer_role(i:discord.Interaction,member:discord.Member,remove:bool=False):
    if not i.user.guild_permissions.manage_roles: return await deny(i,'Manage Roles permission is required.')
    r=role(i.guild,'creator')
    if not r: return await deny(i,'Run /setup_streamers first.')
    if not i.user.guild_permissions.administrator and (r>=i.user.top_role or member.top_role>=i.user.top_role):
        return await deny(i,'You can only manage roles/members below your own highest role.')
    if r>=i.guild.me.top_role or member.top_role>=i.guild.me.top_role:
        return await deny(i,'Move the bot role above the Streamer role and this member’s highest role first.')
    await i.response.defer(ephemeral=True)
    if remove: await member.remove_roles(r,reason=f'Streamer role removed by {i.user.id}')
    else: await member.add_roles(r,reason=f'Streamer role assigned by {i.user.id}')
    await i.followup.send(f'🎥 Streamer role {"removed from" if remove else "given to"} {member.mention}.',ephemeral=True)

class StreamModal(discord.ui.Modal,title='Register your stream links'):
    def __init__(self):
        super().__init__(timeout=600)
        for platform in STREAM_PLATFORMS:
            self.add_item(discord.ui.TextInput(label=platform+' channel link',placeholder='https://...',required=False,max_length=300))
    async def on_submit(self,i):
        wl=role(i.guild,'whitelist'); streamer=role(i.guild,'creator')
        if not wl or not streamer or wl not in i.user.roles or streamer not in i.user.roles:
            return await deny(i,'Key to the city AND Streamer roles are required.')
        try:
            links={platform:valid_stream_link(platform,field.value) for platform,field in zip(STREAM_PLATFORMS,self.children)}
        except ValueError as e: return await deny(i,str(e))
        links={k:v for k,v in links.items() if v}
        if not links: return await deny(i,'Enter at least one stream channel link.')
        channel=i.guild.get_channel(int(CFG['channels'].get('stream_links',0)))
        if not isinstance(channel,discord.TextChannel): return await deny(i,'Run /setup_streamers first.')
        await i.response.defer(ephemeral=True,thinking=True)
        async with LOCKS.setdefault(('stream',i.guild_id,i.user.id),asyncio.Lock()):
            e=embed('🎥 '+i.user.display_name+' • Project 37 Streamer',f'Watch {i.user.mention} bring their Project 37 story to life. 🤍')
            for platform,url in links.items(): e.add_field(name=platform,value=f'[Watch on {platform}]({url})',inline=False)
            previous=DB.execute('SELECT channel,message FROM streams WHERE guild=? AND owner=?',(i.guild_id,i.user.id)).fetchone()
            message=None
            if previous and previous[0]==channel.id:
                try:
                    old=await channel.fetch_message(previous[1])
                    if old.author.id==i.client.user.id:
                        message=await old.edit(embed=e,allowed_mentions=discord.AllowedMentions.none())
                except discord.NotFound: pass
            if not message: message=await channel.send(embed=e,allowed_mentions=discord.AllowedMentions.none())
            DB.execute('INSERT INTO streams VALUES(?,?,?,?,?) ON CONFLICT(guild,owner) DO UPDATE SET channel=excluded.channel,message=excluded.message,links=excluded.links',(i.guild_id,i.user.id,channel.id,message.id,json.dumps(links)))
            DB.commit()
            await i.followup.send(f'✅ Your links are saved in {channel.mention}. Run /register_stream again to update your card.',ephemeral=True)
    async def on_error(self,i,error):
        logging.error('Stream registration failed',exc_info=(type(error),error,error.__traceback__))
        await deny(i,'Could not save your stream links. Contact support and check channel permissions.')

@bot.tree.command(name='register_stream',description='Streamers: register or update your Twitch, Kick, YouTube, and TikTok links')
async def register_stream(i:discord.Interaction):
    wl=role(i.guild,'whitelist'); streamer=role(i.guild,'creator')
    if not wl or not streamer or wl not in i.user.roles or streamer not in i.user.roles:
        return await deny(i,'Key to the city AND Streamer roles are required. Ask staff for the Streamer role.')
    await i.response.send_modal(StreamModal())

@bot.tree.command(name='unregister_stream',description='Remove your saved streamer card')
async def unregister_stream(i:discord.Interaction):
    await i.response.defer(ephemeral=True)
    async with LOCKS.setdefault(('stream',i.guild_id,i.user.id),asyncio.Lock()):
        row=DB.execute('SELECT channel,message FROM streams WHERE guild=? AND owner=?',(i.guild_id,i.user.id)).fetchone()
        if not row: return await deny(i,'You do not have a saved streamer card.')
        channel=i.guild.get_channel(row[0])
        if channel:
            try:
                message=await channel.fetch_message(row[1])
                if message.author.id==i.client.user.id: await message.delete()
            except discord.NotFound: pass
        DB.execute('DELETE FROM streams WHERE guild=? AND owner=?',(i.guild_id,i.user.id)); DB.commit()
        await i.followup.send('Your saved streamer card was removed.',ephemeral=True)

# EMS features are isolated from the main city's role/channel configuration.
import time
EMS_GUILD = int(os.getenv('EMS_GUILD_ID', '0'))
EMS_DIR = Path(os.getenv('EMS_DATA_DIR', str(ROOT)))
EMS_DIR.mkdir(parents=True, exist_ok=True)
EMS_DB = sqlite3.connect(EMS_DIR / 'ems.sqlite3')
EMS_DB.execute('CREATE TABLE IF NOT EXISTS settings (guild INTEGER PRIMARY KEY, data TEXT)')
EMS_DB.execute('CREATE TABLE IF NOT EXISTS shifts (id INTEGER PRIMARY KEY, guild INTEGER, member INTEGER, started INTEGER, ended INTEGER)')
EMS_DB.execute('CREATE TABLE IF NOT EXISTS tickets (channel INTEGER PRIMARY KEY, guild INTEGER, owner INTEGER, kind TEXT, closed INTEGER DEFAULT 0)')
EMS_DB.commit()
EMS_COMMANDS = {'setup_ems', 'setup_ems_roles', 'ems_hours', 'ems_patient_record'}

def ems_settings():
    row = EMS_DB.execute('SELECT data FROM settings WHERE guild=?', (EMS_GUILD,)).fetchone()
    return json.loads(row[0]) if row else {}

def ems_staff(member, command=False):
    cfg = ems_settings()
    key = 'command_role' if command else 'staff_role'
    ids = {cfg.get(key), cfg.get('command_role')}
    return member.guild_permissions.administrator or any(r.id in ids for r in member.roles)

async def ems_guard(i, staff=False, command=False):
    if not EMS_GUILD or i.guild_id != EMS_GUILD:
        await deny(i, 'This action is available only in the configured EMS Discord.')
        return False
    if (staff or command) and not ems_staff(i.user, command):
        await deny(i, 'EMS command permission is required.' if command else 'EMS staff permission is required.')
        return False
    return True

def ems_embed(title, text):
    return discord.Embed(title=title, description=text, color=0xE74C3C).set_footer(text='Project 37 EMS • Roleplay department')

class EMSModal(discord.ui.Modal):
    def __init__(self, kind):
        super().__init__(title='EMS Application' if kind == 'application' else 'EMS Appointment', timeout=600)
        self.kind = kind
        questions = (['Character name / age', 'EMS experience', 'Availability and timezone', 'Why do you want to join EMS?'] if kind == 'application' else ['Character name', 'Appointment reason', 'Preferred date, time and timezone'])
        for q in questions:
            self.add_item(discord.ui.TextInput(label=q, style=discord.TextStyle.paragraph, max_length=800))
    async def on_submit(self, i):
        if not await ems_guard(i): return
        patient=ems_settings().get('rank_roles',{}).get('patient')
        if self.kind=='appointment' and not i.user.guild_permissions.administrator and not any(r.id==patient for r in i.user.roles):
            return await deny(i,'The Patient role is required to book an appointment.')
        await i.response.defer(ephemeral=True)
        async with LOCKS.setdefault(('ems-ticket', i.user.id, self.kind), asyncio.Lock()):
            cfg = ems_settings()
            for (cid,) in EMS_DB.execute('SELECT channel FROM tickets WHERE guild=? AND owner=? AND kind=? AND closed=0', (EMS_GUILD,i.user.id,self.kind)).fetchall():
                if i.guild.get_channel(cid): return await deny(i, f'Your ticket is already open: <#{cid}>.')
                EMS_DB.execute('UPDATE tickets SET closed=1 WHERE channel=?', (cid,))
            category = i.guild.get_channel(cfg.get('tickets', 0))
            team = i.guild.get_role(cfg.get('command_role' if self.kind == 'application' else 'staff_role', 0))
            command = i.guild.get_role(cfg.get('command_role', 0))
            if not isinstance(category, discord.CategoryChannel) or not team or not command:
                return await deny(i, 'Ask an administrator to run /setup_ems first.')
            perms = {i.guild.default_role: discord.PermissionOverwrite(view_channel=False), i.guild.me: discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True), i.user: discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)}
            for r in (team, command): perms[r] = discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)
            ch = await i.guild.create_text_channel(f'{self.kind}-{i.user.id}', category=category, overwrites=perms)
            EMS_DB.execute('INSERT INTO tickets(channel,guild,owner,kind) VALUES(?,?,?,?)',(ch.id,EMS_GUILD,i.user.id,self.kind)); EMS_DB.commit()
            e = ems_embed('EMS '+self.kind.title(), f'Opened by {i.user.mention}')
            for field in self.children: e.add_field(name=field.label,value=field.value,inline=False)
            await ch.send(embed=e,view=EMSClose())
            await i.followup.send(f'Ticket ready: {ch.mention}',ephemeral=True)
    async def on_error(self,i,error):
        logging.error('EMS ticket failed',exc_info=(type(error),error,error.__traceback__))
        await deny(i,'Could not finish opening the ticket. Check for an existing ticket and ask an administrator to check bot permissions.')

class EMSClose(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='Close & save transcript',style=discord.ButtonStyle.danger,custom_id='p37ems:close')
    async def close(self,i,button):
        if not await ems_guard(i,command=True): return
        await i.response.defer(ephemeral=True)
        async with LOCKS.setdefault(('ems-close',i.channel_id),asyncio.Lock()):
            row=EMS_DB.execute('SELECT owner,kind,closed FROM tickets WHERE channel=? AND guild=?',(i.channel_id,EMS_GUILD)).fetchone()
            if not row or row[2]: return await deny(i,'This is not an open EMS ticket.')
            logs=i.guild.get_channel(ems_settings().get('transcripts',0))
            if not isinstance(logs,discord.TextChannel) or logs==i.channel: return await deny(i,'Run /setup_ems to configure transcripts first.')
            lines=[]
            async for m in i.channel.history(limit=None,oldest_first=True):
                fields='\n'.join((e.title or '')+'\n'+(e.description or '')+'\n'+'\n'.join(f'{f.name}: {f.value}' for f in e.fields) for e in m.embeds)
                lines.append(f'[{m.created_at.isoformat()}] {m.author} ({m.author.id}): {m.content}\n{fields}\n'+ '\n'.join(a.url for a in m.attachments))
            await logs.send(content=f'EMS {row[1]} • owner {row[0]} • closed by {i.user.id}',file=discord.File(io.BytesIO('\n\n'.join(lines).encode()),filename=f'ems-{i.channel_id}.txt'))
            EMS_DB.execute('UPDATE tickets SET closed=1 WHERE channel=?',(i.channel_id,)); EMS_DB.commit()
            await i.followup.send('Transcript saved. Closing ticket.',ephemeral=True)
            await i.channel.delete(reason=f'EMS ticket closed by {i.user.id}')

class EMSPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='Clock in',style=discord.ButtonStyle.success,custom_id='p37ems:in')
    async def clock_in(self,i,button):
        if not await ems_guard(i,staff=True): return
        if EMS_DB.execute('SELECT 1 FROM shifts WHERE guild=? AND member=? AND ended IS NULL',(EMS_GUILD,i.user.id)).fetchone(): return await deny(i,'You are already clocked in.')
        EMS_DB.execute('INSERT INTO shifts(guild,member,started) VALUES(?,?,?)',(EMS_GUILD,i.user.id,int(time.time()))); EMS_DB.commit()
        await deny(i,'✅ Clocked in. Stay safe out there!')
    @discord.ui.button(label='Clock out',style=discord.ButtonStyle.secondary,custom_id='p37ems:out')
    async def clock_out(self,i,button):
        if not await ems_guard(i,staff=True): return
        row=EMS_DB.execute('SELECT id,started FROM shifts WHERE guild=? AND member=? AND ended IS NULL',(EMS_GUILD,i.user.id)).fetchone()
        if not row: return await deny(i,'You are not clocked in.')
        now=int(time.time()); EMS_DB.execute('UPDATE shifts SET ended=? WHERE id=?',(now,row[0])); EMS_DB.commit()
        await deny(i,f'✅ Clocked out. Shift length: {(now-row[1])/3600:.2f} hours.')
    @discord.ui.button(label='Book appointment',style=discord.ButtonStyle.primary,custom_id='p37ems:appointment')
    async def appointment(self,i,button):
        if not await ems_guard(i): return
        patient=ems_settings().get('rank_roles',{}).get('patient')
        if not i.user.guild_permissions.administrator and not any(r.id==patient for r in i.user.roles):
            return await deny(i,'The Patient role is required to book an appointment.')
        await i.response.send_modal(EMSModal('appointment'))
    @discord.ui.button(label='Apply for EMS',style=discord.ButtonStyle.primary,custom_id='p37ems:application')
    async def application(self,i,button):
        if await ems_guard(i): await i.response.send_modal(EMSModal('application'))

class EMSDutyView(EMSPanel):
    def __init__(self):
        super().__init__()
        for item in list(self.children):
            if item.custom_id not in ('p37ems:in','p37ems:out','p37ems:hours'): self.remove_item(item)
    @discord.ui.button(label='View my hours',style=discord.ButtonStyle.primary,custom_id='p37ems:hours')
    async def hours(self,i,button):
        await ems_hours.callback(i)

class EMSAppointmentView(EMSPanel):
    def __init__(self):
        super().__init__()
        for item in list(self.children):
            if item.custom_id!='p37ems:appointment': self.remove_item(item)

class EMSApplicationView(EMSPanel):
    def __init__(self):
        super().__init__()
        for item in list(self.children):
            if item.custom_id!='p37ems:application': self.remove_item(item)

EMS_RANKS = [
    ('staff_role', '🚑 EMS Staff', 0xE74C3C),
    ('command_role', '👑 EMS Command', 0x9B59B6),
    ('chief', '🏥 EMS Chief', 0xC0392B),
    ('deputy_chief', '⭐ Deputy Chief', 0xD35400),
    ('captain', '🎖️ EMS Captain', 0xF1C40F),
    ('paramedic', '💉 Paramedic', 0x3498DB),
    ('emt', '🩺 EMT', 0x1ABC9C),
    ('trainee', '📚 EMS Trainee', 0x95A5A6),
    ('nurse', '💊 Nurse', 0xE84393),
    ('physical_therapist', '🦽 Physical Therapist', 0x16A085),
    ('therapist', '🧠 Therapist', 0x8E44AD),
    ('government', '🏛️ Government', 0x34495E),
    ('patient', '💚 Patient', 0x2ECC71),
]

@bot.tree.command(name='setup_ems_roles', description='Create basic EMS ranks and reuse your existing staff and command roles')
@app_commands.default_permissions(administrator=True)
async def setup_ems_roles(i:discord.Interaction):
    if not await ems_guard(i): return
    if not i.user.guild_permissions.administrator:
        return await deny(i,'Administrator permission is required.')
    if not i.guild.me.guild_permissions.manage_roles:
        return await deny(i,'Give the bot Manage Roles and move its role above the EMS roles first.')
    await i.response.defer(ephemeral=True,thinking=True)
    async with LOCKS.setdefault('ems-setup',asyncio.Lock()):
        cfg=ems_settings()
        rank_ids=cfg.setdefault('rank_roles',{})
        resolved={}
        # Resolve all existing roles first; stop rather than guess duplicate names.
        for key,name,color in EMS_RANKS:
            saved=cfg.get(key) if key in ('staff_role','command_role') else rank_ids.get(key)
            r=i.guild.get_role(saved or 0)
            if r is None:
                candidates=[r for r in i.guild.roles if not r.managed and r.name in (name,name.split(' ',1)[1])]
                if len(candidates)>1:
                    return await deny(i,f'Multiple roles match {name}. Rename the extra roles before retrying.')
                r=candidates[0] if candidates else None
            if r and (r.is_default() or r.managed):
                return await deny(i,f'{name} must be a normal member role.')
            resolved[key]=r
        for key,name,color in EMS_RANKS:
            r=resolved[key]
            if r is None:
                r=await i.guild.create_role(name=name,color=discord.Color(color),permissions=discord.Permissions.none(),hoist=key not in ('staff_role','command_role'),mentionable=False,reason=f'EMS ranks requested by {i.user.id}')
            # Existing permissions, names and member assignments are preserved.
            if key in ('staff_role','command_role'): cfg[key]=r.id
            else: rank_ids[key]=r.id
            EMS_DB.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data',(EMS_GUILD,json.dumps(cfg))); EMS_DB.commit()
        await i.followup.send('✅ EMS roles ready: Chief, Deputy Chief, Captain, Paramedic, EMT, Trainee, Nurse, Physical Therapist, Therapist, Government, Patient, EMS Staff and EMS Command.\n\nGive active employees **EMS Staff + their rank/profession**. Give leadership **EMS Command** too. Trainees can have **EMS Trainee** alone for training-only access. Government alone has public access; it does not grant patient-record or staff access. Rank roles are labels; EMS Staff and EMS Command control bot access.\n\nIf channels are not set up yet, run `/setup_ems` and select the EMS Staff and EMS Command roles. Existing role permissions and assignments were kept.',ephemeral=True)

@bot.tree.command(name='setup_ems',description='Set up compact EMS channels using your existing staff and command roles')
@app_commands.default_permissions(administrator=True)
async def setup_ems(i:discord.Interaction,staff_role:discord.Role,command_role:discord.Role):
    if not await ems_guard(i): return
    if not i.user.guild_permissions.administrator: return await deny(i,'Administrator permission is required.')
    if staff_role.is_default() or command_role.is_default() or staff_role.managed or command_role.managed:
        return await deny(i,'Select existing EMS member roles, not @everyone or a bot/integration role.')
    await i.response.defer(ephemeral=True,thinking=True)
    async with LOCKS.setdefault('ems-setup',asyncio.Lock()):
        cfg=ems_settings(); cfg.update(staff_role=staff_role.id,command_role=command_role.id)
        def save():
            EMS_DB.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data',(EMS_GUILD,json.dumps(cfg))); EMS_DB.commit()
        save()
        def permissions(roles,public=False):
            result={i.guild.default_role:discord.PermissionOverwrite(view_channel=public,send_messages=False),i.guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True,connect=True,speak=True)}
            for r in roles: result[r]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True,connect=True,speak=True)
            return result
        async def category(key,name,perms):
            ch=i.guild.get_channel(cfg.get(key,0))
            if not isinstance(ch,discord.CategoryChannel): ch=await i.guild.create_category(name,overwrites=perms)
            else: await ch.edit(name=name,overwrites=perms)
            cfg[key]=ch.id; save(); return ch
        public=await category('public_category','🏥 HOSPITAL HELP',permissions([],True))
        patient=i.guild.get_role(cfg.get('rank_roles',{}).get('patient',0))
        appointment_perms=permissions([])
        if patient: appointment_perms[patient]=discord.PermissionOverwrite(view_channel=True,read_message_history=True,send_messages=False)
        for r in (staff_role,command_role):
            appointment_perms[r]=discord.PermissionOverwrite(view_channel=True,read_message_history=True,send_messages=False)
        private=await category('staff_category','☕ STAFF LOUNGE',permissions([staff_role,command_role]))
        clinical=await category('clinical_category','🏥 CLINICAL OPERATIONS',permissions([staff_role,command_role]))
        command_section=await category('command_category','👑 EMS COMMAND',permissions([command_role]))
        await category('tickets','🎫 EMS PRIVATE TICKETS',permissions([command_role]))
        trainee=i.guild.get_role(cfg.get('rank_roles',{}).get('trainee',0))
        training_roles=[staff_role,command_role]+([trainee] if trainee else [])
        training=await category('training_category','📚 IN TRAINING',permissions(training_roles))
        # Staff can post resources; trainees can read without editing them.
        resource_perms=permissions([staff_role,command_role])
        if trainee and trainee not in (staff_role,command_role):
            resource_perms[trainee]=discord.PermissionOverwrite(view_channel=True,read_message_history=True,send_messages=False)

        for key,name,parent,perms in [
            ('desk','📨・apply-for-ems',public,permissions([],True)),
            ('appointments','📅・book-appointment',public,appointment_perms),
            ('announcements','📢・ems-announcements',public,permissions([command_role],True)),
            ('information','📖・ems-information',public,permissions([command_role],True)),
            ('staff_chat','💬・ems-staff-chat',private,permissions([staff_role,command_role])),
            ('shift_updates','📅・shift-updates',private,permissions([staff_role,command_role])),
            ('lounge_media','📸・ems-moments',private,permissions([staff_role,command_role])),
            ('duty','⏱️・clock-in-out',private,permissions([staff_role,command_role])),
            ('protocols','📚・medical-protocols',clinical,permissions([staff_role,command_role])),
            ('command_chat','👑・command-chat',command_section,permissions([command_role])),
            ('training_resources','📖・training-resources',training,resource_perms),
            ('training_chat','💬・trainee-chat',training,permissions(training_roles)),
            ('training_progress','✅・training-progress',training,permissions(training_roles)),
            ('records','📋・patient-records',clinical,permissions([staff_role,command_role])),
            ('transcripts','🗂️・ems-transcripts',command_section,permissions([command_role]))]:
            ch=i.guild.get_channel(cfg.get(key,0))
            if not isinstance(ch,discord.TextChannel): ch=await i.guild.create_text_channel(name,category=parent,overwrites=perms)
            else: await ch.edit(name=name,category=parent,overwrites=perms)
            cfg[key]=ch.id; save()
        for key,name,parent,perms in [
            ('staff_voice','☕ Staff Break Room',private,permissions([staff_role,command_role])),
            ('training_voice','🎓 Training Room',training,permissions(training_roles))]:
            ch=i.guild.get_channel(cfg.get(key,0))
            if not isinstance(ch,discord.VoiceChannel):
                ch=await i.guild.create_voice_channel(name,category=parent,overwrites=perms)
            else: await ch.edit(name=name,category=parent,overwrites=perms)
            cfg[key]=ch.id; save()
        duty=i.guild.get_channel(cfg['duty'])
        duty_message=None
        if cfg.get('duty_panel'):
            try: duty_message=await duty.fetch_message(cfg['duty_panel'])
            except discord.NotFound: pass
        duty_embed=ems_embed('⏱️ EMS Duty Desk','Clock in at the start of your shift and clock out when finished. Use /ems_hours to view your totals.')
        if duty_message and duty_message.author.id==bot.user.id:
            await duty_message.edit(embed=duty_embed,view=EMSDutyView())
        else:
            duty_message=await duty.send(embed=duty_embed,view=EMSDutyView())
            cfg['duty_panel']=duty_message.id; save()
        desk=i.guild.get_channel(cfg['desk']); msg=None
        if cfg.get('panel'):
            try: msg=await desk.fetch_message(cfg['panel'])
            except discord.NotFound: pass
        e=ems_embed('🚑 Join Project 37 EMS','Want to join the hospital team? Apply below. Applications are open to everyone and reviewed privately by EMS Command.')
        if msg and msg.author.id==bot.user.id: await msg.edit(embed=e,view=EMSApplicationView())
        else:
            msg=await desk.send(embed=e,view=EMSApplicationView()); cfg['panel']=msg.id; save()
        appointments=i.guild.get_channel(cfg['appointments']); appointment_message=None
        if cfg.get('appointment_panel'):
            try: appointment_message=await appointments.fetch_message(cfg['appointment_panel'])
            except discord.NotFound: pass
        appointment_embed=ems_embed('📅 Book a Hospital Appointment','Patients: request an appointment below. Your details go into a private ticket with EMS. Use fictional roleplay patient information only.')
        if appointment_message and appointment_message.author.id==bot.user.id:
            await appointment_message.edit(embed=appointment_embed,view=EMSAppointmentView())
        else:
            appointment_message=await appointments.send(embed=appointment_embed,view=EMSAppointmentView())
            cfg['appointment_panel']=appointment_message.id; save()
        await i.followup.send(f'✅ EMS channels and In Training section ready. Front desk: {desk.mention}. Duty desk: {duty.mention}. Run /setup_ems_roles first if the Trainee role is missing, then rerun this command to grant training access. Staff use /ems_hours and /ems_patient_record. Applications are reviewed by command; staff assign accepted applicants their EMS role manually.',ephemeral=True)

@bot.tree.command(name='ems_hours',description='View your EMS shift totals; command can view another member')
async def ems_hours(i:discord.Interaction,member:discord.Member=None):
    if not await ems_guard(i,staff=True): return
    target=member or i.user
    if target.id!=i.user.id and not ems_staff(i.user,True): return await deny(i,'Only command can view another member’s hours.')
    rows=EMS_DB.execute('SELECT started,ended FROM shifts WHERE guild=? AND member=?',(EMS_GUILD,target.id)).fetchall()
    total=sum(max(0,(end or int(time.time()))-start) for start,end in rows)
    await deny(i,f'{target.display_name}: {total/3600:.2f} total hours. '+('Currently clocked in.' if any(end is None for _,end in rows) else 'Currently clocked out.'))

@bot.tree.command(name='ems_patient_record',description='File a fictional RP patient record in the staff-only records channel')
async def ems_patient_record(i:discord.Interaction,character_name:str,notes:str):
    if not await ems_guard(i,staff=True): return
    if len(character_name)>200 or len(notes)>3500: return await deny(i,'Use at most 200 characters for the name and 3,500 for notes.')
    ch=i.guild.get_channel(ems_settings().get('records',0))
    if not isinstance(ch,discord.TextChannel): return await deny(i,'Run /setup_ems first.')
    await i.response.defer(ephemeral=True)
    e=ems_embed('RP Patient Record • '+character_name,notes); e.add_field(name='Filed by',value=i.user.mention); e.timestamp=discord.utils.utcnow()
    await ch.send(embed=e,allowed_mentions=discord.AllowedMentions.none())
    await i.followup.send('Fictional patient record saved.',ephemeral=True)

# Block commands outside their intended server, including any stale guild commands.
async def server_scope_check(i):
    name=i.command.name if i.command else ''
    expected=EMS_GUILD if name in EMS_COMMANDS else GUILD
    if not expected or i.guild_id!=expected:
        await deny(i,'This command belongs to the other Project 37 Discord.')
        return False
    return True
bot.tree.interaction_check=server_scope_check


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    token=os.getenv('DISCORD_TOKEN','')
    if not token or not GUILD: raise SystemExit('Fill DISCORD_TOKEN and GUILD_ID in .env first.')
    bot.run(token)
