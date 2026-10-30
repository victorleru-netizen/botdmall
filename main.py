import asyncio
import random

import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.members = True          # Server Members Intent (Developer Portal)
intents.message_content = True  # Message Content Intent (Developer Portal)
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")


@bot.command()
@commands.guild_only()
@commands.has_permissions(administrator=True)
async def annonce(ctx):
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    # Question 1 : le rôle
    await ctx.send("Quel rôle voulez-vous que le message soit envoyé ? (mentionne-le ou écris son nom)")
    try:
        rep = await bot.wait_for("message", check=check, timeout=60)
        role = await commands.RoleConverter().convert(ctx, rep.content)
    except asyncio.TimeoutError:
        return await ctx.send("Temps écoulé, commande annulée.")
    except commands.RoleNotFound:
        return await ctx.send("Rôle introuvable, commande annulée.")

    # Question 2 : le message
    await ctx.send("Message à envoyer :")
    try:
        msg = await bot.wait_for("message", check=check, timeout=300)
    except asyncio.TimeoutError:
        return await ctx.send("Temps écoulé, commande annulée.")

    await ctx.guild.chunk()
    membres = [m for m in role.members if not m.bot]

    # Confirmation
    await ctx.send(
        f"Envoyer ce message à **{len(membres)}** membre(s) du rôle **{role.name}** ? "
        "Réponds `oui` pour confirmer."
    )
    try:
        conf = await bot.wait_for("message", check=check, timeout=30)
    except asyncio.TimeoutError:
        return await ctx.send("Pas de confirmation, commande annulée.")
    if conf.content.lower() not in ("oui", "o", "yes"):
        return await ctx.send("Annulé.")

    # Envoi avec délai pour éviter le rate limit
    envoyes, echecs = 0, 0
    for membre in membres:
        try:
            await membre.send(msg.content)
            envoyes += 1
        except (discord.Forbidden, discord.HTTPException):
            echecs += 1  # DM fermés ou erreur
        await asyncio.sleep(random.uniform(3, 6))

    await ctx.send(f"Terminé : {envoyes} envoyé(s), {echecs} échec(s).")


bot.run("DISCORD TOKEN")  # remplace par ton vrai token uniquement sur ta machine / ton hébergeur
