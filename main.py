import asyncio
import os
import random

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

# Sur Render : ajoute la variable d'environnement DISCORD_TOKEN.
# Sur GitHub, le placeholder ci-dessous reste tel quel.
TOKEN = os.getenv("DISCORD_TOKEN", "DISCORD TOKEN")
PORT = int(os.getenv("PORT", 10000))  # Render fournit PORT automatiquement

intents = discord.Intents.default()
intents.members = True  # Server Members Intent (Developer Portal)


# ---------- Mini serveur web (obligatoire pour un Web Service Render) ----------
async def health(request):
    return web.Response(text="Bot en ligne")


async def start_web_server():
    app = web.Application()
    app.add_routes([web.get("/", health), web.get("/health", health)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()


class AnnonceBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self):
        await start_web_server()
        await self.tree.sync()  # enregistre les commandes /


bot = AnnonceBot()


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")


# ---------- Envoi des DM ----------
async def envoyer_dm(channel, membres, texte):
    envoyes, echecs = 0, 0
    for membre in membres:
        try:
            await membre.send(texte)
            envoyes += 1
        except (discord.Forbidden, discord.HTTPException):
            echecs += 1  # DM fermés ou erreur
        await asyncio.sleep(random.uniform(3, 6))  # évite le rate limit
    await channel.send(f"Terminé : {envoyes} envoyé(s), {echecs} échec(s).")


# ---------- Confirmation (boutons) ----------
class Confirmation(discord.ui.View):
    def __init__(self, auteur, membres, texte):
        super().__init__(timeout=60)
        self.auteur = auteur
        self.membres = membres
        self.texte = texte

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.auteur.id:
            await interaction.response.send_message("Cette confirmation ne t'est pas destinée.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.green)
    async def confirmer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Envoi en cours...", view=None)
        asyncio.create_task(envoyer_dm(interaction.channel, self.membres, self.texte))
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.red)
    async def annuler(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Annulé.", view=None)
        self.stop()


# ---------- Formulaire « Message à envoyer » ----------
class MessageModal(discord.ui.Modal, title="Message à envoyer"):
    message = discord.ui.TextInput(
        label="Message à envoyer",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    def __init__(self, role: discord.Role):
        super().__init__()
        self.role = role

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.guild.chunk()
        membres = [m for m in self.role.members if not m.bot]
        view = Confirmation(interaction.user, membres, self.message.value)
        await interaction.response.send_message(
            f"Envoyer ce message à **{len(membres)}** membre(s) du rôle **{self.role.name}** ?",
            view=view,
            ephemeral=True,
        )


# ---------- Commande slash ----------
@bot.tree.command(name="annonce", description="Envoyer un message privé aux membres d'un rôle")
@app_commands.describe(role="Quel rôle voulez-vous que le message soit envoyé ?")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def annonce(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Réservé aux administrateurs.", ephemeral=True)
    if role.is_default():
        return await interaction.response.send_message(
            "Le rôle @everyone n'est pas autorisé. Choisis un rôle opt-in.", ephemeral=True
        )
    await interaction.response.send_modal(MessageModal(role))


bot.run(TOKEN)
