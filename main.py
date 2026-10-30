import asyncio
import json
import logging
import os
import random
import re
import sys

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("annonce")

# Sur Render : ajoute la variable d'environnement DISCORD_TOKEN.
# Sur GitHub, le placeholder ci-dessous reste tel quel.
PLACEHOLDER = "DISCORD TOKEN"
TOKEN = os.getenv("DISCORD_TOKEN", PLACEHOLDER)
PORT = int(os.getenv("PORT", 10000))  # Render fournit PORT automatiquement

# Propriétaire du bot : par défaut le compte qui possède l'application Discord.
# Tu peux le forcer avec la variable d'environnement OWNER_ID.
OWNER_ID_ENV = os.getenv("OWNER_ID")

# Utilisateurs ajoutés avec /add (sauvegardés dans ce fichier).
# Sur Render gratuit le disque est effacé à chaque redéploiement/redémarrage :
# pour des accès permanents, mets leurs IDs dans la variable AUTORISES (séparés par des virgules).
FICHIER = "autorises.json"

DELAI_MIN, DELAI_MAX = 3, 6   # secondes entre deux DM
ERREURS_MAX = 5               # erreurs d'affilée avant d'interrompre l'envoi (protège ton bot)


# ---------- Utilisateurs autorisés ----------
def charger_autorises() -> set:
    ids = set()
    for x in os.getenv("AUTORISES", "").split(","):
        if x.strip().isdigit():
            ids.add(int(x.strip()))
    try:
        with open(FICHIER, "r", encoding="utf-8") as f:
            ids.update(int(i) for i in json.load(f))
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as e:
        log.warning("Lecture de %s impossible : %s", FICHIER, e)
    return ids


def sauvegarder_autorises() -> bool:
    try:
        with open(FICHIER, "w", encoding="utf-8") as f:
            json.dump(sorted(autorises), f)
        return True
    except OSError as e:
        log.warning("Écriture de %s impossible : %s", FICHIER, e)
        return False


autorises = charger_autorises()
envois_en_cours = set()  # ids des serveurs avec un envoi actif (évite les doublons)
taches = set()           # garde une référence aux tâches en arrière-plan

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
    log.info("Serveur web démarré sur le port %s", PORT)


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
        try:
            await self.tree.sync()  # enregistre les commandes /
        except discord.HTTPException as e:
            log.error("Synchronisation des commandes impossible : %s", e)


bot = AnnonceBot()


@bot.event
async def on_ready():
    log.info("Connecté en tant que %s | propriétaire : %s", bot.user, bot.owner_id)


# ---------- Vérifications d'accès ----------
def est_proprietaire(interaction: discord.Interaction) -> bool:
    return interaction.user.id == bot.owner_id


def est_autorise(interaction: discord.Interaction) -> bool:
    return interaction.user.id == bot.owner_id or interaction.user.id in autorises


async def repondre(interaction: discord.Interaction, texte: str):
    """Répond en privé, que l'interaction ait déjà reçu une réponse ou non."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(texte, ephemeral=True)
        else:
            await interaction.response.send_message(texte, ephemeral=True)
    except discord.HTTPException as e:
        log.warning("Réponse impossible : %s", e)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await repondre(interaction, "Tu n'es pas autorisé à utiliser cette commande.")
    else:
        log.exception("Erreur de commande", exc_info=error)
        await repondre(interaction, "Une erreur est survenue, réessaie.")


# ---------- Envoi des DM ----------
async def notifier(channel, auteur, texte: str):
    """Envoie le bilan dans le salon, sinon en MP à l'auteur."""
    try:
        if channel is None:
            raise discord.HTTPException
        await channel.send(texte)
    except (discord.HTTPException, AttributeError):
        try:
            await auteur.send(texte)
        except discord.HTTPException:
            log.warning("Impossible d'envoyer le bilan.")


async def envoyer_dm(channel, auteur, guild_id: int, membres: list, texte: str):
    envoyes = fermes = erreurs = consecutives = 0
    interrompu = False
    try:
        for membre in membres:
            try:
                await membre.send(texte)
                envoyes += 1
                consecutives = 0
            except discord.HTTPException as e:
                if e.code == 50007:  # DM fermés : normal, pas une erreur
                    fermes += 1
                else:
                    erreurs += 1
                    consecutives += 1
                    log.warning("DM échoué (%s) code=%s : %s", membre.id, e.code, e.text)
                    if e.code == 40003:  # ouverture de DM trop rapide : on temporise
                        await asyncio.sleep(60)
                    if consecutives >= ERREURS_MAX:
                        interrompu = True
                        break
            await asyncio.sleep(random.uniform(DELAI_MIN, DELAI_MAX))
    except Exception:
        log.exception("Erreur inattendue pendant l'envoi")
        interrompu = True
    finally:
        envois_en_cours.discard(guild_id)

    bilan = f"{'Envoi interrompu' if interrompu else 'Terminé'} : "
    bilan += f"{envoyes} envoyé(s), {fermes} DM fermé(s), {erreurs} erreur(s)."
    if interrompu:
        bilan += " Trop d'erreurs d'affilée : vérifie la console du bot avant de relancer."
    await notifier(channel, auteur, bilan)


# ---------- Confirmation (boutons) ----------
class Confirmation(discord.ui.View):
    def __init__(self, auteur, guild_id, membres, texte):
        super().__init__(timeout=60)
        self.auteur = auteur
        self.guild_id = guild_id
        self.membres = membres
        self.texte = texte
        self.interaction = None  # interaction d'origine, pour désactiver les boutons à l'expiration

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.auteur.id:
            await repondre(interaction, "Cette confirmation ne t'est pas destinée.")
            return False
        return True

    async def on_timeout(self):
        if self.interaction:
            try:
                await self.interaction.edit_original_response(content="Confirmation expirée.", view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.green)
    async def confirmer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.guild_id in envois_en_cours:
            await interaction.response.edit_message(content="Un envoi est déjà en cours sur ce serveur.", view=None)
            return self.stop()
        envois_en_cours.add(self.guild_id)
        await interaction.response.edit_message(content="Envoi en cours... Tu recevras un bilan à la fin.", view=None)
        tache = asyncio.create_task(
            envoyer_dm(interaction.channel, self.auteur, self.guild_id, self.membres, self.texte)
        )
        taches.add(tache)
        tache.add_done_callback(taches.discard)
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
        await interaction.response.defer(ephemeral=True, thinking=True)  # évite l'expiration des 3 s
        try:
            await interaction.guild.chunk()
        except discord.HTTPException as e:
            log.warning("chunk() a échoué : %s", e)

        membres = [m for m in self.role.members if not m.bot]
        if not membres:
            return await interaction.edit_original_response(content="Aucun membre (hors bots) dans ce rôle.")

        duree = max(1, round(len(membres) * (DELAI_MIN + DELAI_MAX) / 2 / 60))
        view = Confirmation(interaction.user, interaction.guild.id, membres, self.message.value)
        view.interaction = interaction
        await interaction.edit_original_response(
            content=(
                f"Envoyer ce message à **{len(membres)}** membre(s) du rôle **{self.role.name}** ?\n"
                f"Durée estimée : environ {duree} min."
            ),
            view=view,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Erreur dans le formulaire", exc_info=error)
        await repondre(interaction, "Une erreur est survenue, réessaie.")


# ---------- /annonce (propriétaire + utilisateurs ajoutés) ----------
@bot.tree.command(name="annonce", description="Envoyer un message privé aux membres d'un rôle")
@app_commands.describe(role="Quel rôle voulez-vous que le message soit envoyé ?")
@app_commands.guild_only()
@app_commands.check(est_autorise)
async def annonce(interaction: discord.Interaction, role: discord.Role):
    if role.is_default():
        return await interaction.response.send_message(
            "Le rôle @everyone n'est pas autorisé. Choisis un rôle opt-in.", ephemeral=True
        )
    if interaction.guild.id in envois_en_cours:
        return await interaction.response.send_message(
            "Un envoi est déjà en cours sur ce serveur. Attends le bilan.", ephemeral=True
        )
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

    if user_id == bot.owner_id or user_id in autorises:
        return await interaction.response.send_message(f"<@{user_id}> a déjà accès au bot.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    try:
        user = await bot.fetch_user(user_id)
    except discord.NotFound:
        return await interaction.followup.send("Utilisateur introuvable.", ephemeral=True)
    except discord.HTTPException:
        return await interaction.followup.send("Impossible de vérifier cet utilisateur, réessaie.", ephemeral=True)

    autorises.add(user_id)
    texte = f"{user.mention} peut maintenant utiliser le bot."
    if not sauvegarder_autorises():
        texte += "\nAttention : sauvegarde impossible, l'accès sera perdu au redémarrage (utilise AUTORISES)."
    await interaction.followup.send(texte, ephemeral=True)


def main():
    if not TOKEN or TOKEN == PLACEHOLDER:
        sys.exit("Token manquant : définis la variable d'environnement DISCORD_TOKEN.")
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        sys.exit("Token invalide : vérifie DISCORD_TOKEN.")
    except discord.PrivilegedIntentsRequired:
        sys.exit("Active « Server Members Intent » dans le Developer Portal (onglet Bot).")


if __name__ == "__main__":
    main()
