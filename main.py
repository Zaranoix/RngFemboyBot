import os
import asyncio
import random
import logging
import json

import discord
import aiohttp

from discord.ext import commands
from discord import Intents, Embed

# ─── Configuration ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
RNG_SETTINGS_FILE = "rng_settings.json"
INVENTORY_FILE    = "inventory.json"
NSFW_ENDPOINT     = "https://api.waifu.pics/nsfw/trap"
ASTOLFO_NAME      = "Astolfo"
ASTOLFO_URL       = "https://i.imgur.com/8ZQZ4aP.jpg"  # Legendary

# ─── Persistence Helpers ────────────────────────────────────────────────────────
def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

rng_settings = load_json(RNG_SETTINGS_FILE)
inventory    = load_json(INVENTORY_FILE)

def save_all():
    save_json(RNG_SETTINGS_FILE, rng_settings)
    save_json(INVENTORY_FILE, inventory)

def user_settings(uid):
    return rng_settings.setdefault(uid, {"autoclaim": False, "autodelete": []})

def user_inventory(uid):
    return inventory.setdefault(uid, [])

# ─── Bot & HTTP Setup ─────────────────────────────────────────────────────────
intents = Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
http: aiohttp.ClientSession = None

# Track every URL we've sent so far to avoid repeats
seen_urls = set()

# ─── Ensure #rng Channel ───────────────────────────────────────────────────────
async def ensure_rng_channel(guild: discord.Guild):
    existing = discord.utils.get(guild.text_channels, name="rng")
    if existing:
        return existing
    ch = await guild.create_text_channel("rng", topic="RNG femboy rolls")
    embed = Embed(title="🎲 RNG Subsystem", color=discord.Color.purple())
    embed.add_field(
        name="Welcome!",
        value=(
            "• Use `!roll` to roll a femboy (NSFW trap)\n"
            "• Rarity: Common, Rare, Elite, Epic, Legendary\n"
            "• Legendary is always Astolfo\n\n"
            "Settings: `!rngsettings`\n"
            "Collection: `!inventory`"
        ),
        inline=False
    )
    await ch.send(embed=embed)
    await ch.send("Say `!roll` to roll for a femboy.")
    return ch

@bot.event
async def on_ready():
    global http
    if http is None:
        http = aiohttp.ClientSession()
    logging.info(f"✅ RNG bot ready as {bot.user}")
    for guild in bot.guilds:
        await ensure_rng_channel(guild)

@bot.event
async def on_guild_join(guild):
    await ensure_rng_channel(guild)

# ─── Rarity Table ──────────────────────────────────────────────────────────────
RARITY_TABLE = [
    ("Common",    0.70),
    ("Rare",      0.20),
    ("Elite",     0.075),
    ("Epic",      0.024),
    ("Legendary", 0.001),
]

def choose_rarity():
    roll = random.random()
    cum = 0
    for name, prob in RARITY_TABLE:
        cum += prob
        if roll <= cum:
            # The "number" is just for flavor
            return name, random.randint(1, 100_000_000)
    return "Common", random.randint(1, 100_000_000)

# ─── Fetch & Deduplicate ──────────────────────────────────────────────────────
async def fetch_femboy(rarity: str):
    if rarity == "Legendary":
        return ASTOLFO_NAME, ASTOLFO_URL

    # Try up to 5 times to get a new URL
    for _ in range(5):
        async with http.get(NSFW_ENDPOINT) as resp:
            resp.raise_for_status()
            data = await resp.json()
            url = data["url"]
        if url not in seen_urls:
            seen_urls.add(url)
            return "Femboy", url
    # Fallback to Astolfo if we can't get a unique image
    return ASTOLFO_NAME, ASTOLFO_URL

# ─── RNG COMMANDS ──────────────────────────────────────────────────────────────
@bot.command(name="roll")
async def roll(ctx):
    if ctx.channel.name != "rng":
        return await ctx.send("❌ Rolls only in `#rng`.")
    rarity, num = choose_rarity()
    try:
        name, img = await fetch_femboy(rarity)
    except Exception:
        return await ctx.send("❌ Could not reach image API. Try again later.")

    embed = Embed(title=f"{name} — {rarity} ({num}/100000000)",
                  color=discord.Color.blue())
    embed.set_image(url=img)
    msg = await ctx.send(embed=embed)

    # save to inventory
    uid = str(ctx.author.id)
    inv = user_inventory(uid)
    inv.append({"name": name, "rarity": rarity, "number": num})
    save_all()

    # autoclaim DM
    s = user_settings(uid)
    if s["autoclaim"]:
        try: await ctx.author.send(f"You rolled **{name}** ({rarity})!")
        except: pass

    # autodelete
    if rarity in s["autodelete"]:
        await asyncio.sleep(5)
        try: await msg.delete()
        except: pass

@bot.command(name="inventory")
async def inventory_cmd(ctx):
    inv = user_inventory(str(ctx.author.id))
    if not inv:
        return await ctx.send("📭 Your inventory is empty.")
    embed = Embed(title=f"{ctx.author.display_name}'s Inventory",
                  color=discord.Color.green())
    counts = {}
    for item in inv:
        key = f"{item['name']} ({item['rarity']})"
        counts[key] = counts.get(key, 0) + 1
    for k, v in counts.items():
        embed.add_field(name=k, value=f"x{v}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="rngsettings")
async def settings_overview(ctx):
    s = user_settings(str(ctx.author.id))
    embed = Embed(title="⚙️ RNG Settings", color=discord.Color.orange())
    embed.add_field(
        name="Auto Claim",
        value=f"`!autoclaim on/off` (currently {'On' if s['autoclaim'] else 'Off'})",
        inline=False
    )
    embed.add_field(
        name="Auto Delete Rarity",
        value=f"`!autodelete <rarity>` to toggle\n"
              f"Currently: {', '.join(s['autodelete']) or 'None'}",
        inline=False
    )
    await ctx.send(embed=embed)

@bot.command(name="autoclaim")
async def autoclaim(ctx, arg: str):
    uid = str(ctx.author.id)
    s = user_settings(uid)
    if arg.lower() == "on":
        s["autoclaim"] = True
    elif arg.lower() == "off":
        s["autoclaim"] = False
    else:
        return await ctx.send("❌ Usage: `!autoclaim on/off`")
    save_all()
    await ctx.send(f"✅ AutoClaim set to {s['autoclaim']}.")

@bot.command(name="autodelete")
async def autodelete(ctx, rarity: str):
    uid = str(ctx.author.id)
    s = user_settings(uid)
    valid = [r for r,_ in RARITY_TABLE]
    if rarity.title() not in valid:
        return await ctx.send(f"❌ Rarity must be: {', '.join(valid)}")
    if rarity.title() in s["autodelete"]:
        s["autodelete"].remove(rarity.title()); action = "disabled"
    else:
        s["autodelete"].append(rarity.title()); action = "enabled"
    save_all()
    await ctx.send(f"✅ AutoDelete {action} for {rarity.title()}.")

if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))
