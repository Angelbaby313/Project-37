# Project 37 Concierge

Custom Python Discord bot for Project 37. This download is source code, not an already-hosted service. Run one instance continuously on your PC or Python bot host. Includes a separate FiveM resource for automatic server-up messages.

## Included

- Individual member welcome pointing to rules, Verify, then Request Whitelist.
- Persistent Verify and WL buttons. Verified is separate from Key to the city.
- Private Donations, Support, Player Report, and Female Verification tickets.
- EMS, PD, and Real Estate application forms; WL required before applying.
- Team-specific private review channels, claim/approve/deny controls, confirmed close, and transcripts.
- WL approval assigns the EXISTING Key to the city role; female approval assigns the EXISTING Project Baddies role.
- Career approval assigns configured Discord EMS/PD/Real Estate roles. It does not assign in-game ESX jobs.
- SQLite request records and persistent buttons survive bot restarts. Keep config.json and project37.sqlite3 with your hosting backups.
- `/setup_city` creates the organized role/channel layout without deleting old content.
- A FiveM server-side restart announcement resource that works even when the Python bot is offline.

## First-time Windows setup

1. Install Python 3.11 or newer. Make sure Python is available through the Windows `py` command.
2. Extract the ZIP into its own folder.
3. Create an application at https://discord.com/developers/applications and add its bot. Set its name/avatar as desired.
4. Enable **Server Members Intent** on the Bot page. Message Content intent is NOT needed.
5. Copy `.env.example` to `.env`. Put the BOT token into DISCORD_TOKEN and the Project 37 Discord SERVER ID into GUILD_ID. Keep these local; do not send your token in chat.
6. With Discord Developer Mode enabled, right-click the existing **Key to the city / Key To Da City** role and **Project Baddies** role and Copy Role ID. Put these numeric IDs (unquoted numbers) in config.json under roles.whitelist and roles.female. Set IDs for any existing staff roles you want reused too. This preserves the existing roles and current members. The setup command also recognizes matching role names, but explicit IDs are best.
7. Invite the bot with the `bot` and `applications.commands` OAuth scopes. Give it Manage Roles, Manage Channels, View Channels, Send Messages, Embed Links, Attach Files, and Read Message History. Invite it to Project 37 only.
8. Move the bot's Discord role ABOVE Verified, Key to the city, Project Baddies, and the EMS/PD/Real Estate roles it assigns. Keep owner/management above the bot. Do not grant Administrator to the bot.
9. Double-click `start.bat`. Keep the window open. On a Python host, install requirements and use `python bot.py` with this folder as working directory.
10. As a Discord administrator, run `/setup_city`. Allow time for Discord's rate limits. If interrupted, rerun; existing matching channels are reused, not duplicated. No old channels or roles are deleted. Matching existing channels are left unchanged: check their permissions manually.
11. Place authorized staff in the relevant roles: WL Team, Female Verifier, Support, Moderation Team, Donation Manager, EMS Command, PD Command, Real Estate Management. City Management can review every kind of request. Roles created by setup have no global administration permissions.
12. Run `/post_panel` once per option: `verify`, `whitelist`, `tickets`, `applications`. Each posts to its configured channel. Keep panel channels read-only for members. Delete duplicate panels manually if you post twice.
13. Paste the client's approved rules into city-rules. Fill city-guide, donation info, and any other informational channels. The bot does not invent their policies or delete their old Discord.
14. Test with a non-staff account before opening the revamped server. Once reviewed, archive/remove old channels and obsolete roles manually as agreed with the owner.

### Access design

- Everyone can see arrival/help channels; cannot chat broadly.
- Verified can request WL and use tickets.
- Key to the city unlocks city/community areas.
- Project Baddies unlocks the Baddies section separately; does NOT automatically grant WL.
- Only the requester, their assigned team, City Management, and Discord administrators can see an individual request.
- Transcript archive created by setup is management-only. Keep it private if configuring manually. Transcripts include attachment LINKS, not downloaded evidence files; preserve important evidence separately before deleting a ticket.
- Close requires confirmation and successful transcript upload before channel deletion. Very large transcripts exceeding Discord's upload limit must be archived manually; failed uploads leave the ticket open.
- Approved/denied requests remain open for discussion until staff closes them.
- `/request_action` is a staff fallback for claim/approve/deny. `/close_request` confirms closing.
- Bot-side claims are informational; other authorized reviewers can still handle the request.

## Application forms

Applicants use `/post_panel applications` buttons; answers are collected in a private form and posted to their department team. Five grouped questions keep each form within Discord modal limits.

### EMS
1. Character name, your age, and timezone.
2. RP experience and EMS experience.
3. Why EMS? What is your weekly availability?
4. A patient is unresponsive: how would you handle the RP scene?
5. The scene is unsafe: what do you do? Do you agree to department/city rules?

### PD
1. Character name, your age, and timezone.
2. RP experience and PD experience.
3. Why PD? What is your weekly availability?
4. How would you approach a traffic stop and handle escalation?
5. Your friend breaks the law: how do you respond? Do you agree to department/city rules?

### Real Estate
1. Character name, your age, and timezone.
2. Sales or real estate RP experience.
3. Why real estate? What is your weekly availability?
4. How would you handle a property viewing?
5. How would you handle a dispute or suspected scam?

Edit questions/role IDs in config.json, then restart the bot. Use actual resource and role names, not sample placeholders.

## Automatic `server up, fly back in` channel

The setup command creates **🔄・server-restarts**. Use a Discord webhook in that channel for the FiveM resource:

1. In that channel: Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL.
2. Give the FiveM server owner/developer the `fivem/project37_restart` folder to place under the server's resources.
3. Add this at the BOTTOM of server.cfg after the city's critical resources:

```cfg
# `set`, NOT `setr`: the webhook must stay server-side.
set p37_restart_webhook "YOUR_DISCORD_WEBHOOK_URL"
# Replace these example names with this city's real critical resource names.
set p37_ready_resources "es_extended,oxmysql"
set p37_ready_delay "60"
ensure project37_restart
```

If they use another framework, replace `es_extended` with that resource's actual name; include other critical resources as appropriate. Missing readiness resource configuration disables announcements. A resource that never starts prevents the message.

The resource waits until ALL configured critical resources have state `started` continuously for the configured delay, then posts:

> 🟢 PROJECT 37 IS BACK UP!
> The city has restarted—fly back in! ✈️
> Grab your people and pick up where your story left off. 🤍
> Welcome back to Project 37.

This is a startup signal with resource checks, not a guarantee that a player can connect or every database/gameplay dependency is healthy. Have the developer pick readiness resources and a delay that fit their startup. It posts after a full server startup, including initial deployment; successful sends set a runtime marker so restarting this announcement resource in the SAME server process does not repost. Do NOT add p37_restart_announced to server.cfg; it must reset with the full server process. No scheduled restart countdowns are included. No @everyone or role ping is sent.

The Discord webhook URL is a secret: keep it in server.cfg only, do not put it in client scripts or screenshots. The bot token and webhook are different secrets.

## Troubleshooting

- Bot online but no welcomes: enable Server Members Intent and check welcome channel ID/permissions. Welcomes trigger for new joins, not existing members.
- Verify/approve says action failed: check bot role position, Manage Roles permission, and configured role IDs. It cannot assign roles above itself.
- Can't see slash commands: confirm correct guild ID, invite with applications.commands scope, restart, then refresh Discord.
- Setup created another role: set explicit existing IDs before setup. No existing role is deleted automatically.
- Ticket team cannot see a ticket: check the configured team role and staff membership. Don't open ticket permissions to everyone.
- Server up message not sent: inspect FiveM console; check webhook, real readiness resource names, outbound access to Discord, and resource startup order.
- Keep only ONE running bot process and persist its database. This bot targets ONE Discord guild.

## Validation

Included tests exercise review permissions, duplicate-request protection under concurrent submissions, role grants, and transcript failure keeping tickets intact. These run with Discord API mocks. A real guild token/server was not supplied, so live Discord permissions and FiveM startup behavior must be checked during installation.

Run `python -m unittest discover -s tests -v` from this folder.
