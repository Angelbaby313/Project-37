-- Server-side only. Keep webhook in a non-replicated `set` convar.
-- Ensure this resource LAST. Never use `setr` for the webhook.
local sent = GetConvar('p37_restart_announced', '0') == '1'
local readySince = nil
local required = {}
for name in string.gmatch(GetConvar('p37_ready_resources', ''), '[^,]+') do
    required[#required + 1] = name:match('^%s*(.-)%s*$')
end
local settle = math.max(10, GetConvarInt('p37_ready_delay', 60))

local function announce(attempt)
    local url = GetConvar('p37_restart_webhook', '')
    if not url:match('^https://discord.com/api/webhooks/') then
        print('[Project 37] Missing/invalid restart webhook. No announcement posted.')
        return
    end
    PerformHttpRequest(url .. '?wait=true', function(code, body)
        if code >= 200 and code < 300 then
            sent = true
            SetConvar('p37_restart_announced', '1')
            print('[Project 37] Server-up announcement posted.')
        elseif attempt < 4 and (code == 429 or code >= 500 or code == 0) then
            local retrySeconds = 15
            if code == 429 then
                local ok, data = pcall(json.decode, body or '')
                if ok and data and tonumber(data.retry_after) then
                    retrySeconds = math.max(1, tonumber(data.retry_after))
                end
            end
            SetTimeout(math.ceil(retrySeconds * 1000), function() announce(attempt + 1) end)
        else
            print('[Project 37] Announcement failed, HTTP ' .. tostring(code) .. '. Check webhook and Discord access.')
        end
    end, 'POST', json.encode({
        username = 'Project 37 • City Status',
        allowed_mentions = { parse = {} },
        embeds = {{
            title = '🟢 PROJECT 37 IS BACK UP!',
            description = 'The city has restarted—fly back in! ✈️\n\nGrab your people and pick up where your story left off. 🤍\n\n**Welcome back to Project 37.**',
            color = 5763719,
            footer = { text = 'Project 37 • Your next chapter starts here' }
        }}
    }), { ['Content-Type'] = 'application/json' })
end

CreateThread(function()
    if #required == 0 then
        print('[Project 37] Set p37_ready_resources to your critical resource names. Automatic announcements disabled until configured.')
        return
    end
    while not sent do
        Wait(1000)
        local allReady = true
        for _, name in ipairs(required) do
            if GetResourceState(name) ~= 'started' then allReady = false; break end
        end
        if not allReady then readySince = nil end
        if allReady then
            readySince = readySince or GetGameTimer()
            if GetGameTimer() - readySince >= settle * 1000 then
                announce(1)
                return
            end
        end
    end
end)
