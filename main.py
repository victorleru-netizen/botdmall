import asyncio
import json
import os
import random
import re

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

# Sur Render : ajoute la variable d'environnement DISCORD_TOKEN.
# Sur GitHub, le placeholder ci-dessous reste tel quel.
TOKEN = os.getenv("DISCORD_TOKEN", "DISCORD TOKEN")
PORT = int(os.getenv("PORT", 10000))  # Render fournit PORT automatiquement

# Propriétaire du bot : par défaut le compte qui possède l'application Discord.
# Tu peux le forcer avec la variable d'environnement OWNER_ID.
OWNER_ID_ENV = os.getenv("OWNER_ID")

# Utilisateurs autorisés ajoutés avec /add (sauvegardés dans ce fichier).
# Sur Render gratuit le disque est effacé à chaque redéploiement/redémarrage :
# pour des ajouts permanents, mets leurs IDs dans la variable AUTORISES (séparés par des virgules).
FICHIER = "autorises.json"


def charger_autorises():
    ids = set()
    for x in os.getenv("AUTORISES", "").split(","):
        if x.strip().isdigit():
            ids.add(int(x.strip()))
    try:
        with open(FICHIER, "r", encoding="utf-8") as f:
            ids.update(int(i) for i in json.load(f))
    except (FileNotFoundError, ValueError):
        pass
    return ids


def sauvegarder_autorises():
    with open(FICHIER, "w", encoding="utf-8") as f:
        json.dump(sorted(autorises), f)


autorises = charger_autorises()

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
        if OWNER_ID_ENV and OWNER_ID_ENV.isdigit():
            self.owner_id = int(OWNER_ID_ENV)
        else:
            info = await self.application_info()
            self.owner_id = info.team.owner_id if info.team else info.owner.id
        await start_web_server()
        await self.tree.sync()  # enregistre les commandes /


bot = AnnonceBot()


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user} | propriétaire : {bot.owner_id}")


# ---------- Vérifications d'accès ----------
def est_proprietaire(interaction: discord.Interaction) -> bool:
    return interaction.user.id == bot.owner_id


def est_autorise(interaction: discord.Interaction) -> bool:
    return interaction.user.id == bot.owner_id or interaction.user.id in autorises


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        texte = "Tu n'es pas autorisé à utiliser cette commande."
    else:
        print(f"Erreur commande : {error!r}")
        texte = "Une erreur est survenue."
    if interaction.response.is_done():
        await interaction.followup.send(texte, ephemeral=True)
    else:
        await interaction.response.send_message(texte, ephemeral=True)


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


# ---------- /annonce (propriétaire + utilisateurs ajoutés) ----------
@bot.tree.command(name="annonce", description="Envoyer un message privé aux membres d'un rôle")
@app_commands.describe(role="Quel rôle voulez-vous que le message soit envoyé ?")
@app_commands.guild_only()
@app_commands.check(est_autorise)
async def annonce(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.send_modal(MessageModal(role))


# ---------- /add (propriétaire uniquement) ----------
@bot.tree.command(name="add", description="Autoriser quelqu'un à utiliser le bot")
@app_commands.describe(utilisateur="Mention (@) ou ID de la personne")
@app_commands.check(est_proprietaire)
async def add(interaction: discord.Interaction, utilisateur: str):
    trouve = re.search(r"\d{15,20}", utilisateur)
    if not trouve:
        return await interaction.response.send_message("Mention ou ID invalide.", ephemeral=True)
    user_id = int(trouve.group())
    try:
        user = await bot.fetch_user(user_id)
    except discord.NotFound:
        return await interaction.response.send_message("Utilisateur introuvable.", ephemeral=True)

    if user_id == bot.owner_id or user_id in autorises:
        return await interaction.response.send_message(f"{user.mention} a déjà accès au bot.", ephemeral=True)

    autorises.add(user_id)
    sauvegarder_autorises()
    await interaction.response.send_message(f"{user.mention} peut maintenant utiliser le bot.", ephemeral=True)


bot.run(TOKEN)
