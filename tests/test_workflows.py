import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import sqlite3
import discord
import bot

class FakeResponse:
    def __init__(self): self.done=False; self.send_message=AsyncMock()
    async def defer(self,**kwargs): self.done=True
    def is_done(self): return self.done

class Workflows(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old=bot.DB; bot.DB=sqlite3.connect(':memory:')
        bot.DB.execute('CREATE TABLE requests(channel INTEGER PRIMARY KEY,owner INTEGER,kind TEXT,state TEXT,claimed INTEGER DEFAULT 0)')
        bot.LOCKS.clear()
        self.member=SimpleNamespace(id=12,roles=[],mention='<@12>',guild_permissions=SimpleNamespace(administrator=False))
        self.i=SimpleNamespace(user=self.member,channel_id=100,response=FakeResponse(),followup=SimpleNamespace(send=AsyncMock()),guild=Mock(),channel=Mock())
        self.i.channel.send=AsyncMock(); self.i.channel.delete=AsyncMock()
        self.r=SimpleNamespace(id=22)
        self.role_patch=patch('bot.role',return_value=self.r);self.role_patch.start()
    async def asyncTearDown(self):
        self.role_patch.stop();bot.DB.close();bot.DB=self.old
    def seed(self,kind='whitelist',state='open'):
        bot.DB.execute('INSERT INTO requests VALUES(100,12,?,?,0)',(kind,state));bot.DB.commit()
    async def test_unauthorized_cannot_approve(self):
        self.seed()
        await bot.act(self.i,'approve')
        self.assertFalse(self.i.guild.fetch_member.called)
        self.assertEqual(bot.record(100)[2],'open')
    async def test_approval_grants_role_once(self):
        self.seed();self.member.guild_permissions.administrator=True
        target=SimpleNamespace(add_roles=AsyncMock())
        self.i.guild.get_member.return_value=target
        await bot.act(self.i,'approve')
        target.add_roles.assert_awaited_once_with(self.r,reason='whitelist approved by 12')
        self.assertEqual(bot.record(100)[2],'approve')
        self.i.response=FakeResponse()
        await bot.act(self.i,'approve')
        self.assertEqual(target.add_roles.await_count,1)
    async def test_role_failure_keeps_open(self):
        self.seed();self.member.guild_permissions.administrator=True
        target=SimpleNamespace(add_roles=AsyncMock(side_effect=RuntimeError('role too high')))
        self.i.guild.get_member.return_value=target
        with self.assertRaises(RuntimeError): await bot.act(self.i,'approve')
        self.assertEqual(bot.record(100)[2],'open')
    async def test_transcript_failure_does_not_delete(self):
        self.seed();self.member.guild_permissions.administrator=True
        logs=Mock(spec=discord.TextChannel);logs.send=AsyncMock(side_effect=RuntimeError('upload failed'))
        self.i.guild.get_channel.return_value=logs
        async def history(**kwargs):
            for item in []: yield item
        self.i.channel.history=history
        with self.assertRaises(RuntimeError): await bot.act(self.i,'close')
        self.i.channel.delete.assert_not_awaited()
        self.assertEqual(bot.record(100)[2],'open')
    async def test_concurrent_requests_create_one_private_channel(self):
        category=Mock(spec=discord.CategoryChannel)
        channel=SimpleNamespace(id=100,mention='<#100>',send=AsyncMock())
        guild=Mock()
        guild.default_role=Mock(spec=discord.Role)
        guild.me=Mock(spec=discord.Member)
        def get_channel(n): return category if n==0 else channel
        guild.get_channel.side_effect=get_channel
        guild.create_text_channel=AsyncMock(return_value=channel)
        user=Mock(spec=discord.Member);user.id=12;user.mention='<@12>'
        team=Mock(spec=discord.Role)
        interactions=[SimpleNamespace(user=user,guild=guild,response=FakeResponse(),followup=SimpleNamespace(send=AsyncMock())) for _ in range(2)]
        with patch('bot.role',return_value=team):
            await asyncio.gather(*(bot.create_request(i,'support',[('Issue','Help')]) for i in interactions))
        guild.create_text_channel.assert_awaited_once()
        overwrites=guild.create_text_channel.call_args.kwargs['overwrites']
        self.assertFalse(overwrites[guild.default_role].view_channel)
        self.assertTrue(overwrites[user].view_channel)
        self.assertTrue(overwrites[team].view_channel)
        self.assertEqual(bot.DB.execute('SELECT COUNT(*) FROM requests').fetchone()[0],1)
    async def test_persistent_components(self):
        self.assertTrue(bot.EntryView(['verify','whitelist','ems','pd','realestate']).is_persistent())
        self.assertTrue(bot.TicketControls().is_persistent())
        for kind in bot.CFG['requests']:
            modal=bot.RequestModal(kind)
            self.assertLessEqual(len(modal.children),5)

if __name__=='__main__': unittest.main()
